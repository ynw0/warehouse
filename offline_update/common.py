#!/usr/bin/env python3
"""Shared primitives for the warehouse offline updater.

This module deliberately imports no application entry point. Importing app.py
initializes the database and background services, which is unsafe for precheck.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parents[1]
APP_PAYLOAD_DIR = PACKAGE_DIR / "app"
MANIFEST_PATH = PACKAGE_DIR / "package_manifest.json"
CHECKSUMS_PATH = PACKAGE_DIR / "checksums.sha256"
LOCK_NAME = ".upgrade.lock"
STATE_NAME = ".upgrade-state.json"

COUNT_TABLES = (
    "materials",
    "material_batches",
    "inventory",
    "stock_records",
    "workflow_forms",
    "workflow_items",
    "borrow_records",
    "notifications",
    "temporary_issue_obligations",
    "inventory_transfer_tasks",
    "inventory_transfer_items",
    "inventory_transfer_obligations",
    "transfer_acceptance_links",
    "inventory_reservations",
    "transfer_auto_claims",
    "transfer_auto_claim_obligations",
    "audit_logs",
)


class UpgradeError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def load_env_file(path):
    values = {}
    if not path:
        return values
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise UpgradeError(f"配置文件不存在: {source}")
    for line_number, raw in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise UpgradeError(f"配置文件第 {line_number} 行缺少等号")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def setting(config, name, default="", required=False):
    value = str(config.get(name, os.environ.get(name, default)) or "").strip()
    if required and not value:
        raise UpgradeError(f"缺少必填升级配置: {name}")
    return value


def safe_path(value, name, must_exist=False, preserve_symlink=False):
    candidate = Path(value).expanduser()
    path = Path(os.path.abspath(candidate)) if preserve_symlink else candidate.resolve()
    safety_target = path.resolve()
    if str(safety_target) in {"/", str(Path.home().resolve())}:
        raise UpgradeError(f"{name} 不能指向根目录或用户主目录: {path}")
    if must_exist and not path.exists():
        raise UpgradeError(f"{name} 不存在: {path}")
    return path


def load_manifest():
    if not MANIFEST_PATH.is_file():
        raise UpgradeError("更新包缺少 package_manifest.json")
    manifest = read_json(MANIFEST_PATH)
    if not manifest.get("target_application_version"):
        raise UpgradeError("更新包清单缺少目标版本")
    return manifest


def verify_package_checksums():
    if not CHECKSUMS_PATH.is_file():
        raise UpgradeError("更新包缺少 checksums.sha256")
    checked = 0
    for line in CHECKSUMS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        expected, relative = line.split("  ", 1)
        target = (PACKAGE_DIR / relative).resolve()
        try:
            target.relative_to(PACKAGE_DIR.resolve())
        except ValueError as exc:
            raise UpgradeError(f"校验清单路径越界: {relative}") from exc
        if not target.is_file():
            raise UpgradeError(f"更新包文件缺失: {relative}")
        actual = sha256_file(target)
        if actual != expected:
            raise UpgradeError(f"更新包文件校验失败: {relative}")
        checked += 1
    if checked == 0:
        raise UpgradeError("checksums.sha256 为空")
    return checked


def read_version(app_dir):
    app_dir = Path(app_dir)
    version_file = app_dir / "VERSION"
    if version_file.is_file():
        return version_file.read_text(encoding="utf-8").strip()
    init_file = app_dir / "warehouse_suit" / "__init__.py"
    if init_file.is_file():
        match = re.search(r"__version__\s*=\s*[\"']([^\"']+)", init_file.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    project = app_dir / "pyproject.toml"
    if project.is_file():
        match = re.search(r"^version\s*=\s*[\"']([^\"']+)", project.read_text(encoding="utf-8"), re.M)
        if match:
            return match.group(1)
    raise UpgradeError(f"无法识别当前应用版本: {app_dir}")


def sqlite_connection(path, readonly=False):
    path = Path(path).resolve()
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    else:
        conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def database_checks(path):
    conn = sqlite_connection(path, readonly=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
        migrations = []
        if table_exists(conn, "schema_migrations"):
            migrations = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        return {"integrity_check": integrity, "foreign_key_check": foreign_keys, "migrations": migrations}
    finally:
        conn.close()


def table_exists(conn, table):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def column_exists(conn, table, column):
    if not table_exists(conn, table):
        return False
    return column in {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def scalar(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else 0


def database_metrics(path):
    conn = sqlite_connection(path, readonly=True)
    try:
        metrics = {}
        for table in COUNT_TABLES:
            metrics[f"count.{table}"] = int(scalar(conn, f'SELECT COUNT(*) FROM "{table}"')) if table_exists(conn, table) else 0
        if table_exists(conn, "material_batches"):
            if column_exists(conn, "material_batches", "stock_source"):
                for source in ("formal", "temporary"):
                    metrics[f"batch_count.{source}"] = int(
                        scalar(conn, "SELECT COUNT(*) FROM material_batches WHERE stock_source = ?", (source,))
                    )
                    metrics[f"batch_quantity.{source}"] = float(
                        scalar(conn, "SELECT COALESCE(SUM(quantity), 0) FROM material_batches WHERE stock_source = ?", (source,))
                    )
            else:
                metrics["batch_count.formal"] = int(scalar(conn, "SELECT COUNT(*) FROM material_batches"))
                metrics["batch_quantity.formal"] = float(scalar(conn, "SELECT COALESCE(SUM(quantity), 0) FROM material_batches"))
                metrics["batch_count.temporary"] = 0
                metrics["batch_quantity.temporary"] = 0.0
            if column_exists(conn, "material_batches", "inventory_status"):
                for status in ("available", "transfer_locked", "transferred"):
                    metrics[f"batch_status_count.{status}"] = int(
                        scalar(conn, "SELECT COUNT(*) FROM material_batches WHERE inventory_status = ?", (status,))
                    )
            else:
                metrics["batch_status_count.available"] = int(scalar(conn, "SELECT COUNT(*) FROM material_batches"))
                metrics["batch_status_count.transfer_locked"] = 0
                metrics["batch_status_count.transferred"] = 0
        if table_exists(conn, "inventory"):
            metrics["inventory.quantity"] = float(scalar(conn, "SELECT COALESCE(SUM(quantity), 0) FROM inventory"))
        if table_exists(conn, "temporary_issue_obligations"):
            metrics["obligation.issued"] = float(scalar(conn, "SELECT COALESCE(SUM(issued_quantity), 0) FROM temporary_issue_obligations"))
            metrics["obligation.settled"] = float(scalar(conn, "SELECT COALESCE(SUM(settled_quantity), 0) FROM temporary_issue_obligations"))
        return metrics
    finally:
        conn.close()


def assert_business_metrics_unchanged(before, after):
    ignored_prefixes = (
        "count.inventory_transfer_",
        "count.transfer_acceptance_links",
        "count.transfer_auto_claim",
        "count.temporary_issue_obligations",
        "count.inventory_reservations",
    )
    differences = {}
    for key, value in before.items():
        if key.startswith(ignored_prefixes) and value == 0:
            continue
        if after.get(key) != value:
            differences[key] = {"before": value, "after": after.get(key)}
    if differences:
        raise UpgradeError(f"数据库迁移前后关键数据不一致: {json.dumps(differences, ensure_ascii=False)}")
    return differences


def checkpoint_database(path):
    conn = sqlite_connection(path)
    try:
        result = tuple(conn.execute("PRAGMA wal_checkpoint(FULL)").fetchone())
        conn.commit()
        if result and int(result[0]) != 0:
            raise UpgradeError(f"WAL checkpoint 未完成: {result}")
        return result
    finally:
        conn.close()


def sqlite_backup(source, destination):
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise UpgradeError(f"备份文件已存在，拒绝覆盖: {destination}")
    src = sqlite_connection(source, readonly=True)
    dst = sqlite_connection(destination)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    checks = database_checks(destination)
    if checks["integrity_check"] != "ok" or checks["foreign_key_check"]:
        raise UpgradeError(f"数据库备份完整性检查失败: {destination}")
    return {
        "path": str(destination),
        "size": destination.stat().st_size,
        "sha256": sha256_file(destination),
        **checks,
    }


def restore_sqlite_backup(backup, destination):
    checks = database_checks(backup)
    if checks["integrity_check"] != "ok" or checks["foreign_key_check"]:
        raise UpgradeError("拒绝恢复损坏的数据库备份")
    destination = Path(destination).resolve()
    failed_copy = destination.with_name(destination.name + f".failed_{timestamp()}")
    if destination.exists():
        sqlite_backup(destination, failed_copy)
    temporary = destination.with_name(destination.name + ".restore_tmp")
    if temporary.exists():
        temporary.unlink()
    sqlite_backup(backup, temporary)
    os.replace(temporary, destination)
    return {"restored": str(destination), "failed_copy": str(failed_copy) if failed_copy.exists() else ""}


def run_migrations(db_path, app_dir=APP_PAYLOAD_DIR):
    app_dir = str(Path(app_dir).resolve())
    sys.path.insert(0, app_dir)
    try:
        from warehouse_suit.migrations import available_migrations, run_migrations as application_migrations

        expected = [str(item["version"]) for item in available_migrations()]
        conn = sqlite_connection(db_path)
        try:
            applied = application_migrations(conn)
        finally:
            conn.close()
        return {"expected": expected, "applied": applied}
    finally:
        try:
            sys.path.remove(app_dir)
        except ValueError:
            pass


def migrate_database_copy(source_db, work_dir):
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    copy_path = work_dir / f"migration_rehearsal_{timestamp()}.db"
    backup_info = sqlite_backup(source_db, copy_path)
    before = database_metrics(copy_path)
    first = run_migrations(copy_path)
    after_first = database_metrics(copy_path)
    second = run_migrations(copy_path)
    after_second = database_metrics(copy_path)
    assert_business_metrics_unchanged(before, after_first)
    if after_first != after_second:
        raise UpgradeError("迁移第二次执行改变了数据库数据")
    checks = database_checks(copy_path)
    if checks["integrity_check"] != "ok" or checks["foreign_key_check"]:
        raise UpgradeError("数据库副本迁移后的完整性检查失败")
    if sorted(first["expected"]) != sorted(checks["migrations"]):
        raise UpgradeError("数据库副本没有包含全部目标迁移")
    return {
        "copy": str(copy_path),
        "backup": backup_info,
        "before": before,
        "after": after_first,
        "first": first,
        "second": second,
        "checks": checks,
    }


def run_command(command, name, cwd=None, allow_empty=False):
    command = str(command or "").strip()
    if not command:
        if allow_empty:
            return {"skipped": True}
        raise UpgradeError(f"未配置{name}命令")
    completed = subprocess.run(command, shell=True, cwd=cwd, text=True, capture_output=True)
    result = {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-8000:],
    }
    if completed.returncode:
        raise UpgradeError(f"{name}失败，退出码 {completed.returncode}: {completed.stderr[-1000:]}")
    return result


def disk_space_check(path, required_bytes):
    usage = shutil.disk_usage(path)
    if usage.free < required_bytes:
        raise UpgradeError(f"磁盘空间不足，需要 {required_bytes} 字节，可用 {usage.free} 字节")
    return {"required": required_bytes, "free": usage.free, "total": usage.total}


def copy_application_release(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        if read_version(destination) == read_version(APP_PAYLOAD_DIR):
            return {"path": str(destination), "existing": True}
        raise UpgradeError(f"目标 release 目录已存在且版本不匹配: {destination}")
    shutil.copytree(APP_PAYLOAD_DIR, destination, symlinks=True)
    return {"path": str(destination), "existing": False}


def install_offline_dependencies(release_dir, python=sys.executable):
    release_dir = Path(release_dir).resolve()
    venv = release_dir / ".venv"
    if not venv.exists():
        subprocess.run([python, "-m", "venv", str(venv)], check=True)
    venv_python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    command = [
        str(venv_python),
        "-m",
        "pip",
        "install",
        "--no-index",
        "--find-links",
        str(PACKAGE_DIR / "wheels"),
        "-r",
        str(release_dir / "requirements-runtime.txt"),
    ]
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode:
        raise UpgradeError(f"离线依赖安装失败: {completed.stderr[-2000:]}")
    return {"python": str(venv_python), "stdout": completed.stdout[-8000:]}


def compile_release(release_dir, python):
    completed = subprocess.run(
        [str(python), "-m", "compileall", "-q", "warehouse_suit"],
        cwd=release_dir,
        text=True,
        capture_output=True,
    )
    if completed.returncode:
        raise UpgradeError(f"新版本 Python 编译检查失败: {completed.stderr[-2000:]}")
    return True


def switch_current_link(current_link, target):
    current_link = Path(current_link)
    target = Path(target).resolve()
    current_link.parent.mkdir(parents=True, exist_ok=True)
    previous_target = ""
    if current_link.is_symlink():
        previous_target = str(current_link.resolve())
    elif current_link.exists():
        raise UpgradeError("current 必须是符号链接；请先按升级指南完成 release 目录准备")
    temporary = current_link.with_name(current_link.name + ".new")
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(target, target_is_directory=True)
    os.replace(temporary, current_link)
    return previous_target


@contextmanager
def upgrade_lock(root, current_version, target_version, action):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / LOCK_NAME
    payload = {
        "pid": os.getpid(),
        "operator": os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
        "hostname": socket.gethostname(),
        "current_version": current_version,
        "target_version": target_version,
        "started_at": utc_now(),
        "step": action,
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        existing = path.read_text(encoding="utf-8", errors="replace")
        raise UpgradeError(f"检测到升级锁，禁止并发执行。请人工核实后处理: {existing}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    succeeded = False
    try:
        yield path
        succeeded = True
    except Exception:
        payload["step"] = "failed-requires-operator-review"
        payload["failed_at"] = utc_now()
        write_json(path, payload)
        raise
    finally:
        if succeeded and path.exists():
            path.unlink()


def precheck(config, args):
    manifest = load_manifest()
    checked_files = verify_package_checksums()
    app_root = safe_path(setting(config, "APP_ROOT", required=True), "APP_ROOT", must_exist=True)
    lock_path = app_root / LOCK_NAME
    if lock_path.exists():
        existing = lock_path.read_text(encoding="utf-8", errors="replace")
        raise UpgradeError(f"upgrade lock exists; operator review required: {existing}")
    db_path = safe_path(setting(config, "DB_PATH", required=True), "DB_PATH", must_exist=True)
    allow_test_paths = setting(config, "ALLOW_TEST_PATHS", "0").lower() in {"1", "true", "yes"}
    if not allow_test_paths and str(db_path).startswith(("/tmp/", "/var/tmp/")):
        raise UpgradeError("production upgrade refuses a database path in a temporary directory")
    backup_dir = safe_path(setting(config, "BACKUP_DIR", required=True), "BACKUP_DIR")
    report_dir = safe_path(setting(config, "REPORT_DIR", str(app_root / "reports")), "REPORT_DIR")
    releases_dir = safe_path(setting(config, "RELEASES_DIR", str(app_root / "releases")), "RELEASES_DIR")
    current_link = safe_path(setting(config, "CURRENT_LINK", str(app_root / "current")), "CURRENT_LINK", preserve_symlink=True)
    current_app = current_link.resolve() if current_link.is_symlink() else current_link
    current_version = read_version(current_app)
    allowed = set(manifest.get("supported_from_versions") or [])
    target = manifest["target_application_version"]
    if current_version != target and current_version not in allowed:
        raise UpgradeError(f"当前版本 {current_version} 不在支持升级范围 {sorted(allowed)}")
    if platform.system() not in manifest.get("operating_systems", [platform.system()]):
        raise UpgradeError(f"操作系统不兼容: {platform.system()}")
    if platform.machine() not in manifest.get("cpu_architectures", [platform.machine()]):
        raise UpgradeError(f"CPU 架构不兼容: {platform.machine()}")
    minimum_python = tuple(manifest.get("minimum_python", [3, 8]))
    if sys.version_info[:2] < minimum_python:
        raise UpgradeError(f"Python 版本过低: {platform.python_version()}")
    wheel_python = tuple(manifest.get("wheel_python") or ())
    if wheel_python and sys.version_info[:2] != wheel_python:
        raise UpgradeError(
            f"offline wheelhouse requires Python {wheel_python[0]}.{wheel_python[1]}.x; "
            f"current version is {platform.python_version()}"
        )
    checks = database_checks(db_path)
    if checks["integrity_check"] != "ok" or checks["foreign_key_check"]:
        raise UpgradeError("当前数据库完整性检查失败")
    expected_migrations = set(manifest.get("migration_versions") or [])
    unknown = set(checks["migrations"]) - expected_migrations
    if unknown:
        raise UpgradeError(f"数据库存在更新包未知的迁移版本: {sorted(unknown)}")
    for directory in (backup_dir, report_dir, releases_dir):
        directory.mkdir(parents=True, exist_ok=True)
        if not os.access(directory, os.W_OK):
            raise UpgradeError(f"目录不可写: {directory}")
    required = db_path.stat().st_size * 4 + sum(p.stat().st_size for p in PACKAGE_DIR.rglob("*") if p.is_file()) * 2 + 100 * 1024 * 1024
    disk = disk_space_check(app_root, required)
    result = {
        "manifest": manifest,
        "checked_files": checked_files,
        "app_root": str(app_root),
        "current_app": str(current_app),
        "current_link": str(current_link),
        "current_version": current_version,
        "target_version": target,
        "db_path": str(db_path),
        "backup_dir": str(backup_dir),
        "report_dir": str(report_dir),
        "releases_dir": str(releases_dir),
        "database_checks": checks,
        "database_metrics": database_metrics(db_path),
        "disk": disk,
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "already_target": current_version == target,
        "protected_paths": [
            line.strip()
            for line in (PACKAGE_DIR / "config" / "protected-paths.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ],
    }
    if args.dry_run or args.precheck_only:
        result["migration_rehearsal"] = migrate_database_copy(db_path, report_dir / "dry-run")
    return result


def write_report(report_dir, prefix, report):
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp()
    json_path = report_dir / f"{prefix}-{stamp}.json"
    md_path = report_dir / f"{prefix}-{stamp}.md"
    write_json(json_path, report)
    lines = [f"# {prefix}", "", f"- generated_at: {report.get('generated_at', utc_now())}", f"- status: {report.get('status', 'unknown')}", "", "```json", json.dumps(report, ensure_ascii=False, indent=2), "```", ""]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}
