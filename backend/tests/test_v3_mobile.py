from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import sqlite3
from types import SimpleNamespace

import pytest
from werkzeug.security import check_password_hash

from app import create_app, get_service_container
from config import MobileSecuritySettings
from database import init_db
from services.device_traffic import TrafficSample
from services.incident_workflow import IncidentActor
from services.mobile_access import (
    MobileAccessService,
    MobileActor,
    MobileAuthenticationError,
    MobileRefreshReplay,
    MobileScopeConflict,
)
from v3_database import (
    V3_MIGRATIONS,
    V3_MOBILE_ACCESS_INDEXES,
    V3_MOBILE_ACCESS_MIGRATION,
    V3_MOBILE_USER_ADMIN_MIGRATION,
    V3_MOBILE_ACCESS_TABLES,
    apply_v3_migrations,
    connect_v3,
    initialize_v3_database,
)


FROZEN_CHECKSUMS = [
    "3fe72003fd5eb35063bd5aea3f677bc66ef0aaaa36cded58a83f46738bd26952",
    "77ce4e371366c7d3e53640debb212c9a736fd5e9f8849b06e6d3700508d48078",
    "bfe9842f09d391284b408dd0df36e205e4ad9f92361ee304dd52955c9f6aa329",
    "685caf41c5d21471c14ef7b6608cce6d43a4cd69fff3961c1888c3166a4bebcd",
    "77e7011d94ef2b3a7022013fbef0c2e68f70f1ca9ead58446dd3d4bde78c74c6",
]
V6_CHECKSUM = "f1ce25c5393381750c7582eb7dc783c7625db80a0dad483c1e46cf1b7521b61d"
V7_CHECKSUM = "5e8e572496607b58d0ccf93be0bcd1deaaa7d3935f93cef54cccd35e905b3623"
NOW = datetime(2026, 9, 21, 4, 0, tzinfo=timezone.utc)
ADMIN = MobileActor(1, "admin-test", "admin")


@dataclass
class MutableClock:
    value: datetime = NOW

    def __call__(self):
        return self.value

    def advance(self, seconds: int):
        self.value += timedelta(seconds=seconds)


def _settings(**overrides):
    values = {
        "token_secret": "mobile-test-secret-0123456789-abcdefghijklmnopqrstuvwxyz",
        "environment": "testing",
        "allow_insecure_http": True,
        "pairing_ttl_seconds": 300,
        "access_ttl_seconds": 300,
        "refresh_ttl_seconds": 3600,
        "pairing_max_attempts": 3,
        "rate_window_seconds": 60,
        "rate_block_seconds": 60,
        "claim_rate_limit": 20,
        "refresh_rate_limit": 20,
    }
    values.update(overrides)
    return MobileSecuritySettings(**values)


def _insert_fixture_rows(database_path):
    with sqlite3.connect(database_path) as connection:
        connection.executemany(
            "INSERT INTO users (id, username, password_hash, role) VALUES (?, ?, 'x', ?)",
            [
                (1, "admin-test", "admin"),
                (2, "operator-test", "operator"),
                (3, "mobile-alice", "user"),
                (4, "mobile-bob", "user"),
            ],
        )
        connection.executemany(
            "INSERT INTO v3_device_profiles "
            "(device_id, identity_kind, identity_value, display_name, device_type, "
            "area_id, operation_mode, created_at, updated_at, importance, "
            "profile_source, profile_version, retired_at, retirement_reason) "
            "VALUES (?, 'mac', ?, ?, ?, ?, ?, ?, ?, 'normal', 'physical', 1, ?, ?)",
            [
                (
                    "camera-01", "AA:BB:CC:DD:EE:01", "门厅摄像机", "camera",
                    "area-a", "active", "2026-09-20T00:00:00Z",
                    "2026-09-21T03:59:00Z", None, None,
                ),
                (
                    "lock-01", "AA:BB:CC:DD:EE:02", "后门门锁", "lock",
                    "area-b", "active", "2026-09-20T00:00:00Z",
                    "2026-09-21T03:58:00Z", None, None,
                ),
                (
                    "sensor-01", "AA:BB:CC:DD:EE:03", "旧温度传感器", "sensor",
                    "area-a", "disabled", "2026-09-20T00:00:00Z",
                    "2026-09-21T03:57:00Z", "2026-09-21T03:00:00Z", "replaced",
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO v3_device_current_state "
            "(device_id, connection_status, ip_address, last_observed_at, "
            "last_received_at, state_version, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "camera-01", "online", "192.0.2.10", "2026-09-21T03:59:50Z",
                    "2026-09-21T03:59:51Z", 2, "2026-09-21T03:59:51Z",
                ),
                (
                    "lock-01", "unknown", None, None, None, 0,
                    "2026-09-20T00:00:00Z",
                ),
                (
                    "sensor-01", "stale", "192.0.2.12", "2026-09-21T03:40:00Z",
                    "2026-09-21T03:40:01Z", 4, "2026-09-21T03:40:01Z",
                ),
            ],
        )


@pytest.fixture
def mobile_context(tmp_path):
    database_path = tmp_path / "mobile.sqlite"
    init_db(database_path)
    initialize_v3_database(database_path)
    _insert_fixture_rows(database_path)
    clock = MutableClock()
    settings = _settings()
    application = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "mobile-web-session-test-secret",
            "DATABASE_PATH": str(database_path),
            "V3_CLOCK": clock,
        },
        mobile_settings=settings,
    )
    return SimpleNamespace(
        database_path=database_path,
        clock=clock,
        settings=settings,
        service=application.extensions["iot_ids_mobile_access"],
        app=application,
        client=application.test_client(),
    )


def _login(client, user_id=1, username="admin-test", role="admin"):
    with client.session_transaction() as state:
        state["user_id"] = user_id
        state["username"] = username
        state["role"] = role


def _csrf(client, user_id=3):
    response = client.get(f"/api/v3/mobile-users/{user_id}/scopes")
    assert response.status_code == 200
    return response.headers["X-CSRF-Token"]


def _set_scopes(context, scopes, expected=0, user_id=3):
    return context.service.replace_scopes(
        user_id,
        scopes,
        expected_scope_version=expected,
        actor=ADMIN,
        request_id=f"scope-{expected + 1}",
    )


def _pair(context, *, user_id=3, client_suffix="one"):
    started = context.service.start_pairing(
        user_id, actor=ADMIN, request_id=f"pair-{client_suffix}"
    )
    claimed = context.service.claim_pairing(
        started["pairing_code"],
        client_instance_id=f"client-{client_suffix}-1234",
        client_display_name=f"测试手机 {client_suffix}",
        rate_identity=f"127.0.0.{len(client_suffix) + 1}",
        request_id=f"claim-{client_suffix}",
    )
    return started, claimed


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_v6_v7_migrations_are_additive_idempotent_and_keep_prior_checksums(tmp_path):
    assert [item.checksum for item in V3_MIGRATIONS[:5]] == FROZEN_CHECKSUMS
    assert V3_MOBILE_ACCESS_MIGRATION.checksum == V6_CHECKSUM
    assert V3_MOBILE_USER_ADMIN_MIGRATION.checksum == V7_CHECKSUM
    database_path = tmp_path / "upgrade.sqlite"
    init_db(database_path)
    connection = connect_v3(database_path)
    try:
        assert apply_v3_migrations(connection, V3_MIGRATIONS[:5])["schema_version"] == 5
        first = apply_v3_migrations(connection)
        second = apply_v3_migrations(connection)
        objects = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'index')"
            )
        }
        session_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(v3_mobile_sessions)")
        }
    finally:
        connection.close()
    assert first["applied_versions"] == [6, 7, 8, 9]
    assert second["applied_versions"] == []
    assert second["skipped_versions"] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert V3_MOBILE_ACCESS_TABLES <= objects.keys()
    assert V3_MOBILE_ACCESS_INDEXES <= objects.keys()
    assert "access_token" not in session_columns
    assert "refresh_token" not in session_columns


def test_scopes_require_user_role_deduplicate_and_use_optimistic_version(mobile_context):
    first = _set_scopes(
        mobile_context,
        [
            {"scope_kind": "device", "scope_value": "camera-01"},
            {"scope_kind": "device", "scope_value": "camera-01"},
            {"scope_kind": "area", "scope_value": "area-a"},
        ],
    )
    assert first["scope_version"] == 1
    assert [(x["scope_kind"], x["scope_value"]) for x in first["scopes"]] == [
        ("area", "area-a"),
        ("device", "camera-01"),
    ]
    with pytest.raises(MobileScopeConflict):
        _set_scopes(mobile_context, [], expected=0)
    with pytest.raises(Exception) as caught:
        _set_scopes(mobile_context, [], user_id=1)
    assert getattr(caught.value, "code", "") == "mobile_user_ineligible"


def test_scope_admin_api_csrf_permissions_and_audit(mobile_context):
    client = mobile_context.client
    assert client.get("/api/v3/mobile-users/3/scopes").status_code == 401
    _login(client, 2, "operator-test", "operator")
    assert client.get("/api/v3/mobile-users/3/scopes").status_code == 403
    _login(client, 3, "mobile-alice", "user")
    assert client.get("/api/v3/mobile-users/3/scopes").status_code == 403
    _login(client)
    assert client.put(
        "/api/v3/mobile-users/3/scopes",
        json={"expected_scope_version": 0, "scopes": []},
    ).status_code == 403
    token = _csrf(client)
    response = client.put(
        "/api/v3/mobile-users/3/scopes",
        json={
            "expected_scope_version": 0,
            "scopes": [{"scope_kind": "device", "scope_value": "camera-01"}],
        },
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200
    with sqlite3.connect(mobile_context.database_path) as connection:
        row = connection.execute(
            "SELECT action, result FROM v3_mobile_security_audit ORDER BY audit_id DESC"
        ).fetchone()
    assert row == ("scopes_replaced", "success")


def test_pairing_is_one_time_hashed_normalized_and_never_returned_again(mobile_context):
    _set_scopes(
        mobile_context, [{"scope_kind": "device", "scope_value": "camera-01"}]
    )
    started = mobile_context.service.start_pairing(
        3, actor=ADMIN, request_id="pair-start"
    )
    compact = started["pairing_code"].replace("-", "")
    assert len(compact) == 32
    assert not set(compact) & set("IO01")
    with sqlite3.connect(mobile_context.database_path) as connection:
        stored = connection.execute(
            "SELECT code_hash, code_selector FROM v3_mobile_pairings"
        ).fetchone()
    assert started["pairing_code"] not in stored
    assert compact not in stored
    claimed = mobile_context.service.claim_pairing(
        "  " + started["pairing_code"].lower().replace("-", " - ") + "  ",
        client_instance_id="phone-instance-0001",
        client_display_name="Alice Phone",
        rate_identity="127.0.0.1",
        request_id="claim-one",
    )
    assert claimed["user"] == {
        "user_id": 3, "username": "mobile-alice", "role": "user"
    }
    assert "pairing_code" not in claimed
    with pytest.raises(Exception) as reused:
        mobile_context.service.claim_pairing(
            started["pairing_code"],
            client_instance_id="phone-instance-0002",
            client_display_name="Second Phone",
            rate_identity="127.0.0.2",
            request_id="claim-reuse",
        )
    assert reused.value.code == "pairing_claim_rejected"


def test_pairing_start_requires_scope_and_admin_csrf(mobile_context):
    with pytest.raises(Exception) as no_scope:
        mobile_context.service.start_pairing(
            3, actor=ADMIN, request_id="no-scope"
        )
    assert no_scope.value.code == "mobile_scope_required"
    client = mobile_context.client
    assert client.post("/api/v3/pairing/start", json={"user_id": 3}).status_code == 401
    _login(client, 2, "operator-test", "operator")
    assert client.post("/api/v3/pairing/start", json={"user_id": 3}).status_code == 403
    _login(client)
    assert client.post("/api/v3/pairing/start", json={"user_id": 3}).status_code == 403


def test_new_pairing_invalidates_old_and_attempt_limit_is_persistent(mobile_context):
    _set_scopes(
        mobile_context, [{"scope_kind": "device", "scope_value": "camera-01"}]
    )
    old = mobile_context.service.start_pairing(3, actor=ADMIN, request_id="old")
    new = mobile_context.service.start_pairing(3, actor=ADMIN, request_id="new")
    with pytest.raises(Exception):
        mobile_context.service.claim_pairing(
            old["pairing_code"], client_instance_id="old-client-0001",
            client_display_name="Old", rate_identity="198.51.100.1",
            request_id="old-claim",
        )
    compact = new["pairing_code"].replace("-", "")
    replacement = "A" if compact[-1] != "A" else "B"
    wrong = compact[:-1] + replacement
    for index in range(3):
        with pytest.raises(Exception) as rejected:
            mobile_context.service.claim_pairing(
                wrong, client_instance_id="wrong-client-0001",
                client_display_name="Wrong", rate_identity=f"198.51.100.{index + 2}",
                request_id=f"wrong-{index}",
            )
        assert rejected.value.code == "pairing_claim_rejected"
    with sqlite3.connect(mobile_context.database_path) as connection:
        row = connection.execute(
            "SELECT attempt_count, invalidated_at FROM v3_mobile_pairings "
            "WHERE pairing_id=?", (new["pairing_id"],),
        ).fetchone()
    assert row[0] == 3
    assert row[1] is not None


def test_concurrent_claim_has_one_winner_and_claim_transaction_rolls_back(mobile_context):
    _set_scopes(
        mobile_context, [{"scope_kind": "device", "scope_value": "camera-01"}]
    )
    started = mobile_context.service.start_pairing(3, actor=ADMIN, request_id="race")

    def claim(index):
        try:
            result = mobile_context.service.claim_pairing(
                started["pairing_code"],
                client_instance_id=f"race-client-{index:04d}",
                client_display_name=f"Race {index}",
                rate_identity=f"203.0.113.{index + 1}",
                request_id=f"race-{index}",
            )
            return result["session_id"]
        except Exception as exc:
            return getattr(exc, "code", type(exc).__name__)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, (1, 2)))
    assert sum(value == "pairing_claim_rejected" for value in outcomes) == 1
    assert sum(value != "pairing_claim_rejected" for value in outcomes) == 1

    second = mobile_context.service.start_pairing(3, actor=ADMIN, request_id="rollback")

    def fail(point):
        if point == "claim_after_session_insert":
            raise RuntimeError("injected failure without secret")

    failing = MobileAccessService(
        mobile_context.database_path, mobile_context.settings,
        clock=mobile_context.clock, fault_injector=fail,
    )
    with pytest.raises(RuntimeError):
        failing.claim_pairing(
            second["pairing_code"], client_instance_id="rollback-client-01",
            client_display_name="Rollback", rate_identity="203.0.113.9",
            request_id="rollback-claim",
        )
    with sqlite3.connect(mobile_context.database_path) as connection:
        pairing = connection.execute(
            "SELECT claimed_at FROM v3_mobile_pairings WHERE pairing_id=?",
            (second["pairing_id"],),
        ).fetchone()
        count = connection.execute(
            "SELECT COUNT(*) FROM v3_mobile_sessions WHERE client_instance_id=?",
            ("rollback-client-01",),
        ).fetchone()[0]
    assert pairing[0] is None
    assert count == 0


def test_tokens_are_hashed_expire_rotate_and_replay_revokes_session(mobile_context):
    _set_scopes(
        mobile_context, [{"scope_kind": "device", "scope_value": "camera-01"}]
    )
    _started, claimed = _pair(mobile_context)
    with sqlite3.connect(mobile_context.database_path) as connection:
        row = connection.execute(
            "SELECT access_token_hash, refresh_token_hash FROM v3_mobile_sessions "
            "WHERE session_id=?", (claimed["session_id"],),
        ).fetchone()
    assert claimed["access_token"] not in row
    assert claimed["refresh_token"] not in row

    rotated = mobile_context.service.refresh_tokens(
        claimed["refresh_token"], rate_identity="127.0.0.5", request_id="refresh-1"
    )
    assert rotated["access_token"] != claimed["access_token"]
    assert rotated["refresh_token"] != claimed["refresh_token"]
    assert rotated["token_generation"] == 2
    with pytest.raises(MobileRefreshReplay):
        mobile_context.service.refresh_tokens(
            claimed["refresh_token"], rate_identity="127.0.0.6",
            request_id="refresh-replay",
        )
    with pytest.raises(Exception) as revoked:
        mobile_context.service.authenticate_access(rotated["access_token"])
    assert revoked.value.code == "mobile_session_revoked"

    _started, expiring = _pair(mobile_context, client_suffix="expiry")
    mobile_context.clock.advance(301)
    with pytest.raises(Exception) as expired:
        mobile_context.service.authenticate_access(expiring["access_token"])
    assert expired.value.code == "mobile_token_expired"


def test_logout_admin_revoke_and_session_listing_are_safe_and_idempotent(mobile_context):
    _set_scopes(
        mobile_context, [{"scope_kind": "device", "scope_value": "camera-01"}]
    )
    _started, first = _pair(mobile_context, client_suffix="logout")
    one = mobile_context.service.logout(first["access_token"], request_id="logout-1")
    two = mobile_context.service.logout(first["access_token"], request_id="logout-2")
    assert one["already_revoked"] is False
    assert two["already_revoked"] is True

    _started, second = _pair(mobile_context, client_suffix="admin")
    revoked = mobile_context.service.revoke_session(
        second["session_id"], reason="lost_device", actor=ADMIN, request_id="admin-revoke"
    )
    repeated = mobile_context.service.revoke_session(
        second["session_id"], reason="ignored", actor=ADMIN, request_id="admin-repeat"
    )
    assert revoked["already_revoked"] is False
    assert repeated["already_revoked"] is True
    listing = mobile_context.service.list_sessions(user_id=3, limit=100)
    assert listing["pagination"]["total"] == 2
    serialized = repr(listing)
    assert "access_token" not in serialized
    assert "refresh_token" not in serialized
    assert "hash" not in serialized


def test_role_change_invalidates_existing_mobile_session(mobile_context):
    _set_scopes(
        mobile_context, [{"scope_kind": "device", "scope_value": "camera-01"}]
    )
    _started, claimed = _pair(mobile_context)
    with sqlite3.connect(mobile_context.database_path) as connection:
        connection.execute("UPDATE users SET role='operator' WHERE id=3")
    with pytest.raises(MobileAuthenticationError):
        mobile_context.service.authenticate_access(claimed["access_token"])
    with pytest.raises(MobileAuthenticationError):
        mobile_context.service.refresh_tokens(
            claimed["refresh_token"], rate_identity="127.0.0.8",
            request_id="changed-role",
        )


def test_overview_uses_current_device_and_area_scopes_without_sensitive_fields(
    mobile_context,
):
    scoped = _set_scopes(
        mobile_context,
        [
            {"scope_kind": "device", "scope_value": "lock-01"},
            {"scope_kind": "area", "scope_value": "area-a"},
        ],
    )
    _started, claimed = _pair(mobile_context)
    response = mobile_context.client.get(
        "/api/v3/mobile/overview", headers=_bearer(claimed["access_token"])
    )
    assert response.status_code == 200
    body = response.get_json()
    assert {device["device_id"] for device in body["devices"]} == {
        "lock-01", "camera-01", "sensor-01"
    }
    assert len(body["devices"]) == 3
    by_id = {device["device_id"]: device for device in body["devices"]}
    assert by_id["lock-01"]["connection_status"] == "unknown"
    assert by_id["lock-01"]["availability_status"] == "unknown"
    assert by_id["sensor-01"]["retired"] is True
    assert by_id["sensor-01"]["availability_status"] == "retired"
    assert body["security_capability"] == {
        "available": True,
        "reason": "recorded_incident_workflow_available_gnn_unavailable",
        "gnn": {
            "available": False,
            "reason": "gnn_capability_unavailable",
        },
        "semantics": "no_recorded_incidents_is_not_a_safety_assurance",
        "unread_count": 0,
        "unacknowledged_count": 0,
        "recent_notices": [],
    }
    forbidden = {
        "mac", "identity_value", "ip_address", "peer_ip", "port",
        "graph_id", "gnn_score", "gnn_features",
    }
    assert all(not forbidden & set(device) for device in body["devices"])
    assert "192.0.2.10" not in response.get_data(as_text=True)

    with sqlite3.connect(mobile_context.database_path) as connection:
        connection.execute(
            "UPDATE v3_device_profiles SET area_id='area-b' WHERE device_id='camera-01'"
        )
    moved = mobile_context.client.get(
        "/api/v3/mobile/overview", headers=_bearer(claimed["access_token"])
    ).get_json()
    assert "camera-01" not in {item["device_id"] for item in moved["devices"]}

    emptied = _set_scopes(
        mobile_context, [], expected=scoped["scope_version"]
    )
    assert emptied["scopes"] == []
    empty_overview = mobile_context.client.get(
        "/api/v3/mobile/overview", headers=_bearer(claimed["access_token"])
    ).get_json()
    assert empty_overview["devices"] == []


def test_mobile_and_web_authentication_boundaries_and_permission_matrix(mobile_context):
    _set_scopes(
        mobile_context, [{"scope_kind": "device", "scope_value": "camera-01"}]
    )
    _started, claimed = _pair(mobile_context)
    mobile_client = mobile_context.app.test_client()
    assert mobile_client.get(
        "/api/v3/devices", headers=_bearer(claimed["access_token"])
    ).status_code == 401
    assert mobile_client.get("/api/v3/mobile/overview").status_code == 401

    _login(mobile_context.client)
    assert mobile_context.client.get("/api/v3/mobile/overview").status_code == 401
    assert mobile_context.client.get("/api/v3/mobile-sessions").status_code == 200
    _login(mobile_context.client, 2, "operator-test", "operator")
    assert mobile_context.client.get("/api/v3/mobile-sessions").status_code == 403
    _login(mobile_context.client, 3, "mobile-alice", "user")
    assert mobile_context.client.get("/api/v3/mobile-sessions").status_code == 403


def test_api_start_claim_refresh_session_logout_and_admin_revoke(mobile_context):
    _set_scopes(
        mobile_context, [{"scope_kind": "device", "scope_value": "camera-01"}]
    )
    client = mobile_context.client
    _login(client)
    token = _csrf(client)
    start = client.post(
        "/api/v3/pairing/start",
        json={"user_id": 3},
        headers={"X-CSRF-Token": token},
    )
    assert start.status_code == 201
    claim = client.post(
        "/api/v3/pairing/claim",
        json={
            "pairing_code": start.get_json()["pairing_code"],
            "client_instance_id": "api-client-0001",
            "client_display_name": "API Phone",
        },
    )
    assert claim.status_code == 201
    credentials = claim.get_json()
    session_response = client.get(
        "/api/v3/mobile/session", headers=_bearer(credentials["access_token"])
    )
    assert session_response.status_code == 200
    assert "access_token" not in session_response.get_data(as_text=True)
    refresh = client.post(
        "/api/v3/mobile/token/refresh",
        json={"refresh_token": credentials["refresh_token"]},
    )
    assert refresh.status_code == 200
    refreshed = refresh.get_json()
    logout = client.post(
        "/api/v3/mobile/logout", headers=_bearer(refreshed["access_token"])
    )
    assert logout.status_code == 200
    assert client.post(
        "/api/v3/mobile/logout", headers=_bearer(refreshed["access_token"])
    ).get_json()["already_revoked"] is True


def test_pairing_failures_share_one_public_error_and_expiry_is_enforced(
    mobile_context,
):
    _set_scopes(
        mobile_context, [{"scope_kind": "device", "scope_value": "camera-01"}]
    )
    started = mobile_context.service.start_pairing(3, actor=ADMIN, request_id="expiring")
    mobile_context.clock.advance(301)
    client = mobile_context.client
    payload = {
        "pairing_code": started["pairing_code"],
        "client_instance_id": "expired-client-01",
        "client_display_name": "Expired",
    }
    expired = client.post("/api/v3/pairing/claim", json=payload)
    payload["pairing_code"] = "AAAA-AAAA-AAAA-AAAA-AAAA-AAAA-AAAA-AAAA"
    unknown = client.post("/api/v3/pairing/claim", json=payload)
    assert expired.status_code == unknown.status_code == 401
    assert expired.get_json()["error"]["code"] == "pairing_claim_rejected"
    assert unknown.get_json()["error"]["code"] == "pairing_claim_rejected"
    assert expired.get_json()["error"]["message"] == unknown.get_json()["error"]["message"]
    assert started["pairing_code"] not in expired.get_data(as_text=True)


def test_database_rate_limits_claim_and_does_not_store_remote_identity(mobile_context):
    service = MobileAccessService(
        mobile_context.database_path,
        _settings(claim_rate_limit=1),
        clock=mobile_context.clock,
    )
    arguments = {
        "client_instance_id": "limited-client-01",
        "client_display_name": "Limited",
        "rate_identity": "198.51.100.77",
    }
    with pytest.raises(Exception) as first:
        service.claim_pairing("invalid-code", request_id="limited-1", **arguments)
    with pytest.raises(Exception) as second:
        service.claim_pairing("invalid-code", request_id="limited-2", **arguments)
    assert first.value.code == "pairing_claim_rejected"
    assert second.value.code == "mobile_rate_limited"
    with sqlite3.connect(mobile_context.database_path) as connection:
        row = connection.execute(
            "SELECT bucket_key, attempt_count, blocked_until "
            "FROM v3_mobile_rate_limits WHERE action='pairing_claim'"
        ).fetchone()
    assert row[1] == 2
    assert row[2] is not None
    assert "198.51.100.77" not in row[0]

    refresh_limited = MobileAccessService(
        mobile_context.database_path,
        _settings(refresh_rate_limit=1),
        clock=mobile_context.clock,
    )
    with pytest.raises(Exception) as refresh_first:
        refresh_limited.refresh_tokens(
            "malformed", rate_identity="198.51.100.88", request_id="refresh-limit-1"
        )
    with pytest.raises(Exception) as refresh_second:
        refresh_limited.refresh_tokens(
            "malformed", rate_identity="198.51.100.88", request_id="refresh-limit-2"
        )
    assert refresh_first.value.code == "mobile_token_invalid"
    assert refresh_second.value.code == "mobile_rate_limited"


def test_https_proxy_rules_are_explicit_and_forwarded_headers_are_not_trusted(
    mobile_context,
):
    strict = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "strict-mobile-test-secret",
            "DATABASE_PATH": str(mobile_context.database_path),
            "V3_CLOCK": mobile_context.clock,
        },
        mobile_settings=_settings(allow_insecure_http=False, trust_proxy=False),
    ).test_client()
    payload = {
        "pairing_code": "AAAA-AAAA-AAAA-AAAA-AAAA-AAAA-AAAA-AAAA",
        "client_instance_id": "https-client-0001",
        "client_display_name": "HTTPS",
    }
    plain = strict.post("/api/v3/pairing/claim", json=payload)
    forwarded = strict.post(
        "/api/v3/pairing/claim", json=payload,
        headers={"X-Forwarded-Proto": "https"},
    )
    secure = strict.post(
        "/api/v3/pairing/claim", json=payload, base_url="https://localhost"
    )
    assert plain.get_json()["error"]["code"] == "https_required"
    assert forwarded.get_json()["error"]["code"] == "https_required"
    assert secure.get_json()["error"]["code"] == "pairing_claim_rejected"

    trusted = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "trusted-mobile-test-secret",
            "DATABASE_PATH": str(mobile_context.database_path),
            "V3_CLOCK": mobile_context.clock,
        },
        mobile_settings=_settings(allow_insecure_http=False, trust_proxy=True),
    ).test_client()
    accepted_proxy = trusted.post(
        "/api/v3/pairing/claim", json=payload,
        headers={"X-Forwarded-Proto": "https"},
    )
    assert accepted_proxy.get_json()["error"]["code"] == "pairing_claim_rejected"


def test_input_limits_unknown_fields_and_secrets_are_not_echoed_or_logged(
    mobile_context, caplog,
):
    secret_code = "AAAA-BBBB-CCCC-DDDD-EEEE-FFFF-GGGG-HHHH"
    too_large = mobile_context.client.post(
        "/api/v3/pairing/claim",
        data="{\"pairing_code\":\"" + "A" * 9000 + "\"}",
        content_type="application/json",
    )
    assert too_large.status_code == 413
    invalid = mobile_context.client.post(
        "/api/v3/pairing/claim",
        json={
            "pairing_code": secret_code,
            "client_instance_id": "x" * 129,
            "client_display_name": "Phone",
            "unexpected": True,
        },
    )
    assert invalid.status_code == 400
    assert invalid.get_json()["error"]["code"] == "unknown_fields"
    too_long = mobile_context.client.post(
        "/api/v3/pairing/claim",
        json={
            "pairing_code": secret_code,
            "client_instance_id": "x" * 129,
            "client_display_name": "Phone",
        },
    )
    assert too_long.status_code == 400
    assert too_long.get_json()["error"]["code"] == "invalid_mobile_request"
    wrong_type = mobile_context.client.post(
        "/api/v3/pairing/claim", data="{}", content_type="text/plain"
    )
    assert wrong_type.status_code == 400
    assert wrong_type.get_json()["error"]["code"] == "invalid_content_type"
    output = invalid.get_data(as_text=True) + caplog.text
    assert secret_code not in output


def test_scope_and_claim_transaction_faults_leave_no_partial_rows(mobile_context):
    def fail(point):
        if point in {"replace_scopes_before_commit", "start_pairing_before_commit"}:
            raise RuntimeError("injected transaction failure")

    service = MobileAccessService(
        mobile_context.database_path, mobile_context.settings,
        clock=mobile_context.clock, fault_injector=fail,
    )
    with pytest.raises(RuntimeError):
        service.replace_scopes(
            3, [{"scope_kind": "device", "scope_value": "camera-01"}],
            expected_scope_version=0, actor=ADMIN, request_id="fault-scope",
        )
    with sqlite3.connect(mobile_context.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_mobile_user_scopes"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_mobile_security_audit"
        ).fetchone()[0] == 0

    _set_scopes(
        mobile_context, [{"scope_kind": "device", "scope_value": "camera-01"}]
    )
    with pytest.raises(RuntimeError):
        service.start_pairing(3, actor=ADMIN, request_id="fault-pair")
    with sqlite3.connect(mobile_context.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_mobile_pairings"
        ).fetchone()[0] == 0


def test_admin_session_api_revoke_is_csrf_protected_and_immediate(mobile_context):
    _set_scopes(
        mobile_context, [{"scope_kind": "device", "scope_value": "camera-01"}]
    )
    _started, claimed = _pair(mobile_context)
    client = mobile_context.client
    _login(client)
    session_id = claimed["session_id"]
    path = f"/api/v3/mobile-sessions/{session_id}/revoke"
    assert client.post(path, json={"reason": "lost"}).status_code == 403
    token = _csrf(client)
    revoked = client.post(
        path, json={"reason": "lost"}, headers={"X-CSRF-Token": token}
    )
    assert revoked.status_code == 200
    assert revoked.get_json()["already_revoked"] is False
    repeated = client.post(
        path, json={"reason": "again"}, headers={"X-CSRF-Token": token}
    )
    assert repeated.get_json()["already_revoked"] is True
    denied = client.get(
        "/api/v3/mobile/overview", headers=_bearer(claimed["access_token"])
    )
    assert denied.status_code == 401
    assert denied.get_json()["error"]["code"] == "mobile_session_revoked"


def test_missing_database_returns_503_without_creating_file(tmp_path):
    missing = tmp_path / "must-not-exist.sqlite"
    application = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "missing-database-test-secret",
            "DATABASE_PATH": str(missing),
            "V3_CLOCK": MutableClock(),
        },
        mobile_settings=_settings(),
    )
    client = application.test_client()
    _login(client)
    response = client.get("/api/v3/mobile-users/3/scopes")
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "mobile_store_unavailable"
    assert not missing.exists()


def test_production_settings_require_https_and_a_separate_secret():
    with pytest.raises(Exception):
        MobileSecuritySettings(
            token_secret="short", environment="production"
        ).validate()
    with pytest.raises(Exception):
        _settings(environment="production", allow_insecure_http=True).validate()


def test_mobile_only_user_creation_uses_random_hash_and_denies_web_login(
    mobile_context,
):
    created = mobile_context.service.create_mobile_user(
        username="resident.mobile",
        display_name="住户移动账号",
        actor=ADMIN,
        request_id="create-mobile-user",
    )
    assert created["mobile_only"] is True
    assert created["account_status"] == "active"
    assert created["profile_version"] == 1
    assert created["device_scope_count"] == 0
    assert created["area_scope_count"] == 0
    with sqlite3.connect(mobile_context.database_path) as connection:
        row = connection.execute(
            "SELECT password_hash, role FROM users WHERE id=?", (created["user_id"],)
        ).fetchone()
    assert row[1] == "user"
    assert not check_password_hash(row[0], "predictable-password")
    login = mobile_context.client.post(
        "/api/auth/login",
        json={"username": "resident.mobile", "password": "anything"},
    )
    assert login.status_code == 401
    assert login.get_json()["message"] == "该账号仅支持 APP 配对登录"


def test_mobile_user_admin_api_permissions_csrf_validation_and_duplicate(
    mobile_context,
):
    client = mobile_context.client
    assert client.get("/api/v3/mobile-users").status_code == 401
    _login(client, 2, "operator-test", "operator")
    assert client.get("/api/v3/mobile-users").status_code == 403
    _login(client, 3, "mobile-alice", "user")
    assert client.get("/api/v3/mobile-users").status_code == 403
    _login(client)
    assert client.post(
        "/api/v3/mobile-users",
        json={"username": "no-csrf", "display_name": "No CSRF"},
    ).status_code == 403
    csrf = _csrf(client)
    rejected_role = client.post(
        "/api/v3/mobile-users",
        json={
            "username": "bad-role", "display_name": "Bad",
            "role": "admin",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected_role.status_code == 400
    assert rejected_role.get_json()["error"]["code"] == "unknown_fields"
    created = client.post(
        "/api/v3/mobile-users",
        json={"username": "resident.two", "display_name": "住户二"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    user = created.get_json()
    duplicate = client.post(
        "/api/v3/mobile-users",
        json={"username": "resident.two", "display_name": "重复"},
        headers={"X-CSRF-Token": csrf},
    )
    assert duplicate.status_code == 409
    assert duplicate.get_json()["error"]["code"] == "mobile_username_conflict"
    listing = client.get(
        "/api/v3/mobile-users?search=resident.two&mobile_only=true"
        "&account_status=active&limit=10&offset=0"
    )
    assert listing.status_code == 200
    assert [item["user_id"] for item in listing.get_json()["items"]] == [
        user["user_id"]
    ]
    no_scope_pair = client.post(
        "/api/v3/pairing/start",
        json={"user_id": user["user_id"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert no_scope_pair.status_code == 409
    assert no_scope_pair.get_json()["error"]["code"] == "mobile_scope_required"


def test_profile_version_disable_restore_revokes_sessions_and_pairings(
    mobile_context,
):
    user = mobile_context.service.create_mobile_user(
        username="resident.three",
        display_name="住户三",
        actor=ADMIN,
        request_id="create-three",
    )
    _set_scopes(
        mobile_context,
        [{"scope_kind": "device", "scope_value": "camera-01"}],
        user_id=user["user_id"],
    )
    _started, claimed = _pair(
        mobile_context, user_id=user["user_id"], client_suffix="disable"
    )
    unused = mobile_context.service.start_pairing(
        user["user_id"], actor=ADMIN, request_id="unused-before-disable"
    )
    with pytest.raises(Exception) as stale:
        mobile_context.service.update_mobile_user(
            user["user_id"],
            expected_profile_version=0,
            display_name="冲突草稿",
            actor=ADMIN,
            request_id="stale-profile",
        )
    assert stale.value.code == "mobile_user_profile_version_conflict"
    disabled = mobile_context.service.update_mobile_user(
        user["user_id"],
        expected_profile_version=1,
        account_status="disabled",
        disabled_reason="设备遗失",
        actor=ADMIN,
        request_id="disable-user",
    )
    assert disabled["account_status"] == "disabled"
    assert disabled["profile_version"] == 2
    with sqlite3.connect(mobile_context.database_path) as connection:
        session = connection.execute(
            "SELECT revoked_reason FROM v3_mobile_sessions WHERE session_id=?",
            (claimed["session_id"],),
        ).fetchone()
        pairing = connection.execute(
            "SELECT invalidated_at FROM v3_mobile_pairings WHERE pairing_id=?",
            (unused["pairing_id"],),
        ).fetchone()
    assert session[0] == "account_disabled"
    assert pairing[0] is not None
    with pytest.raises(Exception) as denied:
        mobile_context.service.authenticate_access(claimed["access_token"])
    assert denied.value.code == "mobile_session_revoked"
    restored = mobile_context.service.update_mobile_user(
        user["user_id"],
        expected_profile_version=2,
        account_status="active",
        actor=ADMIN,
        request_id="restore-user",
    )
    assert restored["account_status"] == "active"
    assert restored["profile_version"] == 3
    with pytest.raises(Exception):
        mobile_context.service.authenticate_access(claimed["access_token"])
    new_pairing = mobile_context.service.start_pairing(
        user["user_id"], actor=ADMIN, request_id="pair-after-restore"
    )
    assert "pairing_code" in new_pairing


def test_mobile_user_patch_requires_csrf_and_audit_contains_no_secrets(
    mobile_context,
):
    user = mobile_context.service.create_mobile_user(
        username="resident.four",
        display_name="住户四",
        actor=ADMIN,
        request_id="create-four",
    )
    client = mobile_context.client
    _login(client)
    path = f"/api/v3/mobile-users/{user['user_id']}"
    assert client.patch(
        path,
        json={"expected_profile_version": 1, "display_name": "新名称"},
    ).status_code == 403
    csrf = _csrf(client)
    updated = client.patch(
        path,
        json={"expected_profile_version": 1, "display_name": "新名称"},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200
    assert updated.get_json()["display_name"] == "新名称"
    with sqlite3.connect(mobile_context.database_path) as connection:
        audits = connection.execute(
            "SELECT action, actor, stable_reason_code "
            "FROM v3_mobile_security_audit WHERE user_id=? ORDER BY audit_id",
            (user["user_id"],),
        ).fetchall()
    serialized = repr(audits)
    assert "password" not in serialized
    assert "token" not in serialized
    assert [row[0] for row in audits] == [
        "mobile_user_created", "mobile_user_updated"
    ]


def _device_pair(context, scope):
    _set_scopes(context, [scope])
    _started, claimed = _pair(context, client_suffix=f"device-{scope['scope_kind']}")
    return claimed


def _mobile_get(context, path, token):
    return context.client.get(path, headers=_bearer(token))


def test_mobile_device_detail_is_allowlisted_dynamic_and_scope_hidden_is_not_found(
    mobile_context,
):
    claimed = _device_pair(mobile_context, {
        "scope_kind": "device", "scope_value": "camera-01",
    })
    path = "/api/v3/mobile/devices/camera-01"
    response = _mobile_get(mobile_context, path, claimed["access_token"])
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.get_json()
    assert body["device_id"] == "camera-01"
    assert body["connection_status"] == "online"
    assert body["operation_mode"] == "active"
    assert body["last_seen_at"] == "2026-09-21T03:59:51Z"
    assert body["status_text"]["connection"] == "设备最近有连接记录"
    denied = _mobile_get(mobile_context, "/api/v3/mobile/devices/lock-01", claimed["access_token"])
    missing = _mobile_get(mobile_context, "/api/v3/mobile/devices/not-real", claimed["access_token"])
    assert denied.status_code == missing.status_code == 404
    assert denied.get_json()["error"]["code"] == missing.get_json()["error"]["code"] == "mobile_device_unavailable"
    assert denied.get_json()["error"]["message"] == missing.get_json()["error"]["message"]
    assert denied.get_json()["error"]["request_id"]
    forbidden_fields = {
        "mac", "identity_kind", "identity_value", "ip_address", "last_ip",
        "peer_ip", "peer_device_id", "port", "graph_id", "gnn_score",
        "state_version", "profile_version", "admin_summary", "internal_reason",
    }
    assert forbidden_fields.isdisjoint(body)
    assert "gnn" in body["security_capability"]
    assert body["security_capability"]["gnn"] == {
        "available": False, "reason": "gnn_capability_unavailable",
    }
    assert "mac" not in repr(body).lower()
    assert "192.0.2." not in repr(body)


def test_mobile_device_area_scope_is_dynamic_and_revocation_takes_effect_immediately(
    mobile_context,
):
    claimed = _device_pair(mobile_context, {
        "scope_kind": "area", "scope_value": "area-a",
    })
    path = "/api/v3/mobile/devices/camera-01"
    assert _mobile_get(mobile_context, path, claimed["access_token"]).status_code == 200
    with sqlite3.connect(mobile_context.database_path) as connection:
        connection.execute(
            "UPDATE v3_device_profiles SET area_id='area-moved' WHERE device_id='camera-01'"
        )
    moved = _mobile_get(mobile_context, path, claimed["access_token"])
    assert moved.status_code == 404
    _set_scopes(mobile_context, [], expected=1)
    revoked = _mobile_get(mobile_context, path, claimed["access_token"])
    assert revoked.status_code == 404
    assert revoked.get_json()["error"]["code"] == "mobile_device_unavailable"


def test_mobile_device_details_never_grant_global_admin_or_cookie_access(mobile_context):
    claimed = _device_pair(mobile_context, {
        "scope_kind": "device", "scope_value": "camera-01",
    })
    token = claimed["access_token"]
    for path in (
        "/api/v3/devices/camera-01",
        "/api/v3/devices/camera-01/traffic",
        "/api/v3/devices/camera-01/peers",
        "/api/v3/monitor",
    ):
        response = _mobile_get(mobile_context, path, token)
        assert response.status_code == 401, path
    cookie_only = mobile_context.app.test_client()
    _login(cookie_only)
    response = cookie_only.get("/api/v3/mobile/devices/camera-01")
    assert response.status_code == 401


def test_mobile_device_notice_summary_includes_only_current_visible_affected_incident(
    mobile_context,
):
    claimed = _device_pair(mobile_context, {
        "scope_kind": "device", "scope_value": "camera-01",
    })
    incident = mobile_context.app.extensions["iot_ids_incident_workflow"]
    created = incident.create_incident(
        incident_type="device_anomaly", severity="high", source="manual",
        admin_title="管理端标题不得返回", admin_summary="管理端详情不得返回",
        user_title="设备需要留意", user_summary="管理员正在核查设备情况。",
        devices=[
            {"device_id": "camera-01", "incident_role": "affected", "user_visible": True},
            {"device_id": "lock-01", "incident_role": "suspected_source", "user_visible": False},
        ],
        publish_to_mobile=True, first_seen_at=mobile_context.clock.value,
        actor=IncidentActor(1, "admin-test", "admin"), request_id="mobile-detail-incident",
    )
    response = _mobile_get(
        mobile_context, "/api/v3/mobile/devices/camera-01", claimed["access_token"]
    )
    assert response.status_code == 200
    summary = response.get_json()["security_capability"]
    assert summary["available"] is True
    assert summary["active_notice_count"] == 1
    assert summary["recent_notices"] == [{
        "incident_id": created["incident_id"], "user_title": "设备需要留意",
        "severity": "high", "status": "open",
        "updated_at": created["updated_at"], "read": False, "acknowledged": False,
    }]
    serialized = repr(response.get_json()).lower()
    assert "管理端标题" not in serialized
    assert "lock-01" not in serialized
    assert "admin_summary" not in serialized


def test_mobile_traffic_has_fixed_windows_no_samples_true_zero_stale_and_no_peer_fields(
    mobile_context,
):
    claimed = _device_pair(mobile_context, {
        "scope_kind": "device", "scope_value": "camera-01",
    })
    base = "/api/v3/mobile/devices/camera-01/traffic"
    empty = _mobile_get(mobile_context, base + "?window=15m", claimed["access_token"])
    assert empty.status_code == 200
    empty_body = empty.get_json()
    assert empty_body["availability"] == {
        "status": "no_samples", "available": False, "reason": "no_samples",
    }
    assert empty_body["summary"] is None
    assert empty_body["current_rate"]["status"] == "warming_up"
    assert empty_body["current_rate"]["uploaded_bytes_per_second"] is None
    for window, duration in (("15m", 900), ("1h", 3600), ("24h", 86400)):
        response = _mobile_get(mobile_context, f"{base}?window={window}", claimed["access_token"])
        assert response.status_code == 200
        bounds = response.get_json()["query_window"]
        assert (datetime.fromisoformat(bounds["to"].replace("Z", "+00:00")) -
                datetime.fromisoformat(bounds["from"].replace("Z", "+00:00"))).total_seconds() == duration
    invalid = _mobile_get(mobile_context, base + "?window=7d", claimed["access_token"])
    assert invalid.status_code == 400
    assert invalid.get_json()["error"]["code"] == "invalid_mobile_traffic_window"
    extra = _mobile_get(mobile_context, base + "?window=15m&peer=all", claimed["access_token"])
    assert extra.status_code == 400

    sample_at = mobile_context.clock.value - timedelta(minutes=3)
    bucket = sample_at.replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z")
    occurred = sample_at.isoformat().replace("+00:00", "Z")
    with sqlite3.connect(mobile_context.database_path) as connection:
        connection.execute(
            "INSERT INTO v3_device_traffic_minutes "
            "(device_id,bucket_start,tx_bytes,rx_bytes,tx_packets,rx_packets,tx_flow_count,"
            "rx_flow_count,first_sample_at,last_sample_at,updated_at) "
            "VALUES (?,?,0,512,1,1,0,0,?,?,?)",
            ("camera-01", bucket, occurred, occurred, occurred),
        )
        connection.executemany(
            "INSERT INTO v3_device_traffic_protocol_minutes "
            "(device_id,bucket_start,direction,protocol,bytes,packets,flow_count,"
            "first_sample_at,last_sample_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                ("camera-01", bucket, "tx", "TCP", 0, 1, 0, occurred, occurred, occurred),
                ("camera-01", bucket, "rx", "UDP", 512, 1, 0, occurred, occurred, occurred),
            ],
        )
    service = get_service_container(mobile_context.app).get_traffic_service()
    zero = TrafficSample(
        source_id="test-zero", sample_id="zero-sample", occurred_at=mobile_context.clock.value,
        received_at=mobile_context.clock.value, src_ip="192.0.2.10", dst_ip="203.0.113.7",
        network_protocol="TCP", application_protocol=None, application_protocol_inferred=False,
        src_port=None, dst_port=None, bytes=0, packets=1, flow_count=0,
    )
    service.realtime_window.add("camera-01", "tx", zero)
    populated = _mobile_get(mobile_context, base + "?window=15m", claimed["access_token"])
    assert populated.status_code == 200
    body = populated.get_json()
    assert body["availability"]["available"] is True
    assert body["freshness"]["status"] == "stale"
    assert body["summary"]["uploaded_bytes"] == 0
    assert body["summary"]["downloaded_bytes"] == 512
    assert body["current_rate"]["status"] == "available"
    assert body["current_rate"]["uploaded_bytes_per_second"] == 0
    assert {item["category"] for item in body["protocols"]} == {"tcp", "udp"}
    assert "peer" not in repr(body).lower()
    assert "192.0.2." not in repr(body)
    assert "203.0.113." not in repr(body)


def test_mobile_device_read_rate_limit_and_missing_database_fail_closed(
    mobile_context, tmp_path,
):
    claimed = _device_pair(mobile_context, {
        "scope_kind": "device", "scope_value": "camera-01",
    })
    principal = mobile_context.service.authenticate_access(claimed["access_token"])
    for _ in range(120):
        mobile_context.service.consume_scoped_read_limit(
            principal, action="mobile_device_detail", limit=120,
        )
    limited = _mobile_get(
        mobile_context, "/api/v3/mobile/devices/camera-01", claimed["access_token"]
    )
    assert limited.status_code == 429
    assert limited.get_json()["error"]["code"] == "mobile_rate_limited"

    missing = tmp_path / "mobile-device-missing.sqlite"
    app = create_app({
        "TESTING": True, "SECRET_KEY": "mobile-device-missing-secret",
        "DATABASE_PATH": str(missing), "V3_CLOCK": mobile_context.clock,
    }, mobile_settings=mobile_context.settings)
    client = app.test_client()
    for suffix in ("", "/traffic"):
        response = client.get(
            f"/api/v3/mobile/devices/camera-01{suffix}",
            headers=_bearer(claimed["access_token"]),
        )
        assert response.status_code == 503
        assert response.get_json()["error"]["request_id"]
    assert not missing.exists()
    assert [item.version for item in V3_MIGRATIONS] == list(range(1, 10))
