from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from v3_database import (
    MIGRATION_TABLE,
    SchemaMigration,
    V3_DEVICE_STATE_TABLES,
    V3_MIGRATIONS,
    apply_v3_migrations,
    connect_v3,
)
from v3_db_upgrade import (
    EXIT_DATABASE,
    EXIT_INPUT,
    InputPathError,
    UpgradeMigrationError,
    apply_upgrade,
    create_verified_backup,
    main,
    plan_database,
)


def _create_legacy_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE legacy_marker (value TEXT NOT NULL);
            INSERT INTO legacy_marker VALUES ('preserve-me');
            CREATE TABLE assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                ip_address TEXT NOT NULL,
                mac_address TEXT,
                device_type TEXT
            );
            """
        )
        connection.executemany(
            "INSERT INTO assets (name, ip_address, mac_address, device_type) "
            "VALUES (?, ?, ?, ?)",
            [
                ("摄像头一", "192.168.4.10", "AA:BB:CC:DD:EE:01", "camera"),
                ("摄像头二", "192.168.4.10", "aa-bb-cc-dd-ee-01", "camera"),
                ("温度计", "", None, "sensor"),
                ("门锁", "invalid-ip", "not-a-mac", "door"),
            ],
        )
        connection.execute("PRAGMA user_version=27")


def _schema_snapshot(path: Path) -> list[tuple]:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()


def _file_digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }


def test_plan_is_read_only_and_json_serializable(tmp_path):
    database_path = tmp_path / "legacy.sqlite"
    _create_legacy_database(database_path)
    before_stat = database_path.stat()
    before_digest = _file_digest(database_path)
    before_schema = _schema_snapshot(database_path)
    before_sidecars = {
        str(path) for path in tmp_path.iterdir() if path.name != database_path.name
    }

    report = plan_database(database_path)

    after_stat = database_path.stat()
    assert report["integrity_check"] == ["ok"]
    assert report["current_schema_version"] == 0
    assert report["automatic_legacy_asset_import"] is False
    assert report["database_file"]["sha256"] == before_digest
    assert json.loads(json.dumps(report))["database"] == str(database_path.resolve())
    assert database_path.stat().st_size == before_stat.st_size
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert _file_digest(database_path) == before_digest
    assert _schema_snapshot(database_path) == before_schema
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 27
    assert {
        str(path) for path in tmp_path.iterdir() if path.name != database_path.name
    } == before_sidecars


def test_plan_cli_can_emit_machine_readable_json(tmp_path, capsys):
    database_path = tmp_path / "audit.sqlite"
    _create_legacy_database(database_path)

    assert main(["plan", "--database", str(database_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["operation"] == "plan"
    assert report["integrity_ok"] is True
    assert report["automatic_legacy_asset_import"] is False


@pytest.mark.parametrize("operation", ["plan", "apply"])
def test_missing_database_path_is_rejected_without_creation(
    tmp_path, capsys, operation
):
    missing = tmp_path / "missing.sqlite"
    arguments = [operation, "--database", str(missing), "--json"]
    if operation == "apply":
        arguments.extend(["--backup-directory", str(tmp_path)])

    assert main(arguments) == EXIT_INPUT
    assert not missing.exists()
    assert "refusing to create" in capsys.readouterr().err


def test_non_sqlite_file_is_rejected(tmp_path, capsys):
    invalid = tmp_path / "not-sqlite.db"
    invalid.write_bytes(b"this is not sqlite")
    before = invalid.read_bytes()

    assert main(["plan", "--database", str(invalid), "--json"]) == EXIT_DATABASE
    assert invalid.read_bytes() == before
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "InvalidDatabaseError"


def test_plan_reports_legacy_identity_conflicts_without_import(tmp_path):
    database_path = tmp_path / "conflicts.sqlite"
    _create_legacy_database(database_path)

    report = plan_database(database_path)
    audit = report["legacy_asset_identity_audit"]

    assert len(audit["duplicate_mac"]) == 1
    assert audit["duplicate_mac"][0]["count"] == 2
    assert len(audit["duplicate_ip"]) == 1
    assert len(audit["missing_mac"]) == 1
    assert len(audit["invalid_mac"]) == 1
    assert len(audit["missing_ip"]) == 1
    assert len(audit["invalid_ip"]) == 1
    assert audit["automatic_import"] is False
    assert "v3_device_profiles" not in _table_names(database_path)


def test_strict_plan_refuses_live_wal_sidecars_without_touching_them(tmp_path):
    database_path = tmp_path / "live.sqlite"
    writer = sqlite3.connect(database_path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("CREATE TABLE events (value TEXT NOT NULL)")
        writer.execute("INSERT INTO events VALUES ('pending-audit-copy')")
        writer.commit()
        wal_path = Path(f"{database_path}-wal")
        assert wal_path.exists()
        before = _file_digest(wal_path)

        with pytest.raises(InputPathError, match="backup copy"):
            plan_database(database_path)

        assert _file_digest(wal_path) == before
    finally:
        writer.close()


def test_apply_creates_verified_pre_migration_backup_and_additive_schema(tmp_path):
    database_path = tmp_path / "legacy.sqlite"
    backup_directory = tmp_path / "backups"
    backup_directory.mkdir()
    _create_legacy_database(database_path)

    result = apply_upgrade(database_path, backup_directory)
    backup_path = Path(result["backup"]["path"])

    assert backup_path.exists()
    assert result["backup"]["method"] == "sqlite_backup_api"
    assert result["backup"]["backup_integrity_check"] == ["ok"]
    assert result["pre_integrity_check"] == ["ok"]
    assert result["post_integrity_check"] == ["ok"]
    assert result["applied_versions"] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert V3_DEVICE_STATE_TABLES <= _table_names(database_path)
    assert MIGRATION_TABLE in _table_names(database_path)
    assert MIGRATION_TABLE not in _table_names(backup_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM legacy_marker").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM v3_device_profiles").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM v3_schema_migrations").fetchone()[0] == 9


def test_repeated_apply_is_idempotent_and_does_not_rerun_recorded_migration(tmp_path):
    database_path = tmp_path / "repeat.sqlite"
    backup_directory = tmp_path / "backups"
    backup_directory.mkdir()
    _create_legacy_database(database_path)
    apply_upgrade(database_path, backup_directory)

    with sqlite3.connect(database_path) as connection:
        first_ledger = connection.execute(
            "SELECT version, name, checksum, applied_at FROM v3_schema_migrations"
        ).fetchall()
        connection.execute(
            "INSERT INTO v3_device_profiles "
            "(device_id, identity_kind, identity_value, display_name, device_type, "
            "operation_mode, created_at, updated_at) "
            "VALUES ('door-01', 'mac', 'AA:BB:CC:DD:EE:10', '门锁', 'door', "
            "'active', '2026-09-19T00:00:00Z', '2026-09-19T00:00:00Z')"
        )

    second = apply_upgrade(database_path, backup_directory)

    assert second["applied_versions"] == []
    assert second["skipped_versions"] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version, name, checksum, applied_at FROM v3_schema_migrations"
        ).fetchall() == first_ledger
        assert connection.execute("SELECT COUNT(*) FROM v3_device_profiles").fetchone()[0] == 1


def test_upgrade_plan_and_apply_add_pending_v3_and_v4_migrations_to_v2(tmp_path):
    database_path = tmp_path / "existing-v2.sqlite"
    backup_directory = tmp_path / "backups"
    backup_directory.mkdir()
    connection = connect_v3(database_path)
    try:
        apply_v3_migrations(connection, V3_MIGRATIONS[:2])
    finally:
        connection.close()

    plan = plan_database(database_path)
    assert plan["current_schema_version"] == 2
    assert [item["version"] for item in plan["pending_migrations"]] == [
        3, 4, 5, 6, 7, 8, 9,
    ]
    assert plan["schema_drift"] == []

    result = apply_upgrade(database_path, backup_directory)
    assert result["applied_versions"] == [3, 4, 5, 6, 7, 8, 9]
    assert result["skipped_versions"] == [1, 2]
    assert result["current_schema_version"] == 9
    assert "v3_realtime_events" in _table_names(database_path)
    assert "v3_device_management_audit" in _table_names(database_path)
    assert "v3_device_traffic_minutes" in _table_names(database_path)


def test_sqlite_backup_api_captures_committed_wal_rows(tmp_path):
    database_path = tmp_path / "wal.sqlite"
    backup_directory = tmp_path / "backups"
    backup_directory.mkdir()
    writer = sqlite3.connect(database_path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("CREATE TABLE events (value TEXT NOT NULL)")
        writer.execute("INSERT INTO events VALUES ('from-wal')")
        writer.commit()
        assert Path(f"{database_path}-wal").exists()

        backup = create_verified_backup(database_path, backup_directory)
    finally:
        writer.close()

    with sqlite3.connect(backup["path"]) as connection:
        assert connection.execute("SELECT value FROM events").fetchall() == [("from-wal",)]
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_migration_failure_rolls_back_all_schema_changes(tmp_path):
    database_path = tmp_path / "rollback.sqlite"
    backup_directory = tmp_path / "backups"
    backup_directory.mkdir()
    _create_legacy_database(database_path)
    failing = SchemaMigration(
        version=99,
        name="forced_test_failure",
        statements=(
            "CREATE TABLE v3_partial_should_rollback (id INTEGER PRIMARY KEY)",
            "CREATE TABL this_is_invalid (id INTEGER)",
        ),
    )

    with pytest.raises(UpgradeMigrationError, match="rolled back"):
        apply_upgrade(
            database_path,
            backup_directory,
            migrations=V3_MIGRATIONS + (failing,),
        )

    tables = _table_names(database_path)
    assert "v3_partial_should_rollback" not in tables
    assert MIGRATION_TABLE not in tables
    assert not (V3_DEVICE_STATE_TABLES & tables)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT value FROM legacy_marker").fetchone()[0] == "preserve-me"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    backups = list(backup_directory.glob("*.sqlite"))
    assert len(backups) == 1
    assert MIGRATION_TABLE not in _table_names(backups[0])
