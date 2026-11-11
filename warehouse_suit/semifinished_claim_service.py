"""半成品和成品按设备编号领用。"""

from __future__ import annotations

import json

from warehouse_suit.db import now_text
from warehouse_suit.inventory_service import begin_inventory_transaction, pending_product_borrow_quantity
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


def _pending_quantity(cursor, source_kind, source_id, exclude_form_id=None, kind="semifinished"):
    cursor.execute(
        """
        SELECT COALESCE(SUM(wi.request_quantity), 0)
        FROM workflow_items wi
        JOIN workflow_forms f ON f.id = wi.form_id
        WHERE f.form_type = ?
          AND f.status NOT IN ('completed', 'rejected', 'cancelled', 'draft')
          AND json_valid(COALESCE(wi.data_json, '{}'))
          AND json_extract(wi.data_json, '$.source_kind') = ?
          AND CAST(json_extract(wi.data_json, '$.source_id') AS INTEGER) = ?
          AND (? IS NULL OR f.id != ?)
        """,
        (f"{kind}_claim", source_kind, int(source_id), exclude_form_id, exclude_form_id),
    )
    return float(cursor.fetchone()[0] or 0)


# 库表由固定配置选择，外部输入不能成为 SQL 标识符。
SOURCE_TABLES = {
    "semifinished": ("semifinished_inventory", "defective_semifinished_goods", "scrapped_semifinished_goods", "name"),
    "finished": ("finished_good_inventory", "defective_finished_goods", "scrapped_finished_goods", "product_name"),
}


def _source_query(kind, source_kind):
    qualified, defective, scrapped, name = SOURCE_TABLES[kind]
    table = dict(zip(SOURCE_KINDS, (qualified, defective, scrapped)))[source_kind]
    acceptance_key = f"{kind}_acceptance_id" if source_kind == SOURCE_DEFECTIVE else "acceptance_id"
    return (
        f"SELECT i.*, i.{name} AS name, TRIM(COALESCE(a.project_code, '')) AS project_code "
        f"FROM {table} i LEFT JOIN {kind}_acceptances a ON a.id = i.{acceptance_key}"
    )


def list_claimable_semifinished(cursor, keyword="", purchase_project="", kind="semifinished"):
    rows = []
    like = f"%{str(keyword or '').strip()}%"
    name = SOURCE_TABLES[kind][3]
    for source_kind in SOURCE_KINDS:
        cursor.execute(
            _source_query(kind, source_kind)
            + f" WHERE (i.{name} LIKE ? OR i.spec LIKE ? OR i.serial_no LIKE ?)"
              " AND (? = '' OR TRIM(COALESCE(a.project_code, '')) = ?) ORDER BY i.id DESC",
            (like, like, like, purchase_project, purchase_project),
        )
        for raw in cursor.fetchall():
            item = dict(raw)
            if not item.get("serial_no") or _available_now(cursor, source_kind, item, kind=kind) < 1:
                continue
            rows.append({
                "source_kind": source_kind, "source_id": item["id"],
                "name": item["name"], "serial_no": item["serial_no"],
                "spec": item.get("spec") or "", "unit": item.get("unit") or "台",
                "available_quantity": 1, "whole_item": True,
                "project_code": item["project_code"],
                "purchase_project": purchase_project_from_acceptance_code(item["project_code"]),
            })
    return rows


def _load_source(cursor, source_kind, source_id, kind="semifinished"):
    cursor.execute(_source_query(kind, source_kind) + " WHERE i.id = ?", (source_id,))
    row = cursor.fetchone()
    if not row:
        raise ValueError("领用来源不存在")
    return dict(row)


def pending_product_claim_quantity(cursor, item_type, source_id):
    """借用申请使用同一套设备领用占用判断。"""
    parts = item_type.split("_", 1)
    if len(parts) == 1:
        source_kind, kind = SOURCE_QUALIFIED, parts[0]
    else:
        source_kind, kind = parts
    return _pending_quantity(cursor, source_kind, source_id, kind=kind)


def _available_now(cursor, source_kind, row, exclude_form_id=None, kind="semifinished"):
    source_id = int(row["id"])
    pending = _pending_quantity(cursor, source_kind, source_id, exclude_form_id, kind)
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
    if exclude_form_id is None:
        borrow_type = kind if source_kind == SOURCE_QUALIFIED else f"{source_kind}_{kind}"
        pending += pending_product_borrow_quantity(cursor, borrow_type, source_id)
    return physical - pending


def create_semifinished_claim(cursor, user, data, kind="semifinished"):
    """提交领用并占用选中的编号，实际库存由库房出库。"""
    items = data.get("items") or []
    if not items:
        raise ValueError("领用至少需要选择一台设备")
    project_code = validate_project_code(data.get("project_code"))
    purchase_project = purchase_project_from_acceptance_code(project_code)
    begin_inventory_transaction(cursor.connection)
    prepared = []
    serials = set()
    for raw in items:
        source_kind = str(raw.get("source_kind") or "").strip()
        if source_kind not in SOURCE_KINDS:
            raise ValueError("设备来源只能是合格、不合格或报废")
        source_id = int(raw.get("source_id") or 0)
        row = _load_source(cursor, source_kind, source_id, kind)
        if str(row.get("project_code") or "").strip() != project_code:
            raise ValueError("只能领用同一项目代号的设备")
        serial = row.get("serial_no")
        if not serial or serial in serials:
            raise ValueError("请选择不同的有效设备编号")
        serials.add(serial)
        quantity = quantity_value(raw.get("quantity", 1), "领用数量", positive=True)
        if quantity != 1:
            raise ValueError("每个设备编号只能领用一台")
        available = _available_now(cursor, source_kind, row, kind=kind)
        if quantity > available + 1e-9:
            raise ValueError(f"{row.get('name') or row.get('serial_no') or '设备'} 可领数量不足")
        prepared.append((source_kind, row, quantity))

    requested_leader_id = int(data.get("leader_id") or 0)
    leader_id = resolve_department_leader(
        cursor, user, f"{kind}_claim", f"leader_{kind}_claim", requested_leader_id
    )
    form_no = next_form_no(cursor, "BC" if kind == "semifinished" else "CPCK")
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            form_no,
            f"{kind}_claim",
            workflow_generated_title(user, form_no),
            f"leader_{kind}_claim",
            f"leader_{kind}_claim",
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
    create_workflow_tasks(cursor, form_id, f"{kind}_claim", f"leader_{kind}_claim", [leader_id])
    return form_id


def decide_semifinished_claim(cursor, user, form_id, data, kind="semifinished"):
    begin_inventory_transaction(cursor.connection)
    form = dict(require_form_status(cursor, form_id, f"{kind}_claim", f"leader_{kind}_claim"))
    task = require_task_assignee(cursor, user, form_id, f"leader_{kind}_claim")
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
            f"{kind}_claim",
            f"{kind}_outbound",
            [int(data.get("warehouse_user_id") or user["id"])],
        )[0]
        cursor.execute(
            """
            UPDATE workflow_forms
            SET status = ?, current_step = ?, updated_at = ?
            WHERE id = ?
            """,
            (f"{kind}_outbound", f"{kind}_outbound", now_text(), form_id),
        )
        create_workflow_tasks(
            cursor, form_id, f"{kind}_claim", f"{kind}_outbound", [outbound_user_id]
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


def _occupy_source(cursor, source_kind, source_id, quantity, kind):
    table = dict(zip(SOURCE_KINDS, SOURCE_TABLES[kind][:3]))[source_kind]
    used = "COALESCE(used_quantity, 0)" if kind == "semifinished" else "0"
    if source_kind == SOURCE_QUALIFIED:
        cursor.execute(
            f"""
            UPDATE {table}
            SET claimed_quantity = COALESCE(claimed_quantity, 0) + ?, updated_at = ?
            WHERE id = ?
              AND COALESCE(quantity, 0) - COALESCE(borrowed_quantity, 0)
                  - MAX({used}, COALESCE(claimed_quantity, 0)) >= ?
            """,
            (quantity, now_text(), source_id, quantity),
        )
    elif source_kind == SOURCE_DEFECTIVE:
        cursor.execute(
            f"""
            UPDATE {table}
            SET claimed = 1, updated_at = ?
            WHERE id = ? AND COALESCE(claimed, 0) = 0 AND COALESCE(borrowed_quantity, 0) = 0
            """,
            (now_text(), source_id),
        )
    else:
        cursor.execute(
            f"""
            UPDATE {table}
            SET claimed_quantity = COALESCE(claimed_quantity, 0) + ?
            WHERE id = ?
              AND COALESCE(quantity, 0) - COALESCE(claimed_quantity, 0) >= ?
            """,
            (quantity, source_id, quantity),
        )
    if cursor.rowcount != 1:
        raise ValueError("设备可领数量已变化，请刷新后重试")


def outbound_semifinished_claim(cursor, user, form_id, data, kind="semifinished"):
    """流程中心出库。库存中心只随后显示已领用。"""
    begin_inventory_transaction(cursor.connection)
    require_form_status(cursor, form_id, f"{kind}_claim", f"{kind}_outbound")
    task = require_task_assignee(cursor, user, form_id, f"{kind}_outbound")
    cursor.execute("SELECT * FROM workflow_items WHERE form_id = ? ORDER BY id", (form_id,))
    items = [dict(row) for row in cursor.fetchall()]
    if not items:
        raise ValueError("领用明细不存在")
    for item in items:
        item_data = parse_json(item.get("data_json"), {})
        source_kind = str(item_data.get("source_kind") or "")
        source_id = int(item_data.get("source_id") or 0)
        quantity = float(item.get("request_quantity") or 0)
        row = _load_source(cursor, source_kind, source_id, kind)
        available = _available_now(cursor, source_kind, row, exclude_form_id=form_id, kind=kind)
        if quantity > available + 1e-9:
            raise ValueError(f"{item.get('material_name') or '设备'} 可领数量不足")
        _occupy_source(cursor, source_kind, source_id, quantity, kind)
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
