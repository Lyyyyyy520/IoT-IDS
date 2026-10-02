"""Quarantined discovery evidence, separate from trusted device inventory."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import ipaddress
import json
import re
import sqlite3
from pathlib import Path
from typing import Callable, Iterator
from uuid import uuid4

from contracts import is_valid_device_id
from services.device_management import (
    DeviceActor, DeviceIdConflictError, DeviceIdentityConflictError,
    DeviceManagementError, DeviceManagementService, _area_id, _device_id,
    _device_type, _enum, _normalize_mac, _text,
)
from services.device_state import DeviceStateService
from services.realtime_events import V3DatabaseUnavailable, append_realtime_event
from v3_database import V3_DEVICE_DISCOVERY_MIGRATION, connect_v3_existing

Clock = Callable[[], datetime]
DISCOVERY_SOURCES = frozenset({"mqtt_unknown", "dhcp", "arp", "probe", "other"})
_MAC = re.compile(r"^(?:[0-9a-fA-F]{12}|[0-9a-fA-F]{2}([:-])[0-9a-fA-F]{2}(?:\1[0-9a-fA-F]{2}){4})$")
_SAFE_HINT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_FIRMWARE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}$")
_DIGEST = re.compile(r"^[0-9a-f]{16}$")
_METADATA_KEYS = {"device_type_hint", "firmware_version", "sequence", "boot_id_digest"}
_SECRET_REASON = re.compile(r"(?i)(password|passwd|token|authorization|bearer|cookie|secret|mqtt.{0,20}credential|wifi.{0,12}(?:password|key))")


class DeviceDiscoveryError(ValueError):
    code = "invalid_discovery_request"


class DiscoveryNotFoundError(DeviceDiscoveryError):
    code = "candidate_not_found"


class CandidateVersionConflictError(DeviceDiscoveryError):
    code = "candidate_version_conflict"


class CandidateStateConflictError(DeviceDiscoveryError):
    code = "candidate_state_conflict"


class CandidateIdentityConflictError(DeviceDiscoveryError):
    code = "candidate_identity_conflict"


class _CapacityReached(Exception):
    pass


class _RateLimited(Exception):
    pass


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DeviceDiscoveryError(f"{field} must include a timezone")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value, "timestamp").isoformat().replace("+00:00", "Z")


def _parse_iso(value: str, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise DeviceDiscoveryError(f"{field} must be timezone-aware ISO 8601") from exc
    return _iso(_utc(parsed, field))


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def ensure_device_discovery_schema(connection: sqlite3.Connection) -> None:
    """Verify v9 without repairing or creating schema objects."""
    try:
        migration = connection.execute(
            "SELECT name, checksum FROM v3_schema_migrations WHERE version=9"
        ).fetchone()
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('v3_discovered_device_candidates','v3_discovery_observations','v3_discovery_actions')"
        )}
    except sqlite3.Error as exc:
        raise V3DatabaseUnavailable("device discovery schema is unavailable") from exc
    expected = {"v3_discovered_device_candidates", "v3_discovery_observations", "v3_discovery_actions"}
    if (not migration or migration["name"] != V3_DEVICE_DISCOVERY_MIGRATION.name
            or migration["checksum"] != V3_DEVICE_DISCOVERY_MIGRATION.checksum
            or tables != expected):
        raise V3DatabaseUnavailable("device discovery migration v9 is unavailable")


class DeviceDiscoveryService:
    """Accept bounded MAC-backed evidence, never convert it to live device state."""

    def __init__(self, database_path: str | Path | None, *, clock: Clock | None = None,
                 max_candidates: int = 5_000, max_observations: int = 100_000,
                 max_observations_per_candidate: int = 1_000,
                 max_source_observations_per_minute: int = 12):
        self.database_path = Path(database_path) if database_path else None
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.max_candidates = max_candidates
        self.max_observations = max_observations
        self.max_observations_per_candidate = max_observations_per_candidate
        self.max_source_observations_per_minute = max_source_observations_per_minute
        self._devices = DeviceManagementService(self.database_path, clock=self._clock)

    def _now(self) -> datetime:
        return _utc(self._clock(), "injected clock")

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        if self.database_path is None:
            raise V3DatabaseUnavailable("v3 database path is not configured")
        connection = None
        try:
            connection = connect_v3_existing(self.database_path)
            ensure_device_discovery_schema(connection)
            if write:
                connection.execute("BEGIN IMMEDIATE")
        except V3DatabaseUnavailable:
            if connection is not None: connection.close()
            raise
        except (FileNotFoundError, sqlite3.Error) as exc:
            if connection is not None: connection.close()
            raise V3DatabaseUnavailable("device discovery database is unavailable") from exc
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _normalize_ip(value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > 64:
            raise DeviceDiscoveryError("ip_address is invalid")
        try:
            address = ipaddress.ip_address(value.strip())
        except ValueError as exc:
            raise DeviceDiscoveryError("ip_address is invalid") from exc
        if address.is_unspecified or address.is_loopback or address.is_multicast:
            raise DeviceDiscoveryError("ip_address must be unicast")
        return str(address)

    @staticmethod
    def _sanitize_metadata(value: dict | None) -> dict:
        if value is None:
            return {}
        if not isinstance(value, dict) or not set(value) <= _METADATA_KEYS:
            raise DeviceDiscoveryError("metadata contains unsupported fields")
        safe = {}
        for key, item in value.items():
            if key == "device_type_hint":
                if not isinstance(item, str) or not _SAFE_HINT.fullmatch(item):
                    raise DeviceDiscoveryError("device_type_hint is invalid")
                safe[key] = item
            elif key == "firmware_version":
                if not isinstance(item, str) or not _FIRMWARE.fullmatch(item):
                    raise DeviceDiscoveryError("firmware_version is invalid")
                safe[key] = item
            elif key == "sequence":
                if type(item) is not int or not 0 <= item <= (1 << 63) - 1:
                    raise DeviceDiscoveryError("sequence is invalid")
                safe[key] = item
            elif key == "boot_id_digest":
                if not isinstance(item, str) or not _DIGEST.fullmatch(item):
                    raise DeviceDiscoveryError("boot_id_digest is invalid")
                safe[key] = item
        if len(_json(safe).encode("utf-8")) > 1024:
            raise DeviceDiscoveryError("sanitized metadata is too large")
        return safe

    def observe(self, *, source: str, mac_address: str, ip_address: str | None,
                proposed_device_id: str | None, observed_at: datetime,
                received_at: datetime, deduplication_key: str,
                sanitized_metadata: dict | None = None):
        """Record one observation; callers must supply stable MAC identity."""
        if source not in DISCOVERY_SOURCES:
            raise DeviceDiscoveryError("discovery source is unsupported")
        if not isinstance(mac_address, str) or len(mac_address) > 32 or not _MAC.fullmatch(mac_address):
            raise DeviceDiscoveryError("a valid MAC identity is required")
        mac = _normalize_mac(mac_address)
        address = self._normalize_ip(ip_address)
        if proposed_device_id is not None and not is_valid_device_id(proposed_device_id):
            raise DeviceDiscoveryError("proposed_device_id is invalid")
        if not isinstance(deduplication_key, str) or not deduplication_key.strip() or len(deduplication_key) > 256:
            raise DeviceDiscoveryError("deduplication_key is invalid")
        observed = _utc(observed_at, "observed_at")
        received = _utc(received_at, "received_at")
        if observed > received + timedelta(minutes=5) or observed < received - timedelta(days=30):
            raise DeviceDiscoveryError("observed_at is outside the accepted evidence window")
        metadata = self._sanitize_metadata(sanitized_metadata)
        observed_text, received_text = _iso(observed), _iso(received)
        dedup_hash = sha256(f"{source}\0{deduplication_key}".encode()).hexdigest()
        evidence = _json({"source": source, "identity_kind": "mac", "identity_value": mac,
                          "proposed_device_id": proposed_device_id, "ip_address": address,
                          "observed_at": observed_text,
                          "sanitized_metadata": metadata})
        evidence_hash = sha256(evidence.encode("utf-8")).hexdigest()
        try:
            with self._connection(write=True) as connection:
                row = connection.execute(
                    "SELECT * FROM v3_discovered_device_candidates WHERE identity_kind='mac' AND identity_value=?",
                    (mac,),
                ).fetchone()
                if (row is not None and row["status"] == "claimed"
                        and proposed_device_id == row["claimed_device_id"]):
                    return {"code": "already_claimed", "candidate_id": row["candidate_id"], "status": "claimed", "created": False, "changed": False}
                is_new = row is None
                if is_new:
                    count = connection.execute("SELECT COUNT(*) FROM v3_discovered_device_candidates").fetchone()[0]
                    if int(count) >= self.max_candidates:
                        raise _CapacityReached()
                    candidate_id = str(uuid4())
                    bound = connection.execute(
                        "SELECT 1 FROM v3_device_profiles WHERE identity_kind='mac' AND identity_value=?", (mac,)
                    ).fetchone()
                    status = "conflict" if bound else "pending"
                    conflict_reason = "mac_already_bound" if bound else None
                    connection.execute(
                        "INSERT INTO v3_discovered_device_candidates "
                        "(candidate_id,identity_kind,identity_value,proposed_device_id,latest_ip,status,conflict_reason,"
                        "first_seen_at,last_seen_at,source_count,observation_count,candidate_version,created_at,updated_at) "
                        "VALUES (?,'mac',?,?,?,?,?,?,?,0,0,1,?,?)",
                        (candidate_id, mac, proposed_device_id, address, status, conflict_reason,
                         received_text, received_text, received_text, received_text),
                    )
                    row = connection.execute(
                        "SELECT * FROM v3_discovered_device_candidates WHERE candidate_id=?", (candidate_id,)
                    ).fetchone()
                else:
                    candidate_id = row["candidate_id"]
                duplicate = connection.execute(
                    "SELECT evidence_hash FROM v3_discovery_observations WHERE candidate_id=? AND deduplication_key=?",
                    (candidate_id, dedup_hash),
                ).fetchone()
                reused_key = bool(duplicate and duplicate["evidence_hash"] != evidence_hash)
                if duplicate and not reused_key:
                    return {"code": "duplicate", "candidate_id": candidate_id, "status": row["status"], "created": False, "changed": False}
                if reused_key:
                    dedup_hash = sha256(f"{dedup_hash}\0{evidence_hash}".encode()).hexdigest()
                    conflict_observation = connection.execute(
                        "SELECT evidence_hash FROM v3_discovery_observations WHERE candidate_id=? AND deduplication_key=?",
                        (candidate_id, dedup_hash),
                    ).fetchone()
                    if conflict_observation:
                        return {"code": "duplicate_conflict_evidence", "candidate_id": candidate_id,
                                "status": row["status"], "created": False, "changed": False}
                recent = connection.execute(
                    "SELECT COUNT(*) FROM v3_discovery_observations WHERE candidate_id=? AND source=? AND received_at>=?",
                    (candidate_id, source, _iso(received - timedelta(minutes=1))),
                ).fetchone()[0]
                if int(recent) >= self.max_source_observations_per_minute:
                    raise _RateLimited()
                per_candidate = connection.execute(
                    "SELECT COUNT(*) FROM v3_discovery_observations WHERE candidate_id=?", (candidate_id,)
                ).fetchone()[0]
                all_observations = connection.execute("SELECT COUNT(*) FROM v3_discovery_observations").fetchone()[0]
                if int(per_candidate) >= self.max_observations_per_candidate or int(all_observations) >= self.max_observations:
                    raise _CapacityReached()
                bound = connection.execute(
                    "SELECT 1 FROM v3_device_profiles WHERE identity_kind='mac' AND identity_value=?", (mac,)
                ).fetchone()
                proposals = {item[0] for item in connection.execute(
                    "SELECT DISTINCT proposed_device_id FROM v3_discovery_observations "
                    "WHERE candidate_id=? AND proposed_device_id IS NOT NULL", (candidate_id,)
                ).fetchall()}
                if proposed_device_id is not None:
                    proposals.add(proposed_device_id)
                conflict = "mac_already_bound" if bound else (
                    "multiple_proposed_device_ids" if len(proposals) > 1 else
                    "deduplication_key_reused" if reused_key else row["conflict_reason"]
                )
                status = row["status"]
                if status in {"pending", "conflict"} and conflict:
                    status = "conflict"
                version = int(row["candidate_version"])
                changed = status != row["status"] or conflict != row["conflict_reason"]
                if changed and not is_new:
                    version += 1
                connection.execute(
                    "INSERT INTO v3_discovery_observations "
                    "(candidate_id,source,observed_at,received_at,proposed_device_id,ip_address,evidence_hash,"
                    "sanitized_metadata_json,deduplication_key) VALUES (?,?,?,?,?,?,?,?,?)",
                    (candidate_id, source, observed_text, received_text, proposed_device_id,
                     address, evidence_hash, _json(metadata), dedup_hash),
                )
                sources = connection.execute(
                    "SELECT COUNT(DISTINCT source) FROM v3_discovery_observations WHERE candidate_id=?", (candidate_id,)
                ).fetchone()[0]
                connection.execute(
                    "UPDATE v3_discovered_device_candidates SET proposed_device_id=COALESCE(?,proposed_device_id),"
                    "latest_ip=COALESCE(?,latest_ip),status=?,conflict_reason=?,last_seen_at=?,source_count=?,"
                    "observation_count=?,candidate_version=?,updated_at=? WHERE candidate_id=?",
                    (proposed_device_id, address, status, conflict, max(row["last_seen_at"], received_text),
                     int(sources), int(per_candidate) + 1, version, received_text, candidate_id),
                )
                if is_new or changed:
                    self._event(connection, candidate_id, status, version, received)
                return {"code": "discovered" if is_new else "observed", "candidate_id": candidate_id,
                        "status": status, "created": is_new, "changed": bool(is_new or changed)}
        except _CapacityReached:
            self._mark_capacity_degraded()
            return {"code": "candidate_capacity_reached", "candidate_id": None, "status": None, "created": False, "changed": False}
        except _RateLimited:
            return {"code": "observation_rate_limited", "candidate_id": None, "status": None, "created": False, "changed": False}

    def _mark_capacity_degraded(self) -> None:
        self.mark_degraded("candidate_capacity_reached")

    def mark_degraded(self, reason: str) -> None:
        if self.database_path is None:
            return
        if reason not in {"candidate_capacity_reached", "discovery_storage_error"}:
            return
        try:
            DeviceStateService(self.database_path, clock=self._clock, create_if_missing=False).set_component_readiness(
                "device_discovery", "degraded", reason
            )
        except Exception:
            return

    @staticmethod
    def _public(row, include_identity: bool) -> dict:
        result = {"candidate_id": row["candidate_id"], "proposed_device_id": row["proposed_device_id"],
                  "status": row["status"], "conflict": row["conflict_reason"] is not None,
                  "conflict_reason": row["conflict_reason"], "first_seen_at": row["first_seen_at"],
                  "last_seen_at": row["last_seen_at"], "source_count": int(row["source_count"]),
                  "observation_count": int(row["observation_count"]), "claimed_device_id": row["claimed_device_id"],
                  "claimed_at": row["claimed_at"], "ignored_at": row["ignored_at"],
                  "ignored_reason": row["ignored_reason"], "candidate_version": int(row["candidate_version"]),
                  "updated_at": row["updated_at"]}
        if include_identity:
            result.update(identity_kind=row["identity_kind"], mac_address=row["identity_value"], latest_ip=row["latest_ip"])
        return result

    def list_candidates(self, *, status: str | None = None, source: str | None = None,
                        search: str | None = None, conflict: bool | None = None,
                        first_seen_from: str | None = None, first_seen_to: str | None = None,
                        last_seen_from: str | None = None, last_seen_to: str | None = None,
                        limit: int = 50, offset: int = 0, include_identity: bool = True) -> dict:
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 1_000_000:
            raise DeviceDiscoveryError("pagination is invalid")
        if status is not None and status not in {"pending", "ignored", "claimed", "conflict"}:
            raise DeviceDiscoveryError("status is invalid")
        if source is not None and source not in DISCOVERY_SOURCES:
            raise DeviceDiscoveryError("source is invalid")
        parts, args = [], []
        if status is not None: parts.append("c.status=?"); args.append(status)
        if conflict is not None: parts.append("c.conflict_reason IS " + ("NOT " if conflict else "") + "NULL")
        if source is not None:
            parts.append("EXISTS (SELECT 1 FROM v3_discovery_observations o WHERE o.candidate_id=c.candidate_id AND o.source=?)")
            args.append(source)
        if search:
            value = _text(search, "search", maximum=100)
            search_parts = ["LOWER(COALESCE(c.proposed_device_id,'')) LIKE LOWER(?)"]
            args.append(f"%{value}%")
            if include_identity:
                search_parts.append("LOWER(c.identity_value) LIKE LOWER(?)")
                args.append(f"%{value}%")
            parts.append("(" + " OR ".join(search_parts) + ")")
        for column, raw, op in (
            ("first_seen_at", first_seen_from, ">="), ("first_seen_at", first_seen_to, "<="),
            ("last_seen_at", last_seen_from, ">="), ("last_seen_at", last_seen_to, "<="),
        ):
            if raw is not None: parts.append(f"c.{column}{op}?"); args.append(_parse_iso(raw, column))
        where = " WHERE " + " AND ".join(parts) if parts else ""
        with self._connection() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM v3_discovered_device_candidates c" + where, args).fetchone()[0])
            rows = connection.execute(
                "SELECT c.*, (SELECT o.sanitized_metadata_json FROM v3_discovery_observations o "
                "WHERE o.candidate_id=c.candidate_id ORDER BY o.received_at DESC,o.observation_id DESC LIMIT 1) metadata "
                "FROM v3_discovered_device_candidates c" + where +
                " ORDER BY c.last_seen_at DESC,c.candidate_id LIMIT ? OFFSET ?", [*args, limit, offset]
            ).fetchall()
            items = []
            for row in rows:
                item = self._public(row, include_identity)
                item["sources"] = [r[0] for r in connection.execute(
                    "SELECT DISTINCT source FROM v3_discovery_observations WHERE candidate_id=? ORDER BY source", (row["candidate_id"],)
                )]
                item["device_type_hint"] = json.loads(row["metadata"] or "{}").get("device_type_hint")
                items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def get_candidate(self, candidate_id: str, *, include_identity: bool = True) -> dict:
        if not isinstance(candidate_id, str) or len(candidate_id) > 64:
            raise DeviceDiscoveryError("candidate_id is invalid")
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM v3_discovered_device_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
            if row is None: raise DiscoveryNotFoundError("candidate was not found")
            result = self._public(row, include_identity)
            result["sources"] = [r[0] for r in connection.execute(
                "SELECT DISTINCT source FROM v3_discovery_observations WHERE candidate_id=? ORDER BY source", (candidate_id,)
            )]
            result["conflicting_proposed_device_ids"] = [r[0] for r in connection.execute(
                "SELECT DISTINCT proposed_device_id FROM v3_discovery_observations "
                "WHERE candidate_id=? AND proposed_device_id IS NOT NULL ORDER BY proposed_device_id", (candidate_id,)
            )]
            observations = connection.execute(
                "SELECT source,observed_at,received_at,proposed_device_id,ip_address,evidence_hash,sanitized_metadata_json "
                "FROM v3_discovery_observations WHERE candidate_id=? "
                "ORDER BY received_at DESC,observation_id DESC LIMIT 50", (candidate_id,)
            ).fetchall()
            result["observations"] = []
            for item in observations:
                clean = {"source": item["source"], "observed_at": item["observed_at"],
                         "received_at": item["received_at"], "proposed_device_id": item["proposed_device_id"],
                         "sanitized_metadata": json.loads(item["sanitized_metadata_json"])}
                if include_identity: clean.update(ip_address=item["ip_address"], evidence_hash=item["evidence_hash"])
                result["observations"].append(clean)
            return result

    @staticmethod
    def _action_snapshot(row) -> dict:
        return {key: row[key] for key in ("candidate_id", "status", "conflict_reason", "candidate_version",
                 "claimed_device_id", "claimed_at", "ignored_at", "ignored_reason")}

    @staticmethod
    def _write_action(connection, row, action, actor, occurred, request_id, before, after, reason=None):
        connection.execute(
            "INSERT INTO v3_discovery_actions(candidate_id,action,actor_user_id,actor_username,actor_role,"
            "occurred_at,request_id,before_json,after_json,reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (row["candidate_id"], action, actor.user_id, actor.username, actor.role, _iso(occurred),
             request_id, _json(before), _json(after), reason),
        )

    @staticmethod
    def _event(connection, candidate_id, status, version, occurred):
        append_realtime_event(connection, event_type="device.discovered", occurred_at=occurred,
            device_id=None, state_version=version,
            payload={"candidate_id": candidate_id, "status": status, "candidate_version": version})

    def _write_actor(self, actor: DeviceActor, request_id: str):
        return self._devices._validate_write_context(actor, request_id)

    @staticmethod
    def _expected(value: int) -> int:
        if type(value) is not int or value < 1:
            raise DeviceDiscoveryError("expected_candidate_version must be positive")
        return value

    def ignore_candidate(self, candidate_id: str, *, expected_candidate_version: int, reason: str,
                         actor: DeviceActor, request_id: str) -> dict:
        self._write_actor(actor, request_id); expected = self._expected(expected_candidate_version)
        reason = _text(reason, "reason", maximum=500)
        if _SECRET_REASON.search(reason) or reason.lstrip().startswith(("{", "[")):
            raise DeviceDiscoveryError("reason must not contain secrets or raw payload data")
        occurred = self._now()
        with self._connection(write=True) as connection:
            row = connection.execute("SELECT * FROM v3_discovered_device_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
            if row is None: raise DiscoveryNotFoundError("candidate was not found")
            if int(row["candidate_version"]) != expected: raise CandidateVersionConflictError("candidate version is stale")
            if row["status"] not in {"pending", "conflict"}: raise CandidateStateConflictError("candidate cannot be ignored")
            before = self._action_snapshot(row)
            connection.execute("UPDATE v3_discovered_device_candidates SET status='ignored',ignored_at=?,ignored_reason=?,"
                "candidate_version=candidate_version+1,updated_at=? WHERE candidate_id=? AND candidate_version=?",
                (_iso(occurred), reason, _iso(occurred), candidate_id, expected))
            after_row = connection.execute("SELECT * FROM v3_discovered_device_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
            after = self._action_snapshot(after_row)
            self._write_action(connection, row, "ignored", actor, occurred, request_id, before, after, reason)
            self._event(connection, candidate_id, "ignored", after["candidate_version"], occurred)
            return self._public(after_row, True)

    def restore_candidate(self, candidate_id: str, *, expected_candidate_version: int,
                          actor: DeviceActor, request_id: str) -> dict:
        self._write_actor(actor, request_id); expected = self._expected(expected_candidate_version); occurred = self._now()
        with self._connection(write=True) as connection:
            row = connection.execute("SELECT * FROM v3_discovered_device_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
            if row is None: raise DiscoveryNotFoundError("candidate was not found")
            if int(row["candidate_version"]) != expected: raise CandidateVersionConflictError("candidate version is stale")
            if row["status"] != "ignored": raise CandidateStateConflictError("only ignored candidates can be restored")
            before = self._action_snapshot(row)
            connection.execute("UPDATE v3_discovered_device_candidates SET status='pending',ignored_at=NULL,ignored_reason=NULL,"
                "candidate_version=candidate_version+1,updated_at=? WHERE candidate_id=? AND candidate_version=?",
                (_iso(occurred), candidate_id, expected))
            after_row = connection.execute("SELECT * FROM v3_discovered_device_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
            after = self._action_snapshot(after_row)
            self._write_action(connection, row, "restored", actor, occurred, request_id, before, after)
            self._event(connection, candidate_id, "pending", after["candidate_version"], occurred)
            return self._public(after_row, True)

    def claim_candidate(self, candidate_id: str, *, expected_candidate_version: int, device_id: str,
                        display_name: str, device_type: str, area_id: str | None, importance: str,
                        profile_source: str, actor: DeviceActor, request_id: str,
                        resolve_identity_conflict: bool = False) -> dict:
        self._write_actor(actor, request_id); expected = self._expected(expected_candidate_version); occurred = self._now()
        with self._connection(write=True) as connection:
            row = connection.execute("SELECT * FROM v3_discovered_device_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
            if row is None: raise DiscoveryNotFoundError("candidate was not found")
            if int(row["candidate_version"]) != expected: raise CandidateVersionConflictError("candidate version is stale")
            if row["status"] not in {"pending", "conflict"}: raise CandidateStateConflictError("candidate cannot be claimed")
            if row["conflict_reason"] == "mac_already_bound":
                raise CandidateIdentityConflictError("candidate MAC is bound to a trusted device")
            if type(resolve_identity_conflict) is not bool:
                raise DeviceDiscoveryError("resolve_identity_conflict must be a boolean")
            if row["conflict_reason"] and not resolve_identity_conflict:
                raise CandidateIdentityConflictError("identity conflict requires explicit administrator resolution")
            if resolve_identity_conflict and row["conflict_reason"] not in {
                "multiple_proposed_device_ids", "deduplication_key_reused",
            }:
                raise CandidateIdentityConflictError("this identity conflict cannot be resolved by claiming this candidate")
            before = self._action_snapshot(row)
            self._devices.create_device_in_transaction(connection, device_id=device_id,
                mac=row["identity_value"], display_name=display_name, device_type=device_type,
                area_id=area_id, importance=importance, profile_source=profile_source,
                actor=actor, request_id=request_id)
            now = _iso(occurred)
            updated = connection.execute("UPDATE v3_discovered_device_candidates SET status='claimed',conflict_reason=NULL,"
                "claimed_device_id=?,claimed_at=?,candidate_version=candidate_version+1,updated_at=? "
                "WHERE candidate_id=? AND candidate_version=?", (device_id, now, now, candidate_id, expected))
            if updated.rowcount != 1: raise CandidateVersionConflictError("candidate version is stale")
            after_row = connection.execute("SELECT * FROM v3_discovered_device_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
            after = self._action_snapshot(after_row)
            self._write_action(connection, row, "claimed", actor, occurred, request_id, before, after,
                "管理员人工核验并确认了新的稳定设备身份")
            self._event(connection, candidate_id, "claimed", after["candidate_version"], occurred)
            device = self._devices._detail(connection, device_id)
            return {"device": device, "candidate": self._public(after_row, True),
                    "credential_provisioning_required": True,
                    "provisioning_message": "档案已建立但设备仍不可信；需人工配置专属 MQTT 用户与 ACL，之后等待新的合法心跳。"}


__all__ = ["CandidateIdentityConflictError", "CandidateStateConflictError", "CandidateVersionConflictError",
           "DeviceDiscoveryError", "DeviceDiscoveryService", "DiscoveryNotFoundError", "ensure_device_discovery_schema"]
