"""Record claim returns against their original outbound ledger entries."""


def upgrade(conn):
    conn.execute("""
        CREATE TABLE claim_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outbound_record_id INTEGER NOT NULL REFERENCES stock_records(id) ON DELETE RESTRICT,
            inbound_record_id INTEGER NOT NULL UNIQUE REFERENCES stock_records(id) ON DELETE RESTRICT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            quantity REAL NOT NULL CHECK (quantity > 0),
            operation_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX idx_claim_returns_outbound ON claim_returns(outbound_record_id)")
    conn.execute("""
        ALTER TABLE temporary_issue_obligations ADD COLUMN returned_quantity
        REAL NOT NULL DEFAULT 0 CHECK (returned_quantity >= 0 AND returned_quantity <= issued_quantity)
    """)


MIGRATION = {"version": "2026090701", "name": "claim returns", "upgrade": upgrade}
