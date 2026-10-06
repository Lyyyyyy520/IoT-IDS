import sqlite3

from werkzeug.security import check_password_hash

import database


def test_empty_database_does_not_receive_shared_default_accounts(tmp_path, monkeypatch):
    database_path = tmp_path / "ids.db"
    monkeypatch.delenv("IOT_IDS_BOOTSTRAP_ADMIN_PASSWORD", raising=False)

    database.init_db(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_empty_legacy_asset_inventory_is_not_filled_with_demo_devices(tmp_path, monkeypatch):
    database_path = tmp_path / "ids.db"
    monkeypatch.delenv("IOT_IDS_BOOTSTRAP_ADMIN_PASSWORD", raising=False)

    database.init_db(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM config WHERE key IN "
            "('detection_mode', 'confidence_threshold', 'merge_window_minutes', 'auto_block')"
        ).fetchone()[0] == 0

def test_explicit_bootstrap_admin_is_hashed_and_idempotent(tmp_path, monkeypatch):
    database_path = tmp_path / "ids.db"
    monkeypatch.setenv("IOT_IDS_BOOTSTRAP_ADMIN_USERNAME", "initial-admin")
    monkeypatch.setenv("IOT_IDS_BOOTSTRAP_ADMIN_PASSWORD", "test-only-unique-password")

    database.init_db(database_path)
    database.init_db(database_path)

    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT username, password_hash, role FROM users"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "initial-admin"
    assert rows[0][1] != "test-only-unique-password"
    assert check_password_hash(rows[0][1], "test-only-unique-password")
    assert rows[0][2] == "admin"


def test_runtime_connection_refuses_missing_file_without_creation(tmp_path):
    database_path = tmp_path / "missing.sqlite"

    try:
        database.get_db(database_path)
    except database.DatabaseUnavailableError:
        pass
    else:
        raise AssertionError("missing runtime database must be rejected")

    assert not database_path.exists()
