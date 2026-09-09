# -*- coding: utf-8 -*-
"""Claim workflow route registration."""

import json

from flask import jsonify, request

from warehouse_suit.claim_allocation_service import (
    allocate_claim_items,
    claim_material_rows,
)
from warehouse_suit.claim_service import create_claim_workflow, create_temporary_issue_obligations
from warehouse_suit.db import now_text, today_text
from warehouse_suit.inventory_constants import (
    BUSINESS_TYPE_CLAIM_OUTBOUND,
    validate_stock_source,
)
from warehouse_suit.inventory_service import begin_inventory_transaction, consume_inventory_fifo
from warehouse_suit.settings import parse_json, temporary_inventory_enabled, workflow_settings
from warehouse_suit.validation import quantity_value, validate_project_code, validation_rule_enabled
from warehouse_suit.workflow_service import (
    create_workflow_tasks,
    require_form_status,
    require_form_view,
    require_permission,
    require_task_assignee,
    resolve_department_leader,
    serialize_form,
    workflow_assignees,
)


def register_claim_routes(app, *, get_db, current_user_provider):
    current_user = current_user_provider

    @app.get("/api/claims/materials")
    def claim_material_options():
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "start_claim")
            rows = claim_material_rows(
                cursor,
                keyword=request.args.get("keyword", ""),
                include_temporary=temporary_inventory_enabled(cursor),
                limit=request.args.get("limit", 100),
            )
        except Exception as exc:
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify(rows)

    @app.post("/api/claims")
    def create_claim():
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_permission(cursor, "start_claim")
            items = data.get("items") or []
            if not items:
                raise ValueError("申领单至少需要一行物料")
            begin_inventory_transaction(conn)
            allocations = allocate_claim_items(
                cursor,
                items,
                user,
                include_temporary=temporary_inventory_enabled(cursor),
            )
            requested_leader_ids = [int(value) for value in data.get("leader_ids") or [] if int(value)]
            requested_leader_id = int(data.get("leader_id") or (requested_leader_ids[0] if requested_leader_ids else 0))
            leader_id = resolve_department_leader(cursor, user, "claim", "leader_claim", requested_leader_id)
            leader_ids = []
            for requested in requested_leader_ids or [leader_id]:
                resolved = resolve_department_leader(cursor, user, "claim", "leader_claim", requested)
                if resolved not in leader_ids:
                    leader_ids.append(resolved)
            purpose = str(data.get("purpose") or "研发").strip()
            rd_item_kind = str(data.get("rd_item_kind") or "").strip()
            project_material_kind = str(data.get("project_material_kind") or "").strip()
            project_code = validate_project_code(data.get("project_code"))
            if purpose not in {"办公", "研发"}:
                raise ValueError("用途必须选择办公或研发")
            if purpose == "研发":
                if rd_item_kind not in {"项目物料", "辅料"}:
                    raise ValueError("研发用途需要选择项目物料或辅料")
                if rd_item_kind == "项目物料":
                    if project_material_kind not in {"硬件", "结构"}:
                        raise ValueError("项目物料需要选择硬件或结构")
                    project_codes = workflow_settings(cursor).get("project_codes") or []
                    if project_codes and project_code not in project_codes:
                        raise ValueError("项目代号不在系统设置范围内")
            created = create_claim_workflow(
                cursor,
                user,
                allocations,
                leader_id,
                leader_ids=leader_ids or [leader_id],
                purpose=purpose,
                rd_item_kind=rd_item_kind,
                project_material_kind=project_material_kind,
                project_code=project_code,
            )
            form_id = created["form_id"]
            conn.commit()
            form = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "form": form})

    @app.post("/api/claims/<int:form_id>/leader")
    def leader_claim(form_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            form = dict(require_form_status(cursor, form_id, "claim", "leader_claim"))
            task = require_task_assignee(cursor, user, form_id, "leader_claim")
            decision = data.get("decision") or "同意"
            remark = str(data.get("remark") or "").strip()
            if decision != "同意" and not remark:
                raise ValueError("不同意时必须填写审批意见")
            cursor.execute(
                "UPDATE workflow_tasks SET status = 'completed', decision = ?, signature = ?, signed_at = ?, data_json = ?, updated_at = ? WHERE id = ?",
                (
                    decision,
                    data.get("signature") or user["display_name"],
                    data.get("signed_at") or today_text(),
                    json.dumps({"remark": remark}, ensure_ascii=False),
                    now_text(),
                    task["id"],
                ),
            )
            if decision == "同意":
                cursor.execute(
                    "SELECT COUNT(*) FROM workflow_tasks WHERE form_id = ? AND step_code = 'leader_claim' AND status = 'pending'",
                    (form_id,),
                )
                if int(cursor.fetchone()[0] or 0) == 0:
                    outbound_user_id = workflow_assignees(
                        cursor,
                        "claim",
                        "outbound",
                        [int(data.get("warehouse_user_id") or user["id"])],
                    )[0]
                    cursor.execute(
                        "UPDATE workflow_forms SET status = 'outbound', current_step = 'outbound', updated_at = ? WHERE id = ?",
                        (now_text(), form_id),
                    )
                    create_workflow_tasks(cursor, form_id, "claim", "outbound", [outbound_user_id])
            else:
                cursor.execute(
                    "UPDATE workflow_forms SET status = 'applicant_revision', current_step = 'applicant_revision', updated_at = ? WHERE id = ?",
                    (now_text(), form_id),
                )
                cursor.execute(
                    "INSERT INTO workflow_tasks (form_id, step_code, assignee_id, data_json, created_at, updated_at) VALUES (?, 'applicant_revision', ?, ?, ?, ?)",
                    (
                        form_id,
                        form["applicant_id"],
                        json.dumps({"remark": remark, "return_reason": remark}, ensure_ascii=False),
                        now_text(),
                        now_text(),
                    ),
                )
            conn.commit()
            form = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "form": form})

    @app.post("/api/claims/<int:form_id>/outbound")
    def outbound_claim(form_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            begin_inventory_transaction(conn)
            operation_key = str(data.get("operation_key") or "").strip()
            if not operation_key:
                raise ValueError("出库操作标识不能为空")
            if len(operation_key) > 100:
                raise ValueError("出库操作标识过长")
            cursor.execute(
                "SELECT form_id FROM claim_outbound_operations WHERE operation_key = ?",
                (operation_key,),
            )
            existing_operation = cursor.fetchone()
            if existing_operation:
                if int(existing_operation["form_id"]) != form_id:
                    raise ValueError("出库操作标识已被其他单据使用")
                require_form_view(cursor, form_id)
                form_data = serialize_form(cursor, form_id)
                conn.commit()
                conn.close()
                return jsonify(
                    {
                        "success": True,
                        "form": form_data,
                        "idempotent": True,
                        "partial_outbound": form_data.get("status") == "outbound",
                    }
                )

            form = dict(require_form_status(cursor, form_id, "claim", "outbound"))
            task = dict(require_task_assignee(cursor, user, form_id, "outbound"))
            submitted_items = data.get("items") or []
            if not submitted_items:
                raise ValueError("请填写本次出库物料")
            submitted_ids = [int(item.get("id") or 0) for item in submitted_items]
            if any(not item_id for item_id in submitted_ids) or len(set(submitted_ids)) != len(submitted_ids):
                raise ValueError("出库物料明细无效或重复")
            items_by_id = {item_id: item for item_id, item in zip(submitted_ids, submitted_items)}
            cursor.execute("SELECT * FROM workflow_items WHERE form_id = ? ORDER BY id", (form_id,))
            workflow_items = [dict(row) for row in cursor.fetchall()]
            known_ids = {int(item["id"]) for item in workflow_items}
            unknown_ids = set(submitted_ids) - known_ids
            if unknown_ids:
                raise ValueError("出库物料不属于当前申领单")

            processed = []
            for item in workflow_items:
                patch = items_by_id.get(item["id"])
                if patch is None:
                    continue
                item_data = parse_json(item.get("data_json"), {})
                qty = quantity_value(
                    patch.get("outbound_quantity"),
                    "出库数量",
                    positive=False,
                )
                if qty <= 1e-9:
                    continue
                previous_quantity = float(item.get("outbound_quantity") or 0)
                remaining_quantity = max(0.0, float(item["request_quantity"] or 0) - previous_quantity)
                if validation_rule_enabled("workflow_bounds") and qty > remaining_quantity + 1e-9:
                    raise ValueError(f"{item['material_name']} 本次出库数量不能大于待出数量")
                stock_source = validate_stock_source(item.get("stock_source"))
                consumed = consume_inventory_fifo(
                    cursor,
                    item["material_id"],
                    qty,
                    form["form_no"],
                    data.get("outbound_date") or today_text(),
                    f"申领单 {form['form_no']} 出库",
                    patch.get("batches") or patch.get("batch_allocations"),
                    stock_source=stock_source,
                    business_type=BUSINESS_TYPE_CLAIM_OUTBOUND,
                    operation_key=f"claim:{form_id}:{item['id']}:outbound:{operation_key}",
                    operator_id=user["id"],
                    workflow_item_id=item["id"],
                )
                create_temporary_issue_obligations(cursor, form, item, consumed)
                consumed_history = item_data.get("consumed_batches")
                if not isinstance(consumed_history, list):
                    consumed_history = []
                cumulative_quantity = previous_quantity + qty
                item_data.update(
                    {
                        "consumed_batches": [*consumed_history, *consumed],
                        "outbound_stock_source": stock_source,
                    }
                )
                cursor.execute(
                    "UPDATE workflow_items SET outbound_quantity = ?, data_json = ? WHERE id = ?",
                    (cumulative_quantity, json.dumps(item_data, ensure_ascii=False), item["id"]),
                )
                processed.append(
                    {
                        "item_id": item["id"],
                        "quantity": qty,
                        "cumulative_quantity": cumulative_quantity,
                    }
                )
            if not processed:
                raise ValueError("本次出库数量必须大于 0")

            task_data = parse_json(task.get("data_json"), {})
            operation_history = task_data.get("outbound_operations")
            if not isinstance(operation_history, list):
                operation_history = []
            signed_at = data.get("outbound_date") or today_text()
            signature = data.get("signature") or user["display_name"]
            operation_summary = {
                "operation_key": operation_key,
                "operator_id": user["id"],
                "signature": signature,
                "signed_at": signed_at,
                "items": processed,
            }
            task_data["outbound_operations"] = [*operation_history, operation_summary]
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM workflow_items
                WHERE form_id = ? AND request_quantity - outbound_quantity > 0.000000001
                """,
                (form_id,),
            )
            partial_outbound = int(cursor.fetchone()[0] or 0) > 0
            if partial_outbound:
                cursor.execute(
                    """
                    UPDATE workflow_tasks
                    SET decision = '已部分出库', data_json = ?, updated_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (json.dumps(task_data, ensure_ascii=False), now_text(), task["id"]),
                )
                cursor.execute(
                    "UPDATE workflow_forms SET warehouse_user_id = ?, updated_at = ? WHERE id = ?",
                    (user["id"], now_text(), form_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE workflow_tasks
                    SET status = 'completed', decision = '已出库', signature = ?,
                        signed_at = ?, data_json = ?, updated_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (signature, signed_at, json.dumps(task_data, ensure_ascii=False), now_text(), task["id"]),
                )
                cursor.execute(
                    """
                    UPDATE workflow_forms
                    SET status = 'completed', current_step = 'completed',
                        warehouse_user_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (user["id"], now_text(), form_id),
                )
            cursor.execute(
                """
                INSERT INTO claim_outbound_operations
                    (form_id, task_id, operator_id, operation_key, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    form_id,
                    task["id"],
                    user["id"],
                    operation_key,
                    json.dumps(operation_summary, ensure_ascii=False),
                    now_text(),
                ),
            )
            conn.commit()
            form_data = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify(
            {
                "success": True,
                "form": form_data,
                "idempotent": False,
                "partial_outbound": partial_outbound,
            }
        )
