"""Preview, back up, and apply the additive v3 SQLite schema.

This module intentionally never imports ``database.py`` or the Flask app, so
the legacy database path cannot be selected implicitly and legacy seed logic
cannot run as a side effect.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import ipaddress
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Iterable
from urllib.parse import quote
from uuid import uuid4

from v3_database import (
    SchemaMigration,
    V3_EXPECTED_OBJECTS,
    V3_EXPECTED_OBJECT_VERSIONS,
    V3_MIGRATIONS,
    apply_v3_migrations,
    current_v3_schema_version,
    read_applied_migrations,
)


EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_DATABASE = 4
EXIT_BACKUP = 5
EXIT_MIGRATION = 6

SQLITE_HEADER = b"SQLite format 3\x00"
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


class UpgradeError(RuntimeError):
    exit_code = EXIT_DATABASE


class InputPathError(UpgradeError):
    exit_code = EXIT_INPUT


class InvalidDatabaseError(UpgradeError):
    exit_code = EXIT_DATABASE


class IntegrityCheckError(UpgradeError):
    exit_code = EXIT_DATABASE


class BackupError(UpgradeError):
    exit_code = EXIT_BACKUP


class UpgradeMigrationError(UpgradeError):
    exit_code = EXIT_MIGRATION


def _resolve_existing_database(raw_path: str | Path) -> Path:
    candidate = Path(raw_path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise InputPathError(
            f"database path does not exist; refusing to create it: {candidate}"
        ) from exc
    if not resolved.is_file():
        raise InputPathError(f"database path is not a file: {resolved}")
    try:
        with resolved.open("rb") as handle:
            header = handle.read(len(SQLITE_HEADER))
    except OSError as exc:
        raise InputPathError(f"cannot read database path {resolved}: {exc}") from exc
    if header != SQLITE_HEADER:
        raise InvalidDatabaseError(f"file is not a SQLite 3 database: {resolved}")
    return resolved


def _resolve_backup_directory(raw_path: str | Path) -> Path:
    candidate = Path(raw_path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise InputPathError(
            f"backup directory does not exist; create and verify it first: {candidate}"
        ) from exc
    if not resolved.is_dir():
        raise InputPathError(f"backup destination is not a directory: {resolved}")
    return resolved


def _sqlite_uri(path: Path, mode: str, *, immutable: bool = False) -> str:
    encoded = quote(path.resolve().as_posix(), safe="/:")
    options = f"mode={mode}"
    if immutable:
        options += "&immutable=1"
    return f"file:{encoded}?{options}"


def _connect_existing(
    path: Path,
    *,
    mode: str,
    immutable: bool = False,
) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(
            _sqlite_uri(path, mode, immutable=immutable),
            uri=True,
            timeout=5,
        )
    except sqlite3.Error as exc:
        raise InvalidDatabaseError(f"cannot open SQLite database {path}: {exc}") from exc
    connection.row_factory = sqlite3.Row
    return connection


def _strict_plan_sidecars(path: Path) -> list[str]:
    return [
        str(Path(f"{path}{suffix}"))
        for suffix in SIDECAR_SUFFIXES
        if Path(f"{path}{suffix}").exists()
    ]


def _file_fingerprint(path: Path) -> dict:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    stat = path.stat()
    return {
        "size_bytes": stat.st_size,
        "modified_time_ns": stat.st_mtime_ns,
        "sha256": digest.hexdigest(),
    }


def _integrity_check(connection: sqlite3.Connection) -> list[str]:
    try:
        return [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
    except sqlite3.Error as exc:
        raise InvalidDatabaseError(f"SQLite integrity_check could not run: {exc}") from exc


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _schema_objects(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        "SELECT type, name FROM sqlite_master "
        "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY type, name"
    ).fetchall()
    return [{"type": row["type"], "name": row["name"]} for row in rows]


def _table_row_counts(connection: sqlite3.Connection, tables: Iterable[str]) -> dict:
    counts = {}
    for table in sorted(tables):
        counts[table] = connection.execute(
            f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
        ).fetchone()[0]
    return counts


def _asset_reference(row: dict, ordinal: int) -> dict:
    reference = {"row": ordinal}
    if "id" in row:
        reference["id"] = row["id"]
    if "name" in row:
        reference["name"] = row["name"]
    return reference


def _normalize_mac(value: object) -> str | None:
    if value is None or not str(value).strip():
        return None
    compact = re.sub(r"[^0-9a-fA-F]", "", str(value))
    if len(compact) != 12 or not re.fullmatch(r"[0-9a-fA-F]{12}", compact):
        return ""
    return ":".join(compact[index:index + 2] for index in range(0, 12, 2)).upper()


def _normalize_ip(value: object) -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        return str(ipaddress.ip_address(str(value).strip()))
    except ValueError:
        return ""


def _duplicate_groups(values: dict[str, list[dict]]) -> list[dict]:
    return [
        {"value": value, "assets": references, "count": len(references)}
        for value, references in sorted(values.items())
        if len(references) > 1
    ]


def _analyze_legacy_assets(
    connection: sqlite3.Connection,
    table_names: set[str],
) -> dict:
    result = {
        "table_present": "assets" in table_names,
        "row_count": 0,
        "missing_columns": [],
        "missing_mac": [],
        "invalid_mac": [],
        "duplicate_mac": [],
        "missing_ip": [],
        "invalid_ip": [],
        "duplicate_ip": [],
        "automatic_import": False,
        "note": (
            "Legacy assets are audit-only; no row will be imported until stable "
            "device_id and physical identity are confirmed on site."
        ),
    }
    if "assets" not in table_names:
        return result

    columns = {
        row["name"]
        for row in connection.execute('PRAGMA table_info("assets")').fetchall()
    }
    result["missing_columns"] = sorted(
        column for column in ("mac_address", "ip_address") if column not in columns
    )
    selected = [
        column
        for column in ("id", "name", "mac_address", "ip_address")
        if column in columns
    ]
    if not selected:
        return result

    projection = ", ".join(_quote_identifier(column) for column in selected)
    rows = [dict(row) for row in connection.execute(f'SELECT {projection} FROM "assets"')]
    result["row_count"] = len(rows)
    mac_groups: dict[str, list[dict]] = {}
    ip_groups: dict[str, list[dict]] = {}

    for ordinal, row in enumerate(rows, start=1):
        reference = _asset_reference(row, ordinal)
        if "mac_address" in columns:
            normalized_mac = _normalize_mac(row.get("mac_address"))
            if normalized_mac is None:
                result["missing_mac"].append(reference)
            elif normalized_mac == "":
                result["invalid_mac"].append(
                    {**reference, "value": row.get("mac_address")}
                )
            else:
                mac_groups.setdefault(normalized_mac, []).append(reference)
        if "ip_address" in columns:
            normalized_ip = _normalize_ip(row.get("ip_address"))
            if normalized_ip is None:
                result["missing_ip"].append(reference)
            elif normalized_ip == "":
                result["invalid_ip"].append(
                    {**reference, "value": row.get("ip_address")}
                )
            else:
                ip_groups.setdefault(normalized_ip, []).append(reference)

    result["duplicate_mac"] = _duplicate_groups(mac_groups)
    result["duplicate_ip"] = _duplicate_groups(ip_groups)
    return result


def _migration_plan(connection: sqlite3.Connection, objects: list[dict]) -> dict:
    applied = read_applied_migrations(connection)
    applied_by_version = {item["version"]: item for item in applied}
    pending = []
    ledger_issues = []
    for migration in V3_MIGRATIONS:
        existing = applied_by_version.get(migration.version)
        if not existing:
            pending.append(
                {
                    "version": migration.version,
                    "name": migration.name,
                    "checksum": migration.checksum,
                }
            )
        elif (
            existing["name"] != migration.name
            or existing["checksum"] != migration.checksum
        ):
            ledger_issues.append(
                {
                    "version": migration.version,
                    "expected_name": migration.name,
                    "recorded_name": existing["name"],
                    "expected_checksum": migration.checksum,
                    "recorded_checksum": existing["checksum"],
                }
            )

    existing_objects = {item["name"]: item["type"] for item in objects}
    object_actions = []
    for name, expected_type in sorted(V3_EXPECTED_OBJECTS.items()):
        actual_type = existing_objects.get(name)
        if actual_type is None:
            action = "create"
        elif actual_type == expected_type:
            action = "already_present"
        else:
            action = "type_conflict"
        object_actions.append(
            {
                "name": name,
                "type": expected_type,
                "action": action,
                "actual_type": actual_type,
            }
        )

    schema_version = current_v3_schema_version(connection)
    schema_drift = []
    if schema_version >= 1:
        schema_drift = [
            item
            for item in object_actions
            if V3_EXPECTED_OBJECT_VERSIONS[item["name"]] <= schema_version
            and item["action"] != "already_present"
        ]
    return {
        "current_schema_version": schema_version,
        "applied_migrations": applied,
        "pending_migrations": pending,
        "migration_ledger_issues": ledger_issues,
        "object_actions": object_actions,
        "schema_drift": schema_drift,
    }


def _inspect_connection(path: Path, connection: sqlite3.Connection) -> dict:
    integrity = _integrity_check(connection)
    try:
        objects = _schema_objects(connection)
        table_names = {
            item["name"] for item in objects if item["type"] == "table"
        }
        old_tables = sorted(name for name in table_names if not name.startswith("v3_"))
        v3_tables = sorted(name for name in table_names if name.startswith("v3_"))
        row_counts = _table_row_counts(connection, table_names)
        migration = _migration_plan(connection, objects)
        assets = _analyze_legacy_assets(connection, table_names)
    except sqlite3.Error as exc:
        raise InvalidDatabaseError(f"cannot inspect SQLite schema: {exc}") from exc

    return {
        "operation": "plan",
        "database": str(path),
        "integrity_check": integrity,
        "integrity_ok": integrity == ["ok"],
        "old_tables": old_tables,
        "v3_tables": v3_tables,
        "row_counts": row_counts,
        **migration,
        "legacy_asset_identity_audit": assets,
        "automatic_legacy_asset_import": False,
    }


def plan_database(database_path: str | Path) -> dict:
    """Build a strict read-only plan using SQLite immutable mode."""
    path = _resolve_existing_database(database_path)
    sidecars = _strict_plan_sidecars(path)
    if sidecars:
        raise InputPathError(
            "strict read-only plan refuses databases with WAL/journal sidecars; "
            "create a consistent SQLite backup copy first: " + ", ".join(sidecars)
        )
    before = _file_fingerprint(path)
    connection = _connect_existing(path, mode="ro", immutable=True)
    try:
        report = _inspect_connection(path, connection)
    finally:
        connection.close()
    after_sidecars = _strict_plan_sidecars(path)
    after = _file_fingerprint(path)
    if after_sidecars or after != before:
        raise InputPathError(
            "database changed while plan was running; discard the result and "
            "use an offline SQLite backup copy"
        )
    report["database_file"] = before
    return report


def _inspect_for_apply(path: Path) -> dict:
    connection = _connect_existing(path, mode="ro")
    try:
        return _inspect_connection(path, connection)
    finally:
        connection.close()


def _backup_name(source: Path) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{source.stem}-before-v3-{timestamp}-{uuid4().hex[:8]}.sqlite"


def create_verified_backup(
    database_path: str | Path,
    backup_directory: str | Path,
) -> dict:
    """Create a WAL-aware backup with SQLite's online backup API and verify it."""
    source_path = _resolve_existing_database(database_path)
    backup_dir = _resolve_backup_directory(backup_directory)
    final_path = backup_dir / _backup_name(source_path)
    partial_path = final_path.with_suffix(final_path.suffix + ".partial")
    if final_path.exists() or partial_path.exists():
        raise BackupError(f"refusing to overwrite backup path: {final_path}")

    source = None
    destination = None
    try:
        source = _connect_existing(source_path, mode="ro")
        source_integrity = _integrity_check(source)
        if source_integrity != ["ok"]:
            raise IntegrityCheckError(
                "source integrity_check failed before backup: "
                + "; ".join(source_integrity)
            )
        destination = sqlite3.connect(str(partial_path))
        source.backup(destination)
        destination.close()
        destination = None
        source.close()
        source = None

        backup_path = _resolve_existing_database(partial_path)
        verification = _connect_existing(backup_path, mode="ro", immutable=True)
        try:
            backup_integrity = _integrity_check(verification)
        finally:
            verification.close()
        if backup_integrity != ["ok"]:
            raise BackupError(
                "backup integrity_check failed: " + "; ".join(backup_integrity)
            )
        partial_path.replace(final_path)
    except UpgradeError:
        if partial_path.exists():
            partial_path.unlink()
        raise
    except (OSError, sqlite3.Error) as exc:
        if partial_path.exists():
            partial_path.unlink()
        raise BackupError(f"SQLite backup failed: {exc}") from exc
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()

    return {
        "path": str(final_path),
        "source_integrity_check": source_integrity,
        "backup_integrity_check": backup_integrity,
        "method": "sqlite_backup_api",
    }


def backup_database(
    database_path: str | Path,
    backup_directory: str | Path,
) -> dict:
    path = _resolve_existing_database(database_path)
    backup = create_verified_backup(path, backup_directory)
    return {
        "operation": "backup",
        "database": str(path),
        "backup": backup,
        "schema_modified": False,
    }


def apply_upgrade(
    database_path: str | Path,
    backup_directory: str | Path,
    *,
    migrations: Iterable[SchemaMigration] = V3_MIGRATIONS,
) -> dict:
    """Back up an existing database, then atomically apply pending v3 schema."""
    path = _resolve_existing_database(database_path)
    backup_dir = _resolve_backup_directory(backup_directory)
    before = _inspect_for_apply(path)
    if not before["integrity_ok"]:
        raise IntegrityCheckError(
            "source integrity_check failed before migration: "
            + "; ".join(before["integrity_check"])
        )
    if before["migration_ledger_issues"]:
        raise UpgradeMigrationError("migration ledger checksum/name mismatch")
    type_conflicts = [
        item for item in before["object_actions"] if item["action"] == "type_conflict"
    ]
    if type_conflicts:
        raise UpgradeMigrationError(
            "schema object type conflict: "
            + ", ".join(item["name"] for item in type_conflicts)
        )
    if before["schema_drift"]:
        raise UpgradeMigrationError(
            "applied migration schema is incomplete or has object type conflicts"
        )

    backup = create_verified_backup(path, backup_dir)
    connection = _connect_existing(path, mode="rw")
    try:
        try:
            migration_result = apply_v3_migrations(connection, migrations)
        except Exception as exc:
            raise UpgradeMigrationError(
                f"migration failed and was rolled back; verified backup retained at "
                f"{backup['path']}: {exc}"
            ) from exc
    finally:
        connection.close()

    after = _inspect_for_apply(path)
    if not after["integrity_ok"]:
        raise IntegrityCheckError(
            "source integrity_check failed after migration; restore verified backup: "
            + backup["path"]
        )
    return {
        "operation": "apply",
        "database": str(path),
        "backup": backup,
        "pre_integrity_check": before["integrity_check"],
        "post_integrity_check": after["integrity_check"],
        "previous_schema_version": before["current_schema_version"],
        "current_schema_version": after["current_schema_version"],
        "applied_versions": migration_result["applied_versions"],
        "skipped_versions": migration_result["skipped_versions"],
        "old_tables": after["old_tables"],
        "v3_tables": after["v3_tables"],
        "row_counts": after["row_counts"],
        "legacy_asset_identity_audit": after["legacy_asset_identity_audit"],
        "automatic_legacy_asset_import": False,
    }


def _format_names(values: list[str]) -> str:
    return ", ".join(values) if values else "（无）"


def _render_identity_audit(audit: dict) -> list[str]:
    if not audit["table_present"]:
        return [
            "旧 assets 身份审计：未发现 assets 表",
            "旧资产自动导入：否（本轮只创建并登记 v3 schema）",
        ]
    lines = [
        f"旧 assets 行数：{audit['row_count']}",
        "身份问题："
        f"缺失 MAC {len(audit['missing_mac'])}，"
        f"无效 MAC {len(audit['invalid_mac'])}，"
        f"重复 MAC 组 {len(audit['duplicate_mac'])}，"
        f"缺失 IP {len(audit['missing_ip'])}，"
        f"无效 IP {len(audit['invalid_ip'])}，"
        f"重复 IP 组 {len(audit['duplicate_ip'])}",
        "旧资产自动导入：否（稳定 device_id 和真实身份需现场确认）",
    ]
    if audit["missing_columns"]:
        lines.append("assets 缺少列：" + ", ".join(audit["missing_columns"]))
    for label, groups in (
        ("重复 MAC", audit["duplicate_mac"]),
        ("重复 IP", audit["duplicate_ip"]),
    ):
        for group in groups:
            references = ", ".join(
                str(item.get("id", item["row"])) for item in group["assets"]
            )
            lines.append(f"{label}：{group['value']} -> 资产 {references}")
    return lines


def render_human(report: dict) -> str:
    operation = report["operation"]
    if operation == "plan":
        lines = [
            "v3 数据库升级计划（严格只读）",
            f"数据库：{report['database']}",
            f"文件 SHA-256：{report['database_file']['sha256']}",
            f"SQLite 完整性：{'; '.join(report['integrity_check'])}",
            f"当前 v3 schema migration 版本：{report['current_schema_version']}",
            f"旧表：{_format_names(report['old_tables'])}",
            f"v3 表：{_format_names(report['v3_tables'])}",
            "相关表行数："
            + (", ".join(f"{name}={count}" for name, count in report["row_counts"].items()) or "（无）"),
            "待执行迁移："
            + (
                ", ".join(
                    f"v{item['version']} {item['name']}"
                    for item in report["pending_migrations"]
                )
                or "（无）"
            ),
            "对象计划："
            + ", ".join(
                f"{item['name']}={item['action']}"
                for item in report["object_actions"]
            ),
        ]
        if report["migration_ledger_issues"]:
            lines.append(
                f"迁移登记冲突：{len(report['migration_ledger_issues'])} 项（禁止 apply）"
            )
        if report["schema_drift"]:
            lines.append(
                "已登记 schema 缺失/冲突对象："
                + ", ".join(item["name"] for item in report["schema_drift"])
            )
        lines.extend(_render_identity_audit(report["legacy_asset_identity_audit"]))
        return "\n".join(lines)

    if operation == "backup":
        return "\n".join(
            [
                "SQLite 一致性备份完成",
                f"源数据库：{report['database']}",
                f"备份文件：{report['backup']['path']}",
                "备份方法：SQLite backup API（兼容 WAL）",
                f"备份完整性：{'; '.join(report['backup']['backup_integrity_check'])}",
                "源 schema 修改：否",
            ]
        )

    lines = [
        "v3 数据库升级完成",
        f"数据库：{report['database']}",
        f"备份文件：{report['backup']['path']}",
        f"修改前完整性：{'; '.join(report['pre_integrity_check'])}",
        f"修改后完整性：{'; '.join(report['post_integrity_check'])}",
        f"schema 版本：{report['previous_schema_version']} -> {report['current_schema_version']}",
        f"已执行迁移：{report['applied_versions'] or '（无）'}",
        f"已跳过迁移：{report['skipped_versions'] or '（无）'}",
        "旧资产自动导入：否",
    ]
    lines.extend(_render_identity_audit(report["legacy_asset_identity_audit"]))
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview, back up, and apply the additive IoT-IDS v3 SQLite schema."
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)

    plan = subparsers.add_parser("plan", help="strict read-only schema and identity audit")
    plan.add_argument("--database", required=True, help="existing SQLite database path")
    plan.add_argument("--json", action="store_true", help="emit JSON")

    backup = subparsers.add_parser("backup", help="create a verified SQLite backup copy")
    backup.add_argument("--database", required=True, help="existing SQLite database path")
    backup.add_argument("--backup-directory", required=True, help="existing backup directory")
    backup.add_argument("--json", action="store_true", help="emit JSON")

    apply_parser = subparsers.add_parser("apply", help="back up and apply pending v3 migrations")
    apply_parser.add_argument("--database", required=True, help="existing SQLite database path")
    apply_parser.add_argument("--backup-directory", required=True, help="existing backup directory")
    apply_parser.add_argument("--json", action="store_true", help="emit JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.operation == "plan":
            report = plan_database(args.database)
        elif args.operation == "backup":
            report = backup_database(args.database, args.backup_directory)
        else:
            report = apply_upgrade(args.database, args.backup_directory)
    except UpgradeError as exc:
        error = {
            "error": {
                "code": exc.__class__.__name__,
                "message": str(exc),
                "exit_code": exc.exit_code,
            }
        }
        if getattr(args, "json", False):
            print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        else:
            print(f"错误（退出码 {exc.exit_code}）：{exc}", file=sys.stderr)
        return exc.exit_code

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_human(report))
    if not report.get("integrity_ok", True):
        return EXIT_DATABASE
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
