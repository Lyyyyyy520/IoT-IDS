"""Authenticated v3 device inventory and lifecycle API."""
from functools import wraps
from hashlib import sha256
import hmac
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable
from uuid import uuid4

from flask import Blueprint, current_app, g, jsonify, request, session

from contracts import Role
from services.auth import effective_role
from services.device_management import (
    ConfirmationMismatchError,
    DeviceActor,
    DeviceHasHistoryError,
    DeviceIdConflictError,
    DeviceIdentityConflictError,
    DeviceLifecycleConflictError,
    DeviceManagementError,
    DeviceManagementService,
    DeviceNotFoundError,
    ProfileVersionConflictError,
)
from services.realtime_events import V3DatabaseUnavailable


_CSRF_HEADER = "X-CSRF-Token"
_MAX_JSON_BYTES = 16 * 1024
_NON_NEGATIVE_INTEGER = re.compile(r"^(0|[1-9][0-9]*)$")


class ApiInputError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _error(code: str, message: str, status: int, *, details: dict | None = None):
    error = {"code": code, "message": message, "request_id": g.v3_request_id}
    if details is not None:
        error["details"] = details
    return jsonify({"error": error}), status


def _current_role() -> str:
    return effective_role(session.get("username", ""), session.get("role"))


def _csrf_token() -> str:
    secret = current_app.secret_key
    if not secret:
        raise RuntimeError("Flask SECRET_KEY is required for CSRF protection")
    secret_bytes = secret if isinstance(secret, bytes) else str(secret).encode("utf-8")
    identity = (
        f"v3-device-write:{session.get('user_id')}:{session.get('username', '')}:"
        f"{_current_role()}"
    ).encode("utf-8")
    return hmac.new(secret_bytes, identity, sha256).hexdigest()


def _require_device_access(*, write: bool):
    def decorator(view):
        @wraps(view)
        def decorated(*args, **kwargs):
            if "user_id" not in session:
                return _error("unauthenticated", "需要登录", 401)
            role = _current_role()
            if role == Role.USER.value:
                return _error(
                    "user_scope_unavailable",
                    "用户设备授权范围尚未实现",
                    403,
                )
            if role not in {Role.ADMIN.value, Role.OPERATOR.value}:
                return _error("forbidden", "权限不足", 403)
            if write and role != Role.ADMIN.value:
                return _error(
                    "device_write_forbidden",
                    "只有管理员可以修改设备档案",
                    403,
                )
            if write:
                expected = _csrf_token()
                supplied = request.headers.get(_CSRF_HEADER)
                if not supplied:
                    return _error("csrf_token_missing", "缺少 CSRF token", 403)
                if not hmac.compare_digest(expected, supplied):
                    return _error("csrf_token_invalid", "CSRF token 无效", 403)
            return view(*args, **kwargs)

        return decorated

    return decorator


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
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ApiInputError(
            "unknown_fields",
            "请求包含不允许的字段",
        )
    missing = sorted(field for field in required if field not in payload)
    if missing:
        raise ApiInputError(
            "missing_fields",
            "缺少必填字段：" + ", ".join(missing),
        )
    return payload


def _actor() -> DeviceActor:
    return DeviceActor(
        user_id=session["user_id"],
        username=session.get("username", ""),
        role=_current_role(),
    )


def _handle(callable_):
    try:
        return callable_()
    except ApiInputError as exc:
        return _error(exc.code, str(exc), exc.status)
    except DeviceNotFoundError as exc:
        return _error(exc.code, str(exc), 404)
    except DeviceHasHistoryError as exc:
        return _error(exc.code, str(exc), 409, details=exc.references)
    except (
        DeviceIdConflictError,
        DeviceIdentityConflictError,
        ProfileVersionConflictError,
        ConfirmationMismatchError,
        DeviceLifecycleConflictError,
    ) as exc:
        return _error(exc.code, str(exc), 409)
    except DeviceManagementError as exc:
        return _error(exc.code, str(exc), 400)
    except V3DatabaseUnavailable:
        return _error(
            "v3_database_unavailable",
            "v3 数据库不存在、未升级或暂时不可用",
            503,
        )
    except sqlite3.Error:
        return _error("device_store_unavailable", "设备存储暂时不可用", 503)


def _single_query(name: str) -> str | None:
    values = request.args.getlist(name)
    if len(values) > 1:
        raise ApiInputError("invalid_query", f"查询参数 {name} 不得重复")
    return values[0] if values else None


def _query_integer(name: str, default: int) -> int:
    raw = _single_query(name)
    if raw is None:
        return default
    if not _NON_NEGATIVE_INTEGER.fullmatch(raw):
        raise ApiInputError("invalid_query", f"查询参数 {name} 必须是非负整数")
    return int(raw)


def _query_retired() -> bool | None:
    raw = _single_query("retired")
    if raw is None:
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise ApiInputError("invalid_query", "retired 必须是 true 或 false")


def create_v3_devices_blueprint(
    database_path: str | Path | None,
    *,
    clock: Callable | None = None,
) -> Blueprint:
    """Build device routes without opening or migrating the database."""
    service = DeviceManagementService(database_path, clock=clock)
    blueprint = Blueprint("v3_devices", __name__)

    @blueprint.before_request
    def assign_request_id():
        g.v3_request_id = str(uuid4())

    @blueprint.after_request
    def attach_security_headers(response):
        response.headers["X-Request-ID"] = g.v3_request_id
        response.headers["Cache-Control"] = "no-store"
        if "user_id" in session:
            response.headers[_CSRF_HEADER] = _csrf_token()
        return response

    @blueprint.get("/api/v3/devices")
    @_require_device_access(write=False)
    def list_devices():
        def execute():
            allowed = {
                "search",
                "connection_status",
                "operation_mode",
                "area_id",
                "retired",
                "limit",
                "offset",
            }
            unknown = sorted(set(request.args) - allowed)
            if unknown:
                raise ApiInputError("unknown_query_parameters", "存在不支持的查询参数")
            return jsonify(
                service.list_devices(
                    search=_single_query("search"),
                    connection_status=_single_query("connection_status"),
                    operation_mode=_single_query("operation_mode"),
                    area_id=_single_query("area_id"),
                    retired=_query_retired(),
                    limit=_query_integer("limit", 50),
                    offset=_query_integer("offset", 0),
                )
            )

        return _handle(execute)

    @blueprint.get("/api/v3/devices/<device_id>")
    @_require_device_access(write=False)
    def get_device(device_id: str):
        return _handle(lambda: jsonify({"device": service.get_device(device_id)}))

    @blueprint.post("/api/v3/devices")
    @_require_device_access(write=True)
    def create_device():
        def execute():
            payload = _json_body(
                allowed={
                    "device_id",
                    "mac",
                    "display_name",
                    "device_type",
                    "area_id",
                    "importance",
                    "profile_source",
                },
                required={
                    "device_id",
                    "mac",
                    "display_name",
                    "device_type",
                    "profile_source",
                },
            )
            device = service.create_device(
                **payload,
                actor=_actor(),
                request_id=g.v3_request_id,
            )
            return jsonify({"device": device}), 201

        return _handle(execute)

    @blueprint.patch("/api/v3/devices/<device_id>")
    @_require_device_access(write=True)
    def update_device(device_id: str):
        def execute():
            profile_fields = {"display_name", "device_type", "area_id", "importance"}
            payload = _json_body(
                allowed={*profile_fields, "operation_mode", "expected_profile_version"},
                required={"expected_profile_version"},
            )
            operation_mode = payload.pop("operation_mode", None)
            expected = payload.pop("expected_profile_version")
            if operation_mode is not None:
                if payload:
                    raise ApiInputError(
                        "mixed_update_not_allowed",
                        "运行模式与档案字段必须分开修改",
                    )
                result = service.set_operation_mode(
                    device_id,
                    operation_mode=operation_mode,
                    expected_profile_version=expected,
                    actor=_actor(),
                    request_id=g.v3_request_id,
                )
            else:
                result = service.update_profile(
                    device_id,
                    changes=payload,
                    expected_profile_version=expected,
                    actor=_actor(),
                    request_id=g.v3_request_id,
                )
            return jsonify({"device": result})

        return _handle(execute)

    @blueprint.post("/api/v3/devices/<device_id>/retire")
    @_require_device_access(write=True)
    def retire_device(device_id: str):
        def execute():
            payload = _json_body(
                allowed={"reason", "expected_profile_version"},
                required={"reason", "expected_profile_version"},
            )
            return jsonify(
                {
                    "device": service.retire_device(
                        device_id,
                        reason=payload["reason"],
                        expected_profile_version=payload["expected_profile_version"],
                        actor=_actor(),
                        request_id=g.v3_request_id,
                    )
                }
            )

        return _handle(execute)

    @blueprint.post("/api/v3/devices/<device_id>/restore")
    @_require_device_access(write=True)
    def restore_device(device_id: str):
        def execute():
            payload = _json_body(
                allowed={"expected_profile_version"},
                required={"expected_profile_version"},
            )
            return jsonify(
                {
                    "device": service.restore_device(
                        device_id,
                        expected_profile_version=payload["expected_profile_version"],
                        actor=_actor(),
                        request_id=g.v3_request_id,
                    )
                }
            )

        return _handle(execute)

    @blueprint.delete("/api/v3/devices/<device_id>")
    @_require_device_access(write=True)
    def delete_device(device_id: str):
        def execute():
            payload = _json_body(
                allowed={"confirmation"}, required={"confirmation"}
            )
            return jsonify(
                service.delete_device(
                    device_id,
                    confirmation=payload["confirmation"],
                    actor=_actor(),
                    request_id=g.v3_request_id,
                )
            )

        return _handle(execute)

    return blueprint


__all__ = ["create_v3_devices_blueprint"]
