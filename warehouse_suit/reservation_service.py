"""Reservation-aware formal stock availability helpers."""

from __future__ import annotations


ACTIVE_RESERVATION_STATUS = "active"


def batch_reserved_quantity(cursor, batch_id, exclude_auto_claim_id=0):
    params = [int(batch_id), ACTIVE_RESERVATION_STATUS]
    extra = ""
    if exclude_auto_claim_id:
        extra = " AND auto_claim_id <> ?"
        params.append(int(exclude_auto_claim_id))
    cursor.execute(
        f"""
        SELECT COALESCE(SUM(reserved_quantity - consumed_quantity - released_quantity), 0)
        FROM inventory_reservations
        WHERE formal_batch_id = ? AND status = ?
          AND reserved_quantity - consumed_quantity - released_quantity > 0
          {extra}
        """,
        params,
    )
    return float(cursor.fetchone()[0] or 0)


def material_reserved_quantity(cursor, material_id):
    cursor.execute(
        """
        SELECT COALESCE(SUM(reserved_quantity - consumed_quantity - released_quantity), 0)
        FROM inventory_reservations
        WHERE material_id = ? AND status = 'active'
          AND reserved_quantity - consumed_quantity - released_quantity > 0
        """,
        (int(material_id),),
    )
    return float(cursor.fetchone()[0] or 0)


def reservation_rows(cursor, auto_claim_id):
    cursor.execute(
        """
        SELECT r.*, b.batch_no, b.quantity AS physical_quantity,
               b.inventory_status, b.stock_source
        FROM inventory_reservations r
        JOIN material_batches b ON b.id = r.formal_batch_id
        WHERE r.auto_claim_id = ?
        ORDER BY b.received_date, b.id, r.id
        """,
        (int(auto_claim_id),),
    )
    return [dict(row) for row in cursor.fetchall()]

