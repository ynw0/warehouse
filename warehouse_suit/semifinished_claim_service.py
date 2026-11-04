"""半成品领用：合格、不合格、报废都可以领，出库后才占用已领用数量。"""

from __future__ import annotations

import json

from warehouse_suit.db import now_text
from warehouse_suit.inventory_service import begin_inventory_transaction
from warehouse_suit.numbering import next_form_no
from warehouse_suit.purchase_project import purchase_project_from_acceptance_code
from warehouse_suit.settings import parse_json
from warehouse_suit.validation import quantity_value, validate_project_code
from warehouse_suit.workflow_service import (
    create_workflow_tasks,
    require_form_status,
    require_task_assignee,
    resolve_department_leader,
    workflow_assignees,
    workflow_generated_title,
)


SOURCE_QUALIFIED = "qualified"
SOURCE_DEFECTIVE = "defective"
SOURCE_SCRAPPED = "scrapped"
SOURCE_KINDS = (SOURCE_QUALIFIED, SOURCE_DEFECTIVE, SOURCE_SCRAPPED)
OPEN_STATUSES = ("completed", "rejected", "cancelled")


def inventory_status_label(item):
    """库存中心展示：已领用、已借用或在库。"""
    if int(item.get("claimed") or 0) == 1:
        return "已领用"
    claimed = float(item.get("claimed_quantity") or 0)
    used = float(item.get("used_quantity") or 0)
    if claimed - used > 1e-9:
        return "已领用"
    if float(item.get("borrowed_quantity") or 0) > 1e-9:
        return "已借用"
    return "在库"


def _pending_quantity(cursor, source_kind, source_id, exclude_form_id=None):
    cursor.execute(
        """
        SELECT COALESCE(SUM(wi.request_quantity), 0)
        FROM workflow_items wi
        JOIN workflow_forms f ON f.id = wi.form_id
        WHERE f.form_type = 'semifinished_claim'
          AND f.status NOT IN ('completed', 'rejected', 'cancelled', 'draft')
          AND json_valid(COALESCE(wi.data_json, '{}'))
          AND json_extract(wi.data_json, '$.source_kind') = ?
          AND CAST(json_extract(wi.data_json, '$.source_id') AS INTEGER) = ?
          AND (? IS NULL OR f.id != ?)
        """,
        (source_kind, int(source_id), exclude_form_id, exclude_form_id),
    )
    return float(cursor.fetchone()[0] or 0)


def _row_project(row):
    return purchase_project_from_acceptance_code(row.get("project_code") or "")


def list_claimable_semifinished(cursor, keyword="", purchase_project=""):
    """可领半成品。不合格按整件，合格和报废按数量。"""
    keyword = str(keyword or "").strip()
    project = str(purchase_project or "").strip()
    like = f"%{keyword}%"
    rows = []

    where = [
        "COALESCE(si.quantity, 0) - COALESCE(si.borrowed_quantity, 0) - MAX(COALESCE(si.used_quantity, 0), COALESCE(si.claimed_quantity, 0)) > 0"
    ]
    params = []
    if project:
        where.append("TRIM(COALESCE(sa.project_code, '')) = ?")
        params.append(project)
    if keyword:
        where.append("(si.name LIKE ? OR si.spec LIKE ? OR si.serial_no LIKE ?)")
        params.extend([like, like, like])
    cursor.execute(
        f"""
        SELECT si.id, si.name, si.spec, si.unit, si.serial_no, si.quantity, si.used_quantity,
               si.borrowed_quantity, si.claimed_quantity, si.cost_price,
               TRIM(COALESCE(sa.project_code, '')) AS project_code
        FROM semifinished_inventory si
        LEFT JOIN semifinished_acceptances sa ON sa.id = si.acceptance_id
        WHERE {' AND '.join(where)}
        ORDER BY si.id DESC
        """,
        params,
    )
    for row in cursor.fetchall():
        item = dict(row)
        physical = (
            float(item["quantity"] or 0)
            - float(item["borrowed_quantity"] or 0)
            - max(float(item["used_quantity"] or 0), float(item["claimed_quantity"] or 0))
        )
        pending = _pending_quantity(cursor, SOURCE_QUALIFIED, item["id"])
        available = physical - pending
        if available <= 1e-9:
            continue
        rows.append(
            {
                "source_kind": SOURCE_QUALIFIED,
                "source_id": int(item["id"]),
                "name": item["name"] or "",
                "spec": item["spec"] or "",
                "unit": item["unit"] or "",
                "serial_no": item["serial_no"] or "",
                "project_code": item["project_code"] or "",
                "purchase_project": _row_project(item),
                "available_quantity": available,
                "whole_item": False,
            }
        )

    defect_where = ["COALESCE(d.claimed, 0) = 0", "COALESCE(d.borrowed_quantity, 0) = 0"]
    defect_params = []
    if project:
        defect_where.append("TRIM(COALESCE(sa.project_code, '')) = ?")
        defect_params.append(project)
    if keyword:
        defect_where.append("(d.name LIKE ? OR d.spec LIKE ? OR d.serial_no LIKE ?)")
        defect_params.extend([like, like, like])
    cursor.execute(
        f"""
        SELECT d.id, d.name, d.spec, d.serial_no, TRIM(COALESCE(sa.project_code, '')) AS project_code
        FROM defective_semifinished_goods d
        LEFT JOIN semifinished_acceptances sa ON sa.id = d.semifinished_acceptance_id
        WHERE {' AND '.join(defect_where)}
        ORDER BY d.id DESC
        """,
        defect_params,
    )
    for row in cursor.fetchall():
        item = dict(row)
        if _pending_quantity(cursor, SOURCE_DEFECTIVE, item["id"]) >= 1 - 1e-9:
            continue
        rows.append(
            {
                "source_kind": SOURCE_DEFECTIVE,
                "source_id": int(item["id"]),
                "name": item["name"] or "",
                "spec": item["spec"] or "",
                "unit": "个",
                "serial_no": item["serial_no"] or "",
                "project_code": item["project_code"] or "",
                "purchase_project": _row_project(item),
                "available_quantity": 1,
                "whole_item": True,
            }
        )

    scrap_where = ["COALESCE(ss.quantity, 0) - COALESCE(ss.claimed_quantity, 0) > 0"]
    scrap_params = []
    if project:
        scrap_where.append("TRIM(COALESCE(sa.project_code, '')) = ?")
        scrap_params.append(project)
    if keyword:
        scrap_where.append("(ss.name LIKE ? OR ss.spec LIKE ? OR ss.serial_no LIKE ?)")
        scrap_params.extend([like, like, like])
    cursor.execute(
        f"""
        SELECT ss.id, ss.name, ss.spec, ss.unit, ss.serial_no, ss.quantity, ss.claimed_quantity,
               TRIM(COALESCE(sa.project_code, '')) AS project_code
        FROM scrapped_semifinished_goods ss
        LEFT JOIN semifinished_acceptances sa ON sa.id = ss.acceptance_id
        WHERE {' AND '.join(scrap_where)}
        ORDER BY ss.id DESC
        """,
        scrap_params,
    )
    for row in cursor.fetchall():
        item = dict(row)
        available = float(item["quantity"] or 0) - float(item["claimed_quantity"] or 0)
        available -= _pending_quantity(cursor, SOURCE_SCRAPPED, item["id"])
        if available <= 1e-9:
            continue
        rows.append(
            {
                "source_kind": SOURCE_SCRAPPED,
                "source_id": int(item["id"]),
                "name": item["name"] or "",
                "spec": item["spec"] or "",
                "unit": item["unit"] or "个",
                "serial_no": item["serial_no"] or "",
                "project_code": item["project_code"] or "",
                "purchase_project": _row_project(item),
                "available_quantity": available,
                "whole_item": False,
            }
        )
    return rows


def _load_source(cursor, source_kind, source_id):
    if source_kind == SOURCE_QUALIFIED:
        cursor.execute(
            """
            SELECT si.*, TRIM(COALESCE(sa.project_code, '')) AS project_code
            FROM semifinished_inventory si
            LEFT JOIN semifinished_acceptances sa ON sa.id = si.acceptance_id
            WHERE si.id = ?
            """,
            (int(source_id),),
        )
    elif source_kind == SOURCE_DEFECTIVE:
        cursor.execute(
            """
            SELECT d.*, TRIM(COALESCE(sa.project_code, '')) AS project_code
            FROM defective_semifinished_goods d
            LEFT JOIN semifinished_acceptances sa ON sa.id = d.semifinished_acceptance_id
            WHERE d.id = ?
            """,
            (int(source_id),),
        )
    elif source_kind == SOURCE_SCRAPPED:
        cursor.execute(
            """
            SELECT ss.*, TRIM(COALESCE(sa.project_code, '')) AS project_code
            FROM scrapped_semifinished_goods ss
            LEFT JOIN semifinished_acceptances sa ON sa.id = ss.acceptance_id
            WHERE ss.id = ?
            """,
            (int(source_id),),
        )
    else:
        raise ValueError("半成品来源不正确")
    row = cursor.fetchone()
    if not row:
        raise ValueError("半成品不存在")
    return dict(row)


def _available_now(cursor, source_kind, row, exclude_form_id=None):
    source_id = int(row["id"])
    pending = _pending_quantity(cursor, source_kind, source_id, exclude_form_id)
    if source_kind == SOURCE_QUALIFIED:
        physical = (
            float(row.get("quantity") or 0)
            - float(row.get("borrowed_quantity") or 0)
            - max(float(row.get("used_quantity") or 0), float(row.get("claimed_quantity") or 0))
        )
    elif source_kind == SOURCE_DEFECTIVE:
        if int(row.get("claimed") or 0) == 1 or float(row.get("borrowed_quantity") or 0) > 1e-9:
            physical = 0
        else:
            physical = 1
    else:
        physical = float(row.get("quantity") or 0) - float(row.get("claimed_quantity") or 0)
    return physical - pending


def create_semifinished_claim(cursor, user, data):
    """提交半成品领用，单号前缀 BC。库存要等库房出库才占用。"""
    items = data.get("items") or []
    if not items:
        raise ValueError("半成品领用至少需要一行")
    project_code = validate_project_code(data.get("project_code"))
    purchase_project = purchase_project_from_acceptance_code(project_code)
    begin_inventory_transaction(cursor.connection)
    prepared = []
    for raw in items:
        source_kind = str(raw.get("source_kind") or "").strip()
        if source_kind not in SOURCE_KINDS:
            raise ValueError("半成品来源只能是合格、不合格或报废")
        source_id = int(raw.get("source_id") or 0)
        row = _load_source(cursor, source_kind, source_id)
        if str(row.get("project_code") or "").strip() != project_code:
            raise ValueError("只能领用同一项目代号的半成品")
        if source_kind == SOURCE_DEFECTIVE:
            quantity = 1.0
        else:
            quantity = quantity_value(raw.get("quantity"), "领用数量", positive=True)
        available = _available_now(cursor, source_kind, row)
        if quantity > available + 1e-9:
            raise ValueError(f"{row.get('name') or row.get('serial_no') or '半成品'} 可领数量不足")
        prepared.append((source_kind, row, quantity))

    requested_leader_id = int(data.get("leader_id") or 0)
    leader_id = resolve_department_leader(
        cursor, user, "semifinished_claim", "leader_semifinished_claim", requested_leader_id
    )
    form_no = next_form_no(cursor, "BC")
    form_data = {
        "department": user.get("department") or "",
        "project_code": project_code,
        "purchase_project": purchase_project,
        "leader_ids": [leader_id],
    }
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, leader_id,
             data_json, created_at, updated_at)
        VALUES (?, 'semifinished_claim', ?, 'leader_semifinished_claim', 'leader_semifinished_claim', ?, ?, ?, ?, ?)
        """,
        (
            form_no,
            workflow_generated_title(user, form_no),
            int(user["id"]),
            leader_id,
            json.dumps(form_data, ensure_ascii=False),
            now_text(),
            now_text(),
        ),
    )
    form_id = int(cursor.lastrowid)
    for source_kind, row, quantity in prepared:
        name = row.get("name") or row.get("serial_no") or ""
        cursor.execute(
            """
            INSERT INTO workflow_items
                (form_id, material_code, material_name, spec, unit, request_quantity, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                form_id,
                row.get("serial_no") or "",
                name,
                row.get("spec") or "",
                row.get("unit") or "个",
                quantity,
                json.dumps(
                    {
                        "source_kind": source_kind,
                        "source_id": int(row["id"]),
                        "project_code": project_code,
                        "serial_no": row.get("serial_no") or "",
                    },
                    ensure_ascii=False,
                ),
            ),
        )
    create_workflow_tasks(cursor, form_id, "semifinished_claim", "leader_semifinished_claim", [leader_id])
    return form_id


def decide_semifinished_claim(cursor, user, form_id, data):
    form = dict(require_form_status(cursor, form_id, "semifinished_claim", "leader_semifinished_claim"))
    task = require_task_assignee(cursor, user, form_id, "leader_semifinished_claim")
    decision = data.get("decision") or "同意"
    remark = str(data.get("remark") or "").strip()
    if decision != "同意" and not remark:
        raise ValueError("不同意时必须填写审批意见")
    cursor.execute(
        """
        UPDATE workflow_tasks
        SET status = 'completed', decision = ?, signature = ?, signed_at = ?, data_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            decision,
            data.get("signature") or user["display_name"],
            data.get("signed_at") or now_text()[:10],
            json.dumps({"remark": remark}, ensure_ascii=False),
            now_text(),
            task["id"],
        ),
    )
    if decision == "同意":
        outbound_user_id = workflow_assignees(
            cursor,
            "semifinished_claim",
            "semifinished_outbound",
            [int(data.get("warehouse_user_id") or user["id"])],
        )[0]
        cursor.execute(
            """
            UPDATE workflow_forms
            SET status = 'semifinished_outbound', current_step = 'semifinished_outbound', updated_at = ?
            WHERE id = ?
            """,
            (now_text(), form_id),
        )
        create_workflow_tasks(
            cursor, form_id, "semifinished_claim", "semifinished_outbound", [outbound_user_id]
        )
    else:
        cursor.execute(
            """
            UPDATE workflow_forms
            SET status = 'rejected', current_step = 'completed', updated_at = ?
            WHERE id = ?
            """,
            (now_text(), form_id),
        )
    return form


def _occupy_source(cursor, source_kind, source_id, quantity):
    if source_kind == SOURCE_QUALIFIED:
        cursor.execute(
            """
            UPDATE semifinished_inventory
            SET claimed_quantity = COALESCE(claimed_quantity, 0) + ?, updated_at = ?
            WHERE id = ?
              AND COALESCE(quantity, 0) - COALESCE(borrowed_quantity, 0)
                  - MAX(COALESCE(used_quantity, 0), COALESCE(claimed_quantity, 0)) >= ?
            """,
            (quantity, now_text(), source_id, quantity),
        )
    elif source_kind == SOURCE_DEFECTIVE:
        cursor.execute(
            """
            UPDATE defective_semifinished_goods
            SET claimed = 1, updated_at = ?
            WHERE id = ? AND COALESCE(claimed, 0) = 0 AND COALESCE(borrowed_quantity, 0) = 0
            """,
            (now_text(), source_id),
        )
    else:
        cursor.execute(
            """
            UPDATE scrapped_semifinished_goods
            SET claimed_quantity = COALESCE(claimed_quantity, 0) + ?
            WHERE id = ?
              AND COALESCE(quantity, 0) - COALESCE(claimed_quantity, 0) >= ?
            """,
            (quantity, source_id, quantity),
        )
    if cursor.rowcount != 1:
        raise ValueError("半成品可领数量已变化，请刷新后重试")


def outbound_semifinished_claim(cursor, user, form_id, data):
    """流程中心出库。库存中心只随后显示已领用。"""
    require_form_status(cursor, form_id, "semifinished_claim", "semifinished_outbound")
    task = require_task_assignee(cursor, user, form_id, "semifinished_outbound")
    begin_inventory_transaction(cursor.connection)
    cursor.execute("SELECT * FROM workflow_items WHERE form_id = ? ORDER BY id", (form_id,))
    items = [dict(row) for row in cursor.fetchall()]
    if not items:
        raise ValueError("半成品领用明细不存在")
    for item in items:
        item_data = parse_json(item.get("data_json"), {})
        source_kind = str(item_data.get("source_kind") or "")
        source_id = int(item_data.get("source_id") or 0)
        quantity = float(item.get("request_quantity") or 0)
        row = _load_source(cursor, source_kind, source_id)
        available = _available_now(cursor, source_kind, row, exclude_form_id=form_id)
        if quantity > available + 1e-9:
            raise ValueError(f"{item.get('material_name') or '半成品'} 可领数量不足")
        _occupy_source(cursor, source_kind, source_id, quantity)
        cursor.execute(
            "UPDATE workflow_items SET outbound_quantity = ? WHERE id = ?",
            (quantity, item["id"]),
        )
    cursor.execute(
        """
        UPDATE workflow_tasks
        SET status = 'completed', decision = '已出库', signature = ?, signed_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            data.get("signature") or user["display_name"],
            data.get("outbound_date") or now_text()[:10],
            now_text(),
            task["id"],
        ),
    )
    cursor.execute(
        """
        UPDATE workflow_forms
        SET status = 'completed', current_step = 'completed', warehouse_user_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (user["id"], now_text(), form_id),
    )
    return form_id
