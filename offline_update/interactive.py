#!/usr/bin/env python3
"""Chinese, fail-closed entry point for a Warehouse Suite offline update.

This file is deliberately dependency-free.  The same file is copied to an
offline package as ``scripts/update.py``; therefore it must never import the
Flask application (doing so could initialise a production database).
"""
from __future__ import print_function

import argparse
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from common import (UpgradeError, database_checks, database_metrics,
                    migrate_database_copy, read_json, run_migrations,
                    sha256_file, sqlite_backup, switch_current_link,
                    timestamp, upgrade_lock, verify_package_checksums,
                    write_report)


PACKAGE_DIR = Path(__file__).resolve().parent.parent
DANGEROUS_ROOTS = {"/", "/home", "/tmp", "/var/tmp", "/root"}
CONFIG_NAME = "update-config.json"


def _version(root):
    for name in ("VERSION",):
        candidate = root / name
        if candidate.is_file() and candidate.read_text(encoding="utf-8").strip():
            return candidate.read_text(encoding="utf-8").strip()
    init = root / "warehouse_suit" / "__init__.py"
    if init.is_file():
        match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", init.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        match = re.search(r"^version\s*=\s*['\"]([^'\"]+)", pyproject.read_text(encoding="utf-8"), re.M)
        if match:
            return match.group(1)
    raise UpgradeError("无法识别当前应用版本（缺少 VERSION 或 pyproject.toml）")


def _manifest():
    path = PACKAGE_DIR / "package_manifest.json"
    if not path.is_file():
        raise UpgradeError("更新包缺少 package_manifest.json；请从完整离线包目录运行 update")
    data = read_json(path)
    required = ("target_application_version", "supported_from_versions", "migration_versions")
    if any(not data.get(item) for item in required):
        raise UpgradeError("package_manifest.json 不完整")
    return data


def _is_writable(path):
    candidate = path if path.exists() else path.parent
    return candidate.exists() and os.access(str(candidate), os.W_OK | os.X_OK)


def _database_in(data_dir):
    candidates = [p for p in sorted(data_dir.glob("*.db")) if p.is_file() and p.stat().st_size > 0]
    if len(candidates) != 1:
        raise UpgradeError("data/ 下必须且只能识别一个非空 SQLite 数据库；请先清理或在 update-config.json 中指定 database_path")
    db = candidates[0]
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        conn.close()
    except sqlite3.Error as exc:
        raise UpgradeError("无法打开 SQLite 数据库: %s" % exc)
    return db


def _python_version(python):
    result = subprocess.run([str(python), "-c", "import sys; print('%s.%s.%s' % sys.version_info[:3])"], text=True, capture_output=True)
    if result.returncode:
        raise UpgradeError("无法运行 .venv/bin/python: %s" % result.stderr.strip())
    return result.stdout.strip()


def validate_install(app_dir, allow_test=False):
    """Perform only read-only validation; this function creates no files."""
    manifest = _manifest()
    raw = Path(app_dir).expanduser()
    if not raw.is_absolute():
        raise UpgradeError("安装目录必须是绝对路径")
    root = raw.resolve()
    if str(root) in DANGEROUS_ROOTS:
        raise UpgradeError("安装目录不能是危险路径: %s" % root)
    if not root.is_dir() or not os.access(str(root), os.R_OK | os.X_OK):
        raise UpgradeError("安装目录不存在或不可读: %s" % root)
    if not (root / "app.py").is_file() or not (root / "warehouse_suit").is_dir():
        raise UpgradeError("目录不是 warehouse-suit 安装目录（缺少 app.py 或 warehouse_suit/）")
    data = root / "data"
    if not data.is_dir():
        raise UpgradeError("缺少 data/ 目录")
    db = _database_in(data)
    if not allow_test and ("test" in db.name.lower() or str(db).startswith(("/tmp/", "/var/tmp/"))):
        raise UpgradeError("拒绝将测试数据库作为生产数据库升级")
    python = root / ".venv" / "bin" / "python"
    if not python.is_file() or not os.access(str(python), os.X_OK):
        raise UpgradeError("缺少可执行的 .venv/bin/python")
    py_version = _python_version(python)
    if tuple(int(x) for x in py_version.split(".")[:2]) != (3, 8):
        raise UpgradeError("生产环境 Python 必须为 3.8.x，当前为 %s" % py_version)
    current = root / "current"
    if current.exists() or current.is_symlink():
        if not current.is_symlink():
            raise UpgradeError("current 存在但不是符号链接")
        current_app = current.resolve()
        releases = (root / "releases").resolve()
        try:
            current_app.relative_to(releases)
        except ValueError:
            raise UpgradeError("current 不允许指向 releases/ 之外")
        if not (current_app / "app.py").is_file():
            raise UpgradeError("current 指向的 release 无效")
        layout = "release"
    else:
        current_app, layout = root, "direct"
    previous = root / "previous"
    if previous.exists() or previous.is_symlink():
        if not previous.is_symlink():
            raise UpgradeError("previous 存在但不是符号链接")
        try:
            previous.resolve().relative_to((root / "releases").resolve())
        except ValueError:
            raise UpgradeError("previous 不允许指向 releases/ 之外")
    version = _version(current_app)
    supported = set(manifest["supported_from_versions"])
    target = manifest["target_application_version"]
    if version != target and version not in supported:
        raise UpgradeError("当前版本 %s 不在更新包支持范围 %s" % (version, sorted(supported)))
    checks = database_checks(db)
    if checks["integrity_check"] != "ok" or checks["foreign_key_check"]:
        raise UpgradeError("当前数据库完整性或外键检查失败")
    unknown = set(checks["migrations"]) - set(str(x) for x in manifest["migration_versions"])
    if unknown:
        raise UpgradeError("数据库存在未知的更高迁移版本: %s" % sorted(unknown))
    for required in (data, root / "backups", root / "reports"):
        if not _is_writable(required):
            raise UpgradeError("目录不可写: %s" % required)
    return {"root": root, "data": data, "db": db, "python": python, "python_version": py_version,
            "current_app": current_app, "version": version, "target": target, "layout": layout,
            "manifest": manifest, "checks": checks}


def _copy_direct_release(info):
    """First-time release layout initialisation without moving persistent data."""
    root, version = info["root"], info["version"]
    release = root / "releases" / version
    if release.exists():
        raise UpgradeError("首次初始化目标 release 已存在: %s" % release)
    ignored = shutil.ignore_patterns("data", ".venv", ".env", "releases", "current", "previous", "backups", "reports", ".git", "*.db", "*.db-wal", "*.db-shm", "__pycache__", ".pytest_cache")
    temporary = root / ".current.initializing"
    try:
        shutil.copytree(str(root), str(release), symlinks=True, ignore=ignored)
        temporary.symlink_to(release, target_is_directory=True)
        os.replace(str(temporary), str(root / "current"))
    except Exception:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        if release.exists():
            shutil.rmtree(str(release))
        raise
    return release


def _write_config(info, service):
    config = {"installation_path": str(info["root"]), "database_path": str(info["db"]),
              "backup_path": str(info["root"] / "backups"), "report_path": str(info["root"] / "reports"),
              "service_control": service}
    target = info["data"] / CONFIG_NAME
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(str(temp), str(target))
    return config


def detect_service(root):
    """Return only concrete, locally verifiable controls; never guess a service name."""
    candidates = []
    scripts = [root / name for name in ("stop.sh", "start_background.sh", "status.sh")]
    if all(item.is_file() for item in scripts):
        candidates.append({"kind": "project-scripts", "stop": "sh %s" % scripts[0], "start": "sh %s" % scripts[1], "status": "sh %s" % scripts[2]})
    # systemd names are accepted only when a unit file explicitly lives in this project.
    for unit in root.glob("*.service"):
        name = unit.name
        candidates.append({"kind": "systemd", "stop": "systemctl stop %s" % name, "start": "systemctl start %s" % name, "status": "systemctl is-active %s" % name})
    return candidates


def choose_service(candidates, input_fn=input, output=print, non_interactive=False):
    """Require an explicit choice whenever multiple concrete controls exist."""
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return None
    output("检测到多个服务控制候选项：")
    for index, candidate in enumerate(candidates, 1):
        output("%d. %s\n   停止：%s\n   启动：%s\n   状态：%s" % (index, candidate["kind"], candidate["stop"], candidate["start"], candidate["status"]))
    if non_interactive:
        return None
    while True:
        selected = input_fn("请选择服务编号，或输入 q 取消：").strip().lower()
        if selected in ("q", "quit", ""):
            return None
        if selected.isdigit() and 1 <= int(selected) <= len(candidates):
            candidate = candidates[int(selected) - 1]
            check = subprocess.run(candidate["status"], shell=True, text=True, capture_output=True)
            if check.returncode in (0, 3):
                return candidate
            output("该服务状态命令无法验证，请重新选择：%s" % check.stderr.strip())
            continue
        output("请输入有效编号，或 q 取消。")


def dry_run(info):
    verify_package_checksums()
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        raise UpgradeError("更新包仅支持 Linux x86_64")
    package_size = sum(p.stat().st_size for p in PACKAGE_DIR.rglob("*") if p.is_file())
    required = info["db"].stat().st_size * 4 + package_size * 2 + 100 * 1024 * 1024
    usage = shutil.disk_usage(str(info["root"]))
    if usage.free < required:
        raise UpgradeError("磁盘空间不足：需要 %d 字节，当前可用 %d 字节" % (required, usage.free))
    rehearsal = migrate_database_copy(info["db"], info["root"] / "reports" / "dry-run")
    compiled = subprocess.run([str(info["python"]), "-m", "compileall", "-q", str(PACKAGE_DIR / "app")], text=True, capture_output=True)
    if compiled.returncode:
        raise UpgradeError("新 release compileall 失败: %s" % compiled.stderr[-1000:])
    return {"required_bytes": required, "free_bytes": usage.free, "migration_rehearsal": rehearsal,
            "database_metrics": database_metrics(info["db"])}


def _maintenance(info, action):
    script = PACKAGE_DIR / "scripts" / "warehouse-maintenance"
    result = subprocess.run(["sh", str(script), action, "--flag", str(info["data"] / "warehouse-maintenance.flag")], text=True, capture_output=True)
    if result.returncode not in (0, 1 if action == "status" else 0):
        raise UpgradeError("维护模式命令失败: %s" % result.stderr.strip())
    return result.stdout.strip()


def _start_switched_release(info, release, service):
    """Start a project-script service from the newly selected release."""
    if service.get("kind") != "project-scripts":
        return subprocess.run(service["start"], shell=True, text=True, capture_output=True)

    script = release / "start_background.sh"
    if not script.is_file():
        raise UpgradeError("新 release 缺少 start_background.sh: %s" % script)
    environment = os.environ.copy()
    environment.update({
        "VENV_DIR": str(info["root"] / ".venv"),
        "DATA_DIR": str(info["data"]),
        "WAREHOUSE_DATA_DIR": str(info["data"]),
        "WAREHOUSE_DB": str(info["db"]),
        "PID_FILE": str(info["data"] / "warehouse.pid"),
        "LOG_FILE": str(info["data"] / "warehouse.log"),
    })
    return subprocess.run(
        ["sh", str(script)],
        cwd=str(release),
        env=environment,
        text=True,
        capture_output=True,
    )


def formal_upgrade(info, service=None):
    root, target = info["root"], info["target"]
    old_target = str((root / "current").resolve())
    db_backup = None
    switched = False
    with upgrade_lock(root, info["version"], target, "interactive-upgrade"):
        try:
            _maintenance(info, "enable")
            if service:
                stop = subprocess.run(service["stop"], shell=True, text=True, capture_output=True)
                if stop.returncode:
                    raise UpgradeError("停止服务失败: %s" % stop.stderr[-1000:])
            conn = sqlite3.connect(str(info["db"])); conn.execute("PRAGMA wal_checkpoint(FULL)"); conn.close()
            backup = root / "backups" / ("warehouse_pre_upgrade_%s_%s.db" % (info["version"], timestamp()))
            db_backup = sqlite_backup(info["db"], backup)
            release = root / "releases" / target
            if release.exists() and _version(release) != target:
                raise UpgradeError("目标 release 已存在且版本不匹配")
            if not release.exists():
                shutil.copytree(str(PACKAGE_DIR / "app"), str(release), symlinks=True)
            compiled = subprocess.run([str(info["python"]), "-m", "compileall", "-q", str(release / "warehouse_suit")], text=True, capture_output=True)
            if compiled.returncode:
                raise UpgradeError("新版本编译失败")
            before = database_metrics(info["db"])
            migrations = run_migrations(info["db"], release)
            after = database_metrics(info["db"])
            checks = database_checks(info["db"])
            if before != after or checks["integrity_check"] != "ok" or checks["foreign_key_check"]:
                raise UpgradeError("正式迁移后的数据对账或完整性检查失败")
            prior = switch_current_link(root / "current", release)
            previous = root / "previous"
            if previous.exists() or previous.is_symlink(): previous.unlink()
            previous.symlink_to(prior or old_target, target_is_directory=True)
            switched = True
            if service:
                started = _start_switched_release(info, release, service)
                if started.returncode: raise UpgradeError("启动新服务失败: %s" % started.stderr[-1000:])
            return {"backup": db_backup, "migrations": migrations, "current": str(release), "previous": prior or old_target}
        except Exception:
            if switched:
                switch_current_link(root / "current", old_target)
            if db_backup:
                # SQLite backup API rather than copying a live database file.
                source = sqlite3.connect(db_backup["path"]); destination = sqlite3.connect(str(info["db"])); source.backup(destination); destination.close(); source.close()
            if service:
                subprocess.run(service["start"], shell=True, text=True, capture_output=True)
            raise


def _print_validation(info, output=print):
    output("已识别 warehouse-suit 安装目录：\n%s\n\n当前版本：\n%s\n\n数据库：\n%s\n\nPython：\n%s" % (info["root"], info["version"], info["db"], info["python"]))


def main(argv=None, input_fn=input, output=print):
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--app-dir")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--precheck-only", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--config")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args(argv)
    if args.config:
        args.app_dir = read_json(args.config).get("installation_path")
    default = args.app_dir or "/mnt/disk0/warehouse-suit"
    if args.non_interactive and not args.app_dir:
        raise UpgradeError("--non-interactive 必须与 --app-dir 或 --config 一起使用")
    output("仓库物料系统离线升级程序\n本程序将执行环境检查、数据库副本迁移演练、备份和版本切换。\n正式升级期间系统将暂停写入，请勿关闭终端。")
    while True:
        chosen = args.app_dir or input_fn("请输入 warehouse-suit 安装目录：\n默认值：%s\n输入路径后按回车继续：" % default).strip() or default
        if str(chosen).strip().lower() in ("q", "quit"):
            output("已退出，未修改任何文件。")
            return 0
        try:
            info = validate_install(chosen, allow_test=os.environ.get("WAREHOUSE_UPDATER_ALLOW_TEST") == "1")
        except Exception as exc:
            output("验证失败：%s" % exc)
            if args.non_interactive: return 1
            args.app_dir = None
            continue
        _print_validation(info, output)
        if not args.non_interactive and input_fn("是否正确？[Y/n] ").strip().lower() not in ("", "y", "yes"):
            args.app_dir = None
            continue
        break
    if args.status:
        output("维护模式：%s" % _maintenance(info, "status")); return 0
    if args.rollback:
        raise UpgradeError("请在维护模式下使用本次升级报告中的备份执行回滚；自动回滚仅在升级失败时执行")
    service_candidates = detect_service(info["root"])
    service = choose_service(service_candidates, input_fn, output, args.non_interactive)
    report = {"status": "started", "install": str(info["root"]), "version": info["version"], "target": info["target"]}
    try:
        report["dry_run"] = dry_run(info)
        report["status"] = "dry_run_passed"
    except Exception as exc:
        report["status"], report["error"] = "dry_run_failed", str(exc)
        paths = write_report(info["root"] / "reports", "offline-update-precheck", report)
        output("预检查失败，未对生产系统进行任何修改\n%s\n报告：%s" % (exc, paths["markdown"]))
        return 1
    paths = write_report(info["root"] / "reports", "offline-update-precheck", report)
    output("预检查和数据库副本迁移演练成功。\n正式数据库尚未修改。\n当前系统尚未停止。\n备份所需空间充足。\n预计维护时间：10 分钟。")
    if args.dry_run or args.precheck_only:
        return 0
    if service:
        output("已识别服务控制方式：\n%s\n%s" % (service["stop"], service["start"]))
        if not args.non_interactive and input_fn("是否使用？[Y/n] ").strip().lower() not in ("", "y", "yes"):
            service = None
    elif service_candidates:
        output("未选择服务控制方式，已安全取消，未修改生产系统。")
        return 1
    else:
        output("未能唯一识别服务控制方式，已安全停止；请配置唯一服务后重试。")
        return 1
    _write_config(info, service)
    if args.non_interactive or input_fn("是否开始正式升级？输入 yes 继续，输入其他内容取消：").strip() != "yes":
        output("已取消，未修改生产系统。")
        return 0
    if info["layout"] == "direct":
        output("首次初始化：将复制当前代码到 releases/%s；data、.venv、.env 和备份不会移动。" % info["version"])
        _copy_direct_release(info)
        info = validate_install(info["root"], allow_test=os.environ.get("WAREHOUSE_UPDATER_ALLOW_TEST") == "1")
    result = formal_upgrade(info, service)
    output("新版本已启动，系统仍处于维护模式。\n请完成页面和只读数据检查。")
    final = input_fn("输入 confirm 关闭维护模式并完成升级。输入 rollback 执行回滚。输入其他内容保持维护模式并退出。").strip()
    if final == "confirm":
        _maintenance(info, "disable"); output("升级完成，维护模式已关闭。")
    elif final == "rollback":
        raise UpgradeError("请保持维护模式并使用升级备份回滚：%s" % result["backup"]["path"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("升级失败：%s" % exc, file=sys.stderr)
        raise SystemExit(1)
