"""半成品已领用数量，以及领用退库待入库占用。"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _has_table(conn, table):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def upgrade(conn):
    if _has_table(conn, "semifinished_inventory") and "claimed_quantity" not in _columns(conn, "semifinished_inventory"):
        conn.execute(
            "ALTER TABLE semifinished_inventory ADD COLUMN claimed_quantity REAL NOT NULL DEFAULT 0"
        )
    if _has_table(conn, "scrapped_semifinished_goods") and "claimed_quantity" not in _columns(conn, "scrapped_semifinished_goods"):
        conn.execute(
            "ALTER TABLE scrapped_semifinished_goods ADD COLUMN claimed_quantity REAL NOT NULL DEFAULT 0"
        )
    if _has_table(conn, "defective_semifinished_goods") and "claimed" not in _columns(conn, "defective_semifinished_goods"):
        conn.execute(
            "ALTER TABLE defective_semifinished_goods ADD COLUMN claimed INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS issued_return_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_kind TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            batch_id INTEGER,
            user_id INTEGER NOT NULL,
            quantity REAL NOT NULL CHECK (quantity > 0),
            condition_status TEXT NOT NULL,
            remark TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            form_id INTEGER,
            operation_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_issued_return_source
        ON issued_return_requests(source_kind, source_id, status)
        """
    )


MIGRATION = {
    "version": "2026101201",
    "name": "semifinished claim and return hold",
    "upgrade": upgrade,
}
