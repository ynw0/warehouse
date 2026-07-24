"""Add operator attribution needed by temporary inventory stock records."""

from __future__ import annotations

VERSION = "2026071401"
NAME = "temporary inventory management foundation"


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def upgrade(conn):
    if "operator_id" not in _columns(conn, "stock_records"):
        conn.execute("ALTER TABLE stock_records ADD COLUMN operator_id INTEGER")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_stock_records_source_business_date
        ON stock_records(stock_source, business_type, operation_date, id)
        """
    )
    if "batch_id" in _columns(conn, "stock_records"):
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_stock_records_batch_source
            ON stock_records(batch_id, stock_source, id)
            """
        )


MIGRATION = {"version": VERSION, "name": NAME, "upgrade": upgrade}
