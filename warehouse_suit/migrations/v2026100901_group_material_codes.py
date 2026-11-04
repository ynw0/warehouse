"""物料主数据上的集团分类码，以及赋码任务表。"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def upgrade(conn):
    if "materials" in _tables(conn):
        columns = _columns(conn, "materials")
        for name in ("group_category_code", "group_service_code", "group_standard_description"):
            if name not in columns:
                conn.execute(
                    f'ALTER TABLE materials ADD COLUMN {name} TEXT NOT NULL DEFAULT ""'
                )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS coding_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            form_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            input_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT NOT NULL DEFAULT '{}',
            error TEXT NOT NULL DEFAULT '',
            operator_name TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_coding_jobs_item ON coding_jobs(item_id, status, id)"
    )


MIGRATION = {
    "version": "2026100901",
    "name": "group material codes",
    "upgrade": upgrade,
}
