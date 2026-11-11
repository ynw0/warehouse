"""增加成品领用占用和已领物料报废台账。"""


def upgrade(conn):
    for table, column, definition in (
        ("finished_good_inventory", "claimed_quantity", "REAL NOT NULL DEFAULT 0"),
        ("defective_finished_goods", "claimed", "INTEGER NOT NULL DEFAULT 0"),
        ("scrapped_finished_goods", "claimed_quantity", "REAL NOT NULL DEFAULT 0"),
    ):
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone():
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS issued_material_scraps (
            id INTEGER PRIMARY KEY,
            source_kind TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            batch_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            quantity REAL NOT NULL CHECK (quantity > 0),
            operation_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_issued_scrap_lot
        ON issued_material_scraps(source_kind, source_id, batch_id)
    """)


MIGRATION = {
    "version": "2026111101",
    "name": "product claim and issued material scrap",
    "upgrade": upgrade,
}
