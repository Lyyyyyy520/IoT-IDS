import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import secrets
import sqlite3
import sys

from app import create_app
from config import MqttSubscriberSettings
from database import init_db
from v3_admin_bootstrap import (
    AdminBootstrapError,
    bootstrap_first_admin,
    main,
)
from v3_database import V3_MIGRATIONS, initialize_v3_database, read_applied_migrations
from werkzeug.security import check_password_hash, generate_password_hash


def _make_v9(path: Path, monkeypatch) -> None:
    monkeypatch.setenv("IOT_IDS_BOOTSTRAP_ADMIN_PASSWORD", "")
    with redirect_stdout(io.StringIO()):
        init_db(path)
    initialize_v3_database(path)


def _app(path: Path):
    return create_app(
        {
            "TESTING": True,
            "SECRET_KEY": secrets.token_hex(32),
            "DATABASE_PATH": str(path),
        },
        mqtt_settings_provider=lambda: MqttSubscriberSettings(enabled=False),
        service_environment={},
    )


def _add_test_user(path: Path, username: str, role: str, password: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
            (username, generate_password_hash(password), role),
        )


def test_manual_cli_creates_only_first_admin_and_login_grants_health_access(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "bootstrap-v9.sqlite"
    username = "bootstrap-test-" + secrets.token_hex(4)
    password = secrets.token_urlsafe(24)
    _make_v9(path, monkeypatch)
    prompts = []

    result = main(
        ["--database", str(path), "--username", username],
        password_reader=lambda prompt: prompts.append(prompt) or password,
    )

    assert result == 0
    output = capsys.readouterr()
    assert "first_admin_created" in output.out
    assert password not in output.out + output.err
    assert len(prompts) == 2
    with sqlite3.connect(path) as connection:
        user = connection.execute(
            "SELECT username,password_hash,role FROM users WHERE username=?",
            (username,),
        ).fetchone()
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        applied = read_applied_migrations(connection)
    assert user[0] == username
    assert user[1] != password
    assert check_password_hash(user[1], password)
    assert user[2] == "admin"
    assert [(row["version"], row["name"], row["checksum"]) for row in applied] == [
        (migration.version, migration.name, migration.checksum)
        for migration in V3_MIGRATIONS
    ]

    app = _app(path)
    client = app.test_client()
    login = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert login.status_code == 200
    assert login.get_json()["user"]["role"] == "admin"
    health = client.get("/api/v3/system/health")
    assert health.status_code == 200
    encoded = json.dumps(health.get_json(), ensure_ascii=False)
    assert str(path.resolve()) not in encoded
    assert password not in encoded
    assert user[1] not in encoded
    assert health.get_json()["components"]["graph"]["reason_code"] == "graph_capability_unavailable"

    refused_prompts = []
    assert main(
        ["--database", str(path), "--username", "another-explicit-account"],
        password_reader=lambda prompt: refused_prompts.append(prompt),
    ) == 3
    assert refused_prompts == []
    refused_output = capsys.readouterr()
    assert "users_already_exist" in refused_output.err
    assert str(path.resolve()) not in refused_output.out + refused_output.err
    assert password not in refused_output.out + refused_output.err


def test_admin_operator_user_mobile_and_anonymous_health_permissions(tmp_path, monkeypatch):
    path = tmp_path / "roles-v9.sqlite"
    _make_v9(path, monkeypatch)
    for role in ("admin", "operator", "user"):
        _add_test_user(path, role + "-fixture", role, secrets.token_urlsafe(24))

    anonymous = _app(path).test_client()
    assert anonymous.get("/api/v3/system/health").status_code == 401
    assert anonymous.get(
        "/api/v3/system/health",
        headers={"Authorization": "Bearer mobile-test-token"},
    ).status_code == 403

    for role, expected_status in (("admin", 200), ("operator", 200), ("user", 403)):
        password = secrets.token_urlsafe(24)
        username = role + "-login"
        _add_test_user(path, username, role, password)
        client = _app(path).test_client()
        response = client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        assert response.status_code == 200
        assert client.get("/api/v3/system/health").status_code == expected_status


def test_missing_wrong_version_and_checksum_databases_fail_without_prompt_or_creation(
    tmp_path, monkeypatch, capsys
):
    missing = tmp_path / "not-created.sqlite"
    assert main(
        ["--database", str(missing), "--username", "explicit-user"],
        password_reader=lambda _prompt: (_ for _ in ()).throw(AssertionError()),
    ) == 4
    assert not missing.exists()
    missing_output = capsys.readouterr()
    assert "database_file_missing" in missing_output.err
    assert str(missing.resolve()) not in missing_output.out + missing_output.err

    path = tmp_path / "wrong-version.sqlite"
    _make_v9(path, monkeypatch)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM v3_schema_migrations WHERE version=9")
    assert main(
        ["--database", str(path), "--username", "explicit-user"],
        password_reader=lambda _prompt: (_ for _ in ()).throw(AssertionError()),
    ) == 4
    version_output = capsys.readouterr()
    assert "schema_not_v9" in version_output.err
    assert str(path.resolve()) not in version_output.out + version_output.err
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO v3_schema_migrations(version,name,checksum,applied_at) "
            "VALUES(9,?,?,?)",
            (V3_MIGRATIONS[-1].name, "bad-checksum", "2026-01-01T00:00:00Z"),
        )
    assert main(
        ["--database", str(path), "--username", "explicit-user"],
        password_reader=lambda _prompt: (_ for _ in ()).throw(AssertionError()),
    ) == 4
    checksum_output = capsys.readouterr()
    assert "schema_checksum_mismatch" in checksum_output.err
    assert "bad-checksum" not in checksum_output.out + checksum_output.err
    assert str(path.resolve()) not in checksum_output.out + checksum_output.err
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_confirmation_and_transaction_failure_leave_no_user(tmp_path, monkeypatch, capsys):
    path = tmp_path / "rollback-v9.sqlite"
    _make_v9(path, monkeypatch)
    first = secrets.token_urlsafe(24)
    second = secrets.token_urlsafe(24)
    prompts = iter((first, second))
    try:
        bootstrap_first_admin(path, "rollback-test", password_reader=lambda _prompt: next(prompts))
    except AdminBootstrapError as exc:
        assert exc.reason_code == "password_confirmation_mismatch"
    else:
        raise AssertionError("mismatched password confirmation must be rejected")

    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_first_admin BEFORE INSERT ON users "
            "BEGIN SELECT RAISE(ABORT, 'test failure'); END"
        )
    password = secrets.token_urlsafe(24)
    assert main(
        ["--database", str(path), "--username", "rollback-test"],
        password_reader=lambda _prompt: password,
    ) == 4
    failed_output = capsys.readouterr()
    assert "database_operation_failed" in failed_output.err
    assert "test failure" not in failed_output.out + failed_output.err
    assert password not in failed_output.out + failed_output.err
    assert str(path.resolve()) not in failed_output.out + failed_output.err
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    assert not any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))


def test_noninteractive_cli_refuses_password_fallback(tmp_path, monkeypatch, capsys):
    path = tmp_path / "noninteractive-v9.sqlite"
    _make_v9(path, monkeypatch)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    result = main(["--database", str(path), "--username", "explicit-user"])

    captured = capsys.readouterr()
    assert result == 3
    assert "interactive_terminal_required" in captured.err
    assert "password" not in captured.out.lower()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_eof_during_password_prompt_has_stable_output_and_creates_no_user(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "password-eof-v9.sqlite"
    _make_v9(path, monkeypatch)

    def end_input(_prompt):
        raise EOFError("private input failure detail")

    result = main(
        ["--database", str(path), "--username", "explicit-user"],
        password_reader=end_input,
    )

    output = capsys.readouterr()
    assert result == 3
    assert "password_input_ended" in output.err
    assert "private input failure detail" not in output.out + output.err
    assert str(path.resolve()) not in output.out + output.err
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_unexpected_password_reader_error_is_redacted_and_creates_no_user(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "password-error-v9.sqlite"
    _make_v9(path, monkeypatch)

    def fail_input(_prompt):
        raise RuntimeError("private terminal detail")

    result = main(
        ["--database", str(path), "--username", "explicit-user"],
        password_reader=fail_input,
    )

    output = capsys.readouterr()
    assert result == 3
    assert "bootstrap_operation_failed" in output.err
    assert "private terminal detail" not in output.out + output.err
    assert str(path.resolve()) not in output.out + output.err
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
