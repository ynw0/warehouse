"""Align inventory batch warehouse types with their shelves."""

from __future__ import annotations


VERSION = "2026083101"
NAME = "batch warehouse alignment"


def upgrade(conn):
    conn.execute(
        """
        UPDATE material_batches AS batch
        SET warehouse_type = (
                SELECT shelf.warehouse_type
                FROM shelves AS shelf
                WHERE shelf.id = batch.shelf_id
            ),
            updated_at = datetime('now', 'localtime')
        WHERE batch.shelf_id IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM shelves AS shelf
              WHERE shelf.id = batch.shelf_id
                AND shelf.warehouse_type <> batch.warehouse_type
          )
        """
    )


MIGRATION = {
    "version": VERSION,
    "name": NAME,
    "upgrade": upgrade,
}
