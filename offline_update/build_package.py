#!/usr/bin/env python3
"""Build the reproducible, sensitive-data-free offline update archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET_PYTHON = (3, 8)
BANNED_PARTS = {".git", ".env", "data", "uploads", "attachments", "logs", "backups", "__pycache__", ".pytest_cache", "node_modules", "codex_work"}
BANNED_SUFFIXES = {".db", ".db-wal", ".db-shm", ".orig", ".rej", ".pyc", ".pyo"}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def excluded(path):
    lowered = {part.lower() for part in path.parts}
    if lowered & BANNED_PARTS:
        return True
    name = path.name.lower()
    return name.endswith(".identifier") or "zone.identifier" in name or any(name.endswith(suffix) for suffix in BANNED_SUFFIXES)


def copy_tree(source, destination):
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if excluded(relative):
            continue
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def project_version():
    content = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*["\']([^"\']+)', content, re.M)
    if not match:
        raise RuntimeError("无法从 pyproject.toml 读取项目版本")
    return match.group(1)


def migration_versions():
    versions = []
    for source in (ROOT / "warehouse_suit" / "migrations").glob("v*_*.py"):
        match = re.match(r"v(\d+)_", source.name)
        if match:
            versions.append(match.group(1))
    return sorted(versions)


def configured_source_versions(explicit):
    support_path = ROOT / "offline_update" / "version_support.json"
    configured = []
    if support_path.is_file():
        configured = json.loads(support_path.read_text(encoding="utf-8")).get("supported_from_versions", [])
    values = explicit or configured
    values = sorted({str(value).strip() for value in values if str(value).strip()})
    if not values:
        raise RuntimeError("请用 --from-version 指定可升级的来源版本，或配置 offline_update/version_support.json")
    return values


def scan_package(stage):
    problems = []
    for path in stage.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(stage)
        if excluded(relative):
            problems.append(str(relative))
            continue
        if path.stat().st_size <= 2 * 1024 * 1024:
            data = path.read_bytes()
            if b"/home/ynw/warehouse-suite" in data or b"\\\\wsl.localhost\\Ubuntu-26.04\\home\\ynw\\warehouse-suite" in data:
                problems.append(f"development absolute path: {relative}")
    if problems:
        raise RuntimeError("package contains forbidden content:\n" + "\n".join(problems))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel-dir", default=str(ROOT / "wheelhouse"))
    parser.add_argument("--from-version", action="append", help="可升级的当前版本；可重复指定")
    parser.add_argument("--dist-dir", default=str(ROOT / "dist"))
    args = parser.parse_args(argv)
    wheel_dir = Path(args.wheel_dir).resolve()
    wheels = sorted(wheel_dir.glob("*.whl"))
    if not wheels:
        raise RuntimeError("wheelhouse is empty")
    target_version = project_version()
    source_versions = configured_source_versions(args.from_version)
    if target_version in source_versions:
        raise RuntimeError("目标版本与来源版本相同；请先更新 pyproject.toml 中的项目版本")
    package_name = "warehouse-update-%s-to-%s" % (source_versions[0] if len(source_versions) == 1 else "multi", target_version)
    dist = Path(args.dist_dir).resolve()
    dist.mkdir(parents=True, exist_ok=True)
    stage = dist / package_name
    if stage.exists():
        if stage.parent != dist or not stage.name.startswith("warehouse-update-"):
            raise RuntimeError("refusing to replace unexpected directory")
        shutil.rmtree(stage)
    stage.mkdir()
    app_dir = stage / "app"
    app_dir.mkdir()
    for name in ("app.py", "pyproject.toml", "run.sh", "start_background.sh", "stop.sh", "status.sh"):
        source = ROOT / name
        if source.is_file():
            shutil.copy2(source, app_dir / name)
    for name in ("warehouse_suit", "static", "templates", "wuliao_skill"):
        copy_tree(ROOT / name, app_dir / name)
    (app_dir / "VERSION").write_text(target_version + "\n", encoding="utf-8")
    (app_dir / "requirements-runtime.txt").write_text(
        "\n".join((
            "Flask==3.0.3", "gunicorn==22.0.0", "blinker==1.8.2", "click==8.1.8",
            "itsdangerous==2.2.0", "Jinja2==3.1.6", "MarkupSafe==2.1.5; python_version < '3.10'",
            "MarkupSafe==3.0.3; python_version >= '3.10'", "Werkzeug==3.0.6", "packaging==26.2", "",
        )),
        encoding="utf-8",
    )

    scripts = stage / "scripts"
    scripts.mkdir()
    for name in ("common.py", "interactive.py", "upgrade.py", "rollback.py", "precheck.py", "backup.py", "migrate_copy.py", "verify.py", "health_check.py", "setup_release_layout.py", "warehouse-maintenance", "update.sh", "rollback.sh", "update.ps1", "rollback.ps1"):
        shutil.copy2(ROOT / "offline_update" / name, scripts / name)
    shutil.copy2(ROOT / "offline_update" / "interactive.py", scripts / "update.py")
    for name in ("warehouse-maintenance", "update.sh", "rollback.sh", "update.py"):
        (scripts / name).chmod((scripts / name).stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    shutil.copy2(ROOT / "offline_update" / "direct_apply.sh", stage / "update_direct.sh")
    (stage / "update_direct.sh").chmod((stage / "update_direct.sh").stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    entry = stage / "update"
    entry.write_text("#!/usr/bin/env sh\nset -eu\nBASE_DIR=$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd)\nexec \"${PYTHON:-python3}\" \"$BASE_DIR/scripts/update.py\" \"$@\"\n", encoding="utf-8")
    entry.chmod(entry.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    config = stage / "config"
    config.mkdir()
    shutil.copy2(ROOT / "offline_update" / "update-config.example.env", config / "update-config.example.env")
    shutil.copy2(ROOT / "offline_update" / "protected-paths.txt", config / "protected-paths.txt")
    tests = stage / "tests"
    tests.mkdir()
    shutil.copy2(ROOT / "offline_update" / "read_only_smoke.py", tests / "read_only_smoke.py")
    (stage / "reports").mkdir()
    wheel_target = stage / "wheels"
    wheel_target.mkdir()
    for wheel in wheels:
        shutil.copy2(wheel, wheel_target / wheel.name)
    
    (stage / "VERSION").write_text(target_version + "\n", encoding="utf-8")

    scan_package(stage)
    payload_files = {}
    for path in sorted(stage.rglob("*")):
        if path.is_file() and path.name not in {"package_manifest.json", "checksums.sha256"}:
            payload_files[str(path.relative_to(stage)).replace(os.sep, "/")] = {"sha256": sha256(path), "size": path.stat().st_size}
    manifest = {
        "package_version": target_version,
        "target_application_version": target_version,
        "minimum_supported_version": min(source_versions),
        "maximum_supported_version": max(source_versions),
        "supported_from_versions": source_versions,
        "migration_versions": migration_versions(),
        "minimum_python": [3, 8],
        "wheel_python": list(TARGET_PYTHON),
        "built_with_python": platform.python_version(),
        "sqlite_minimum": "3.24.0",
        "built_with_sqlite": sqlite3.sqlite_version,
        "operating_systems": ["Linux"],
        "cpu_architectures": ["x86_64"],
        "dependency_versions": (app_dir / "requirements-runtime.txt").read_text(encoding="utf-8").splitlines(),
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source_git": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True).stdout.strip(),
        "source_dirty": True,
        "file_count": len(payload_files) + 2,
        "files": payload_files,
    }
    manifest_path = stage / "package_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksum_lines = []
    for path in sorted(stage.rglob("*")):
        if path.is_file() and path.name != "checksums.sha256" and "reports" not in path.relative_to(stage).parts:
            checksum_lines.append(f"{sha256(path)}  {str(path.relative_to(stage)).replace(os.sep, '/')}")
    (stage / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    scan_package(stage)

    zip_path = dist / f"{package_name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                archive.write(path, f"{package_name}/{path.relative_to(stage)}")
    build_report = dist / f"warehouse-update-{target_version}-build-report.md"
    build_report.write_text(
        "\n".join([
            f"# Warehouse update {target_version} build report",
            "",
            f"- archive: {zip_path}",
            f"- size_bytes: {zip_path.stat().st_size}",
            f"- sha256: {sha256(zip_path)}",
            f"- package_file_count: {sum(1 for p in stage.rglob('*') if p.is_file())}",
            f"- supported_from: {', '.join(source_versions)}",
            f"- target: {target_version}",
            f"- python: CPython {TARGET_PYTHON[0]}.{TARGET_PYTHON[1]}.x (target: 3.8.10); build host: CPython {platform.python_version()}",
            f"- platform: Linux x86_64",
            "- recommended_free_space: max(4 x database size + 100 MiB, 500 MiB)",
            "- recommended_maintenance_window: 30-60 minutes after a production-copy rehearsal",
            "- 单一入口: chmod +x update && ./update",
            "- 预检查: ./update --dry-run --app-dir /绝对路径",
            "",
        ]),
        encoding="utf-8",
    )
    print(json.dumps({"stage": str(stage), "zip": str(zip_path), "size": zip_path.stat().st_size, "sha256": sha256(zip_path), "file_count": sum(1 for p in stage.rglob('*') if p.is_file()), "build_report": str(build_report)}, indent=2))


if __name__ == "__main__":
    main()
