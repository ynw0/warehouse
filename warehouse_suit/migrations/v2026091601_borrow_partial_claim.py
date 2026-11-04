"""Add claimed_quantity so borrow-to-claim can take a partial amount."""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def upgrade(conn):
    if "claimed_quantity" not in _columns(conn, "borrow_records"):
        conn.execute(
            'ALTER TABLE borrow_records ADD COLUMN claimed_quantity REAL NOT NULL DEFAULT 0'
        )


MIGRATION = {
    "version": "2026091601",
    "name": "borrow partial claim",
    "upgrade": upgrade,
}
