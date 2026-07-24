#!/usr/bin/env python3
"""Restore the complete pre-upgrade database and previous release."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import (
    STATE_NAME,
    UpgradeError,
    database_checks,
    database_metrics,
    load_env_file,
    read_json,
    restore_sqlite_backup,
    run_command,
    setting,
    switch_current_link,
    upgrade_lock,
    utc_now,
    write_report,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rollback warehouse offline update")
    parser.add_argument("--config-file", required=True)
    parser.add_argument("--state-file")
    parser.add_argument("--skip-service-control", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args(argv)
    config = load_env_file(args.config_file)
    app_root = Path(setting(config, "APP_ROOT", required=True)).resolve()
    state_path = Path(args.state_file).resolve() if args.state_file else app_root / STATE_NAME
    if not state_path.is_file():
        raise UpgradeError(f"找不到升级状态文件: {state_path}")
    state = read_json(state_path)
    report_dir = Path(setting(config, "REPORT_DIR", str(app_root / "reports"))).resolve()
    report = {"generated_at": utc_now(), "status": "started", "state": state}
    with upgrade_lock(app_root, state.get("target_version", "unknown"), state.get("current_version", "unknown"), "rollback"):
        if not args.skip_service_control:
            report["service_stop"] = run_command(setting(config, "SERVICE_STOP_COMMAND"), "停止新版本服务")
        db_path = Path(state["db_path"])
        report["failed_database_metrics"] = database_metrics(db_path)
        report["database_restore"] = restore_sqlite_backup(state["db_backup"], db_path)
        previous = state.get("previous_target")
        if not previous:
            raise UpgradeError("升级状态没有 previous_target，拒绝猜测旧代码位置")
        switch_current_link(state["current_link"], previous)
        report["code_restore"] = previous
        if not args.skip_service_control:
            report["service_start"] = run_command(setting(config, "SERVICE_START_COMMAND"), "启动旧版本服务")
        checks = database_checks(db_path)
        if checks["integrity_check"] != "ok" or checks["foreign_key_check"]:
            raise UpgradeError("回滚数据库完整性检查失败")
        report["restored_database_checks"] = checks
        report["restored_database_metrics"] = database_metrics(db_path)
        report["status"] = "rollback_succeeded_maintenance_retained"
        paths = write_report(report_dir, "rollback-report", report)
        print(json.dumps({"status": report["status"], "reports": paths}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"rollback failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
