"""Scoped mobile pairing and opaque-token sessions backed by SQLite."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hmac
from hashlib import sha256
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Callable
from uuid import uuid4

from werkzeug.security import generate_password_hash

from config import MobileSecuritySettings
from contracts import Role, is_valid_device_id
from v3_database import (
    V3_MOBILE_USER_ADMIN_MIGRATION,
    connect_v3_existing,
    read_applied_migrations,
)


Clock = Callable[[], datetime]
FaultInjector = Callable[[str], None]
_AREA_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_USERNAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{2,63}$")
_CLIENT_INSTANCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
_PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_PAIRING_NORMALIZED_LENGTH = 32
_PAIRING_SELECTOR_LENGTH = 8
_TOKEN_PATTERN = re.compile(
    r"^(?P<prefix>ma|mr)_(?P<selector>[0-9a-f]{16})_"
    r"(?P<secret>[A-Za-z0-9_-]{32,96})$"
)


class MobileAccessError(ValueError):
    code = "invalid_mobile_request"
    status = 400


class MobileStoreUnavailable(MobileAccessError):
    code = "mobile_store_unavailable"
    status = 503


class MobileUserNotFound(MobileAccessError):
    code = "mobile_user_not_found"
    status = 404


class MobileUserIneligible(MobileAccessError):
    code = "mobile_user_ineligible"
    status = 409


class MobileUserDisabled(MobileAccessError):
    code = "mobile_user_disabled"
    status = 409


class MobileUsernameConflict(MobileAccessError):
    code = "mobile_username_conflict"
    status = 409


class MobileUserProfileConflict(MobileAccessError):
    code = "mobile_user_profile_version_conflict"
    status = 409


class MobileScopeConflict(MobileAccessError):
    code = "mobile_scope_version_conflict"
    status = 409


class MobileScopeReferenceError(MobileAccessError):
    code = "mobile_scope_reference_invalid"
    status = 400


class PairingScopeRequired(MobileAccessError):
    code = "mobile_scope_required"
    status = 409


class PairingClaimRejected(MobileAccessError):
    code = "pairing_claim_rejected"
    status = 401


class MobileRateLimited(MobileAccessError):
    code = "mobile_rate_limited"
    status = 429


class MobileAuthenticationError(MobileAccessError):
    code = "mobile_token_invalid"
    status = 401


class MobileTokenExpired(MobileAuthenticationError):
    code = "mobile_token_expired"


class MobileSessionRevoked(MobileAuthenticationError):
    code = "mobile_session_revoked"


class MobileRefreshReplay(MobileAuthenticationError):
    code = "refresh_token_replay"


class MobileSessionNotFound(MobileAccessError):
    code = "mobile_session_not_found"
    status = 404


@dataclass(frozen=True)
class MobileActor:
    user_id: int
    username: str
    role: str

    def validate(self) -> None:
        if type(self.user_id) is not int or self.user_id <= 0:
            raise MobileAccessError("actor user_id must be a positive integer")
        if not isinstance(self.username, str) or not self.username.strip():
            raise MobileAccessError("actor username is required")
        if self.role != Role.ADMIN.value:
            raise MobileAccessError("mobile administration requires admin role")


@dataclass(frozen=True)
class MobilePrincipal:
    session_id: str
    user_id: int
    username: str
    client_instance_id: str
    client_display_name: str
    token_generation: int


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MobileAccessError("timestamps and injected clocks must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _text(value, field: str, *, minimum: int = 1, maximum: int = 128) -> str:
    if not isinstance(value, str):
        raise MobileAccessError(f"{field} must be a string")
    normalized = value.strip()
    if not minimum <= len(normalized) <= maximum:
        raise MobileAccessError(f"{field} length must be between {minimum} and {maximum}")
    if any(ord(character) < 32 for character in normalized):
        raise MobileAccessError(f"{field} contains control characters")
    return normalized


def _positive_user_id(value) -> int:
    if type(value) is not int or value <= 0:
        raise MobileAccessError("user_id must be a positive integer")
    return value


def _request_id(value) -> str:
    return _text(value, "request_id", maximum=128)


def _client_instance(value) -> str:
    normalized = _text(value, "client_instance_id", minimum=8, maximum=128)
    if not _CLIENT_INSTANCE_PATTERN.fullmatch(normalized):
        raise MobileAccessError("client_instance_id contains unsupported characters")
    return normalized


def _client_name(value) -> str:
    return _text(value, "client_display_name", maximum=100)


def _username(value) -> str:
    normalized = _text(value, "username", minimum=3, maximum=64)
    if not _USERNAME_PATTERN.fullmatch(normalized):
        raise MobileAccessError(
            "username must start with a letter and contain only letters, digits, ., _, or -"
        )
    return normalized


def _normalize_pairing_code(value) -> str:
    if not isinstance(value, str):
        return ""
    normalized = re.sub(r"[\s-]+", "", value).upper()
    if (
        len(normalized) != _PAIRING_NORMALIZED_LENGTH
        or any(character not in _PAIRING_ALPHABET for character in normalized)
    ):
        return ""
    return normalized


def _format_pairing_code(value: str) -> str:
    return "-".join(value[index:index + 4] for index in range(0, len(value), 4))


class MobileAccessService:
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
            raise MobileStoreUnavailable("mobile database path is not configured")
        try:
            connection = connect_v3_existing(self.database_path)
            ledger = {row["version"]: row for row in read_applied_migrations(connection)}
            migration = ledger.get(V3_MOBILE_USER_ADMIN_MIGRATION.version)
            if (
                migration is None
                or migration["name"] != V3_MOBILE_USER_ADMIN_MIGRATION.name
                or migration["checksum"] != V3_MOBILE_USER_ADMIN_MIGRATION.checksum
            ):
                connection.close()
                raise MobileStoreUnavailable("mobile schema migration is unavailable")
            return connection
        except (FileNotFoundError, sqlite3.Error) as exc:
            raise MobileStoreUnavailable("mobile database is unavailable") from exc

    def _digest(self, purpose: str, value: str) -> str:
        return hmac.new(
            self._secret, f"{purpose}:{value}".encode("utf-8"), sha256
        ).hexdigest()

    def _audit(
        self,
        connection: sqlite3.Connection,
        *,
        action: str,
        actor: str,
        request_id: str,
        result: str,
        reason: str,
        user_id: int | None = None,
        session_id: str | None = None,
        pairing_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO v3_mobile_security_audit "
            "(action, user_id, session_id, pairing_id, actor, occurred_at, "
            "request_id, result, stable_reason_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                action, user_id, session_id, pairing_id, actor,
                _iso(occurred_at or self._now()), request_id, result, reason,
            ),
        )

    @staticmethod
    def _user(connection: sqlite3.Connection, user_id: int) -> sqlite3.Row:
        try:
            row = connection.execute(
                "SELECT id, username, role FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        except sqlite3.Error as exc:
            raise MobileStoreUnavailable("legacy users table is unavailable") from exc
        if row is None:
            raise MobileUserNotFound("target user does not exist")
        return row

    def _role_user(self, connection: sqlite3.Connection, user_id: int) -> sqlite3.Row:
        row = self._user(connection, user_id)
        if row["role"] != Role.USER.value:
            raise MobileUserIneligible("only role=user accounts can use mobile access")
        return row

    def _eligible_user(self, connection: sqlite3.Connection, user_id: int) -> sqlite3.Row:
        row = self._role_user(connection, user_id)
        profile = connection.execute(
            "SELECT account_status FROM v3_mobile_user_profiles WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if profile is not None and profile["account_status"] == "disabled":
            raise MobileUserDisabled("mobile user account is disabled")
        return row

    @staticmethod
    def _active_scope_count(connection: sqlite3.Connection, user_id: int) -> int:
        return int(connection.execute(
            "SELECT COUNT(*) FROM v3_mobile_user_scopes "
            "WHERE user_id = ? AND revoked_at IS NULL", (user_id,)
        ).fetchone()[0])

    @staticmethod
    def _user_summary_row(
        connection: sqlite3.Connection, user_id: int, now_text: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT u.id AS user_id, u.username, "
            "COALESCE(p.display_name, u.username) AS display_name, "
            "COALESCE(p.mobile_only, 0) AS mobile_only, "
            "COALESCE(p.account_status, 'active') AS account_status, "
            "COALESCE(p.profile_version, 0) AS profile_version, "
            "p.created_at, p.updated_at, p.disabled_at, p.disabled_reason, "
            "(SELECT COUNT(*) FROM v3_mobile_user_scopes s WHERE s.user_id=u.id "
            " AND s.revoked_at IS NULL AND s.scope_kind='device') AS device_scope_count, "
            "(SELECT COUNT(*) FROM v3_mobile_user_scopes s WHERE s.user_id=u.id "
            " AND s.revoked_at IS NULL AND s.scope_kind='area') AS area_scope_count, "
            "(SELECT COUNT(*) FROM v3_mobile_sessions ms WHERE ms.user_id=u.id "
            " AND ms.revoked_at IS NULL AND ms.refresh_expires_at>?) AS active_session_count, "
            "(SELECT COUNT(*) FROM v3_mobile_sessions ms WHERE ms.user_id=u.id "
            " AND (ms.revoked_at IS NOT NULL OR ms.refresh_expires_at<=?)) "
            " AS revoked_session_count, "
            "(SELECT MAX(mp.expires_at) FROM v3_mobile_pairings mp WHERE mp.user_id=u.id "
            " AND mp.claimed_at IS NULL AND mp.invalidated_at IS NULL "
            " AND mp.expires_at>?) AS unused_pairing_expires_at "
            "FROM users u LEFT JOIN v3_mobile_user_profiles p ON p.user_id=u.id "
            "WHERE u.id=? AND u.role='user'",
            (now_text, now_text, now_text, user_id),
        ).fetchone()
        if row is None:
            raise MobileUserNotFound("mobile user does not exist")
        return row

    @staticmethod
    def _summary(row: sqlite3.Row) -> dict:
        return {
            "user_id": int(row["user_id"]),
            "username": row["username"],
            "display_name": row["display_name"],
            "mobile_only": bool(row["mobile_only"]),
            "account_status": row["account_status"],
            "profile_version": int(row["profile_version"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "disabled_at": row["disabled_at"],
            "disabled_reason": row["disabled_reason"],
            "device_scope_count": int(row["device_scope_count"]),
            "area_scope_count": int(row["area_scope_count"]),
            "active_session_count": int(row["active_session_count"]),
            "revoked_session_count": int(row["revoked_session_count"]),
            "unused_pairing": {
                "available": row["unused_pairing_expires_at"] is not None,
                "expires_at": row["unused_pairing_expires_at"],
            },
        }

    def list_mobile_users(
        self,
        *,
        search: str | None = None,
        account_status: str | None = None,
        mobile_only: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        if search is not None:
            search = _text(search, "search", maximum=100)
        if account_status not in {None, "active", "disabled"}:
            raise MobileAccessError("account_status must be active or disabled")
        if mobile_only is not None and type(mobile_only) is not bool:
            raise MobileAccessError("mobile_only must be a boolean")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise MobileAccessError("limit must be between 1 and 100")
        if type(offset) is not int or offset < 0:
            raise MobileAccessError("offset must be non-negative")
        connection = self._connect()
        now_text = _iso(self._now())
        conditions = ["u.role='user'"]
        parameters: list[object] = []
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append(
                "(u.username LIKE ? ESCAPE '\\' OR "
                "COALESCE(p.display_name, u.username) LIKE ? ESCAPE '\\')"
            )
            parameters.extend([f"%{escaped}%", f"%{escaped}%"])
        if account_status:
            conditions.append("COALESCE(p.account_status, 'active')=?")
            parameters.append(account_status)
        if mobile_only is not None:
            conditions.append("COALESCE(p.mobile_only, 0)=?")
            parameters.append(1 if mobile_only else 0)
        where = " WHERE " + " AND ".join(conditions)
        try:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM users u LEFT JOIN v3_mobile_user_profiles p "
                "ON p.user_id=u.id" + where,
                parameters,
            ).fetchone()[0])
            ids = connection.execute(
                "SELECT u.id FROM users u LEFT JOIN v3_mobile_user_profiles p "
                "ON p.user_id=u.id" + where
                + " ORDER BY u.username COLLATE NOCASE, u.id LIMIT ? OFFSET ?",
                [*parameters, limit, offset],
            ).fetchall()
            items = [
                self._summary(self._user_summary_row(connection, int(row[0]), now_text))
                for row in ids
            ]
            return {
                "items": items,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(items) < total,
            }
        finally:
            connection.close()

    def get_mobile_user(self, user_id: int) -> dict:
        user_id = _positive_user_id(user_id)
        connection = self._connect()
        try:
            return self._summary(
                self._user_summary_row(connection, user_id, _iso(self._now()))
            )
        finally:
            connection.close()

    def create_mobile_user(
        self,
        *,
        username,
        display_name,
        actor: MobileActor,
        request_id: str,
    ) -> dict:
        username = _username(username)
        display_name = _text(display_name, "display_name", maximum=100)
        actor.validate()
        request_id = _request_id(request_id)
        connection = self._connect()
        now = self._now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM users WHERE username=? COLLATE NOCASE", (username,)
            ).fetchone():
                raise MobileUsernameConflict("username already exists")
            password_hash = generate_password_hash(secrets.token_urlsafe(48))
            cursor = connection.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'user')",
                (username, password_hash),
            )
            user_id = int(cursor.lastrowid)
            now_text = _iso(now)
            connection.execute(
                "INSERT INTO v3_mobile_user_profiles "
                "(user_id, display_name, mobile_only, account_status, profile_version, "
                "created_by, created_at, updated_at) VALUES (?, ?, 1, 'active', 1, ?, ?, ?)",
                (user_id, display_name, actor.user_id, now_text, now_text),
            )
            self._audit(
                connection, action="mobile_user_created",
                actor=f"web-admin:{actor.user_id}:{actor.username}",
                request_id=request_id, result="success", reason="mobile_user_created",
                user_id=user_id, occurred_at=now,
            )
            self.fault_injector("create_mobile_user_before_commit")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_mobile_user(user_id)

    def update_mobile_user(
        self,
        user_id: int,
        *,
        expected_profile_version,
        display_name=None,
        account_status=None,
        disabled_reason=None,
        actor: MobileActor,
        request_id: str,
    ) -> dict:
        user_id = _positive_user_id(user_id)
        if type(expected_profile_version) is not int or expected_profile_version < 0:
            raise MobileAccessError(
                "expected_profile_version must be a non-negative integer"
            )
        if display_name is not None:
            display_name = _text(display_name, "display_name", maximum=100)
        if account_status not in {None, "active", "disabled"}:
            raise MobileAccessError("account_status must be active or disabled")
        if disabled_reason is not None:
            disabled_reason = _text(disabled_reason, "disabled_reason", maximum=256)
        if account_status == "disabled" and not disabled_reason:
            raise MobileAccessError("disabled_reason is required when disabling a user")
        if display_name is None and account_status is None:
            raise MobileAccessError("PATCH must change display_name or account_status")
        actor.validate()
        request_id = _request_id(request_id)
        connection = self._connect()
        now = self._now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            user = self._role_user(connection, user_id)
            profile = connection.execute(
                "SELECT * FROM v3_mobile_user_profiles WHERE user_id=?", (user_id,)
            ).fetchone()
            current_version = int(profile["profile_version"]) if profile else 0
            if current_version != expected_profile_version:
                raise MobileUserProfileConflict("mobile user profile version is stale")
            current_name = profile["display_name"] if profile else user["username"]
            current_status = profile["account_status"] if profile else "active"
            next_name = display_name if display_name is not None else current_name
            next_status = account_status if account_status is not None else current_status
            if next_name == current_name and next_status == current_status:
                connection.rollback()
                return self.get_mobile_user(user_id)
            version = current_version + 1
            now_text = _iso(now)
            disabled_at = now_text if next_status == "disabled" else None
            reason = disabled_reason if next_status == "disabled" else None
            if profile is None:
                connection.execute(
                    "INSERT INTO v3_mobile_user_profiles "
                    "(user_id, display_name, mobile_only, account_status, profile_version, "
                    "created_by, created_at, updated_at, disabled_at, disabled_reason) "
                    "VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        user_id, next_name, next_status, version, actor.user_id,
                        now_text, now_text, disabled_at, reason,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE v3_mobile_user_profiles SET display_name=?, account_status=?, "
                    "profile_version=?, updated_at=?, disabled_at=?, disabled_reason=? "
                    "WHERE user_id=?",
                    (
                        next_name, next_status, version, now_text, disabled_at,
                        reason, user_id,
                    ),
                )
            action = "mobile_user_updated"
            if current_status != next_status and next_status == "disabled":
                action = "mobile_user_disabled"
                connection.execute(
                    "UPDATE v3_mobile_sessions SET revoked_at=COALESCE(revoked_at, ?), "
                    "revoked_reason=CASE WHEN revoked_at IS NULL THEN 'account_disabled' "
                    "ELSE revoked_reason END WHERE user_id=?",
                    (now_text, user_id),
                )
                connection.execute(
                    "UPDATE v3_mobile_pairings SET invalidated_at=? WHERE user_id=? "
                    "AND claimed_at IS NULL AND invalidated_at IS NULL",
                    (now_text, user_id),
                )
            elif current_status != next_status:
                action = "mobile_user_restored"
            self._audit(
                connection, action=action,
                actor=f"web-admin:{actor.user_id}:{actor.username}",
                request_id=request_id, result="success", reason=action,
                user_id=user_id, occurred_at=now,
            )
            self.fault_injector("update_mobile_user_before_commit")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_mobile_user(user_id)

    def _normalize_scopes(
        self, connection: sqlite3.Connection, scopes
    ) -> list[tuple[str, str]]:
        if not isinstance(scopes, list) or len(scopes) > 256:
            raise MobileAccessError("scopes must be an array with at most 256 entries")
        normalized: set[tuple[str, str]] = set()
        for item in scopes:
            if not isinstance(item, dict) or set(item) != {"scope_kind", "scope_value"}:
                raise MobileAccessError("each scope must contain scope_kind and scope_value")
            kind, value = item["scope_kind"], item["scope_value"]
            if kind == "device":
                if not isinstance(value, str) or not is_valid_device_id(value):
                    raise MobileScopeReferenceError("device scope value is invalid")
                if connection.execute(
                    "SELECT 1 FROM v3_device_profiles WHERE device_id = ?", (value,)
                ).fetchone() is None:
                    raise MobileScopeReferenceError("device scope references an unknown device")
                normalized.add((kind, value))
            elif kind == "area":
                area = _text(value, "area_id", maximum=64)
                if not _AREA_PATTERN.fullmatch(area):
                    raise MobileScopeReferenceError("area scope value is invalid")
                normalized.add((kind, area))
            else:
                raise MobileScopeReferenceError("scope_kind must be device or area")
        return sorted(normalized)

    def get_scopes(self, user_id: int) -> dict:
        user_id = _positive_user_id(user_id)
        connection = self._connect()
        try:
            user = self._role_user(connection, user_id)
            version_row = connection.execute(
                "SELECT scope_version FROM v3_mobile_scope_sets WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            scopes = connection.execute(
                "SELECT scope_kind, scope_value, scope_version, created_at "
                "FROM v3_mobile_user_scopes WHERE user_id = ? AND revoked_at IS NULL "
                "ORDER BY scope_kind, scope_value", (user_id,),
            ).fetchall()
            return {
                "user": {
                    "user_id": user["id"], "username": user["username"],
                    "role": user["role"],
                },
                "scope_version": int(version_row[0]) if version_row else 0,
                "scopes": [dict(row) for row in scopes],
            }
        finally:
            connection.close()

    def replace_scopes(
        self,
        user_id: int,
        scopes,
        *,
        expected_scope_version: int,
        actor: MobileActor,
        request_id: str,
    ) -> dict:
        user_id = _positive_user_id(user_id)
        actor.validate()
        request_id = _request_id(request_id)
        if type(expected_scope_version) is not int or expected_scope_version < 0:
            raise MobileAccessError("expected_scope_version must be a non-negative integer")
        connection = self._connect()
        now = self._now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._role_user(connection, user_id)
            normalized = self._normalize_scopes(connection, scopes)
            row = connection.execute(
                "SELECT scope_version FROM v3_mobile_scope_sets WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            current_version = int(row[0]) if row else 0
            if current_version != expected_scope_version:
                raise MobileScopeConflict("mobile scope version is stale")
            new_version = current_version + 1
            now_text = _iso(now)
            connection.execute(
                "UPDATE v3_mobile_user_scopes SET revoked_at = ? "
                "WHERE user_id = ? AND revoked_at IS NULL", (now_text, user_id),
            )
            connection.executemany(
                "INSERT INTO v3_mobile_user_scopes "
                "(user_id, scope_kind, scope_value, scope_version, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(user_id, kind, value, new_version, actor.user_id, now_text)
                 for kind, value in normalized],
            )
            connection.execute(
                "INSERT INTO v3_mobile_scope_sets "
                "(user_id, scope_version, updated_by, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET scope_version=excluded.scope_version, "
                "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                (user_id, new_version, actor.user_id, now_text),
            )
            self._audit(
                connection, action="scopes_replaced",
                actor=f"web-admin:{actor.user_id}:{actor.username}",
                request_id=request_id, result="success", reason="scopes_replaced",
                user_id=user_id, occurred_at=now,
            )
            self.fault_injector("replace_scopes_before_commit")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_scopes(user_id)

    def _new_pairing_code(self, connection: sqlite3.Connection) -> tuple[str, str, str]:
        for _ in range(10):
            normalized = "".join(
                secrets.choice(_PAIRING_ALPHABET)
                for _index in range(_PAIRING_NORMALIZED_LENGTH)
            )
            selector = normalized[:_PAIRING_SELECTOR_LENGTH]
            if connection.execute(
                "SELECT 1 FROM v3_mobile_pairings WHERE code_selector = ?", (selector,)
            ).fetchone() is None:
                return normalized, selector, self._digest("pairing", normalized)
        raise MobileAccessError("could not allocate pairing selector")

    def start_pairing(
        self,
        user_id: int,
        *,
        actor: MobileActor,
        request_id: str,
        ttl_seconds: int | None = None,
    ) -> dict:
        user_id = _positive_user_id(user_id)
        actor.validate()
        request_id = _request_id(request_id)
        ttl = self.settings.pairing_ttl_seconds if ttl_seconds is None else ttl_seconds
        if type(ttl) is not int or not 60 <= ttl <= 900:
            raise MobileAccessError("pairing ttl_seconds must be between 60 and 900")
        connection = self._connect()
        now = self._now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            user = self._eligible_user(connection, user_id)
            if self._active_scope_count(connection, user_id) == 0:
                raise PairingScopeRequired("user needs an active device or area scope")
            now_text = _iso(now)
            connection.execute(
                "UPDATE v3_mobile_pairings SET invalidated_at = ? "
                "WHERE user_id = ? AND claimed_at IS NULL AND invalidated_at IS NULL",
                (now_text, user_id),
            )
            code, selector, code_hash = self._new_pairing_code(connection)
            pairing_id = str(uuid4())
            expires_at = now + timedelta(seconds=ttl)
            connection.execute(
                "INSERT INTO v3_mobile_pairings "
                "(pairing_id, user_id, code_selector, code_hash, expires_at, max_attempts, "
                "attempt_count, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (pairing_id, user_id, selector, code_hash, _iso(expires_at),
                 self.settings.pairing_max_attempts, actor.user_id, now_text),
            )
            self._audit(
                connection, action="pairing_started",
                actor=f"web-admin:{actor.user_id}:{actor.username}",
                request_id=request_id, result="success", reason="pairing_started",
                user_id=user_id, pairing_id=pairing_id, occurred_at=now,
            )
            self.fault_injector("start_pairing_before_commit")
            connection.commit()
            return {
                "pairing_id": pairing_id,
                "user": {"user_id": user["id"], "username": user["username"]},
                "pairing_code": _format_pairing_code(code),
                "expires_at": _iso(expires_at),
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _new_token(
        self, connection: sqlite3.Connection, prefix: str, selector_column: str
    ) -> tuple[str, str, str]:
        purpose = "access" if prefix == "ma" else "refresh"
        for _ in range(10):
            selector, secret = secrets.token_hex(8), secrets.token_urlsafe(32)
            token = f"{prefix}_{selector}_{secret}"
            session_collision = connection.execute(
                f"SELECT 1 FROM v3_mobile_sessions WHERE {selector_column} = ?",
                (selector,),
            ).fetchone()
            history_collision = (
                connection.execute(
                    "SELECT 1 FROM v3_mobile_refresh_history "
                    "WHERE refresh_token_selector = ?",
                    (selector,),
                ).fetchone()
                if prefix == "mr"
                else None
            )
            if session_collision is None and history_collision is None:
                return token, selector, self._digest(purpose, token)
        raise MobileAccessError("could not allocate mobile token selector")

    def _rate_bucket(self, action: str, identity: str) -> str:
        return self._digest("rate", f"{action}:{identity or 'unknown'}")

    def _consume_rate_limit(
        self, connection: sqlite3.Connection, *, action: str, identity: str,
        limit: int, now: datetime,
    ) -> bool:
        key = self._rate_bucket(action, identity)
        row = connection.execute(
            "SELECT window_started_at, attempt_count, blocked_until "
            "FROM v3_mobile_rate_limits WHERE action = ? AND bucket_key = ?",
            (action, key),
        ).fetchone()
        window_start, attempts, blocked_until = now, 1, None
        if row:
            existing_block = _parse_time(row["blocked_until"]) if row["blocked_until"] else None
            if existing_block and now < existing_block:
                connection.execute(
                    "UPDATE v3_mobile_rate_limits SET updated_at=? "
                    "WHERE action=? AND bucket_key=?", (_iso(now), action, key),
                )
                return False
            prior_start = _parse_time(row["window_started_at"])
            if now - prior_start <= timedelta(seconds=self.settings.rate_window_seconds):
                window_start, attempts = prior_start, int(row["attempt_count"]) + 1
        if attempts > limit:
            blocked_until = now + timedelta(seconds=self.settings.rate_block_seconds)
        connection.execute(
            "INSERT INTO v3_mobile_rate_limits "
            "(action, bucket_key, window_started_at, attempt_count, blocked_until, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(action, bucket_key) DO UPDATE SET "
            "window_started_at=excluded.window_started_at, attempt_count=excluded.attempt_count, "
            "blocked_until=excluded.blocked_until, updated_at=excluded.updated_at",
            (action, key, _iso(window_start), attempts,
             _iso(blocked_until) if blocked_until else None, _iso(now)),
        )
        return blocked_until is None

    def _clear_rate_limit(
        self, connection: sqlite3.Connection, *, action: str, identity: str
    ) -> None:
        connection.execute(
            "DELETE FROM v3_mobile_rate_limits WHERE action=? AND bucket_key=?",
            (action, self._rate_bucket(action, identity)),
        )

    def consume_scoped_read_limit(
        self,
        principal: MobilePrincipal,
        *,
        action: str,
        limit: int,
    ) -> None:
        """Apply a persistent per-mobile-session limit to scoped read APIs."""
        action = _text(action, "action", maximum=64)
        if not action.startswith("mobile_device_"):
            raise MobileAccessError("invalid mobile read limit action")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise MobileAccessError("mobile read limit is invalid")
        connection = self._connect()
        now = self._now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            allowed = self._consume_rate_limit(
                connection,
                action=action,
                identity=principal.session_id,
                limit=limit,
                now=now,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        if not allowed:
            raise MobileRateLimited("too many mobile device read requests")

    def claim_pairing(
        self,
        pairing_code,
        *,
        client_instance_id,
        client_display_name,
        rate_identity: str,
        request_id: str,
    ) -> dict:
        client_id = _client_instance(client_instance_id)
        client_name = _client_name(client_display_name)
        request_id = _request_id(request_id)
        normalized_code = _normalize_pairing_code(pairing_code)
        selector = normalized_code[:_PAIRING_SELECTOR_LENGTH]
        supplied_hash = self._digest("pairing", normalized_code)
        connection = self._connect()
        now = self._now()
        error: MobileAccessError | None = None
        result: dict | None = None
        try:
            connection.execute("BEGIN IMMEDIATE")
            if not self._consume_rate_limit(
                connection, action="pairing_claim", identity=rate_identity,
                limit=self.settings.claim_rate_limit, now=now,
            ):
                self._audit(
                    connection, action="pairing_claim", actor="mobile-anonymous",
                    request_id=request_id, result="failure", reason="rate_limited",
                    occurred_at=now,
                )
                error = MobileRateLimited("too many pairing attempts")
            else:
                row = connection.execute(
                    "SELECT * FROM v3_mobile_pairings WHERE code_selector = ?",
                    (selector,),
                ).fetchone() if selector else None
                hash_matches = bool(row) and hmac.compare_digest(
                    row["code_hash"], supplied_hash
                )
                valid = False
                user = None
                if hash_matches:
                    valid = (
                        row["claimed_at"] is None
                        and row["invalidated_at"] is None
                        and int(row["attempt_count"]) < int(row["max_attempts"])
                        and now < _parse_time(row["expires_at"])
                    )
                    if valid:
                        try:
                            user = self._eligible_user(connection, int(row["user_id"]))
                            valid = self._active_scope_count(connection, int(row["user_id"])) > 0
                        except MobileAccessError:
                            valid = False
                if valid and row is not None and user is not None:
                    access_token, access_selector, access_hash = self._new_token(
                        connection, "ma", "access_token_selector"
                    )
                    refresh_token, refresh_selector, refresh_hash = self._new_token(
                        connection, "mr", "refresh_token_selector"
                    )
                    session_id = str(uuid4())
                    access_expires = now + timedelta(seconds=self.settings.access_ttl_seconds)
                    refresh_expires = now + timedelta(seconds=self.settings.refresh_ttl_seconds)
                    now_text = _iso(now)
                    connection.execute(
                        "INSERT INTO v3_mobile_sessions "
                        "(session_id, user_id, client_instance_id, client_display_name, "
                        "access_token_selector, access_token_hash, refresh_token_selector, "
                        "refresh_token_hash, created_at, issued_at, access_expires_at, "
                        "refresh_expires_at, last_seen_at, token_generation) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                        (
                            session_id, row["user_id"], client_id, client_name,
                            access_selector, access_hash, refresh_selector, refresh_hash,
                            now_text, now_text, _iso(access_expires),
                            _iso(refresh_expires), now_text,
                        ),
                    )
                    self.fault_injector("claim_after_session_insert")
                    updated = connection.execute(
                        "UPDATE v3_mobile_pairings SET claimed_at=? "
                        "WHERE pairing_id=? AND claimed_at IS NULL AND invalidated_at IS NULL",
                        (now_text, row["pairing_id"]),
                    ).rowcount
                    if updated != 1:
                        raise PairingClaimRejected("pairing code is not valid")
                    self._audit(
                        connection, action="pairing_claim",
                        actor=f"mobile-client:{client_id}", request_id=request_id,
                        result="success", reason="pairing_claimed", user_id=row["user_id"],
                        session_id=session_id, pairing_id=row["pairing_id"], occurred_at=now,
                    )
                    self._clear_rate_limit(
                        connection, action="pairing_claim", identity=rate_identity
                    )
                    result = {
                        "session_id": session_id,
                        "access_token": access_token,
                        "refresh_token": refresh_token,
                        "access_expires_at": _iso(access_expires),
                        "refresh_expires_at": _iso(refresh_expires),
                        "user": {
                            "user_id": user["id"], "username": user["username"],
                            "role": Role.USER.value,
                        },
                    }
                else:
                    pairing_id = row["pairing_id"] if row else None
                    user_id = row["user_id"] if row else None
                    if row and not hash_matches:
                        attempts = int(row["attempt_count"]) + 1
                        invalidated = _iso(now) if attempts >= int(row["max_attempts"]) else None
                        connection.execute(
                            "UPDATE v3_mobile_pairings SET attempt_count=?, "
                            "invalidated_at=COALESCE(invalidated_at, ?) WHERE pairing_id=?",
                            (attempts, invalidated, row["pairing_id"]),
                        )
                    elif row and hash_matches and now >= _parse_time(row["expires_at"]):
                        connection.execute(
                            "UPDATE v3_mobile_pairings SET "
                            "invalidated_at=COALESCE(invalidated_at, ?) WHERE pairing_id=?",
                            (_iso(now), row["pairing_id"]),
                        )
                    self._audit(
                        connection, action="pairing_claim",
                        actor=f"mobile-client:{client_id}", request_id=request_id,
                        result="failure", reason="pairing_claim_rejected", user_id=user_id,
                        pairing_id=pairing_id, occurred_at=now,
                    )
                    error = PairingClaimRejected("pairing code is invalid or unavailable")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        if error:
            raise error
        if result is None:
            raise PairingClaimRejected("pairing code is invalid or unavailable")
        return result

    @staticmethod
    def _parse_token(token, expected_prefix: str) -> tuple[str, str]:
        if not isinstance(token, str):
            raise MobileAuthenticationError("mobile token is invalid")
        match = _TOKEN_PATTERN.fullmatch(token)
        if not match or match.group("prefix") != expected_prefix:
            raise MobileAuthenticationError("mobile token is invalid")
        return match.group("selector"), token

    def authenticate_access(self, token) -> MobilePrincipal:
        selector, raw_token = self._parse_token(token, "ma")
        connection = self._connect()
        now = self._now()
        try:
            row = connection.execute(
                "SELECT s.*, u.username, u.role FROM v3_mobile_sessions s "
                "LEFT JOIN users u ON u.id=s.user_id WHERE s.access_token_selector=?",
                (selector,),
            ).fetchone()
            if row is None or not hmac.compare_digest(
                row["access_token_hash"], self._digest("access", raw_token)
            ):
                raise MobileAuthenticationError("mobile token is invalid")
            if row["revoked_at"] is not None:
                raise MobileSessionRevoked("mobile session is revoked")
            if now >= _parse_time(row["access_expires_at"]):
                raise MobileTokenExpired("mobile access token is expired")
            if row["role"] != Role.USER.value:
                raise MobileAuthenticationError("mobile user is no longer eligible")
            connection.execute(
                "UPDATE v3_mobile_sessions SET last_seen_at=? WHERE session_id=?",
                (_iso(now), row["session_id"]),
            )
            connection.commit()
            return MobilePrincipal(
                session_id=row["session_id"], user_id=row["user_id"],
                username=row["username"], client_instance_id=row["client_instance_id"],
                client_display_name=row["client_display_name"],
                token_generation=row["token_generation"],
            )
        finally:
            connection.close()

    def refresh_tokens(
        self, refresh_token, *, rate_identity: str, request_id: str
    ) -> dict:
        try:
            selector, raw_token = self._parse_token(refresh_token, "mr")
        except MobileAuthenticationError:
            # Keep malformed attempts inside the same persistent rate-limit path.
            # Empty selectors cannot match a generated token.
            selector, raw_token = "", ""
        request_id = _request_id(request_id)
        connection = self._connect()
        now = self._now()
        error: MobileAccessError | None = None
        result: dict | None = None
        try:
            connection.execute("BEGIN IMMEDIATE")
            if not self._consume_rate_limit(
                connection, action="token_refresh", identity=rate_identity,
                limit=self.settings.refresh_rate_limit, now=now,
            ):
                self._audit(
                    connection, action="token_refresh", actor="mobile-refresh",
                    request_id=request_id, result="failure", reason="rate_limited",
                    occurred_at=now,
                )
                error = MobileRateLimited("too many token refresh attempts")
            else:
                row = connection.execute(
                    "SELECT s.*, u.username, u.role FROM v3_mobile_sessions s "
                    "LEFT JOIN users u ON u.id=s.user_id "
                    "WHERE s.refresh_token_selector=?", (selector,),
                ).fetchone()
                current_match = bool(row) and hmac.compare_digest(
                    row["refresh_token_hash"], self._digest("refresh", raw_token)
                )
                if current_match and row is not None:
                    if row["revoked_at"] is not None:
                        error = MobileSessionRevoked("mobile session is revoked")
                    elif now >= _parse_time(row["refresh_expires_at"]):
                        connection.execute(
                            "UPDATE v3_mobile_sessions SET revoked_at=?, "
                            "revoked_reason='refresh_token_expired' "
                            "WHERE session_id=? AND revoked_at IS NULL",
                            (_iso(now), row["session_id"]),
                        )
                        error = MobileTokenExpired("mobile refresh token is expired")
                    elif row["role"] != Role.USER.value:
                        connection.execute(
                            "UPDATE v3_mobile_sessions SET revoked_at=?, "
                            "revoked_reason='user_ineligible' "
                            "WHERE session_id=? AND revoked_at IS NULL",
                            (_iso(now), row["session_id"]),
                        )
                        error = MobileAuthenticationError("mobile user is no longer eligible")
                    else:
                        access_token, access_selector, access_hash = self._new_token(
                            connection, "ma", "access_token_selector"
                        )
                        new_refresh, refresh_selector, refresh_hash = self._new_token(
                            connection, "mr", "refresh_token_selector"
                        )
                        generation = int(row["token_generation"]) + 1
                        access_expires = now + timedelta(seconds=self.settings.access_ttl_seconds)
                        connection.execute(
                            "INSERT INTO v3_mobile_refresh_history "
                            "(refresh_token_selector, refresh_token_hash, session_id, "
                            "token_generation, rotated_at) VALUES (?, ?, ?, ?, ?)",
                            (row["refresh_token_selector"], row["refresh_token_hash"],
                             row["session_id"], row["token_generation"], _iso(now)),
                        )
                        connection.execute(
                            "UPDATE v3_mobile_sessions SET access_token_selector=?, "
                            "access_token_hash=?, refresh_token_selector=?, refresh_token_hash=?, "
                            "issued_at=?, access_expires_at=?, last_seen_at=?, token_generation=? "
                            "WHERE session_id=?",
                            (access_selector, access_hash, refresh_selector, refresh_hash,
                             _iso(now), _iso(access_expires), _iso(now), generation,
                             row["session_id"]),
                        )
                        self._audit(
                            connection, action="token_refreshed",
                            actor=f"mobile-session:{row['session_id']}",
                            request_id=request_id, result="success", reason="token_rotated",
                            user_id=row["user_id"], session_id=row["session_id"],
                            occurred_at=now,
                        )
                        self._clear_rate_limit(
                            connection, action="token_refresh", identity=rate_identity
                        )
                        result = {
                            "session_id": row["session_id"],
                            "access_token": access_token,
                            "refresh_token": new_refresh,
                            "access_expires_at": _iso(access_expires),
                            "refresh_expires_at": row["refresh_expires_at"],
                            "token_generation": generation,
                        }
                else:
                    history = connection.execute(
                        "SELECT * FROM v3_mobile_refresh_history "
                        "WHERE refresh_token_selector=?", (selector,),
                    ).fetchone()
                    replay = bool(history) and hmac.compare_digest(
                        history["refresh_token_hash"], self._digest("refresh", raw_token)
                    )
                    if replay and history is not None:
                        session_row = connection.execute(
                            "SELECT user_id FROM v3_mobile_sessions WHERE session_id=?",
                            (history["session_id"],),
                        ).fetchone()
                        connection.execute(
                            "UPDATE v3_mobile_sessions SET revoked_at=COALESCE(revoked_at, ?), "
                            "revoked_reason='refresh_token_replay' WHERE session_id=?",
                            (_iso(now), history["session_id"]),
                        )
                        connection.execute(
                            "UPDATE v3_mobile_refresh_history SET "
                            "replayed_at=COALESCE(replayed_at, ?) "
                            "WHERE refresh_token_selector=?", (_iso(now), selector),
                        )
                        self._audit(
                            connection, action="refresh_replay", actor="mobile-refresh",
                            request_id=request_id, result="failure",
                            reason="refresh_token_replay",
                            user_id=session_row["user_id"] if session_row else None,
                            session_id=history["session_id"], occurred_at=now,
                        )
                        error = MobileRefreshReplay("refresh token replay detected")
                    else:
                        self._audit(
                            connection, action="token_refresh", actor="mobile-refresh",
                            request_id=request_id, result="failure",
                            reason="refresh_token_invalid", occurred_at=now,
                        )
                        error = MobileAuthenticationError("mobile refresh token is invalid")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        if error:
            raise error
        if result is None:
            raise MobileAuthenticationError("mobile refresh token is invalid")
        return result

    def logout(self, access_token, *, request_id: str) -> dict:
        selector, raw_token = self._parse_token(access_token, "ma")
        request_id = _request_id(request_id)
        connection = self._connect()
        now = self._now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM v3_mobile_sessions WHERE access_token_selector=?",
                (selector,),
            ).fetchone()
            if row is None or not hmac.compare_digest(
                row["access_token_hash"], self._digest("access", raw_token)
            ):
                raise MobileAuthenticationError("mobile token is invalid")
            already = row["revoked_at"] is not None
            if not already:
                connection.execute(
                    "UPDATE v3_mobile_sessions SET revoked_at=?, "
                    "revoked_reason='client_logout' WHERE session_id=?",
                    (_iso(now), row["session_id"]),
                )
            self._audit(
                connection, action="mobile_logout",
                actor=f"mobile-session:{row['session_id']}", request_id=request_id,
                result="no_op" if already else "success",
                reason="already_revoked" if already else "client_logout",
                user_id=row["user_id"], session_id=row["session_id"], occurred_at=now,
            )
            connection.commit()
            return {
                "session_id": row["session_id"], "revoked": True,
                "already_revoked": already,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def session_summary(principal: MobilePrincipal) -> dict:
        return {
            "session_id": principal.session_id,
            "user": {
                "user_id": principal.user_id, "username": principal.username,
                "role": Role.USER.value,
            },
            "client_instance_id": principal.client_instance_id,
            "client_display_name": principal.client_display_name,
            "token_generation": principal.token_generation,
        }

    def list_sessions(
        self, *, user_id: int | None = None, status: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> dict:
        if user_id is not None:
            user_id = _positive_user_id(user_id)
        if status not in {None, "active", "revoked", "expired"}:
            raise MobileAccessError("status must be active, revoked, or expired")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise MobileAccessError("limit must be between 1 and 100")
        if type(offset) is not int or offset < 0:
            raise MobileAccessError("offset must be non-negative")
        now_text = _iso(self._now())
        conditions, parameters = [], []
        if user_id is not None:
            conditions.append("s.user_id=?")
            parameters.append(user_id)
        if status == "active":
            conditions.append("s.revoked_at IS NULL AND s.refresh_expires_at>?")
            parameters.append(now_text)
        elif status == "revoked":
            conditions.append("s.revoked_at IS NOT NULL")
        elif status == "expired":
            conditions.append("s.revoked_at IS NULL AND s.refresh_expires_at<=?")
            parameters.append(now_text)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        connection = self._connect()
        try:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM v3_mobile_sessions s" + where, parameters
            ).fetchone()[0])
            rows = connection.execute(
                "SELECT s.session_id, s.user_id, u.username, s.client_instance_id, "
                "s.client_display_name, s.created_at, s.issued_at, s.last_seen_at, "
                "s.access_expires_at, s.refresh_expires_at, s.revoked_at, "
                "s.revoked_reason, s.token_generation FROM v3_mobile_sessions s "
                "LEFT JOIN users u ON u.id=s.user_id" + where +
                " ORDER BY s.created_at DESC, s.session_id LIMIT ? OFFSET ?",
                [*parameters, limit, offset],
            ).fetchall()
            return {
                "sessions": [dict(row) for row in rows],
                "pagination": {
                    "limit": limit, "offset": offset, "total": total,
                    "has_more": offset + len(rows) < total,
                },
            }
        finally:
            connection.close()

    def revoke_session(
        self,
        session_id: str,
        *,
        reason: str,
        actor: MobileActor,
        request_id: str,
    ) -> dict:
        session_id = _text(session_id, "session_id", maximum=64)
        reason = _text(reason, "reason", maximum=256)
        actor.validate()
        request_id = _request_id(request_id)
        connection = self._connect()
        now = self._now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT user_id, revoked_at, revoked_reason FROM v3_mobile_sessions "
                "WHERE session_id=?", (session_id,),
            ).fetchone()
            if row is None:
                raise MobileSessionNotFound("mobile session does not exist")
            already = row["revoked_at"] is not None
            if not already:
                connection.execute(
                    "UPDATE v3_mobile_sessions SET revoked_at=?, revoked_reason=? "
                    "WHERE session_id=?", (_iso(now), reason, session_id),
                )
            self._audit(
                connection, action="session_revoked",
                actor=f"web-admin:{actor.user_id}:{actor.username}",
                request_id=request_id, result="no_op" if already else "success",
                reason="already_revoked" if already else "admin_revoked",
                user_id=row["user_id"], session_id=session_id, occurred_at=now,
            )
            connection.commit()
            return {
                "session_id": session_id, "revoked": True,
                "already_revoked": already,
                "revoked_reason": row["revoked_reason"] if already else reason,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _availability(
        connection_status: str, operation_mode: str, retired: bool
    ) -> str:
        if retired:
            return "retired"
        if operation_mode == "maintenance":
            return "maintenance"
        if operation_mode == "disabled":
            return "disabled"
        return {
            "online": "available", "stale": "delayed",
            "offline": "unavailable", "unknown": "unknown",
        }.get(connection_status, "unknown")

    def overview(self, principal: MobilePrincipal) -> dict:
        connection = self._connect()
        try:
            scopes = connection.execute(
                "SELECT scope_kind, scope_value FROM v3_mobile_user_scopes "
                "WHERE user_id=? AND revoked_at IS NULL", (principal.user_id,),
            ).fetchall()
            device_ids = sorted({
                row["scope_value"] for row in scopes if row["scope_kind"] == "device"
            })
            area_ids = sorted({
                row["scope_value"] for row in scopes if row["scope_kind"] == "area"
            })
            conditions, parameters = [], []
            if device_ids:
                conditions.append(
                    "p.device_id IN (" + ",".join("?" for _ in device_ids) + ")"
                )
                parameters.extend(device_ids)
            if area_ids:
                conditions.append(
                    "p.area_id IN (" + ",".join("?" for _ in area_ids) + ")"
                )
                parameters.extend(area_ids)
            rows = []
            if conditions:
                rows = connection.execute(
                    "SELECT p.device_id, p.display_name, p.device_type, p.area_id, "
                    "p.operation_mode, p.retired_at, p.updated_at AS profile_updated_at, "
                    "COALESCE(s.connection_status, 'unknown') AS connection_status, "
                    "s.updated_at AS state_updated_at FROM v3_device_profiles p "
                    "LEFT JOIN v3_device_current_state s ON s.device_id=p.device_id WHERE "
                    + " OR ".join(f"({condition})" for condition in conditions)
                    + " ORDER BY p.display_name COLLATE NOCASE, p.device_id",
                    parameters,
                ).fetchall()
            devices = []
            for row in rows:
                retired = row["retired_at"] is not None
                devices.append({
                    "device_id": row["device_id"],
                    "display_name": row["display_name"],
                    "device_type": row["device_type"],
                    "area_id": row["area_id"],
                    "connection_status": row["connection_status"],
                    "operation_mode": row["operation_mode"],
                    "retired": retired,
                    "retired_at": row["retired_at"],
                    "last_updated_at": row["state_updated_at"] or row["profile_updated_at"],
                    "availability_status": self._availability(
                        row["connection_status"], row["operation_mode"], retired
                    ),
                })
            try:
                # Delayed import avoids a module cycle: the incident service
                # consumes MobilePrincipal while pairing stays independently
                # importable before migration v8 is installed.
                from services.incident_workflow import (
                    IncidentStoreUnavailable,
                    IncidentWorkflowService,
                )

                security_capability = IncidentWorkflowService(
                    self.database_path,
                    self.settings,
                    clock=self.clock,
                ).overview_security(principal)
            except IncidentStoreUnavailable:
                security_capability = {
                    "available": False,
                    "reason": "incident_pipeline_not_ready",
                    "gnn": {
                        "available": False,
                        "reason": "gnn_capability_unavailable",
                    },
                }
            return {
                "generated_at": _iso(self._now()),
                "user": {
                    "user_id": principal.user_id, "username": principal.username,
                },
                "devices": devices,
                "security_capability": security_capability,
            }
        finally:
            connection.close()


__all__ = [
    "MobileAccessError",
    "MobileAccessService",
    "MobileActor",
    "MobileAuthenticationError",
    "MobilePrincipal",
    "MobileRateLimited",
    "MobileScopeConflict",
    "MobileSessionRevoked",
    "MobileStoreUnavailable",
]
