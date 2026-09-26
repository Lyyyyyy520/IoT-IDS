"""Explicitly create the first Web administrator in an existing v9 database.

Run manually with ``--database`` and ``--username``. The password is read twice
from a terminal with echo disabled. This module is not imported by the Flask app.
"""
import argparse
import getpass
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote

from werkzeug.security import generate_password_hash

from v3_database import V3_EXPECTED_OBJECTS, V3_MIGRATIONS, read_applied_migrations


EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_DATABASE = 4

SQLITE_HEADER = b"SQLite format 3\x00"
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
LEGACY_TABLES = frozenset(
    {
        "users",
        "alerts",
        "traffic_logs",
        "audit_logs",
        "assets",
        "policies",
        "config",
        "rules",
    }
)
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024


class AdminBootstrapError(RuntimeError):
    """Stable, non-sensitive failure code for the manual bootstrap operation."""

    def __init__(self, reason_code: str, exit_code: int = EXIT_REFUSED):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code


def _sqlite_reason(error: sqlite3.Error) -> str:
    code = getattr(error, "sqlite_errorcode", None)
    primary = (int(code) & 0xFF) if isinstance(code, int) else None
    if primary == sqlite3.SQLITE_BUSY:
        return "database_busy"
    if primary == sqlite3.SQLITE_LOCKED:
        return "database_locked"
    if primary == sqlite3.SQLITE_READONLY:
        return "database_read_only"
    if primary == sqlite3.SQLITE_FULL:
        return "database_disk_full"
    if primary in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}:
        return "database_corrupt"
    if primary == sqlite3.SQLITE_CANTOPEN:
        return "database_open_failed"
    if primary == sqlite3.SQLITE_IOERR:
        return "database_io_error"
    message = str(error).lower()
    if "locked" in message:
        return "database_locked"
    if "busy" in message:
        return "database_busy"
    if "readonly" in message or "read-only" in message:
        return "database_read_only"
    if "disk is full" in message or "database or disk is full" in message:
        return "database_disk_full"
    if "malformed" in message or "not a database" in message:
        return "database_corrupt"
    return "database_operation_failed"


def _existing_database(raw_path: str | Path) -> Path:
    candidate = Path(raw_path).expanduser()
    try:
        path = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AdminBootstrapError("database_file_missing", EXIT_DATABASE) from exc
    if not path.is_file():
        raise AdminBootstrapError("database_file_missing", EXIT_DATABASE)
    try:
        with path.open("rb") as handle:
            header = handle.read(len(SQLITE_HEADER))
    except OSError as exc:
        raise AdminBootstrapError("database_open_failed", EXIT_DATABASE) from exc
    if header != SQLITE_HEADER:
        raise AdminBootstrapError("database_not_sqlite", EXIT_DATABASE)
    if any(Path(str(path) + suffix).exists() for suffix in SIDECAR_SUFFIXES):
        raise AdminBootstrapError("database_sidecar_present", EXIT_DATABASE)
    return path


def _sqlite_uri(path: Path, mode: str, *, immutable: bool = False) -> str:
    encoded = quote(path.as_posix(), safe="/:")
    options = f"mode={mode}"
    if immutable:
        options += "&immutable=1"
    return f"file:{encoded}?{options}"


def _connect(path: Path, mode: str, *, immutable: bool = False) -> sqlite3.Connection:
    connection = None
    try:
        connection = sqlite3.connect(
            _sqlite_uri(path, mode, immutable=immutable),
            uri=True,
            timeout=5,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if mode == "ro":
            connection.execute("PRAGMA query_only=ON")
        return connection
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise AdminBootstrapError(_sqlite_reason(exc), EXIT_DATABASE) from exc


def _validate_v9(connection: sqlite3.Connection) -> None:
    try:
        objects = {
            row["name"]: row["type"]
            for row in connection.execute(
                "SELECT name, type FROM sqlite_master "
                "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%'"
            )
        }
        if any(objects.get(name) != "table" for name in LEGACY_TABLES):
            raise AdminBootstrapError("schema_incomplete", EXIT_DATABASE)
        if any(objects.get(name) != kind for name, kind in V3_EXPECTED_OBJECTS.items()):
            raise AdminBootstrapError("schema_incomplete", EXIT_DATABASE)

        applied = read_applied_migrations(connection)
        if len(applied) != len(V3_MIGRATIONS):
            raise AdminBootstrapError("schema_not_v9", EXIT_DATABASE)
        for actual, expected in zip(applied, V3_MIGRATIONS):
            if actual["version"] != expected.version:
                raise AdminBootstrapError("schema_not_v9", EXIT_DATABASE)
            if actual["name"] != expected.name or actual["checksum"] != expected.checksum:
                raise AdminBootstrapError("schema_checksum_mismatch", EXIT_DATABASE)

        integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
    except AdminBootstrapError:
        raise
    except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, sqlite3.Error):
            raise AdminBootstrapError(_sqlite_reason(exc), EXIT_DATABASE) from exc
        raise AdminBootstrapError("schema_invalid", EXIT_DATABASE) from exc

    if integrity != ["ok"]:
        raise AdminBootstrapError("integrity_check_failed", EXIT_DATABASE)


def _validate_username(username: str) -> str:
    normalized = username.strip()
    if (
        not normalized
        or len(normalized) > 128
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise AdminBootstrapError("username_invalid")
    return normalized


def _read_user_count(connection: sqlite3.Connection) -> int:
    try:
        return int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
    except sqlite3.Error as exc:
        raise AdminBootstrapError(_sqlite_reason(exc), EXIT_DATABASE) from exc


def bootstrap_first_admin(
    database_path: str | Path,
    username: str,
    *,
    password_reader=None,
) -> None:
    """Create one admin only after checking an existing, unchanged v9 schema."""
    path = _existing_database(database_path)
    normalized_username = _validate_username(username)
    reader = password_reader or getpass.getpass

    connection = _connect(path, "ro", immutable=True)
    try:
        _validate_v9(connection)
        if _read_user_count(connection) != 0:
            raise AdminBootstrapError("users_already_exist")
    finally:
        connection.close()

    password = reader("New administrator password: ")
    confirmation = reader("Confirm administrator password: ")
    if password != confirmation:
        raise AdminBootstrapError("password_confirmation_mismatch")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AdminBootstrapError("password_too_short")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AdminBootstrapError("password_too_long")

    password_hash = generate_password_hash(password)
    connection = _connect(path, "rw")
    try:
        try:
            connection.execute("BEGIN IMMEDIATE")
            _validate_v9(connection)
            if _read_user_count(connection) != 0:
                raise AdminBootstrapError("users_already_exist")
            connection.execute(
                "INSERT INTO users (username, password_hash, role) "
                "VALUES (?, ?, 'admin')",
                (normalized_username, password_hash),
            )
            connection.commit()
        except AdminBootstrapError:
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as exc:
            if connection.in_transaction:
                connection.rollback()
            raise AdminBootstrapError(_sqlite_reason(exc), EXIT_DATABASE) from exc
    finally:
        connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create the first administrator in an existing v9 database. "
            "The password is read twice without echo from an interactive terminal."
        )
    )
    parser.add_argument("--database", required=True, help="existing SQLite database file")
    parser.add_argument("--username", required=True, help="explicit first administrator username")
    return parser


def main(argv: list[str] | None = None, *, password_reader=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if password_reader is None and not sys.stdin.isatty():
            raise AdminBootstrapError("interactive_terminal_required")
        bootstrap_first_admin(
            args.database,
            args.username,
            password_reader=password_reader,
        )
        print("status=created reason_code=first_admin_created")
        return EXIT_OK
    except AdminBootstrapError as exc:
        print(f"error reason_code={exc.reason_code}", file=sys.stderr)
        return exc.exit_code
    except EOFError:
        print("error reason_code=password_input_ended", file=sys.stderr)
        return EXIT_REFUSED
    except KeyboardInterrupt:
        print("error reason_code=interrupted", file=sys.stderr)
        return 130
    except Exception:
        print("error reason_code=bootstrap_operation_failed", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
