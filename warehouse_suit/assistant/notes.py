"""按用户保存事项，并在指定验收物料可以申领时提醒。"""

import json

from warehouse_suit.db import now_text
from warehouse_suit.notifications import create_notification
from warehouse_suit.settings import temporary_inventory_enabled
from warehouse_suit.stock_allocation_service import stock_source_quantities
from warehouse_suit.usage_kind import validate_usage_kind
from warehouse_suit.validation import quantity_value
from warehouse_suit.workflow_service import user_can_view_form, workflow_view_scope
from warehouse_suit.temporary_inventory_visibility import append_workflow_visibility


def list_notes(cursor, user):
    cursor.execute(
        """
        SELECT n.id, n.content, n.kind, n.status, n.quantity, n.recipient_user_id,
               n.acceptance_item_id, m.material_code, m.name AS material_name,
               f.form_no AS acceptance_no, n.created_at, n.notified_at
        FROM assistant_notes n
        LEFT JOIN materials m ON m.id = n.material_id
        LEFT JOIN workflow_items wi ON wi.id = n.acceptance_item_id
        LEFT JOIN workflow_forms f ON f.id = wi.form_id
        WHERE n.user_id = ? AND n.status <> 'done'
        ORDER BY n.id DESC
        """,
        (user["id"],),
    )
    return [dict(row) for row in cursor.fetchall()]


def find_people(cursor, query):
    text = str(query or "").strip()
    if not text:
        raise ValueError("请提供要查找的人员姓名")
    cursor.execute(
        """
        SELECT id, display_name, department FROM users
        WHERE is_active = 1 AND (display_name LIKE ? OR username = ?)
        ORDER BY display_name, id LIMIT 20
        """,
        (f"%{text}%", text),
    )
    return [dict(row) for row in cursor.fetchall()]


def find_acceptance_items(cursor, user, query):
    text = str(query or "").strip()
    if not text:
        raise ValueError("请提供物料名称、编码或验收单号")
    like = f"%{text}%"
    scope, scope_params = workflow_view_scope(cursor, user)
    where = [
        "f.form_type = 'acceptance'",
        "(wi.material_name LIKE ? OR wi.material_code LIKE ? OR wi.spec LIKE ? OR f.form_no LIKE ?)",
        scope,
    ]
    params = [like, like, like, like, *scope_params]
    append_workflow_visibility(cursor, where, params, "f")
    cursor.execute(
        f"""
        SELECT wi.id, wi.form_id, wi.material_id, wi.material_code, wi.material_name,
               wi.brand_model, wi.spec, wi.purchase_project, wi.data_json, f.form_no, f.status
        FROM workflow_items wi JOIN workflow_forms f ON f.id = wi.form_id
        WHERE {' AND '.join(where)}
        ORDER BY f.id DESC, wi.id LIMIT 20
        """,
        params,
    )
    results = []
    for row in cursor.fetchall():
        item = dict(row)
        data = json.loads(item.pop("data_json") or "{}")
        item["inbound_done"] = bool(data.get("inbound_done"))
        results.append(item)
    return results


def save_note(conn, user, payload, source_message_id=None):
    from warehouse_suit.inventory_service import begin_inventory_transaction
    begin_inventory_transaction(conn)
    cursor = conn.cursor()
    note_id = int(payload.get("note_id") or 0)
    existing = None
    if note_id:
        cursor.execute(
            "SELECT * FROM assistant_notes WHERE id = ? AND user_id = ? AND status IN ('incomplete', 'watching')",
            (note_id, user["id"]),
        )
        existing = cursor.fetchone()
        if not existing:
            raise ValueError("事项不存在，或提醒已发送、事项已完成")
        existing = dict(existing)
    content = str(payload.get("content") or (existing or {}).get("content") or "").strip()
    if not content or len(content) > 2000:
        raise ValueError("事项内容应为 1 到 2000 字")
    kind = str(payload.get("kind") or (existing or {}).get("kind") or "note")
    if kind not in {"note", "material_wait"}:
        raise ValueError("事项类型不正确")
    raw_quantity = payload.get("quantity", (existing or {}).get("quantity"))
    quantity = quantity_value(raw_quantity, "提前取用数量", positive=True) if raw_quantity not in (None, "") else None
    recipient = int(payload.get("recipient_user_id", (existing or {}).get("recipient_user_id")) or 0) or None
    if recipient:
        cursor.execute("SELECT id FROM users WHERE id = ? AND is_active = 1", (recipient,))
        if not cursor.fetchone():
            raise ValueError("接收人不存在或已停用，请重新确认")
    acceptance_item_id = int(payload.get("acceptance_item_id", (existing or {}).get("acceptance_item_id")) or 0) or None
    material_id = (existing or {}).get("material_id")
    code = str(payload.get("material_code") or "").strip()
    if code:
        cursor.execute("SELECT id FROM materials WHERE material_code = ?", (code,))
        material = cursor.fetchone()
        if not material:
            raise ValueError("物料编码不存在，请重新查询")
        material_id = int(material["id"])
    if acceptance_item_id:
        cursor.execute(
            """
            SELECT wi.material_id, wi.form_id, wi.material_code, f.form_type
            FROM workflow_items wi JOIN workflow_forms f ON f.id = wi.form_id
            WHERE wi.id = ?
            """,
            (acceptance_item_id,),
        )
        item = cursor.fetchone()
        if not item or item["form_type"] != "acceptance" or not user_can_view_form(cursor, user, item["form_id"]):
            raise ValueError("验收明细不存在或当前账号无权查看")
        if material_id and material_id != item["material_id"]:
            raise ValueError("物料编码与验收明细不一致")
        if code and code != item["material_code"]:
            raise ValueError("物料编码与验收明细不一致")
        if not item["material_id"]:
            raise ValueError("验收明细尚未关联物料，请先补充物料信息")
        material_id = int(item["material_id"])
    status = "recorded" if kind == "note" else ("watching" if acceptance_item_id and material_id and quantity else "incomplete")
    stamp = now_text()
    if existing:
        cursor.execute(
            """
            UPDATE assistant_notes
            SET content = ?, kind = ?, status = ?, material_id = ?, acceptance_item_id = ?,
                quantity = ?, recipient_user_id = ?, updated_at = ?
            WHERE id = ? AND user_id = ? AND status IN ('incomplete', 'watching')
            """,
            (content, kind, status, material_id, acceptance_item_id, quantity, recipient, stamp, note_id, user["id"]),
        )
        if not cursor.rowcount:
            raise ValueError("事项状态已变化，请刷新后重试")
        if status == "watching":
            evaluate_material_notes(cursor, material_id)
        conn.commit()
        return next(item for item in list_notes(conn.cursor(), user) if item["id"] == note_id)
    cursor.execute(
        """
        INSERT INTO assistant_notes
            (user_id, content, kind, status, material_id, acceptance_item_id, quantity,
             recipient_user_id, source_message_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, source_message_id, content) DO NOTHING
        """,
        (user["id"], content, kind, status, material_id, acceptance_item_id, quantity,
         recipient, source_message_id, stamp, stamp),
    )
    if cursor.rowcount:
        note_id = int(cursor.lastrowid)
    else:
        cursor.execute(
            "SELECT id FROM assistant_notes WHERE user_id = ? AND source_message_id = ? AND content = ?",
            (user["id"], source_message_id, content),
        )
        note_id = int(cursor.fetchone()["id"])
    if status == "watching":
        evaluate_material_notes(cursor, material_id)
    conn.commit()
    return next(item for item in list_notes(conn.cursor(), user) if item["id"] == note_id)


def complete_note(conn, user, note_id):
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE assistant_notes SET status = 'done', updated_at = ? WHERE id = ? AND user_id = ? AND status <> 'done'",
        (now_text(), int(note_id), user["id"]),
    )
    if cursor.rowcount != 1:
        raise ValueError("事项不存在或已完成")
    # 完成事项时只标记当前用户对应提醒已读。
    cursor.execute(
        """
        UPDATE notifications SET is_read = 1, read_at = ?
        WHERE user_id = ? AND category = 'assistant' AND is_read = 0
          AND json_valid(data_json)
          AND CAST(json_extract(data_json, '$.assistant_note_id') AS INTEGER) = ?
        """,
        (now_text(), user["id"], int(note_id)),
    )
    conn.commit()


def evaluate_material_notes(cursor, material_id):
    """与库存更新处于同一事务，提醒只生成一次。"""
    cursor.execute(
        """
        SELECT n.*, wi.data_json AS item_data, wi.purchase_project, wi.form_id,
               wi.stock_source, m.material_code, m.name, m.unit, f.form_no, f.status AS form_status
        FROM assistant_notes n
        JOIN workflow_items wi ON wi.id = n.acceptance_item_id
        JOIN workflow_forms f ON f.id = wi.form_id
        JOIN materials m ON m.id = n.material_id
        WHERE n.material_id = ? AND n.status = 'watching'
        """,
        (int(material_id),),
    )
    for row in cursor.fetchall():
        note = dict(row)
        data = json.loads(note["item_data"] or "{}")
        temporary_enabled = temporary_inventory_enabled(cursor)
        if (not data.get("inbound_done") or not note["purchase_project"]
                or note["form_status"] in {"rejected", "cancelled"}
                or (note["stock_source"] == "temporary" and not temporary_enabled)):
            continue
        available = stock_source_quantities(
            cursor, note["material_id"], temporary_enabled,
            purchase_project=note["purchase_project"],
            usage_kind=validate_usage_kind(data.get("usage_kind")),
        )
        # 申领规则不允许用正式库和临时库拼凑同一行。
        if max(available["formal"], available["temporary"]) + 1e-9 < float(note["quantity"]):
            continue
        stamp = now_text()
        cursor.execute(
            "UPDATE assistant_notes SET status = 'notified', notified_at = ?, updated_at = ? WHERE id = ? AND status = 'watching'",
            (stamp, stamp, note["id"]),
        )
        if not cursor.rowcount:
            continue
        body = f"{note['content']}。验收单 {note['form_no']} 的物料 {note['name']}（{note['material_code']}）已有可申领库存，请核对并办理。"
        recipients = {int(note["user_id"])}
        if note["recipient_user_id"]:
            recipients.add(int(note["recipient_user_id"]))
        for user_id in recipients:
            create_notification(
                cursor, user_id, "提前取用物料提醒", body,
                {"assistant_note_id": note["id"], "material_id": note["material_id"], "form_id": note["form_id"]},
                category="assistant",
            )
