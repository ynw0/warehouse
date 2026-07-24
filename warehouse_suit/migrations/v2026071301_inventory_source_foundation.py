"""Add stock-source, inventory-status, and idempotency foundations."""

from __future__ import annotations

VERSION = "2026071301"
NAME = "inventory source foundation"


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _ensure_column(conn, table, column, definition):
    if column not in _columns(conn, table):
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')


def upgrade(conn):
    _ensure_column(
        conn,
        "material_batches",
        "stock_source",
        "TEXT NOT NULL DEFAULT 'formal' CHECK (stock_source IN ('formal', 'temporary'))",
    )
    _ensure_column(
        conn,
        "material_batches",
        "inventory_status",
        "TEXT NOT NULL DEFAULT 'available' CHECK (inventory_status IN ('available'))",
    )
    _ensure_column(conn, "material_batches", "version", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(
        conn,
        "stock_records",
        "stock_source",
        "TEXT NOT NULL DEFAULT 'formal' CHECK (stock_source IN ('formal', 'temporary'))",
    )
    _ensure_column(conn, "stock_records", "business_type", "TEXT NOT NULL DEFAULT 'manual'")
    _ensure_column(conn, "stock_records", "operation_key", "TEXT")
    _ensure_column(conn, "stock_records", "transfer_task_id", "INTEGER")
    _ensure_column(
        conn,
        "workflow_items",
        "stock_source",
        "TEXT NOT NULL DEFAULT 'formal' CHECK (stock_source IN ('formal', 'temporary'))",
    )
    _ensure_column(conn, "workflow_forms", "origin_type", "TEXT NOT NULL DEFAULT 'manual'")
    _ensure_column(conn, "workflow_forms", "origin_ref_id", "INTEGER")
    _ensure_column(conn, "borrow_records", "workflow_item_id", "INTEGER")
    _ensure_column(
        conn,
        "borrow_records",
        "stock_source",
        "TEXT NOT NULL DEFAULT 'formal' CHECK (stock_source IN ('formal', 'temporary'))",
    )

    conn.execute("UPDATE material_batches SET stock_source = 'formal' WHERE stock_source IS NULL OR stock_source = ''")
    conn.execute("UPDATE material_batches SET inventory_status = 'available' WHERE inventory_status IS NULL OR inventory_status = ''")
    conn.execute("UPDATE material_batches SET version = 0 WHERE version IS NULL")
    conn.execute("UPDATE stock_records SET stock_source = 'formal' WHERE stock_source IS NULL OR stock_source = ''")
    conn.execute("UPDATE stock_records SET business_type = 'manual' WHERE business_type IS NULL OR business_type = ''")
    conn.execute("UPDATE workflow_items SET stock_source = 'formal' WHERE stock_source IS NULL OR stock_source = ''")
    conn.execute("UPDATE workflow_forms SET origin_type = 'manual' WHERE origin_type IS NULL OR origin_type = ''")
    conn.execute("UPDATE borrow_records SET stock_source = 'formal' WHERE stock_source IS NULL OR stock_source = ''")

    negative_count = conn.execute("SELECT COUNT(*) FROM material_batches WHERE quantity < 0").fetchone()[0]
    if negative_count:
        raise ValueError(f"material_batches contains {negative_count} negative quantity rows")

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_material_batches_source_status_material_fifo
        ON material_batches(stock_source, inventory_status, material_id, received_date, id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_stock_records_source_material_date
        ON stock_records(stock_source, material_id, operation_date)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_records_operation_key
        ON stock_records(operation_key)
        WHERE operation_key IS NOT NULL AND operation_key <> ''
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_workflow_items_form_source_material
        ON workflow_items(form_id, stock_source, material_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_borrow_records_source_material_borrower_status
        ON borrow_records(stock_source, material_id, borrower_id, status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_notifications_user_read_id
        ON notifications(user_id, is_read, id)
        """
    )


MIGRATION = {"version": VERSION, "name": NAME, "upgrade": upgrade}
