#!/usr/bin/env python3
"""Safe, report-producing warehouse application upgrade orchestration."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from common import (
    APP_PAYLOAD_DIR,
    PACKAGE_DIR,
    STATE_NAME,
    UpgradeError,
    assert_business_metrics_unchanged,
    checkpoint_database,
    compile_release,
    copy_application_release,
    database_checks,
    database_metrics,
    install_offline_dependencies,
    load_env_file,
    migrate_database_copy,
    precheck,
    restore_sqlite_backup,
    run_command,
    run_migrations,
    setting,
    sha256_file,
    sqlite_backup,
    switch_current_link,
    timestamp,
    upgrade_lock,
    utc_now,
    write_json,
    write_report,
)


def parser():
    result = argparse.ArgumentParser(description="Warehouse offline updater")
    result.add_argument("--config-file", required=True)
    result.add_argument("--app-dir", dest="APP_ROOT")
    result.add_argument("--db-path", dest="DB_PATH")
    result.add_argument("--backup-dir", dest="BACKUP_DIR")
    result.add_argument("--package-dir")
    result.add_argument("--target-version")
    result.add_argument("--service-name", dest="SERVICE_NAME")
    result.add_argument("--report-dir", dest="REPORT_DIR")
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--precheck-only", action="store_true")
    result.add_argument("--skip-service-control", action="store_true")
    result.add_argument("--maintenance-mode", action="store_true")
    result.add_argument("--no-color", action="store_true")
    return result


def build_config(args):
    config = load_env_file(args.config_file)
    for name in ("APP_ROOT", "DB_PATH", "BACKUP_DIR", "SERVICE_NAME", "REPORT_DIR"):
        value = getattr(args, name, None)
        if value:
            config[name] = value
    return config


def code_backup(current_app, backup_dir, current_version):
    destination = Path(backup_dir) / f"warehouse_code_{current_version}_{timestamp()}"
    ignored = shutil.ignore_patterns(
        ".git", ".env", "data", "uploads", "attachments", "logs", "backups",
        "__pycache__", ".pytest_cache", "node_modules", "codex_work", "*.db", "*.db-wal", "*.db-shm",
    )
    shutil.copytree(current_app, destination, symlinks=True, ignore=ignored)
    files = []
    for path in sorted(destination.rglob("*")):
        if path.is_file():
            files.append({"path": str(path.relative_to(destination)), "sha256": sha256_file(path), "size": path.stat().st_size})
    return {"path": str(destination), "files": files, "file_count": len(files)}


def http_health(url):
    if not url:
        raise UpgradeError("正式升级必须配置 HEALTH_URL")
    from urllib.request import Request, urlopen

    request = Request(url, method="GET", headers={"User-Agent": "warehouse-offline-updater"})
    with urlopen(request, timeout=15) as response:
        body = response.read(4096).decode("utf-8", errors="replace")
        if response.status >= 400:
            raise UpgradeError(f"健康检查失败: HTTP {response.status}")
    return {"url": url, "status": response.status, "body_prefix": body[:500]}


def main(argv=None):
    args = parser().parse_args(argv)
    if args.package_dir and Path(args.package_dir).resolve() != PACKAGE_DIR.resolve():
        raise UpgradeError("--package-dir 必须指向当前已校验更新包")
    config = build_config(args)
    report = {"generated_at": utc_now(), "status": "started", "mode": "dry-run" if args.dry_run else "upgrade", "steps": []}
    db_backup = None
    service_stopped = False
    switched = False
    previous_target = ""
    try:
        checked = precheck(config, args)
        report["precheck"] = checked
        report["steps"].append("precheck")
        report_dir = Path(checked["report_dir"])
        if args.precheck_only or args.dry_run:
            report["status"] = "dry_run_passed" if args.dry_run else "precheck_passed"
            paths = write_report(report_dir, "upgrade-dry-run-report", report)
            print(json.dumps({"status": report["status"], "reports": paths}, ensure_ascii=False))
            return 0
        if checked["already_target"]:
            report["status"] = "already_target_version"
            paths = write_report(report_dir, "upgrade-report", report)
            print(json.dumps({"status": report["status"], "reports": paths}, ensure_ascii=False))
            return 0

        app_root = Path(checked["app_root"])
        db_path = Path(checked["db_path"])
        backup_dir = Path(checked["backup_dir"])
        releases_dir = Path(checked["releases_dir"])
        current_link = Path(checked["current_link"])
        target = checked["target_version"]
        state_path = app_root / STATE_NAME
        with upgrade_lock(app_root, checked["current_version"], target, "upgrade"):
            if args.maintenance_mode:
                report["maintenance_enable"] = run_command(
                    setting(config, "MAINTENANCE_ENABLE_COMMAND"), "开启维护模式"
                )
                report["steps"].append("maintenance_enabled")
            if not args.skip_service_control:
                report["service_stop"] = run_command(setting(config, "SERVICE_STOP_COMMAND"), "停止服务")
                service_stopped = True
                report["steps"].append("service_stopped")
            report["checkpoint"] = checkpoint_database(db_path)
            report["steps"].append("wal_checkpoint")

            db_backup_path = backup_dir / f"warehouse_pre_upgrade_{checked['current_version']}_{timestamp()}.db"
            db_backup = sqlite_backup(db_path, db_backup_path)
            report["database_backup"] = db_backup
            report["code_backup"] = code_backup(checked["current_app"], backup_dir, checked["current_version"])
            report["steps"].append("backups_created")

            rehearsal = migrate_database_copy(db_backup_path, report_dir / "migration-rehearsal")
            report["migration_rehearsal"] = rehearsal
            report["steps"].append("migration_rehearsal")

            release_dir = releases_dir / target
            report["release_copy"] = copy_application_release(release_dir)
            report["dependency_install"] = install_offline_dependencies(release_dir, setting(config, "PYTHON", sys.executable))
            compile_release(release_dir, report["dependency_install"]["python"])
            report["steps"].append("release_prepared")

            state = {
                "created_at": utc_now(),
                "current_version": checked["current_version"],
                "target_version": target,
                "db_path": str(db_path),
                "db_backup": str(db_backup_path),
                "current_link": str(current_link),
                "previous_target": str(current_link.resolve()) if current_link.is_symlink() else "",
                "target_release": str(release_dir),
                "maintenance_mode": bool(args.maintenance_mode),
            }
            write_json(state_path, state)

            before_metrics = database_metrics(db_path)
            report["formal_migration"] = run_migrations(db_path, release_dir)
            after_metrics = database_metrics(db_path)
            assert_business_metrics_unchanged(before_metrics, after_metrics)
            checks = database_checks(db_path)
            if checks["integrity_check"] != "ok" or checks["foreign_key_check"]:
                raise UpgradeError("正式数据库迁移后完整性检查失败")
            report["database_before"] = before_metrics
            report["database_after"] = after_metrics
            report["database_checks"] = checks
            report["steps"].append("formal_migration")

            previous_target = switch_current_link(current_link, release_dir)
            switched = True
            state["previous_target"] = previous_target
            write_json(state_path, state)
            report["code_switch"] = {"current": str(current_link), "target": str(release_dir), "previous": previous_target}
            report["steps"].append("code_switched")

            if not args.skip_service_control:
                report["service_start"] = run_command(setting(config, "SERVICE_START_COMMAND"), "启动服务")
                service_stopped = False
                report["health"] = http_health(setting(config, "HEALTH_URL"))
                report["steps"].append("service_started_and_healthy")
            report["status"] = "upgrade_succeeded_maintenance_retained" if args.maintenance_mode else "upgrade_succeeded"
            paths = write_report(report_dir, "upgrade-report", report)
            print(json.dumps({"status": report["status"], "reports": paths, "state": str(state_path)}, ensure_ascii=False))
            return 0
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        try:
            checked = report.get("precheck") or {}
            app_root = Path(checked.get("app_root") or ".").resolve()
            report_dir = Path(checked.get("report_dir") or app_root / "reports")
            state_path = app_root / STATE_NAME
            if not args.dry_run and db_backup and Path(db_backup["path"]).is_file():
                report["automatic_database_restore"] = restore_sqlite_backup(db_backup["path"], checked["db_path"])
            if not args.dry_run and switched and previous_target:
                switch_current_link(checked["current_link"], previous_target)
                report["automatic_code_restore"] = previous_target
            if not args.dry_run and service_stopped and not args.skip_service_control:
                report["automatic_service_restart"] = run_command(
                    setting(config, "SERVICE_START_COMMAND"), "恢复旧服务"
                )
            paths = write_report(report_dir, "upgrade-report", report)
            print(json.dumps({"status": "failed", "error": report["error"], "reports": paths}, ensure_ascii=False), file=sys.stderr)
        except Exception as recovery_error:
            print(f"升级失败且自动恢复报告写入失败: {exc}; recovery={recovery_error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
