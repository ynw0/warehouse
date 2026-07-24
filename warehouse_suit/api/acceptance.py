# -*- coding: utf-8 -*-
"""Material acceptance workflow route registration."""

import json

from flask import jsonify, request

from warehouse_suit.acceptance_service import create_acceptance_workflow
from warehouse_suit.attachments import bind_material_attachments, bind_workflow_item_attachments_to_batch
from warehouse_suit.db import now_text, today_text
from warehouse_suit.inventory_service import add_inventory_batch, begin_inventory_transaction, ensure_material_from_payload
from warehouse_suit.material_repository import material_snapshot
from warehouse_suit.notifications import notify_material_inbound
from warehouse_suit.numbering import next_form_no
from warehouse_suit.settings import parse_json, workflow_settings
from warehouse_suit.transfer_settlement_service import (
    mark_auto_claim_exception,
    process_auto_claims,
)
from warehouse_suit.transfer_service import (
    mark_transfer_acceptance_failed,
    record_transfer_formal_inbound,
)
from warehouse_suit.validation import price_value, quantity_value, validation_rule_enabled
from warehouse_suit.workflow_service import (
    aggregate_acceptance_results,
    create_workflow_tasks,
    require_form_status,
    require_permission,
    require_task_assignee,
    resolve_department_leader,
    serialize_form,
    validate_validator_users,
    workflow_assignees,
    workflow_generated_title,
)


def register_acceptance_routes(app, *, get_db, current_user_provider):
    current_user = current_user_provider
    @app.post("/api/acceptance")
    def create_acceptance():
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_permission(cursor, "start_acceptance")
            created = create_acceptance_workflow(
                cursor,
                user,
                data.get("items") or [],
                validator_ids=data.get("validator_ids") or [],
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


    @app.post("/api/acceptance/<int:form_id>/inspect")
    def inspect_acceptance(form_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            task_id = int(data.get("task_id") or 0)
            task = require_task_assignee(cursor, user, form_id, "acceptance", task_id)
            form_row = require_form_status(cursor, form_id, "acceptance", "acceptance")
            current_leader_id = form_row["leader_id"]
            leader_id = int(data.get("leader_id") or 0)
            if current_leader_id:
                if leader_id and leader_id != current_leader_id:
                    raise ValueError("已有验收员指定领导审批，不能更改")
            else:
                leader_id = resolve_department_leader(cursor, user, "acceptance", "leader_acceptance", leader_id)
                cursor.execute("UPDATE workflow_forms SET leader_id = ?, updated_at = ? WHERE id = ?", (leader_id, now_text(), form_id))
                current_leader_id = leader_id
            decision = data.get("decision") or "同意"
            task_items = []
            for item in data.get("items") or []:
                task_items.append(
                    {
                        "id": int(item.get("id") or 0),
                        "qualified_quantity": float(item.get("qualified_quantity") or 0),
                        "unqualified_quantity": float(item.get("unqualified_quantity") or 0),
                        "package_ok_quantity": float(item.get("package_ok_quantity") or 0),
                        "appearance_ok_quantity": float(item.get("appearance_ok_quantity") or 0),
                        "name_spec_ok_quantity": float(item.get("name_spec_ok_quantity") or 0),
                        "usage_ok_quantity": float(item.get("usage_ok_quantity") or 0),
                        "remark": item.get("remark") or "",
                    }
                )
            roles = set(user.get("role_codes") or [])
            attachment_required_for_user = bool({"admin", "warehouse"} & roles)
            if decision == "同意" and attachment_required_for_user:
                settings = workflow_settings(cursor)
                required_types = []
                if settings.get("acceptance_material_photo_required"):
                    required_types.append(("material_photo", "物料照片"))
                if settings.get("acceptance_document_required"):
                    required_types.append(("document", "资料"))
                for item in task_items:
                    item_id = int(item.get("id") or 0)
                    if not item_id:
                        continue
                    cursor.execute("SELECT material_name FROM workflow_items WHERE id = ? AND form_id = ?", (item_id, form_id))
                    item_row = cursor.fetchone()
                    if not item_row:
                        raise ValueError("验收物料行不存在")
                    for attachment_type, label in required_types:
                        if attachment_type == "material_photo":
                            cursor.execute(
                                """
                                SELECT COUNT(*)
                                FROM material_attachments
                                WHERE workflow_form_id = ?
                                  AND workflow_item_id = ?
                                  AND attachment_type IN ('material_photo', 'photo')
                                """,
                                (form_id, item_id),
                            )
                        else:
                            cursor.execute(
                                """
                                SELECT COUNT(*)
                                FROM material_attachments
                                WHERE workflow_form_id = ?
                                  AND workflow_item_id = ?
                                  AND attachment_type NOT IN ('material_photo', 'photo')
                                """,
                                (form_id, item_id),
                            )
                        if int(cursor.fetchone()[0] or 0) <= 0:
                            raise ValueError(f"{item_row['material_name']} 请上传{label}")
            cursor.execute(
                """
                UPDATE workflow_tasks
                SET status = 'completed', decision = ?, signature = ?, signed_at = ?, data_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    decision,
                    data.get("signature") or user["display_name"],
                    data.get("signed_at") or today_text(),
                    json.dumps({"remark": data.get("remark") or "", "items": task_items, "leader_id": current_leader_id, "warehouse_user_id": int(data.get("warehouse_user_id") or 0)}, ensure_ascii=False),
                    now_text(),
                    task["id"],
                ),
            )
            aggregate_acceptance_results(cursor, form_id)
            warehouse_user_id = int(data.get("warehouse_user_id") or 0)
            if warehouse_user_id:
                warehouse_user_id = workflow_assignees(cursor, "acceptance", "inbound", [warehouse_user_id])[0]
                cursor.execute("UPDATE workflow_forms SET warehouse_user_id = ?, updated_at = ? WHERE id = ?", (warehouse_user_id, now_text(), form_id))
            cursor.execute("SELECT COUNT(*) FROM workflow_tasks WHERE form_id = ? AND step_code = 'acceptance' AND status = 'pending'", (form_id,))
            if cursor.fetchone()[0] == 0:
                create_workflow_tasks(cursor, form_id, "acceptance", "leader_acceptance", [current_leader_id])
                cursor.execute("UPDATE workflow_forms SET status = 'leader_acceptance', current_step = 'leader_acceptance', updated_at = ? WHERE id = ?", (now_text(), form_id))
            conn.commit()
            form = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "form": form})


    @app.post("/api/acceptance/<int:form_id>/leader")
    def leader_acceptance(form_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            require_form_status(cursor, form_id, "acceptance", "leader_acceptance")
            task = require_task_assignee(cursor, user, form_id, "leader_acceptance")
            decision = data.get("decision") or "同意"
            cursor.execute(
                """
                UPDATE workflow_tasks
                SET status = 'completed', decision = ?, signature = ?, signed_at = ?, data_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    decision,
                    data.get("signature") or user["display_name"],
                    data.get("signed_at") or today_text(),
                    json.dumps({"remark": data.get("remark") or ""}, ensure_ascii=False),
                    now_text(),
                    task["id"],
                ),
            )
            status = "inbound" if decision == "同意" else "rejected"
            cursor.execute("UPDATE workflow_forms SET status = ?, current_step = ?, updated_at = ? WHERE id = ?", (status, status, now_text(), form_id))
            if decision == "同意":
                cursor.execute("SELECT warehouse_user_id FROM workflow_forms WHERE id = ?", (form_id,))
                form_row = cursor.fetchone()
                warehouse_user_id = int(data.get("warehouse_user_id") or (form_row["warehouse_user_id"] if form_row else 0) or user["id"])
                create_workflow_tasks(cursor, form_id, "acceptance", "inbound", [warehouse_user_id])
            else:
                mark_transfer_acceptance_failed(
                    cursor,
                    form_id,
                    data.get("remark") or "领导审批未通过",
                    user,
                    request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                )
            conn.commit()
            form = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "form": form})


    @app.post("/api/acceptance/<int:form_id>/inbound")
    def inbound_acceptance(form_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            begin_inventory_transaction(conn)
            form = require_form_status(cursor, form_id, "acceptance", "inbound")
            task = require_task_assignee(cursor, user, form_id, "inbound")
            inbound_no = next_form_no(cursor, "RK")
            items_by_id = {int(item.get("id") or 0): item for item in data.get("items") or []}
            single_item_enabled = bool(workflow_settings(cursor).get("single_item_inbound_enabled"))
            target_item_ids = set(items_by_id) if single_item_enabled and items_by_id else set()
            cursor.execute("SELECT * FROM workflow_items WHERE form_id = ? ORDER BY id", (form_id,))
            rows = [dict(row) for row in cursor.fetchall()]
            processed_ids = set()
            for item in rows:
                if target_item_ids and item["id"] not in target_item_ids:
                    continue
                item_data = parse_json(item.get("data_json"), {})
                if item_data.get("inbound_done"):
                    continue
                patch = items_by_id.get(item["id"], {})
                approved_qty = quantity_value(patch.get("approved_quantity") if patch.get("approved_quantity") is not None else item["qualified_quantity"], "入库数量")
                if validation_rule_enabled("workflow_bounds") and approved_qty > float(item["qualified_quantity"] or 0) + 1e-9:
                    raise ValueError(f"{item['material_name']} 入库数量不能大于合格数量")
                if approved_qty <= 0:
                    continue
                location = {
                    "warehouse_type": patch.get("warehouse_type") or "office",
                    "shelf_id": patch.get("shelf_id") or data.get("shelf_id"),
                    "layer_number": patch.get("layer_number") or data.get("layer_number") or 1,
                    "zone_name": patch.get("zone_name") or data.get("zone_name") or "A",
                    "received_date": data.get("inbound_date") or today_text(),
                    "remark": f"验收单 {form['form_no']} 入库",
                }
                batch_id = add_inventory_batch(
                    cursor,
                    item["material_id"],
                    approved_qty,
                    item["unit_price"],
                    location,
                    inbound_no,
                    operation_key=f"acceptance:{form_id}:{item['id']}:inbound",
                )
                bind_workflow_item_attachments_to_batch(
                    cursor,
                    workflow_form_id=form_id,
                    workflow_item_id=item["id"],
                    material_id=item["material_id"],
                    material_batch_id=batch_id,
                )
                record_transfer_formal_inbound(
                    cursor,
                    form_id,
                    item["id"],
                    batch_id,
                    approved_qty,
                    user,
                    request.headers.get(
                        "X-Forwarded-For", request.remote_addr or ""
                    ),
                )
                cursor.execute("SELECT batch_no FROM material_batches WHERE id = ?", (batch_id,))
                batch_row = cursor.fetchone()
                inbound_batches = item_data.get("inbound_batches") or []
                inbound_batches.append({"batch_id": batch_id, "batch_no": batch_row["batch_no"] if batch_row else "", "quantity": approved_qty})
                item_data.update(
                    {
                        "inbound_done": True,
                        "inbound_no": inbound_no,
                        "inbound_batch_id": batch_id,
                        "inbound_batch_no": batch_row["batch_no"] if batch_row else "",
                        "inbound_batches": inbound_batches,
                    }
                )
                cursor.execute(
                    "UPDATE workflow_items SET approved_quantity = ?, data_json = ? WHERE id = ?",
                    (approved_qty, json.dumps(item_data, ensure_ascii=False), item["id"]),
                )
                processed_ids.add(item["id"])
                notify_material_inbound(cursor, form_id, item)
            cursor.execute("SELECT id, qualified_quantity, approved_quantity, data_json FROM workflow_items WHERE form_id = ? ORDER BY id", (form_id,))
            remaining = []
            for row in cursor.fetchall():
                row_data = parse_json(row["data_json"], {})
                required_qty = float(row["qualified_quantity"] or 0)
                done = row_data.get("inbound_done") or (required_qty > 0 and float(row["approved_quantity"] or 0) >= required_qty - 1e-9)
                if required_qty > 0 and not done:
                    remaining.append(row["id"])
            if not remaining:
                cursor.execute(
                    """
                    UPDATE workflow_tasks
                    SET status = 'completed', decision = '已入库', signature = ?, signed_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (data.get("signature") or user["display_name"], data.get("inbound_date") or today_text(), now_text(), task["id"]),
                )
                cursor.execute(
                    """
                    UPDATE workflow_forms
                    SET status = 'completed', current_step = 'completed', warehouse_user_id = ?, updated_at = ?,
                        data_json = json_set(COALESCE(NULLIF(data_json, ''), '{}'), '$.inbound_no', ?)
                    WHERE id = ?
                    """,
                    (user["id"], now_text(), inbound_no, form_id),
                )
            else:
                cursor.execute("UPDATE workflow_forms SET warehouse_user_id = ?, updated_at = ? WHERE id = ?", (user["id"], now_text(), form_id))
            conn.commit()
            settlement_error = ""
            if form["origin_type"] == "temporary_transfer":
                transfer_task_id = int(form["origin_ref_id"] or 0)
                transfer_row = cursor.execute(
                    "SELECT status FROM inventory_transfer_tasks WHERE id = ?",
                    (transfer_task_id,),
                ).fetchone()
                if transfer_row and transfer_row["status"] == "formal_inbound_complete":
                    try:
                        process_auto_claims(
                            cursor,
                            transfer_task_id,
                            user,
                            request.headers.get(
                                "X-Forwarded-For", request.remote_addr or ""
                            ),
                        )
                        conn.commit()
                    except Exception as settlement_exc:
                        conn.rollback()
                        settlement_error = str(settlement_exc)
                        try:
                            mark_auto_claim_exception(
                                cursor,
                                transfer_task_id,
                                "auto_claim_process_failed",
                                settlement_error,
                                user,
                                request.headers.get(
                                    "X-Forwarded-For", request.remote_addr or ""
                                ),
                            )
                            conn.commit()
                        except Exception:
                            conn.rollback()
            form_data = serialize_form(cursor, form_id)
            form_data["inbound_no"] = inbound_no
            form_data["partial_inbound"] = bool(remaining)
            form_data["processed_item_ids"] = sorted(processed_ids)
            if settlement_error:
                form_data["settlement_error"] = settlement_error
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "form": form_data})
