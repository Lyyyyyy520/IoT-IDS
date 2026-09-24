from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import sqlite3
from types import SimpleNamespace

import pytest

from app import create_app
from config import MobileSecuritySettings
from database import init_db
from services.device_management import (
    DeviceActor,
    DeviceHasHistoryError,
    DeviceManagementService,
)
from services.incident_workflow import (
    HelpRequestVersionConflict,
    IncidentActor,
    IncidentSourceForbidden,
    IncidentTransitionConflict,
    IncidentVersionConflict,
    IncidentWorkflowError,
    IncidentWorkflowService,
    MobileResourceUnavailable,
    SupportVersionConflict,
)
from services.mobile_access import MobileActor
from v3_db_maintenance import RETENTION_DEFAULTS, RetentionSettings, apply_retention
from v3_database import (
    V3_INCIDENT_WORKFLOW_INDEXES,
    V3_INCIDENT_WORKFLOW_MIGRATION,
    V3_INCIDENT_WORKFLOW_TABLES,
    V3_MIGRATIONS,
    apply_v3_migrations,
    connect_v3,
    initialize_v3_database,
)


NOW = datetime(2026, 9, 23, 2, 0, tzinfo=timezone.utc)
FROZEN_V1_TO_V7 = [
    "3fe72003fd5eb35063bd5aea3f677bc66ef0aaaa36cded58a83f46738bd26952",
    "77ce4e371366c7d3e53640debb212c9a736fd5e9f8849b06e6d3700508d48078",
    "bfe9842f09d391284b408dd0df36e205e4ad9f92361ee304dd52955c9f6aa329",
    "685caf41c5d21471c14ef7b6608cce6d43a4cd69fff3961c1888c3166a4bebcd",
    "77e7011d94ef2b3a7022013fbef0c2e68f70f1ca9ead58446dd3d4bde78c74c6",
    "f1ce25c5393381750c7582eb7dc783c7625db80a0dad483c1e46cf1b7521b61d",
    "5e8e572496607b58d0ccf93be0bcd1deaaa7d3935f93cef54cccd35e905b3623",
]


@dataclass
class Clock:
    value: datetime = NOW

    def __call__(self):
        return self.value

    def advance(self, seconds: int):
        self.value += timedelta(seconds=seconds)


def _settings():
    return MobileSecuritySettings(
        token_secret="incident-test-secret-0123456789-abcdefghijklmnopqrstuvwxyz",
        environment="testing",
        allow_insecure_http=True,
        pairing_ttl_seconds=300,
        access_ttl_seconds=300,
        refresh_ttl_seconds=3600,
        pairing_max_attempts=3,
        rate_window_seconds=60,
        rate_block_seconds=60,
        claim_rate_limit=20,
        refresh_rate_limit=20,
    )


def _fixture_rows(database_path):
    with sqlite3.connect(database_path) as connection:
        connection.executemany(
            "INSERT INTO users (id,username,password_hash,role) "
            "VALUES (?,?,'x',?)",
            [
                (1, "admin-test", "admin"),
                (2, "operator-test", "operator"),
                (3, "mobile-alice", "user"),
                (4, "mobile-bob", "user"),
            ],
        )
        connection.executemany(
            "INSERT INTO v3_device_profiles "
            "(device_id,identity_kind,identity_value,display_name,"
            "device_type,area_id,operation_mode,created_at,updated_at,"
            "importance,profile_source,profile_version,retired_at,"
            "retirement_reason) VALUES "
            "(?,'mac',?,?,?,?, 'active',?,?,'normal','physical',1,NULL,NULL)",
            [
                (
                    "camera-01", "AA:BB:CC:DD:EE:01",
                    "门厅摄像机", "camera", "area-a",
                    "2026-09-20T00:00:00Z", "2026-09-23T01:59:00Z",
                ),
                (
                    "lock-01", "AA:BB:CC:DD:EE:02",
                    "后门门锁", "lock", "area-b",
                    "2026-09-20T00:00:00Z", "2026-09-23T01:59:00Z",
                ),
                (
                    "sensor-01", "AA:BB:CC:DD:EE:03",
                    "客厅传感器", "sensor", "area-a",
                    "2026-09-20T00:00:00Z", "2026-09-23T01:59:00Z",
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO v3_device_current_state "
            "(device_id,connection_status,ip_address,last_observed_at,"
            "last_received_at,state_version,updated_at) "
            "VALUES (?,'unknown',NULL,NULL,NULL,0,?)",
            [
                ("camera-01", "2026-09-23T01:59:00Z"),
                ("lock-01", "2026-09-23T01:59:00Z"),
                ("sensor-01", "2026-09-23T01:59:00Z"),
            ],
        )


@pytest.fixture
def context(tmp_path):
    database_path = tmp_path / "incidents.sqlite"
    init_db(database_path)
    initialize_v3_database(database_path)
    _fixture_rows(database_path)
    clock = Clock()
    settings = _settings()
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "incident-web-csrf-test-secret",
            "DATABASE_PATH": str(database_path),
            "V3_CLOCK": clock,
        },
        mobile_settings=settings,
    )
    mobile = app.extensions["iot_ids_mobile_access"]
    incident = app.extensions["iot_ids_incident_workflow"]
    mobile.replace_scopes(
        3,
        [{"scope_kind": "device", "scope_value": "camera-01"}],
        expected_scope_version=0,
        actor=MobileActor(1, "admin-test", "admin"),
        request_id="scope-alice-1",
    )
    mobile.replace_scopes(
        4,
        [{"scope_kind": "area", "scope_value": "area-b"}],
        expected_scope_version=0,
        actor=MobileActor(1, "admin-test", "admin"),
        request_id="scope-bob-1",
    )
    pairing = mobile.start_pairing(
        3,
        actor=MobileActor(1, "admin-test", "admin"),
        request_id="pair-alice",
    )
    tokens = mobile.claim_pairing(
        pairing["pairing_code"],
        client_instance_id="client-alice-1234",
        client_display_name="Alice Phone",
        rate_identity="127.0.0.1",
        request_id="claim-alice",
    )
    principal = mobile.authenticate_access(tokens["access_token"])
    return SimpleNamespace(
        database_path=database_path,
        clock=clock,
        settings=settings,
        app=app,
        client=app.test_client(),
        mobile=mobile,
        incident=incident,
        tokens=tokens,
        principal=principal,
    )


ADMIN = IncidentActor(1, "admin-test", "admin")
OPERATOR = IncidentActor(2, "operator-test", "operator")
SYSTEM = IncidentActor(None, "trusted-rule-engine", "system")


def _create(service, *, source="manual", actor=ADMIN, devices=None):
    return service.create_incident(
        incident_type="device_anomaly",
        severity="high",
        source=source,
        admin_title="设备通信异常",
        admin_summary="管理端证据摘要，仅管理员可见。",
        user_title="设备需要关注",
        user_summary="管理员正在核查设备状态，请留意后续进度。",
        devices=devices or [
            {
                "device_id": "camera-01",
                "incident_role": "affected",
                "user_visible": True,
            },
            {
                "device_id": "lock-01",
                "incident_role": "suspected_source",
                "user_visible": False,
            },
        ],
        publish_to_mobile=True,
        first_seen_at=NOW,
        actor=actor,
        request_id="incident-create",
    )


def _login(client, role="admin", user_id=1):
    with client.session_transaction() as state:
        state["user_id"] = user_id
        state["username"] = f"{role}-test"
        state["role"] = role


def _csrf(client):
    response = client.get("/api/v3/incidents")
    assert response.status_code == 200
    return response.headers["X-CSRF-Token"]


def _bearer(context):
    return {"Authorization": f"Bearer {context.tokens['access_token']}"}


def test_v8_migration_is_additive_idempotent_and_freezes_v1_to_v7(tmp_path):
    assert [item.checksum for item in V3_MIGRATIONS[:7]] == FROZEN_V1_TO_V7
    assert V3_INCIDENT_WORKFLOW_MIGRATION.checksum == (
        "b87d02359023eafef439bbf04ce0f9c4929e73bd03a08f3dff955c7f1306d3ac"
    )
    database_path = tmp_path / "upgrade.sqlite"
    connection = connect_v3(database_path)
    try:
        first = apply_v3_migrations(connection, V3_MIGRATIONS[:7])
        upgrade = apply_v3_migrations(connection, V3_MIGRATIONS[:8])
        repeated = apply_v3_migrations(connection, V3_MIGRATIONS[:8])
        objects = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type IN ('table','index')"
            )
        }
    finally:
        connection.close()
    assert first["schema_version"] == 7
    assert upgrade["applied_versions"] == [8]
    assert repeated["applied_versions"] == []
    assert repeated["skipped_versions"] == list(range(1, 9))
    assert V3_INCIDENT_WORKFLOW_TABLES <= objects
    assert V3_INCIDENT_WORKFLOW_INDEXES <= objects


def test_sources_roles_state_machine_version_and_atomic_events(context):
    manual = _create(context.incident)
    assert manual["status"] == "open"
    assert manual["incident_version"] == 1
    assert {item["incident_role"] for item in manual["devices"]} == {
        "affected", "suspected_source",
    }
    assert next(
        item for item in manual["devices"]
        if item["incident_role"] == "suspected_source"
    )["user_visible"] is False

    for source in ("rule", "system"):
        created = _create(
            context.incident, source=source, actor=SYSTEM,
            devices=[{
                "device_id": "sensor-01",
                "incident_role": "affected",
                "user_visible": True,
            }],
        )
        assert created["source"] == source
    with pytest.raises(IncidentWorkflowError):
        _create(context.incident, source="gnn")
    with pytest.raises(IncidentSourceForbidden):
        _create(context.incident, source="rule", actor=ADMIN)
    with pytest.raises(IncidentTransitionConflict):
        context.incident.transition_incident(
            manual["incident_id"], target_status="recovering",
            expected_incident_version=1,
            public_progress="正在恢复设备服务。",
            actor=OPERATOR, request_id="bad-transition",
        )

    acknowledged = context.incident.transition_incident(
        manual["incident_id"], target_status="acknowledged",
        expected_incident_version=1,
        public_progress="管理员已收到提醒并开始核查。",
        actor=OPERATOR, request_id="ack-1",
    )
    assert acknowledged["incident_version"] == 2
    with pytest.raises(IncidentVersionConflict):
        context.incident.transition_incident(
            manual["incident_id"], target_status="recovering",
            expected_incident_version=1,
            public_progress="正在恢复设备服务。",
            actor=OPERATOR, request_id="stale",
        )
    recovering = context.incident.transition_incident(
        manual["incident_id"], target_status="recovering",
        expected_incident_version=2,
        public_progress="管理员正在恢复设备服务。",
        admin_details="隔离环境内完成人工检查。",
        actor=OPERATOR, request_id="recovering",
    )
    resolved = context.incident.transition_incident(
        manual["incident_id"], target_status="resolved",
        expected_incident_version=3,
        public_progress="管理员已完成处理。",
        resolution_summary="已完成核查和恢复。",
        actor=OPERATOR, request_id="resolved",
    )
    assert recovering["status"] == "recovering"
    assert resolved["status"] == "resolved"
    with pytest.raises(IncidentTransitionConflict):
        context.incident.transition_incident(
            manual["incident_id"], target_status="false_positive",
            expected_incident_version=4,
            public_progress="提醒已结束。",
            false_positive_reason="复核后无需处置。",
            actor=OPERATOR, request_id="terminal",
        )
    with sqlite3.connect(context.database_path) as connection:
        connection.row_factory = sqlite3.Row
        events = connection.execute(
            "SELECT event_type,state_version,payload_json "
            "FROM v3_realtime_events WHERE event_type LIKE 'incident.%' "
            "ORDER BY event_id"
        ).fetchall()
        timeline = connection.execute(
            "SELECT COUNT(*) FROM v3_incident_timeline "
            "WHERE incident_id=?", (manual["incident_id"],)
        ).fetchone()[0]
    assert [row["event_type"] for row in events[:4]] == [
        "incident.opened", "incident.opened",
        "incident.opened", "incident.updated",
    ]
    assert events[-2]["event_type"] == "incident.recovering"
    assert events[-1]["event_type"] == "incident.resolved"
    assert timeline == 4


def test_transition_fault_rolls_back_incident_timeline_audit_and_sse(context):
    incident = _create(context.incident)
    failing = IncidentWorkflowService(
        context.database_path, context.settings,
        clock=context.clock,
        fault_injector=lambda point: (
            (_ for _ in ()).throw(RuntimeError("injected"))
            if point == "incident_transition_before_commit"
            else None
        ),
    )
    with sqlite3.connect(context.database_path) as connection:
        before = tuple(connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM v3_incident_timeline),"
            "(SELECT COUNT(*) FROM v3_incident_workflow_audit),"
            "(SELECT COUNT(*) FROM v3_realtime_events)"
        ).fetchone())
    with pytest.raises(RuntimeError, match="injected"):
        failing.transition_incident(
            incident["incident_id"], target_status="acknowledged",
            expected_incident_version=1,
            actor=OPERATOR, request_id="fault",
        )
    with sqlite3.connect(context.database_path) as connection:
        after = tuple(connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM v3_incident_timeline),"
            "(SELECT COUNT(*) FROM v3_incident_workflow_audit),"
            "(SELECT COUNT(*) FROM v3_realtime_events)"
        ).fetchone())
        row = connection.execute(
            "SELECT status,incident_version FROM v3_incidents "
            "WHERE incident_id=?", (incident["incident_id"],)
        ).fetchone()
    assert after == before
    assert row == ("open", 1)


def test_mobile_scope_redaction_read_ack_and_scope_revocation(context):
    incident = _create(context.incident, devices=[
        {
            "device_id": "camera-01",
            "incident_role": "affected",
            "user_visible": True,
        },
        {
            "device_id": "sensor-01",
            "incident_role": "affected",
            "user_visible": True,
        },
        {
            "device_id": "lock-01",
            "incident_role": "suspected_source",
            "user_visible": False,
        },
    ])
    snapshot = context.incident.list_mobile_notices(
        context.principal, view="active"
    )
    assert snapshot["snapshot_required"] is False
    assert len(snapshot["notices"]) == 1
    notice = snapshot["notices"][0]
    assert notice["incident_id"] == incident["incident_id"]
    assert notice["affected_devices"] == [{
        "device_id": "camera-01",
        "display_name": "门厅摄像机",
        "device_type": "camera",
        "area_id": "area-a",
    }]
    serialized = repr(notice).lower()
    for forbidden in (
        "admin_title", "admin_summary", "admin_details",
        "suspected_source", "mac", "ip_address", "graph_id",
        "gnn", "model_version", "port",
    ):
        assert forbidden not in serialized

    read_once = context.incident.mark_notice(
        context.principal, incident["incident_id"],
        acknowledged=False, request_id="read-1",
    )
    read_twice = context.incident.mark_notice(
        context.principal, incident["incident_id"],
        acknowledged=False, request_id="read-2",
    )
    acknowledged = context.incident.mark_notice(
        context.principal, incident["incident_id"],
        acknowledged=True, request_id="ack-user-1",
    )
    context.incident.mark_notice(
        context.principal, incident["incident_id"],
        acknowledged=True, request_id="ack-user-2",
    )
    assert read_once["read"] is True
    assert read_twice["first_read_at"] == read_once["first_read_at"]
    assert acknowledged["acknowledged"] is True
    assert context.incident.get_incident(
        incident["incident_id"]
    )["status"] == "open"
    with sqlite3.connect(context.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_mobile_notice_acknowledgements "
            "WHERE incident_id=? AND user_id=3",
            (incident["incident_id"],),
        ).fetchone()[0] == 1

    context.mobile.replace_scopes(
        3, [], expected_scope_version=1,
        actor=MobileActor(1, "admin-test", "admin"),
        request_id="scope-revoked",
    )
    delta = context.incident.list_mobile_notices(
        context.principal, after=snapshot["next_cursor"]
    )
    assert delta["snapshot_required"] is True
    assert context.incident.list_mobile_notices(
        context.principal
    )["notices"] == []
    with pytest.raises(MobileResourceUnavailable):
        context.incident.get_mobile_notice(
            context.principal, incident["incident_id"]
        )


def test_area_scope_is_dynamic_and_suspected_source_never_expands_scope(context):
    incident = _create(context.incident)
    bob_pair = context.mobile.start_pairing(
        4, actor=MobileActor(1, "admin-test", "admin"),
        request_id="pair-bob",
    )
    bob_tokens = context.mobile.claim_pairing(
        bob_pair["pairing_code"],
        client_instance_id="client-bob-12345",
        client_display_name="Bob Phone",
        rate_identity="127.0.0.2",
        request_id="claim-bob",
    )
    bob = context.mobile.authenticate_access(
        bob_tokens["access_token"]
    )
    assert context.incident.list_mobile_notices(bob)["notices"] == []
    with sqlite3.connect(context.database_path) as connection:
        connection.execute(
            "UPDATE v3_device_profiles SET area_id='area-b' "
            "WHERE device_id='camera-01'"
        )
    assert context.incident.list_mobile_notices(bob)["notices"][0][
        "incident_id"
    ] == incident["incident_id"]


def test_delta_cursor_resolution_false_positive_and_snapshot_required(context):
    first = _create(context.incident)
    baseline = context.incident.list_mobile_notices(
        context.principal, view="all"
    )
    context.incident.transition_incident(
        first["incident_id"], target_status="acknowledged",
        expected_incident_version=1,
        public_progress="管理员已开始核查。",
        actor=OPERATOR, request_id="fp-ack",
    )
    context.incident.transition_incident(
        first["incident_id"], target_status="false_positive",
        expected_incident_version=2,
        public_progress="该提醒已结束，无需进一步操作。",
        false_positive_reason="复核后未发现需处置事项。",
        actor=OPERATOR, request_id="fp-end",
    )
    delta = context.incident.list_mobile_notices(
        context.principal, after=baseline["next_cursor"],
        view="active",
    )
    assert delta["notices"] == []
    assert delta["tombstones"][-1]["reason"] == "false_positive"
    history = context.incident.list_mobile_notices(
        context.principal, view="history"
    )
    assert history["notices"][0]["status"] == "false_positive"
    assert history["notices"][0]["public_progress"] == (
        "该提醒已结束，无需进一步操作。"
    )
    with pytest.raises(IncidentWorkflowError) as error:
        context.incident.list_mobile_notices(
            context.principal, after="not-a-cursor"
        )
    assert error.value.code == "invalid_notice_cursor"

    cursor = context.incident.list_mobile_notices(
        context.principal
    )["next_cursor"]
    for _index in range(3):
        _create(context.incident)
    overflow = context.incident.list_mobile_notices(
        context.principal, after=cursor, limit=2
    )
    assert overflow["snapshot_required"] is True


def test_support_contact_public_boundary_and_version_conflict(context):
    assert context.incident.get_support_contact(mobile=True) == {
        "available": False,
        "reason": "support_contact_not_configured",
    }
    configured = context.incident.put_support_contact(
        display_name="社区安全值班",
        phone="+86 010-5555-0101",
        email="support@example.test",
        working_hours="工作日 09:00-18:00",
        public_note="紧急情况请优先联系值班人员。",
        enabled=True,
        expected_config_version=0,
        actor=ADMIN,
        request_id="support-1",
    )
    assert configured["config_version"] == 1
    mobile = context.incident.get_support_contact(mobile=True)
    assert mobile["available"] is True
    assert mobile["email"] == "support@example.test"
    assert "updated_by" not in mobile
    assert "enabled" not in mobile
    with pytest.raises(SupportVersionConflict):
        context.incident.put_support_contact(
            display_name="社区安全值班",
            phone=None, email=None, working_hours=None,
            public_note=None, enabled=True,
            expected_config_version=0,
            actor=ADMIN, request_id="support-stale",
        )
    with pytest.raises(IncidentWorkflowError):
        context.incident.put_support_contact(
            display_name="社区安全值班",
            phone="not a phone", email=None,
            working_hours=None, public_note=None, enabled=True,
            expected_config_version=1,
            actor=ADMIN, request_id="support-invalid",
        )


def test_help_request_scope_idempotency_ownership_and_progress(context):
    incident = _create(context.incident)
    created = context.incident.create_help_request(
        context.principal,
        incident_id=incident["incident_id"],
        device_id="camera-01",
        category="security_question",
        user_message="请问需要我暂时停止使用这个设备吗？",
        idempotency_key="help-key-0001",
        request_id="help-create",
    )
    replay = context.incident.create_help_request(
        context.principal,
        incident_id=incident["incident_id"],
        device_id="camera-01",
        category="security_question",
        user_message="请问需要我暂时停止使用这个设备吗？",
        idempotency_key="help-key-0001",
        request_id="help-replay",
    )
    assert created["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert replay["help_request_id"] == created["help_request_id"]
    with pytest.raises(IncidentWorkflowError) as conflict:
        context.incident.create_help_request(
            context.principal,
            incident_id=incident["incident_id"],
            device_id="camera-01",
            category="other",
            user_message="不同内容",
            idempotency_key="help-key-0001",
            request_id="help-conflict",
        )
    assert conflict.value.code == "idempotency_conflict"
    with pytest.raises(MobileResourceUnavailable):
        context.incident.create_help_request(
            context.principal,
            incident_id=None, device_id="lock-01",
            category="device_issue", user_message="门锁需要协助。",
            idempotency_key="help-key-0002",
            request_id="help-out-of-scope",
        )

    updated = context.incident.update_help_request(
        created["help_request_id"],
        expected_request_version=1,
        status="in_progress",
        public_response="管理员已收到请求，正在处理。",
        internal_note="分配给值班 operator。",
        assigned_to=2,
        actor=OPERATOR,
        request_id="help-progress",
    )
    assert updated["request_version"] == 2
    mobile = context.incident.get_mobile_help_request(
        context.principal, created["help_request_id"]
    )
    assert mobile["public_response"] == "管理员已收到请求，正在处理。"
    assert "internal_note" not in mobile
    assert "assigned_to" not in mobile
    with pytest.raises(HelpRequestVersionConflict):
        context.incident.update_help_request(
            created["help_request_id"],
            expected_request_version=1,
            status="closed",
            public_response="已处理。",
            internal_note=None, assigned_to=2,
            actor=OPERATOR, request_id="help-stale",
        )

    bob_pair = context.mobile.start_pairing(
        4, actor=MobileActor(1, "admin-test", "admin"),
        request_id="pair-help-bob",
    )
    bob_tokens = context.mobile.claim_pairing(
        bob_pair["pairing_code"],
        client_instance_id="client-help-bob",
        client_display_name="Bob Phone",
        rate_identity="127.0.0.4",
        request_id="claim-help-bob",
    )
    bob = context.mobile.authenticate_access(
        bob_tokens["access_token"]
    )
    with pytest.raises(MobileResourceUnavailable):
        context.incident.get_mobile_help_request(
            bob, created["help_request_id"]
        )
    with sqlite3.connect(context.database_path) as connection:
        assert connection.execute(
            "SELECT attempt_count FROM v3_mobile_rate_limits "
            "WHERE action='mobile_help_failed_query'"
        ).fetchone() == (1,)


def test_monitor_overview_and_device_delete_references(context):
    incident = _create(context.incident)
    _login(context.client)
    monitor = context.client.get("/api/v3/monitor")
    assert monitor.status_code == 200
    body = monitor.get_json()
    assert body["capabilities"]["incident"]["available"] is True
    assert body["capabilities"]["graph"] == {
        "available": False,
        "reason": "graph_snapshots_not_implemented",
    }
    assert body["incidents"]["active"][0]["incident_id"] == (
        incident["incident_id"]
    )
    assert body["incidents"]["empty_meaning"] == (
        "no_recorded_incidents_not_proven_safe"
    )
    overview = context.client.get(
        "/api/v3/mobile/overview", headers=_bearer(context)
    ).get_json()
    capability = overview["security_capability"]
    assert capability["available"] is True
    assert capability["gnn"]["available"] is False
    assert capability["unread_count"] == 1
    assert "security_status" not in capability
    assert capability["semantics"] == (
        "no_recorded_incidents_is_not_a_safety_assurance"
    )

    manager = DeviceManagementService(
        context.database_path, clock=context.clock
    )
    detail = manager.get_device("camera-01")
    assert detail["can_delete"] is False
    assert detail["references"]["incident_devices"] == 1
    with pytest.raises(DeviceHasHistoryError):
        manager.delete_device(
            "camera-01", confirmation="门厅摄像机",
            actor=DeviceActor(1, "admin-test", "admin"),
            request_id="delete-with-incident",
        )


def test_admin_api_permissions_csrf_and_mobile_auth_boundary(context):
    assert context.client.get("/api/v3/incidents").status_code == 401
    _login(context.client, "user", 3)
    assert context.client.get("/api/v3/incidents").status_code == 403
    _login(context.client, "operator", 2)
    assert context.client.get("/api/v3/incidents").status_code == 200
    csrf = _csrf(context.client)
    payload = {
        "incident_type": "device_anomaly",
        "severity": "medium",
        "source": "manual",
        "admin_title": "管理员标题",
        "admin_summary": "管理员摘要",
        "user_title": "设备提醒",
        "user_summary": "管理员正在核查，请留意后续进度。",
        "public_progress": "管理员正在开始核查设备服务。",
        "publish_to_mobile": True,
        "devices": [{
            "device_id": "camera-01",
            "incident_role": "affected",
            "user_visible": True,
        }],
    }
    assert context.client.post(
        "/api/v3/incidents", json=payload,
        headers={"X-CSRF-Token": csrf},
    ).status_code == 403
    _login(context.client, "admin", 1)
    assert context.client.post(
        "/api/v3/incidents", json=payload
    ).status_code == 403
    csrf = _csrf(context.client)
    created = context.client.post(
        "/api/v3/incidents", json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    incident_id = created.get_json()["incident_id"]
    assert created.get_json()["user_preview"]["public_progress"] == (
        "管理员正在开始核查设备服务。"
    )
    assert context.client.post(
        f"/api/v3/incidents/{incident_id}/ack",
        json={"expected_incident_version": 1},
        headers={"X-CSRF-Token": csrf},
    ).status_code == 200
    rejected = context.client.post(
        "/api/v3/incidents", json=dict(payload, source="gnn"),
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected.status_code in {400, 403}
    mobile_only = context.app.test_client()
    assert mobile_only.get(
        "/api/v3/incidents", headers=_bearer(context)
    ).status_code == 401
    cookie_only = context.app.test_client()
    _login(cookie_only, "admin", 1)
    assert cookie_only.get(
        "/api/v3/mobile/notices"
    ).status_code == 401


def test_admin_incident_list_search_progress_counts_and_event_cursor(context):
    incident = _create(context.incident)
    context.incident.transition_incident(
        incident["incident_id"], target_status="acknowledged",
        expected_incident_version=1,
        public_progress="管理员已收到提醒并开始核查。",
        actor=OPERATOR, request_id="list-progress",
    )
    _login(context.client)
    response = context.client.get(
        "/api/v3/incidents?search=%E9%80%9A%E4%BF%A1&status=acknowledged"
        "&limit=10&offset=0"
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["event_cursor"] >= 2
    assert body["total"] == 1
    assert body["items"][0]["affected_device_count"] == 1
    assert body["items"][0]["latest_public_progress"] == (
        "管理员已收到提醒并开始核查。"
    )
    assert body["items"][0]["incident_id"] == incident["incident_id"]


def test_help_admin_filters_and_detail_do_not_expose_mobile_session_or_idempotency(context):
    incident = _create(context.incident)
    help_item = context.incident.create_help_request(
        context.principal,
        incident_id=incident["incident_id"],
        device_id="camera-01",
        category="device_issue",
        user_message="请协助确认设备使用安排。",
        idempotency_key="filtered-help-key-001",
        request_id="filtered-help-create",
    )
    filtered = context.incident.list_help_requests(
        status="open", category="device_issue", user_id=3,
        device_id="camera-01", incident_id=incident["incident_id"],
        from_time="2026-09-23T01:00:00Z",
        to_time="2026-09-23T03:00:00Z", limit=10, offset=0,
    )
    assert filtered["total"] == 1
    assert filtered["items"][0]["help_request_id"] == help_item["help_request_id"]

    _login(context.client)
    response = context.client.get(
        "/api/v3/help-requests?status=open&category=device_issue&user_id=3"
        "&device_id=camera-01&incident_id="
        f"{incident['incident_id']}&from=2026-09-23T01%3A00%3A00Z"
        "&to=2026-09-23T03%3A00%3A00Z"
    )
    assert response.status_code == 200
    assert response.get_json()["total"] == 1
    detail = context.client.get(
        f"/api/v3/help-requests/{help_item['help_request_id']}"
    )
    assert detail.status_code == 200
    serialized = detail.get_data(as_text=True)
    assert "mobile_session_id" not in serialized
    assert "idempotency_key" not in serialized
    assert "request_fingerprint" not in serialized


def test_mobile_api_redaction_read_ack_help_and_support(context):
    incident = _create(context.incident)
    headers = _bearer(context)
    response = context.client.get(
        "/api/v3/mobile/notices", headers=headers
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["notices"][0]["incident_id"] == incident["incident_id"]
    assert "admin_summary" not in response.get_data(as_text=True)
    assert context.client.post(
        f"/api/v3/mobile/notices/{incident['incident_id']}/read",
        json={}, headers=headers,
    ).status_code == 200
    acknowledged = context.client.post(
        f"/api/v3/mobile/notices/{incident['incident_id']}/acknowledge",
        json={}, headers=headers,
    )
    assert acknowledged.status_code == 200
    assert acknowledged.get_json()["acknowledged"] is True
    support = context.client.get(
        "/api/v3/mobile/support-contact", headers=headers
    )
    assert support.status_code == 200
    assert support.get_json()["available"] is False
    help_response = context.client.post(
        "/api/v3/mobile/help-requests",
        json={
            "incident_id": incident["incident_id"],
            "device_id": "camera-01",
            "category": "device_issue",
            "user_message": "请协助确认设备使用安排。",
        },
        headers={**headers, "Idempotency-Key": "api-help-key-001"},
    )
    assert help_response.status_code == 201
    help_id = help_response.get_json()["help_request_id"]
    assert context.client.get(
        f"/api/v3/mobile/help-requests/{help_id}",
        headers=headers,
    ).status_code == 200


def test_public_text_input_and_request_limits_are_safe(context):
    with pytest.raises(IncidentWorkflowError):
        context.incident.create_incident(
            incident_type="device_anomaly", severity="high",
            source="manual", admin_title="管理标题",
            admin_summary="内部可见 192.0.2.1",
            user_title="设备提醒",
            user_summary="来源 192.0.2.1 port 443 GNN score",
            devices=[{
                "device_id": "camera-01",
                "incident_role": "affected",
                "user_visible": True,
            }],
            publish_to_mobile=True, first_seen_at=NOW,
            actor=ADMIN, request_id="sensitive-user-copy",
        )
    with pytest.raises(IncidentWorkflowError):
        context.incident.create_help_request(
            context.principal, incident_id=None,
            device_id="camera-01", category="other",
            user_message="Authorization Bearer secret",
            idempotency_key="safe-key-00001",
            request_id="secret-help",
        )
    _login(context.client)
    csrf = _csrf(context.client)
    response = context.client.post(
        "/api/v3/incidents",
        data=b"{" + (b"x" * (17 * 1024)) + b"}",
        content_type="application/json",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 413
    assert "request_id" in response.get_json()["error"]


def test_missing_database_fails_closed_without_creation(tmp_path):
    database_path = tmp_path / "missing.sqlite"
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "missing-db-secret",
            "DATABASE_PATH": str(database_path),
        },
        mobile_settings=_settings(),
    )
    client = app.test_client()
    _login(client)
    response = client.get("/api/v3/incidents")
    assert response.status_code == 503
    assert response.get_json()["error"]["request_id"]
    assert not database_path.exists()


def test_mobile_notice_writes_use_persistent_rate_limit(context):
    incident = _create(context.incident)
    for index in range(120):
        context.incident.mark_notice(
            context.principal, incident["incident_id"],
            acknowledged=False, request_id=f"read-rate-{index}",
        )
    with pytest.raises(IncidentWorkflowError) as error:
        context.incident.mark_notice(
            context.principal, incident["incident_id"],
            acknowledged=False, request_id="read-rate-blocked",
        )
    assert error.value.code == "mobile_rate_limited"
    with sqlite3.connect(context.database_path) as connection:
        row = connection.execute(
            "SELECT attempt_count FROM v3_mobile_rate_limits "
            "WHERE action='mobile_notice_write'"
        ).fetchone()
    assert row == (120,)


def test_pruned_mobile_notice_cursor_requires_snapshot(context, tmp_path):
    baseline = context.incident.list_mobile_notices(
        context.principal, view="all"
    )
    incident = _create(context.incident)
    context.incident.transition_incident(
        incident["incident_id"], target_status="acknowledged",
        expected_incident_version=1,
        public_progress="管理员已收到提醒并开始核查。",
        actor=OPERATOR, request_id="retention-ack",
    )
    context.incident.transition_incident(
        incident["incident_id"], target_status="false_positive",
        expected_incident_version=2,
        public_progress="该提醒已结束，无需进一步操作。",
        false_positive_reason="复核后无需处置。",
        actor=OPERATOR, request_id="retention-false-positive",
    )
    with sqlite3.connect(context.database_path) as connection:
        connection.execute(
            "UPDATE v3_mobile_notice_changes SET changed_at='2018-01-01T00:00:00Z'"
        )
    settings = RetentionSettings({
        **RETENTION_DEFAULTS,
        "mobile_notice_changes": 1,
    })
    backup_directory = tmp_path / "retention-backups"
    backup_directory.mkdir()

    apply_retention(
        context.database_path, backup_directory,
        now=datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc),
        settings=settings, batch_limit=1,
    )

    delta = context.incident.list_mobile_notices(
        context.principal, after=baseline["next_cursor"], view="all"
    )
    assert delta["snapshot_required"] is True
    assert delta["notices"] == []
