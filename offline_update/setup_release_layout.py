#!/usr/bin/env python3
"""One-time migration from a direct deployment to releases/current layout."""
from __future__ import annotations
import argparse
import os
import shutil
from pathlib import Path

PERSISTENT = {"data", ".venv", "releases", "current", "previous", "reports", "backups", ".git", ".env"}

def version_of(root: Path) -> str:
    value = root / "VERSION"
    if value.is_file(): return value.read_text(encoding="utf-8").strip()
    init = root / "warehouse_suit" / "__init__.py"
    for line in init.read_text(encoding="utf-8").splitlines():
        if "__version__" in line and "=" in line: return line.split("=", 1)[1].strip().strip("\"\'")
    raise RuntimeError("无法识别当前版本")

def ignored(name: str) -> bool:
    return name in PERSISTENT or name.startswith("warehouse-update-") or name.endswith((".db", ".db-wal", ".db-shm"))

def main() -> int:
    parser = argparse.ArgumentParser(description="初始化 releases/current 目录（不移动 data 或 .venv）")
    parser.add_argument("--app-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--version")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = Path(args.app_root).resolve()
    if not (root / "app.py").is_file() or not (root / "warehouse_suit").is_dir():
        raise RuntimeError(f"不是仓库系统根目录: {root}")
    if (root / "current").exists() or (root / "current").is_symlink():
        raise RuntimeError("current 已存在；拒绝覆盖")
    version = args.version or version_of(root)
    release = root / "releases" / version
    if release.exists(): raise RuntimeError(f"release 已存在: {release}")
    plan = {"app_root": str(root), "release": str(release), "current": str(root / "current"), "persistent": sorted(PERSISTENT)}
    print(plan)
    if not args.apply: return 0
    release.mkdir(parents=True)
    for source in root.iterdir():
        if ignored(source.name): continue
        target = release / source.name
        if source.is_dir(): shutil.copytree(source, target, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"))
        elif source.is_file(): shutil.copy2(source, target)
    venv = root / ".venv"
    if venv.is_dir(): (release / ".venv").symlink_to(venv, target_is_directory=True)
    temporary = root / ".current.new"
    temporary.symlink_to(release, target_is_directory=True)
    os.replace(temporary, root / "current")
    print(f"initialized: {root / 'current'} -> {release}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
