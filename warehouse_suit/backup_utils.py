"""Backup path and SQLite backup utility helpers."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta


_BASE_DIR = os.getcwd()
_DEFAULT_BACKUP_DIR = os.path.join(_BASE_DIR, "data", "backups")


def configure_backup_paths(base_dir, default_backup_dir):
    global _BASE_DIR, _DEFAULT_BACKUP_DIR
    _BASE_DIR = os.path.abspath(str(base_dir or os.getcwd()))
    _DEFAULT_BACKUP_DIR = os.path.abspath(str(default_backup_dir or os.path.join(_BASE_DIR, "data", "backups")))


def resolve_backup_dir(path, create=True):
    raw = str(path or _DEFAULT_BACKUP_DIR).strip() or _DEFAULT_BACKUP_DIR
    windows_abs = (len(raw) >= 3 and raw[1] == ":" and raw[2] in "\\/") or raw.startswith("\\\\")
    if os.name != "nt" and windows_abs and not os.path.exists(raw):
        raw = _DEFAULT_BACKUP_DIR
    expanded = os.path.expanduser(raw)
    if not os.path.isabs(expanded):
        expanded = os.path.join(_BASE_DIR, expanded)
    backup_dir = os.path.abspath(expanded)
    base_abs = os.path.abspath(_BASE_DIR)
    default_abs = os.path.abspath(_DEFAULT_BACKUP_DIR)
    defaultish = os.path.basename(os.path.normpath(backup_dir)) == "backups" and not (
        backup_dir == default_abs or backup_dir.startswith(base_abs + os.sep)
    )
    if defaultish and not os.path.exists(backup_dir):
        backup_dir = default_abs
    if create:
        try:
            os.makedirs(backup_dir, exist_ok=True)
        except OSError:
            backup_dir = default_abs
            os.makedirs(backup_dir, exist_ok=True)
    return backup_dir


def ensure_backup_dir(path):
    return resolve_backup_dir(path, create=True)


def backup_filename():
    return f"warehouse_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"


def prune_backups(backup_dir, retention_count):
    backups = sorted(
        [os.path.join(backup_dir, name) for name in os.listdir(backup_dir) if name.endswith(".db")],
        key=lambda path: os.path.getmtime(path),
        reverse=True,
    )
    for path in backups[int(retention_count or 30):]:
        try:
            os.remove(path)
        except OSError:
            pass


def validate_sqlite_file(path):
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            raise ValueError("backup integrity check failed")
    finally:
        conn.close()


def backup_due(settings):
    if not settings.get("enabled"):
        return False
    last_text = settings.get("last_backup_at") or ""
    if not last_text:
        return True
    try:
        last = datetime.strptime(last_text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return datetime.now() - last >= timedelta(hours=int(settings.get("frequency_hours") or 24))
