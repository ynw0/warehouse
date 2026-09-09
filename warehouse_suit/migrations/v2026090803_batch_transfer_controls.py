"""Add batch-level transfer controls for temporary inventory."""


VERSION = "2026090803"
NAME = "temporary batch transfer controls"


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def upgrade(conn):
    columns = _columns(conn, "material_batches")
    if not columns:
        return
    if "transfer_blocked" not in columns:
        conn.execute(
            """
            ALTER TABLE material_batches
            ADD COLUMN transfer_blocked INTEGER NOT NULL DEFAULT 0
            CHECK (transfer_blocked IN (0, 1))
            """
        )
    if "transfer_block_reason" not in columns:
        conn.execute(
            """
            ALTER TABLE material_batches
            ADD COLUMN transfer_block_reason TEXT NOT NULL DEFAULT ''
            """
        )
    stock_record_columns = _columns(conn, "stock_records")
    if {"batch_id", "business_type"}.issubset(stock_record_columns):
        conn.execute(
            """
            UPDATE material_batches
            SET transfer_blocked = 1,
                transfer_block_reason = 'claim_return',
                updated_at = datetime('now', 'localtime')
            WHERE stock_source = 'temporary'
              AND id IN (
                  SELECT batch_id
                  FROM stock_records
                  WHERE business_type = 'claim_return_inbound'
                    AND batch_id IS NOT NULL
              )
            """
        )
    conn.execute(
        """
        CREATE INDEX idx_material_batches_temporary_transferable
        ON material_batches(
            material_id, stock_source, transfer_blocked, inventory_status, id
        )
        """
    )


MIGRATION = {"version": VERSION, "name": NAME, "upgrade": upgrade}
