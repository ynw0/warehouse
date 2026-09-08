# -*- coding: utf-8 -*-
"""Database backup settings, execution, restore, and scheduler."""

import json
import os
import sqlite3
import threading
import time
from datetime import datetime

from warehouse_suit.backup_utils import (
    backup_due,
    backup_filename,
    ensure_backup_dir,
    prune_backups,
    resolve_backup_dir,
    validate_sqlite_file,
)
from warehouse_suit.db import now_text
from warehouse_suit.settings import get_setting, parse_json, set_setting


_db_provider = None
_db_path_provider = None
_default_backup_dir = ""
_backup_thread = None
_backup_thread_lock = threading.Lock()
BACKUP_LOCK_MAX_AGE_SECONDS = 6 * 60 * 60


def configure_backup_service(db_provider, db_path_provider, default_backup_dir):
    global _db_provider, _db_path_provider, _default_backup_dir
    _db_provider = db_provider
    _db_path_provider = db_path_provider
    _default_backup_dir = default_backup_dir


def _get_db():
    if _db_provider is None:
        raise RuntimeError("database provider is not configured")
    return _db_provider()


def _db_path():
    if _db_path_provider is None:
        raise RuntimeError("database path provider is not configured")
    return _db_path_provider()


def _default_dir():
    return _default_backup_dir


def backup_settings(cursor):
    defaults = {
        "enabled": True,
        "backup_dir": _default_dir(),
        "frequency_hours": 24,
        "retention_count": 30,
        "last_backup_at": "",
        "last_backup_file": "",
        "last_error": "",
    }
    stored = parse_json(get_setting(cursor, "backup_settings", "{}"), {})
    if isinstance(stored, dict):
        defaults.update(stored)
    defaults["enabled"] = bool(defaults.get("enabled"))
    try:
        defaults["backup_dir"] = resolve_backup_dir(defaults.get("backup_dir"), create=False)
    except Exception as exc:
        defaults["backup_dir"] = resolve_backup_dir(_default_dir(), create=False)
        defaults["last_error"] = f"backup directory unavailable, using default: {exc}"
    defaults["frequency_hours"] = max(1, int(float(defaults.get("frequency_hours") or 24)))
    defaults["retention_count"] = max(1, int(float(defaults.get("retention_count") or 30)))
    return defaults


def save_backup_settings(cursor, settings):
    set_setting(cursor, "backup_settings", json.dumps(settings, ensure_ascii=False))


def _process_is_running(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, TypeError, ValueError):
        return False


def acquire_backup_lock(lock_path):
    """获取跨进程备份锁，并清理已经失效的锁文件。"""
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        age = max(0, time.time() - os.path.getmtime(lock_path))
        try:
            with open(lock_path, "r", encoding="ascii") as file:
                pid = int(file.read().strip())
        except (OSError, ValueError):
            pid = 0
        if age < 5 or (_process_is_running(pid) and age < BACKUP_LOCK_MAX_AGE_SECONDS):
            raise FileExistsError("备份任务正在运行")
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(lock_fd, str(os.getpid()).encode("ascii"))
    return lock_fd


def create_database_backup(cursor=None, reason="manual", backup_dir=None):
    own_conn = None
    if cursor is None:
        own_conn = _get_db()
        cursor = own_conn.cursor()
    settings = backup_settings(cursor)
    target_dir = ensure_backup_dir(backup_dir or settings.get("backup_dir"))
    lock_path = os.path.join(target_dir, ".backup.lock")
    lock_fd = None
    try:
        lock_fd = acquire_backup_lock(lock_path)
        filename = backup_filename()
        target_path = os.path.join(target_dir, filename)
        src = sqlite3.connect(_db_path())
        dst = sqlite3.connect(target_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        validate_sqlite_file(target_path)
        settings.update({"last_backup_at": now_text(), "last_backup_file": target_path, "last_error": ""})
        save_backup_settings(cursor, settings)
        prune_backups(target_dir, settings.get("retention_count", 30))
        if own_conn:
            own_conn.commit()
        return {"filename": filename, "path": target_path, "size": os.path.getsize(target_path), "created_at": settings["last_backup_at"], "reason": reason}
    finally:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
                os.remove(lock_path)
            except OSError:
                pass
        if own_conn:
            own_conn.close()


def list_database_backups(cursor):
    settings = backup_settings(cursor)
    try:
        backup_dir = resolve_backup_dir(settings.get("backup_dir"), create=False)
    except Exception:
        return []
    rows = []
    try:
        names = os.listdir(backup_dir)
    except OSError:
        return []
    for name in names:
        path = os.path.join(backup_dir, name)
        if not name.endswith(".db") or not os.path.isfile(path):
            continue
        stat = os.stat(path)
        rows.append({"filename": name, "path": path, "size": stat.st_size, "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")})
    return sorted(rows, key=lambda item: item["created_at"], reverse=True)


def backup_path_from_name(cursor, filename):
    settings = backup_settings(cursor)
    backup_dir = ensure_backup_dir(settings.get("backup_dir"))
    safe_name = os.path.basename(str(filename or ""))
    path = os.path.abspath(os.path.join(backup_dir, safe_name))
    if os.path.commonpath([os.path.abspath(backup_dir), path]) != os.path.abspath(backup_dir) or not os.path.exists(path):
        raise FileNotFoundError("backup file not found")
    return path


def sqlite_copy_database(source_path, destination_path):
    """通过SQLite备份接口复制完整数据库，包括WAL中的已提交事务。"""
    source_uri = f"file:{os.path.abspath(source_path)}?mode=ro"
    source = sqlite3.connect(source_uri, uri=True)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("数据库复制后的完整性检查失败")
    finally:
        destination.close()
        source.close()


def restore_database_from_backup(source_path):
    validate_sqlite_file(source_path)
    restore_guard = _db_path() + f".before_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    if os.path.exists(_db_path()):
        sqlite_copy_database(_db_path(), restore_guard)
    sqlite_copy_database(source_path, _db_path())
    return restore_guard


def backup_scheduler_loop():
    while True:
        try:
            conn = _get_db()
            cursor = conn.cursor()
            settings = backup_settings(cursor)
            conn.close()
            if backup_due(settings):
                create_database_backup(reason="scheduled")
        except FileExistsError:
            pass
        except Exception as exc:
            try:
                conn = _get_db()
                cursor = conn.cursor()
                settings = backup_settings(cursor)
                settings["last_error"] = str(exc)
                save_backup_settings(cursor, settings)
                conn.commit()
                conn.close()
            except Exception:
                pass
        time.sleep(60)


def start_background_services():
    global _backup_thread
    with _backup_thread_lock:
        if _backup_thread and _backup_thread.is_alive():
            return
        _backup_thread = threading.Thread(target=backup_scheduler_loop, name="warehouse-backup-scheduler", daemon=True)
        _backup_thread.start()
