"""供货交接回执：申请人电话、货物状态、打印标记和交接凭证。"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _ensure_column(conn, table, column, definition):
    if column in _columns(conn, table):
        return
    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} {definition}')


def upgrade(conn):
    _ensure_column(conn, "supply_orders", "contact_phone", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "supply_orders", "handover_required", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "supply_orders", "handover_printed", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "supply_items", "goods_status", "TEXT NOT NULL DEFAULT ''")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS supply_handover_vouchers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            form_id INTEGER NOT NULL,
            original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            content_type TEXT NOT NULL DEFAULT '',
            file_size INTEGER NOT NULL DEFAULT 0,
            uploaded_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES supply_orders(id) ON DELETE CASCADE,
            FOREIGN KEY (form_id) REFERENCES workflow_forms(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_supply_handover_vouchers_form ON supply_handover_vouchers(form_id, id)"
    )


MIGRATION = {
    "version": "2026101202",
    "name": "supply handover receipt",
    "upgrade": upgrade,
}
