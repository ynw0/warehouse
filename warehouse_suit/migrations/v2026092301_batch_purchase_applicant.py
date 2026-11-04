"""采购申请人记在批次上。"""

def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def upgrade(conn):
    if "purchase_applicant" not in _columns(conn, "material_batches"):
        conn.execute(
            "ALTER TABLE material_batches ADD COLUMN purchase_applicant TEXT NOT NULL DEFAULT ''"
        )
    if "purchase_applicant" not in _columns(conn, "materials"):
        return
    conn.execute(
        """
        UPDATE material_batches
        SET purchase_applicant = (
            SELECT COALESCE(m.purchase_applicant, '')
            FROM materials m
            WHERE m.id = material_batches.material_id
        )
        WHERE TRIM(COALESCE(purchase_applicant, '')) = ''
        """
    )


MIGRATION = {
    "version": "2026092301",
    "name": "batch purchase applicant",
    "upgrade": upgrade,
}
