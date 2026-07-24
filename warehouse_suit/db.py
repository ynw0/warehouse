"""Small database and row helpers used by the legacy Flask module."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta


def connect_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.Error:
        pass
    return conn


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_text() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def next_stocktake_due_date(day: int = 25, base: datetime | None = None) -> str:
    base = base or datetime.now()
    day = max(1, min(28, int(day or 25)))
    if base.day < day:
        due = base.replace(day=day)
    else:
        month = base.month + 1
        year = base.year
        if month > 12:
            month = 1
            year += 1
        due = base.replace(year=year, month=month, day=day)
    return due.strftime("%Y-%m-%d")


def row_to_dict(row):
    return dict(row) if row else None


def decode_db_label(label):
    if label is None:
        return ""
    text = str(label)
    for src in ("latin1", "cp1252"):
        try:
            decoded = text.encode(src).decode("utf-8")
            if decoded != text and any("\u4e00" <= ch <= "\u9fff" for ch in decoded):
                return decoded
        except UnicodeError:
            pass
    return text


def quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def ensure_column(cursor, table, column, definition):
    cursor.execute(f"PRAGMA table_info({quote_identifier(table)})")
    columns = {row["name"] for row in cursor.fetchall()}
    if column not in columns:
        cursor.execute(f"ALTER TABLE {quote_identifier(table)} ADD COLUMN {quote_identifier(column)} {definition}")
