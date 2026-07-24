"""Read-only data aggregation for the material-system dashboard."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from warehouse_suit.db import today_text
from warehouse_suit.inventory_constants import (
    BUSINESS_TYPE_BORROW_RETURN_INBOUND,
    BUSINESS_TYPE_CLAIM_OUTBOUND,
    BUSINESS_TYPE_TEMPORARY_MANUAL_ADJUST_IN,
    BUSINESS_TYPE_TEMPORARY_MANUAL_ADJUST_OUT,
    INVENTORY_STATUS_AVAILABLE,
    STOCK_SOURCE_FORMAL,
    STOCK_SOURCE_TEMPORARY,
)
from warehouse_suit.settings import get_setting, temporary_inventory_enabled
from warehouse_suit.temporary_inventory_visibility import temporary_workflow_sql
from warehouse_suit.todo_service import pending_tasks_for_user
from warehouse_suit.extended_service import formal_available_quantity


LOGGER = logging.getLogger(__name__)

_TRANSFER_PENDING_STATUSES = (
    "awaiting_purchase",
    "acceptance_in_progress",
    "acceptance_failed",
    "formal_inbound_partial",
    "exception",
)
_ACCEPTANCE_FORM_TYPES = ("acceptance", "semifinished", "finished")


def empty_dashboard_overview():
    """Return the public dashboard shape with safe zero-value defaults."""
    return {
        "summary": {
            "total_materials": 0,
            "total_amount": 0.0,
            "research_materials": 0,
            "office_materials": 0,
            "today_inbound": 0,
            "today_outbound": 0,
            "month_inbound": 0,
            "month_outbound": 0,
        },
        "inventory": {
            "total_stock": 0,
            "formal_stock": 0,
            "temporary_stock": 0,
            "formal_available": 0,
            "temporary_available": 0,
            "low_stock_count": 0,
            "low_stock_items": [],
            "zero_stock_count": 0,
            "pending_transfer": 0,
        },
        "business": {
            "today_inbound": 0,
            "today_issue": 0,
            "today_borrow": 0,
            "today_return": 0,
            "today_adjust": 0,
        },
        "borrow": {
            "unreturned": 0,
            "overdue": 0,
            "today_borrow": 0,
            "today_return": 0,
        },
        "workflow": {
            "pending_issue": 0,
            "pending_borrow": 0,
            "pending_return": 0,
            "pending_acceptance": 0,
            "pending_inbound": 0,
            "applicant_revision": 0,
            "pending_transfer": 0,
        },
        "trend": _empty_trend(),
        "categories": [],
        "todos": [],
        "alerts": [],
        "inventory_check": {
            "next_date": None,
            "days_remaining": None,
            "status": "unset",
        },
        "settings": {"temporary_warehouse_enabled": False},
    }


def _safe(section, fallback, callback):
    try:
        return callback()
    except (sqlite3.Error, ValueError, TypeError) as exc:
        LOGGER.exception("Dashboard %s query failed: %s", section, exc)
        return fallback
    except Exception as exc:  # Keep a non-critical dashboard card from breaking the whole page.
        LOGGER.exception("Dashboard %s aggregation failed: %s", section, exc)
        return fallback


def _float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _number(value):
    numeric = _float(value)
    return int(numeric) if numeric.is_integer() else numeric


def _sources(temporary_enabled):
    return [STOCK_SOURCE_FORMAL, STOCK_SOURCE_TEMPORARY] if temporary_enabled else [STOCK_SOURCE_FORMAL]


def _placeholders(values):
    return ", ".join("?" for _ in values)


def _month_keys(today):
    year, month = (int(part) for part in today[:7].split("-"))
    result = []
    for offset in range(11, -1, -1):
        value = year * 12 + (month - 1) - offset
        item_year, item_month = divmod(value, 12)
        result.append(f"{item_year:04d}-{item_month + 1:02d}")
    return result


def _empty_trend(today=None):
    today = today or today_text()
    return [
        {"month": month, "label": month[5:], "inbound": 0, "outbound": 0}
        for month in _month_keys(today)
    ]


def _inventory_metrics(cursor, temporary_enabled):
    sources = _sources(temporary_enabled)
    source_marks = _placeholders(sources)
    cursor.execute(
        f"""
        WITH active_reservations AS (
            SELECT formal_batch_id,
                   SUM(MAX(reserved_quantity - consumed_quantity - released_quantity, 0)) AS reserved_quantity
            FROM inventory_reservations
            WHERE status = 'active'
            GROUP BY formal_batch_id
        )
        SELECT
            COUNT(*) AS batch_count,
            COALESCE(SUM(CASE WHEN b.stock_source = ?
                                AND b.inventory_status <> 'transferred'
                              THEN b.quantity ELSE 0 END), 0) AS formal_stock,
            COALESCE(SUM(CASE WHEN b.stock_source = ?
                                AND b.inventory_status <> 'transferred'
                              THEN b.quantity ELSE 0 END), 0) AS temporary_stock,
            COALESCE(SUM(CASE WHEN b.stock_source = ?
                                AND b.inventory_status = ?
                              THEN MAX(b.quantity - COALESCE(r.reserved_quantity, 0), 0) ELSE 0 END), 0) AS formal_available,
            COALESCE(SUM(CASE WHEN b.stock_source = ?
                                AND b.inventory_status = ?
                              THEN b.quantity ELSE 0 END), 0) AS temporary_available,
            COALESCE(SUM(CASE WHEN b.stock_source = ?
                                AND b.inventory_status = ?
                              THEN b.quantity * b.unit_price ELSE 0 END), 0) AS total_amount,
            COALESCE(SUM(CASE WHEN b.stock_source = ?
                                AND b.inventory_status = ? AND b.warehouse_type = 'rd'
                              THEN b.quantity ELSE 0 END), 0) AS research_materials,
            COALESCE(SUM(CASE WHEN b.stock_source = ?
                                AND b.inventory_status = ? AND b.warehouse_type = 'office'
                              THEN b.quantity ELSE 0 END), 0) AS office_materials
        FROM material_batches b
        LEFT JOIN active_reservations r ON r.formal_batch_id = b.id
        WHERE b.stock_source IN ({source_marks})
        """,
        [
            STOCK_SOURCE_FORMAL,
            STOCK_SOURCE_TEMPORARY,
            STOCK_SOURCE_FORMAL,
            INVENTORY_STATUS_AVAILABLE,
            STOCK_SOURCE_TEMPORARY,
            INVENTORY_STATUS_AVAILABLE,
            STOCK_SOURCE_FORMAL,
            INVENTORY_STATUS_AVAILABLE,
            STOCK_SOURCE_FORMAL,
            INVENTORY_STATUS_AVAILABLE,
            STOCK_SOURCE_FORMAL,
            INVENTORY_STATUS_AVAILABLE,
            *sources,
        ],
    )
    row = dict(cursor.fetchone() or {})
    metrics = {
        "formal_stock": _number(row.get("formal_stock")),
        "temporary_stock": _number(row.get("temporary_stock")) if temporary_enabled else 0,
        "formal_available": _number(row.get("formal_available")),
        "temporary_available": _number(row.get("temporary_available")) if temporary_enabled else 0,
        "total_amount": round(_float(row.get("total_amount")), 2),
        "research_materials": _number(row.get("research_materials")),
        "office_materials": _number(row.get("office_materials")),
    }

    # Older databases may still contain only the legacy formal inventory mirror.
    if not int(row.get("batch_count") or 0):
        cursor.execute("SELECT COALESCE(SUM(quantity), 0), COALESCE(SUM(amount), 0) FROM inventory")
        formal_stock, total_amount = cursor.fetchone()
        metrics.update(
            {
                "formal_stock": _number(formal_stock),
                "formal_available": _number(formal_stock),
                "total_amount": round(_float(total_amount), 2),
            }
        )
        cursor.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN s.warehouse_type = 'rd' THEN i.quantity ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN COALESCE(s.warehouse_type, 'office') = 'office'
                                  THEN i.quantity ELSE 0 END), 0)
            FROM inventory i
            JOIN materials m ON m.id = i.material_id
            LEFT JOIN material_positions mp ON mp.material_id = m.id
            LEFT JOIN shelves s ON s.id = mp.shelf_id
            """
        )
        research, office = cursor.fetchone()
        metrics["research_materials"] = _number(research)
        metrics["office_materials"] = _number(office)

    metrics["total_stock"] = _number(metrics["formal_stock"] + metrics["temporary_stock"])
    return metrics


def _zero_stock_count(cursor, temporary_enabled):
    sources = _sources(temporary_enabled)
    cursor.execute(
        f"""
        WITH material_stock AS (
            SELECT material_id, COALESCE(SUM(quantity), 0) AS quantity
            FROM material_batches
            WHERE stock_source IN ({_placeholders(sources)})
              AND inventory_status <> 'transferred'
            GROUP BY material_id
        )
        SELECT COUNT(*)
        FROM materials m
        LEFT JOIN material_stock ms ON ms.material_id = m.id
        WHERE COALESCE(ms.quantity, 0) <= 0
        """,
        sources,
    )
    return int(cursor.fetchone()[0] or 0)


def _stock_record_metrics(cursor, today, temporary_enabled):
    sources = _sources(temporary_enabled)
    source_marks = _placeholders(sources)
    month_start = today[:7] + "-01"
    cursor.execute(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN stock_source = ? AND operation_type = 'in'
                                AND substr(operation_date, 1, 10) = ?
                              THEN quantity ELSE 0 END), 0) AS today_inbound,
            COALESCE(SUM(CASE WHEN stock_source = ? AND operation_type = 'out'
                                AND substr(operation_date, 1, 10) = ?
                              THEN quantity ELSE 0 END), 0) AS today_outbound,
            COALESCE(SUM(CASE WHEN stock_source = ? AND operation_type = 'in'
                                AND substr(operation_date, 1, 10) BETWEEN ? AND ?
                              THEN quantity ELSE 0 END), 0) AS month_inbound,
            COALESCE(SUM(CASE WHEN stock_source = ? AND operation_type = 'out'
                                AND substr(operation_date, 1, 10) BETWEEN ? AND ?
                              THEN quantity ELSE 0 END), 0) AS month_outbound,
            COALESCE(SUM(CASE WHEN stock_source IN ({source_marks}) AND operation_type = 'in'
                                AND substr(operation_date, 1, 10) = ?
                              THEN quantity ELSE 0 END), 0) AS business_inbound,
            COALESCE(SUM(CASE WHEN stock_source IN ({source_marks}) AND operation_type = 'out'
                                AND business_type = ? AND substr(operation_date, 1, 10) = ?
                              THEN quantity ELSE 0 END), 0) AS today_issue,
            COALESCE(SUM(CASE WHEN stock_source IN ({source_marks}) AND operation_type = 'in'
                                AND business_type = ? AND substr(operation_date, 1, 10) = ?
                              THEN quantity ELSE 0 END), 0) AS today_return,
            COALESCE(SUM(CASE WHEN stock_source IN ({source_marks})
                                AND business_type IN (?, ?)
                                AND substr(operation_date, 1, 10) = ?
                              THEN quantity ELSE 0 END), 0) AS today_adjust
        FROM stock_records
        """,
        [
            STOCK_SOURCE_FORMAL,
            today,
            STOCK_SOURCE_FORMAL,
            today,
            STOCK_SOURCE_FORMAL,
            month_start,
            today,
            STOCK_SOURCE_FORMAL,
            month_start,
            today,
            *sources,
            today,
            *sources,
            BUSINESS_TYPE_CLAIM_OUTBOUND,
            today,
            *sources,
            BUSINESS_TYPE_BORROW_RETURN_INBOUND,
            today,
            *sources,
            BUSINESS_TYPE_TEMPORARY_MANUAL_ADJUST_IN,
            BUSINESS_TYPE_TEMPORARY_MANUAL_ADJUST_OUT,
            today,
        ],
    )
    row = dict(cursor.fetchone() or {})
    return {key: _number(value) for key, value in row.items()}


def _borrow_metrics(cursor, today, temporary_enabled):
    sources = _sources(temporary_enabled)
    source_marks = _placeholders(sources)
    cursor.execute(
        f"""
        SELECT
            COUNT(CASE WHEN br.status <> 'returned'
                          AND br.quantity > br.returned_quantity THEN 1 END) AS unreturned,
            COUNT(CASE WHEN br.status <> 'returned'
                          AND br.quantity > br.returned_quantity
                          AND json_valid(COALESCE(f.data_json, '{{}}'))
                          AND NULLIF(json_extract(f.data_json, '$.expected_return_date'), '') IS NOT NULL
                          AND json_extract(f.data_json, '$.expected_return_date') < ?
                       THEN 1 END) AS overdue,
            COALESCE(SUM(CASE WHEN COALESCE(NULLIF(br.outbound_date, ''), substr(br.created_at, 1, 10)) = ?
                              THEN br.quantity ELSE 0 END), 0) AS today_borrow
        FROM borrow_records br
        LEFT JOIN workflow_forms f ON f.id = br.borrow_form_id
        WHERE br.stock_source IN ({source_marks})
        """,
        [today, today, *sources],
    )
    row = dict(cursor.fetchone() or {})
    return {key: _number(value) for key, value in row.items()}


def _workflow_metrics(cursor, temporary_enabled):
    where = ["t.status = 'pending'"]
    params = []
    if not temporary_enabled:
        temporary_sql, temporary_params = temporary_workflow_sql("f")
        where.append(f"NOT {temporary_sql}")
        params.extend(temporary_params)
    acceptance_marks = _placeholders(_ACCEPTANCE_FORM_TYPES)
    cursor.execute(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN f.form_type = 'claim' AND t.step_code = 'leader_claim'
                              THEN 1 ELSE 0 END), 0) AS pending_issue,
            COALESCE(SUM(CASE WHEN f.form_type = 'borrow' AND t.step_code = 'leader_borrow'
                              THEN 1 ELSE 0 END), 0) AS pending_borrow,
            COALESCE(SUM(CASE WHEN f.form_type = 'borrow_return' AND t.step_code = 'return_inbound'
                              THEN 1 ELSE 0 END), 0) AS pending_return,
            COALESCE(SUM(CASE WHEN f.form_type IN ({acceptance_marks}) AND t.step_code = 'acceptance'
                              THEN 1 ELSE 0 END), 0) AS pending_acceptance,
            COALESCE(SUM(CASE WHEN f.form_type IN ({acceptance_marks}) AND t.step_code = 'inbound'
                              THEN 1 ELSE 0 END), 0) AS pending_inbound,
            COALESCE(SUM(CASE WHEN t.step_code = 'applicant_revision'
                              THEN 1 ELSE 0 END), 0) AS applicant_revision
        FROM workflow_tasks t
        JOIN workflow_forms f ON f.id = t.form_id
        WHERE {' AND '.join(where)}
        """,
        [*_ACCEPTANCE_FORM_TYPES, *_ACCEPTANCE_FORM_TYPES, *params],
    )
    row = dict(cursor.fetchone() or {})
    return {key: int(value or 0) for key, value in row.items()}


def _pending_transfer_count(cursor):
    cursor.execute(
        f"""
        SELECT COUNT(DISTINCT material_id)
        FROM inventory_transfer_tasks
        WHERE status IN ({_placeholders(_TRANSFER_PENDING_STATUSES)})
        """,
        _TRANSFER_PENDING_STATUSES,
    )
    return int(cursor.fetchone()[0] or 0)


def _trend(cursor, today):
    rows = _empty_trend(today)
    by_month = {item["month"]: item for item in rows}
    first_month = rows[0]["month"]
    cursor.execute(
        """
        SELECT substr(operation_date, 1, 7) AS month,
               COALESCE(SUM(CASE WHEN operation_type = 'in' THEN quantity ELSE 0 END), 0) AS inbound,
               COALESCE(SUM(CASE WHEN operation_type = 'out' THEN quantity ELSE 0 END), 0) AS outbound
        FROM stock_records
        WHERE stock_source = ?
          AND substr(operation_date, 1, 7) BETWEEN ? AND ?
        GROUP BY substr(operation_date, 1, 7)
        """,
        (STOCK_SOURCE_FORMAL, first_month, today[:7]),
    )
    for row in cursor.fetchall():
        item = by_month.get(row["month"])
        if item:
            item["inbound"] = _number(row["inbound"])
            item["outbound"] = _number(row["outbound"])
    return rows


def _categories(cursor, temporary_enabled):
    sources = _sources(temporary_enabled)
    cursor.execute(
        f"""
        SELECT COALESCE(NULLIF(TRIM(m.category_name), ''), NULLIF(TRIM(m.category), ''), '未分类') AS name,
               COALESCE(SUM(b.quantity), 0) AS stock_quantity
        FROM material_batches b
        JOIN materials m ON m.id = b.material_id
        WHERE b.stock_source IN ({_placeholders(sources)})
          AND b.inventory_status <> 'transferred'
        GROUP BY COALESCE(NULLIF(TRIM(m.category_name), ''), NULLIF(TRIM(m.category), ''), '未分类')
        HAVING COALESCE(SUM(b.quantity), 0) > 0
        ORDER BY stock_quantity DESC, name ASC
        LIMIT 5
        """,
        sources,
    )
    return [
        {"name": str(row["name"] or "未分类"), "stock_quantity": _number(row["stock_quantity"])}
        for row in cursor.fetchall()
    ]


def _inventory_check(cursor, today):
    date_text = str(get_setting(cursor, "next_stocktake_date", "") or "").strip()[:10]
    if not date_text:
        return {"next_date": None, "days_remaining": None, "status": "unset"}
    try:
        due_date = datetime.strptime(date_text, "%Y-%m-%d").date()
        current_date = datetime.strptime(today, "%Y-%m-%d").date()
    except ValueError:
        LOGGER.warning("Ignoring invalid next_stocktake_date setting: %s", date_text)
        return {"next_date": None, "days_remaining": None, "status": "unset"}
    remaining = (due_date - current_date).days
    return {
        "next_date": date_text,
        "days_remaining": remaining,
        "status": "overdue" if remaining < 0 else ("due" if remaining == 0 else "upcoming"),
    }


def _common_low_stock(cursor):
    cursor.execute('SELECT p.material_id, p.warning_quantity, m.material_code, m.name, m.unit FROM common_material_profiles p JOIN materials m ON m.id=p.material_id WHERE p.active=1 ORDER BY m.material_code')
    items = []
    for row in cursor.fetchall():
        current = formal_available_quantity(cursor, row['material_id'])
        threshold = float(row['warning_quantity'] or 0)
        if current < threshold:
            items.append({'material_id': int(row['material_id']), 'material_code': row['material_code'] or '', 'name': row['name'] or '', 'unit': row['unit'] or '', 'quantity': current, 'warning_quantity': threshold})
    return items


def _alerts(inventory, borrow, workflow, inventory_check):
    alerts = []
    if int(borrow.get("overdue") or 0):
        alerts.append({"level": "critical", "text": f"{int(borrow['overdue'])} 笔借用已超过预计归还日", "date": today_text(), "action": "borrow_overdue"})
    if inventory_check.get("status") == "overdue":
        days = abs(int(inventory_check.get("days_remaining") or 0))
        alerts.append({"level": "critical", "text": f"盘点已逾期 {days} 天", "date": inventory_check.get("next_date") or ""})
    if int(inventory.get("low_stock_count") or 0):
        alerts.append({"level": "critical", "text": f"{int(inventory['low_stock_count'])} 项物料低于库存下限", "date": today_text(), "action": "common_low_stock"})
    if int(workflow.get("pending_transfer") or 0):
        alerts.append({"level": "warning", "text": f"{int(workflow['pending_transfer'])} 项临时库物料待转正式库", "date": today_text()})
    if int(inventory.get("zero_stock_count") or 0):
        alerts.append({"level": "warning", "text": f"{int(inventory['zero_stock_count'])} 种物料当前零库存", "date": today_text()})
    return alerts[:6]


def build_dashboard_overview(cursor, user):
    """Build the dashboard payload without writing to the database.

    Stock totals use batch rows by source.  Category rows deliberately aggregate
    each warehouse/source batch, so the same material in formal and temporary
    inventory is not deduplicated into an incorrect physical-stock total.
    """
    today = today_text()
    payload = empty_dashboard_overview()
    temporary_enabled = bool(_safe("temporary setting", False, lambda: temporary_inventory_enabled(cursor)))
    payload["settings"]["temporary_warehouse_enabled"] = temporary_enabled

    inventory = _safe("inventory", payload["inventory"].copy(), lambda: _inventory_metrics(cursor, temporary_enabled))
    inventory["zero_stock_count"] = _safe("zero stock", 0, lambda: _zero_stock_count(cursor, temporary_enabled))
    low_stock_items = _safe('common low stock', [], lambda: _common_low_stock(cursor))
    inventory['low_stock_count'] = len(low_stock_items)
    inventory['low_stock_items'] = low_stock_items[:20]
    pending_transfer = _safe("pending transfer", 0, lambda: _pending_transfer_count(cursor)) if temporary_enabled else 0
    inventory["pending_transfer"] = pending_transfer
    payload["inventory"].update(inventory)

    records = _safe("stock records", {}, lambda: _stock_record_metrics(cursor, today, temporary_enabled))
    payload["summary"].update(
        {
            "total_amount": round(_float(inventory.get("total_amount")), 2),
            "research_materials": _number(inventory.get("research_materials")),
            "office_materials": _number(inventory.get("office_materials")),
            "today_inbound": _number(records.get("today_inbound")),
            "today_outbound": _number(records.get("today_outbound")),
            "month_inbound": _number(records.get("month_inbound")),
            "month_outbound": _number(records.get("month_outbound")),
        }
    )
    payload["business"].update(
        {
            "today_inbound": _number(records.get("business_inbound")),
            "today_issue": _number(records.get("today_issue")),
            "today_return": _number(records.get("today_return")),
            "today_adjust": _number(records.get("today_adjust")),
        }
    )
    payload["summary"]["total_materials"] = _safe(
        "material count", 0, lambda: int(cursor.execute("SELECT COUNT(*) FROM materials").fetchone()[0] or 0)
    )

    borrow = _safe("borrow", payload["borrow"].copy(), lambda: _borrow_metrics(cursor, today, temporary_enabled))
    borrow["today_return"] = _number(records.get("today_return"))
    payload["borrow"].update(borrow)
    payload["business"]["today_borrow"] = _number(borrow.get("today_borrow"))

    workflow = _safe("workflow", payload["workflow"].copy(), lambda: _workflow_metrics(cursor, temporary_enabled))
    workflow["pending_transfer"] = pending_transfer
    payload["workflow"].update(workflow)
    payload["trend"] = _safe("trend", _empty_trend(today), lambda: _trend(cursor, today))
    payload["categories"] = _safe("categories", [], lambda: _categories(cursor, temporary_enabled))
    payload["todos"] = _safe("todos", [], lambda: pending_tasks_for_user(cursor, user, limit=12))
    payload["inventory_check"] = _safe("inventory check", payload["inventory_check"], lambda: _inventory_check(cursor, today))
    payload["alerts"] = _alerts(payload["inventory"], payload["borrow"], payload["workflow"], payload["inventory_check"])
    return payload
