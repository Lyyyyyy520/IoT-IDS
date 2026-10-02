from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import sqlite3

import pytest

from database import init_db
from v3_database import apply_v3_migrations, initialize_v3_database, V3_MIGRATIONS
from services.realtime_events import RealtimeEventStore
from v3_db_maintenance import (
    MaintenanceError,
    RetentionSettings,
    apply_retention,
    main,
    plan_database,
)


NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
OLD = "2018-01-01T00:00:00Z"
RECENT = "2026-09-20T00:00:00Z"


def _database(tmp_path):
    path = tmp_path / "isolated-maintenance.sqlite"
    init_db(path)
    initialize_v3_database(path)
    return path


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _settings(**overrides):
    from v3_db_maintenance import RETENTION_DEFAULTS

    return RetentionSettings({**RETENTION_DEFAULTS, **overrides})


def _insert_old_traffic(path, count=1):
    with sqlite3.connect(path) as connection:
        for index in range(count):
            connection.execute(
                "INSERT INTO traffic_logs(timestamp,src_ip,dst_ip,payload_hex) "
                "VALUES(?,?,?,?)",
                (OLD, f"198.51.100.{index + 1}", "203.0.113.8", "sensitive-payload"),
            )


def _insert_protected_rows(path):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO v3_device_profiles(device_id,identity_kind,identity_value,"
            "display_name,device_type,created_at,updated_at) "
            "VALUES('device-current','mac','aa:bb:cc:dd:ee:10','Test camera','camera',?,?)",
            (OLD, OLD),
        )
        current_observation = connection.execute(
            "INSERT INTO v3_device_state_observations(device_id,source,ip_address,"
            "observed_at,received_at,payload_json) VALUES(?,?,?,?,?,?)",
            ("device-current", "mqtt", "192.0.2.10", OLD, OLD, '{"sensitive":"observation"}'),
        ).lastrowid
        connection.execute(
            "INSERT INTO v3_device_current_state(device_id,connection_status,"
            "last_observation_id,updated_at) VALUES(?,?,?,?)",
            ("device-current", "online", current_observation, OLD),
        )
        connection.execute(
            "INSERT INTO v3_device_state_observations(device_id,source,ip_address,"
            "observed_at,received_at,payload_json) VALUES(?,?,?,?,?,?)",
            ("device-current", "probe", "192.0.2.11", OLD, OLD, '{"history":true}'),
        )

        connection.execute(
            "INSERT INTO v3_incidents(incident_id,incident_type,severity,status,source,"
            "admin_title,admin_summary,user_title,user_summary,first_seen_at,last_seen_at,"
            "created_at,updated_at,incident_version,created_by) "
            "VALUES('incident-active','device_issue','high','open','system','Internal title',"
            "'Internal summary','Public title','Public summary',?,?,?,?,1,1)",
            (OLD, OLD, OLD, OLD),
        )
        connection.execute(
            "INSERT INTO v3_realtime_events(event_type,occurred_at,payload_json) "
            "VALUES('device.telemetry_updated',?,'{}')",
            (OLD,),
        )
        connection.execute(
            "INSERT INTO v3_realtime_events(event_type,occurred_at,payload_json) "
            "VALUES('incident.opened',?,?)",
            (OLD, json.dumps({"incident_id": "incident-active", "message": "private"})),
        )
        connection.execute(
            "INSERT INTO v3_realtime_events(event_type,occurred_at,payload_json) "
            "VALUES('device.telemetry_updated',?,'{}')",
            (OLD,),
        )
        connection.execute(
            "INSERT INTO v3_incident_timeline(incident_id,action,actor_username,actor_role,"
            "occurred_at,request_id,resulting_status,incident_version) "
            "VALUES('incident-active','opened','operator','operator',?,'request','open',1)",
            (OLD,),
        )
        connection.execute(
            "INSERT INTO v3_mobile_notice_acknowledgements(incident_id,user_id,updated_at) "
            "VALUES('incident-active',1,?)",
            (OLD,),
        )

        connection.execute(
            "INSERT INTO v3_discovered_device_candidates(candidate_id,identity_kind,"
            "identity_value,status,first_seen_at,last_seen_at,created_at,updated_at) "
            "VALUES('candidate-active','mac','aa:bb:cc:dd:ee:20','pending',?,?,?,?)",
            (OLD, OLD, OLD, OLD),
        )
        connection.execute(
            "INSERT INTO v3_discovery_observations(candidate_id,source,observed_at,"
            "received_at,evidence_hash,sanitized_metadata_json,deduplication_key) "
            "VALUES('candidate-active','probe',?,?,?, ?,?)",
            (OLD, OLD, "a" * 64, "{}", "b" * 64),
        )

        connection.execute(
            "INSERT INTO v3_mobile_sessions(session_id,user_id,client_instance_id,"
            "client_display_name,access_token_selector,access_token_hash,refresh_token_selector,"
            "refresh_token_hash,created_at,issued_at,access_expires_at,refresh_expires_at,"
            "last_seen_at,token_generation) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("session-active", 1, "client", "test", "access-selector", "c" * 64,
             "refresh-selector", "d" * 64, RECENT, RECENT,
             "2026-09-25T00:00:00Z", "2026-10-20T00:00:00Z", RECENT, 2),
        )
        connection.execute(
            "INSERT INTO v3_mobile_refresh_history(refresh_token_selector,refresh_token_hash,"
            "session_id,token_generation,rotated_at) VALUES(?,?,?,?,?)",
            ("refresh-selector", "d" * 64, "session-active", 2, RECENT),
        )
        connection.execute(
            "INSERT INTO v3_mobile_pairings(pairing_id,user_id,code_selector,code_hash,"
            "expires_at,max_attempts,created_by,created_at) VALUES(?,?,?,?,?,5,1,?)",
            ("pairing-active", 1, "pairing-selector", "e" * 64,
             "2026-09-25T00:00:00Z", RECENT),
        )
        connection.execute(
            "INSERT INTO audit_logs(username,action,detail,ip_address,created_at) "
            "VALUES('tester','review','private audit body','192.0.2.80','2021-01-01 00:00:00')"
        )


def test_plan_is_read_only_and_never_prints_sensitive_values(tmp_path, capsys):
    path = _database(tmp_path)
    _insert_old_traffic(path)
    _insert_protected_rows(path)
    before_hash = _hash(path)
    before_mtime = path.stat().st_mtime_ns
    sidecars_before = {p.name for p in tmp_path.iterdir() if p != path}

    report = plan_database(path, now=NOW)
    assert json.loads(json.dumps(report))["operation"] == "plan"
    assert report["schema_version"] == 9
    assert report["integrity_ok"] is True
    assert path.stat().st_mtime_ns == before_mtime
    assert _hash(path) == before_hash
    assert {p.name for p in tmp_path.iterdir() if p != path} == sidecars_before

    capsys.readouterr()
    result = main(["plan", "--database", str(path), "--now", NOW.isoformat(), "--json"])
    assert result == 0
    output = capsys.readouterr().out
    for secret in (
        "192.0.2.10", "192.0.2.80", "aa:bb:cc:dd:ee:10",
        "private audit body", "sensitive-payload", "c" * 64, str(path.resolve()),
    ):
        assert secret not in output


def test_apply_creates_backup_then_rolls_back_failed_transaction(tmp_path):
    path = _database(tmp_path)
    _insert_old_traffic(path, count=2)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    before = _hash(path)

    def fail_after_deletes(_connection):
        raise RuntimeError("forced isolated failure")

    with pytest.raises(MaintenanceError, match="rolled back"):
        apply_retention(
            path, backup_dir, now=NOW, before_commit=fail_after_deletes,
        )

    assert _hash(path) == before
    backups = list(backup_dir.glob("*.sqlite"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM traffic_logs").fetchone()[0] == 2
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM traffic_logs").fetchone()[0] == 2


def test_apply_is_bounded_and_idempotent(tmp_path):
    path = _database(tmp_path)
    _insert_old_traffic(path, count=3)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    first = apply_retention(path, backup_dir, now=NOW, batch_limit=1)
    second = apply_retention(path, backup_dir, now=NOW, batch_limit=1)
    third = apply_retention(path, backup_dir, now=NOW, batch_limit=1)
    fourth = apply_retention(path, backup_dir, now=NOW, batch_limit=1)

    assert first["removed_rows"]["traffic_logs"] == 1
    assert second["removed_rows"]["traffic_logs"] == 1
    assert third["removed_rows"]["traffic_logs"] == 1
    assert fourth["removed_rows"]["traffic_logs"] == 0
    assert len(list(backup_dir.glob("*.sqlite"))) == 4
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM traffic_logs").fetchone()[0] == 0
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT reason FROM v3_system_component_health "
            "WHERE component_id='database_maintenance'"
        ).fetchone()[0] == "maintenance_applied"


def test_active_event_prefix_and_live_state_are_protected(tmp_path):
    path = _database(tmp_path)
    _insert_protected_rows(path)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    report = apply_retention(path, backup_dir, now=NOW)

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_device_state_observations"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_discovery_observations"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_mobile_sessions"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_mobile_refresh_history"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_mobile_pairings"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_incident_timeline"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_mobile_notice_acknowledgements"
        ).fetchone()[0] == 1
        events = connection.execute(
            "SELECT event_id,event_type FROM v3_realtime_events ORDER BY event_id"
        ).fetchall()
        assert len(events) == 2
        assert events[0][1] == "incident.opened"
        assert connection.execute("SELECT seq FROM sqlite_sequence "
                                  "WHERE name='v3_realtime_events'").fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_logs"
        ).fetchone()[0] == 1
    event_policy = next(item for item in report["plan"]["policies"]
                        if item["policy"] == "realtime_events")
    assert event_policy["eligible_rows"] == 1
    assert event_policy["protected_rows"] == 1

    replay = RealtimeEventStore(path).read_after(0, limit=16)
    assert replay.events == ()
    assert replay.requires_snapshot is True
    assert replay.reason == "cursor_before_replay_window"


def test_unresolved_candidates_and_current_ingest_cursor_are_retained(tmp_path):
    path = _database(tmp_path)
    _insert_protected_rows(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO v3_traffic_ingest_batches(source_id,source_session_id,batch_id,"
            "batch_sequence,received_at,status,created_at) VALUES('source','session','old',0,?,'committed',?)",
            (OLD, OLD),
        )
        connection.execute(
            "INSERT INTO v3_traffic_ingest_samples(source_id,source_session_id,sample_id,"
            "batch_id,occurred_at,created_at) VALUES('source','session','sample-old','old',?,?)",
            (OLD, OLD),
        )
        connection.execute(
            "INSERT INTO v3_traffic_ingest_batches(source_id,source_session_id,batch_id,"
            "batch_sequence,received_at,status,created_at) VALUES('source','session','current',1,?,'committed',?)",
            (OLD, OLD),
        )

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    apply_retention(path, backup_dir, now=NOW)

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_traffic_ingest_batches WHERE batch_id='current'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_traffic_ingest_batches WHERE batch_id='old'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_traffic_ingest_samples"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT status FROM v3_discovered_device_candidates WHERE candidate_id='candidate-active'"
        ).fetchone()[0] == "pending"


def test_missing_database_and_missing_backup_directory_are_rejected(tmp_path):
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(MaintenanceError, match="refusing to create"):
        plan_database(missing)
    path = _database(tmp_path)
    with pytest.raises(MaintenanceError, match="already exist"):
        apply_retention(path, tmp_path / "missing-backups", now=NOW)
    assert not missing.exists()
    assert not (tmp_path / "missing-backups").exists()


def test_existing_sidecars_and_corrupt_files_are_rejected_without_creation(tmp_path):
    path = _database(tmp_path)
    wal = Path(str(path) + "-wal")
    wal.write_bytes(b"test-sidecar")
    with pytest.raises(MaintenanceError, match="sidecar"):
        plan_database(path)
    wal.unlink()

    corrupt = tmp_path / "corrupt.sqlite"
    corrupt.write_bytes(b"not an sqlite database")
    with pytest.raises(MaintenanceError):
        plan_database(corrupt)


def test_wal_mode_database_is_rejected_without_creating_sidecars(tmp_path):
    path = _database(tmp_path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
    sidecars = [Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal")]
    assert not any(item.exists() for item in sidecars)
    before = _hash(path)

    with pytest.raises(MaintenanceError, match="WAL mode"):
        plan_database(path, now=NOW)

    assert _hash(path) == before
    assert not any(item.exists() for item in sidecars)


def test_retention_configuration_is_bounded():
    with pytest.raises(MaintenanceError, match="between 1 and 36500"):
        from v3_db_maintenance import RetentionSettings

        RetentionSettings.from_environment({"IOT_IDS_RETENTION_REALTIME_EVENTS_DAYS": "0"})


def test_large_aggregate_cleanup_is_bounded_per_table(tmp_path):
    path = _database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO v3_traffic_unassigned_minutes "
            "(source_id,bucket_start,reason_code,sample_count,bytes,packets,"
            "first_sample_at,last_sample_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            [("source", (datetime(2018, 1, 1, tzinfo=timezone.utc)
                           + timedelta(days=index)).isoformat(), "unmatched", 1, 10, 1,
              OLD, OLD, OLD) for index in range(75)],
        )
    backups = tmp_path / "backups"
    backups.mkdir()

    first = apply_retention(path, backups, now=NOW, batch_limit=20)
    policy = next(item for item in first["plan"]["policies"]
                  if item["table"] == "v3_traffic_unassigned_minutes")
    assert policy["eligible_rows"] == 75
    assert policy["planned_rows"] == 20
    assert first["removed_rows"]["v3_traffic_unassigned_minutes"] == 20
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_traffic_unassigned_minutes"
        ).fetchone()[0] == 55


def test_open_help_request_and_its_timeline_are_never_cleaned(tmp_path):
    path = _database(tmp_path)
    _insert_protected_rows(path)
    with sqlite3.connect(path) as connection:
        common = (
            1, "session-active", "other", "private help message", "key-00000001",
            "f" * 64, OLD, OLD,
        )
        connection.execute(
            "INSERT INTO v3_help_requests(help_request_id,user_id,mobile_session_id,"
            "category,user_message,status,idempotency_key,request_fingerprint,created_at,updated_at) "
            "VALUES('help-open',?,?,?,?,'open',?,?,?,?)", common,
        )
        connection.execute(
            "INSERT INTO v3_help_request_timeline(help_request_id,action,actor_username,"
            "actor_role,occurred_at,request_id,resulting_status,request_version) "
            "VALUES('help-open','created','mobile-user','user',?,'request-open','open',1)",
            (OLD,),
        )
        connection.execute(
            "INSERT INTO v3_help_requests(help_request_id,user_id,mobile_session_id,"
            "category,user_message,status,idempotency_key,request_fingerprint,created_at,updated_at,closed_at) "
            "VALUES('help-closed',?,?,?,?,'closed','key-00000002',?,?,?,?)",
            (1, "session-active", "other", "closed private message", "f" * 64,
             OLD, OLD, OLD),
        )
        connection.execute(
            "INSERT INTO v3_help_request_timeline(help_request_id,action,actor_username,"
            "actor_role,occurred_at,request_id,resulting_status,request_version) "
            "VALUES('help-closed','closed','operator','operator',?,'request-closed','closed',2)",
            (OLD,),
        )
    backups = tmp_path / "backups"
    backups.mkdir()

    report = apply_retention(path, backups, now=NOW)

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT status FROM v3_help_requests WHERE help_request_id='help-open'"
        ).fetchone()[0] == "open"
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_help_request_timeline WHERE help_request_id='help-open'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_help_requests WHERE help_request_id='help-closed'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_help_request_timeline WHERE help_request_id='help-closed'"
        ).fetchone()[0] == 0
    assert report["removed_rows"]["v3_help_requests"] == 1


def test_current_mqtt_cursor_boot_session_is_preserved(tmp_path):
    path = _database(tmp_path)
    _insert_protected_rows(path)
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO v3_mqtt_boot_sessions(device_id,boot_id,first_received_at,"
            "last_received_at,last_sequence,last_uptime_ms,firmware_version) "
            "VALUES('device-current',?,?,?,1,1,'1.0')",
            [("boot-current", OLD, OLD), ("boot-old", OLD, OLD)],
        )
        connection.execute(
            "INSERT INTO v3_mqtt_device_cursors(device_id,current_boot_id,updated_at) "
            "VALUES('device-current','boot-current',?)", (OLD,),
        )
    backups = tmp_path / "backups"
    backups.mkdir()

    apply_retention(path, backups, now=NOW)

    with sqlite3.connect(path) as connection:
        boots = connection.execute(
            "SELECT boot_id FROM v3_mqtt_boot_sessions ORDER BY boot_id"
        ).fetchall()
    assert [row[0] for row in boots] == ["boot-current"]


def test_notice_change_retention_stops_at_active_incident_and_keeps_contiguous_suffix(tmp_path):
    path = _database(tmp_path)
    _insert_protected_rows(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO v3_incidents(incident_id,incident_type,severity,status,source,"
            "admin_title,admin_summary,user_title,user_summary,first_seen_at,last_seen_at,"
            "created_at,updated_at,incident_version,created_by) "
            "VALUES('incident-closed','device_issue','low','resolved','system','Title',"
            "'Summary','User title','User summary',?,?,?,?,2,1)",
            (OLD, OLD, OLD, OLD),
        )
        connection.executemany(
            "INSERT INTO v3_mobile_notice_changes(incident_id,change_kind,incident_version,changed_at) "
            "VALUES(?,?,1,?)",
            [("incident-closed", "opened", OLD),
             ("incident-active", "updated", OLD),
             ("incident-closed", "resolved", OLD)],
        )
    backups = tmp_path / "backups"
    backups.mkdir()

    report = apply_retention(path, backups, now=NOW, batch_limit=1)

    with sqlite3.connect(path) as connection:
        changes = connection.execute(
            "SELECT change_id FROM v3_mobile_notice_changes ORDER BY change_id"
        ).fetchall()
    assert [row[0] for row in changes] == [2, 3]
    policy = next(item for item in report["plan"]["policies"]
                  if item["policy"] == "mobile_notice_changes")
    assert policy["eligible_rows"] == 1


def test_disk_full_and_locked_writer_fail_with_stable_codes_and_no_partial_deletes(tmp_path):
    path = _database(tmp_path)
    _insert_old_traffic(path, count=2)
    backups = tmp_path / "backups"
    backups.mkdir()
    original = _hash(path)

    def disk_full(_connection):
        raise sqlite3.OperationalError("database or disk is full")

    with pytest.raises(MaintenanceError) as error:
        apply_retention(path, backups, now=NOW, before_commit=disk_full)
    assert error.value.code == "database_disk_full"
    assert _hash(path) == original

    lock = sqlite3.connect(path, timeout=0.0, isolation_level=None)
    try:
        lock.execute("BEGIN EXCLUSIVE")
        with pytest.raises(MaintenanceError) as error:
            apply_retention(path, backups, now=NOW)
        assert error.value.code in {"database_busy", "database_locked"}
    finally:
        lock.rollback()
        lock.close()
    assert _hash(path) == original


def test_plan_reports_retention_bounds_and_never_defaults_to_live_database(tmp_path, capsys):
    path = _database(tmp_path)
    report = plan_database(path, now=NOW)
    row = next(item for item in report["policies"] if item["policy"] == "audit_records")
    assert row["default_retention_days"] == 2555
    assert row["minimum_retention_days"] == 1
    assert row["maximum_retention_days"] == 36500
    with pytest.raises(SystemExit) as error:
        main(["plan", "--json"])
    assert error.value.code == 2
    assert "backend/data/ids.db" not in capsys.readouterr().err
    assert not (Path(__file__).resolve().parents[1] / "data" / "ids.db").exists()


def test_large_sse_history_paginates_before_cleanup_and_requires_snapshot_afterward(tmp_path):
    path = _database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO v3_realtime_events(event_type,occurred_at,payload_json) "
            "VALUES('device.telemetry_updated',?,'{}')",
            [(OLD,) for _ in range(40)],
        )
    store = RealtimeEventStore(path)
    page = store.read_after(0, limit=64)
    assert len(page.events) == 40
    assert page.requires_snapshot is False
    backups = tmp_path / "backups"
    backups.mkdir()

    apply_retention(path, backups, now=NOW, batch_limit=10)

    old_cursor = store.read_after(0, limit=64)
    assert old_cursor.requires_snapshot is True
    assert old_cursor.reason == "cursor_before_replay_window"
    suffix = store.read_after(10, limit=64)
    assert suffix.requires_snapshot is False
    assert [event["event_id"] for event in suffix.events] == list(range(11, 41))


def test_schema_version_and_checksum_fail_closed(tmp_path):
    old_path = tmp_path / "schema-v8.sqlite"
    init_db(old_path)
    connection = sqlite3.connect(old_path)
    try:
        apply_v3_migrations(connection, V3_MIGRATIONS[:-1])
    finally:
        connection.close()
    with pytest.raises(MaintenanceError) as error:
        plan_database(old_path, now=NOW)
    assert error.value.code == "schema_version_unsupported"

    path = _database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE v3_schema_migrations SET checksum='bad-checksum' WHERE version=9"
        )
    with pytest.raises(MaintenanceError) as error:
        plan_database(path, now=NOW)
    assert error.value.code == "schema_checksum_mismatch"
