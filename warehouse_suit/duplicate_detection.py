# -*- coding: utf-8 -*-
"""Duplicate workflow detection helpers."""

from datetime import datetime, timedelta

from warehouse_suit.material_repository import material_snapshot
from warehouse_suit.settings import workflow_settings


def duplicate_check_days(cursor):
    return int(workflow_settings(cursor).get("duplicate_acceptance_check_days") or 7)


def duplicate_norm(value):
    return str(value or "").strip()


def duplicate_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def duplicate_material_values(cursor, item):
    material_id = int(item.get("material_id") or 0)
    snap = None
    if material_id:
        snap = material_snapshot(cursor, material_id)
    if not snap and item.get("material_code"):
        cursor.execute("SELECT id FROM materials WHERE material_code = ?", (str(item.get("material_code") or "").strip(),))
        row = cursor.fetchone()
        if row:
            snap = material_snapshot(cursor, row["id"])
    return {
        "material_name": duplicate_norm((snap or {}).get("name") or item.get("material_name") or item.get("name")),
        "brand_model": duplicate_norm((snap or {}).get("brand_model") or item.get("brand_model")),
        "spec": duplicate_norm((snap or {}).get("spec") or item.get("spec")),
        "purchase_applicant": duplicate_norm(item.get("purchase_applicant") or (snap or {}).get("purchase_applicant")),
        "unit": duplicate_norm((snap or {}).get("unit") or item.get("unit")),
        "request_quantity": duplicate_float(item.get("purchase_quantity") or item.get("request_quantity")),
        "arrival_quantity": duplicate_float(item.get("arrival_quantity")),
        "unit_price": duplicate_float(item.get("unit_price")),
    }


def duplicate_acceptance_match_rows(cursor, form_type, values, days):
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    if form_type == "acceptance":
        cursor.execute(
            """
            SELECT f.id AS form_id, f.form_no, f.title, f.created_at, wi.material_name, wi.brand_model, wi.spec
            FROM workflow_items wi
            JOIN workflow_forms f ON f.id = wi.form_id
            WHERE f.form_type = 'acceptance'
              AND f.created_at >= ?
              AND f.status <> 'cancelled'
              AND TRIM(COALESCE(wi.material_name, '')) = ?
              AND TRIM(COALESCE(wi.brand_model, '')) = ?
              AND TRIM(COALESCE(wi.spec, '')) = ?
              AND TRIM(COALESCE(wi.purchase_applicant, '')) = ?
              AND TRIM(COALESCE(wi.unit, '')) = ?
              AND ABS(COALESCE(wi.request_quantity, 0) - ?) < 0.0000001
              AND ABS(COALESCE(wi.arrival_quantity, 0) - ?) < 0.0000001
              AND ABS(COALESCE(wi.unit_price, 0) - ?) < 0.0000001
            ORDER BY f.id DESC
            LIMIT 5
            """,
            (
                cutoff,
                values["material_name"],
                values["brand_model"],
                values["spec"],
                values["purchase_applicant"],
                values["unit"],
                values["request_quantity"],
                values["arrival_quantity"],
                values["unit_price"],
            ),
        )
    else:
        cursor.execute(
            """
            SELECT f.id AS form_id, f.form_no, f.title, f.created_at, wi.material_name, wi.brand_model, wi.spec
            FROM workflow_items wi
            JOIN workflow_forms f ON f.id = wi.form_id
            WHERE f.form_type = ?
              AND f.created_at >= ?
              AND f.status <> 'cancelled'
              AND TRIM(COALESCE(wi.material_name, '')) = ?
              AND TRIM(COALESCE(wi.spec, '')) = ?
              AND TRIM(COALESCE(wi.unit, '')) = ?
              AND ABS(COALESCE(wi.arrival_quantity, 0) - ?) < 0.0000001
              AND ABS(COALESCE(wi.unit_price, 0) - ?) < 0.0000001
            ORDER BY f.id DESC
            LIMIT 5
            """,
            (
                form_type,
                cutoff,
                values["material_name"],
                values["spec"],
                values["unit"],
                values["arrival_quantity"],
                values["unit_price"],
            ),
        )
    return [dict(row) for row in cursor.fetchall()]
