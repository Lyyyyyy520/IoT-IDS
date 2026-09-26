"""Create a fresh local demo database only when the explicit target is absent."""
import os
import sqlite3
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(backend_dir))
from database import init_db  # noqa: E402
from werkzeug.security import check_password_hash  # noqa: E402

SQLITE_HEADER = b"SQLite format 3\x00"
SIDECARS = ("-wal", "-shm", "-journal")


def fail(message: str) -> int:
    print(f"Demo database bootstrap failed: {message}", file=sys.stderr)
    return 1


def main() -> int:
    raw_path = os.environ.get("IOT_IDS_DATABASE_PATH", "").strip()
    username = os.environ.get("IOT_IDS_BOOTSTRAP_ADMIN_USERNAME", "").strip()
    password = os.environ.get("IOT_IDS_BOOTSTRAP_ADMIN_PASSWORD", "")
    if not raw_path or not username or not password:
        return fail("required process-local database/admin bootstrap settings are missing")

    path = Path(raw_path).expanduser().resolve()
    if path.exists():
        return fail("target already exists; refusing to initialize or migrate it")
    if any(Path(str(path) + suffix).exists() for suffix in SIDECARS):
        return fail("SQLite sidecar exists; refusing to initialize this target")

    init_db(path)

    try:
        with path.open("rb") as handle:
            if handle.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
                return fail("initializer did not create a SQLite 3 database")
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
            user = connection.execute(
                "SELECT password_hash, role FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if integrity != ["ok"]:
                return fail("new base database failed PRAGMA integrity_check")
            if not user or user[1] != "admin" or not check_password_hash(user[0], password):
                return fail("requested administrator was not initialized correctly")
        finally:
            connection.close()
    except (OSError, sqlite3.Error) as exc:
        return fail(f"new database could not be verified: {exc}")

    print(f"Fresh base database initialized and verified: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())