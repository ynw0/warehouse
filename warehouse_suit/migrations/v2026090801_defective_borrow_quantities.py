"""Add borrowed quantities for serialized defective production items."""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def upgrade(conn):
    for table in ("defective_semifinished_goods", "defective_finished_goods"):
        columns = _columns(conn, table)
        if not columns:
            continue
        if "borrowed_quantity" not in columns:
            conn.execute(
                f'ALTER TABLE "{table}" ADD COLUMN borrowed_quantity REAL NOT NULL DEFAULT 0'
            )
        if "updated_at" not in columns:
            conn.execute(
                f'ALTER TABLE "{table}" ADD COLUMN updated_at TEXT NOT NULL DEFAULT ""'
            )


MIGRATION = {
    "version": "2026090801",
    "name": "defective borrow quantities",
    "upgrade": upgrade,
}
