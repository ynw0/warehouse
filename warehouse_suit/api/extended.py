"""Routes for defective inventory, common materials, and supply."""

from __future__ import annotations

import json

from flask import jsonify, request

from warehouse_suit.db import now_text
from warehouse_suit.extended_service import (
    approve_common_material_application,
    approve_supply,
    create_common_material_application,
    update_common_material_threshold,
    create_supply_order,
    create_supply_return,
    create_supply_extension,
    approve_supply_extension,
    evaluate_supply_due_alerts,
    finish_supply_shipping,
    reopen_supply,
    dispose_defective_inventory,
    evaluate_common_material_alerts,
    inbound_supply_return,
    list_common_materials,
    list_defective_inventory,
    ship_supply,
    supply_item_choices,
    transfer_material_to_defective,
)
from warehouse_suit.settings import get_setting, set_setting, parse_json
from warehouse_suit.validation import positive_int_value, quantity_value
from warehouse_suit.workflow_service import require_permission


def register_extended_routes(app, *, get_db, current_user_provider, notify_todos_changed):
    current_user = current_user_provider

    @app.get("/api/defective-inventory")
    def defective_inventory_list():
        conn = get_db()
        cursor = conn.cursor()
        require_permission(cursor, "view_defective_inventory")
        rows = list_defective_inventory(cursor)
        conn.close()
        return jsonify({"items": rows})

    @app.post("/api/defective-inventory/transfers")
    def defective_inventory_transfer():
        data = request.get_json(force=True) or {}
        conn = get_db()
        try:
            cursor = conn.cursor()
            user = require_permission(cursor, "manage_defective_inventory")
            ids = transfer_material_to_defective(cursor, user, data)
            conn.commit()
            notify_todos_changed()
            return jsonify({"success": True, "ids": ids})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/defective-inventory/<int:defective_id>/dispositions")
    def defective_inventory_disposition(defective_id):
        data = request.get_json(force=True) or {}
        conn = get_db()
        try:
            cursor = conn.cursor()
            user = require_permission(cursor, "manage_defective_inventory")
            row = dispose_defective_inventory(cursor, user, defective_id, data)
            conn.commit()
            notify_todos_changed()
            return jsonify({"success": True, "item": row})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.get("/api/common-materials")
    def common_material_list():
        conn = get_db()
        cursor = conn.cursor()
        require_permission(cursor, "view_query")
        evaluate_common_material_alerts(cursor)
        conn.commit()
        rows = list_common_materials(cursor)
        conn.close()
        return jsonify({"items": rows})

    @app.post("/api/common-material-applications")
    def common_material_application_create():
        data = request.get_json(force=True) or {}
        conn = get_db()
        try:
            cursor = conn.cursor()
            user = require_permission(cursor, "start_common_material")
            form = create_common_material_application(cursor, user, data.get("material_id"), data.get("warning_quantity"), data.get("reason"), data.get("leader_id"))
            conn.commit()
            notify_todos_changed()
            return jsonify({"success": True, "form": form})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.put('/api/common-materials/<int:material_id>/threshold')
    def common_material_threshold(material_id):
        data = request.get_json(force=True) or {}
        conn = get_db()
        try:
            cursor = conn.cursor()
            user = current_user(cursor)
            profile = update_common_material_threshold(cursor, user, material_id, data.get('warning_quantity'))
            conn.commit()
            notify_todos_changed()
            return jsonify({'success': True, 'profile': profile})
        except Exception as exc:
            conn.rollback()
            return jsonify({'success': False, 'error': str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/common-material-applications/<int:form_id>/leader")
    def common_material_application_leader(form_id):
        data = request.get_json(force=True) or {}
        conn = get_db()
        try:
            cursor = conn.cursor()
            user = current_user(cursor)
            form = approve_common_material_application(cursor, user, form_id, data.get("decision"), data.get("remark"))
            conn.commit()
            notify_todos_changed()
            return jsonify({"success": True, "form": form})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.get("/api/supplies")
    def supply_list():
        conn = get_db()
        cursor = conn.cursor()
        require_permission(cursor, "view_supply")
        evaluate_supply_due_alerts(cursor)
        conn.commit()
        cursor.execute(
            """
            SELECT s.*, f.form_no, f.title, f.status AS form_status,
                   u.display_name AS applicant_name
            FROM supply_orders s
            JOIN workflow_forms f ON f.id = s.form_id
            LEFT JOIN users u ON u.id = s.applicant_id
            ORDER BY s.id DESC
            """
        )
        rows = [dict(row) for row in cursor.fetchall()]
        for row in rows:
            cursor.execute("SELECT * FROM supply_items WHERE order_id = ? ORDER BY id", (row["id"],))
            row["items"] = [dict(item) for item in cursor.fetchall()]
            for item in row["items"]:
                item["outstanding_quantity"] = max(0, float(item.get("shipped_quantity") or 0) - float(item.get("good_returned_quantity") or 0) - float(item.get("defective_returned_quantity") or 0) - float(item.get("no_return_quantity") or 0))
        conn.close()
        return jsonify({"items": rows})

    @app.get("/api/supplies/item-choices")
    def supply_item_choices_list():
        conn = get_db()
        try:
            cursor = conn.cursor()
            require_permission(cursor, "start_supply")
            rows = supply_item_choices(cursor, request.args.get("q", ""), request.args.get("item_type", ""))
            return jsonify({"items": rows})
        finally:
            conn.close()


    @app.post("/api/supplies")
    def supply_create():
        data = request.get_json(force=True) or {}
        conn = get_db()
        try:
            cursor = conn.cursor()
            user = require_permission(cursor, "start_supply")
            form = create_supply_order(cursor, user, data, data.get("leader_id"))
            conn.commit()
            notify_todos_changed()
            return jsonify({"success": True, "form": form})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/supplies/<int:form_id>/leader")
    def supply_leader(form_id):
        data = request.get_json(force=True) or {}
        conn = get_db()
        try:
            cursor = conn.cursor()
            user = current_user(cursor)
            form = approve_supply(cursor, user, form_id, data.get("decision"), data.get("warehouse_user_id"), data.get("remark"))
            conn.commit()
            notify_todos_changed()
            return jsonify({"success": True, "form": form})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/supplies/<int:form_id>/shipments")
    def supply_shipment(form_id):
        data = request.get_json(force=True) or {}
        conn = get_db()
        try:
            cursor = conn.cursor()
            user = require_permission(cursor, "edit_supply")
            form = ship_supply(cursor, user, form_id, data)
            conn.commit()
            notify_todos_changed()
            return jsonify({"success": True, "form": form})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post('/api/supplies/<int:form_id>/finish-shipping')
    def supply_finish_shipping(form_id):
        data = request.get_json(force=True) or {}
        conn = get_db()
        try:
            cursor = conn.cursor()
            user = require_permission(cursor, 'edit_supply')
            form = finish_supply_shipping(cursor, user, form_id, data)
            conn.commit()
            notify_todos_changed()
            return jsonify({'success': True, 'form': form})
        except Exception as exc:
            conn.rollback()
            return jsonify({'success': False, 'error': str(exc)}), 400
        finally:
            conn.close()

    @app.post('/api/supplies/<int:form_id>/extensions')
    def supply_extension_create(form_id):
        data = request.get_json(force=True) or {}
        conn = get_db()
        try:
            cursor = conn.cursor()
            user = require_permission(cursor, 'view_supply')
            form = create_supply_extension(cursor, user, form_id, data.get('new_date'), data.get('reason'), data.get('leader_id'))
            conn.commit()
            notify_todos_changed()
            return jsonify({'success': True, 'form': form})
        except Exception as exc:
            conn.rollback()
            return jsonify({'success': False, 'error': str(exc)}), 400
        finally:
            conn.close()

    @app.post('/api/supply-extensions/<int:extension_form_id>/leader')
    def supply_extension_leader(extension_form_id):
        data = request.get_json(force=True) or {}
        conn = get_db()
        try:
            cursor = conn.cursor()
            user = current_user(cursor)
            form = approve_supply_extension(cursor, user, extension_form_id, data.get('decision'), data.get('remark'))
            conn.commit()
            notify_todos_changed()
            return jsonify({'success': True, 'form': form})
        except Exception as exc:
            conn.rollback()
            return jsonify({'success': False, 'error': str(exc)}), 400
        finally:
            conn.close()

    @app.post('/api/supplies/<int:form_id>/reopen')
    def supply_reopen(form_id):
        data = request.get_json(force=True) or {}
        conn = get_db()
        try:
            cursor = conn.cursor()
            user = current_user(cursor)
            form = reopen_supply(cursor, user, form_id, data.get('reason'))
            conn.commit()
            notify_todos_changed()
            return jsonify({'success': True, 'form': form})
        except Exception as exc:
            conn.rollback()
            return jsonify({'success': False, 'error': str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/supplies/<int:form_id>/returns")
    def supply_return_create(form_id):
        data = request.get_json(force=True) or {}
        conn = get_db()
        try:
            cursor = conn.cursor()
            user = require_permission(cursor, "view_supply")
            form = create_supply_return(cursor, user, form_id, data)
            conn.commit()
            notify_todos_changed()
            return jsonify({"success": True, "form": form})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/supply-returns/<int:return_form_id>/inbound")
    def supply_return_inbound(return_form_id):
        data = request.get_json(force=True) or {}
        conn = get_db()
        try:
            cursor = conn.cursor()
            user = require_permission(cursor, "edit_supply")
            form = inbound_supply_return(cursor, user, return_form_id, data)
            conn.commit()
            notify_todos_changed()
            return jsonify({"success": True, "form": form})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/supplies/<int:form_id>/no-return-settlements")
    def supply_no_return(form_id):
        data = request.get_json(force=True) or {}
        conn = get_db()
        try:
            cursor = conn.cursor()
            user = current_user(cursor)
            cursor.execute("SELECT * FROM supply_orders WHERE form_id = ?", (form_id,))
            order = cursor.fetchone()
            if not order:
                raise ValueError("供货台账不存在")
            if "admin" not in user.get("role_codes", []) and int(order["applicant_id"] or 0) != int(user["id"]):
                raise PermissionError("只有申请人可以确认不回寄")
            items = data.get("items") or []
            reason = str(data.get("reason") or "").strip()
            if not reason:
                raise ValueError("请填写不回寄原因")
            for raw in items:
                supply_item_id = int(raw.get("supply_item_id") or 0)
                quantity = quantity_value(raw.get("quantity"), "不回寄数量", positive=True)
                cursor.execute("SELECT * FROM supply_items WHERE id = ? AND order_id = ?", (supply_item_id, order["id"]))
                item = cursor.fetchone()
                if not item:
                    raise ValueError("供货明细不存在")
                outstanding = max(0, float(item["shipped_quantity"] or 0) - float(item["good_returned_quantity"] or 0) - float(item["defective_returned_quantity"] or 0) - float(item["no_return_quantity"] or 0))
                if quantity > outstanding + 1e-9:
                    raise ValueError("不回寄数量超过外部未结数量")
                cursor.execute("UPDATE supply_items SET no_return_quantity = no_return_quantity + ?, updated_at = ? WHERE id = ?", (quantity, now_text(), supply_item_id))
                cursor.execute("INSERT INTO supply_no_return_events (order_id, supply_item_id, quantity, reason, operator_id, created_at) VALUES (?, ?, ?, ?, ?, ?)", (order["id"], supply_item_id, quantity, reason, user["id"], now_text()))
            cursor.execute("SELECT COUNT(*) AS count FROM supply_items WHERE order_id = ? AND shipped_quantity - good_returned_quantity - defective_returned_quantity - no_return_quantity > 0.000001", (order["id"],))
            if cursor.fetchone()["count"] == 0:
                cursor.execute("UPDATE supply_orders SET status = 'completed', closed_at = ?, updated_at = ? WHERE id = ?", (now_text(), now_text(), order["id"]))
                cursor.execute("UPDATE workflow_forms SET status = 'completed', current_step = 'completed', updated_at = ? WHERE id = ?", (now_text(), form_id))
            conn.commit()
            notify_todos_changed()
            return jsonify({"success": True})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.get("/api/common-material-settings")
    def common_material_settings_get():
        conn = get_db()
        cursor = conn.cursor()
        require_permission(cursor, "view_query")
        ids = parse_json(get_setting(cursor, "common_material_buyer_ids", "[]"), [])
        conn.close()
        return jsonify({"buyer_ids": ids if isinstance(ids, list) else []})

    @app.put("/api/common-material-settings")
    def common_material_settings_put():
        data = request.get_json(force=True) or {}
        conn = get_db()
        try:
            cursor = conn.cursor()
            require_permission(cursor, "edit_department")
            ids = []
            for value in data.get("buyer_ids") or []:
                ids.append(positive_int_value(value, "采购接收人"))
            if not ids:
                raise ValueError("至少配置一名采购接收人")
            set_setting(cursor, "common_material_buyer_ids", json.dumps(sorted(set(ids))))
            conn.commit()
            return jsonify({"success": True, "buyer_ids": sorted(set(ids))})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()
