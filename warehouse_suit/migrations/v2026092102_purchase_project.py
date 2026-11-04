"""验收行、入库批次记录采购申请项目。"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _ensure_column(conn, table, column, definition):
    if column in _columns(conn, table):
        return
    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} {definition}')


def upgrade(conn):
    _ensure_column(conn, "workflow_items", "purchase_project", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "material_batches", "purchase_project", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "materials", "purchase_project", "TEXT NOT NULL DEFAULT ''")


MIGRATION = {
    "version": "2026092102",
    "name": "purchase project on batches",
    "upgrade": upgrade,
}
