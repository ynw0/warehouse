"""Add reservation and automatic-claim settlement foundations."""

from __future__ import annotations

import re


def _rebuild_with_check(conn, table, old_fragment, new_fragment, suffix):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    schema_sql = str(row[0] if row else "")
    if new_fragment in schema_sql:
        return
    if old_fragment not in schema_sql:
        raise RuntimeError(f"无法识别 {table} 状态约束")
    index_sql = [
        item[0]
        for item in conn.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL
            ORDER BY name
            """,
            (table,),
        )
    ]
    temporary = f"{table}__{suffix}"
    create_sql = schema_sql.replace(old_fragment, new_fragment)
    create_sql = re.sub(
        rf'^CREATE TABLE\s+["\x60]?{re.escape(table)}["\x60]?',
        f"CREATE TABLE {temporary}",
        create_sql,
        count=1,
        flags=re.IGNORECASE,
    )
    columns = [str(column[1]) for column in conn.execute(f"PRAGMA table_info('{table}')")]
    quoted = ", ".join('"' + name.replace('"', '""') + '"' for name in columns)
    conn.execute("PRAGMA defer_foreign_keys = ON")
    conn.execute(create_sql)
    conn.execute(f"INSERT INTO {temporary} ({quoted}) SELECT {quoted} FROM {table}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {temporary} RENAME TO {table}")
    for statement in index_sql:
        conn.execute(statement)


def upgrade(conn):
    _rebuild_with_check(
        conn,
        "material_batches",
        "CHECK (inventory_status IN ('available', 'transfer_locked'))",
        "CHECK (inventory_status IN ('available', 'transfer_locked', 'transferred'))",
        "settlement_new",
    )
    _rebuild_with_check(
        conn,
        "inventory_transfer_tasks",
        "'formal_inbound_partial', 'formal_inbound_complete', 'paused',\n                    'exception', 'cancelled'",
        "'formal_inbound_partial', 'formal_inbound_complete', 'reserving',\n                    'auto_claim_creating', 'auto_claim_pending', 'auto_claim_exception',\n                    'paused', 'exception', 'cancelled', 'completed'",
        "settlement_new",
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transfer_auto_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            applicant_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            quantity REAL NOT NULL CHECK (quantity > 0),
            current_claim_form_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending_create'
                CHECK (status IN (
                    'pending_create', 'workflow_created', 'approval_pending',
                    'outbound_pending', 'outbound_completed', 'rejected', 'exception'
                )),
            attempt_no INTEGER NOT NULL DEFAULT 0 CHECK (attempt_no >= 0),
            idempotency_key TEXT NOT NULL UNIQUE,
            active_key TEXT UNIQUE,
            error_code TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            workflow_created_at TEXT DEFAULT '',
            outbound_completed_at TEXT DEFAULT '',
            completed_at TEXT DEFAULT '',
            UNIQUE (task_id, applicant_id),
            FOREIGN KEY (task_id) REFERENCES inventory_transfer_tasks(id) ON DELETE RESTRICT,
            FOREIGN KEY (applicant_id) REFERENCES users(id) ON DELETE RESTRICT,
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE RESTRICT,
            FOREIGN KEY (current_claim_form_id) REFERENCES workflow_forms(id) ON DELETE RESTRICT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transfer_auto_claim_obligations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            auto_claim_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            obligation_id INTEGER NOT NULL UNIQUE,
            settlement_quantity REAL NOT NULL CHECK (settlement_quantity > 0),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'settled', 'exception')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            settled_at TEXT DEFAULT '',
            UNIQUE (auto_claim_id, obligation_id),
            FOREIGN KEY (auto_claim_id) REFERENCES transfer_auto_claims(id) ON DELETE RESTRICT,
            FOREIGN KEY (task_id) REFERENCES inventory_transfer_tasks(id) ON DELETE RESTRICT,
            FOREIGN KEY (obligation_id) REFERENCES temporary_issue_obligations(id) ON DELETE RESTRICT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory_reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            auto_claim_id INTEGER NOT NULL,
            formal_batch_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            applicant_id INTEGER NOT NULL,
            reserved_quantity REAL NOT NULL CHECK (reserved_quantity > 0),
            consumed_quantity REAL NOT NULL DEFAULT 0 CHECK (consumed_quantity >= 0),
            released_quantity REAL NOT NULL DEFAULT 0 CHECK (released_quantity >= 0),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'consumed', 'released', 'exception')),
            operation_key TEXT NOT NULL UNIQUE,
            version INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            consumed_at TEXT DEFAULT '',
            released_at TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            CHECK (consumed_quantity + released_quantity <= reserved_quantity),
            UNIQUE (task_id, applicant_id, formal_batch_id),
            FOREIGN KEY (task_id) REFERENCES inventory_transfer_tasks(id) ON DELETE RESTRICT,
            FOREIGN KEY (auto_claim_id) REFERENCES transfer_auto_claims(id) ON DELETE RESTRICT,
            FOREIGN KEY (formal_batch_id) REFERENCES material_batches(id) ON DELETE RESTRICT,
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE RESTRICT,
            FOREIGN KEY (applicant_id) REFERENCES users(id) ON DELETE RESTRICT
        )
        """
    )
    stock_record_columns = {
        row[1] for row in conn.execute("PRAGMA table_info('stock_records')")
    }
    if "transfer_auto_claim_id" not in stock_record_columns:
        conn.execute("ALTER TABLE stock_records ADD COLUMN transfer_auto_claim_id INTEGER")
    if "inventory_reservation_id" not in stock_record_columns:
        conn.execute("ALTER TABLE stock_records ADD COLUMN inventory_reservation_id INTEGER")
    indexes = (
        "CREATE INDEX IF NOT EXISTS idx_reservations_task_status ON inventory_reservations(task_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_reservations_auto_claim_status ON inventory_reservations(auto_claim_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_reservations_batch_status ON inventory_reservations(formal_batch_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_reservations_applicant_material_status ON inventory_reservations(applicant_id, material_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_auto_claims_task_status ON transfer_auto_claims(task_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_auto_claims_form ON transfer_auto_claims(current_claim_form_id)",
        "CREATE INDEX IF NOT EXISTS idx_auto_claim_obligations_task_status ON transfer_auto_claim_obligations(task_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_auto_claim_obligations_claim_status ON transfer_auto_claim_obligations(auto_claim_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_stock_records_transfer_auto_claim ON stock_records(transfer_auto_claim_id, id)",
        "CREATE INDEX IF NOT EXISTS idx_stock_records_inventory_reservation ON stock_records(inventory_reservation_id, id)",
    )
    for statement in indexes:
        conn.execute(statement)


MIGRATION = {
    "version": "2026071501",
    "name": "transfer reservation and auto claim settlement foundation",
    "upgrade": upgrade,
    "rebuilds_referenced_table": True,
}

