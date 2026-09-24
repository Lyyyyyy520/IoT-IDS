"""Transactional v3 incidents, scoped mobile notices, and support workflow."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable, Iterator
from uuid import uuid4

from config import MobileSecuritySettings
from contracts import IncidentRole, IncidentStage, Role, enum_values, is_valid_device_id
from services.mobile_access import MobilePrincipal
from services.realtime_events import append_realtime_event
from v3_database import (
    V3_INCIDENT_WORKFLOW_MIGRATION,
    connect_v3_existing,
    read_applied_migrations,
)


Clock = Callable[[], datetime]
FaultInjector = Callable[[str], None]
_SEVERITIES = {"info", "low", "medium", "high", "critical"}
_SOURCES = {"manual", "rule", "system"}
_INCIDENT_STATUSES = set(enum_values(IncidentStage))
_INCIDENT_ROLES = set(enum_values(IncidentRole))
_HELP_CATEGORIES = {"device_issue", "security_question", "service_problem", "other"}
_HELP_STATUSES = {"open", "in_progress", "waiting_for_user", "closed"}
_TERMINAL_STATUSES = {"resolved", "false_positive"}
_TRANSITIONS = {
    "open": {"acknowledged", "false_positive"},
    "acknowledged": {"recovering", "resolved", "false_positive"},
    "recovering": {"resolved", "false_positive"},
    "resolved": set(),
    "false_positive": set(),
}
_HELP_TRANSITIONS = {
    "open": {"in_progress", "closed"},
    "in_progress": {"waiting_for_user", "closed"},
    "waiting_for_user": {"in_progress", "closed"},
    "closed": set(),
}
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PHONE = re.compile(r"^[0-9+() .-]{5,32}$")
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
_IPV4 = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
_MAC = re.compile(
    r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![0-9a-f])"
)
_INTERNAL_MARKERS = re.compile(
    r"(?i)\b(?:gnn|graph[_ -]?id|model[_ -]?version|"
    r"rule[_ -]?expression|port\s*[:=]?\s*\d+)\b"
)
_SECRET_MARKERS = re.compile(
    r"(?i)\b(?:authorization|bearer|refresh[_ -]?token|access[_ -]?token|"
    r"pairing[_ -]?code|password)\b"
)


class IncidentWorkflowError(ValueError):
    code = "invalid_incident_request"
    status = 400


class IncidentStoreUnavailable(IncidentWorkflowError):
    code = "incident_store_unavailable"
    status = 503


class IncidentNotFound(IncidentWorkflowError):
    code = "incident_not_found"
    status = 404


class IncidentVersionConflict(IncidentWorkflowError):
    code = "incident_version_conflict"
    status = 409


class IncidentTransitionConflict(IncidentWorkflowError):
    code = "incident_transition_conflict"
    status = 409


class IncidentSourceForbidden(IncidentWorkflowError):
    code = "incident_source_forbidden"
    status = 403


class MobileResourceUnavailable(IncidentWorkflowError):
    code = "mobile_resource_unavailable"
    status = 404


class NoticeCursorError(IncidentWorkflowError):
    code = "invalid_notice_cursor"
    status = 400


class WorkflowRateLimited(IncidentWorkflowError):
    code = "mobile_rate_limited"
    status = 429


class SupportVersionConflict(IncidentWorkflowError):
    code = "support_config_version_conflict"
    status = 409


class HelpRequestNotFound(IncidentWorkflowError):
    code = "help_request_not_found"
    status = 404


class HelpRequestVersionConflict(IncidentWorkflowError):
    code = "help_request_version_conflict"
    status = 409


class HelpRequestTransitionConflict(IncidentWorkflowError):
    code = "help_request_transition_conflict"
    status = 409


class IdempotencyConflict(IncidentWorkflowError):
    code = "idempotency_conflict"
    status = 409


@dataclass(frozen=True)
class IncidentActor:
    user_id: int | None
    username: str
    role: str

    def validate(self, *, allow_system: bool = False) -> None:
        allowed = {Role.ADMIN.value, Role.OPERATOR.value}
        if allow_system:
            allowed.add("system")
        if self.role not in allowed:
            raise IncidentWorkflowError("actor role is not allowed")
        if self.role != "system" and (
            type(self.user_id) is not int or self.user_id <= 0
        ):
            raise IncidentWorkflowError("actor user_id must be a positive integer")
        _text(self.username, "actor username", maximum=128)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise IncidentWorkflowError(
            "timestamps and injected clocks must be timezone-aware"
        )
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _text(value, field: str, *, minimum: int = 1, maximum: int = 500) -> str:
    if not isinstance(value, str):
        raise IncidentWorkflowError(f"{field} must be a string")
    normalized = value.strip()
    if not minimum <= len(normalized) <= maximum:
        raise IncidentWorkflowError(
            f"{field} length must be between {minimum} and {maximum}"
        )
    if any(
        ord(character) < 32 and character not in "\n\t"
        for character in normalized
    ):
        raise IncidentWorkflowError(f"{field} contains control characters")
    return normalized


def _optional_text(value, field: str, *, maximum: int = 500) -> str | None:
    return None if value is None else _text(value, field, maximum=maximum)


def _public_text(value, field: str, *, maximum: int = 500) -> str:
    normalized = _text(value, field, maximum=maximum)
    if (
        _IPV4.search(normalized)
        or _MAC.search(normalized)
        or _INTERNAL_MARKERS.search(normalized)
    ):
        raise IncidentWorkflowError(
            f"{field} contains restricted technical details"
        )
    return normalized


def _user_message(value) -> str:
    normalized = _text(value, "user_message", maximum=1000)
    if _SECRET_MARKERS.search(normalized) or _MAC.search(normalized):
        raise IncidentWorkflowError(
            "user_message contains restricted secret material"
        )
    return normalized


def _version(value, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise IncidentWorkflowError(f"{field} must be a positive integer")
    return value


def _request_id(value) -> str:
    return _text(value, "request_id", maximum=128)


class IncidentWorkflowService:
    """Open the explicit database per operation and commit workflows atomically."""

    def __init__(
        self,
        database_path: str | Path | None,
        settings: MobileSecuritySettings,
        *,
        clock: Clock | None = None,
        fault_injector: FaultInjector | None = None,
    ):
        self.database_path = (
            Path(database_path).expanduser().resolve()
            if database_path is not None and str(database_path).strip()
            else None
        )
        self.settings = settings.validate()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.fault_injector = fault_injector or (lambda _point: None)
        self._secret = settings.token_secret.encode("utf-8")

    def _now(self) -> datetime:
        return _utc(self.clock())

    def _connect(self) -> sqlite3.Connection:
        if self.database_path is None:
            raise IncidentStoreUnavailable(
                "incident database path is not configured"
            )
        try:
            connection = connect_v3_existing(self.database_path)
            ledger = {
                row["version"]: row
                for row in read_applied_migrations(connection)
            }
            row = ledger.get(V3_INCIDENT_WORKFLOW_MIGRATION.version)
            if (
                row is None
                or row["name"] != V3_INCIDENT_WORKFLOW_MIGRATION.name
                or row["checksum"] != V3_INCIDENT_WORKFLOW_MIGRATION.checksum
            ):
                connection.close()
                raise IncidentStoreUnavailable(
                    "incident migration is unavailable"
                )
            return connection
        except (FileNotFoundError, sqlite3.Error) as exc:
            raise IncidentStoreUnavailable(
                "incident database is unavailable"
            ) from exc

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _digest(self, purpose: str, value: str) -> str:
        return hmac.new(
            self._secret,
            f"{purpose}:{value}".encode("utf-8"),
            sha256,
        ).hexdigest()

    def _audit(
        self,
        connection: sqlite3.Connection,
        *,
        entity_type: str,
        entity_id: str,
        action: str,
        actor: IncidentActor,
        request_id: str,
        occurred_at: datetime,
        result: str = "success",
    ) -> None:
        connection.execute(
            "INSERT INTO v3_incident_workflow_audit "
            "(entity_type,entity_id,action,actor_user_id,actor_username,"
            "actor_role,occurred_at,request_id,result) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                entity_type,
                entity_id,
                action,
                actor.user_id,
                actor.username,
                actor.role,
                _iso(occurred_at),
                request_id,
                result,
            ),
        )

    @staticmethod
    def _mobile_audit(
        connection: sqlite3.Connection,
        *,
        action: str,
        principal: MobilePrincipal,
        request_id: str,
        occurred_at: datetime,
        reason: str,
        result: str = "success",
    ) -> None:
        connection.execute(
            "INSERT INTO v3_mobile_security_audit "
            "(action,user_id,session_id,pairing_id,actor,occurred_at,"
            "request_id,result,stable_reason_code) "
            "VALUES (?,?,?,NULL,?,?,?,?,?)",
            (
                action,
                principal.user_id,
                principal.session_id,
                f"mobile-user:{principal.user_id}",
                _iso(occurred_at),
                request_id,
                result,
                reason,
            ),
        )

    def _consume_rate(
        self,
        connection: sqlite3.Connection,
        *,
        action: str,
        identity: str,
        limit: int,
        now: datetime,
    ) -> bool:
        key = self._digest("workflow-rate", f"{action}:{identity}")
        row = connection.execute(
            "SELECT window_started_at,attempt_count,blocked_until "
            "FROM v3_mobile_rate_limits WHERE action=? AND bucket_key=?",
            (action, key),
        ).fetchone()
        window_start, count, blocked_until = now, 1, None
        if row:
            existing_block = (
                datetime.fromisoformat(
                    row["blocked_until"].replace("Z", "+00:00")
                )
                if row["blocked_until"]
                else None
            )
            if existing_block and now < existing_block:
                connection.execute(
                    "UPDATE v3_mobile_rate_limits SET updated_at=? "
                    "WHERE action=? AND bucket_key=?",
                    (_iso(now), action, key),
                )
                return False
            prior = datetime.fromisoformat(
                row["window_started_at"].replace("Z", "+00:00")
            )
            if now - prior <= timedelta(
                seconds=self.settings.rate_window_seconds
            ):
                window_start = prior
                count = int(row["attempt_count"]) + 1
        if count > limit:
            blocked_until = now + timedelta(
                seconds=self.settings.rate_block_seconds
            )
        connection.execute(
            "INSERT INTO v3_mobile_rate_limits "
            "(action,bucket_key,window_started_at,attempt_count,"
            "blocked_until,updated_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(action,bucket_key) DO UPDATE SET "
            "window_started_at=excluded.window_started_at,"
            "attempt_count=excluded.attempt_count,"
            "blocked_until=excluded.blocked_until,"
            "updated_at=excluded.updated_at",
            (
                action,
                key,
                _iso(window_start),
                count,
                _iso(blocked_until) if blocked_until else None,
                _iso(now),
            ),
        )
        return blocked_until is None

    @staticmethod
    def _incident_row(
        connection: sqlite3.Connection, incident_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM v3_incidents WHERE incident_id=?",
            (incident_id,),
        ).fetchone()
        if row is None:
            raise IncidentNotFound("incident does not exist")
        return row

    @staticmethod
    def _validate_incident_id(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(
            r"inc_[0-9a-f]{32}", value
        ):
            raise IncidentWorkflowError("incident_id is invalid")
        return value

    @staticmethod
    def _device_links(
        connection: sqlite3.Connection, incident_id: str
    ) -> list[dict]:
        rows = connection.execute(
            "SELECT d.device_id,d.incident_role,d.user_visible,"
            "p.display_name,p.device_type,p.area_id "
            "FROM v3_incident_devices d JOIN v3_device_profiles p "
            "ON p.device_id=d.device_id WHERE d.incident_id=? "
            "ORDER BY d.incident_role,d.device_id",
            (incident_id,),
        ).fetchall()
        return [
            {
                "device_id": row["device_id"],
                "incident_role": row["incident_role"],
                "user_visible": bool(row["user_visible"]),
                "display_name": row["display_name"],
                "device_type": row["device_type"],
                "area_id": row["area_id"],
            }
            for row in rows
        ]

    def _admin_detail(
        self, connection: sqlite3.Connection, incident_id: str
    ) -> dict:
        row = self._incident_row(connection, incident_id)
        timeline = [
            dict(item)
            for item in connection.execute(
                "SELECT timeline_id,action,actor_user_id,actor_username,"
                "actor_role,occurred_at,request_id,public_progress,"
                "admin_details,resulting_status,incident_version "
                "FROM v3_incident_timeline WHERE incident_id=? "
                "ORDER BY timeline_id",
                (incident_id,),
            ).fetchall()
        ]
        result = dict(row)
        result["mobile_published"] = bool(result["mobile_published"])
        result["devices"] = self._device_links(connection, incident_id)
        result["timeline"] = timeline
        result["user_preview"] = {
            "user_title": row["user_title"],
            "user_summary": row["user_summary"],
            "public_progress": next(
                (
                    item["public_progress"]
                    for item in reversed(timeline)
                    if item["public_progress"]
                ),
                None,
            ),
        }
        return result

    def create_incident(
        self, *, incident_type, severity, source, admin_title, admin_summary,
        user_title, user_summary, devices, publish_to_mobile: bool,
        first_seen_at: datetime | None, public_progress=None,
        actor: IncidentActor, request_id: str,
    ) -> dict:
        actor.validate(allow_system=True)
        request_id = _request_id(request_id)
        source = _text(source, "source", maximum=32).lower()
        if source not in _SOURCES:
            raise IncidentWorkflowError("source must be manual, rule, or system")
        if source == "manual" and actor.role != Role.ADMIN.value:
            raise IncidentSourceForbidden("only admin can create manual incidents")
        if source in {"rule", "system"} and actor.role != "system":
            raise IncidentSourceForbidden(
                "rule and system incidents require a trusted system actor"
            )
        severity = _text(severity, "severity", maximum=16).lower()
        if severity not in _SEVERITIES:
            raise IncidentWorkflowError("severity is invalid")
        incident_type = _text(incident_type, "incident_type", maximum=64).lower()
        if not _IDENTIFIER.fullmatch(incident_type):
            raise IncidentWorkflowError("incident_type is invalid")
        admin_title = _text(admin_title, "admin_title", maximum=160)
        admin_summary = _text(admin_summary, "admin_summary", maximum=2000)
        user_title = _public_text(user_title, "user_title", maximum=120)
        user_summary = _public_text(user_summary, "user_summary", maximum=500)
        progress = (
            _public_text(public_progress, "public_progress", maximum=500)
            if public_progress is not None else None
        )
        if type(publish_to_mobile) is not bool:
            raise IncidentWorkflowError("publish_to_mobile must be a boolean")
        if not isinstance(devices, list) or not devices:
            raise IncidentWorkflowError("at least one incident device is required")
        normalized: list[tuple[str, str, bool]] = []
        seen = set()
        for item in devices:
            if not isinstance(item, dict) or set(item) != {
                "device_id", "incident_role", "user_visible",
            }:
                raise IncidentWorkflowError("incident device entries have invalid fields")
            device_id, role, visible = (
                item["device_id"], item["incident_role"], item["user_visible"]
            )
            if not isinstance(device_id, str) or not is_valid_device_id(device_id):
                raise IncidentWorkflowError("incident device_id is invalid")
            if role not in _INCIDENT_ROLES or type(visible) is not bool:
                raise IncidentWorkflowError("incident device role or visibility is invalid")
            if visible and role != IncidentRole.AFFECTED.value:
                raise IncidentWorkflowError("only affected devices may be user-visible")
            if (device_id, role) in seen:
                raise IncidentWorkflowError("duplicate incident device role")
            seen.add((device_id, role))
            normalized.append((device_id, role, visible))
        if publish_to_mobile and not any(
            role == IncidentRole.AFFECTED.value and visible
            for _, role, visible in normalized
        ):
            raise IncidentWorkflowError(
                "mobile incidents require a user-visible affected device"
            )
        now = self._now()
        first_seen = _utc(first_seen_at or now)
        if first_seen > now + timedelta(minutes=5):
            raise IncidentWorkflowError("first_seen_at is too far in the future")
        incident_id = f"inc_{uuid4().hex}"
        with self._transaction() as connection:
            device_ids = sorted({item[0] for item in normalized})
            placeholders = ",".join("?" for _ in device_ids)
            found = {
                row[0] for row in connection.execute(
                    "SELECT device_id FROM v3_device_profiles "
                    f"WHERE device_id IN ({placeholders})", device_ids,
                ).fetchall()
            }
            if set(device_ids) != found:
                raise IncidentWorkflowError("one or more incident devices do not exist")
            now_text = _iso(now)
            connection.execute(
                "INSERT INTO v3_incidents "
                "(incident_id,incident_type,severity,status,source,admin_title,"
                "admin_summary,user_title,user_summary,mobile_published,first_seen_at,"
                "last_seen_at,created_at,updated_at,resolved_at,incident_version,"
                "created_by,resolution_summary,false_positive_reason) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,1,?,NULL,NULL)",
                (
                    incident_id, incident_type, severity, "open", source,
                    admin_title, admin_summary, user_title, user_summary,
                    int(publish_to_mobile), _iso(first_seen), _iso(first_seen),
                    now_text, now_text, actor.user_id or 0,
                ),
            )
            connection.executemany(
                "INSERT INTO v3_incident_devices "
                "(incident_id,device_id,incident_role,user_visible,created_at) "
                "VALUES (?,?,?,?,?)",
                [
                    (incident_id, device_id, role, int(visible), now_text)
                    for device_id, role, visible in normalized
                ],
            )
            connection.execute(
                "INSERT INTO v3_incident_timeline "
                "(incident_id,action,actor_user_id,actor_username,actor_role,"
                "occurred_at,request_id,public_progress,admin_details,"
                "resulting_status,incident_version) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
                (
                    incident_id, "opened", actor.user_id, actor.username,
                    actor.role, now_text, request_id, progress,
                    "incident created", "open",
                ),
            )
            self._audit(
                connection, entity_type="incident", entity_id=incident_id,
                action="created", actor=actor, request_id=request_id, occurred_at=now,
            )
            connection.execute(
                "INSERT INTO v3_mobile_notice_changes "
                "(incident_id,user_id,change_kind,incident_version,changed_at) "
                "VALUES (?,NULL,'opened',1,?)", (incident_id, now_text),
            )
            append_realtime_event(
                connection, event_type="incident.opened", occurred_at=now,
                device_id=None, state_version=1,
                payload={
                    "incident_id": incident_id, "status": "open",
                    "severity": severity, "source": source, "incident_version": 1,
                    "affected_device_ids": sorted({
                        device_id for device_id, role, _ in normalized
                        if role == IncidentRole.AFFECTED.value
                    }),
                },
            )
            self.fault_injector("incident_before_commit")
            return self._admin_detail(connection, incident_id)

    def list_incidents(
        self, *, status: str | None = None, severity: str | None = None,
        source: str | None = None, device_id: str | None = None,
        search: str | None = None,
        from_time: str | None = None, to_time: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> dict:
        if status is not None and status not in _INCIDENT_STATUSES:
            raise IncidentWorkflowError("status is invalid")
        if severity is not None and severity not in _SEVERITIES:
            raise IncidentWorkflowError("severity is invalid")
        if source is not None and source not in _SOURCES:
            raise IncidentWorkflowError("source is invalid")
        if device_id is not None and not is_valid_device_id(device_id):
            raise IncidentWorkflowError("device_id is invalid")
        if search is not None:
            search = _text(search.strip(), "search", maximum=160)
        if (
            type(limit) is not int or not 1 <= limit <= 100
            or type(offset) is not int or offset < 0
        ):
            raise IncidentWorkflowError("pagination is invalid")
        conditions, params = [], []
        for column, value in (
            ("i.status", status), ("i.severity", severity), ("i.source", source),
        ):
            if value is not None:
                conditions.append(f"{column}=?")
                params.append(value)
        if device_id is not None:
            conditions.append(
                "EXISTS (SELECT 1 FROM v3_incident_devices d "
                "WHERE d.incident_id=i.incident_id AND d.device_id=?)"
            )
            params.append(device_id)
        if search is not None:
            conditions.append(
                "(LOWER(i.incident_id) LIKE ? OR LOWER(i.incident_type) LIKE ? "
                "OR LOWER(i.admin_title) LIKE ? OR LOWER(i.user_title) LIKE ?)"
            )
            needle = f"%{search.lower()}%"
            params.extend([needle, needle, needle, needle])
        if from_time is not None:
            conditions.append("i.updated_at>=?")
            params.append(from_time)
        if to_time is not None:
            conditions.append("i.updated_at<=?")
            params.append(to_time)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._read() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM v3_incidents i" + where, params
            ).fetchone()[0])
            rows = connection.execute(
                "SELECT i.incident_id,i.incident_type,i.severity,i.status,"
                "i.source,i.admin_title,i.user_title,i.first_seen_at,"
                "i.last_seen_at,i.updated_at,i.resolved_at,i.incident_version,"
                "i.mobile_published,"
                "(SELECT COUNT(*) FROM v3_incident_devices d "
                "WHERE d.incident_id=i.incident_id AND d.incident_role='affected') "
                "AS affected_device_count,"
                "(SELECT t.public_progress FROM v3_incident_timeline t "
                "WHERE t.incident_id=i.incident_id AND t.public_progress IS NOT NULL "
                "ORDER BY t.timeline_id DESC LIMIT 1) AS latest_public_progress "
                "FROM v3_incidents i" + where
                + " ORDER BY i.updated_at DESC,i.incident_id LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            items = [dict(row) for row in rows]
            for item in items:
                item["mobile_published"] = bool(item["mobile_published"])
            return {
                "items": items, "total": total, "limit": limit, "offset": offset,
                "event_cursor": int(connection.execute(
                    "SELECT COALESCE(MAX(event_id),0) FROM v3_realtime_events"
                ).fetchone()[0]),
            }

    def get_incident(self, incident_id: str) -> dict:
        incident_id = self._validate_incident_id(incident_id)
        with self._read() as connection:
            return self._admin_detail(connection, incident_id)

    def transition_incident(
        self, incident_id: str, *, target_status: str,
        expected_incident_version, public_progress=None, admin_details=None,
        resolution_summary=None, false_positive_reason=None,
        actor: IncidentActor, request_id: str,
    ) -> dict:
        actor.validate()
        incident_id = self._validate_incident_id(incident_id)
        expected = _version(
            expected_incident_version, "expected_incident_version"
        )
        if target_status not in _INCIDENT_STATUSES - {"open"}:
            raise IncidentWorkflowError("target status is invalid")
        progress = (
            _public_text(public_progress, "public_progress", maximum=500)
            if public_progress is not None else None
        )
        details = _optional_text(admin_details, "admin_details", maximum=2000)
        resolution = _optional_text(
            resolution_summary, "resolution_summary", maximum=1000
        )
        false_reason = _optional_text(
            false_positive_reason, "false_positive_reason", maximum=1000
        )
        if target_status == "recovering" and progress is None:
            raise IncidentWorkflowError("recovering requires public_progress")
        if target_status == "resolved" and (
            progress is None or resolution is None
        ):
            raise IncidentWorkflowError(
                "resolved requires public_progress and resolution_summary"
            )
        if target_status == "false_positive" and (
            progress is None or false_reason is None
        ):
            raise IncidentWorkflowError(
                "false_positive requires public_progress and reason"
            )
        request_id = _request_id(request_id)
        now = self._now()
        with self._transaction() as connection:
            row = self._incident_row(connection, incident_id)
            if int(row["incident_version"]) != expected:
                raise IncidentVersionConflict("incident version is stale")
            if target_status not in _TRANSITIONS[row["status"]]:
                raise IncidentTransitionConflict(
                    "incident status transition is invalid"
                )
            new_version = expected + 1
            resolved_at = (
                _iso(now) if target_status in _TERMINAL_STATUSES else None
            )
            cursor = connection.execute(
                "UPDATE v3_incidents SET status=?,updated_at=?,last_seen_at=?,"
                "resolved_at=?,incident_version=?,resolution_summary=?,"
                "false_positive_reason=? WHERE incident_id=? AND incident_version=?",
                (
                    target_status, _iso(now), _iso(now), resolved_at, new_version,
                    resolution if target_status == "resolved"
                    else row["resolution_summary"],
                    false_reason if target_status == "false_positive"
                    else row["false_positive_reason"],
                    incident_id, expected,
                ),
            )
            if cursor.rowcount != 1:
                raise IncidentVersionConflict("incident version is stale")
            action = (
                "false_positive" if target_status == "false_positive"
                else target_status
            )
            connection.execute(
                "INSERT INTO v3_incident_timeline "
                "(incident_id,action,actor_user_id,actor_username,actor_role,"
                "occurred_at,request_id,public_progress,admin_details,"
                "resulting_status,incident_version) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    incident_id, action, actor.user_id, actor.username, actor.role,
                    _iso(now), request_id, progress, details, target_status, new_version,
                ),
            )
            self._audit(
                connection, entity_type="incident", entity_id=incident_id,
                action=action, actor=actor, request_id=request_id, occurred_at=now,
            )
            connection.execute(
                "INSERT INTO v3_mobile_notice_changes "
                "(incident_id,user_id,change_kind,incident_version,changed_at) "
                "VALUES (?,NULL,?,?,?)",
                (incident_id, target_status, new_version, _iso(now)),
            )
            event_type = {
                "recovering": "incident.recovering",
                "resolved": "incident.resolved",
            }.get(target_status, "incident.updated")
            append_realtime_event(
                connection, event_type=event_type, occurred_at=now,
                device_id=None, state_version=new_version,
                payload={
                    "incident_id": incident_id, "status": target_status,
                    "severity": row["severity"], "incident_version": new_version,
                    "updated_at": _iso(now),
                },
            )
            self.fault_injector("incident_transition_before_commit")
            return self._admin_detail(connection, incident_id)

    @staticmethod
    def _scope_version(
        connection: sqlite3.Connection, user_id: int
    ) -> int:
        row = connection.execute(
            "SELECT scope_version FROM v3_mobile_scope_sets WHERE user_id=?",
            (user_id,),
        ).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _visible_incident_ids(
        connection: sqlite3.Connection, user_id: int
    ) -> set[str]:
        rows = connection.execute(
            "SELECT DISTINCT i.incident_id FROM v3_incidents i "
            "JOIN v3_incident_devices d ON d.incident_id=i.incident_id "
            "JOIN v3_device_profiles p ON p.device_id=d.device_id "
            "JOIN v3_mobile_user_scopes s ON s.user_id=? "
            "AND s.revoked_at IS NULL AND "
            "((s.scope_kind='device' AND s.scope_value=d.device_id) OR "
            "(s.scope_kind='area' AND s.scope_value=p.area_id)) "
            "WHERE i.mobile_published=1 AND d.incident_role='affected' "
            "AND d.user_visible=1",
            (user_id,),
        ).fetchall()
        return {row[0] for row in rows}

    @staticmethod
    def _default_progress(status: str) -> str:
        return {
            "open": "管理员尚未开始处理",
            "acknowledged": "管理员已收到提醒并正在核查",
            "recovering": "管理员正在处理，设备处于恢复阶段",
            "resolved": "管理员已完成处理",
            "false_positive": "该提醒已结束，无需进一步操作",
        }[status]

    def _mobile_notice(
        self, connection: sqlite3.Connection, incident_id: str, user_id: int
    ) -> dict:
        row = connection.execute(
            "SELECT * FROM v3_incidents WHERE incident_id=?",
            (incident_id,),
        ).fetchone()
        if row is None:
            raise MobileResourceUnavailable("notice is unavailable")
        devices = [
            {
                "device_id": item["device_id"],
                "display_name": item["display_name"],
                "device_type": item["device_type"],
                "area_id": item["area_id"],
            }
            for item in self._device_links(connection, incident_id)
            if item["incident_role"] == "affected"
            and item["user_visible"]
            and self._device_in_scope(
                connection, user_id, item["device_id"]
            )
        ]
        progress = connection.execute(
            "SELECT public_progress FROM v3_incident_timeline "
            "WHERE incident_id=? AND public_progress IS NOT NULL "
            "ORDER BY timeline_id DESC LIMIT 1",
            (incident_id,),
        ).fetchone()
        ack = connection.execute(
            "SELECT first_read_at,acknowledged_at "
            "FROM v3_mobile_notice_acknowledgements "
            "WHERE incident_id=? AND user_id=?",
            (incident_id, user_id),
        ).fetchone()
        return {
            "incident_id": row["incident_id"],
            "user_title": row["user_title"],
            "user_summary": row["user_summary"],
            "severity": row["severity"],
            "affected_devices": devices,
            "first_seen_at": row["first_seen_at"],
            "updated_at": row["updated_at"],
            "status": row["status"],
            "public_progress": (
                progress[0]
                if progress
                else self._default_progress(row["status"])
            ),
            "read": bool(ack and ack["first_read_at"]),
            "first_read_at": ack["first_read_at"] if ack else None,
            "acknowledged": bool(ack and ack["acknowledged_at"]),
            "acknowledged_at": ack["acknowledged_at"] if ack else None,
            "resolved_at": row["resolved_at"],
        }

    @staticmethod
    def _cursor(change_id: int, scope_version: int) -> str:
        return f"{change_id}:{scope_version}"

    @staticmethod
    def _parse_cursor(value: str) -> tuple[int, int]:
        if not isinstance(value, str) or not re.fullmatch(
            r"(?:0|[1-9][0-9]*):(?:0|[1-9][0-9]*)", value
        ):
            raise NoticeCursorError("notice cursor is invalid")
        change, scope = (int(part) for part in value.split(":"))
        return change, scope

    def list_mobile_notices(
        self, principal: MobilePrincipal, *, after: str | None = None,
        view: str = "active", limit: int = 50,
    ) -> dict:
        if view not in {"active", "history", "all"}:
            raise IncidentWorkflowError("notice view is invalid")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise IncidentWorkflowError("notice limit is invalid")
        with self._read() as connection:
            visible = self._visible_incident_ids(
                connection, principal.user_id
            )
            scope_version = self._scope_version(
                connection, principal.user_id
            )
            max_change = int(connection.execute(
                "SELECT COALESCE(MAX(change_id),0) "
                "FROM v3_mobile_notice_changes"
            ).fetchone()[0])
            sequence_row = connection.execute(
                "SELECT seq FROM sqlite_sequence "
                "WHERE name='v3_mobile_notice_changes'"
            ).fetchone()
            cursor_high_water = max(
                max_change, int(sequence_row[0]) if sequence_row else 0
            )
            if after is None:
                notices = [
                    self._mobile_notice(
                        connection, incident_id, principal.user_id
                    )
                    for incident_id in visible
                ]
                if view == "active":
                    notices = [
                        item for item in notices
                        if item["status"] not in _TERMINAL_STATUSES
                    ]
                elif view == "history":
                    notices = [
                        item for item in notices
                        if item["status"] in _TERMINAL_STATUSES
                    ]
                notices.sort(
                    key=lambda item: (
                        item["updated_at"], item["incident_id"]
                    ),
                    reverse=True,
                )
                return {
                    "mode": "snapshot",
                    "notices": notices[:limit],
                    "tombstones": [],
                    "next_cursor": self._cursor(
                        cursor_high_water, scope_version
                    ),
                    "snapshot_required": len(notices) > limit,
                }
            change_id, cursor_scope = self._parse_cursor(after)
            if change_id > cursor_high_water:
                raise NoticeCursorError(
                    "notice cursor is ahead of the server"
                )
            if cursor_scope != scope_version:
                return {
                    "mode": "delta",
                    "notices": [],
                    "tombstones": [],
                    "next_cursor": self._cursor(
                        cursor_high_water, scope_version
                    ),
                    "snapshot_required": True,
                }
            bounds = connection.execute(
                "SELECT MIN(change_id) FROM v3_mobile_notice_changes"
            ).fetchone()
            minimum_change = int(bounds[0]) if bounds[0] is not None else None
            retained_count = int(connection.execute(
                "SELECT COUNT(*) FROM v3_mobile_notice_changes "
                "WHERE change_id>? AND change_id<=?",
                (change_id, cursor_high_water),
            ).fetchone()[0])
            cursor_row_exists = True
            if change_id > 0 and minimum_change is not None and change_id >= minimum_change:
                cursor_row_exists = connection.execute(
                    "SELECT 1 FROM v3_mobile_notice_changes WHERE change_id=?",
                    (change_id,),
                ).fetchone() is not None
            if (
                (minimum_change is not None and change_id < minimum_change - 1)
                or (minimum_change is None and change_id < cursor_high_water)
                or not cursor_row_exists
                or retained_count != cursor_high_water - change_id
            ):
                return {
                    "mode": "delta",
                    "notices": [],
                    "tombstones": [],
                    "next_cursor": self._cursor(
                        cursor_high_water, scope_version
                    ),
                    "snapshot_required": True,
                }
            rows = connection.execute(
                "SELECT change_id,incident_id,user_id,change_kind "
                "FROM v3_mobile_notice_changes WHERE change_id>? "
                "AND (user_id IS NULL OR user_id=?) "
                "ORDER BY change_id LIMIT ?",
                (change_id, principal.user_id, limit + 1),
            ).fetchall()
            if len(rows) > limit:
                return {
                    "mode": "delta",
                    "notices": [],
                    "tombstones": [],
                    "next_cursor": self._cursor(
                        cursor_high_water, scope_version
                    ),
                    "snapshot_required": True,
                }
            changed, tombstones = [], []
            for row in rows:
                if row["incident_id"] not in visible:
                    continue
                status = connection.execute(
                    "SELECT status FROM v3_incidents "
                    "WHERE incident_id=?",
                    (row["incident_id"],),
                ).fetchone()[0]
                if status == "false_positive" or (
                    view == "active"
                    and status in _TERMINAL_STATUSES
                ):
                    tombstones.append({
                        "incident_id": row["incident_id"],
                        "change_id": int(row["change_id"]),
                        "reason": status,
                    })
                elif view != "history" or status in _TERMINAL_STATUSES:
                    changed.append(row["incident_id"])
            changed = list(dict.fromkeys(reversed(changed)))
            notices = [
                self._mobile_notice(
                    connection, item, principal.user_id
                )
                for item in reversed(changed)
            ]
            return {
                "mode": "delta",
                "notices": notices,
                "tombstones": tombstones,
                "next_cursor": self._cursor(
                    cursor_high_water, scope_version
                ),
                "snapshot_required": False,
            }

    def get_mobile_notice(
        self, principal: MobilePrincipal, incident_id: str
    ) -> dict:
        incident_id = self._validate_incident_id(incident_id)
        connection = self._connect()
        try:
            if incident_id not in self._visible_incident_ids(
                connection, principal.user_id
            ):
                now = self._now()
                connection.execute("BEGIN IMMEDIATE")
                allowed = self._consume_rate(
                    connection,
                    action="mobile_notice_failed_query",
                    identity=principal.session_id,
                    limit=30,
                    now=now,
                )
                connection.commit()
                if not allowed:
                    raise WorkflowRateLimited(
                        "too many unavailable notice requests"
                    )
                raise MobileResourceUnavailable(
                    "notice is unavailable"
                )
            return self._mobile_notice(
                connection, incident_id, principal.user_id
            )
        finally:
            connection.close()

    def update_help_request(
        self, help_request_id: str, *, expected_request_version,
        status, public_response, internal_note, assigned_to,
        actor: IncidentActor, request_id: str,
    ) -> dict:
        actor.validate()
        expected = _version(
            expected_request_version, "expected_request_version"
        )
        if status not in _HELP_STATUSES - {"open"}:
            raise IncidentWorkflowError("help status is invalid")
        public = (
            _public_text(
                public_response, "public_response", maximum=1000
            )
            if public_response is not None else None
        )
        internal = _optional_text(
            internal_note, "internal_note", maximum=2000
        )
        if assigned_to is not None and (
            type(assigned_to) is not int or assigned_to <= 0
        ):
            raise IncidentWorkflowError(
                "assigned_to must be a positive integer"
            )
        request_id = _request_id(request_id)
        now = self._now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM v3_help_requests "
                "WHERE help_request_id=?", (help_request_id,),
            ).fetchone()
            if row is None:
                raise HelpRequestNotFound(
                    "help request does not exist"
                )
            if int(row["request_version"]) != expected:
                raise HelpRequestVersionConflict(
                    "help request version is stale"
                )
            if status not in _HELP_TRANSITIONS[row["status"]]:
                raise HelpRequestTransitionConflict(
                    "help request status transition is invalid"
                )
            version = expected + 1
            closed_at = _iso(now) if status == "closed" else None
            cursor = connection.execute(
                "UPDATE v3_help_requests SET status=?,"
                "public_response=?,internal_note=?,assigned_to=?,"
                "updated_at=?,closed_at=?,request_version=? "
                "WHERE help_request_id=? AND request_version=?",
                (
                    status, public, internal, assigned_to, _iso(now),
                    closed_at, version, help_request_id, expected,
                ),
            )
            if cursor.rowcount != 1:
                raise HelpRequestVersionConflict(
                    "help request version is stale"
                )
            connection.execute(
                "INSERT INTO v3_help_request_timeline "
                "(help_request_id,action,actor_user_id,"
                "actor_username,actor_role,occurred_at,request_id,"
                "public_response,internal_note,resulting_status,"
                "request_version) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    help_request_id, "status_updated", actor.user_id,
                    actor.username, actor.role, _iso(now), request_id,
                    public, internal, status, version,
                ),
            )
            self._audit(
                connection, entity_type="help_request",
                entity_id=help_request_id, action="updated",
                actor=actor, request_id=request_id, occurred_at=now,
            )
            self.fault_injector("help_update_before_commit")
            updated = connection.execute(
                "SELECT * FROM v3_help_requests "
                "WHERE help_request_id=?", (help_request_id,),
            ).fetchone()
            return self._help_admin(connection, updated)

    def overview_security(
        self, principal: MobilePrincipal
    ) -> dict:
        with self._read() as connection:
            visible = self._visible_incident_ids(
                connection, principal.user_id
            )
            notices = [
                self._mobile_notice(
                    connection, item, principal.user_id
                )
                for item in visible
            ]
            active = [
                item for item in notices
                if item["status"] not in _TERMINAL_STATUSES
            ]
            active.sort(
                key=lambda item: item["updated_at"], reverse=True
            )
            return {
                "available": True,
                "reason": (
                    "recorded_incident_workflow_available_"
                    "gnn_unavailable"
                ),
                "gnn": {
                    "available": False,
                    "reason": "gnn_capability_unavailable",
                },
                "semantics": (
                    "no_recorded_incidents_is_not_a_safety_assurance"
                ),
                "unread_count": sum(
                    not item["read"] for item in active
                ),
                "unacknowledged_count": sum(
                    not item["acknowledged"] for item in active
                ),
                "recent_notices": active[:3],
            }
    def create_help_request(
        self, principal: MobilePrincipal, *, incident_id, device_id,
        category, user_message, idempotency_key, request_id: str,
    ) -> dict:
        if category not in _HELP_CATEGORIES:
            raise IncidentWorkflowError("help request category is invalid")
        message = _user_message(user_message)
        if not isinstance(idempotency_key, str) or not _IDEMPOTENCY.fullmatch(
            idempotency_key
        ):
            raise IncidentWorkflowError("Idempotency-Key is invalid")
        if incident_id is not None:
            incident_id = self._validate_incident_id(incident_id)
        if device_id is not None and (
            not isinstance(device_id, str)
            or not is_valid_device_id(device_id)
        ):
            raise IncidentWorkflowError("device_id is invalid")
        request_id = _request_id(request_id)
        fingerprint = sha256(json.dumps(
            {
                "incident_id": incident_id, "device_id": device_id,
                "category": category, "message": message,
            },
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        now = self._now()
        actor = IncidentActor(
            principal.user_id, principal.username, Role.USER.value
        )
        with self._transaction() as connection:
            if not self._consume_rate(
                connection, action="mobile_help_create",
                identity=principal.session_id, limit=10, now=now,
            ):
                raise WorkflowRateLimited("too many help requests")
            if incident_id is not None and incident_id not in (
                self._visible_incident_ids(
                    connection, principal.user_id
                )
            ):
                raise MobileResourceUnavailable(
                    "referenced resource is unavailable"
                )
            if device_id is not None and not self._device_in_scope(
                connection, principal.user_id, device_id
            ):
                raise MobileResourceUnavailable(
                    "referenced resource is unavailable"
                )
            prior = connection.execute(
                "SELECT * FROM v3_help_requests WHERE user_id=? "
                "AND mobile_session_id=? AND idempotency_key=?",
                (
                    principal.user_id, principal.session_id,
                    idempotency_key,
                ),
            ).fetchone()
            if prior:
                if prior["request_fingerprint"] != fingerprint:
                    raise IdempotencyConflict(
                        "Idempotency-Key was used for another request"
                    )
                result = self._help_mobile(prior)
                result["idempotent_replay"] = True
                return result
            help_id = f"help_{uuid4().hex}"
            now_text = _iso(now)
            connection.execute(
                "INSERT INTO v3_help_requests "
                "(help_request_id,user_id,mobile_session_id,incident_id,"
                "device_id,category,user_message,status,public_response,"
                "internal_note,assigned_to,idempotency_key,"
                "request_fingerprint,created_at,updated_at,closed_at,"
                "request_version) "
                "VALUES (?,?,?,?,?,?,?,'open',NULL,NULL,NULL,?,?,?, ?,NULL,1)",
                (
                    help_id, principal.user_id, principal.session_id,
                    incident_id, device_id, category, message,
                    idempotency_key, fingerprint, now_text, now_text,
                ),
            )
            connection.execute(
                "INSERT INTO v3_help_request_timeline "
                "(help_request_id,action,actor_user_id,actor_username,"
                "actor_role,occurred_at,request_id,public_response,"
                "internal_note,resulting_status,request_version) "
                "VALUES (?,?,?,?,?,?,?,?,?,'open',1)",
                (
                    help_id, "created", principal.user_id,
                    principal.username, "user", now_text,
                    request_id, None, None,
                ),
            )
            self._audit(
                connection, entity_type="help_request",
                entity_id=help_id, action="created", actor=actor,
                request_id=request_id, occurred_at=now,
            )
            self._mobile_audit(
                connection, action="help_request_created",
                principal=principal, request_id=request_id,
                occurred_at=now, reason="created",
            )
            self.fault_injector("help_request_before_commit")
            row = connection.execute(
                "SELECT * FROM v3_help_requests "
                "WHERE help_request_id=?", (help_id,),
            ).fetchone()
            result = self._help_mobile(row)
            result["idempotent_replay"] = False
            return result

    def list_mobile_help_requests(
        self, principal: MobilePrincipal
    ) -> dict:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM v3_help_requests WHERE user_id=? "
                "ORDER BY updated_at DESC,help_request_id",
                (principal.user_id,),
            ).fetchall()
            return {
                "items": [self._help_mobile(row) for row in rows]
            }

    def get_mobile_help_request(
        self, principal: MobilePrincipal, help_request_id: str
    ) -> dict:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM v3_help_requests "
                "WHERE help_request_id=? AND user_id=?",
                (help_request_id, principal.user_id),
            ).fetchone()
            if row is None:
                now = self._now()
                connection.execute("BEGIN IMMEDIATE")
                allowed = self._consume_rate(
                    connection,
                    action="mobile_help_failed_query",
                    identity=principal.session_id,
                    limit=30,
                    now=now,
                )
                connection.commit()
                if not allowed:
                    raise WorkflowRateLimited(
                        "too many unavailable help request queries"
                    )
                raise MobileResourceUnavailable(
                    "help request is unavailable"
                )
            return self._help_mobile(row)
        finally:
            connection.close()

    @staticmethod
    def _help_admin(
        connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict:
        result = {
            key: row[key] for key in (
                "help_request_id", "user_id", "incident_id", "device_id",
                "category", "user_message", "status", "public_response",
                "internal_note", "assigned_to", "created_at", "updated_at",
                "closed_at", "request_version",
            )
        }
        result["timeline"] = [
            dict(item) for item in connection.execute(
                "SELECT * FROM v3_help_request_timeline "
                "WHERE help_request_id=? ORDER BY timeline_id",
                (row["help_request_id"],),
            ).fetchall()
        ]
        return result

    def list_help_requests(
        self, *, status: str | None = None, category: str | None = None,
        user_id: int | None = None, device_id: str | None = None,
        incident_id: str | None = None, from_time: str | None = None,
        to_time: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> dict:
        if status is not None and status not in _HELP_STATUSES:
            raise IncidentWorkflowError("help status is invalid")
        if category is not None and category not in _HELP_CATEGORIES:
            raise IncidentWorkflowError("help category is invalid")
        if user_id is not None and (type(user_id) is not int or user_id <= 0):
            raise IncidentWorkflowError("help user_id is invalid")
        if device_id is not None and not is_valid_device_id(device_id):
            raise IncidentWorkflowError("help device_id is invalid")
        if incident_id is not None:
            incident_id = self._validate_incident_id(incident_id)
        if (
            type(limit) is not int or not 1 <= limit <= 100
            or type(offset) is not int or offset < 0
        ):
            raise IncidentWorkflowError("pagination is invalid")
        conditions, params = [], []
        for column, value in (
            ("status", status), ("category", category), ("user_id", user_id),
            ("device_id", device_id), ("incident_id", incident_id),
        ):
            if value is not None:
                conditions.append(f"{column}=?")
                params.append(value)
        if from_time is not None:
            conditions.append("updated_at>=?")
            params.append(from_time)
        if to_time is not None:
            conditions.append("updated_at<=?")
            params.append(to_time)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._read() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM v3_help_requests" + where,
                params,
            ).fetchone()[0])
            rows = connection.execute(
                "SELECT help_request_id,user_id,incident_id,device_id,category,"
                "user_message,status,public_response,internal_note,assigned_to,"
                "created_at,updated_at,closed_at,request_version "
                "FROM v3_help_requests" + where
                + " ORDER BY updated_at DESC,help_request_id "
                "LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            return {
                "items": [dict(row) for row in rows],
                "total": total, "limit": limit, "offset": offset,
            }

    def get_help_request(self, help_request_id: str) -> dict:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM v3_help_requests "
                "WHERE help_request_id=?", (help_request_id,),
            ).fetchone()
            if row is None:
                raise HelpRequestNotFound(
                    "help request does not exist"
                )
            return self._help_admin(connection, row)

    def get_support_contact(self, *, mobile: bool = False) -> dict:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM v3_support_contacts "
                "WHERE contact_id='primary'"
            ).fetchone()
            if row is None or (mobile and not row["enabled"]):
                return {
                    "available": False,
                    "reason": "support_contact_not_configured",
                }
            result = {
                "available": True,
                "display_name": row["display_name"],
                "phone": row["phone"],
                "email": row["email"],
                "working_hours": row["working_hours"],
                "public_note": row["public_note"],
                "config_version": int(row["config_version"]),
                "updated_at": row["updated_at"],
            }
            if not mobile:
                result.update({
                    "enabled": bool(row["enabled"]),
                    "updated_by": row["updated_by"],
                })
            return result

    def put_support_contact(
        self, *, display_name, phone, email, working_hours,
        public_note, enabled, expected_config_version,
        actor: IncidentActor, request_id: str,
    ) -> dict:
        actor.validate()
        if actor.role != Role.ADMIN.value:
            raise IncidentSourceForbidden(
                "only admin can update support contact"
            )
        display_name = _public_text(
            display_name, "display_name", maximum=120
        )
        phone = _optional_text(phone, "phone", maximum=32)
        email = _optional_text(email, "email", maximum=254)
        hours = _optional_text(
            working_hours, "working_hours", maximum=200
        )
        note = (
            _public_text(
                public_note, "public_note", maximum=500
            )
            if public_note is not None else None
        )
        if phone is not None and not _PHONE.fullmatch(phone):
            raise IncidentWorkflowError("phone is invalid")
        if email is not None and not _EMAIL.fullmatch(email):
            raise IncidentWorkflowError("email is invalid")
        if (
            type(enabled) is not bool
            or type(expected_config_version) is not int
            or expected_config_version < 0
        ):
            raise IncidentWorkflowError(
                "support contact version or enabled value is invalid"
            )
        request_id = _request_id(request_id)
        now = self._now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT config_version FROM v3_support_contacts "
                "WHERE contact_id='primary'"
            ).fetchone()
            current = int(row[0]) if row else 0
            if current != expected_config_version:
                raise SupportVersionConflict(
                    "support contact version is stale"
                )
            version = current + 1
            connection.execute(
                "INSERT INTO v3_support_contacts "
                "(contact_id,display_name,phone,email,working_hours,"
                "public_note,enabled,config_version,updated_by,updated_at) "
                "VALUES ('primary',?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(contact_id) DO UPDATE SET "
                "display_name=excluded.display_name,"
                "phone=excluded.phone,email=excluded.email,"
                "working_hours=excluded.working_hours,"
                "public_note=excluded.public_note,"
                "enabled=excluded.enabled,"
                "config_version=excluded.config_version,"
                "updated_by=excluded.updated_by,"
                "updated_at=excluded.updated_at",
                (
                    display_name, phone, email, hours, note,
                    int(enabled), version, actor.user_id, _iso(now),
                ),
            )
            self._audit(
                connection,
                entity_type="support_contact",
                entity_id="primary",
                action="updated",
                actor=actor,
                request_id=request_id,
                occurred_at=now,
            )
        return self.get_support_contact()

    def _device_in_scope(
        self, connection: sqlite3.Connection, user_id: int,
        device_id: str,
    ) -> bool:
        row = connection.execute(
            "SELECT 1 FROM v3_device_profiles p "
            "JOIN v3_mobile_user_scopes s ON s.user_id=? "
            "AND s.revoked_at IS NULL AND "
            "((s.scope_kind='device' AND s.scope_value=p.device_id) "
            "OR (s.scope_kind='area' AND s.scope_value=p.area_id)) "
            "WHERE p.device_id=? LIMIT 1",
            (user_id, device_id),
        ).fetchone()
        return row is not None

    @staticmethod
    def _help_mobile(row: sqlite3.Row) -> dict:
        return {
            "help_request_id": row["help_request_id"],
            "incident_id": row["incident_id"],
            "device_id": row["device_id"],
            "category": row["category"],
            "user_message": row["user_message"],
            "status": row["status"],
            "public_response": row["public_response"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "closed_at": row["closed_at"],
            "request_version": int(row["request_version"]),
        }
    def mark_notice(
        self, principal: MobilePrincipal, incident_id: str, *,
        acknowledged: bool, request_id: str,
    ) -> dict:
        incident_id = self._validate_incident_id(incident_id)
        request_id = _request_id(request_id)
        now = self._now()
        action = (
            "notice_acknowledged" if acknowledged else "notice_read"
        )
        change_kind = "acknowledged" if acknowledged else "read"
        actor = IncidentActor(
            principal.user_id, principal.username, Role.USER.value
        )
        with self._transaction() as connection:
            if not self._consume_rate(
                connection,
                action="mobile_notice_write",
                identity=principal.session_id,
                limit=120,
                now=now,
            ):
                raise WorkflowRateLimited("too many notice updates")
            if incident_id not in self._visible_incident_ids(
                connection, principal.user_id
            ):
                raise MobileResourceUnavailable(
                    "notice is unavailable"
                )
            row = self._incident_row(connection, incident_id)
            existing = connection.execute(
                "SELECT first_read_at,acknowledged_at "
                "FROM v3_mobile_notice_acknowledgements "
                "WHERE incident_id=? AND user_id=?",
                (incident_id, principal.user_id),
            ).fetchone()
            read_at = (
                existing["first_read_at"] if existing else None
            )
            ack_at = (
                existing["acknowledged_at"] if existing else None
            )
            changed = False
            if read_at is None:
                read_at, changed = _iso(now), True
            if acknowledged and ack_at is None:
                ack_at, changed = _iso(now), True
            if changed:
                connection.execute(
                    "INSERT INTO v3_mobile_notice_acknowledgements "
                    "(incident_id,user_id,first_read_at,acknowledged_at,"
                    "updated_at) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(incident_id,user_id) DO UPDATE SET "
                    "first_read_at=excluded.first_read_at,"
                    "acknowledged_at=excluded.acknowledged_at,"
                    "updated_at=excluded.updated_at",
                    (
                        incident_id,
                        principal.user_id,
                        read_at,
                        ack_at,
                        _iso(now),
                    ),
                )
                connection.execute(
                    "INSERT INTO v3_mobile_notice_changes "
                    "(incident_id,user_id,change_kind,"
                    "incident_version,changed_at) VALUES (?,?,?,?,?)",
                    (
                        incident_id,
                        principal.user_id,
                        change_kind,
                        row["incident_version"],
                        _iso(now),
                    ),
                )
            result = "success" if changed else "no_op"
            reason = "recorded" if changed else "already_recorded"
            self._mobile_audit(
                connection,
                action=action,
                principal=principal,
                request_id=request_id,
                occurred_at=now,
                reason=reason,
                result=result,
            )
            self._audit(
                connection,
                entity_type="notice",
                entity_id=incident_id,
                action=action,
                actor=actor,
                request_id=request_id,
                occurred_at=now,
                result=result,
            )
            return self._mobile_notice(
                connection, incident_id, principal.user_id
            )


def monitor_incident_snapshot(
    connection: sqlite3.Connection,
) -> dict:
    """Read a non-sensitive administrator summary in an existing transaction."""
    ledger = {
        row["version"]: row
        for row in read_applied_migrations(connection)
    }
    migration = ledger.get(V3_INCIDENT_WORKFLOW_MIGRATION.version)
    if (
        migration is None
        or migration["name"] != V3_INCIDENT_WORKFLOW_MIGRATION.name
        or migration["checksum"]
        != V3_INCIDENT_WORKFLOW_MIGRATION.checksum
    ):
        return {
            "capability": {
                "available": False,
                "reason": "incident_store_not_migrated",
            },
            "data": None,
        }
    active_rows = connection.execute(
        "SELECT incident_id,incident_type,severity,status,source,"
        "admin_title,first_seen_at,updated_at,incident_version "
        "FROM v3_incidents WHERE status NOT IN "
        "('resolved','false_positive') "
        "ORDER BY updated_at DESC,incident_id LIMIT 50"
    ).fetchall()
    recent_rows = connection.execute(
        "SELECT incident_id,incident_type,severity,status,source,"
        "admin_title,first_seen_at,updated_at,resolved_at,"
        "incident_version FROM v3_incidents "
        "ORDER BY updated_at DESC,incident_id LIMIT 20"
    ).fetchall()
    return {
        "capability": {
            "available": True,
            "reason": "recorded_incident_workflow_available",
            "semantics": (
                "no_recorded_incidents_is_not_a_safety_assurance"
            ),
        },
        "data": {
            "active": [dict(row) for row in active_rows],
            "recent": [dict(row) for row in recent_rows],
            "empty_meaning": (
                "no_recorded_incidents_not_proven_safe"
            ),
        },
    }


__all__ = [
    "HelpRequestNotFound",
    "IncidentActor",
    "IncidentNotFound",
    "IncidentStoreUnavailable",
    "IncidentTransitionConflict",
    "IncidentVersionConflict",
    "IncidentWorkflowError",
    "IncidentWorkflowService",
    "MobileResourceUnavailable",
    "NoticeCursorError",
    "WorkflowRateLimited",
    "monitor_incident_snapshot",
]
