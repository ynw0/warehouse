"""Add temporary claim obligations and workflow-item ledger attribution."""

from __future__ import annotations

VERSION = "2026071402"
NAME = "temporary claim obligation foundation"


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def upgrade(conn):
    if "workflow_item_id" not in _columns(conn, "stock_records"):
        conn.execute("ALTER TABLE stock_records ADD COLUMN workflow_item_id INTEGER")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS temporary_issue_obligations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            applicant_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            source_batch_id INTEGER NOT NULL,
            claim_form_id INTEGER NOT NULL,
            claim_item_id INTEGER NOT NULL,
            stock_record_id INTEGER NOT NULL UNIQUE,
            issued_quantity REAL NOT NULL CHECK (issued_quantity > 0),
            settled_quantity REAL NOT NULL DEFAULT 0
                CHECK (settled_quantity >= 0 AND settled_quantity <= issued_quantity),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'cancelled', 'exception', 'reserved', 'processing', 'settled')),
            transfer_task_id INTEGER,
            auto_claim_form_id INTEGER,
            operation_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            settled_at TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            FOREIGN KEY (applicant_id) REFERENCES users(id) ON DELETE RESTRICT,
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE RESTRICT,
            FOREIGN KEY (source_batch_id) REFERENCES material_batches(id) ON DELETE RESTRICT,
            FOREIGN KEY (claim_form_id) REFERENCES workflow_forms(id) ON DELETE RESTRICT,
            FOREIGN KEY (claim_item_id) REFERENCES workflow_items(id) ON DELETE RESTRICT,
            FOREIGN KEY (stock_record_id) REFERENCES stock_records(id) ON DELETE RESTRICT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_temp_issue_obligations_status_material
        ON temporary_issue_obligations(status, material_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_temp_issue_obligations_applicant_material_status
        ON temporary_issue_obligations(applicant_id, material_id, status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_temp_issue_obligations_batch_status
        ON temporary_issue_obligations(source_batch_id, status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_temp_issue_obligations_claim
        ON temporary_issue_obligations(claim_form_id, claim_item_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_stock_records_workflow_item
        ON stock_records(workflow_item_id, id)
        """
    )


MIGRATION = {"version": VERSION, "name": NAME, "upgrade": upgrade}
