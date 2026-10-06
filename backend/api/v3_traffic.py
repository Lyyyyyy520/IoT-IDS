"""Read-only v3 device traffic and peer APIs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Callable
from uuid import uuid4

from flask import Blueprint, g, jsonify, request, session

from contracts import Role
from services.auth import effective_role
from services.device_traffic import (
    DeviceTrafficService,
    TrafficDeviceNotFound,
    TrafficQueryError,
    TrafficStoreUnavailable,
    TrafficValidationError,
)


class ApiInputError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _error(code: str, message: str, status: int):
    return jsonify({"error": {
        "code": code, "message": message, "request_id": g.v3_request_id,
    }}), status


def _role() -> str:
    return effective_role(session.get("username", ""), session.get("role"))


def _require_traffic_access(view):
    @wraps(view)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return _error("unauthenticated", "需要登录", 401)
        role = _role()
        if role == Role.USER.value:
            return _error(
                "user_scope_unavailable", "用户设备授权范围尚未实现", 403
            )
        if role not in {Role.ADMIN.value, Role.OPERATOR.value}:
            return _error("forbidden", "权限不足", 403)
        return view(*args, **kwargs)

    return decorated


def _one(name: str) -> str | None:
    values = request.args.getlist(name)
    if len(values) > 1:
        raise ApiInputError("duplicate_query_parameter", f"{name} 不能重复")
    return values[0].strip() if values else None


def _timestamp(name: str, default: datetime) -> datetime:
    value = _one(name)
    if value is None:
        return default
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ApiInputError("invalid_timestamp", f"{name} 不是有效的 ISO 8601 时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ApiInputError("timezone_required", f"{name} 必须包含时区")
    return parsed.astimezone(timezone.utc)


def _integer(name: str, default: int) -> int:
    value = _one(name)
    if value is None:
        return default
    if not value.isascii() or not value.isdigit():
        raise ApiInputError("invalid_query", f"{name} 必须是非负整数")
    return int(value)


def _execute(action):
    try:
        return jsonify(action())
    except ApiInputError as exc:
        return _error(exc.code, str(exc), 400)
    except TrafficDeviceNotFound:
        return _error("device_not_found", "设备不存在", 404)
    except (TrafficQueryError, TrafficValidationError) as exc:
        return _error(exc.code, str(exc), 400)
    except TrafficStoreUnavailable:
        return _error("database_unavailable", "v3 流量数据库尚未准备", 503)


def create_v3_traffic_blueprint(
    service: DeviceTrafficService,
    *,
    clock: Callable[[], datetime] | None = None,
) -> Blueprint:
    """Build routes without opening a database or starting capture threads."""
    current_time = clock or (lambda: datetime.now(timezone.utc))
    blueprint = Blueprint("v3_traffic", __name__)

    @blueprint.before_request
    def assign_request_id():
        g.v3_request_id = str(uuid4())

    @blueprint.after_request
    def attach_headers(response):
        response.headers["X-Request-ID"] = g.v3_request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    def bounds() -> tuple[datetime, datetime]:
        now = current_time().astimezone(timezone.utc)
        return _timestamp("from", now - timedelta(hours=1)), _timestamp("to", now)

    @blueprint.get("/api/v3/devices/<device_id>/traffic")
    @_require_traffic_access
    def device_traffic(device_id: str):
        def action():
            allowed = {"from", "to", "resolution", "protocol"}
            if set(request.args) - allowed:
                raise ApiInputError(
                    "unknown_query_parameters", "存在不支持的查询参数"
                )
            start, end = bounds()
            result = service.query_traffic(
                device_id,
                start=start,
                end=end,
                resolution=_one("resolution") or "auto",
                protocol=_one("protocol"),
            )
            return {
                "api_version": "v3",
                "traffic_schema_version": 1,
                "generated_at": current_time().astimezone(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                ),
                **result,
            }

        return _execute(action)

    @blueprint.get("/api/v3/devices/<device_id>/peers")
    @_require_traffic_access
    def device_peers(device_id: str):
        def action():
            allowed = {"from", "to", "direction", "protocol", "sort", "limit", "offset"}
            if set(request.args) - allowed:
                raise ApiInputError(
                    "unknown_query_parameters", "存在不支持的查询参数"
                )
            start, end = bounds()
            direction = _one("direction")
            if direction == "all":
                direction = None
            result = service.query_peers(
                device_id,
                start=start,
                end=end,
                direction=direction,
                protocol=_one("protocol"),
                sort_by=_one("sort") or "bytes",
                limit=_integer("limit", 50),
                offset=_integer("offset", 0),
                include_peer_ip=_role() == Role.ADMIN.value,
            )
            return {
                "api_version": "v3",
                "traffic_schema_version": 1,
                "generated_at": current_time().astimezone(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                ),
                **result,
            }

        return _execute(action)

    return blueprint


__all__ = ["create_v3_traffic_blueprint"]
