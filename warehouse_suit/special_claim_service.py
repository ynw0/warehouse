"""特殊用料：一行可跨项目、跨用料形式，负责人并行同意后再由部门领导审批、库房出库。"""

from __future__ import annotations

import json

from warehouse_suit.db import now_text, today_text
from warehouse_suit.claim_allocation_service import claim_material_rows
from warehouse_suit.inventory_constants import BUSINESS_TYPE_CLAIM_OUTBOUND, STOCK_SOURCE_FORMAL
from warehouse_suit.inventory_service import consume_inventory_fifo
from warehouse_suit.material_repository import material_stock_total
from warehouse_suit.stock_allocation_service import (
    held_source_quantities,
    occupancy_text,
    pending_outbound_hold_index,
)
from warehouse_suit.numbering import next_form_no
from warehouse_suit.project_service import project_owner_id
from warehouse_suit.purchase_project import OFFICE_PURCHASE_PROJECT, validate_purchase_project
from warehouse_suit.usage_kind import validate_usage_kind
from warehouse_suit.validation import quantity_value
from warehouse_suit.workflow_service import (
    create_workflow_tasks,
    resolve_department_leader,
    workflow_generated_title,
)


def _json(value):
    return json.dumps(value or {}, ensure_ascii=False)


def formal_special_materials(cursor, keyword="", usage_kind="研发用料", purchase_project=""):
    """特殊用料搜索只返回所选项目、用料形式下正式库还有数量的物料。"""
    return claim_material_rows(
        cursor,
        keyword=keyword,
        include_temporary=False,
        usage_kind=usage_kind,
        purchase_project=purchase_project,
        formal_only=True,
    )


def _require_formal_quantity(cursor, lines):
    """同一物料、项目、用料形式的申请数加总，超过正式库可用就不能提交。"""
    grouped = {}
    for material, purchase_project, usage_kind, request_quantity in lines:
        key = (int(material["id"]), purchase_project, usage_kind)
        bucket = grouped.setdefault(key, {"name": material["name"] or material["material_code"], "quantity": 0.0})
        bucket["quantity"] += float(request_quantity)
    holds = pending_outbound_hold_index(cursor)
    for (material_id, purchase_project, usage_kind), bucket in grouped.items():
        physical = material_stock_total(
            cursor,
            material_id,
            stock_source=STOCK_SOURCE_FORMAL,
            purchase_project=purchase_project,
            usage_kind=usage_kind,
        )
        held_formal, _held_temporary = held_source_quantities(
            holds, material_id, purchase_project, usage_kind
        )
        available = max(0.0, float(physical or 0) - held_formal)
        if bucket["quantity"] > available + 1e-9:
            raise ValueError(
                f"{bucket['name']} 正式库可用数量不足"
                f"{occupancy_text(cursor, material_id, purchase_project, usage_kind)}"
            )


def create_special_claim(cursor, user, payload):
    reason = str(payload.get("reason") or payload.get("situation") or "").strip()
    if not reason:
        raise ValueError("请填写情况说明")
    raw_items = payload.get("items") or []
    if not raw_items:
        raise ValueError("特殊用料至少需要一行物料")
    lines = []
    owner_ids = []
    for raw in raw_items:
        material_id = int(raw.get("material_id") or 0)
        if material_id <= 0:
            raise ValueError("请选择物料")
        purchase_project = validate_purchase_project(cursor, raw.get("purchase_project"))
        usage_kind = validate_usage_kind(raw.get("usage_kind"))
        request_quantity = quantity_value(raw.get("request_quantity"), "申领数量", positive=True)
        owner_id = project_owner_id(cursor, purchase_project)
        if not owner_id:
            label = "办公" if purchase_project == OFFICE_PURCHASE_PROJECT else purchase_project
            raise ValueError(f"{label}未设置负责人，不能提交")
        if owner_id not in owner_ids:
            owner_ids.append(owner_id)
        cursor.execute("SELECT * FROM materials WHERE id = ?", (material_id,))
        material = cursor.fetchone()
        if not material:
            raise ValueError("物料不存在")
        lines.append((material, purchase_project, usage_kind, request_quantity))
    _require_formal_quantity(cursor, lines)
    leader_id = resolve_department_leader(
        cursor, user, "special_claim", "leader_special_claim", payload.get("leader_id")
    )
    if int(leader_id) == int(user["id"]):
        raise ValueError("部门领导不能是申请人")
    form_no = next_form_no(cursor, "TS")
    timestamp = now_text()
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, leader_id, data_json, created_at, updated_at)
        VALUES (?, 'special_claim', ?, 'project_owner', 'project_owner', ?, ?, ?, ?, ?)
        """,
        (
            form_no,
            str(payload.get("title") or "").strip() or workflow_generated_title(user, form_no),
            int(user["id"]),
            int(leader_id),
            _json({"reason": reason, "leader_id": int(leader_id)}),
            timestamp,
            timestamp,
        ),
    )
    form_id = int(cursor.lastrowid)
    for material, purchase_project, usage_kind, request_quantity in lines:
        cursor.execute(
            """
            INSERT INTO workflow_items
                (form_id, material_id, material_code, material_name, brand_model, spec, unit,
                 request_quantity, purchase_project, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                form_id,
                int(material["id"]),
                material["material_code"],
                material["name"],
                material["brand_model"],
                material["spec"],
                material["unit"],
                request_quantity,
                purchase_project,
                _json({"usage_kind": usage_kind, "purchase_project": purchase_project, "owner_user_id": project_owner_id(cursor, purchase_project)}),
            ),
        )
    for owner_id in owner_ids:
        cursor.execute(
            "INSERT INTO workflow_tasks (form_id, step_code, assignee_id, created_at, updated_at) VALUES (?, 'project_owner', ?, ?, ?)",
            (form_id, owner_id, timestamp, timestamp),
        )
    return form_id


def decide_special_claim(cursor, user, form_id, approved, remark=""):
    cursor.execute("SELECT * FROM workflow_forms WHERE id = ? AND form_type = 'special_claim'", (int(form_id),))
    form = cursor.fetchone()
    if not form:
        raise ValueError("特殊用料单不存在")
    form = dict(form)
    cursor.execute(
        """
        SELECT * FROM workflow_tasks
        WHERE form_id = ? AND assignee_id = ? AND status = 'pending' AND step_code = ?
        ORDER BY id LIMIT 1
        """,
        (int(form_id), int(user["id"]), form["current_step"]),
    )
    task = cursor.fetchone()
    if not task:
        raise ValueError("当前没有需要你办理的步骤")
    decision = "同意" if approved else "退回"
    cursor.execute(
        "UPDATE workflow_tasks SET status = 'completed', decision = ?, signature = ?, signed_at = ?, updated_at = ? WHERE id = ?",
        (decision, user.get("display_name") or "", today_text(), now_text(), task["id"]),
    )
    if not approved:
        cursor.execute(
            "UPDATE workflow_forms SET status = 'returned', current_step = 'returned', updated_at = ? WHERE id = ?",
            (now_text(), int(form_id)),
        )
        cursor.execute(
            "UPDATE workflow_tasks SET status = 'cancelled', updated_at = ? WHERE form_id = ? AND status = 'pending'",
            (now_text(), int(form_id)),
        )
        return
    if form["current_step"] == "project_owner":
        cursor.execute(
            "SELECT COUNT(*) FROM workflow_tasks WHERE form_id = ? AND step_code = 'project_owner' AND status = 'pending'",
            (int(form_id),),
        )
        if int(cursor.fetchone()[0] or 0) == 0:
            data = json.loads(form.get("data_json") or "{}")
            create_workflow_tasks(cursor, int(form_id), "special_claim", "leader_special_claim", [int(data.get("leader_id") or form["leader_id"])])
            cursor.execute(
                "UPDATE workflow_forms SET status = 'leader_special_claim', current_step = 'leader_special_claim', updated_at = ? WHERE id = ?",
                (now_text(), int(form_id)),
            )
        return
    if form["current_step"] == "leader_special_claim":
        cursor.execute(
            """
            SELECT DISTINCT u.id FROM users u
            JOIN user_roles ur ON ur.user_id = u.id
            JOIN roles r ON r.id = ur.role_id
            WHERE u.is_active = 1 AND r.code = 'warehouse'
            """
        )
        warehouse_ids = [int(row["id"]) for row in cursor.fetchall()]
        if not warehouse_ids:
            raise ValueError("未配置仓库管理员")
        create_workflow_tasks(cursor, int(form_id), "special_claim", "special_outbound", warehouse_ids)
        cursor.execute(
            "UPDATE workflow_forms SET status = 'special_outbound', current_step = 'special_outbound', updated_at = ? WHERE id = ?",
            (now_text(), int(form_id)),
        )


def outbound_special_claim(cursor, user, form_id, payload):
    cursor.execute("SELECT * FROM workflow_forms WHERE id = ? AND form_type = 'special_claim'", (int(form_id),))
    form = cursor.fetchone()
    if not form or form["status"] != "special_outbound":
        raise ValueError("特殊用料单不在库房出库")
    cursor.execute(
        """
        SELECT * FROM workflow_tasks
        WHERE form_id = ? AND assignee_id = ? AND step_code = 'special_outbound' AND status = 'pending'
        LIMIT 1
        """,
        (int(form_id), int(user["id"])),
    )
    task = cursor.fetchone()
    if not task:
        raise ValueError("当前没有需要你办理的出库")
    patches = {int(item.get("id")): item for item in payload.get("items") or []}
    cursor.execute("SELECT * FROM workflow_items WHERE form_id = ? ORDER BY id", (int(form_id),))
    items = [dict(row) for row in cursor.fetchall()]
    for item in items:
        patch = patches.get(int(item["id"]), {})
        data = json.loads(item.get("data_json") or "{}")
        quantity = quantity_value(
            patch.get("outbound_quantity") if patch.get("outbound_quantity") is not None else item["request_quantity"],
            "出库数量",
            positive=False,
        )
        if quantity > float(item["request_quantity"] or 0) + 1e-9:
            raise ValueError("出库数量不能大于申请数量")
        if patch.get("purchase_project") or patch.get("usage_kind"):
            raise ValueError("出库不能改项目或用料形式")
        if quantity <= 1e-9:
            continue
        consume_inventory_fifo(
            cursor,
            int(item["material_id"]),
            quantity,
            form["form_no"],
            payload.get("outbound_date") or today_text(),
            f"特殊用料 {form['form_no']} 出库",
            None,
            stock_source=STOCK_SOURCE_FORMAL,
            business_type=BUSINESS_TYPE_CLAIM_OUTBOUND,
            operation_key=f"special:{form_id}:{item['id']}",
            operator_id=user["id"],
            workflow_item_id=int(item["id"]),
            purchase_project=str(item.get("purchase_project") or "").strip(),
            usage_kind=validate_usage_kind(data.get("usage_kind")),
        )
        cursor.execute(
            "UPDATE workflow_items SET outbound_quantity = ? WHERE id = ?",
            (quantity, int(item["id"])),
        )
    cursor.execute(
        "UPDATE workflow_tasks SET status = 'completed', decision = '已出库', signature = ?, signed_at = ?, updated_at = ? WHERE id = ?",
        (user.get("display_name") or "", today_text(), now_text(), task["id"]),
    )
    cursor.execute(
        "UPDATE workflow_tasks SET status = 'cancelled', updated_at = ? WHERE form_id = ? AND step_code = 'special_outbound' AND status = 'pending'",
        (now_text(), int(form_id)),
    )
    cursor.execute(
        "UPDATE workflow_forms SET status = 'completed', current_step = 'completed', updated_at = ? WHERE id = ?",
        (now_text(), int(form_id)),
    )
