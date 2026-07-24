# -*- coding: utf-8 -*-
"""Generic workflow route registration."""

import json
from datetime import datetime, timedelta

from flask import jsonify, request

from warehouse_suit.dashboard_service import build_dashboard_overview, empty_dashboard_overview
from warehouse_suit.extended_service import evaluate_common_material_alerts, evaluate_supply_due_alerts

from warehouse_suit.borrow_service import (
    allocate_borrow_items,
    borrow_has_actual_outbound,
    borrow_revision_requested_items,
    current_borrow_requested_items,
    insert_borrow_allocations,
)
from warehouse_suit.claim_allocation_service import (
    allocate_claim_items,
    claim_revision_requested_items,
    current_claim_requested_items,
    insert_claim_allocations,
)
from warehouse_suit.claim_service import claim_has_actual_outbound
from warehouse_suit.db import now_text, row_to_dict, today_text
from warehouse_suit.duplicate_detection import (
    duplicate_acceptance_match_rows,
    duplicate_check_days,
    duplicate_float,
    duplicate_material_values,
    duplicate_norm,
)
from warehouse_suit.inventory_constants import AUTO_CLAIM_ORIGIN_TYPE, STOCK_SOURCE_FORMAL
from warehouse_suit.inventory_service import begin_inventory_transaction
from warehouse_suit.material_repository import material_stock_total
from warehouse_suit.material_utils import stock_snapshot_payload
from warehouse_suit.numbering import next_form_no
from warehouse_suit.transfer_service import mark_transfer_acceptance_failed
from warehouse_suit.permissions import role_permissions
from warehouse_suit.production_service import production_components_from_payload, production_item_payload
from warehouse_suit.recycle import recycle_workflow
from warehouse_suit.settings import parse_json, temporary_inventory_enabled
from warehouse_suit.temporary_inventory_visibility import append_workflow_visibility
from warehouse_suit.validation import price_value, quantity_value, validation_rule_enabled
from warehouse_suit.workflow_service import (
    aggregate_acceptance_results,
    create_workflow_tasks,
    is_production_form_type,
    require_any_permission,
    require_form_view,
    require_permission,
    require_task_assignee,
    require_workflow_edit_or_applicant,
    serialize_form,
    user_has_permission,
    workflow_applicant_can_modify,
    workflow_edit_permission,
    workflow_list_rows,
    workflow_return_assignees,
    workflow_step_codes,
)


def register_workflow_routes(app, *, get_db, current_user_provider, notify_todos_changed):
    current_user = current_user_provider

    @app.get("/api/dashboard/overview")
    def dashboard_overview():
        """Serve the read-only data-screen payload for the signed-in user."""
        conn = get_db()
        try:
            cursor = conn.cursor()
            user = current_user(cursor)
            if not user:
                raise PermissionError('请先登录')
            evaluate_common_material_alerts(cursor)
            evaluate_supply_due_alerts(cursor)
            conn.commit()
            return jsonify(build_dashboard_overview(cursor, user))
        except PermissionError:
            raise
        except Exception:
            # Dashboard cards are non-critical: a database/query problem must not
            # turn the homepage into a 500 response or reveal internal details.
            app.logger.exception("Dashboard overview failed")
            return jsonify(empty_dashboard_overview())
        finally:
            conn.close()

    @app.get("/api/workflows")
    def list_workflows():
        form_type = request.args.get("type", "").strip()
        status = request.args.get("status", "").strip()
        active = request.args.get("active", "").strip()
        mine = request.args.get("mine", "").strip()
        material_name = request.args.get("material_name", "").strip()
        material_code = request.args.get("material_code", "").strip()
        brand_model = request.args.get("brand_model", "").strip()
        spec = request.args.get("spec", "").strip()
        inspector = request.args.get("inspector", "").strip()
        applicant = request.args.get("applicant", "").strip()
        form_no = request.args.get("form_no", "").strip()
        maker = request.args.get("maker", "").strip()
        project = request.args.get("project", "").strip()
        conn = get_db()
        cursor = conn.cursor()
        user = current_user(cursor)
        try:
            if form_type == "claim" and status == "outbound":
                require_any_permission(cursor, "view_flow", "view_outbound")
            elif not active and not mine:
                require_permission(cursor, "view_flow")
        except Exception:
            conn.close()
            raise
        where = []
        params = []
        if form_type:
            where.append("f.form_type = ?")
            params.append(form_type)
        if status:
            where.append("f.status = ?")
            params.append(status)
        if active:
            where.append("f.status NOT IN ('completed', 'cancelled', 'rejected')")
        if material_name:
            where.append("EXISTS (SELECT 1 FROM workflow_items wi WHERE wi.form_id = f.id AND wi.material_name LIKE ?)")
            params.append(f"%{material_name}%")
        if material_code:
            where.append("EXISTS (SELECT 1 FROM workflow_items wi WHERE wi.form_id = f.id AND wi.material_code LIKE ?)")
            params.append(f"%{material_code}%")
        if brand_model:
            where.append("EXISTS (SELECT 1 FROM workflow_items wi WHERE wi.form_id = f.id AND wi.brand_model LIKE ?)")
            params.append(f"%{brand_model}%")
        if spec:
            where.append("EXISTS (SELECT 1 FROM workflow_items wi WHERE wi.form_id = f.id AND wi.spec LIKE ?)")
            params.append(f"%{spec}%")
        if inspector:
            where.append("EXISTS (SELECT 1 FROM workflow_tasks t WHERE t.form_id = f.id AND t.step_code = 'acceptance' AND t.signature LIKE ?)")
            params.append(f"%{inspector}%")
        if applicant:
            where.append(
                """
                (
                    u.display_name LIKE ?
                    OR u.username LIKE ?
                    OR f.applicant_id IN (
                        SELECT id FROM users
                        WHERE display_name LIKE ? OR username LIKE ? OR department LIKE ?
                    )
                )
                """
            )
            params.extend([f"%{applicant}%", f"%{applicant}%", f"%{applicant}%", f"%{applicant}%", f"%{applicant}%"])
        if form_no:
            where.append("f.form_no LIKE ?")
            params.append(f"%{form_no}%")
        if maker:
            where.append(
                """
                EXISTS (
                    SELECT 1 FROM workflow_items wi
                    LEFT JOIN semifinished_inventory si ON si.name = wi.material_name AND si.spec = wi.spec
                    LEFT JOIN semifinished_acceptances sa ON sa.id = si.acceptance_id
                    LEFT JOIN finished_good_inventory fgi ON fgi.product_name = wi.material_name AND fgi.spec = wi.spec
                    LEFT JOIN finished_acceptances fa ON fa.id = fgi.acceptance_id
                    LEFT JOIN users m ON m.id = COALESCE(sa.maker_id, fa.maker_id)
                    WHERE wi.form_id = f.id AND m.display_name LIKE ?
                )
                """
            )
            params.append(f"%{maker}%")
        if project:
            where.append(
                """
                EXISTS (
                    SELECT 1 FROM workflow_items wi
                    LEFT JOIN semifinished_inventory si ON si.name = wi.material_name AND si.spec = wi.spec
                    LEFT JOIN semifinished_acceptances sa ON sa.id = si.acceptance_id
                    LEFT JOIN finished_good_inventory fgi ON fgi.product_name = wi.material_name AND fgi.spec = wi.spec
                    LEFT JOIN finished_acceptances fa ON fa.id = fgi.acceptance_id
                    WHERE wi.form_id = f.id AND (sa.project_code LIKE ? OR fa.project_code LIKE ?)
                )
                """
            )
            params.extend([f"%{project}%", f"%{project}%"])
        if mine and user and "admin" not in user.get("role_codes", []):
            where.append(
                """
                EXISTS (
                    SELECT 1 FROM workflow_tasks t
                    WHERE t.form_id = f.id AND t.status = 'pending' AND t.assignee_id = ?
                )
                """
            )
            params.append(user["id"])
        elif user and "admin" not in user.get("role_codes", []):
            perms = role_permissions(cursor)
            has_view_flow = any(perms.get(role, {}).get("view_flow") for role in user.get("role_codes", []))
            has_view_outbound = any(perms.get(role, {}).get("view_outbound") for role in user.get("role_codes", []))
            if has_view_flow and has_view_outbound:
                pass
            elif has_view_flow:
                where.append(
                    """
                    (
                        f.form_type != 'claim'
                        OR f.applicant_id = ?
                        OR f.leader_id = ?
                        OR f.warehouse_user_id = ?
                        OR EXISTS (SELECT 1 FROM workflow_tasks t WHERE t.form_id = f.id AND t.assignee_id = ?)
                    )
                    """
                )
                params.extend([user["id"], user["id"], user["id"], user["id"]])
            elif has_view_outbound:
                where.append("f.form_type = 'claim'")
            else:
                where.append(
                    """
                    (
                        f.applicant_id = ?
                        OR f.leader_id = ?
                        OR f.warehouse_user_id = ?
                        OR EXISTS (SELECT 1 FROM workflow_tasks t WHERE t.form_id = f.id AND t.assignee_id = ?)
                    )
                    """
                )
                params.extend([user["id"], user["id"], user["id"], user["id"]])
        append_workflow_visibility(cursor, where, params, "f")
        sql = """
            SELECT f.*, u.display_name AS applicant_name, l.display_name AS leader_name,
                   (
                       SELECT GROUP_CONCAT(t.signature, '、')
                       FROM workflow_tasks t
                       WHERE t.form_id = f.id
                         AND t.step_code IN ('leader_acceptance', 'leader_claim', 'leader_borrow')
                         AND COALESCE(t.signature, '') <> ''
                   ) AS leader_signatures
            FROM workflow_forms f
            LEFT JOIN users u ON u.id = f.applicant_id
            LEFT JOIN users l ON l.id = f.leader_id
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY f.id DESC"
        cursor.execute(sql, params)
        forms = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify(forms)


    @app.get("/api/dashboard/chart")
    def dashboard_chart():
        metric = request.args.get("metric", "month_in_out")
        conn = get_db()
        cursor = conn.cursor()
        if not current_user(cursor):
            conn.close()
            raise PermissionError("请先登录")
        labels = []
        series = []
        today = datetime.strptime(today_text(), "%Y-%m-%d")
        if metric == "day_in_out":
            labels = [(today - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(13, -1, -1)]
            in_values = []
            out_values = []
            for label in labels:
                cursor.execute("SELECT COALESCE(SUM(quantity), 0) FROM stock_records WHERE stock_source = ? AND operation_type = 'in' AND operation_date = ?", (STOCK_SOURCE_FORMAL, label))
                in_values.append(float(cursor.fetchone()[0] or 0))
                cursor.execute("SELECT COALESCE(SUM(quantity), 0) FROM stock_records WHERE stock_source = ? AND operation_type = 'out' AND operation_date = ?", (STOCK_SOURCE_FORMAL, label))
                out_values.append(float(cursor.fetchone()[0] or 0))
            series = [{"name": "入库", "values": in_values}, {"name": "出库", "values": out_values}]
        elif metric == "month_amount":
            for offset in range(11, -1, -1):
                month_index = today.month - offset
                year = today.year + (month_index - 1) // 12
                month = (month_index - 1) % 12 + 1
                labels.append(f"{year}-{month:02d}")
            in_values = []
            out_values = []
            for label in labels:
                cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM stock_records WHERE stock_source = ? AND operation_type = 'in' AND substr(operation_date, 1, 7) = ?", (STOCK_SOURCE_FORMAL, label))
                in_values.append(float(cursor.fetchone()[0] or 0))
                cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM stock_records WHERE stock_source = ? AND operation_type = 'out' AND substr(operation_date, 1, 7) = ?", (STOCK_SOURCE_FORMAL, label))
                out_values.append(float(cursor.fetchone()[0] or 0))
            series = [{"name": "入库金额", "values": in_values}, {"name": "出库金额", "values": out_values}]
        else:
            for offset in range(11, -1, -1):
                month_index = today.month - offset
                year = today.year + (month_index - 1) // 12
                month = (month_index - 1) % 12 + 1
                labels.append(f"{year}-{month:02d}")
            in_values = []
            out_values = []
            for label in labels:
                cursor.execute("SELECT COALESCE(SUM(quantity), 0) FROM stock_records WHERE stock_source = ? AND operation_type = 'in' AND substr(operation_date, 1, 7) = ?", (STOCK_SOURCE_FORMAL, label))
                in_values.append(float(cursor.fetchone()[0] or 0))
                cursor.execute("SELECT COALESCE(SUM(quantity), 0) FROM stock_records WHERE stock_source = ? AND operation_type = 'out' AND substr(operation_date, 1, 7) = ?", (STOCK_SOURCE_FORMAL, label))
                out_values.append(float(cursor.fetchone()[0] or 0))
            series = [{"name": "入库", "values": in_values}, {"name": "出库", "values": out_values}]
        conn.close()
        return jsonify({"metric": metric, "labels": labels, "series": series})


    @app.get("/api/workflows/next-no")
    def workflow_next_no():
        prefix = request.args.get("prefix", "YS").upper()
        if prefix not in {"YS", "CK", "RK", "BC", "CP", "BY", "CY", "JY", "GH"}:
            return jsonify({"error": "prefix must be YS, CK, RK, BC, CP, BY, CY, JY or GH"}), 400
        conn = get_db()
        cursor = conn.cursor()
        form_no = next_form_no(cursor, prefix)
        conn.close()
        return jsonify({"form_no": form_no})


    @app.put("/api/workflows/<int:form_id>")
    def update_workflow(form_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT * FROM workflow_forms WHERE id = ?", (form_id,))
            form = row_to_dict(cursor.fetchone())
            if not form:
                raise ValueError("流程不存在")
            if form.get("origin_type") == AUTO_CLAIM_ORIGIN_TYPE:
                raise PermissionError("系统自动生成的历史领用结算流程不允许修改")
            user = current_user(cursor)
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
                        "unit",
                        "request_quantity",
                        "arrival_quantity",
                        "unit_price",
                    ]
            else:
                item_fields = [
                    "material_code",
                    "material_name",
                    "brand_model",
                    "spec",
                    "purchase_applicant",
                    "unit",
                    "request_quantity",
                    "arrival_quantity",
                    "unit_price",
                    "qualified_quantity",
                    "unqualified_quantity",
                    "approved_quantity",
                    "outbound_quantity",
                ]
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
                )
                cursor.execute("DELETE FROM workflow_items WHERE form_id = ?", (form_id,))
                insert_claim_allocations(cursor, form_id, allocations)
                workflow_items_replaced = True
            elif applicant_limited_edit and form.get("form_type") == "borrow" and "items" in data:
                requested_items = borrow_revision_requested_items(
                    cursor, form_id, data.get("items") or []
                )
                allocations = allocate_borrow_items(
                    cursor,
                    requested_items,
                    user,
                    include_temporary=temporary_inventory_enabled(cursor),
                )
                cursor.execute("DELETE FROM workflow_items WHERE form_id = ?", (form_id,))
                insert_borrow_allocations(cursor, form_id, allocations)
                workflow_items_replaced = True

            for item in ([] if workflow_items_replaced else (data.get("items") or [])):
                item_id = int(item.get("id") or 0)
                if not item_id:
                    continue
                fields = []
                params = []
                for key in item_fields:
                    if key in item:
                        fields.append(f"{key} = ?")
                        if key in numeric_fields:
                            params.append(price_value(item.get(key), "单价") if key == "unit_price" else quantity_value(item.get(key), "数量"))
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
            conn.commit()
            updated = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "form": updated})


    @app.delete("/api/workflows/<int:form_id>")
    def delete_workflow(form_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT * FROM workflow_forms WHERE id = ?", (form_id,))
            form = row_to_dict(cursor.fetchone())
            if not form:
                raise ValueError("流程不存在")
            if form.get("origin_type") == AUTO_CLAIM_ORIGIN_TYPE:
                raise PermissionError("系统自动生成的历史领用结算流程不允许删除或撤回")
            user = current_user(cursor)
            require_workflow_edit_or_applicant(cursor, form, user)
            if (
                form.get("form_type") == "acceptance"
                and form.get("origin_type") == "temporary_transfer"
            ):
                cursor.execute(
                    """
                    SELECT accepted_quantity
                    FROM inventory_transfer_tasks
                    WHERE id = ?
                    """,
                    (int(form.get("origin_ref_id") or 0),),
                )
                task_row = cursor.fetchone()
                if task_row and float(task_row["accepted_quantity"] or 0) > 1e-9:
                    raise ValueError("该转移验收已有正式入库事实，不能撤回或删除")
                cursor.execute(
                    "UPDATE workflow_tasks SET status = 'cancelled', updated_at = ? WHERE form_id = ? AND status = 'pending'",
                    (now_text(), form_id),
                )
                cursor.execute(
                    "UPDATE workflow_forms SET status = 'cancelled', current_step = 'cancelled', updated_at = ? WHERE id = ?",
                    (now_text(), form_id),
                )
                mark_transfer_acceptance_failed(
                    cursor,
                    form_id,
                    "关联验收已撤回",
                    user,
                    request.headers.get(
                        "X-Forwarded-For", request.remote_addr or ""
                    ),
                )
                conn.commit()
                conn.close()
                notify_todos_changed()
                return jsonify({"success": True, "preserved": True})
            if form.get("form_type") == "claim" and claim_has_actual_outbound(cursor, form_id):
                raise ValueError("已实际出库的领用流程不能删除，请使用后续冲销流程")
            if form.get("form_type") == "borrow" and borrow_has_actual_outbound(cursor, form_id):
                raise ValueError("已实际借出的借用流程不能删除，请使用后续冲销流程")
            recycle_workflow(cursor, form_id, user.get("id"))
            cursor.execute("DELETE FROM workflow_tasks WHERE form_id = ?", (form_id,))
            cursor.execute("DELETE FROM workflow_items WHERE form_id = ?", (form_id,))
            cursor.execute("DELETE FROM workflow_forms WHERE id = ?", (form_id,))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})


    @app.get("/api/workflows/<int:form_id>")
    def get_workflow(form_id):
        conn = get_db()
        cursor = conn.cursor()
        require_form_view(cursor, form_id)
        form = serialize_form(cursor, form_id)
        conn.close()
        if not form:
            return jsonify({"error": "流程不存在"}), 404
        return jsonify(form)


    def personal_flow_filter_clause():
        status = request.args.get("status", "").strip()
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()
        clauses = []
        params = []
        if status:
            clauses.append("f.status = ?")
            params.append(status)
        if date_from:
            clauses.append("substr(f.created_at, 1, 10) >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("substr(f.created_at, 1, 10) <= ?")
            params.append(date_to)
        return clauses, params


    @app.get("/api/my-flows/inspections")
    def my_inspection_flows():
        conn = get_db()
        cursor = conn.cursor()
        user = current_user(cursor)
        if not user:
            conn.close()
            return jsonify({"success": False, "error": "请先登录"}), 401
        extra_clauses, extra_params = personal_flow_filter_clause()
        where_sql = """
            EXISTS (
                SELECT 1 FROM workflow_tasks t
                WHERE t.form_id = f.id
                  AND t.step_code = 'acceptance'
                  AND t.assignee_id = ?
            )
            """
        if extra_clauses:
            where_sql = f"({where_sql}) AND {' AND '.join(extra_clauses)}"
        rows = workflow_list_rows(
            cursor,
            where_sql,
            [user["id"], *extra_params],
        )
        conn.close()
        return jsonify({"items": rows})


    @app.get("/api/my-flows/started")
    def my_started_flows():
        conn = get_db()
        cursor = conn.cursor()
        user = current_user(cursor)
        if not user:
            conn.close()
            return jsonify({"success": False, "error": "请先登录"}), 401
        extra_clauses, extra_params = personal_flow_filter_clause()
        where_sql = "f.applicant_id = ?"
        if extra_clauses:
            where_sql = f"({where_sql}) AND {' AND '.join(extra_clauses)}"
        rows = workflow_list_rows(cursor, where_sql, [user["id"], *extra_params])
        for row in rows:
            allowed = row.get("origin_type") != AUTO_CLAIM_ORIGIN_TYPE and workflow_applicant_can_modify(cursor, row["id"], user)
            row["can_withdraw"] = allowed
            row["can_edit"] = allowed
        conn.close()
        return jsonify({"items": rows})


    @app.post("/api/workflows/<int:form_id>/return")
    def return_workflow(form_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            cursor.execute("SELECT * FROM workflow_forms WHERE id = ?", (form_id,))
            form = row_to_dict(cursor.fetchone())
            if not form:
                raise ValueError("流程不存在")
            if form.get("origin_type") == AUTO_CLAIM_ORIGIN_TYPE:
                raise PermissionError("系统自动生成的历史领用结算流程不允许退回修改")
            if form.get("status") in {"completed", "cancelled"}:
                raise ValueError("已结束流程不能退回")
            current_step = form.get("current_step") or form.get("status")
            steps = workflow_step_codes(form.get("form_type"))
            if current_step not in steps:
                raise ValueError("当前流程步骤不能退回")
            current_index = steps.index(current_step)
            targets = ["applicant_revision"] + steps[:current_index]
            target_step = str(data.get("target_step") or "").strip()
            reason = str(data.get("reason") or "").strip()
            if not target_step or target_step not in targets:
                raise ValueError("请选择要退回到的步骤")
            if not reason:
                raise ValueError("请填写退回原因")
            require_task_assignee(cursor, user, form_id, current_step, int(data.get("task_id") or 0))
            cursor.execute(
                "SELECT * FROM workflow_tasks WHERE form_id = ? AND step_code = ? AND status = 'pending'",
                (form_id, current_step),
            )
            pending_tasks = [dict(row) for row in cursor.fetchall()]
            for task in pending_tasks:
                task_data = parse_json(task.get("data_json"), {})
                task_data["return_to"] = target_step
                task_data["return_reason"] = reason
                cursor.execute(
                    """
                    UPDATE workflow_tasks
                    SET status = 'completed', decision = '退回', signature = ?, signed_at = ?,
                        data_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        data.get("signature") or user.get("display_name") or "",
                        data.get("signed_at") or today_text(),
                        json.dumps(task_data, ensure_ascii=False),
                        now_text(),
                        task["id"],
                    ),
                )
            assignee_ids = workflow_return_assignees(cursor, form, target_step)
            create_workflow_tasks(cursor, form_id, form["form_type"], target_step, assignee_ids)
            cursor.execute(
                "UPDATE workflow_forms SET status = ?, current_step = ?, updated_at = ? WHERE id = ?",
                (target_step, target_step, now_text(), form_id),
            )
            conn.commit()
            form_data = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        notify_todos_changed()
        return jsonify({"success": True, "form": form_data})


    @app.post("/api/workflows/<int:form_id>/resubmit-returned")
    def resubmit_returned_workflow(form_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            cursor.execute("SELECT * FROM workflow_forms WHERE id = ?", (form_id,))
            form = row_to_dict(cursor.fetchone())
            if not form:
                raise ValueError("流程不存在")
            if form.get("origin_type") == AUTO_CLAIM_ORIGIN_TYPE:
                raise PermissionError("系统自动生成的历史领用结算流程不允许重新提交")
            if form.get("current_step") != "applicant_revision" or form.get("status") != "applicant_revision":
                raise ValueError("当前流程不在发起人修改步骤")
            task = require_task_assignee(cursor, user, form_id, "applicant_revision")
            if form.get("form_type") == "claim":
                begin_inventory_transaction(conn)
                requested_items = current_claim_requested_items(cursor, form_id)
                allocations = allocate_claim_items(
                    cursor,
                    requested_items,
                    user,
                    include_temporary=temporary_inventory_enabled(cursor),
                )
                cursor.execute("DELETE FROM workflow_items WHERE form_id = ?", (form_id,))
                insert_claim_allocations(cursor, form_id, allocations)
            elif form.get("form_type") == "borrow":
                begin_inventory_transaction(conn)
                requested_items = current_borrow_requested_items(cursor, form_id)
                allocations = allocate_borrow_items(
                    cursor,
                    requested_items,
                    user,
                    include_temporary=temporary_inventory_enabled(cursor),
                )
                cursor.execute("DELETE FROM workflow_items WHERE form_id = ?", (form_id,))
                insert_borrow_allocations(cursor, form_id, allocations)
            first_step = workflow_step_codes(form.get("form_type"))[0]
            assignee_ids = workflow_return_assignees(cursor, form, first_step)
            cursor.execute(
                """
                UPDATE workflow_tasks
                SET status = 'completed', decision = '已重新提交', signature = ?, signed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (user.get("display_name") or "", today_text(), now_text(), task["id"]),
            )
            create_workflow_tasks(cursor, form_id, form["form_type"], first_step, assignee_ids)
            cursor.execute(
                "UPDATE workflow_forms SET status = ?, current_step = ?, updated_at = ? WHERE id = ?",
                (first_step, first_step, now_text(), form_id),
            )
            conn.commit()
            form_data = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        notify_todos_changed()
        return jsonify({"success": True, "form": form_data})


    @app.post("/api/workflows/duplicate-check")
    def duplicate_workflow_check():
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "start_acceptance")
            form_type = str(data.get("form_type") or "acceptance").strip()
            if form_type not in {"acceptance", "semifinished", "finished"}:
                raise ValueError("重复验收验证仅支持物料、半成品和成品验收")
            days = duplicate_check_days(cursor)
            matches = []
            if form_type == "acceptance":
                for index, item in enumerate(data.get("items") or []):
                    values = duplicate_material_values(cursor, item)
                    rows = duplicate_acceptance_match_rows(cursor, form_type, values, days)
                    for row in rows:
                        row.update(
                            {
                                "index": index,
                                "item_label": values.get("material_name") or values.get("spec") or "物料",
                            }
                        )
                        matches.append(row)
            else:
                payload = production_item_payload(form_type, data)
                unit_price = 0
                try:
                    acceptance_quantity = payload["acceptance_quantity"]
                    _, _, _, _, total_cost = production_components_from_payload(cursor, form_type, data, acceptance_quantity)
                    unit_price = total_cost / acceptance_quantity if acceptance_quantity > 0 else 0
                except Exception:
                    unit_price = duplicate_float(data.get("unit_price"))
                values = {
                    "material_name": duplicate_norm(payload["name"]),
                    "spec": duplicate_norm(payload.get("spec")),
                    "unit": duplicate_norm(payload.get("unit")),
                    "arrival_quantity": duplicate_float(payload.get("acceptance_quantity")),
                    "unit_price": duplicate_float(unit_price),
                }
                rows = duplicate_acceptance_match_rows(cursor, form_type, values, days)
                for row in rows:
                    row.update({"index": 0, "item_label": values.get("material_name") or ("半成品" if form_type == "semifinished" else "成品")})
                    matches.append(row)
            conn.close()
            return jsonify({"success": True, "days": days, "matches": matches})
        except Exception as exc:
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
