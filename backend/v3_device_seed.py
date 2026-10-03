"""Explicitly seed the 30 virtual community devices into an existing v3 database.

Run manually with ``--database``. One-time convenience so the virtual devices
simulated by ``edge/virtual_device_generator.py`` appear in the web UI and can
receive device-graph GNN risk without the per-device "discover -> claim" flow.

The fabricated MAC (``community_devices.mac_for``) matches the generator's
heartbeat MAC, so subsequent heartbeats are accepted as known devices and just
refresh online state + IP binding.

Physical ESP32 devices are NOT seeded here: they report their real MAC and go
through the standard discovery -> claim flow.

This module is not imported by the Flask app.
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent
_EDGE_DIR = _BACKEND_DIR.parent / "edge"
if str(_EDGE_DIR) not in sys.path:
    sys.path.insert(0, str(_EDGE_DIR))

from community_devices import VIRTUAL_DEVICES, mac_for  # noqa: E402
from v3_database import connect_v3_existing  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_DATABASE = 4

SQLITE_HEADER = b"SQLite format 3\x00"

_TYPE_LABELS = {
    'door_sensor': '门磁传感器',
    'smoke': '烟雾报警器',
    'corridor_light': '楼道灯',
    'door': '门禁',
    'temp_sensor': '温湿度传感器',
    'camera': '摄像头',
    'light': '路灯',
    'plug': '插座',
    'speaker': '音箱',
}


class DeviceSeedError(RuntimeError):
    def __init__(self, reason_code: str, exit_code: int = EXIT_REFUSED):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _existing_database(raw_path: str | Path) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise DeviceSeedError("database_file_missing", EXIT_DATABASE)
    try:
        with path.open("rb") as handle:
            header = handle.read(len(SQLITE_HEADER))
    except OSError as exc:
        raise DeviceSeedError("database_open_failed", EXIT_DATABASE) from exc
    if header != SQLITE_HEADER:
        raise DeviceSeedError("database_not_sqlite", EXIT_DATABASE)
    return path.resolve()


def _v3_ready(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='v3_device_profiles'"
    ).fetchone()
    if not row:
        raise DeviceSeedError("v3_schema_missing", EXIT_DATABASE)


def seed_virtual_devices(database_path: str | Path) -> dict:
    """Register the 30 virtual devices + IP bindings + current state (idempotent)."""
    path = _existing_database(database_path)
    connection = connect_v3_existing(path)
    try:
        _v3_ready(connection)
        now = _iso(datetime.now(timezone.utc))
        created = 0
        skipped = 0
        try:
            for device_id, device_type, ip, _coap, _links in VIRTUAL_DEVICES:
                exists = connection.execute(
                    "SELECT 1 FROM v3_device_profiles WHERE device_id = ?",
                    (device_id,),
                ).fetchone()
                if exists:
                    skipped += 1
                    continue

                display_name = f"{_TYPE_LABELS.get(device_type, device_type)} {device_id}"
                connection.execute(
                    "INSERT INTO v3_device_profiles "
                    "(device_id, identity_kind, identity_value, display_name, device_type, "
                    " area_id, operation_mode, created_at, updated_at, importance, "
                    " profile_source, profile_version, retired_at, retirement_reason) "
                    "VALUES (?, 'mac', ?, ?, ?, NULL, 'active', ?, ?, 'normal', 'virtual', 1, NULL, NULL)",
                    (device_id, mac_for(device_id), display_name, device_type, now, now),
                )
                connection.execute(
                    "INSERT INTO v3_device_ip_bindings "
                    "(device_id, ip_address, valid_from, valid_to, source, source_observation_id, created_at) "
                    "VALUES (?, ?, ?, NULL, 'seed', NULL, ?)",
                    (device_id, ip, now, now),
                )
                connection.execute(
                    "INSERT INTO v3_device_current_state "
                    "(device_id, connection_status, ip_address, last_observed_at, last_received_at, "
                    " state_version, updated_at) "
                    "VALUES (?, 'unknown', ?, NULL, NULL, 0, ?)",
                    (device_id, ip, now),
                )
                created += 1
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return {"created": created, "skipped": skipped}
    finally:
        connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seed the 30 virtual community devices into an existing v3 database."
    )
    parser.add_argument("--database", required=True, help="existing SQLite database file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = seed_virtual_devices(args.database)
    except DeviceSeedError as exc:
        print(f"error reason_code={exc.reason_code}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:
        print(f"error reason_code=seed_failed type={type(exc).__name__}", file=sys.stderr)
        return EXIT_REFUSED
    print(
        f"status=ok created={result['created']} skipped={result['skipped']} "
        f"total={len(VIRTUAL_DEVICES)}"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
