"""Persistent realtime event log used by state transactions and SSE replay."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from contracts import REALTIME_EVENT_TYPES, is_valid_device_id
from v3_database import (
    V3_REALTIME_EVENT_MIGRATION,
    connect_v3_existing,
)


class RealtimeEventError(RuntimeError):
    """Base error for persistent realtime event operations."""


class V3DatabaseUnavailable(RealtimeEventError):
    """Raised when an explicit database cannot serve the v3 realtime contract."""


@dataclass(frozen=True)
class ReplayBatch:
    events: tuple[dict, ...]
    current_cursor: int
    requires_snapshot: bool = False
    reason: str | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RealtimeEventError("event timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def ensure_realtime_schema(connection: sqlite3.Connection) -> None:
    """Verify migration v3 without creating or repairing any schema object."""
    try:
        migration = connection.execute(
            "SELECT name, checksum FROM v3_schema_migrations WHERE version = 3"
        ).fetchone()
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'v3_realtime_events'"
        ).fetchone()
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(v3_realtime_events)")
        }
    except sqlite3.Error as exc:
        raise V3DatabaseUnavailable("v3 realtime schema is unavailable") from exc
    if not migration:
        raise V3DatabaseUnavailable("v3 realtime migration has not been applied")
    if (
        migration["name"] != V3_REALTIME_EVENT_MIGRATION.name
        or migration["checksum"] != V3_REALTIME_EVENT_MIGRATION.checksum
    ):
        raise V3DatabaseUnavailable("v3 realtime migration checksum mismatch")
    required_columns = {
        "event_id",
        "event_type",
        "occurred_at",
        "device_id",
        "state_version",
        "payload_json",
    }
    if not table or not required_columns <= columns:
        raise V3DatabaseUnavailable("v3 realtime event table is incomplete")


def current_event_cursor(connection: sqlite3.Connection) -> int:
    """Return the highest allocated event id, including a retained-log gap."""
    maximum = connection.execute(
        "SELECT COALESCE(MAX(event_id), 0) FROM v3_realtime_events"
    ).fetchone()[0]
    sequence = connection.execute(
        "SELECT seq FROM sqlite_sequence WHERE name = 'v3_realtime_events'"
    ).fetchone()
    return max(int(maximum), int(sequence[0]) if sequence else 0)


def append_realtime_event(
    connection: sqlite3.Connection,
    *,
    event_type: str,
    occurred_at: datetime,
    device_id: str | None,
    state_version: int | None,
    payload: dict,
) -> dict:
    """Append one event inside a caller-owned transaction."""
    if event_type not in REALTIME_EVENT_TYPES:
        raise RealtimeEventError("unsupported realtime event type")
    if device_id is not None and not is_valid_device_id(device_id):
        raise RealtimeEventError("invalid event device_id")
    if state_version is not None and (
        type(state_version) is not int or state_version < 0
    ):
        raise RealtimeEventError("invalid event state_version")
    if not isinstance(payload, dict):
        raise RealtimeEventError("event payload must be an object")
    occurred_text = _iso(occurred_at)
    payload_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    cursor = connection.execute(
        "INSERT INTO v3_realtime_events "
        "(event_type, occurred_at, device_id, state_version, payload_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            event_type,
            occurred_text,
            device_id,
            state_version,
            payload_json,
        ),
    )
    return {
        "event_id": int(cursor.lastrowid),
        "event_type": event_type,
        "occurred_at": occurred_text,
        "device_id": device_id,
        "state_version": state_version,
        "payload": payload,
    }


class RealtimeEventStore:
    """Open the explicit database per operation so clients hold no long read lock."""

    def __init__(self, database_path: str | Path | None):
        self.database_path = Path(database_path) if database_path else None

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        if self.database_path is None:
            raise V3DatabaseUnavailable("v3 database path is not configured")
        try:
            connection = connect_v3_existing(self.database_path)
        except (FileNotFoundError, sqlite3.Error) as exc:
            raise V3DatabaseUnavailable("v3 database is unavailable") from exc
        try:
            ensure_realtime_schema(connection)
            yield connection
        finally:
            connection.close()

    def current_cursor(self) -> int:
        with self.connection() as connection:
            try:
                cursor = current_event_cursor(connection)
            except sqlite3.Error as exc:
                raise V3DatabaseUnavailable("cannot read realtime event cursor") from exc
        return cursor

    def append(
        self,
        *,
        event_type: str,
        occurred_at: datetime,
        device_id: str | None = None,
        state_version: int | None = None,
        payload: dict | None = None,
    ) -> dict:
        with self.connection() as connection:
            try:
                event = append_realtime_event(
                    connection,
                    event_type=event_type,
                    occurred_at=occurred_at,
                    device_id=device_id,
                    state_version=state_version,
                    payload=payload or {},
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return event

    def read_after(self, after: int, *, limit: int) -> ReplayBatch:
        if type(after) is not int or after < 0:
            raise RealtimeEventError("event cursor must be a non-negative integer")
        if type(limit) is not int or limit <= 0:
            raise RealtimeEventError("replay limit must be a positive integer")
        with self.connection() as connection:
            try:
                connection.execute("BEGIN")
                bounds = connection.execute(
                    "SELECT MIN(event_id), MAX(event_id) FROM v3_realtime_events"
                ).fetchone()
                minimum = bounds[0]
                maximum = bounds[1]
                current_cursor = current_event_cursor(connection)
                if maximum is None:
                    connection.commit()
                    if current_cursor == 0 and after == 0:
                        return ReplayBatch(events=(), current_cursor=0)
                    return ReplayBatch(
                        events=(),
                        current_cursor=current_cursor,
                        requires_snapshot=True,
                        reason=(
                            "cursor_ahead"
                            if current_cursor == 0
                            else "event_log_empty"
                        ),
                    )
                minimum_cursor = int(minimum)
                if after > current_cursor:
                    connection.commit()
                    return ReplayBatch(
                        events=(),
                        current_cursor=current_cursor,
                        requires_snapshot=True,
                        reason="cursor_ahead",
                    )
                if after < minimum_cursor - 1:
                    connection.commit()
                    return ReplayBatch(
                        events=(),
                        current_cursor=current_cursor,
                        requires_snapshot=True,
                        reason="cursor_before_replay_window",
                    )
                if after >= minimum_cursor and after > 0:
                    cursor_exists = connection.execute(
                        "SELECT 1 FROM v3_realtime_events WHERE event_id = ?",
                        (after,),
                    ).fetchone()
                    if not cursor_exists:
                        connection.commit()
                        return ReplayBatch(
                            events=(),
                            current_cursor=current_cursor,
                            requires_snapshot=True,
                            reason="cursor_not_in_event_log",
                        )
                rows = connection.execute(
                    "SELECT event_id, event_type, occurred_at, device_id, "
                    "state_version, payload_json FROM v3_realtime_events "
                    "WHERE event_id > ? ORDER BY event_id LIMIT ?",
                    (after, limit + 1),
                ).fetchall()
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise V3DatabaseUnavailable("cannot replay realtime events") from exc

        if len(rows) > limit:
            return ReplayBatch(
                events=(),
                current_cursor=current_cursor,
                requires_snapshot=True,
                reason="replay_limit_exceeded",
            )
        expected_id = after + 1
        events = []
        for row in rows:
            event_id = int(row["event_id"])
            if event_id != expected_id:
                return ReplayBatch(
                    events=(),
                    current_cursor=current_cursor,
                    requires_snapshot=True,
                    reason="event_log_gap",
                )
            if row["event_type"] not in REALTIME_EVENT_TYPES:
                return ReplayBatch(
                    events=(),
                    current_cursor=current_cursor,
                    requires_snapshot=True,
                    reason="unsupported_event_in_log",
                )
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                return ReplayBatch(
                    events=(),
                    current_cursor=current_cursor,
                    requires_snapshot=True,
                    reason="invalid_event_payload",
                )
            if not isinstance(payload, dict):
                return ReplayBatch(
                    events=(),
                    current_cursor=current_cursor,
                    requires_snapshot=True,
                    reason="invalid_event_payload",
                )
            events.append(
                {
                    "event_id": event_id,
                    "event_type": row["event_type"],
                    "occurred_at": row["occurred_at"],
                    "device_id": row["device_id"],
                    "state_version": row["state_version"],
                    "payload": payload,
                }
            )
            expected_id += 1
        if events and events[-1]["event_id"] != current_cursor:
            return ReplayBatch(
                events=(),
                current_cursor=current_cursor,
                requires_snapshot=True,
                reason="event_log_gap",
            )
        return ReplayBatch(events=tuple(events), current_cursor=current_cursor)


__all__ = [
    "RealtimeEventError",
    "RealtimeEventStore",
    "ReplayBatch",
    "V3DatabaseUnavailable",
    "append_realtime_event",
    "current_event_cursor",
    "ensure_realtime_schema",
]
