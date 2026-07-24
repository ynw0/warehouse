# -*- coding: utf-8 -*-
"""Production workflow and inventory route registration."""

import json

from flask import jsonify, request

from warehouse_suit.db import now_text, row_to_dict, today_text
from warehouse_suit.numbering import next_form_no, next_table_no
from warehouse_suit.production_service import (
    claimed_material_batch_pool,
    claimed_material_pool,
    ensure_production_serials_available,
    insert_material_consumptions,
    insert_semifinished_consumptions,
    next_production_serials,
    normalize_production_serial_items,
    prepare_material_component_consumptions,
    prepare_semifinished_component_consumptions,
    production_components_from_payload,
    production_item_payload,
    production_quality_from_payload,
    require_production_kind,
    semifinished_pool,
)
from warehouse_suit.recycle import recycle_table_row
from warehouse_suit.settings import parse_json
from warehouse_suit.validation import (
    nonnegative_int_value,
    normalize_component_json,
    positive_int_value,
    price_value,
    quantity_value,
    validate_plain_text,
    validate_project_code,
    validate_serial_no,
    validation_rule_enabled,
)
from warehouse_suit.workflow_service import (
    aggregate_acceptance_results,
    create_workflow_tasks,
    require_form_status,
    require_inventory_permission,
    require_permission,
    require_task_assignee,
    resolve_department_leader,
    serialize_form,
    validate_validator_users,
    workflow_assignees,
    workflow_generated_title,
)


def register_production_routes(app, *, get_db, current_user_provider, optional_active_user_id_provider):
    current_user = current_user_provider
    optional_active_user_id = optional_active_user_id_provider
    @app.get("/api/production/material-pool")
    def production_material_pool():
        conn = get_db()
        cursor = conn.cursor()
        require_permission(cursor, "start_acceptance")
        rows = claimed_material_pool(cursor)
        conn.close()
        return jsonify({"items": rows})


    @app.get("/api/production/material-batch-pool")
    def production_material_batch_pool():
        conn = get_db()
        cursor = conn.cursor()
        require_permission(cursor, "start_acceptance")
        rows = claimed_material_batch_pool(cursor)
        conn.close()
        return jsonify({"items": rows})


    @app.get("/api/production/semifinished-pool")
    def production_semifinished_pool():
        conn = get_db()
        cursor = conn.cursor()
        require_permission(cursor, "start_acceptance")
        rows = semifinished_pool(cursor)
        conn.close()
        return jsonify({"items": rows})


    @app.post("/api/production/<kind>/workflows")
    def create_production_workflow(kind):
        kind = require_production_kind(kind)
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_permission(cursor, "start_acceptance")
            payload = production_item_payload(kind, data)
            acceptance_quantity = payload["acceptance_quantity"]
            material_components, semifinished_components, material_prepared, semifinished_prepared, total_cost = production_components_from_payload(cursor, kind, data, acceptance_quantity)
            prefix = "BY" if kind == "semifinished" else "CY"
            form_no = next_form_no(cursor, prefix)
            validator_ids = [int(value) for value in data.get("validator_ids") or [] if int(value)]
            if not validator_ids:
                validator_ids = [user["id"]]
            validator_ids = validate_validator_users(cursor, validator_ids, kind)
            validator_ids = workflow_assignees(cursor, kind, "acceptance", validator_ids)
            unit_cost = total_cost / acceptance_quantity if acceptance_quantity > 0 else 0
            title = workflow_generated_title(user, form_no)
            form_data = {
                "validator_ids": validator_ids,
                "production_type": kind,
                "acceptance_date": payload["acceptance_date"],
                "maker": payload.get("maker") or user.get("display_name") or user.get("username") or "",
            }
            cursor.execute(
                """
                INSERT INTO workflow_forms
                    (form_no, form_type, title, status, current_step, applicant_id, leader_id,
                     total_amount, data_json, created_at, updated_at)
                VALUES (?, ?, ?, 'acceptance', 'acceptance', ?, ?, ?, ?, ?, ?)
                """,
                (
                    form_no,
                    kind,
                    title,
                    user["id"],
                    None,
                    total_cost,
                    json.dumps(form_data, ensure_ascii=False),
                    now_text(),
                    now_text(),
                ),
            )
            form_id = cursor.lastrowid
            item_data = {
                "production_type": kind,
                "acceptance_date": payload["acceptance_date"],
                "maker": payload.get("maker") or user.get("display_name") or user.get("username") or "",
                "material_components": material_components,
                "semifinished_components": semifinished_components,
                "estimated_material_components": material_prepared,
                "estimated_semifinished_components": semifinished_prepared,
            }
            cursor.execute(
                """
                INSERT INTO workflow_items
                    (form_id, material_id, material_code, material_name, brand_model, spec, unit,
                     request_quantity, arrival_quantity, unit_price, data_json)
                VALUES (?, NULL, ?, ?, '', ?, ?, ?, ?, ?, ?)
                """,
                (
                    form_id,
                    form_no,
                    payload["name"],
                    payload["spec"],
                    payload["unit"],
                    acceptance_quantity,
                    acceptance_quantity,
                    unit_cost,
                    json.dumps(item_data, ensure_ascii=False),
                ),
            )
            create_workflow_tasks(cursor, form_id, kind, "acceptance", validator_ids)
            conn.commit()
            form = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "form": form})


    @app.post("/api/production/<kind>/<int:form_id>/inspect")
    def inspect_production_workflow(kind, form_id):
        kind = require_production_kind(kind)
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            task_id = int(data.get("task_id") or 0)
            task = require_task_assignee(cursor, user, form_id, "acceptance", task_id)
            form_row = require_form_status(cursor, form_id, kind, "acceptance")
            current_leader_id = form_row["leader_id"]
            leader_id = int(data.get("leader_id") or 0)
            if current_leader_id:
                if leader_id and leader_id != current_leader_id:
                    raise ValueError("已有验收员指定领导审批，不能更改")
            else:
                leader_id = resolve_department_leader(cursor, user, kind, "leader_acceptance", leader_id)
                cursor.execute("UPDATE workflow_forms SET leader_id = ?, updated_at = ? WHERE id = ?", (leader_id, now_text(), form_id))
                current_leader_id = leader_id
            cursor.execute("SELECT * FROM workflow_items WHERE form_id = ? ORDER BY id LIMIT 1", (form_id,))
            item_row = cursor.fetchone()
            if not item_row:
                raise ValueError("生产验收明细不存在")
            incoming = (data.get("items") or [{}])[0]
            serial_items = normalize_production_serial_items(cursor, kind, item_row["material_name"], item_row["arrival_quantity"], incoming)
            qualified_count = sum(1 for serial_item in serial_items if serial_item.get("qualified"))
            unqualified_count = len(serial_items) - qualified_count
            if incoming.get("serial_items") or incoming.get("serials"):
                quality = {
                    "acceptance_quantity": float(item_row["arrival_quantity"] or 0),
                    "appearance_ok_quantity": qualified_count,
                    "function_ok_quantity": qualified_count,
                    "performance_ok_quantity": qualified_count,
                    "qualified_quantity": qualified_count,
                    "unqualified_quantity": unqualified_count,
                }
            else:
                quality = production_quality_from_payload(
                    {
                        "acceptance_quantity": item_row["arrival_quantity"],
                        "appearance_ok_quantity": incoming.get("appearance_ok_quantity"),
                        "function_ok_quantity": incoming.get("function_ok_quantity"),
                        "performance_ok_quantity": incoming.get("performance_ok_quantity"),
                    }
                )
                qualified_limit = int(round(quality["qualified_quantity"]))
                for index, serial_item in enumerate(serial_items):
                    serial_item["qualified"] = index < qualified_limit
                    if not serial_item["qualified"] and not serial_item.get("abnormal_conditions"):
                        serial_item["abnormal_conditions"] = ["不合格"]
            defects = [
                {"serial_no": serial_item["serial_no"], "abnormal_conditions": serial_item.get("abnormal_conditions") or ["不合格"]}
                for serial_item in serial_items
                if not serial_item.get("qualified")
            ]
            task_items = [
                {
                    "id": int(item_row["id"]),
                    "qualified_quantity": quality["qualified_quantity"],
                    "unqualified_quantity": quality["unqualified_quantity"],
                    "appearance_ok_quantity": quality["appearance_ok_quantity"],
                    "function_ok_quantity": quality["function_ok_quantity"],
                    "performance_ok_quantity": quality["performance_ok_quantity"],
                    "defects": defects,
                    "serial_items": serial_items,
                    "remark": incoming.get("remark") or data.get("remark") or "",
                }
            ]
            cursor.execute(
                """
                UPDATE workflow_tasks
                SET status = 'completed', decision = ?, signature = ?, signed_at = ?, data_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    data.get("decision") or "同意",
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
                warehouse_user_id = workflow_assignees(cursor, kind, "inbound", [warehouse_user_id])[0]
                cursor.execute("UPDATE workflow_forms SET warehouse_user_id = ?, updated_at = ? WHERE id = ?", (warehouse_user_id, now_text(), form_id))
            cursor.execute("SELECT COUNT(*) FROM workflow_tasks WHERE form_id = ? AND step_code = 'acceptance' AND status = 'pending'", (form_id,))
            if cursor.fetchone()[0] == 0:
                create_workflow_tasks(cursor, form_id, kind, "leader_acceptance", [current_leader_id])
                cursor.execute("UPDATE workflow_forms SET status = 'leader_acceptance', current_step = 'leader_acceptance', updated_at = ? WHERE id = ?", (now_text(), form_id))
            conn.commit()
            form = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "form": form})


    @app.post("/api/production/<kind>/<int:form_id>/leader")
    def leader_production_workflow(kind, form_id):
        kind = require_production_kind(kind)
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            require_form_status(cursor, form_id, kind, "leader_acceptance")
            task = require_task_assignee(cursor, user, form_id, "leader_acceptance")
            decision = data.get("decision") or "同意"
            if decision != "同意" and not str(data.get("remark") or "").strip():
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
                create_workflow_tasks(cursor, form_id, kind, "inbound", [warehouse_user_id])
            conn.commit()
            form = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "form": form})


    @app.post("/api/production/<kind>/<int:form_id>/inbound")
    def inbound_production_workflow(kind, form_id):
        kind = require_production_kind(kind)
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            form = require_form_status(cursor, form_id, kind, "inbound")
            task = require_task_assignee(cursor, user, form_id, "inbound")
            cursor.execute("SELECT * FROM workflow_items WHERE form_id = ? ORDER BY id LIMIT 1", (form_id,))
            item = dict(cursor.fetchone() or {})
            if not item:
                raise ValueError("生产验收明细不存在")
            item_data = parse_json(item.get("data_json"), {})
            approved_qty = quantity_value(data.get("approved_quantity") if data.get("approved_quantity") is not None else item.get("qualified_quantity"), "入库数量")
            if validation_rule_enabled("workflow_bounds") and approved_qty > float(item.get("qualified_quantity") or 0) + 1e-9:
                raise ValueError("入库数量不能大于合格数量")
            shelf_id = nonnegative_int_value(data.get("shelf_id"), "货架", 0)
            layer_number = positive_int_value(data.get("layer_number") or 1, "层号")
            zone_name = validate_plain_text(data.get("zone_name") or "", "分区", max_length=20).upper()
            if shelf_id:
                cursor.execute("SELECT id FROM shelves WHERE id = ?", (shelf_id,))
                if not cursor.fetchone():
                    raise ValueError("storage shelf not found")
            else:
                shelf_id = None
            acceptance_quantity = float(item.get("arrival_quantity") or 0)
            material_components, semifinished_components, material_prepared, semifinished_prepared, total_cost = production_components_from_payload(
                cursor,
                kind,
                {
                    "material_components": item_data.get("material_components") or item_data.get("components") or [],
                    "semifinished_components": item_data.get("semifinished_components") or [],
                },
                acceptance_quantity,
            )
            cost_price = total_cost / acceptance_quantity if acceptance_quantity > 0 else 0
            acceptance_date = item_data.get("acceptance_date") or form["data_json"] and parse_json(form["data_json"], {}).get("acceptance_date") or today_text()
            total_serial_count = int(round(acceptance_quantity))
            if abs(total_serial_count - acceptance_quantity) > 1e-6:
                raise ValueError("半成品/成品入库数量必须是整数，才能逐个编号入库")
            approved_count = int(round(approved_qty))
            if abs(approved_count - approved_qty) > 1e-6:
                raise ValueError("半成品/成品入库数量必须是整数")
            serial_items = [dict(value) for value in (item_data.get("serial_items") or []) if isinstance(value, dict)]
            if len(serial_items) != total_serial_count:
                generated_serials = next_production_serials(cursor, kind, item["material_name"], total_serial_count)
                qualified_limit = int(round(float(item.get("qualified_quantity") or 0)))
                serial_items = [
                    {
                        "serial_no": generated_serials[index],
                        "qualified": index < qualified_limit,
                        "abnormal_conditions": [] if index < qualified_limit else ["不合格"],
                        "remark": "",
                    }
                    for index in range(total_serial_count)
                ]
            qualified_serials = [serial_item for serial_item in serial_items if serial_item.get("qualified")]
            unqualified_serials = [serial_item for serial_item in serial_items if not serial_item.get("qualified")]
            if validation_rule_enabled("workflow_bounds") and approved_count > len(qualified_serials):
                raise ValueError("入库数量不能大于逐件验收合格数量")
            approved_serials = qualified_serials[:approved_count]
            conflict_serials = [item["serial_no"] for item in approved_serials]
            if kind == "finished":
                conflict_serials.extend(item["serial_no"] for item in unqualified_serials)
            else:
                conflict_serials.extend(item["serial_no"] for item in unqualified_serials)
            ensure_production_serials_available(cursor, kind, conflict_serials)
            if kind == "semifinished":
                cursor.execute(
                    """
                    INSERT INTO semifinished_acceptances
                        (acceptance_no, name, spec, acceptance_quantity, unit, acceptance_date,
                         qualified_quantity, unqualified_quantity, appearance_ok_quantity,
                         function_ok_quantity, performance_ok_quantity, cost_price, components_json,
                         serials_json, applicant_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        form["form_no"],
                        item["material_name"],
                        item.get("spec") or "",
                        acceptance_quantity,
                        item.get("unit") or "台",
                        acceptance_date,
                        item.get("qualified_quantity") or 0,
                        item.get("unqualified_quantity") or 0,
                        item_data.get("appearance_ok_quantity") or 0,
                        item_data.get("function_ok_quantity") or 0,
                        item_data.get("performance_ok_quantity") or 0,
                        cost_price,
                        json.dumps(material_prepared, ensure_ascii=False),
                        json.dumps(serial_items, ensure_ascii=False),
                        form["applicant_id"],
                        now_text(),
                        now_text(),
                    ),
                )
                acceptance_id = cursor.lastrowid
                insert_material_consumptions(cursor, "semifinished", acceptance_id, material_prepared)
                if approved_qty > 0:
                    for serial_item in approved_serials:
                        cursor.execute(
                            """
                            INSERT INTO semifinished_inventory
                                (acceptance_id, name, spec, unit, quantity, used_quantity, cost_price,
                                 components_json, shelf_id, layer_number, zone_name, serial_no,
                                 acceptance_date, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                acceptance_id,
                                item["material_name"],
                                item.get("spec") or "",
                                item.get("unit") or "台",
                                1,
                                cost_price,
                                json.dumps(material_prepared, ensure_ascii=False),
                                shelf_id,
                                layer_number,
                                zone_name,
                                serial_item["serial_no"],
                                acceptance_date,
                                now_text(),
                                now_text(),
                            ),
                        )
                defect_serials = [serial_item["serial_no"] for serial_item in unqualified_serials]
                for serial_item in unqualified_serials:
                    cursor.execute(
                        """
                        INSERT INTO defective_semifinished_goods
                            (semifinished_acceptance_id, name, spec, serial_no, abnormal_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            acceptance_id,
                            item["material_name"],
                            item.get("spec") or "",
                            serial_item["serial_no"],
                            json.dumps(serial_item.get("abnormal_conditions") or ["不合格"], ensure_ascii=False),
                            now_text(),
                        ),
                    )
                item_data.update(
                    {
                        "production_acceptance_id": acceptance_id,
                        "serial_items": serial_items,
                        "inbound_serials": [serial_item["serial_no"] for serial_item in approved_serials],
                        "defect_serials": defect_serials,
                    }
                )
            else:
                defects = [
                    {
                        "serial_no": serial_item["serial_no"],
                        "abnormal_conditions": serial_item.get("abnormal_conditions") or ["不合格"],
                    }
                    for serial_item in unqualified_serials
                ]
                cursor.execute(
                    """
                    INSERT INTO finished_acceptances
                        (acceptance_no, product_name, spec, acceptance_quantity, unit, acceptance_date,
                         qualified_quantity, unqualified_quantity, appearance_ok_quantity,
                         function_ok_quantity, performance_ok_quantity, cost_price,
                          material_components_json, semifinished_components_json, serials_json, applicant_id,
                          created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        form["form_no"],
                        item["material_name"],
                        item.get("spec") or "",
                        acceptance_quantity,
                        item.get("unit") or "台",
                        acceptance_date,
                        item.get("qualified_quantity") or 0,
                        item.get("unqualified_quantity") or 0,
                        item_data.get("appearance_ok_quantity") or 0,
                        item_data.get("function_ok_quantity") or 0,
                        item_data.get("performance_ok_quantity") or 0,
                        cost_price,
                        json.dumps(material_prepared, ensure_ascii=False),
                        json.dumps(semifinished_prepared, ensure_ascii=False),
                        json.dumps(serial_items, ensure_ascii=False),
                        form["applicant_id"],
                        now_text(),
                        now_text(),
                    ),
                )
                acceptance_id = cursor.lastrowid
                insert_material_consumptions(cursor, "finished", acceptance_id, material_prepared)
                insert_semifinished_consumptions(cursor, acceptance_id, semifinished_prepared)
                if approved_qty > 0:
                    for serial_item in approved_serials:
                        cursor.execute(
                            """
                            INSERT INTO finished_good_inventory
                                (acceptance_id, product_name, spec, unit, quantity, cost_price,
                                 shelf_id, layer_number, zone_name, serial_no,
                                 acceptance_date, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                acceptance_id,
                                item["material_name"],
                                item.get("spec") or "",
                                item.get("unit") or "台",
                                1,
                                cost_price,
                                shelf_id,
                                layer_number,
                                zone_name,
                                serial_item["serial_no"],
                                acceptance_date,
                                now_text(),
                                now_text(),
                            ),
                        )
                defect_serials = [defect["serial_no"] for defect in defects]
                for defect in defects:
                    cursor.execute(
                        """
                        INSERT INTO defective_finished_goods
                            (finished_acceptance_id, product_name, spec, serial_no, abnormal_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            acceptance_id,
                            item["material_name"],
                            item.get("spec") or "",
                            defect["serial_no"],
                            json.dumps(defect["abnormal_conditions"], ensure_ascii=False),
                            now_text(),
                        ),
                    )
                item_data.update(
                    {
                        "production_acceptance_id": acceptance_id,
                        "serial_items": serial_items,
                        "inbound_serials": [serial_item["serial_no"] for serial_item in approved_serials],
                        "defect_serials": defect_serials,
                    }
                )
            item_data.update({"shelf_id": shelf_id, "layer_number": layer_number, "zone_name": zone_name})
            cursor.execute(
                "UPDATE workflow_items SET approved_quantity = ?, unit_price = ?, data_json = ? WHERE id = ?",
                (approved_qty, cost_price, json.dumps(item_data, ensure_ascii=False), item["id"]),
            )
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
                SET status = 'completed', current_step = 'completed', warehouse_user_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (user["id"], now_text(), form_id),
            )
            conn.commit()
            form_data = serialize_form(cursor, form_id)
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "form": form_data})


    @app.get("/api/production/semifinished")
    def production_semifinished_list():
        conn = get_db()
        cursor = conn.cursor()
        require_inventory_permission(cursor, "semifinished", "read")
        project_code = request.args.get("project_code", "").strip()
        params = []
        where_clause = ""
        if project_code:
            where_clause = " AND (sa.project_code LIKE ?)"
            params.append(f"%{project_code}%")
        cursor.execute(
            f"""
            SELECT sa.*, u.display_name AS applicant_name
            FROM semifinished_acceptances sa
            LEFT JOIN users u ON u.id = sa.applicant_id
            WHERE 1=1 {where_clause}
            ORDER BY sa.id DESC
            LIMIT 100
            """,
            params,
        )
        acceptances = []
        for row in cursor.fetchall():
            item = dict(row)
            item["components"] = parse_json(item.pop("components_json", "[]"), [])
            acceptances.append(item)
        inventory = semifinished_pool(cursor)
        cursor.execute(
            """
            SELECT si.*,
                   s.name AS shelf_name,
                   MAX(0, COALESCE(si.quantity, 0) - COALESCE(si.used_quantity, 0) - COALESCE(si.borrowed_quantity, 0)) AS remaining_quantity
            FROM semifinished_inventory si
            LEFT JOIN shelves s ON s.id = si.shelf_id
            ORDER BY si.id DESC
            """
        )
        all_inventory = []
        for row in cursor.fetchall():
            item = dict(row)
            item["components"] = parse_json(item.pop("components_json", "[]"), [])
            all_inventory.append(item)
        cursor.execute("SELECT * FROM defective_semifinished_goods ORDER BY id DESC")
        defective_goods = []
        for row in cursor.fetchall():
            item = dict(row)
            item["abnormal_conditions"] = parse_json(item.pop("abnormal_json", "[]"), [])
            defective_goods.append(item)
        conn.close()
        return jsonify({"acceptances": acceptances, "inventory": all_inventory, "available_inventory": inventory, "defective_goods": defective_goods})


    @app.put("/api/production/semifinished-inventory/<int:inventory_id>")
    def update_semifinished_inventory_item(inventory_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_inventory_permission(cursor, "semifinished", "write")
            cursor.execute("SELECT * FROM semifinished_inventory WHERE id = ?", (inventory_id,))
            current = row_to_dict(cursor.fetchone())
            if not current:
                raise ValueError("semifinished inventory not found")
            quantity = quantity_value(data.get("quantity") if data.get("quantity") is not None else current.get("quantity"), "半成品库存数量")
            used_quantity = quantity_value(data.get("used_quantity") if data.get("used_quantity") is not None else current.get("used_quantity"), "半成品已用数量")
            if used_quantity > quantity:
                raise ValueError("used quantity cannot exceed total quantity")
            cost_price = price_value(data.get("cost_price") if data.get("cost_price") is not None else current.get("cost_price"), "半成品成本价")
            components_json = normalize_component_json(data.get("components"), current.get("components_json") or "[]")
            cursor.execute(
                """
                UPDATE semifinished_inventory
                SET name = ?, spec = ?, unit = ?, quantity = ?, used_quantity = ?,
                    cost_price = ?, components_json = ?, acceptance_date = ?, shelf_id = ?,
                    layer_number = ?, zone_name = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    str(data.get("name") if data.get("name") is not None else current.get("name") or "").strip(),
                    str(data.get("spec") if data.get("spec") is not None else current.get("spec") or "").strip(),
                    str(data.get("unit") if data.get("unit") is not None else current.get("unit") or "").strip(),
                    quantity,
                    used_quantity,
                    cost_price,
                    components_json,
                    data.get("acceptance_date") if data.get("acceptance_date") is not None else current.get("acceptance_date"),
                    int(data.get("shelf_id")) if str(data.get("shelf_id") or "").strip() else None,
                    int(data.get("layer_number")) if str(data.get("layer_number") or "").strip() else None,
                    str(data.get("zone_name") if data.get("zone_name") is not None else current.get("zone_name") or "").strip(),
                    now_text(),
                    inventory_id,
                ),
            )
            conn.commit()
        except PermissionError:
            conn.rollback()
            conn.close()
            raise
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})


    @app.delete("/api/production/semifinished-inventory/<int:inventory_id>")
    def delete_semifinished_inventory_item(inventory_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_inventory_permission(cursor, "semifinished", "delete")
            cursor.execute("SELECT * FROM semifinished_inventory WHERE id = ?", (inventory_id,))
            item = row_to_dict(cursor.fetchone())
            if not item:
                raise ValueError("semifinished inventory not found")
            if float(item.get("used_quantity") or 0) > 0:
                raise ValueError("semifinished inventory has been consumed by finished goods")
            recycle_table_row(cursor, "semifinished_inventory", "semifinished_inventory", inventory_id, ["serial_no", "name", "spec"], user.get("id"))
            cursor.execute("DELETE FROM semifinished_inventory WHERE id = ?", (inventory_id,))
            conn.commit()
        except PermissionError:
            conn.rollback()
            conn.close()
            raise
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})


    @app.put("/api/production/defective-semifinished/<int:defective_id>")
    def update_defective_semifinished_item(defective_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_inventory_permission(cursor, "semifinished", "write")
            cursor.execute("SELECT * FROM defective_semifinished_goods WHERE id = ?", (defective_id,))
            current = row_to_dict(cursor.fetchone())
            if not current:
                raise ValueError("defective semifinished good not found")
            abnormal = data.get("abnormal_conditions")
            if abnormal is None:
                abnormal_json = current.get("abnormal_json") or "[]"
            else:
                if isinstance(abnormal, str):
                    abnormal = [item.strip() for item in abnormal.replace("，", ",").replace("、", ",").split(",") if item.strip()]
                abnormal_json = json.dumps([str(item).strip() for item in abnormal if str(item).strip()], ensure_ascii=False)
            cursor.execute(
                """
                UPDATE defective_semifinished_goods
                SET name = ?, spec = ?, serial_no = ?, abnormal_json = ?
                WHERE id = ?
                """,
                (
                    str(data.get("name") if data.get("name") is not None else current.get("name") or "").strip(),
                    str(data.get("spec") if data.get("spec") is not None else current.get("spec") or "").strip(),
                    validate_serial_no(data.get("serial_no") if data.get("serial_no") is not None else current.get("serial_no")),
                    abnormal_json,
                    defective_id,
                ),
            )
            conn.commit()
        except PermissionError:
            conn.rollback()
            conn.close()
            raise
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})


    @app.delete("/api/production/defective-semifinished/<int:defective_id>")
    def delete_defective_semifinished_item(defective_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_inventory_permission(cursor, "semifinished", "delete")
            cursor.execute("SELECT id FROM defective_semifinished_goods WHERE id = ?", (defective_id,))
            if not cursor.fetchone():
                raise ValueError("defective semifinished good not found")
            recycle_table_row(cursor, "defective_semifinished", "defective_semifinished_goods", defective_id, ["serial_no", "name", "spec"], user.get("id"))
            cursor.execute("DELETE FROM defective_semifinished_goods WHERE id = ?", (defective_id,))
            conn.commit()
        except PermissionError:
            conn.rollback()
            conn.close()
            raise
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})


    @app.post("/api/production/semifinished-acceptance")
    def create_semifinished_acceptance():
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_permission(cursor, "start_acceptance")
            name = str(data.get("name") or "").strip()
            if not name:
                raise ValueError("请填写半成品名称")
            quality = production_quality_from_payload(data)
            acceptance_quantity = quality["acceptance_quantity"]
            components = prepare_material_component_consumptions(cursor, data.get("components") or [], acceptance_quantity)
            if not components:
                raise ValueError("请至少选择一种单台所用物料")
            total_cost = sum(float(item["amount"] or 0) for item in components)
            cost_price = total_cost / acceptance_quantity if acceptance_quantity > 0 else 0
            serial_items = normalize_production_serial_items(cursor, "semifinished", name, acceptance_quantity, data)
            if not (data.get("serial_items") or data.get("serials")):
                qualified_limit = int(round(float(quality["qualified_quantity"] or 0)))
                for index, serial_item in enumerate(serial_items):
                    serial_item["qualified"] = index < qualified_limit
                    serial_item["abnormal_conditions"] = [] if serial_item["qualified"] else ["不合格"]
            qualified_serials = [serial_item for serial_item in serial_items if serial_item.get("qualified")]
            unqualified_serials = [serial_item for serial_item in serial_items if not serial_item.get("qualified")]
            ensure_production_serials_available(cursor, "semifinished", [item["serial_no"] for item in serial_items])
            acceptance_no = next_table_no(cursor, "semifinished_acceptances", "acceptance_no", "BY")
            project_code = validate_project_code(data.get("project_code"))
            maker_id = optional_active_user_id(cursor, data.get("maker_id"), "制作者")
            cursor.execute(
                """
                INSERT INTO semifinished_acceptances
                    (acceptance_no, name, spec, acceptance_quantity, unit, acceptance_date,
                     qualified_quantity, unqualified_quantity, appearance_ok_quantity,
                     function_ok_quantity, performance_ok_quantity, cost_price, components_json,
                     serials_json, applicant_id, project_code, maker_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    acceptance_no,
                    name,
                    data.get("spec") or "",
                    acceptance_quantity,
                    data.get("unit") or "个",
                    data.get("acceptance_date") or today_text(),
                    quality["qualified_quantity"],
                    quality["unqualified_quantity"],
                    quality["appearance_ok_quantity"],
                    quality["function_ok_quantity"],
                    quality["performance_ok_quantity"],
                    cost_price,
                    json.dumps(components, ensure_ascii=False),
                    json.dumps(serial_items, ensure_ascii=False),
                    user["id"],
                    project_code,
                    maker_id,
                    now_text(),
                    now_text(),
                ),
            )
            acceptance_id = cursor.lastrowid
            insert_material_consumptions(cursor, "semifinished", acceptance_id, components)
            if quality["qualified_quantity"] > 0:
                for serial_item in qualified_serials:
                    cursor.execute(
                        """
                        INSERT INTO semifinished_inventory
                            (acceptance_id, name, spec, unit, quantity, used_quantity, cost_price,
                             components_json, serial_no, acceptance_date, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            acceptance_id,
                            name,
                            data.get("spec") or "",
                            data.get("unit") or "个",
                            1,
                            cost_price,
                            json.dumps(components, ensure_ascii=False),
                            serial_item["serial_no"],
                            data.get("acceptance_date") or today_text(),
                            now_text(),
                            now_text(),
                        ),
                    )
            defect_serials = [serial_item["serial_no"] for serial_item in unqualified_serials]
            for serial_item in unqualified_serials:
                cursor.execute(
                    """
                    INSERT INTO defective_semifinished_goods
                        (semifinished_acceptance_id, name, spec, serial_no, abnormal_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        acceptance_id,
                        name,
                        data.get("spec") or "",
                        serial_item["serial_no"],
                        json.dumps(serial_item.get("abnormal_conditions") or ["不合格"], ensure_ascii=False),
                        now_text(),
                    ),
                )
            conn.commit()
            response = {"id": acceptance_id, "acceptance_no": acceptance_no, "cost_price": cost_price, "defect_serials": defect_serials, **quality}
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "acceptance": response})


    @app.get("/api/production/finished")
    def production_finished_list():
        conn = get_db()
        cursor = conn.cursor()
        require_inventory_permission(cursor, "finished", "read")
        project_code = request.args.get("project_code", "").strip()
        params = []
        where_clause = ""
        if project_code:
            where_clause = " AND (fa.project_code LIKE ?)"
            params.append(f"%{project_code}%")
        cursor.execute(
            f"""
            SELECT fa.*, u.display_name AS applicant_name
            FROM finished_acceptances fa
            LEFT JOIN users u ON u.id = fa.applicant_id
            WHERE 1=1 {where_clause}
            ORDER BY fa.id DESC
            LIMIT 100
            """,
            params,
        )
        acceptances = []
        for row in cursor.fetchall():
            item = dict(row)
            item["material_components"] = parse_json(item.pop("material_components_json", "[]"), [])
            item["semifinished_components"] = parse_json(item.pop("semifinished_components_json", "[]"), [])
            acceptances.append(item)
        cursor.execute(
            """
            SELECT fgi.*, s.name AS shelf_name,
                   MAX(0, COALESCE(fgi.quantity, 0) - COALESCE(fgi.borrowed_quantity, 0)) AS remaining_quantity,
                   fa.material_components_json, fa.semifinished_components_json
            FROM finished_good_inventory fgi
            LEFT JOIN shelves s ON s.id = fgi.shelf_id
            LEFT JOIN finished_acceptances fa ON fa.id = fgi.acceptance_id
            ORDER BY fgi.id DESC
            """
        )
        qualified_inventory = []
        for row in cursor.fetchall():
            item = dict(row)
            item["material_components"] = parse_json(item.pop("material_components_json", "[]"), [])
            item["semifinished_components"] = parse_json(item.pop("semifinished_components_json", "[]"), [])
            qualified_inventory.append(item)
        cursor.execute("SELECT * FROM defective_finished_goods ORDER BY id DESC")
        defective_goods = []
        for row in cursor.fetchall():
            item = dict(row)
            item["abnormal_conditions"] = parse_json(item.pop("abnormal_json", "[]"), [])
            defective_goods.append(item)
        conn.close()
        return jsonify({"acceptances": acceptances, "qualified_inventory": qualified_inventory, "defective_goods": defective_goods})


    @app.get("/api/production/scrapped-semifinished")
    def scrapped_semifinished_list():
        conn = get_db()
        cursor = conn.cursor()
        require_inventory_permission(cursor, "semifinished", "read")
        cursor.execute(
            """
            SELECT ss.*, sa.acceptance_no, sa.acceptance_date, u.display_name AS applicant_name
            FROM scrapped_semifinished_goods ss
            LEFT JOIN semifinished_acceptances sa ON sa.id = ss.acceptance_id
            LEFT JOIN users u ON u.id = sa.applicant_id
            ORDER BY ss.id DESC
            """
        )
        items = []
        for row in cursor.fetchall():
            items.append(dict(row))
        conn.close()
        return jsonify({"items": items})


    @app.get("/api/production/scrapped-finished")
    def scrapped_finished_list():
        conn = get_db()
        cursor = conn.cursor()
        require_inventory_permission(cursor, "finished", "read")
        cursor.execute(
            """
            SELECT sf.*, fa.acceptance_no, fa.acceptance_date, u.display_name AS applicant_name
            FROM scrapped_finished_goods sf
            LEFT JOIN finished_acceptances fa ON fa.id = sf.acceptance_id
            LEFT JOIN users u ON u.id = fa.applicant_id
            ORDER BY sf.id DESC
            """
        )
        items = []
        for row in cursor.fetchall():
            items.append(dict(row))
        conn.close()
        return jsonify({"items": items})


    @app.put("/api/production/finished-inventory/<int:inventory_id>")
    def update_finished_inventory_item(inventory_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_inventory_permission(cursor, "finished", "write")
            cursor.execute("SELECT * FROM finished_good_inventory WHERE id = ?", (inventory_id,))
            current = row_to_dict(cursor.fetchone())
            if not current:
                raise ValueError("finished inventory not found")
            quantity = quantity_value(data.get("quantity") if data.get("quantity") is not None else current.get("quantity"), "成品库存数量")
            cost_price = price_value(data.get("cost_price") if data.get("cost_price") is not None else current.get("cost_price"), "成品成本价")
            cursor.execute(
                """
                UPDATE finished_good_inventory
                SET product_name = ?, spec = ?, unit = ?, quantity = ?, cost_price = ?,
                    acceptance_date = ?, shelf_id = ?, layer_number = ?, zone_name = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    str(data.get("product_name") if data.get("product_name") is not None else current.get("product_name") or "").strip(),
                    str(data.get("spec") if data.get("spec") is not None else current.get("spec") or "").strip(),
                    str(data.get("unit") if data.get("unit") is not None else current.get("unit") or "").strip(),
                    quantity,
                    cost_price,
                    data.get("acceptance_date") if data.get("acceptance_date") is not None else current.get("acceptance_date"),
                    int(data.get("shelf_id")) if str(data.get("shelf_id") or "").strip() else None,
                    int(data.get("layer_number")) if str(data.get("layer_number") or "").strip() else None,
                    str(data.get("zone_name") if data.get("zone_name") is not None else current.get("zone_name") or "").strip(),
                    now_text(),
                    inventory_id,
                ),
            )
            if current.get("acceptance_id") and ("material_components" in data or "semifinished_components" in data):
                cursor.execute(
                    "SELECT material_components_json, semifinished_components_json FROM finished_acceptances WHERE id = ?",
                    (current["acceptance_id"],),
                )
                acceptance = row_to_dict(cursor.fetchone()) or {}
                material_components_json = normalize_component_json(
                    data.get("material_components"),
                    acceptance.get("material_components_json") or "[]",
                )
                semifinished_components_json = normalize_component_json(
                    data.get("semifinished_components"),
                    acceptance.get("semifinished_components_json") or "[]",
                )
                cursor.execute(
                    """
                    UPDATE finished_acceptances
                    SET material_components_json = ?, semifinished_components_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (material_components_json, semifinished_components_json, now_text(), current["acceptance_id"]),
                )
            conn.commit()
        except PermissionError:
            conn.rollback()
            conn.close()
            raise
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})


    @app.delete("/api/production/finished-inventory/<int:inventory_id>")
    def delete_finished_inventory_item(inventory_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_inventory_permission(cursor, "finished", "delete")
            cursor.execute("SELECT id FROM finished_good_inventory WHERE id = ?", (inventory_id,))
            if not cursor.fetchone():
                raise ValueError("finished inventory not found")
            recycle_table_row(cursor, "finished_inventory", "finished_good_inventory", inventory_id, ["serial_no", "product_name", "spec"], user.get("id"))
            cursor.execute("DELETE FROM finished_good_inventory WHERE id = ?", (inventory_id,))
            conn.commit()
        except PermissionError:
            conn.rollback()
            conn.close()
            raise
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})


    @app.put("/api/production/defective-finished/<int:defective_id>")
    def update_defective_finished_item(defective_id):
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_inventory_permission(cursor, "finished", "write")
            cursor.execute("SELECT * FROM defective_finished_goods WHERE id = ?", (defective_id,))
            current = row_to_dict(cursor.fetchone())
            if not current:
                raise ValueError("defective finished good not found")
            abnormal = data.get("abnormal_conditions")
            if abnormal is None:
                abnormal_json = current.get("abnormal_json") or "[]"
            else:
                if isinstance(abnormal, str):
                    abnormal = [item.strip() for item in abnormal.replace("，", ",").split(",") if item.strip()]
                abnormal_json = json.dumps([str(item).strip() for item in abnormal if str(item).strip()], ensure_ascii=False)
            cursor.execute(
                """
                UPDATE defective_finished_goods
                SET product_name = ?, spec = ?, serial_no = ?, abnormal_json = ?
                WHERE id = ?
                """,
                (
                    str(data.get("product_name") if data.get("product_name") is not None else current.get("product_name") or "").strip(),
                    str(data.get("spec") if data.get("spec") is not None else current.get("spec") or "").strip(),
                    validate_serial_no(data.get("serial_no") if data.get("serial_no") is not None else current.get("serial_no")),
                    abnormal_json,
                    defective_id,
                ),
            )
            conn.commit()
        except PermissionError:
            conn.rollback()
            conn.close()
            raise
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})


    @app.delete("/api/production/defective-finished/<int:defective_id>")
    def delete_defective_finished_item(defective_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_inventory_permission(cursor, "finished", "delete")
            cursor.execute("SELECT id FROM defective_finished_goods WHERE id = ?", (defective_id,))
            if not cursor.fetchone():
                raise ValueError("defective finished good not found")
            recycle_table_row(cursor, "defective_finished", "defective_finished_goods", defective_id, ["serial_no", "product_name", "spec"], user.get("id"))
            cursor.execute("DELETE FROM defective_finished_goods WHERE id = ?", (defective_id,))
            conn.commit()
        except PermissionError:
            conn.rollback()
            conn.close()
            raise
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})


    @app.get("/api/production/<kind>/serials")
    def production_serials(kind):
        kind = require_production_kind(kind)
        product_name = request.args.get("product_name", "").strip() or request.args.get("name", "").strip()
        try:
            count = min(100, nonnegative_int_value(request.args.get("count", "0"), "生成数量", 0))
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        conn = get_db()
        cursor = conn.cursor()
        require_permission(cursor, "start_acceptance")
        serials = next_production_serials(cursor, kind, product_name or ("半成品" if kind == "semifinished" else "成品"), count)
        conn.close()
        return jsonify({"serials": serials})


    @app.get("/api/production/finished-serials")
    def production_finished_serials():
        product_name = request.args.get("product_name", "").strip()
        try:
            count = min(100, nonnegative_int_value(request.args.get("count", "0"), "生成数量", 0))
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        conn = get_db()
        cursor = conn.cursor()
        require_permission(cursor, "start_acceptance")
        serials = next_production_serials(cursor, "finished", product_name or "成品", count)
        conn.close()
        return jsonify({"serials": serials})


    @app.post("/api/production/finished-acceptance")
    def create_finished_acceptance():
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_permission(cursor, "start_acceptance")
            product_name = str(data.get("product_name") or "").strip()
            if not product_name:
                raise ValueError("请填写成品名称")
            quality = production_quality_from_payload(data)
            acceptance_quantity = quality["acceptance_quantity"]
            material_components = prepare_material_component_consumptions(cursor, data.get("material_components") or [], acceptance_quantity)
            semifinished_components = prepare_semifinished_component_consumptions(cursor, data.get("semifinished_components") or [], acceptance_quantity)
            if not material_components and not semifinished_components:
                raise ValueError("请至少选择所用物料或所用半成品")
            total_cost = sum(float(item["amount"] or 0) for item in material_components + semifinished_components)
            cost_price = total_cost / acceptance_quantity if acceptance_quantity > 0 else 0
            serial_items = normalize_production_serial_items(cursor, "finished", product_name, acceptance_quantity, data)
            has_serial_payload = bool(data.get("serial_items") or data.get("serials"))
            if has_serial_payload:
                qualified_count = sum(1 for serial_item in serial_items if serial_item.get("qualified"))
                unqualified_count = len(serial_items) - qualified_count
                quality.update(
                    {
                        "appearance_ok_quantity": qualified_count,
                        "function_ok_quantity": qualified_count,
                        "performance_ok_quantity": qualified_count,
                        "qualified_quantity": qualified_count,
                        "unqualified_quantity": unqualified_count,
                    }
                )
            else:
                unqualified_count = int(round(quality["unqualified_quantity"]))
                if abs(unqualified_count - quality["unqualified_quantity"]) > 1e-6:
                    raise ValueError("成品不合格数量必须是整数，才能逐个记录流水号")
                raw_defects = data.get("defects") or []
                if unqualified_count != len(raw_defects):
                    raise ValueError("不合格成品明细数量必须等于不合格数量")
                qualified_limit = int(round(float(quality["qualified_quantity"] or 0)))
                defect_index = 0
                for index, serial_item in enumerate(serial_items):
                    serial_item["qualified"] = index < qualified_limit
                    if serial_item["qualified"]:
                        serial_item["abnormal_conditions"] = []
                        continue
                    defect = raw_defects[defect_index]
                    defect_index += 1
                    abnormal = defect.get("abnormal_conditions") or defect.get("abnormal") or []
                    if isinstance(abnormal, str):
                        abnormal = [abnormal]
                    abnormal = [str(item) for item in abnormal if str(item).strip()]
                    if not abnormal:
                        raise ValueError(f"第 {defect_index} 个不合格品请至少选择一个异常情况")
                    serial_item["abnormal_conditions"] = abnormal
            qualified_serials = [serial_item for serial_item in serial_items if serial_item.get("qualified")]
            defects = [
                {
                    "serial_no": serial_item["serial_no"],
                    "abnormal_conditions": serial_item.get("abnormal_conditions") or ["不合格"],
                }
                for serial_item in serial_items
                if not serial_item.get("qualified")
            ]
            acceptance_no = next_table_no(cursor, "finished_acceptances", "acceptance_no", "CY")
            project_code = validate_project_code(data.get("project_code"))
            maker_id = optional_active_user_id(cursor, data.get("maker_id"), "制作者")
            cursor.execute(
                """
                INSERT INTO finished_acceptances
                    (acceptance_no, product_name, spec, acceptance_quantity, unit, acceptance_date,
                     qualified_quantity, unqualified_quantity, appearance_ok_quantity,
                     function_ok_quantity, performance_ok_quantity, cost_price,
                     material_components_json, semifinished_components_json, serials_json, applicant_id,
                     project_code, maker_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    acceptance_no,
                    product_name,
                    data.get("spec") or "",
                    acceptance_quantity,
                    data.get("unit") or "个",
                    data.get("acceptance_date") or today_text(),
                    quality["qualified_quantity"],
                    quality["unqualified_quantity"],
                    quality["appearance_ok_quantity"],
                    quality["function_ok_quantity"],
                    quality["performance_ok_quantity"],
                    cost_price,
                    json.dumps(material_components, ensure_ascii=False),
                    json.dumps(semifinished_components, ensure_ascii=False),
                    json.dumps(serial_items, ensure_ascii=False),
                    user["id"],
                    project_code,
                    maker_id,
                    now_text(),
                    now_text(),
                ),
            )
            acceptance_id = cursor.lastrowid
            insert_material_consumptions(cursor, "finished", acceptance_id, material_components)
            insert_semifinished_consumptions(cursor, acceptance_id, semifinished_components)
            if quality["qualified_quantity"] > 0:
                for serial_item in qualified_serials:
                    cursor.execute(
                        """
                        INSERT INTO finished_good_inventory
                            (acceptance_id, product_name, spec, unit, quantity, cost_price,
                             serial_no, acceptance_date, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            acceptance_id,
                            product_name,
                            data.get("spec") or "",
                            data.get("unit") or "个",
                            1,
                            cost_price,
                            serial_item["serial_no"],
                            data.get("acceptance_date") or today_text(),
                            now_text(),
                            now_text(),
                        ),
                    )
            defect_serials = [defect["serial_no"] for defect in defects]
            for defect in defects:
                cursor.execute(
                    """
                    INSERT INTO defective_finished_goods
                        (finished_acceptance_id, product_name, spec, serial_no, abnormal_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        acceptance_id,
                        product_name,
                        data.get("spec") or "",
                        defect["serial_no"],
                        json.dumps(defect["abnormal_conditions"], ensure_ascii=False),
                        now_text(),
                    ),
                )
            conn.commit()
            response = {"id": acceptance_id, "acceptance_no": acceptance_no, "cost_price": cost_price, "defect_serials": defect_serials, **quality}
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "acceptance": response})
