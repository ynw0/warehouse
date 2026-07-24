"""Add temporary-to-formal transfer task, snapshot, and acceptance-link tables."""

from __future__ import annotations

import re


def _expand_inventory_status_constraint(conn):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'material_batches'"
    ).fetchone()
    schema_sql = str(row[0] if row else "")
    if "'transfer_locked'" in schema_sql:
        return
    old_constraint = "CHECK (inventory_status IN ('available'))"
    if old_constraint not in schema_sql:
        raise RuntimeError("无法识别 material_batches.inventory_status 约束")
    index_sql = [
        index_row[0]
        for index_row in conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'index' AND tbl_name = 'material_batches'
              AND sql IS NOT NULL
            ORDER BY name
            """
        )
    ]
    replacement = "CHECK (inventory_status IN ('available', 'transfer_locked'))"
    create_sql = schema_sql.replace(old_constraint, replacement)
    create_sql = re.sub(
        r'^CREATE TABLE\s+["\x60]?material_batches["\x60]?',
        "CREATE TABLE material_batches__transfer_new",
        create_sql,
        count=1,
        flags=re.IGNORECASE,
    )
    columns = [
        str(column[1])
        for column in conn.execute("PRAGMA table_info('material_batches')")
    ]
    quoted_columns = ", ".join('"' + name.replace('"', '""') + '"' for name in columns)
    conn.execute("PRAGMA defer_foreign_keys = ON")
    conn.execute(create_sql)
    conn.execute(
        f"""
        INSERT INTO material_batches__transfer_new ({quoted_columns})
        SELECT {quoted_columns}
        FROM material_batches
        """
    )
    conn.execute("DROP TABLE material_batches")
    conn.execute(
        "ALTER TABLE material_batches__transfer_new RENAME TO material_batches"
    )
    for statement in index_sql:
        conn.execute(statement)
VERSION = "2026071404"
NAME = "temporary inventory transfer foundation"


def upgrade(conn):
    _expand_inventory_status_constraint(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory_transfer_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transfer_no TEXT NOT NULL UNIQUE,
            material_id INTEGER NOT NULL,
            requested_by INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'awaiting_purchase'
                CHECK (status IN (
                    'awaiting_purchase', 'acceptance_in_progress', 'acceptance_failed',
                    'formal_inbound_partial', 'formal_inbound_complete', 'paused',
                    'exception', 'cancelled'
                )),
            assigned_buyer_id INTEGER,
            temporary_quantity_snapshot REAL NOT NULL DEFAULT 0
                CHECK (temporary_quantity_snapshot >= 0),
            obligation_quantity_snapshot REAL NOT NULL DEFAULT 0
                CHECK (obligation_quantity_snapshot >= 0),
            target_acceptance_quantity REAL NOT NULL
                CHECK (target_acceptance_quantity > 0),
            accepted_quantity REAL NOT NULL DEFAULT 0
                CHECK (accepted_quantity >= 0),
            active_key TEXT UNIQUE,
            idempotency_key TEXT NOT NULL UNIQUE,
            version INTEGER NOT NULL DEFAULT 0,
            paused_from_status TEXT DEFAULT '',
            error_code TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            claimed_at TEXT DEFAULT '',
            acceptance_started_at TEXT DEFAULT '',
            formal_inbound_at TEXT DEFAULT '',
            cancelled_at TEXT DEFAULT '',
            completed_at TEXT DEFAULT '',
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE RESTRICT,
            FOREIGN KEY (requested_by) REFERENCES users(id) ON DELETE RESTRICT,
            FOREIGN KEY (assigned_buyer_id) REFERENCES users(id) ON DELETE RESTRICT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory_transfer_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            source_batch_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            quantity_snapshot REAL NOT NULL CHECK (quantity_snapshot > 0),
            inventory_status_snapshot TEXT NOT NULL,
            batch_version_snapshot INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (task_id, source_batch_id),
            FOREIGN KEY (task_id) REFERENCES inventory_transfer_tasks(id) ON DELETE RESTRICT,
            FOREIGN KEY (source_batch_id) REFERENCES material_batches(id) ON DELETE RESTRICT,
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE RESTRICT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory_transfer_obligations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            obligation_id INTEGER NOT NULL,
            pending_quantity_snapshot REAL NOT NULL
                CHECK (pending_quantity_snapshot > 0),
            applicant_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            source_batch_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (task_id, obligation_id),
            FOREIGN KEY (task_id) REFERENCES inventory_transfer_tasks(id) ON DELETE RESTRICT,
            FOREIGN KEY (obligation_id) REFERENCES temporary_issue_obligations(id) ON DELETE RESTRICT,
            FOREIGN KEY (applicant_id) REFERENCES users(id) ON DELETE RESTRICT,
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE RESTRICT,
            FOREIGN KEY (source_batch_id) REFERENCES material_batches(id) ON DELETE RESTRICT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transfer_acceptance_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            acceptance_form_id INTEGER NOT NULL,
            acceptance_item_id INTEGER NOT NULL,
            formal_batch_id INTEGER,
            linked_quantity REAL NOT NULL DEFAULT 0 CHECK (linked_quantity >= 0),
            status TEXT NOT NULL DEFAULT 'in_progress',
            operation_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (task_id, acceptance_form_id, acceptance_item_id),
            FOREIGN KEY (task_id) REFERENCES inventory_transfer_tasks(id) ON DELETE RESTRICT,
            FOREIGN KEY (acceptance_form_id) REFERENCES workflow_forms(id) ON DELETE RESTRICT,
            FOREIGN KEY (acceptance_item_id) REFERENCES workflow_items(id) ON DELETE RESTRICT,
            FOREIGN KEY (formal_batch_id) REFERENCES material_batches(id) ON DELETE RESTRICT
        )
        """
    )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_transfer_tasks_material_status "
        "ON inventory_transfer_tasks(material_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_transfer_tasks_buyer_status "
        "ON inventory_transfer_tasks(assigned_buyer_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_transfer_tasks_status_created "
        "ON inventory_transfer_tasks(status, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_transfer_items_task_batch "
        "ON inventory_transfer_items(task_id, source_batch_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_transfer_links_task_status "
        "ON transfer_acceptance_links(task_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_transfer_links_acceptance "
        "ON transfer_acceptance_links(acceptance_form_id, acceptance_item_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_transfer_obligations_task_obligation "
        "ON inventory_transfer_obligations(task_id, obligation_id)"
    )


MIGRATION = {"version": VERSION, "name": NAME, "upgrade": upgrade, "rebuilds_referenced_table": True}
