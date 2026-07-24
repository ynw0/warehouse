"""Form and table number generation helpers."""

from __future__ import annotations

from .db import today_text


def next_form_no(cursor, prefix, date_text=None):
    date_part = (date_text or today_text()).replace("-", "")
    like = f"{prefix}{date_part}%"
    cursor.execute(
        "SELECT form_no FROM workflow_forms WHERE form_no LIKE ? ORDER BY form_no DESC LIMIT 1",
        (like,),
    )
    row = cursor.fetchone()
    serial = int(str(row[0])[-2:]) + 1 if row else 1
    return f"{prefix}{date_part}{serial:02d}"


def next_stocktake_no(cursor):
    date_part = today_text().replace("-", "")
    like = f"PD{date_part}%"
    cursor.execute("SELECT form_no FROM stocktake_forms WHERE form_no LIKE ? ORDER BY form_no DESC LIMIT 1", (like,))
    row = cursor.fetchone()
    serial = int(str(row[0])[-2:]) + 1 if row else 1
    return f"PD{date_part}{serial:02d}"


def next_table_no(cursor, table, column, prefix, date_text=None):
    date_part = (date_text or today_text()).replace("-", "")
    like = f"{prefix}{date_part}%"
    cursor.execute(f"SELECT {column} FROM {table} WHERE {column} LIKE ? ORDER BY {column} DESC LIMIT 1", (like,))
    row = cursor.fetchone()
    serial = int(str(row[0])[-2:]) + 1 if row else 1
    return f"{prefix}{date_part}{serial:02d}"
