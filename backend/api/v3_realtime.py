"""Dependency-injected v3 monitor snapshot and persistent SSE Blueprint."""
from functools import wraps
from pathlib import Path
import re
from typing import Callable
from uuid import uuid4

from flask import Blueprint, Response, g, jsonify, request, session

from contracts import Role
from services.auth import effective_role
from services.monitor_snapshot import MonitorSnapshotService
from services.realtime_events import (
    RealtimeEventError,
    RealtimeEventStore,
    V3DatabaseUnavailable,
)
from services.sse_stream import SseEventStream


_CURSOR_PATTERN = re.compile(r"^(0|[1-9][0-9]*)$")


def _error(code: str, message: str, status: int):
    return (
        jsonify(
            {
                "error": {
                    "code": code,
                    "message": message,
                    "request_id": g.v3_request_id,
                }
            }
        ),
        status,
    )


def _require_current_operator(view):
    @wraps(view)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return _error("unauthenticated", "需要登录", 401)
        role = effective_role(session.get("username", ""), session.get("role"))
        session["role"] = role
        if role not in {Role.ADMIN.value, Role.OPERATOR.value}:
            return _error(
                "user_scope_unavailable",
                "用户设备授权范围尚未实现",
                403,
            )
        return view(*args, **kwargs)

    return decorated


def _event_cursor_from_request() -> int:
    header_value = request.headers.get("Last-Event-ID")
    query_present = "after" in request.args
    raw = header_value if header_value not in (None, "") else None
    if raw is None and query_present:
        raw = request.args.get("after", "")
    if raw is None:
        return 0
    if not _CURSOR_PATTERN.fullmatch(raw):
        raise ValueError("cursor must be a non-negative integer")
    value = int(raw)
    if value > (1 << 63) - 1:
        raise ValueError("cursor exceeds supported range")
    return value


def create_v3_realtime_blueprint(
    database_path: str | Path | None,
    *,
    clock: Callable | None = None,
    waiter: Callable[[float], None] | None = None,
    monotonic_clock: Callable[[], float] | None = None,
    replay_limit: int = 256,
    poll_interval: float = 1.0,
    keepalive_interval: float = 15.0,
    max_idle_cycles: int | None = None,
) -> Blueprint:
    """Build routes without opening the database or starting background work."""
    monitor = MonitorSnapshotService(database_path, clock=clock)
    event_store = RealtimeEventStore(database_path)
    stream_options = {
        "replay_limit": replay_limit,
        "poll_interval": poll_interval,
        "keepalive_interval": keepalive_interval,
        "max_idle_cycles": max_idle_cycles,
    }
    if waiter is not None:
        stream_options["waiter"] = waiter
    if monotonic_clock is not None:
        stream_options["monotonic_clock"] = monotonic_clock
    event_stream = SseEventStream(event_store, **stream_options)

    blueprint = Blueprint("v3_realtime", __name__)

    @blueprint.before_request
    def assign_request_id():
        g.v3_request_id = str(uuid4())

    @blueprint.after_request
    def attach_request_id(response):
        response.headers["X-Request-ID"] = g.v3_request_id
        return response

    @blueprint.get("/api/v3/monitor")
    @_require_current_operator
    def monitor_snapshot():
        try:
            payload = monitor.snapshot()
        except V3DatabaseUnavailable:
            return _error(
                "v3_database_unavailable",
                "v3 数据库不存在、未升级或暂时不可用",
                503,
            )
        response = jsonify(payload)
        response.headers["Cache-Control"] = "no-store"
        return response

    @blueprint.get("/api/v3/events")
    @_require_current_operator
    def realtime_events():
        try:
            after = _event_cursor_from_request()
        except ValueError:
            return _error("invalid_event_cursor", "事件游标必须是非负整数", 400)
        try:
            initial_batch = event_stream.prepare(after)
        except V3DatabaseUnavailable:
            return _error(
                "v3_database_unavailable",
                "v3 数据库不存在、未升级或暂时不可用",
                503,
            )
        except RealtimeEventError:
            return _error("event_stream_unavailable", "事件流暂时不可用", 503)

        response = Response(
            event_stream.generate(after, initial_batch=initial_batch),
            content_type="text/event-stream; charset=utf-8",
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-Accel-Buffering"] = "no"
        response.headers["Connection"] = "keep-alive"
        return response

    return blueprint


__all__ = ["create_v3_realtime_blueprint"]
