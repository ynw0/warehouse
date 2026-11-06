"""按用户保存事项，并在指定验收物料可以申领时提醒。"""

import json
from datetime import datetime, timedelta

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
               n.acceptance_item_id, n.reminder_mode, n.remind_at, m.material_code, m.name AS material_name,
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
    existing = {}
    if note_id:
        cursor.execute(
            "SELECT * FROM assistant_notes WHERE id = ? AND user_id = ? AND status IN ('recorded','incomplete','watching')",
            (note_id, user["id"]),
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError("事项不存在，或提醒已发送、事项已完成")
        existing = dict(row)
    data = {**existing, **payload}
    content = str(data.get("content") or "").strip()
    if not content or len(content) > 2000:
        raise ValueError("事项内容应为 1 到 2000 字")
    kind = str(data.get("kind") or "note")
    if kind not in {"note", "material_wait"}:
        raise ValueError("事项类型不正确")
    mode = str(payload.get("reminder_mode") or existing.get("reminder_mode") or ("material" if kind == "material_wait" else "none"))
    if mode not in {"none","immediate","at_time","material"}:
        raise ValueError("提醒类型不正确")
    if (kind == "material_wait") != (mode == "material"):
        raise ValueError("物料到货提醒需要 material_wait 类型")
    remind_at = None
    if mode == "at_time":
        if payload.get("delay_seconds") is not None:
            delay = quantity_value(payload["delay_seconds"], "延迟秒数", positive=True)
            if delay > 31536000:
                raise ValueError("提醒时间不能超过一年")
            remind_at = (datetime.now() + timedelta(seconds=delay)).strftime("%Y-%m-%d %H:%M:%S")
        elif payload.get("remind_at"):
            try:
                remind_at = datetime.strptime(str(payload["remind_at"]), "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                raise ValueError("提醒时间格式应为 YYYY-MM-DD HH:MM:SS")
            if remind_at <= now_text():
                raise ValueError("提醒时间必须晚于当前时间")
        else:
            remind_at = existing.get("remind_at")
            if not remind_at:
                raise ValueError("请提供延迟秒数或提醒时间")
    quantity = quantity_value(data["quantity"], "提前取用数量", positive=True) if data.get("quantity") not in (None, "") else None
    recipient = int(data.get("recipient_user_id") or 0) or None
    if recipient:
        cursor.execute("SELECT id FROM users WHERE id = ? AND is_active = 1", (recipient,))
        if not cursor.fetchone():
            raise ValueError("接收人不存在或已停用")
    material_id = existing.get("material_id")
    code = str(data.get("material_code") or "").strip()
    if code:
        cursor.execute("SELECT id FROM materials WHERE material_code = ?", (code,))
        row = cursor.fetchone()
        if not row:
            raise ValueError("物料编码不存在")
        material_id = int(row["id"])
    acceptance_item_id = int(data.get("acceptance_item_id") or 0) or None
    if acceptance_item_id:
        cursor.execute(
            "SELECT wi.material_id,wi.material_code,wi.form_id,f.form_type FROM workflow_items wi JOIN workflow_forms f ON f.id=wi.form_id WHERE wi.id=?",
            (acceptance_item_id,),
        )
        item = cursor.fetchone()
        if not item or item["form_type"] != "acceptance" or not user_can_view_form(cursor, user, item["form_id"]):
            raise ValueError("验收明细不存在或无权查看")
        if not item["material_id"] or (material_id and material_id != item["material_id"]):
            raise ValueError("物料与验收明细不一致")
        material_id = int(item["material_id"])
    status = "recorded"
    if mode in {"immediate", "at_time"}:
        status = "watching"
    elif mode == "material":
        status = "watching" if acceptance_item_id and material_id and quantity else "incomplete"
    stamp = now_text()
    values = (content, kind, status, material_id, acceptance_item_id, quantity, recipient, mode, remind_at, stamp)
    if existing:
        cursor.execute(
            """
            UPDATE assistant_notes SET content=?,kind=?,status=?,material_id=?,acceptance_item_id=?,
                quantity=?,recipient_user_id=?,reminder_mode=?,remind_at=?,updated_at=?
            WHERE id=? AND user_id=? AND status IN ('recorded','incomplete','watching')
            """,
            (*values, note_id, user["id"]),
        )
        if cursor.rowcount != 1:
            raise ValueError("事项状态已变化，请刷新后重试")
    else:
        cursor.execute(
            """
            INSERT INTO assistant_notes
                (content,kind,status,material_id,acceptance_item_id,quantity,recipient_user_id,
                 reminder_mode,remind_at,updated_at,user_id,source_message_id,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id,source_message_id,content) DO NOTHING
            """,
            (*values, user["id"], source_message_id, stamp),
        )
        if cursor.rowcount:
            note_id = int(cursor.lastrowid)
        else:
            cursor.execute("SELECT * FROM assistant_notes WHERE user_id=? AND source_message_id=? AND content=?", (user["id"],source_message_id,content))
            previous = dict(cursor.fetchone())
            note_id = int(previous["id"])
            if previous["status"] == "recorded" and previous["reminder_mode"] == "none" and mode in {"immediate", "at_time"}:
                cursor.execute(
                    "UPDATE assistant_notes SET status='watching',reminder_mode=?,remind_at=?,recipient_user_id=?,updated_at=? WHERE id=? AND status='recorded'",
                    (mode, remind_at, recipient, stamp, note_id),
                )
    if mode == "immediate":
        _send_note(cursor, note_id, "事项提醒", content)
    elif mode == "material" and status == "watching":
        evaluate_material_notes(cursor, material_id)
    conn.commit()
    result = next(item for item in list_notes(conn.cursor(), user) if item["id"] == note_id)
    if result["reminder_mode"] == "none":
        result["message"] = "已保存普通笔记，没有发送通知；用户要求发送时请使用 notify_note，要求定时提醒时请使用 schedule_reminder。"
    elif result["status"] == "notified":
        result["message"] = "通知已写入接收人的通知列表，发送时间已记录。"
    elif result["reminder_mode"] == "at_time":
        result["message"] = "已安排定时提醒，到期前 notified_at 为空是正常状态。"
    return result


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
        body = f"{note['content']}。验收单 {note['form_no']} 的物料 {note['name']}（{note['material_code']}）已有可申领库存，请核对并办理。"
        _send_note(cursor, note["id"], "提前取用物料提醒", body,
                   {"material_id": note["material_id"], "form_id": note["form_id"]})


def _send_note(cursor, note_id, title, body, data=None):
    """通知和发送时间同事务保存，失败时一并回滚。"""
    cursor.execute(
        """
        SELECT n.*, u.display_name AS sender_name
        FROM assistant_notes n JOIN users u ON u.id = n.user_id
        WHERE n.id=? AND n.status='watching'
        """,
        (note_id,),
    )
    row = cursor.fetchone()
    if not row:
        return
    note = dict(row)
    stamp = now_text()
    cursor.execute(
        "UPDATE assistant_notes SET status='notified',notified_at=?,updated_at=? WHERE id=? AND status='watching'",
        (stamp, stamp, note_id),
    )
    if cursor.rowcount != 1:
        return
    # 指定收件人时只投递给对方，身份取自事项所属账号，不能由工具参数伪造。
    recipient = int(note["recipient_user_id"] or note["user_id"])
    if recipient != int(note["user_id"]):
        body = f"{note['sender_name']}提醒您“{body}”"
    create_notification(cursor, recipient, title, body, {
        **(data or {}), "assistant_note_id": note_id,
        "sender_user_id": int(note["user_id"]), "sender_name": note["sender_name"],
    }, category="assistant")


def deliver_due_notes(cursor):
    cursor.execute(
        "SELECT id,content FROM assistant_notes WHERE status='watching' AND reminder_mode='at_time' AND remind_at<=? ORDER BY remind_at,id",
        (now_text(),),
    )
    for row in cursor.fetchall():
        _send_note(cursor, row["id"], "定时提醒", row["content"])
