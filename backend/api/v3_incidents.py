"""v3 incident administration and bearer-scoped mobile notice APIs."""
from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
from hashlib import sha256
import hmac
import ipaddress
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable
from uuid import uuid4

from flask import Blueprint, current_app, g, jsonify, request, session

from config import MobileSecuritySettings
from contracts import Role
from services.auth import effective_role
from services.incident_workflow import (
    IncidentActor,
    IncidentSourceForbidden,
    IncidentWorkflowError,
    IncidentWorkflowService,
    MobileResourceUnavailable,
)
from services.mobile_access import (
    MobileAccessError,
    MobileAccessService,
    MobileAuthenticationError,
)


_CSRF_HEADER = "X-CSRF-Token"
_MAX_JSON_BYTES = 16 * 1024
_NON_NEGATIVE_INTEGER = re.compile(r"^(0|[1-9][0-9]*)$")


class ApiInputError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _error(code: str, message: str, status: int):
    return jsonify({"error": {
        "code": code, "message": message,
        "request_id": g.v3_request_id,
    }}), status


def _role() -> str:
    return effective_role(
        session.get("username", ""), session.get("role")
    )


def _csrf_token() -> str:
    secret = current_app.secret_key
    if not secret:
        raise RuntimeError(
            "Flask SECRET_KEY is required for CSRF protection"
        )
    secret_bytes = (
        secret if isinstance(secret, bytes)
        else str(secret).encode("utf-8")
    )
    identity = (
        f"v3-device-write:{session.get('user_id')}:"
        f"{session.get('username', '')}:{_role()}"
    ).encode("utf-8")
    return hmac.new(secret_bytes, identity, sha256).hexdigest()


def _json_body(*, allowed: set[str], required: set[str]) -> dict:
    if request.mimetype != "application/json":
        raise ApiInputError(
            "invalid_content_type",
            "请求 Content-Type 必须是 application/json",
        )
    if (
        request.content_length is not None
        and request.content_length > _MAX_JSON_BYTES
    ):
        raise ApiInputError(
            "request_too_large", "JSON 请求体过大", 413
        )
    raw = request.get_data(cache=False)
    if len(raw) > _MAX_JSON_BYTES:
        raise ApiInputError(
            "request_too_large", "JSON 请求体过大", 413
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ApiInputError(
            "invalid_json", "请求体必须是合法 UTF-8 JSON"
        )
    if not isinstance(payload, dict):
        raise ApiInputError(
            "invalid_json_object", "JSON 请求体必须是对象"
        )
    if set(payload) - allowed:
        raise ApiInputError(
            "unknown_fields", "请求包含不允许的字段"
        )
    missing = sorted(
        field for field in required if field not in payload
    )
    if missing:
        raise ApiInputError(
            "missing_fields",
            "缺少必填字段：" + ", ".join(missing),
        )
    return payload


def _bearer_token() -> str:
    authorization = request.headers.get("Authorization", "")
    parts = authorization.split(" ")
    if (
        len(parts) != 2
        or parts[0] != "Bearer"
        or not parts[1]
    ):
        raise MobileAuthenticationError(
            "mobile bearer token is required"
        )
    return parts[1]


def _is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def _is_secure(
    settings: MobileSecuritySettings,
) -> bool:
    if request.is_secure:
        return True
    if settings.trust_proxy:
        forwarded = request.headers.get(
            "X-Forwarded-Proto", ""
        )
        if (
            forwarded.split(",", 1)[0].strip().lower()
            == "https"
        ):
            return True
    return settings.allow_insecure_http and (
        _is_loopback(request.remote_addr or "")
        or settings.environment == "testing"
    )


def _timestamp(value, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiInputError(
            "invalid_timestamp", f"{field} 必须是 ISO 8601 时间"
        )
    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ApiInputError(
            "invalid_timestamp", f"{field} 必须是 ISO 8601 时间"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ApiInputError(
            "invalid_timestamp", f"{field} 必须包含时区"
        )
    return parsed


def create_v3_incidents_blueprint(
    database_path: str | Path | None,
    settings: MobileSecuritySettings,
    mobile_service: MobileAccessService,
    *,
    clock: Callable | None = None,
    fault_injector: Callable[[str], None] | None = None,
) -> Blueprint:
    service = IncidentWorkflowService(
        database_path, settings, clock=clock,
        fault_injector=fault_injector,
    )
    blueprint = Blueprint("v3_incidents", __name__)
    blueprint.incident_service = service

    @blueprint.before_request
    def assign_request_id():
        g.v3_request_id = str(uuid4())

    @blueprint.after_request
    def attach_headers(response):
        response.headers["X-Request-ID"] = g.v3_request_id
        response.headers["Cache-Control"] = "no-store"
        if "user_id" in session:
            response.headers[_CSRF_HEADER] = _csrf_token()
        return response

    def admin(*, write: bool, admin_only: bool = False):
        def decorator(view):
            @wraps(view)
            def decorated(*args, **kwargs):
                if "user_id" not in session:
                    return _error(
                        "unauthenticated", "需要 Web 登录", 401
                    )
                role = _role()
                if role == Role.USER.value:
                    return _error(
                        "incident_admin_forbidden",
                        "普通用户不能访问事件管理接口",
                        403,
                    )
                if role not in {
                    Role.ADMIN.value, Role.OPERATOR.value,
                }:
                    return _error("forbidden", "权限不足", 403)
                if admin_only and role != Role.ADMIN.value:
                    return _error(
                        "admin_required", "只有管理员可以执行此操作", 403
                    )
                if write:
                    supplied = request.headers.get(_CSRF_HEADER)
                    if not supplied:
                        return _error(
                            "csrf_token_missing",
                            "缺少 CSRF token",
                            403,
                        )
                    if not hmac.compare_digest(
                        _csrf_token(), supplied
                    ):
                        return _error(
                            "csrf_token_invalid",
                            "CSRF token 无效",
                            403,
                        )
                return view(*args, **kwargs)
            return decorated
        return decorator

    def mobile(view):
        @wraps(view)
        def decorated(*args, **kwargs):
            if not _is_secure(settings):
                return _error(
                    "https_required",
                    "移动认证接口要求 HTTPS",
                    400,
                )
            try:
                g.mobile_principal = (
                    mobile_service.authenticate_access(
                        _bearer_token()
                    )
                )
            except MobileAccessError as exc:
                return _error(
                    exc.code, "移动认证无效或已过期", exc.status
                )
            return view(*args, **kwargs)
        return decorated

    def actor() -> IncidentActor:
        return IncidentActor(
            session["user_id"],
            session.get("username", ""),
            _role(),
        )

    def handle(callable_):
        try:
            return callable_()
        except ApiInputError as exc:
            return _error(exc.code, str(exc), exc.status)
        except MobileResourceUnavailable as exc:
            return _error(
                exc.code, "请求的资源不存在或不可见", exc.status
            )
        except IncidentWorkflowError as exc:
            if getattr(g, "mobile_principal", None) is not None:
                message = {
                    "mobile_rate_limited": "操作过于频繁，请稍后重试",
                    "invalid_notice_cursor": "提醒同步游标无效，请重新同步",
                    "idempotency_conflict": "该请求标识已用于其他内容",
                }.get(exc.code, "请求内容无效")
                return _error(exc.code, message, exc.status)
            return _error(exc.code, str(exc), exc.status)
        except sqlite3.Error:
            return _error(
                "incident_store_unavailable",
                "事件服务暂时不可用",
                503,
            )

    def one(name: str) -> str | None:
        values = request.args.getlist(name)
        if len(values) > 1:
            raise ApiInputError(
                "invalid_query", f"查询参数 {name} 不得重复"
            )
        return values[0] if values else None

    def integer(
        name: str, default: int, *, positive: bool = False
    ) -> int:
        raw = one(name)
        if raw is None:
            return default
        if not _NON_NEGATIVE_INTEGER.fullmatch(raw):
            raise ApiInputError(
                "invalid_query",
                f"查询参数 {name} 必须是非负整数",
            )
        value = int(raw)
        if positive and value == 0:
            raise ApiInputError(
                "invalid_query",
                f"查询参数 {name} 必须大于 0",
            )
        return value

    @blueprint.get("/api/v3/incidents")
    @admin(write=False)
    def list_incidents():
        def execute():
            allowed = {
                "status", "severity", "source", "device_id",
                "search", "from", "to", "limit", "offset",
            }
            if set(request.args) - allowed:
                raise ApiInputError(
                    "unknown_query_parameters",
                    "存在不支持的查询参数",
                )
            from_value, to_value = one("from"), one("to")
            if from_value is not None:
                from_value = _timestamp(
                    from_value, "from"
                ).astimezone(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                )
            if to_value is not None:
                to_value = _timestamp(
                    to_value, "to"
                ).astimezone(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                )
            return jsonify(service.list_incidents(
                status=one("status"), severity=one("severity"),
                source=one("source"), device_id=one("device_id"),
                search=one("search"),
                from_time=from_value, to_time=to_value,
                limit=integer("limit", 50, positive=True),
                offset=integer("offset", 0),
            ))
        return handle(execute)

    @blueprint.get("/api/v3/incidents/<incident_id>")
    @admin(write=False)
    def get_incident(incident_id: str):
        return handle(
            lambda: jsonify(service.get_incident(incident_id))
        )

    @blueprint.post("/api/v3/incidents")
    @admin(write=True, admin_only=True)
    def create_incident():
        def execute():
            payload = _json_body(
                allowed={
                    "incident_type", "severity", "source",
                    "admin_title", "admin_summary", "user_title",
                    "user_summary", "devices", "publish_to_mobile",
                    "first_seen_at", "public_progress",
                },
                required={
                    "incident_type", "severity", "source",
                    "admin_title", "admin_summary", "user_title",
                    "user_summary", "devices", "publish_to_mobile",
                },
            )
            if payload["source"] != "manual":
                raise IncidentSourceForbidden(
                    "管理 API 仅允许创建 manual 事件"
                )
            return jsonify(service.create_incident(
                incident_type=payload["incident_type"],
                severity=payload["severity"],
                source=payload["source"],
                admin_title=payload["admin_title"],
                admin_summary=payload["admin_summary"],
                user_title=payload["user_title"],
                user_summary=payload["user_summary"],
                devices=payload["devices"],
                publish_to_mobile=payload["publish_to_mobile"],
                first_seen_at=_timestamp(
                    payload.get("first_seen_at"),
                    "first_seen_at",
                ),
                public_progress=payload.get("public_progress"),
                actor=actor(), request_id=g.v3_request_id,
            )), 201
        return handle(execute)

    def transition(incident_id: str, target: str):
        def execute():
            payload = _json_body(
                allowed={
                    "expected_incident_version", "public_progress",
                    "admin_details", "resolution_summary",
                    "false_positive_reason",
                },
                required={"expected_incident_version"},
            )
            return jsonify(service.transition_incident(
                incident_id, target_status=target,
                expected_incident_version=(
                    payload["expected_incident_version"]
                ),
                public_progress=payload.get("public_progress"),
                admin_details=payload.get("admin_details"),
                resolution_summary=payload.get(
                    "resolution_summary"
                ),
                false_positive_reason=payload.get(
                    "false_positive_reason"
                ),
                actor=actor(), request_id=g.v3_request_id,
            ))
        return handle(execute)

    @blueprint.post("/api/v3/incidents/<incident_id>/ack")
    @admin(write=True)
    def acknowledge_incident(incident_id: str):
        return transition(incident_id, "acknowledged")

    @blueprint.post(
        "/api/v3/incidents/<incident_id>/recovering"
    )
    @admin(write=True)
    def recover_incident(incident_id: str):
        return transition(incident_id, "recovering")

    @blueprint.post("/api/v3/incidents/<incident_id>/resolve")
    @admin(write=True)
    def resolve_incident(incident_id: str):
        return transition(incident_id, "resolved")

    @blueprint.post(
        "/api/v3/incidents/<incident_id>/false-positive"
    )
    @admin(write=True)
    def false_positive_incident(incident_id: str):
        return transition(incident_id, "false_positive")

    @blueprint.get("/api/v3/mobile/notices")
    @mobile
    def mobile_notices():
        def execute():
            if set(request.args) - {"after", "view", "limit"}:
                raise ApiInputError(
                    "unknown_query_parameters",
                    "存在不支持的查询参数",
                )
            return jsonify(service.list_mobile_notices(
                g.mobile_principal, after=one("after"),
                view=one("view") or "active",
                limit=integer("limit", 50, positive=True),
            ))
        return handle(execute)

    @blueprint.get(
        "/api/v3/mobile/notices/<incident_id>"
    )
    @mobile
    def mobile_notice(incident_id: str):
        return handle(lambda: jsonify(
            service.get_mobile_notice(
                g.mobile_principal, incident_id
            )
        ))

    def mark_notice(incident_id: str, acknowledged: bool):
        def execute():
            _json_body(allowed=set(), required=set())
            return jsonify(service.mark_notice(
                g.mobile_principal, incident_id,
                acknowledged=acknowledged,
                request_id=g.v3_request_id,
            ))
        return handle(execute)

    @blueprint.post(
        "/api/v3/mobile/notices/<incident_id>/read"
    )
    @mobile
    def read_notice(incident_id: str):
        return mark_notice(incident_id, False)

    @blueprint.post(
        "/api/v3/mobile/notices/<incident_id>/acknowledge"
    )
    @mobile
    def acknowledge_notice(incident_id: str):
        return mark_notice(incident_id, True)

    @blueprint.get("/api/v3/support-contact")
    @admin(write=False)
    def support_contact():
        return handle(
            lambda: jsonify(service.get_support_contact())
        )

    @blueprint.put("/api/v3/support-contact")
    @admin(write=True, admin_only=True)
    def put_support_contact():
        def execute():
            payload = _json_body(
                allowed={
                    "display_name", "phone", "email",
                    "working_hours", "public_note", "enabled",
                    "expected_config_version",
                },
                required={
                    "display_name", "enabled",
                    "expected_config_version",
                },
            )
            return jsonify(service.put_support_contact(
                display_name=payload["display_name"],
                phone=payload.get("phone"),
                email=payload.get("email"),
                working_hours=payload.get("working_hours"),
                public_note=payload.get("public_note"),
                enabled=payload["enabled"],
                expected_config_version=(
                    payload["expected_config_version"]
                ),
                actor=actor(), request_id=g.v3_request_id,
            ))
        return handle(execute)

    @blueprint.get("/api/v3/mobile/support-contact")
    @mobile
    def mobile_support_contact():
        return handle(lambda: jsonify(
            service.get_support_contact(mobile=True)
        ))

    @blueprint.post("/api/v3/mobile/help-requests")
    @mobile
    def create_help_request():
        def execute():
            payload = _json_body(
                allowed={
                    "incident_id", "device_id",
                    "category", "user_message",
                },
                required={"category", "user_message"},
            )
            key = request.headers.get("Idempotency-Key", "")
            return jsonify(service.create_help_request(
                g.mobile_principal,
                incident_id=payload.get("incident_id"),
                device_id=payload.get("device_id"),
                category=payload["category"],
                user_message=payload["user_message"],
                idempotency_key=key,
                request_id=g.v3_request_id,
            )), 201
        return handle(execute)

    @blueprint.get("/api/v3/mobile/help-requests")
    @mobile
    def mobile_help_requests():
        return handle(lambda: jsonify(
            service.list_mobile_help_requests(g.mobile_principal)
        ))

    @blueprint.get(
        "/api/v3/mobile/help-requests/<help_request_id>"
    )
    @mobile
    def mobile_help_request(help_request_id: str):
        return handle(lambda: jsonify(
            service.get_mobile_help_request(
                g.mobile_principal, help_request_id
            )
        ))

    @blueprint.get("/api/v3/help-requests")
    @admin(write=False)
    def help_requests():
        def execute():
            allowed = {
                "status", "category", "user_id", "device_id",
                "incident_id", "from", "to", "limit", "offset",
            }
            if set(request.args) - allowed:
                raise ApiInputError(
                    "unknown_query_parameters",
                    "存在不支持的查询参数",
                )
            from_value, to_value = one("from"), one("to")
            if from_value is not None:
                from_value = _timestamp(
                    from_value, "from"
                ).astimezone(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                )
            if to_value is not None:
                to_value = _timestamp(
                    to_value, "to"
                ).astimezone(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                )
            user_id = one("user_id")
            if user_id is not None:
                if not _NON_NEGATIVE_INTEGER.fullmatch(user_id) or int(user_id) == 0:
                    raise ApiInputError(
                        "invalid_query", "查询参数 user_id 必须是正整数"
                    )
                user_id = int(user_id)
            return jsonify(service.list_help_requests(
                status=one("status"),
                category=one("category"), user_id=user_id,
                device_id=one("device_id"),
                incident_id=one("incident_id"),
                from_time=from_value, to_time=to_value,
                limit=integer("limit", 50, positive=True),
                offset=integer("offset", 0),
            ))
        return handle(execute)

    @blueprint.get(
        "/api/v3/help-requests/<help_request_id>"
    )
    @admin(write=False)
    def help_request(help_request_id: str):
        return handle(lambda: jsonify(
            service.get_help_request(help_request_id)
        ))

    @blueprint.patch(
        "/api/v3/help-requests/<help_request_id>"
    )
    @admin(write=True)
    def update_help_request(help_request_id: str):
        def execute():
            payload = _json_body(
                allowed={
                    "expected_request_version", "status",
                    "public_response", "internal_note",
                    "assigned_to",
                },
                required={
                    "expected_request_version", "status",
                },
            )
            return jsonify(service.update_help_request(
                help_request_id,
                expected_request_version=(
                    payload["expected_request_version"]
                ),
                status=payload["status"],
                public_response=payload.get("public_response"),
                internal_note=payload.get("internal_note"),
                assigned_to=payload.get("assigned_to"),
                actor=actor(), request_id=g.v3_request_id,
            ))
        return handle(execute)

    return blueprint


__all__ = ["create_v3_incidents_blueprint"]
