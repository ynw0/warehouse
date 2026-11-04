"""Independent table for unsubmitted start-page drafts."""


def upgrade(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            applicant_id INTEGER NOT NULL,
            form_type TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (applicant_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_workflow_drafts_applicant_updated
        ON workflow_drafts (applicant_id, updated_at DESC)
        """
    )


MIGRATION = {
    "version": "2026091602",
    "name": "workflow drafts",
    "upgrade": upgrade,
}
