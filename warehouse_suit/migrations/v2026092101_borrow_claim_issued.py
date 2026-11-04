"""借用转领用单据、制作占用台账、通知分类。"""

from warehouse_suit.borrow_service import backfill_borrow_claim_forms
from warehouse_suit.issued_material_service import backfill_claim_consumptions
from warehouse_suit.notifications import backfill_notification_categories


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def upgrade(conn):
    if "category" not in _columns(conn, "notifications"):
        conn.execute("ALTER TABLE notifications ADD COLUMN category TEXT NOT NULL DEFAULT ''")
    # 补单和领用台账都会读写该列，必须先于 2026092102 存在。
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    for table in ("workflow_items", "material_batches", "materials"):
        if table in tables and "purchase_project" not in _columns(conn, table):
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN purchase_project TEXT NOT NULL DEFAULT \'\'')
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS claim_consumptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_kind TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            batch_id INTEGER,
            production_source_type TEXT NOT NULL,
            production_source_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_claim_consumptions_source ON claim_consumptions(source_kind, source_id, batch_id)"
    )
    cursor = conn.cursor()
    backfill_notification_categories(cursor)
    backfill_borrow_claim_forms(cursor)
    backfill_claim_consumptions(cursor)


MIGRATION = {
    "version": "2026092101",
    "name": "borrow claim forms and issued consumption",
    "upgrade": upgrade,
}
