"""Runtime paths for the warehouse suite.

This module is intentionally small: it gives the legacy single-file app a
stable place for paths while the rest of the codebase is migrated in stages.
"""

from __future__ import annotations

import glob
import os
import shutil
import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("WAREHOUSE_DATA_DIR", BASE_DIR / "data")).resolve()
DEFAULT_DB_PATH = DATA_DIR / "warehouse.db"
DEFAULT_LOG_PATH = DATA_DIR / "warehouse.log"
DEFAULT_PID_PATH = DATA_DIR / "warehouse.pid"
DEFAULT_BACKUP_DIR = DATA_DIR / "backups"
SECRET_KEY_PATH = DATA_DIR / "secret_key"


def ensure_runtime_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def migrate_legacy_database() -> None:
    """Move the old root database into data/ when the new location is empty."""

    ensure_runtime_dirs()
    if DEFAULT_DB_PATH.exists():
        return
    for legacy_name in ("warehouse.db", "warehouse_cards.db"):
        legacy_path = BASE_DIR / legacy_name
        if legacy_path.exists():
            shutil.move(str(legacy_path), str(DEFAULT_DB_PATH))
            return


def _valid_db_files(pattern: str) -> list[str]:
    db_files: list[str] = []
    for path in glob.glob(pattern):
        name = os.path.basename(path)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if "Zone.Identifier" in name or "\\" in name or size == 0:
            continue
        db_files.append(path)
    return db_files


def _looks_like_warehouse_db(path: str) -> bool:
    try:
        conn = sqlite3.connect(path)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
    except sqlite3.Error:
        return False
    return {"materials", "shelves"}.issubset(tables)


def find_database_path() -> str:
    """Find the active SQLite database without silently abandoning old data."""

    ensure_runtime_dirs()
    env_path = os.environ.get("WAREHOUSE_DB")
    if env_path:
        return os.path.abspath(env_path)

    preferred = [DEFAULT_DB_PATH, BASE_DIR / "warehouse.db", BASE_DIR / "warehouse_cards.db"]
    for path in preferred:
        if path.exists() and path.stat().st_size > 0:
            return str(path)

    db_files = _valid_db_files(str(DATA_DIR / "*.db")) + _valid_db_files(str(BASE_DIR / "*.db"))
    for path in sorted(set(db_files)):
        if _looks_like_warehouse_db(path):
            return path
    for path in sorted(set(db_files)):
        if "xin" not in os.path.basename(path).lower():
            return path
    if db_files:
        return sorted(set(db_files))[0]
    return str(DEFAULT_DB_PATH)


def runtime_file(name: str) -> str:
    ensure_runtime_dirs()
    return str(DATA_DIR / name)
