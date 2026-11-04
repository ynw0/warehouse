"""物料小助手的人工确认单。"""


def upgrade(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_confirmations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            summary TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        """
    )


MIGRATION = {
    "version": "2026101401",
    "name": "assistant confirmations",
    "upgrade": upgrade,
}
