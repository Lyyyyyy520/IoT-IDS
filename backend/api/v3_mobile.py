"""v3 mobile pairing, scoped sessions, and restricted overview API."""
from __future__ import annotations

from functools import wraps
from hashlib import sha256
import hmac
import ipaddress
import json
import logging
from pathlib import Path
import re
import sqlite3
from typing import Callable
from uuid import uuid4

from flask import Blueprint, current_app, g, jsonify, request, session

from config import MobileSecuritySettings
from contracts import Role
from services.auth import effective_role
from services.device_traffic import DeviceTrafficService
from services.mobile_access import (
    MobileAccessError,
    MobileAccessService,
    MobileActor,
    MobileAuthenticationError,
    MobileRateLimited,
)
from services.mobile_device import (
    MobileDeviceReadService,
    MobileDeviceTrafficUnavailable,
    MobileDeviceTrafficWindowError,
    MobileDeviceUnavailable,
)


LOGGER = logging.getLogger(__name__)
_CSRF_HEADER = "X-CSRF-Token"
_MAX_JSON_BYTES = 8 * 1024
_NON_NEGATIVE_INTEGER = re.compile(r"^(0|[1-9][0-9]*)$")


class ApiInputError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _error(code: str, message: str, status: int):
    return jsonify({"error": {
        "code": code, "message": message, "request_id": g.v3_request_id,
    }}), status


def _role() -> str:
    return effective_role(session.get("username", ""), session.get("role"))


def _csrf_token() -> str:
    secret = current_app.secret_key
    if not secret:
        raise RuntimeError("Flask SECRET_KEY is required for CSRF protection")
    secret_bytes = secret if isinstance(secret, bytes) else str(secret).encode("utf-8")
    identity = (
        f"v3-device-write:{session.get('user_id')}:{session.get('username', '')}:"
        f"{_role()}"
    ).encode("utf-8")
    return hmac.new(secret_bytes, identity, sha256).hexdigest()


def _json_body(*, allowed: set[str], required: set[str]) -> dict:
    if request.mimetype != "application/json":
        raise ApiInputError(
            "invalid_content_type", "请求 Content-Type 必须是 application/json"
        )
    if request.content_length is not None and request.content_length > _MAX_JSON_BYTES:
        raise ApiInputError("request_too_large", "JSON 请求体过大", 413)
    raw = request.get_data(cache=False)
    if len(raw) > _MAX_JSON_BYTES:
        raise ApiInputError("request_too_large", "JSON 请求体过大", 413)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ApiInputError("invalid_json", "请求体必须是合法 UTF-8 JSON")
    if not isinstance(payload, dict):
        raise ApiInputError("invalid_json_object", "JSON 请求体必须是对象")
    if set(payload) - allowed:
        raise ApiInputError("unknown_fields", "请求包含不允许的字段")
    missing = sorted(field for field in required if field not in payload)
    if missing:
        raise ApiInputError("missing_fields", "缺少必填字段：" + ", ".join(missing))
    return payload


def _bearer_token() -> str:
    authorization = request.headers.get("Authorization", "")
    if not authorization:
        raise MobileAuthenticationError("mobile bearer token is required")
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0] != "Bearer" or not parts[1]:
        raise MobileAuthenticationError("mobile bearer token is malformed")
    return parts[1]


def _client_address(settings: MobileSecuritySettings) -> str:
    if settings.trust_proxy:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()[:128]
    return (request.remote_addr or "unknown")[:128]


def _is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def _is_secure_request(settings: MobileSecuritySettings) -> bool:
    if request.is_secure:
        return True
    if settings.trust_proxy:
        forwarded = request.headers.get("X-Forwarded-Proto", "")
        if forwarded.split(",", 1)[0].strip().lower() == "https":
            return True
    return settings.allow_insecure_http and (
        _is_loopback(request.remote_addr or "") or settings.environment == "testing"
    )


def create_v3_mobile_blueprint(
    database_path: str | Path | None,
    settings: MobileSecuritySettings,
    *,
    clock: Callable | None = None,
    fault_injector: Callable[[str], None] | None = None,
    traffic_service: DeviceTrafficService | None = None,
) -> Blueprint:
    """Create side-effect-free mobile routes with injectable service dependencies."""
    service = MobileAccessService(
        database_path, settings, clock=clock, fault_injector=fault_injector
    )
    device_service = MobileDeviceReadService(
        service,
        traffic_service or DeviceTrafficService(database_path, clock=clock),
        clock=clock,
    )
    blueprint = Blueprint("v3_mobile", __name__)
    blueprint.mobile_service = service
    blueprint.mobile_device_service = device_service
    if settings.allow_insecure_http:
        LOGGER.warning(
            "mobile_insecure_http_enabled environment=%s loopback_or_test_only=true",
            settings.environment,
        )

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

    def secure(view):
        @wraps(view)
        def decorated(*args, **kwargs):
            if not _is_secure_request(settings):
                return _error("https_required", "移动认证接口要求 HTTPS", 400)
            return view(*args, **kwargs)
        return decorated

    def admin(*, write: bool):
        def decorator(view):
            @wraps(view)
            def decorated(*args, **kwargs):
                if "user_id" not in session:
                    return _error("unauthenticated", "需要 Web 管理员登录", 401)
                if _role() != Role.ADMIN.value:
                    return _error("mobile_admin_forbidden", "只有管理员可以执行此操作", 403)
                if write:
                    supplied = request.headers.get(_CSRF_HEADER)
                    if not supplied:
                        return _error("csrf_token_missing", "缺少 CSRF token", 403)
                    if not hmac.compare_digest(_csrf_token(), supplied):
                        return _error("csrf_token_invalid", "CSRF token 无效", 403)
                return view(*args, **kwargs)
            return decorated
        return decorator

    def mobile(view):
        @wraps(view)
        def decorated(*args, **kwargs):
            try:
                principal = service.authenticate_access(_bearer_token())
            except MobileAccessError as exc:
                return _error(exc.code, "移动认证无效或已过期", exc.status)
            g.mobile_principal = principal
            return view(*args, **kwargs)
        return decorated

    def actor() -> MobileActor:
        return MobileActor(session["user_id"], session.get("username", ""), _role())

    def handle(callable_):
        try:
            return callable_()
        except ApiInputError as exc:
            return _error(exc.code, str(exc), exc.status)
        except MobileAccessError as exc:
            message = str(exc)
            if exc.code == "pairing_claim_rejected":
                message = "配对码无效或不可用"
            return _error(exc.code, message, exc.status)
        except sqlite3.Error:
            return _error("mobile_store_unavailable", "移动会话存储暂时不可用", 503)

    def handle_mobile_device_read(callable_):
        try:
            return jsonify(callable_())
        except ApiInputError as exc:
            return _error(exc.code, str(exc), exc.status)
        except MobileDeviceUnavailable as exc:
            return _error(exc.code, "设备不可用或不在当前授权范围", 404)
        except MobileDeviceTrafficWindowError as exc:
            return _error(exc.code, str(exc), 400)
        except MobileDeviceTrafficUnavailable as exc:
            return _error(exc.code, "流量服务暂时不可用", 503)
        except MobileRateLimited:
            return _error("mobile_rate_limited", "请求过于频繁，请稍后再试", 429)
        except MobileAccessError as exc:
            return _error(exc.code, "移动服务暂时不可用", exc.status)
        except sqlite3.Error:
            return _error("mobile_store_unavailable", "移动服务暂时不可用", 503)

    def one(name: str) -> str | None:
        values = request.args.getlist(name)
        if len(values) > 1:
            raise ApiInputError("invalid_query", f"查询参数 {name} 不得重复")
        return values[0] if values else None

    def integer(name: str, default: int) -> int:
        raw = one(name)
        if raw is None:
            return default
        if not _NON_NEGATIVE_INTEGER.fullmatch(raw):
            raise ApiInputError("invalid_query", f"查询参数 {name} 必须是非负整数")
        return int(raw)

    def boolean(name: str) -> bool | None:
        raw = one(name)
        if raw is None:
            return None
        if raw == "true":
            return True
        if raw == "false":
            return False
        raise ApiInputError("invalid_query", f"查询参数 {name} 必须是 true 或 false")

    @blueprint.get("/api/v3/mobile-users")
    @admin(write=False)
    def list_mobile_users():
        def execute():
            allowed = {
                "search", "account_status", "mobile_only", "limit", "offset",
            }
            if set(request.args) - allowed:
                raise ApiInputError(
                    "unknown_query_parameters", "存在不支持的查询参数"
                )
            return jsonify(service.list_mobile_users(
                search=one("search"),
                account_status=one("account_status"),
                mobile_only=boolean("mobile_only"),
                limit=integer("limit", 50),
                offset=integer("offset", 0),
            ))
        return handle(execute)

    @blueprint.post("/api/v3/mobile-users")
    @secure
    @admin(write=True)
    def create_mobile_user():
        def execute():
            payload = _json_body(
                allowed={"username", "display_name"},
                required={"username", "display_name"},
            )
            return jsonify(service.create_mobile_user(
                username=payload["username"],
                display_name=payload["display_name"],
                actor=actor(),
                request_id=g.v3_request_id,
            )), 201
        return handle(execute)

    @blueprint.get("/api/v3/mobile-users/<int:user_id>")
    @admin(write=False)
    def get_mobile_user(user_id: int):
        return handle(lambda: jsonify(service.get_mobile_user(user_id)))

    @blueprint.patch("/api/v3/mobile-users/<int:user_id>")
    @secure
    @admin(write=True)
    def patch_mobile_user(user_id: int):
        def execute():
            payload = _json_body(
                allowed={
                    "expected_profile_version", "display_name",
                    "account_status", "disabled_reason",
                },
                required={"expected_profile_version"},
            )
            return jsonify(service.update_mobile_user(
                user_id,
                expected_profile_version=payload["expected_profile_version"],
                display_name=payload.get("display_name"),
                account_status=payload.get("account_status"),
                disabled_reason=payload.get("disabled_reason"),
                actor=actor(),
                request_id=g.v3_request_id,
            ))
        return handle(execute)

    @blueprint.get("/api/v3/mobile-users/<int:user_id>/scopes")
    @admin(write=False)
    def get_scopes(user_id: int):
        return handle(lambda: jsonify(service.get_scopes(user_id)))

    @blueprint.put("/api/v3/mobile-users/<int:user_id>/scopes")
    @secure
    @admin(write=True)
    def put_scopes(user_id: int):
        def execute():
            payload = _json_body(
                allowed={"expected_scope_version", "scopes"},
                required={"expected_scope_version", "scopes"},
            )
            return jsonify(service.replace_scopes(
                user_id, payload["scopes"],
                expected_scope_version=payload["expected_scope_version"],
                actor=actor(), request_id=g.v3_request_id,
            ))
        return handle(execute)

    @blueprint.post("/api/v3/pairing/start")
    @secure
    @admin(write=True)
    def start_pairing():
        def execute():
            payload = _json_body(
                allowed={"user_id", "ttl_seconds"}, required={"user_id"}
            )
            result = service.start_pairing(
                payload["user_id"], actor=actor(), request_id=g.v3_request_id,
                ttl_seconds=payload.get("ttl_seconds"),
            )
            return jsonify(result), 201
        return handle(execute)

    @blueprint.post("/api/v3/pairing/claim")
    @secure
    def claim_pairing():
        def execute():
            payload = _json_body(
                allowed={"pairing_code", "client_instance_id", "client_display_name"},
                required={"pairing_code", "client_instance_id", "client_display_name"},
            )
            return jsonify(service.claim_pairing(
                payload["pairing_code"],
                client_instance_id=payload["client_instance_id"],
                client_display_name=payload["client_display_name"],
                rate_identity=_client_address(settings),
                request_id=g.v3_request_id,
            )), 201
        return handle(execute)

    @blueprint.post("/api/v3/mobile/token/refresh")
    @secure
    def refresh_token():
        def execute():
            payload = _json_body(
                allowed={"refresh_token"}, required={"refresh_token"}
            )
            return jsonify(service.refresh_tokens(
                payload["refresh_token"],
                rate_identity=_client_address(settings),
                request_id=g.v3_request_id,
            ))
        return handle(execute)

    @blueprint.post("/api/v3/mobile/logout")
    @secure
    def logout():
        return handle(lambda: jsonify(service.logout(
            _bearer_token(), request_id=g.v3_request_id
        )))

    @blueprint.get("/api/v3/mobile/session")
    @secure
    @mobile
    def mobile_session():
        return jsonify(service.session_summary(g.mobile_principal))

    @blueprint.get("/api/v3/mobile/overview")
    @secure
    @mobile
    def mobile_overview():
        return handle(lambda: jsonify(service.overview(g.mobile_principal)))

    @blueprint.get("/api/v3/mobile/devices/<device_id>")
    @secure
    @mobile
    def mobile_device_detail(device_id: str):
        def execute():
            if request.args:
                raise ApiInputError("unknown_query_parameters", "设备详情不接受查询参数")
            service.consume_scoped_read_limit(
                g.mobile_principal,
                action="mobile_device_detail",
                limit=120,
            )
            return device_service.detail(g.mobile_principal, device_id)

        return handle_mobile_device_read(execute)

    @blueprint.get("/api/v3/mobile/devices/<device_id>/traffic")
    @secure
    @mobile
    def mobile_device_traffic(device_id: str):
        def execute():
            if set(request.args) - {"window"} or len(request.args.getlist("window")) > 1:
                raise ApiInputError("invalid_query", "仅支持 window=15m、1h 或 24h")
            window = request.args.get("window", "15m")
            service.consume_scoped_read_limit(
                g.mobile_principal,
                action="mobile_device_traffic",
                limit=60,
            )
            return device_service.traffic(g.mobile_principal, device_id, window)

        return handle_mobile_device_read(execute)

    @blueprint.get("/api/v3/mobile-sessions")
    @admin(write=False)
    def list_mobile_sessions():
        def execute():
            allowed = {"user_id", "status", "limit", "offset"}
            if set(request.args) - allowed:
                raise ApiInputError(
                    "unknown_query_parameters", "存在不支持的查询参数"
                )
            user_value = one("user_id")
            user_id = None
            if user_value is not None:
                if not _NON_NEGATIVE_INTEGER.fullmatch(user_value) or int(user_value) <= 0:
                    raise ApiInputError("invalid_query", "user_id 必须是正整数")
                user_id = int(user_value)
            return jsonify(service.list_sessions(
                user_id=user_id, status=one("status"),
                limit=integer("limit", 50), offset=integer("offset", 0),
            ))
        return handle(execute)

    @blueprint.post("/api/v3/mobile-sessions/<session_id>/revoke")
    @secure
    @admin(write=True)
    def revoke_mobile_session(session_id: str):
        def execute():
            payload = _json_body(allowed={"reason"}, required=set())
            return jsonify(service.revoke_session(
                session_id, reason=payload.get("reason", "admin_revoked"),
                actor=actor(), request_id=g.v3_request_id,
            ))
        return handle(execute)

    return blueprint


__all__ = ["create_v3_mobile_blueprint"]
