"""Admin/operator API for quarantined device candidates."""
from functools import wraps
import hmac
import re
import sqlite3
from pathlib import Path
from typing import Callable
from uuid import uuid4

from flask import Blueprint, g, jsonify, request, session

from contracts import Role
from services.auth import effective_role
from services.device_discovery import (
    CandidateIdentityConflictError,
    CandidateStateConflictError,
    CandidateVersionConflictError,
    DeviceDiscoveryError,
    DeviceDiscoveryService,
    DiscoveryNotFoundError,
)
from services.device_management import DeviceActor, DeviceIdConflictError, DeviceIdentityConflictError, DeviceManagementError
from services.realtime_events import V3DatabaseUnavailable
from api.v3_devices import _csrf_token, _json_body, ApiInputError


_INTEGER = re.compile(r"^(0|[1-9][0-9]*)$")
_CSRF_HEADER = "X-CSRF-Token"


def create_v3_device_discovery_blueprint(database_path: str | Path | None, *, clock: Callable | None = None) -> Blueprint:
    service = DeviceDiscoveryService(database_path, clock=clock)
    blueprint = Blueprint("v3_device_discovery", __name__)

    @blueprint.before_request
    def request_id():
        g.v3_request_id = str(uuid4())

    @blueprint.after_request
    def response_headers(response):
        response.headers["X-Request-ID"] = g.v3_request_id
        response.headers["Cache-Control"] = "no-store"
        if "user_id" in session:
            response.headers[_CSRF_HEADER] = _csrf_token()
        return response

    def error(code: str, message: str, status: int, details: dict | None = None):
        body = {"code": code, "message": message, "request_id": g.v3_request_id}
        if details is not None:
            body["details"] = details
        return jsonify({"error": body}), status

    def access(*, write: bool):
        def decorate(view):
            @wraps(view)
            def wrapped(*args, **kwargs):
                if request.headers.get("Authorization", "").lower().startswith("bearer "):
                    return error("mobile_token_forbidden", "此接口不接受移动令牌", 401)
                if "user_id" not in session:
                    return error("unauthenticated", "需要登录", 401)
                role = effective_role(session.get("username", ""), session.get("role"))
                session["role"] = role
                if role == Role.USER.value:
                    return error("user_scope_unavailable", "普通用户不能访问设备发现管理", 403)
                if role not in {Role.ADMIN.value, Role.OPERATOR.value}:
                    return error("forbidden", "权限不足", 403)
                if write:
                    if role != Role.ADMIN.value:
                        return error("discovery_write_forbidden", "只有管理员可以操作发现候选", 403)
                    expected = _csrf_token()
                    supplied = request.headers.get(_CSRF_HEADER, "")
                    if not supplied:
                        return error("csrf_token_missing", "缺少 CSRF token", 403)
                    if not hmac.compare_digest(expected, supplied):
                        return error("csrf_token_invalid", "CSRF token 无效", 403)
                return view(*args, **kwargs)
            return wrapped
        return decorate

    def run(call):
        try:
            return call()
        except ApiInputError as exc:
            return error(exc.code, str(exc), exc.status)
        except DiscoveryNotFoundError as exc:
            return error(exc.code, str(exc), 404)
        except (CandidateVersionConflictError, CandidateStateConflictError,
                CandidateIdentityConflictError, DeviceIdConflictError,
                DeviceIdentityConflictError) as exc:
            return error(exc.code, str(exc), 409)
        except (DeviceManagementError, DeviceDiscoveryError) as exc:
            return error(getattr(exc, "code", "invalid_discovery_request"), str(exc), 400)
        except V3DatabaseUnavailable:
            return error("v3_database_unavailable", "v3 数据库不存在、未升级或暂时不可用", 503)
        except sqlite3.Error:
            return error("device_discovery_store_unavailable", "设备发现服务暂时不可用", 503)

    def one(name: str) -> str | None:
        values = request.args.getlist(name)
        if len(values) > 1:
            raise ApiInputError("invalid_query", f"查询参数 {name} 不得重复")
        return values[0] if values else None

    def integer(name: str, default: int) -> int:
        value = one(name)
        if value is None:
            return default
        if not _INTEGER.fullmatch(value):
            raise ApiInputError("invalid_query", f"查询参数 {name} 必须是非负整数")
        return int(value)

    def boolean(name: str) -> bool | None:
        value = one(name)
        if value is None:
            return None
        if value not in {"true", "false"}:
            raise ApiInputError("invalid_query", f"查询参数 {name} 必须是 true 或 false")
        return value == "true"

    def actor() -> DeviceActor:
        return DeviceActor(session["user_id"], session.get("username", ""), effective_role(session.get("username", ""), session.get("role")))

    @blueprint.get("/api/v3/devices/discovered")
    @access(write=False)
    def list_candidates():
        def execute():
            allowed = {"status", "source", "search", "conflict", "first_seen_from", "first_seen_to", "last_seen_from", "last_seen_to", "limit", "offset"}
            if set(request.args) - allowed:
                raise ApiInputError("unknown_query_parameters", "存在不支持的查询参数")
            role = effective_role(session.get("username", ""), session.get("role"))
            result = service.list_candidates(
                status=one("status"), source=one("source"), search=one("search"), conflict=boolean("conflict"),
                first_seen_from=one("first_seen_from"), first_seen_to=one("first_seen_to"),
                last_seen_from=one("last_seen_from"), last_seen_to=one("last_seen_to"),
                limit=integer("limit", 50), offset=integer("offset", 0),
                include_identity=role == Role.ADMIN.value,
            )
            return jsonify(result)
        return run(execute)

    @blueprint.get("/api/v3/devices/discovered/<candidate_id>")
    @access(write=False)
    def get_candidate(candidate_id: str):
        role = effective_role(session.get("username", ""), session.get("role"))
        return run(lambda: jsonify({"candidate": service.get_candidate(candidate_id, include_identity=role == Role.ADMIN.value)}))

    @blueprint.post("/api/v3/devices/discovered/<candidate_id>/claim")
    @access(write=True)
    def claim(candidate_id: str):
        def execute():
            payload = _json_body(
                allowed={"expected_candidate_version", "device_id", "display_name", "device_type", "area_id", "importance", "profile_source", "resolve_identity_conflict"},
                required={"expected_candidate_version", "device_id", "display_name", "device_type", "area_id", "importance", "profile_source"},
            )
            result = service.claim_candidate(candidate_id, actor=actor(), request_id=g.v3_request_id, **payload)
            return jsonify(result), 201
        return run(execute)

    @blueprint.post("/api/v3/devices/discovered/<candidate_id>/ignore")
    @access(write=True)
    def ignore(candidate_id: str):
        def execute():
            payload = _json_body(allowed={"expected_candidate_version", "reason"}, required={"expected_candidate_version", "reason"})
            return jsonify({"candidate": service.ignore_candidate(candidate_id, actor=actor(), request_id=g.v3_request_id, **payload)})
        return run(execute)

    @blueprint.post("/api/v3/devices/discovered/<candidate_id>/restore")
    @access(write=True)
    def restore(candidate_id: str):
        def execute():
            payload = _json_body(allowed={"expected_candidate_version"}, required={"expected_candidate_version"})
            return jsonify({"candidate": service.restore_candidate(candidate_id, actor=actor(), request_id=g.v3_request_id, **payload)})
        return run(execute)

    blueprint.discovery_service = service
    return blueprint


__all__ = ["create_v3_device_discovery_blueprint"]
