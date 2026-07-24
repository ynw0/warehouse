"""Material and shelf repository queries."""

from __future__ import annotations

import json

from .db import row_to_dict
from .inventory_constants import (
    INVENTORY_STATUS_AVAILABLE,
    STOCK_SOURCE_FORMAL,
    validate_stock_source,
)
from .material_utils import stock_record_display_type


def fetch_shelf(cursor, shelf_id):
    cursor.execute("SELECT * FROM shelves WHERE id = ?", (shelf_id,))
    shelf = row_to_dict(cursor.fetchone())
    if not shelf:
        return None
    shelf["layers"] = fetch_layers(cursor, shelf_id)
    return shelf


def fetch_layers(cursor, shelf_id):
    cursor.execute(
        "SELECT * FROM shelf_layers WHERE shelf_id = ? ORDER BY layer_number",
        (shelf_id,),
    )
    layers = []
    for row in cursor.fetchall():
        data = dict(row)
        data["zones"] = json.loads(data["zones"] or "[]")
        layers.append(data)
    return layers


def material_query(where="", params=(), stock_source=STOCK_SOURCE_FORMAL):
    stock_source = validate_stock_source(stock_source)
    base = """
        SELECT
            m.*,
            CASE WHEN cmp.id IS NULL THEN 0 ELSE 1 END AS is_common_material,
            cmp.warning_quantity AS common_warning_quantity,
            CASE
                WHEN COALESCE(fs.batch_count, 0) > 0 THEN COALESCE(fs.quantity, 0)
                ELSE COALESCE(i.quantity, 0)
            END AS quantity,
            CASE
                WHEN COALESCE(fs.batch_count, 0) > 0 THEN COALESCE(fs.amount, 0)
                ELSE COALESCE(i.amount, 0)
            END AS amount,
            mp.shelf_id,
            mp.layer_number,
            mp.zone_name,
            mp.slot_index,
            s.name AS shelf_name,
            s.warehouse_type,
            fs.batch_summary
        FROM materials m
        LEFT JOIN inventory i ON i.material_id = m.id
        LEFT JOIN (
            SELECT
                b.material_id,
                COUNT(*) AS batch_count,
                SUM(
                    CASE WHEN b.inventory_status = ?
                         THEN MAX(b.quantity - COALESCE(r.reserved_quantity, 0), 0)
                         ELSE 0 END
                ) AS quantity,
                SUM(
                    CASE WHEN b.inventory_status = ?
                         THEN MAX(b.quantity - COALESCE(r.reserved_quantity, 0), 0)
                              * b.unit_price
                         ELSE 0 END
                ) AS amount,
                GROUP_CONCAT(
                    CASE
                        WHEN b.inventory_status = ?
                             AND b.quantity - COALESCE(r.reserved_quantity, 0) > 0
                        THEN b.batch_no || ':' || printf(
                            '%g',
                            b.quantity - COALESCE(r.reserved_quantity, 0)
                        )
                    END,
                    '；'
                ) AS batch_summary
            FROM material_batches b
            LEFT JOIN (
                SELECT formal_batch_id,
                       SUM(reserved_quantity - consumed_quantity - released_quantity)
                           AS reserved_quantity
                FROM inventory_reservations
                WHERE status = 'active'
                  AND reserved_quantity - consumed_quantity - released_quantity > 0
                GROUP BY formal_batch_id
            ) r ON r.formal_batch_id = b.id
            WHERE b.stock_source = ?
            GROUP BY b.material_id
        ) fs ON fs.material_id = m.id
        LEFT JOIN common_material_profiles cmp ON cmp.material_id = m.id AND cmp.active = 1
        LEFT JOIN material_positions mp ON mp.material_id = m.id
        LEFT JOIN shelves s ON s.id = mp.shelf_id
    """
    query_params = (
        INVENTORY_STATUS_AVAILABLE,
        INVENTORY_STATUS_AVAILABLE,
        INVENTORY_STATUS_AVAILABLE,
        stock_source,
    ) + tuple(params)
    if where:
        base += f" WHERE {where}"
    base += """
        ORDER BY
            CASE
                WHEN (
                    CASE
                        WHEN COALESCE(fs.batch_count, 0) > 0 THEN COALESCE(fs.quantity, 0)
                        ELSE COALESCE(i.quantity, 0)
                    END
                ) > 0 THEN 0
                ELSE 1
            END,
            s.warehouse_type,
            s.id,
            mp.layer_number,
            mp.zone_name,
            m.material_code
    """
    return base, query_params


def fetch_material(cursor, material_id, stock_source=STOCK_SOURCE_FORMAL):
    stock_source = validate_stock_source(stock_source)
    sql, params = material_query("m.id = ?", (material_id,), stock_source=stock_source)
    cursor.execute(sql, params)
    material = row_to_dict(cursor.fetchone())
    if not material:
        return None
    cursor.execute(
        """
        SELECT * FROM stock_records
        WHERE material_id = ? AND stock_source = ?
        ORDER BY operation_date ASC, id ASC
        """,
        (material_id, stock_source),
    )
    records = []
    for row in cursor.fetchall():
        record = dict(row)
        record["display_type"] = stock_record_display_type(record)
        records.append(record)
    material["records"] = records
    cursor.execute(
        """
        SELECT b.*, s.name AS shelf_name,
               CAST(julianday('now') - julianday(b.received_date) AS INTEGER) AS age_days
        FROM material_batches b
        LEFT JOIN shelves s ON s.id = b.shelf_id
        WHERE b.material_id = ? AND b.stock_source = ?
        ORDER BY b.received_date ASC, b.id ASC
        """,
        (material_id, stock_source),
    )
    material["batches"] = [dict(row) for row in cursor.fetchall()]
    return material


def material_snapshot(cursor, material_id, stock_source=STOCK_SOURCE_FORMAL):
    material = fetch_material(cursor, material_id, stock_source=stock_source)
    if not material:
        return {}
    return {
        "material_id": material["id"],
        "material_code": material["material_code"],
        "name": material["name"],
        "brand_model": material.get("brand_model") or "",
        "spec": material.get("spec") or "",
        "purchase_applicant": material.get("purchase_applicant") or "",
        "unit": material.get("unit") or "",
        "quantity": material.get("quantity") or 0,
        "shelf_id": material.get("shelf_id"),
        "layer_number": material.get("layer_number"),
        "zone_name": material.get("zone_name") or "",
    }


def material_batch_rows(cursor, material_id, stock_source=STOCK_SOURCE_FORMAL):
    stock_source = validate_stock_source(stock_source)
    cursor.execute(
        """
        SELECT b.*, b.quantity AS physical_quantity,
               COALESCE(r.reserved_quantity, 0) AS reserved_quantity,
               MAX(b.quantity - COALESCE(r.reserved_quantity, 0), 0) AS quantity,
               s.name AS shelf_name,
               CAST(julianday('now') - julianday(b.received_date) AS INTEGER) AS age_days
        FROM material_batches b
        LEFT JOIN shelves s ON s.id = b.shelf_id
        LEFT JOIN (
            SELECT formal_batch_id,
                   SUM(reserved_quantity - consumed_quantity - released_quantity)
                       AS reserved_quantity
            FROM inventory_reservations
            WHERE status = 'active'
              AND reserved_quantity - consumed_quantity - released_quantity > 0
            GROUP BY formal_batch_id
        ) r ON r.formal_batch_id = b.id
        WHERE b.material_id = ?
          AND b.stock_source = ?
          AND b.inventory_status = ?
          AND b.quantity - COALESCE(r.reserved_quantity, 0) > 0
        ORDER BY b.received_date ASC, b.id ASC
        """,
        (material_id, stock_source, INVENTORY_STATUS_AVAILABLE),
    )
    return [dict(row) for row in cursor.fetchall()]


def material_stock_total(cursor, material_id, stock_source=STOCK_SOURCE_FORMAL):
    stock_source = validate_stock_source(stock_source)
    cursor.execute(
        """
        SELECT
            COUNT(*),
            COALESCE(SUM(
                CASE WHEN b.inventory_status = ?
                     THEN MAX(b.quantity - COALESCE(r.reserved_quantity, 0), 0)
                     ELSE 0 END
            ), 0)
        FROM material_batches b
        LEFT JOIN (
            SELECT formal_batch_id,
                   SUM(reserved_quantity - consumed_quantity - released_quantity)
                       AS reserved_quantity
            FROM inventory_reservations
            WHERE status = 'active'
              AND reserved_quantity - consumed_quantity - released_quantity > 0
            GROUP BY formal_batch_id
        ) r ON r.formal_batch_id = b.id
        WHERE b.material_id = ? AND b.stock_source = ?
        """,
        (INVENTORY_STATUS_AVAILABLE, material_id, stock_source),
    )
    batch_count, batch_total = cursor.fetchone()
    if int(batch_count or 0) > 0:
        return float(batch_total or 0)
    if stock_source != STOCK_SOURCE_FORMAL:
        return 0.0
    cursor.execute("SELECT COALESCE(quantity, 0) FROM inventory WHERE material_id = ?", (material_id,))
    row = cursor.fetchone()
    return float(row[0] if row else 0)


def build_fifo_plan(cursor, material_id, quantity, stock_source=STOCK_SOURCE_FORMAL):
    remain = float(quantity or 0)
    plan = []
    for batch in material_batch_rows(cursor, material_id, stock_source=stock_source):
        if remain <= 0:
            suggested = 0
        else:
            suggested = min(remain, float(batch.get("quantity") or 0))
            remain -= suggested
        batch["suggested_quantity"] = suggested
        plan.append(batch)
    return plan, remain
