"""Form and table number generation helpers."""

from __future__ import annotations

from .db import today_text


def max_daily_serial(cursor, table, column, stem):
    cursor.execute(
        f"""
        SELECT COALESCE(MAX(CAST(substr({column}, ?) AS INTEGER)), 0)
        FROM {table}
        WHERE {column} GLOB ?
        """,
        (len(stem) + 1, f"{stem}[0-9]*"),
    )
    return int(cursor.fetchone()[0] or 0)


def _next_daily_no(cursor, table, column, prefix, date_text=None):
    """按完整数字尾号取下一个单号，并串行化并发取号。"""
    if not cursor.connection.in_transaction:
        cursor.connection.execute("BEGIN IMMEDIATE")
    stem = prefix + (date_text or today_text()).replace("-", "")
    serial = max_daily_serial(cursor, table, column, stem)
    if prefix == "RK":
        # 验收入库号实际写入批次和库存流水。
        serial = max(
            serial,
            max_daily_serial(cursor, "material_batches", "source_form_no", stem),
            max_daily_serial(cursor, "stock_records", "form_no", stem),
        )
    return f"{stem}{serial + 1:02d}"


def next_form_no(cursor, prefix, date_text=None):
    return _next_daily_no(cursor, "workflow_forms", "form_no", prefix, date_text)


def next_stocktake_no(cursor):
    return _next_daily_no(cursor, "stocktake_forms", "form_no", "PD")


def next_table_no(cursor, table, column, prefix, date_text=None):
    return _next_daily_no(cursor, table, column, prefix, date_text)
