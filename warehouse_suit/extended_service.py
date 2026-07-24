"""Business services for defective inventory, common materials, and supply."""

from __future__ import annotations

import json

from .db import now_text, row_to_dict, today_text
from .inventory_constants import STOCK_SOURCE_FORMAL
from .inventory_service import (
    add_inventory_batch,
    borrowable_item_snapshot,
    borrowable_items,
    consume_inventory_fifo,
    production_available_quantity,
)
from .material_repository import material_stock_total
from .numbering import next_form_no
from .notifications import create_notification
from .settings import get_setting, parse_json, workflow_settings
from .validation import quantity_value
from .workflow_service import (
    create_workflow_tasks,
    resolve_department_leader,
    require_task_assignee,
    serialize_form,
    workflow_assignees,
    workflow_generated_title,
)


def _j(value, default=None):
    return parse_json(value, default if default is not None else {})


def _json(value):
    return json.dumps(value or {}, ensure_ascii=False)


def _user_ids_for_role(cursor, role_code):
    cursor.execute(
        """
        SELECT DISTINCT u.id FROM users u
        JOIN user_roles ur ON ur.user_id = u.id
        JOIN roles r ON r.id = ur.role_id
        WHERE u.is_active = 1 AND r.code = ? ORDER BY u.id
        """,
        (role_code,),
    )
    return [int(row["id"]) for row in cursor.fetchall()]


def buyer_recipient_ids(cursor):
    configured = _j(get_setting(cursor, "common_material_buyer_ids", "[]"), [])
    ids = []
    for value in configured if isinstance(configured, list) else []:
        try:
            user_id = int(value)
        except (TypeError, ValueError):
            continue
        if user_id not in ids:
            ids.append(user_id)
    if ids:
        placeholders = ",".join("?" for _ in ids)
        cursor.execute(
            f"SELECT id FROM users WHERE is_active = 1 AND id IN ({placeholders}) ORDER BY id",
            ids,
        )
        return [int(row["id"]) for row in cursor.fetchall()]
    return _user_ids_for_role(cursor, "buyer")


def formal_available_quantity(cursor, material_id):
    return float(material_stock_total(cursor, int(material_id), stock_source=STOCK_SOURCE_FORMAL) or 0)


def supply_item_choices(cursor, keyword="", item_type=""):
    allowed = {"material", "semifinished", "finished"}
    selected_type = str(item_type or "").strip()
    if selected_type and selected_type not in allowed:
        raise ValueError("supply item type is invalid")
    rows = borrowable_items(cursor, keyword=str(keyword or "").strip(), include_temporary=False)
    return [row for row in rows if row.get("item_type") in (allowed if not selected_type else {selected_type})]

def evaluate_common_material_alerts(cursor, material_ids=None):
    params = []
    where = ['p.active = 1']
    if material_ids:
        ids = [int(value) for value in material_ids]
        where.append('p.material_id IN (' + ','.join('?' for _ in ids) + ')')
        params.extend(ids)
    query = 'SELECT p.*, m.material_code, m.name, m.brand_model, m.spec, m.unit FROM common_material_profiles p JOIN materials m ON m.id=p.material_id WHERE ' + ' AND '.join(where) + ' ORDER BY p.id'
    cursor.execute(query, params)
    buyers = buyer_recipient_ids(cursor)
    for row in cursor.fetchall():
        profile = dict(row)
        current = formal_available_quantity(cursor, profile['material_id'])
        threshold = float(profile.get('warning_quantity') or 0)
        below = current < threshold
        state = str(profile.get('alert_state') or 'normal')
        if below and state != 'low':
            recipients = list(dict.fromkeys([int(profile['owner_user_id']), *buyers]))
            body = '常用物料 {} {} 当前正式库可用 {}{}，低于预警数量 {}。'.format(profile.get('material_code') or '', profile.get('name') or '', current, profile.get('unit') or '', threshold)
            for user_id in recipients:
                create_notification(cursor, user_id, '常用物料低库存预警', body, {'material_id': int(profile['material_id']), 'quantity': current, 'warning_quantity': threshold})
            cursor.execute('UPDATE common_material_profiles SET alert_state=?, last_alerted_at=?, updated_at=? WHERE id=?', ('low', now_text(), now_text(), profile['id']))
        elif not below and state == 'low':
            cursor.execute('UPDATE common_material_profiles SET alert_state=?, updated_at=? WHERE id=?', ('normal', now_text(), profile['id']))


def list_common_materials(cursor):
    cursor.execute('SELECT p.*, m.material_code, m.name, m.brand_model, m.spec, m.unit, u.display_name AS owner_name FROM common_material_profiles p JOIN materials m ON m.id=p.material_id LEFT JOIN users u ON u.id=p.owner_user_id WHERE p.active=1 ORDER BY m.material_code')
    rows=[]
    for row in cursor.fetchall():
        item=dict(row)
        item['current_quantity']=formal_available_quantity(cursor,item['material_id'])
        item['below_warning']=item['current_quantity'] < float(item.get('warning_quantity') or 0)
        rows.append(item)
    return rows


def create_common_material_application(cursor, user, material_id, warning_quantity, reason, leader_id=None):
    material_id=int(material_id or 0)
    warning_quantity=quantity_value(warning_quantity,'预警数量',positive=True)
    reason=str(reason or '').strip()
    if not reason:
        raise ValueError('请填写申请理由')
    cursor.execute('SELECT * FROM materials WHERE id=?',(material_id,))
    material=row_to_dict(cursor.fetchone())
    if not material:
        raise ValueError('物料不存在')
    cursor.execute('SELECT id FROM common_material_profiles WHERE material_id=? AND active=1',(material_id,))
    if cursor.fetchone():
        raise ValueError('该物料已经是常用物料')
    quote=chr(39)
    cursor.execute('SELECT f.id FROM workflow_forms f JOIN workflow_items wi ON wi.form_id=f.id WHERE f.form_type=? AND f.status NOT IN ('+quote+'completed'+quote+','+quote+'rejected'+quote+','+quote+'cancelled'+quote+') AND wi.material_id=? LIMIT 1',('common_material',material_id))
    if cursor.fetchone():
        raise ValueError('该物料已有待审批的常用物料申请')
    leader_id=resolve_department_leader(cursor,user,'common_material','leader_common_material',leader_id)
    form_no=next_form_no(cursor,'CY')
    data={'material_id':material_id,'warning_quantity':warning_quantity,'reason':reason,'department':user.get('department') or ''}
    cursor.execute('INSERT INTO workflow_forms (form_no, form_type, title, status, current_step, applicant_id, leader_id, data_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',(form_no,'common_material',workflow_generated_title(user,form_no),'leader_common_material','leader_common_material',user['id'],leader_id,_json(data),now_text(),now_text()))
    form_id=int(cursor.lastrowid)
    cursor.execute('INSERT INTO workflow_items (form_id, material_id, material_code, material_name, brand_model, spec, unit, request_quantity, data_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',(form_id,material_id,material.get('material_code') or '',material.get('name') or '',material.get('brand_model') or '',material.get('spec') or '',material.get('unit') or '',warning_quantity,_json(data)))
    create_workflow_tasks(cursor,form_id,'common_material','leader_common_material',[leader_id])
    return serialize_form(cursor,form_id)


def approve_common_material_application(cursor, user, form_id, decision, remark=''):
    cursor.execute('SELECT * FROM workflow_forms WHERE id=? AND form_type=?',(form_id,'common_material'))
    form=row_to_dict(cursor.fetchone())
    if not form or form['status']!='leader_common_material':
        raise ValueError('常用物料申请不在待审批状态')
    task=require_task_assignee(cursor,user,form_id,'leader_common_material')
    decision=str(decision or '同意').strip()
    remark=str(remark or '').strip()
    if decision not in {'同意','不同意','拒绝'}:
        raise ValueError('审批结果不正确')
    if decision!='同意' and not remark:
        raise ValueError('拒绝时必须填写审批意见')
    data=_j(form.get('data_json'),{})
    if decision=='同意':
        cursor.execute('SELECT id FROM common_material_profiles WHERE material_id=?',(data['material_id'],))
        existing=cursor.fetchone()
        if existing:
            cursor.execute('UPDATE common_material_profiles SET warning_quantity=?, approved_form_id=?, active=1, updated_at=? WHERE id=?',(data['warning_quantity'],form_id,now_text(),existing['id']))
        else:
            cursor.execute('INSERT INTO common_material_profiles (material_id, warning_quantity, owner_user_id, approved_form_id, alert_state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)',(data['material_id'],data['warning_quantity'],form['applicant_id'],form_id,'normal',now_text(),now_text()))
        evaluate_common_material_alerts(cursor,[data['material_id']])
    cursor.execute('UPDATE workflow_tasks SET status=?, decision=?, signature=?, signed_at=?, data_json=?, updated_at=? WHERE id=?',('completed',decision,user.get('display_name') or '',today_text(),_json({'remark':remark}),now_text(),task['id']))
    cursor.execute('UPDATE workflow_forms SET status=?, current_step=?, updated_at=? WHERE id=?',('completed' if decision=='同意' else 'rejected','completed',now_text(),form_id))
    return serialize_form(cursor,form_id)


def update_common_material_threshold(cursor, user, material_id, warning_quantity):
    material_id=int(material_id or 0)
    warning_quantity=quantity_value(warning_quantity,'预警数量',positive=True)
    cursor.execute('SELECT * FROM common_material_profiles WHERE material_id=? AND active=1',(material_id,))
    profile=row_to_dict(cursor.fetchone())
    if not profile:
        raise ValueError('该物料尚未启用常用物料')
    if 'admin' not in set(user.get('role_codes') or []) and int(profile['owner_user_id'])!=int(user['id']):
        raise PermissionError('只有常用物料负责人或管理员可以调整预警数量')
    cursor.execute('UPDATE common_material_profiles SET warning_quantity=?, updated_at=? WHERE id=?',(warning_quantity,now_text(),profile['id']))
    evaluate_common_material_alerts(cursor,[material_id])
    return row_to_dict(cursor.execute('SELECT * FROM common_material_profiles WHERE id=?',(profile['id'],)).fetchone())


def _supply_snapshot(cursor, item_type, item_ref_id):
    if item_type not in {'material', 'semifinished', 'finished'}:
        raise ValueError('供货物品类型不正确')
    snap = borrowable_item_snapshot(cursor, item_type, item_ref_id, stock_source=STOCK_SOURCE_FORMAL)
    if float(snap.get('available_quantity') or 0) <= 0:
        raise ValueError('选择的物品没有可用库存')
    return snap


def _supply_item_rows(cursor, order_id):
    cursor.execute('SELECT * FROM supply_items WHERE order_id=? ORDER BY id', (order_id,))
    return [dict(row) for row in cursor.fetchall()]


def supply_outstanding(item):
    return max(0.0, float(item.get('shipped_quantity') or 0) - float(item.get('good_returned_quantity') or 0) - float(item.get('defective_returned_quantity') or 0) - float(item.get('no_return_quantity') or 0))


def supply_order_complete(cursor, order_id):
    items = _supply_item_rows(cursor, order_id)
    return bool(items) and all(supply_outstanding(item) <= 1e-9 for item in items)


def evaluate_supply_due_alerts(cursor, today=None):
    today = str(today or today_text())[:10]
    quote = chr(39)
    query = 'SELECT s.*, f.leader_id, f.form_no FROM supply_orders s JOIN workflow_forms f ON f.id=s.form_id WHERE s.status IN (' + quote + 'shipping' + quote + ',' + quote + 'external_open' + quote + ') AND TRIM(COALESCE(s.expected_close_date,' + quote + quote + '))<> ' + quote + quote + ' AND substr(s.expected_close_date,1,10)<=? AND COALESCE(s.due_alert_date,' + quote + quote + ')<>? ORDER BY s.id'
    cursor.execute(query, (today, today))
    rows = [dict(row) for row in cursor.fetchall()]
    for order in rows:
        recipients = []
        for value in (order.get('applicant_id'), order.get('leader_id')):
            if value and int(value) not in recipients:
                recipients.append(int(value))
        for user_id in recipients:
            body = '供货单 {} 已超过预计结清日期 {}，请跟进寄回或确认不回寄。'.format(order.get('form_no') or order.get('form_id'), str(order.get('expected_close_date') or '')[:10])
            create_notification(cursor, user_id, '供货结清逾期提醒', body, {'supply_form_id': int(order['form_id']), 'expected_close_date': order.get('expected_close_date')})
        cursor.execute('UPDATE supply_orders SET due_alert_date=?, updated_at=? WHERE id=?', (today, now_text(), order['id']))
    return len(rows)


def _supply_warehouse_user_id(cursor, form_id):
    cursor.execute('SELECT warehouse_user_id FROM workflow_forms WHERE id=?', (form_id,))
    row = cursor.fetchone()
    return int(row['warehouse_user_id'] or 0) if row and row['warehouse_user_id'] else 0


def create_supply_order(cursor, user, payload, leader_id=None):
    recipient = payload.get("recipient") or payload
    required = ["company", "name", "phone", "address"]
    values = {key: str(recipient.get(key) or "").strip() for key in required}
    missing = {"company": "公司", "name": "姓名", "phone": "电话", "address": "地址"}
    for key in required:
        if not values[key]:
            raise ValueError(f"请填写收货方{missing[key]}")
    expected_close_date = str(payload.get("expected_close_date") or "").strip()
    if not expected_close_date:
        raise ValueError("请填写预计结清日期")
    raw_items = payload.get("items") or []
    if not raw_items:
        raise ValueError("供货申请至少需要一项物品")
    leader_id = resolve_department_leader(cursor, user, "supply", "leader_supply", leader_id)
    form_no = next_form_no(cursor, "GH")
    form_data = {"recipient": values, "reason": str(payload.get("reason") or "").strip(), "expected_close_date": expected_close_date}
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, leader_id, data_json, created_at, updated_at)
        VALUES (?, 'supply', ?, 'leader_supply', 'leader_supply', ?, ?, ?, ?, ?)
        """,
        (form_no, workflow_generated_title(user, form_no), user["id"], leader_id, _json(form_data), now_text(), now_text()),
    )
    form_id = int(cursor.lastrowid)
    cursor.execute(
        """
        INSERT INTO supply_orders
            (form_id, applicant_id, recipient_company, recipient_name, recipient_phone, recipient_address, expected_close_date, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        """,
        (form_id, user["id"], values["company"], values["name"], values["phone"], values["address"], expected_close_date, now_text(), now_text()),
    )
    order_id = int(cursor.lastrowid)
    for raw in raw_items:
        item_type = str(raw.get("item_type") or "").strip()
        item_ref_id = int(raw.get("item_ref_id") or 0)
        quantity = quantity_value(raw.get("quantity"), "供货数量", positive=True)
        snap = _supply_snapshot(cursor, item_type, item_ref_id)
        if quantity > float(snap["available_quantity"]) + 1e-9:
            raise ValueError(f"{snap.get('item_name') or '物品'}库存不足")
        cursor.execute(
            """
            INSERT INTO workflow_items
                (form_id, material_id, material_code, material_name, brand_model, spec, unit, request_quantity, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (form_id, snap.get("material_id"), snap.get("item_code") or "", snap.get("item_name") or "",
             snap.get("brand_model") or "", snap.get("spec") or "", snap.get("unit") or "", quantity,
             _json({"item_type": item_type, "item_ref_id": item_ref_id, "quantity": quantity})),
        )
        workflow_item_id = int(cursor.lastrowid)
        cursor.execute(
            """
            INSERT INTO supply_items
                (order_id, workflow_item_id, item_type, item_ref_id, material_id, item_code, item_name, brand_model, spec, unit, approved_quantity, unit_price, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (order_id, workflow_item_id, item_type, item_ref_id, snap.get("material_id"), snap.get("item_code") or "",
             snap.get("item_name") or "", snap.get("brand_model") or "", snap.get("spec") or "", snap.get("unit") or "",
             quantity, float(snap.get("cost_price") or 0), _json({"item_type": item_type, "item_ref_id": item_ref_id})),
        )
    create_workflow_tasks(cursor, form_id, "supply", "leader_supply", [leader_id])
    return serialize_form(cursor, form_id)


def approve_supply(cursor, user, form_id, decision, warehouse_user_id=None, remark=""):
    cursor.execute("SELECT * FROM workflow_forms WHERE id = ? AND form_type = 'supply'", (form_id,))
    form = row_to_dict(cursor.fetchone())
    if not form or form["status"] != "leader_supply":
        raise ValueError("供货申请不在待审批状态")
    task = require_task_assignee(cursor, user, form_id, "leader_supply")
    decision = str(decision or "同意").strip()
    remark = str(remark or "").strip()
    if decision not in {"同意", "不同意", "拒绝"}:
        raise ValueError("审批结果不正确")
    if decision != "同意" and not remark:
        raise ValueError("拒绝时必须填写审批意见")
    if decision == "同意":
        warehouse_user_id = int(warehouse_user_id or 0)
        if not warehouse_user_id:
            ids = _user_ids_for_role(cursor, "warehouse")
            if not ids:
                raise ValueError("未配置有效仓库管理员")
            warehouse_user_id = ids[0]
        workflow_assignees(cursor, "supply", "supply_outbound", [warehouse_user_id])
        cursor.execute("UPDATE supply_orders SET status = 'shipping', updated_at = ? WHERE form_id = ?", (now_text(), form_id))
        cursor.execute("UPDATE workflow_forms SET status = 'supply_outbound', current_step = 'supply_outbound', warehouse_user_id = ?, updated_at = ? WHERE id = ?", (warehouse_user_id, now_text(), form_id))
        create_workflow_tasks(cursor, form_id, "supply", "supply_outbound", [warehouse_user_id])
    else:
        cursor.execute("UPDATE supply_orders SET status = 'rejected', updated_at = ? WHERE form_id = ?", (now_text(), form_id))
        cursor.execute("UPDATE workflow_forms SET status = 'rejected', current_step = 'completed', updated_at = ? WHERE id = ?", (now_text(), form_id))
    cursor.execute("UPDATE workflow_tasks SET status = 'completed', decision = ?, signature = ?, signed_at = ?, data_json = ?, updated_at = ? WHERE id = ?", (decision, user.get("display_name") or "", today_text(), _json({"remark": remark}), now_text(), task["id"]))
    return serialize_form(cursor, form_id)


def _decrement_production_inventory(cursor, item, quantity):
    table = "semifinished_inventory" if item["item_type"] == "semifinished" else "finished_good_inventory"
    cursor.execute(
        f"""UPDATE {table} SET quantity = quantity - ?, updated_at = ? WHERE id = ? AND quantity - COALESCE(borrowed_quantity, 0) >= ?""",
        (quantity, now_text(), item["item_ref_id"], quantity),
    )
    if cursor.rowcount != 1:
        raise ValueError("半成品或成品可用库存不足")


def _increment_production_inventory(cursor, item, quantity):
    table = "semifinished_inventory" if item["item_type"] == "semifinished" else "finished_good_inventory"
    cursor.execute(f"UPDATE {table} SET quantity = quantity + ?, updated_at = ? WHERE id = ?", (quantity, now_text(), item["item_ref_id"]))
    if cursor.rowcount != 1:
        raise ValueError("原半成品或成品库存记录不存在")


def ship_supply(cursor, user, form_id, payload):
    cursor.execute("SELECT * FROM workflow_forms WHERE id = ? AND form_type = 'supply'", (form_id,))
    form = row_to_dict(cursor.fetchone())
    if not form or form["status"] not in {"supply_outbound", "shipping", "external_open"}:
        raise ValueError("供货申请不在可出库状态")
    task = require_task_assignee(cursor, user, form_id, "supply_outbound")
    cursor.execute("SELECT * FROM supply_orders WHERE form_id = ?", (form_id,))
    order = row_to_dict(cursor.fetchone())
    if not order:
        raise ValueError("供货台账不存在")
    shipment_no = next_form_no(cursor, "JS")
    carrier = str(payload.get("carrier") or "").strip()
    tracking_no = str(payload.get("tracking_no") or "").strip()
    shipped_at = str(payload.get("shipped_at") or today_text()).strip()
    raw_items = payload.get("items") or []
    if not raw_items:
        raise ValueError("本次寄件至少需要一项物品")
    cursor.execute("INSERT INTO supply_shipments (order_id, shipment_no, carrier, tracking_no, shipped_at, operator_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (order["id"], shipment_no, carrier, tracking_no, shipped_at, user["id"], now_text()))
    shipment_id = int(cursor.lastrowid)
    supply_map = {int(row["id"]): row for row in _supply_item_rows(cursor, order["id"])}
    material_ids = set()
    for raw in raw_items:
        supply_item_id = int(raw.get("supply_item_id") or 0)
        item = supply_map.get(supply_item_id)
        if not item:
            raise ValueError("供货明细不存在")
        quantity = quantity_value(raw.get("quantity"), "寄件数量", positive=True)
        remaining_approved = float(item["approved_quantity"] or 0) - float(item["shipped_quantity"] or 0)
        if quantity > remaining_approved + 1e-9:
            raise ValueError("寄件数量超过批准数量")
        snap = _supply_snapshot(cursor, item["item_type"], item["item_ref_id"])
        if quantity > float(snap["available_quantity"] or 0) + 1e-9:
            raise ValueError("寄件时库存不足")
        operation_key = f"supply_out:{form_id}:{shipment_id}:{supply_item_id}"
        allocation = raw.get("allocations")
        if item["item_type"] == "material":
            consumed = consume_inventory_fifo(cursor, item["item_ref_id"], quantity, shipment_no, shipped_at, "供货寄件出库", allocation, STOCK_SOURCE_FORMAL, "supply_outbound", operation_key, operator_id=user["id"], workflow_item_id=item.get("workflow_item_id"))
            material_ids.add(int(item["item_ref_id"]))
            allocation = consumed
        else:
            _decrement_production_inventory(cursor, item, quantity)
        cursor.execute("INSERT INTO supply_shipment_items (shipment_id, supply_item_id, quantity, allocation_json, created_at) VALUES (?, ?, ?, ?, ?)", (shipment_id, supply_item_id, quantity, json.dumps(allocation or [], ensure_ascii=False), now_text()))
        cursor.execute("UPDATE supply_items SET shipped_quantity = shipped_quantity + ?, updated_at = ? WHERE id = ?", (quantity, now_text(), supply_item_id))
    evaluate_common_material_alerts(cursor, material_ids)
    finish = bool(payload.get("finish_shipping")) or _all_approved_shipped(cursor, order["id"])
    if finish:
        cursor.execute("UPDATE supply_orders SET status = 'external_open', updated_at = ? WHERE id = ?", (now_text(), order["id"]))
        cursor.execute("UPDATE workflow_tasks SET status = 'completed', decision = '已寄出', signature = ?, signed_at = ?, updated_at = ? WHERE id = ?", (user.get("display_name") or "", today_text(), now_text(), task["id"]))
        cursor.execute("UPDATE workflow_forms SET status = 'external_open', current_step = 'external_open', updated_at = ? WHERE id = ?", (now_text(), form_id))
    return serialize_form(cursor, form_id)


def finish_supply_shipping(cursor, user, form_id, payload):
    cursor.execute('SELECT * FROM workflow_forms WHERE id=? AND form_type=?', (form_id, 'supply'))
    form = row_to_dict(cursor.fetchone())
    if not form or form['status'] not in {'supply_outbound', 'shipping'}:
        raise ValueError('供货单不在可结束出库状态')
    task = require_task_assignee(cursor, user, form_id, 'supply_outbound')
    cursor.execute('SELECT * FROM supply_orders WHERE form_id=?', (form_id,))
    order = row_to_dict(cursor.fetchone())
    if not order:
        raise ValueError('供货台账不存在')
    cancel_remaining = bool(payload.get('cancel_remaining'))
    if not cancel_remaining and not _all_approved_shipped(cursor, order['id']):
        raise ValueError('仍有批准数量未寄出，请继续分批出库或确认取消余量')
    if cancel_remaining:
        cursor.execute('UPDATE supply_items SET approved_quantity=shipped_quantity, updated_at=? WHERE order_id=? AND shipped_quantity<approved_quantity', (now_text(), order['id']))
    cursor.execute('UPDATE supply_orders SET status=?, updated_at=? WHERE id=?', ('external_open', now_text(), order['id']))
    cursor.execute('UPDATE workflow_tasks SET status=?, decision=?, signature=?, signed_at=?, updated_at=? WHERE id=?', ('completed', '已结束出库', user.get('display_name') or '', today_text(), now_text(), task['id']))
    cursor.execute('UPDATE workflow_forms SET status=?, current_step=?, updated_at=? WHERE id=?', ('external_open', 'external_open', now_text(), form_id))
    return serialize_form(cursor, form_id)


def create_supply_extension(cursor, user, form_id, new_date, reason, leader_id=None):
    new_date = str(new_date or '').strip()
    reason = str(reason or '').strip()
    if not new_date or len(new_date) < 10:
        raise ValueError('请填写新的预计结清日期')
    if not reason:
        raise ValueError('请填写延期原因')
    cursor.execute('SELECT * FROM supply_orders WHERE form_id=?', (form_id,))
    order = row_to_dict(cursor.fetchone())
    if not order or order['status'] not in {'shipping', 'external_open'}:
        raise ValueError('当前供货单不能申请延期')
    warehouse_id = _supply_warehouse_user_id(cursor, form_id)
    roles = set(user.get('role_codes') or [])
    if 'admin' not in roles and int(order.get('applicant_id') or 0) != int(user['id']) and warehouse_id != int(user['id']):
        raise PermissionError('只有申请人或库房管理员可以申请延期')
    leader_id = resolve_department_leader(cursor, user, 'supply_extension', 'leader_supply_extension', leader_id)
    form_no = next_form_no(cursor, 'YQ')
    data = {'supply_form_id': form_id, 'old_date': order.get('expected_close_date') or '', 'new_date': new_date, 'reason': reason}
    cursor.execute('INSERT INTO workflow_forms (form_no, form_type, title, status, current_step, applicant_id, leader_id, data_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (form_no, 'supply_extension', '供货延期 ' + form_no, 'leader_supply_extension', 'leader_supply_extension', user['id'], leader_id, _json(data), now_text(), now_text()))
    extension_id = int(cursor.lastrowid)
    create_workflow_tasks(cursor, extension_id, 'supply_extension', 'leader_supply_extension', [leader_id])
    return serialize_form(cursor, extension_id)


def approve_supply_extension(cursor, user, extension_form_id, decision, remark=''):
    cursor.execute('SELECT * FROM workflow_forms WHERE id=? AND form_type=?', (extension_form_id, 'supply_extension'))
    form = row_to_dict(cursor.fetchone())
    if not form or form['status'] != 'leader_supply_extension':
        raise ValueError('延期申请不在待审批状态')
    task = require_task_assignee(cursor, user, extension_form_id, 'leader_supply_extension')
    decision = str(decision or '同意').strip()
    remark = str(remark or '').strip()
    if decision not in {'同意', '拒绝', '不同意'}:
        raise ValueError('审批结果不正确')
    if decision != '同意' and not remark:
        raise ValueError('拒绝时必须填写审批意见')
    data = _j(form.get('data_json'), {})
    if decision == '同意':
        cursor.execute('UPDATE supply_orders SET expected_close_date=?, due_alert_date=?, updated_at=? WHERE form_id=?', (data.get('new_date') or '', '', now_text(), data.get('supply_form_id')))
    cursor.execute('UPDATE workflow_tasks SET status=?, decision=?, signature=?, signed_at=?, data_json=?, updated_at=? WHERE id=?', ('completed', decision, user.get('display_name') or '', today_text(), _json({'remark': remark}), now_text(), task['id']))
    cursor.execute('UPDATE workflow_forms SET status=?, current_step=?, updated_at=? WHERE id=?', ('completed' if decision == '同意' else 'rejected', 'completed', now_text(), extension_form_id))
    return serialize_form(cursor, extension_form_id)


def reopen_supply(cursor, user, form_id, reason):
    reason = str(reason or '').strip()
    if not reason:
        raise ValueError('请填写意外回寄原因')
    cursor.execute('SELECT * FROM supply_orders WHERE form_id=?', (form_id,))
    order = row_to_dict(cursor.fetchone())
    if not order or order['status'] != 'completed':
        raise ValueError('只有已结清供货单可以重新打开')
    warehouse_id = _supply_warehouse_user_id(cursor, form_id)
    roles = set(user.get('role_codes') or [])
    if 'admin' not in roles and int(order.get('applicant_id') or 0) != int(user['id']) and warehouse_id != int(user['id']):
        raise PermissionError('只有申请人或库房管理员可以重新打开供货单')
    cursor.execute('UPDATE supply_orders SET status=?, closed_at=?, due_alert_date=?, updated_at=? WHERE id=?', ('external_open', '', '', now_text(), order['id']))
    cursor.execute('UPDATE workflow_forms SET status=?, current_step=?, updated_at=? WHERE id=?', ('external_open', 'external_open', now_text(), form_id))
    return serialize_form(cursor, form_id)


def _all_approved_shipped(cursor, order_id):
    cursor.execute("SELECT approved_quantity, shipped_quantity FROM supply_items WHERE order_id = ?", (order_id,))
    rows = cursor.fetchall()
    return bool(rows) and all(float(row["shipped_quantity"] or 0) >= float(row["approved_quantity"] or 0) - 1e-9 for row in rows)


def create_supply_return(cursor, user, form_id, payload):
    cursor.execute("SELECT * FROM supply_orders WHERE form_id = ?", (form_id,))
    order = row_to_dict(cursor.fetchone())
    if not order or order["status"] not in {"shipping", "external_open"}:
        raise ValueError("当前供货单不能登记回寄")
    warehouse_user_id = _supply_warehouse_user_id(cursor, form_id)
    role_codes = set(user.get('role_codes') or [])
    if 'admin' not in role_codes and int(order.get('applicant_id') or 0) != int(user['id']) and warehouse_user_id != int(user['id']):
        raise PermissionError('只有申请人或库房管理员可以登记回寄')
    raw_items = payload.get("items") or []
    if not raw_items:
        raise ValueError("回寄登记至少需要一项物品")
    form_no = next_form_no(cursor, "GH")
    cursor.execute("INSERT INTO workflow_forms (form_no, form_type, title, status, current_step, applicant_id, warehouse_user_id, data_json, created_at, updated_at) VALUES (?, 'supply_return', ?, 'supply_return_inbound', 'supply_return_inbound', ?, ?, ?, ?, ?)", (form_no, f"供货回寄 {form_no}", order["applicant_id"], _supply_warehouse_user_id(cursor, form_id), _json({"supply_form_id": form_id}), now_text(), now_text()))
    return_form_id = int(cursor.lastrowid)
    cursor.execute("INSERT INTO supply_return_records (order_id, form_id, initiated_by, logistics_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)", (order["id"], return_form_id, user["id"], _json(payload.get("logistics") or {}), now_text(), now_text()))
    return_id = int(cursor.lastrowid)
    supply_map = {int(row["id"]): row for row in _supply_item_rows(cursor, order["id"])}
    warehouse_id = int(_supply_warehouse_user_id(cursor, form_id) or 0)
    if not warehouse_id:
        ids = _user_ids_for_role(cursor, "warehouse")
        if not ids:
            raise ValueError("未配置有效仓库管理员")
        warehouse_id = ids[0]
        cursor.execute("UPDATE workflow_forms SET warehouse_user_id = ? WHERE id = ?", (warehouse_id, return_form_id))
    for raw in raw_items:
        supply_item_id = int(raw.get("supply_item_id") or 0)
        item = supply_map.get(supply_item_id)
        if not item:
            raise ValueError("供货明细不存在")
        expected = quantity_value(raw.get("expected_quantity"), "预计回寄数量", positive=True)
        if expected > supply_outstanding(item) + 1e-9:
            raise ValueError("预计回寄数量超过外部未结数量")
        cursor.execute("INSERT INTO supply_return_items (return_id, supply_item_id, expected_quantity, data_json) VALUES (?, ?, ?, ?)", (return_id, supply_item_id, expected, _json(raw)))
        cursor.execute("INSERT INTO workflow_items (form_id, material_id, material_code, material_name, brand_model, spec, unit, request_quantity, data_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (return_form_id, item.get("material_id"), item.get("item_code") or "", item.get("item_name") or "", item.get("brand_model") or "", item.get("spec") or "", item.get("unit") or "", expected, _json({"supply_return_id": return_id, "supply_item_id": supply_item_id})))
    create_workflow_tasks(cursor, return_form_id, "supply_return", "supply_return_inbound", [warehouse_id])
    return serialize_form(cursor, return_form_id)


def inbound_supply_return(cursor, user, return_form_id, payload):
    cursor.execute("SELECT * FROM supply_return_records WHERE form_id = ?", (return_form_id,))
    record = row_to_dict(cursor.fetchone())
    if not record or record["status"] != "pending":
        raise ValueError("回寄记录不在待验收状态")
    task = require_task_assignee(cursor, user, return_form_id, "supply_return_inbound")
    raw_items = payload.get("items") or []
    if not raw_items:
        raise ValueError("验收至少需要一项物品")
    cursor.execute("SELECT * FROM supply_orders WHERE id = ?", (record["order_id"],))
    order = row_to_dict(cursor.fetchone())
    supply_map = {int(row["id"]): row for row in _supply_item_rows(cursor, order["id"])}
    return_items = {int(row["supply_item_id"]): row for row in cursor.execute("SELECT * FROM supply_return_items WHERE return_id = ?", (record["id"],)).fetchall()}
    for raw in raw_items:
        supply_item_id = int(raw.get("supply_item_id") or 0)
        item = supply_map.get(supply_item_id)
        return_item = return_items.get(supply_item_id)
        if not item or not return_item:
            raise ValueError("回寄明细不存在")
        received = quantity_value(raw.get("received_quantity"), "实收数量", positive=True)
        good = quantity_value(raw.get("good_quantity"), "完好数量", 0)
        defective = quantity_value(raw.get("defective_quantity"), "不良数量", 0)
        if abs(good + defective - received) > 1e-9:
            raise ValueError("完好数量与不良数量合计必须等于实收数量")
        if received > float(return_item["expected_quantity"] or 0) + 1e-9 or received > supply_outstanding(item) + 1e-9:
            raise ValueError("实收数量超过预计回寄或外部未结数量")
        if good > 0:
            if item["item_type"] == "material":
                location = raw.get("location") or {}
                if not location.get("shelf_id"):
                    raise ValueError("物料完好回库必须选择货架")
                add_inventory_batch(cursor, item["material_id"], good, float(item.get("unit_price") or 0), {**location, "received_date": payload.get("received_at") or today_text(), "remark": f"供货回寄 {order.get('form_id') or ''}"}, f"GH-RETURN-{return_form_id}", STOCK_SOURCE_FORMAL, "supply_return_inbound", f"supply_return:{return_form_id}:{supply_item_id}:good", operator_id=user["id"])
            else:
                _increment_production_inventory(cursor, item, good)
        if defective > 0:
            create_defective_inventory(cursor, user, {"item_type": item["item_type"], "item_ref_id": item["item_ref_id"], "material_id": item.get("material_id"), "item_code": item.get("item_code"), "item_name": item.get("item_name"), "brand_model": item.get("brand_model"), "spec": item.get("spec"), "unit": item.get("unit"), "quantity": defective, "unit_price": item.get("unit_price"), "source_type": "supply_return", "source_ref_id": order["form_id"], "reason": str(raw.get("reason") or "供货回寄不良").strip()})
        cursor.execute("UPDATE supply_items SET good_returned_quantity = good_returned_quantity + ?, defective_returned_quantity = defective_returned_quantity + ?, updated_at = ? WHERE id = ?", (good, defective, now_text(), supply_item_id))
        cursor.execute("UPDATE supply_return_items SET received_quantity = ?, good_quantity = ?, defective_quantity = ?, data_json = ? WHERE id = ?", (received, good, defective, _json(raw), return_item["id"]))
    cursor.execute("UPDATE supply_return_records SET status = 'completed', received_at = ?, updated_at = ? WHERE id = ?", (payload.get("received_at") or today_text(), now_text(), record["id"]))
    cursor.execute("UPDATE workflow_tasks SET status = 'completed', decision = '已验收', signature = ?, signed_at = ?, updated_at = ? WHERE id = ?", (user.get("display_name") or "", today_text(), now_text(), task["id"]))
    cursor.execute("UPDATE workflow_forms SET status = 'completed', current_step = 'completed', updated_at = ? WHERE id = ?", (now_text(), return_form_id))
    new_status = "completed" if supply_order_complete(cursor, order["id"]) else "external_open"
    cursor.execute("UPDATE supply_orders SET status = ?, closed_at = CASE WHEN ? = 'completed' THEN ? ELSE closed_at END, updated_at = ? WHERE id = ?", (new_status, new_status, now_text(), now_text(), order["id"]))
    return serialize_form(cursor, return_form_id)


def create_defective_inventory(cursor, user, payload):
    item_type = str(payload.get("item_type") or "material").strip()
    if item_type not in {"material", "semifinished", "finished"}:
        raise ValueError("不良品类型不正确")
    quantity = quantity_value(payload.get("quantity"), "不良品数量", positive=True)
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise ValueError("请填写不良原因")
    item_ref_id = int(payload.get("item_ref_id") or 0)
    cursor.execute("INSERT INTO defective_inventory (item_type, material_id, original_inventory_id, source_batch_id, source_type, source_ref_id, item_code, item_name, brand_model, spec, unit, quantity, remaining_quantity, unit_price, reason, status, stock_source, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)", (item_type, payload.get("material_id"), payload.get("original_inventory_id") or item_ref_id, payload.get("source_batch_id"), payload.get("source_type") or "manual", payload.get("source_ref_id"), payload.get("item_code") or "", payload.get("item_name") or "", payload.get("brand_model") or "", payload.get("spec") or "", payload.get("unit") or "", quantity, quantity, float(payload.get("unit_price") or 0), reason, payload.get("stock_source") or STOCK_SOURCE_FORMAL, user["id"], now_text(), now_text()))
    defective_id = int(cursor.lastrowid)
    return defective_id


def transfer_material_to_defective(cursor, user, payload):
    material_id = int(payload.get("material_id") or 0)
    quantity = quantity_value(payload.get("quantity"), "转移数量", positive=True)
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise ValueError("请填写不良原因")
    cursor.execute("SELECT * FROM materials WHERE id = ?", (material_id,))
    material = row_to_dict(cursor.fetchone())
    if not material:
        raise ValueError("物料不存在")
    consumed = consume_inventory_fifo(cursor, material_id, quantity, next_form_no(cursor, "BL"), today_text(), "物料转入不良品", payload.get("allocations"), STOCK_SOURCE_FORMAL, "defective_transfer", f"defective_transfer:{material_id}:{payload.get('operation_key') or now_text()}", operator_id=user["id"])
    ids = []
    for row in consumed:
        ids.append(create_defective_inventory(cursor, user, {"item_type": "material", "material_id": material_id, "source_batch_id": row.get("batch_id"), "source_type": "manual_transfer", "source_ref_id": material_id, "item_code": material.get("material_code"), "item_name": material.get("name"), "brand_model": material.get("brand_model"), "spec": material.get("spec"), "unit": material.get("unit"), "quantity": row.get("quantity"), "unit_price": row.get("unit_price"), "reason": reason}))
    evaluate_common_material_alerts(cursor, [material_id])
    return ids


def dispose_defective_inventory(cursor, user, defective_id, payload):
    cursor.execute("SELECT * FROM defective_inventory WHERE id = ?", (defective_id,))
    item = row_to_dict(cursor.fetchone())
    if not item:
        raise ValueError("不良品记录不存在")
    quantity = quantity_value(payload.get("quantity") if payload.get("quantity") is not None else item.get("remaining_quantity"), "处置数量", positive=True)
    if quantity > float(item.get("remaining_quantity") or 0) + 1e-9:
        raise ValueError("处置数量超过不良品剩余数量")
    action = str(payload.get("action") or "").strip()
    if action not in {"repair", "scrap", "source_return"}:
        raise ValueError("不良品处置类型不正确")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise ValueError("请填写处置原因")
    if action == "repair":
        if item["item_type"] == "material":
            location = payload.get("location") or {}
            if not location.get("shelf_id"):
                raise ValueError("修复回库必须选择货架")
            add_inventory_batch(cursor, item["material_id"], quantity, float(item.get("unit_price") or 0), {**location, "remark": "不良品修复回库"}, f"BL-REPAIR-{defective_id}", item.get("stock_source") or STOCK_SOURCE_FORMAL, "defective_repair_inbound", f"defective_repair:{defective_id}:{quantity}", operator_id=user["id"])
        else:
            _increment_production_inventory(cursor, item, quantity)
    cursor.execute("INSERT INTO defective_inventory_events (defective_id, action, quantity, reason, target_ref_id, operator_id, operation_key, data_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (defective_id, action, quantity, reason, item.get("original_inventory_id"), user["id"], f"defective:{defective_id}:{action}:{now_text()}", _json(payload), now_text()))
    remaining = float(item.get("remaining_quantity") or 0) - quantity
    status = "closed" if remaining <= 1e-9 else "partially_disposed"
    cursor.execute("UPDATE defective_inventory SET remaining_quantity = ?, status = ?, updated_at = ? WHERE id = ?", (max(0, remaining), status, now_text(), defective_id))
    return row_to_dict(cursor.execute("SELECT * FROM defective_inventory WHERE id = ?", (defective_id,)).fetchone())


def list_defective_inventory(cursor):
    cursor.execute("SELECT d.*, u.display_name AS creator_name FROM defective_inventory d LEFT JOIN users u ON u.id = d.created_by ORDER BY d.id DESC")
    rows = [dict(row) for row in cursor.fetchall()]
    cursor.execute("SELECT * FROM defective_semifinished_goods ORDER BY id DESC")
    rows.extend({"id": f"semifinished:{row['id']}", "item_type": "semifinished", "item_code": row.get("serial_no") or "", "item_name": row.get("name") or "", "spec": row.get("spec") or "", "unit": row.get("unit") or "个", "quantity": row.get("quantity") or 1, "remaining_quantity": row.get("quantity") or 1, "status": row.get("status") or "pending", "source_type": row.get("source_type") or "production_acceptance", "reason": "生产验收不合格"} for row in [dict(item) for item in cursor.fetchall()])
    cursor.execute("SELECT * FROM defective_finished_goods ORDER BY id DESC")
    rows.extend({"id": f"finished:{row['id']}", "item_type": "finished", "item_code": row.get("serial_no") or "", "item_name": row.get("product_name") or "", "spec": row.get("spec") or "", "unit": row.get("unit") or "台", "quantity": row.get("quantity") or 1, "remaining_quantity": row.get("quantity") or 1, "status": row.get("status") or "pending", "source_type": row.get("source_type") or "production_acceptance", "reason": "生产验收不合格"} for row in [dict(item) for item in cursor.fetchall()])
    return rows
