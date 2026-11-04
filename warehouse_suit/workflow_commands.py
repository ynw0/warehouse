# -*- coding: utf-8 -*-
"""流程新建、修改、删除。页面和 AI 助手调用同一套业务函数。"""

import json

from warehouse_suit.acceptance_service import execute_create_acceptance
from warehouse_suit.borrow_service import (
    allocate_borrow_items,
    borrow_has_actual_outbound,
    borrow_revision_requested_items,
    execute_create_borrow,
    insert_borrow_allocations,
)
from warehouse_suit.claim_allocation_service import (
    allocate_claim_items,
    claim_revision_requested_items,
    insert_claim_allocations,
)
from warehouse_suit.claim_service import claim_has_actual_outbound, execute_create_claim
from warehouse_suit.db import now_text, row_to_dict
from warehouse_suit.extended_service import create_common_material_application, create_supply_order
from warehouse_suit.inventory_service import begin_inventory_transaction
from warehouse_suit.issued_material_service import update_claim_outbound_quantities
from warehouse_suit.material_repository import material_stock_total
from warehouse_suit.material_utils import stock_snapshot_payload
from warehouse_suit.production_service import (
    bind_production_material_hold,
    execute_create_production,
    release_production_material_hold,
)
from warehouse_suit.purchase_project import validate_purchase_project
from warehouse_suit.purchase_request_service import create_purchase_request
from warehouse_suit.recycle import recycle_workflow
from warehouse_suit.semifinished_claim_service import create_semifinished_claim
from warehouse_suit.settings import parse_json, temporary_inventory_enabled
from warehouse_suit.special_claim_service import create_special_claim
from warehouse_suit.temporary_inventory_visibility import temporary_workflow_sql
from warehouse_suit.validation import price_value, quantity_value, validation_rule_enabled
from warehouse_suit.workflow_service import (
    _current_user,
    admin_workflow_item_fields,
    aggregate_acceptance_results,
    is_production_form_type,
    require_permission,
    require_workflow_edit_or_applicant,
    serialize_form,
    user_has_permission,
    workflow_applicant_can_modify,
    workflow_edit_permission,
    workflow_view_scope,
)


# 新建时沿用各流程原有的发起权限。
_CREATE_PERMISSIONS = {
    "acceptance": "start_acceptance",
    "semifinished": "start_acceptance",
    "finished": "start_acceptance",
    "claim": "start_claim",
    "semifinished_claim": "start_claim",
    "special_claim": "start_special_claim",
    "borrow": "start_borrow",
    "purchase_request": "start_purchase_request",
    "supply": "start_supply",
    "common_material": "start_common_material",
}


def create_workflow_record(conn, data, optional_active_user_id):
    """按 form_type 新建流程，权限与原来的发起接口相同。"""
    cursor = conn.cursor()
    payload = data or {}
    form_type = str(payload.get("form_type") or "").strip()
    permission = _CREATE_PERMISSIONS.get(form_type)
    if not permission:
        names = "、".join(_CREATE_PERMISSIONS)
        raise ValueError(f"不支持新建该流程，可用类型：{names}")
    user = require_permission(cursor, permission)
    if form_type == "acceptance":
        form = serialize_form(cursor, execute_create_acceptance(cursor, user, payload))
    elif form_type in {"semifinished", "finished"}:
        form = serialize_form(
            cursor,
            execute_create_production(cursor, user, form_type, payload, optional_active_user_id),
        )
    elif form_type == "claim":
        form = serialize_form(cursor, execute_create_claim(cursor, user, payload))
    elif form_type == "semifinished_claim":
        form = serialize_form(cursor, create_semifinished_claim(cursor, user, payload))
    elif form_type == "special_claim":
        form = serialize_form(cursor, create_special_claim(cursor, user, payload))
    elif form_type == "borrow":
        form = serialize_form(cursor, execute_create_borrow(cursor, user, payload))
    elif form_type == "purchase_request":
        form = serialize_form(cursor, create_purchase_request(cursor, user, payload))
    elif form_type == "supply":
        form = create_supply_order(cursor, user, payload, payload.get("leader_id"))
    elif form_type == "common_material":
        form = create_common_material_application(
            cursor,
            user,
            payload.get("material_id"),
            payload.get("warning_quantity"),
            payload.get("reason"),
            payload.get("leader_id"),
        )
    else:
        names = "、".join(_CREATE_PERMISSIONS)
        raise ValueError(f"不支持新建该流程，可用类型：{names}")
    conn.commit()
    return form


def update_workflow_record(conn, form_id, data):
    """修改流程。完整修改权或发起人可改范围与页面接口一致。"""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM workflow_forms WHERE id = ?", (form_id,))
    form = row_to_dict(cursor.fetchone())
    if not form:
        raise ValueError("流程不存在")
    user = _current_user(cursor)
    full_edit_allowed = user_has_permission(cursor, user, workflow_edit_permission(form))
    applicant_edit_allowed = workflow_applicant_can_modify(cursor, form["id"], user)
    if not full_edit_allowed and not applicant_edit_allowed:
        raise PermissionError("当前账号没有该流程的修改权限，或下一步办理人已处理")
    applicant_revision_edit = applicant_edit_allowed and (
        form.get("current_step") == "applicant_revision"
        or form.get("status") == "applicant_revision"
    )
    applicant_limited_edit = applicant_edit_allowed and (not full_edit_allowed or applicant_revision_edit)
    if applicant_limited_edit and form.get("form_type") in {"claim", "borrow"}:
        begin_inventory_transaction(conn)
    if applicant_limited_edit:
        forbidden_keys = ["status", "current_step", "leader_id", "warehouse_user_id", "tasks", "delete_item_ids"]
        if any(key in data for key in forbidden_keys):
            raise PermissionError("重新提交只能修改发起表单内容，不能修改流程状态、当前环节、办理记录或删除明细")
    form_fields = []
    form_params = []
    form_edit_keys = ["title"] if applicant_limited_edit else ["title", "status", "current_step"]
    for key in form_edit_keys:
        if key in data:
            form_fields.append(f"{key} = ?")
            form_params.append(str(data.get(key) or "").strip())
    if not applicant_limited_edit:
        for key in ["leader_id", "warehouse_user_id"]:
            if key in data:
                form_fields.append(f"{key} = ?")
                form_params.append(int(data.get(key) or 0) or None)
    if form_fields:
        form_fields.append("updated_at = ?")
        form_params.append(now_text())
        form_params.append(form_id)
        cursor.execute(f"UPDATE workflow_forms SET {', '.join(form_fields)} WHERE id = ?", form_params)

    if is_production_form_type(form.get("form_type")) and "maker_id" in data:
        if "admin" not in user.get("role_codes", []):
            raise PermissionError("只有系统管理员可以修改制作人")
        maker_id = int(data.get("maker_id") or 0)
        if not maker_id:
            raise ValueError("请选择制作人")
        cursor.execute(
            "SELECT display_name, username FROM users WHERE id = ? AND is_active = 1",
            (maker_id,),
        )
        maker = cursor.fetchone()
        if not maker:
            raise ValueError("制作人不存在或已停用")
        form_data = parse_json(form.get("data_json"), {})
        form_data["maker_id"] = maker_id
        form_data["maker"] = maker["display_name"] or maker["username"] or ""
        cursor.execute(
            "UPDATE workflow_forms SET data_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(form_data, ensure_ascii=False), now_text(), form_id),
        )

    if applicant_limited_edit:
        if form.get("form_type") in {"claim", "borrow", "borrow_return"}:
            item_fields = ["request_quantity"]
        elif is_production_form_type(form.get("form_type")):
            item_fields = ["material_name", "spec", "unit", "request_quantity", "arrival_quantity"]
        else:
            item_fields = [
                "material_code",
                "material_name",
                "brand_model",
                "spec",
                "purchase_applicant",
                "purchase_project",
                "unit",
                "request_quantity",
                "arrival_quantity",
                "unit_price",
            ]
    else:
        item_fields = admin_workflow_item_fields(form.get("form_type"))
    numeric_fields = {
        "request_quantity",
        "arrival_quantity",
        "unit_price",
        "qualified_quantity",
        "unqualified_quantity",
        "approved_quantity",
        "outbound_quantity",
    }
    workflow_items_replaced = False
    if applicant_limited_edit and form.get("form_type") == "claim" and "items" in data:
        requested_items = claim_revision_requested_items(cursor, form_id, data.get("items") or [])
        allocations = allocate_claim_items(
            cursor,
            requested_items,
            user,
            include_temporary=temporary_inventory_enabled(cursor),
            exclude_form_id=form_id,
        )
        cursor.execute("DELETE FROM workflow_items WHERE form_id = ?", (form_id,))
        insert_claim_allocations(cursor, form_id, allocations)
        workflow_items_replaced = True
    elif applicant_limited_edit and form.get("form_type") == "borrow" and "items" in data:
        requested_items = borrow_revision_requested_items(cursor, form_id, data.get("items") or [])
        allocations = allocate_borrow_items(
            cursor,
            requested_items,
            user,
            include_temporary=temporary_inventory_enabled(cursor),
            exclude_form_id=form_id,
        )
        cursor.execute("DELETE FROM workflow_items WHERE form_id = ?", (form_id,))
        insert_borrow_allocations(cursor, form_id, allocations)
        workflow_items_replaced = True

    for item in ([] if workflow_items_replaced else (data.get("items") or [])):
        item_id = int(item.get("id") or 0)
        if not item_id:
            continue
        if form.get("form_type") in {"claim", "special_claim"} and isinstance(item.get("outbound_lines"), list):
            update_claim_outbound_quantities(cursor, form, item_id, item.get("outbound_lines"))
        fields = []
        params = []
        for key in item_fields:
            if key in item:
                fields.append(f"{key} = ?")
                if key in numeric_fields:
                    params.append(price_value(item.get(key), "单价") if key == "unit_price" else quantity_value(item.get(key), "数量"))
                elif key == "purchase_project":
                    params.append(validate_purchase_project(cursor, item.get(key)))
                else:
                    params.append(str(item.get(key) or "").strip())
        if fields:
            if applicant_limited_edit and form.get("form_type") == "claim" and "request_quantity" in item:
                cursor.execute("SELECT material_id, material_name FROM workflow_items WHERE id = ? AND form_id = ?", (item_id, form_id))
                row = cursor.fetchone()
                qty = quantity_value(item.get("request_quantity"), "申领数量", positive=True)
                if validation_rule_enabled("workflow_bounds") and row and qty > material_stock_total(cursor, row["material_id"]) + 1e-9:
                    raise ValueError(f"{row['material_name']} 申领数量不能大于库存数量")
                data_index = fields.index("request_quantity = ?")
                params[data_index] = qty
                cursor.execute("SELECT data_json FROM workflow_items WHERE id = ? AND form_id = ?", (item_id, form_id))
                data_row = cursor.fetchone()
                item_data = parse_json(data_row["data_json"], {}) if data_row else {}
                item_data.update(stock_snapshot_payload(material_stock_total(cursor, row["material_id"]) if row else 0, source="form_resubmit"))
                fields.append("data_json = ?")
                params.append(json.dumps(item_data, ensure_ascii=False))
            params.extend([item_id, form_id])
            cursor.execute(f"UPDATE workflow_items SET {', '.join(fields)} WHERE id = ? AND form_id = ?", params)

    if not applicant_limited_edit:
        delete_item_ids = [int(x) for x in (data.get("delete_item_ids") or []) if x]
        for item_id in delete_item_ids:
            cursor.execute("DELETE FROM workflow_items WHERE id = ? AND form_id = ?", (item_id, form_id))

        for task in data.get("tasks") or []:
            task_id = int(task.get("id") or 0)
            if not task_id:
                continue
            cursor.execute("SELECT data_json FROM workflow_tasks WHERE id = ? AND form_id = ?", (task_id, form_id))
            task_row = cursor.fetchone()
            if not task_row:
                continue
            task_data = parse_json(task_row["data_json"], {})
            if "remark" in task:
                task_data["remark"] = str(task.get("remark") or "")
            fields = []
            params = []
            for key in ["decision", "signature", "signed_at", "status", "assignee_id"]:
                if key in task:
                    fields.append(f"{key} = ?")
                    params.append(int(task.get(key) or 0) if key == "assignee_id" else str(task.get(key) or ""))
            fields.append("data_json = ?")
            params.append(json.dumps(task_data, ensure_ascii=False))
            fields.append("updated_at = ?")
            params.append(now_text())
            params.extend([task_id, form_id])
            cursor.execute(f"UPDATE workflow_tasks SET {', '.join(fields)} WHERE id = ? AND form_id = ?", params)

    aggregate_acceptance_results(cursor, form_id)
    if is_production_form_type(form.get("form_type")):
        bind_production_material_hold(cursor, form_id)
    conn.commit()
    return serialize_form(cursor, form_id)


def delete_workflow_record(conn, form_id):
    """删除流程。已出库、已借出和借用转领用单据保持原有限制。"""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM workflow_forms WHERE id = ?", (form_id,))
    form = row_to_dict(cursor.fetchone())
    if not form:
        raise ValueError("流程不存在")
    user = _current_user(cursor)
    require_workflow_edit_or_applicant(cursor, form, user)
    if form.get("form_type") == "claim" and claim_has_actual_outbound(cursor, form_id):
        raise ValueError("已实际出库的领用流程不能删除，请使用后续冲销流程")
    if form.get("form_type") == "borrow" and borrow_has_actual_outbound(cursor, form_id):
        raise ValueError("已实际借出的借用流程不能删除，请使用后续冲销流程")
    if form.get("form_type") == "borrow_claim":
        raise ValueError("借用转领用单据不能删除")
    if is_production_form_type(form.get("form_type")):
        release_production_material_hold(cursor, form_id)
    recycle_workflow(cursor, form_id, user.get("id"))
    cursor.execute("DELETE FROM workflow_tasks WHERE form_id = ?", (form_id,))
    cursor.execute("DELETE FROM workflow_items WHERE form_id = ?", (form_id,))
    cursor.execute("DELETE FROM workflow_forms WHERE id = ?", (form_id,))
    conn.commit()


def list_workflow_briefs(cursor, user, keyword="", form_type="", limit=20):
    """按当前用户可见范围列出流程摘要，供修改或删除前定位单据。"""
    scope_sql, scope_params = workflow_view_scope(cursor, user)
    where = [scope_sql]
    params = list(scope_params)
    if not temporary_inventory_enabled(cursor):
        temporary_sql, temporary_params = temporary_workflow_sql("f")
        where.append(f"NOT {temporary_sql}")
        params.extend(temporary_params)
    text = str(keyword or "").strip()
    if text:
        where.append("(f.form_no LIKE ? OR IFNULL(f.title, '') LIKE ?)")
        like = f"%{text}%"
        params.extend([like, like])
    kind = str(form_type or "").strip()
    if kind:
        where.append("f.form_type = ?")
        params.append(kind)
    params.append(int(limit))
    cursor.execute(
        f"""
        SELECT f.id, f.form_no, f.form_type, f.title, f.status, f.current_step
        FROM workflow_forms f
        WHERE {' AND '.join(where)}
        ORDER BY f.id DESC
        LIMIT ?
        """,
        params,
    )
    return [dict(row) for row in cursor.fetchall()]
