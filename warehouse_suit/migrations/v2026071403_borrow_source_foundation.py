"""Add borrow workflow-item idempotency and active-source indexes."""

from __future__ import annotations

VERSION = "2026071403"
NAME = "borrow source transaction foundation"


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def upgrade(conn):
    if "borrow_form_id" not in _columns(conn, "borrow_records"):
        conn.execute(
            "ALTER TABLE borrow_records ADD COLUMN borrow_form_id INTEGER"
        )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_borrow_records_form_item
        ON borrow_records(borrow_form_id, workflow_item_id)
        WHERE borrow_form_id IS NOT NULL AND workflow_item_id IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_borrow_records_active_source_material
        ON borrow_records(stock_source, material_id, status, borrower_id, id)
        """
    )


MIGRATION = {"version": VERSION, "name": NAME, "upgrade": upgrade}
