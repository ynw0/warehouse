# -*- coding: utf-8 -*-
"""物料小助手可办理的系统动作。

高风险动作只生成确认单。页面上的确认按钮调用确认接口后才真正执行。
"""

import json
import re
from datetime import datetime, timedelta

from warehouse_suit.db import now_text
from warehouse_suit.inventory_service import begin_inventory_transaction
from warehouse_suit.material_service import (
    delete_material_record,
    update_material_master_by_id,
    upsert_material_master,
)
from warehouse_suit.production_service import (
    delete_finished_inventory_record,
    delete_semifinished_inventory_record,
    disassemble_semifinished_inventory,
    revoke_completed_semifinished,
)
from warehouse_suit.project_service import delete_project, list_projects, save_project
from warehouse_suit.temporary_inventory_service import (
    adjust_temporary_batch,
    create_temporary_batch,
    create_temporary_material,
    delete_temporary_material,
    list_temporary_acceptance_forms,
    promote_acceptance_form,
    promote_temporary_material,
    require_temporary_inventory_enabled,
    split_acceptance_form_claims,
    temporary_batch_rows,
    temporary_inventory_rows,
    temporary_record_rows,
    update_temporary_material,
)
from warehouse_suit.user_admin import (
    delete_user_account,
    list_departments,
    list_user_rows,
    remove_department,
    reset_user_password,
    save_user_account,
    set_user_active,
    write_department,
)
from warehouse_suit.workflow_commands import (
    create_workflow_record,
    delete_workflow_record,
    list_workflow_briefs,
    update_workflow_record,
)
from warehouse_suit.workflow_service import (
    require_form_view,
    require_workflow_edit_or_applicant,
    serialize_form,
    user_has_permission,
)

CONFIRM_MINUTES = 10
_ACTION_BLOCK = re.compile(r"```assistant-action\s*([\s\S]*?)```", re.IGNORECASE)


def action_catalog():
    """给接口目录用的动作说明，不含确认地址。"""
    return [
        {
            "name": spec["name"],
            "title": spec["title"],
            "params": spec["params"],
            "confirm": bool(spec["confirm"]),
        }
        for spec in REGISTRY
    ]


def action_prompt():
    """写进对话系统提示。模型只提交动作，不能自己确认。"""
    lines = [
        "用户要求查询、新建、修改、删除、转移或维护账号时，在回复最后追加一个动作块。不要说已经完成。",
        "```assistant-action",
        '{"action":"动作名","payload":{}}',
        "```",
        "一次只追加一个。缺编号或必填项时先问，不要猜。编码咨询不要输出动作块。",
        "标成须人工确认的动作不会由你执行。口头同意不算确认，要等用户点击页面上的确认执行。正文不要写确认编号或密码。",
    ]
    for spec in REGISTRY:
        mark = "必须人工确认后才执行" if spec["confirm"] else "可直接执行"
        lines.append(f"- {spec['name']}：{spec['title']}。{mark}。参数：{spec['params']}")
    return "\n".join(lines)


def perform_action(conn, user, action, payload, ip="", *, confirmed=False, optional_active_user_id=None):
    """执行或登记一个动作。confirmed 只由确认接口传入。"""
    spec = _spec(action)
    if not isinstance(payload, dict):
        raise ValueError("动作参数必须是对象")
    cursor = conn.cursor()
    _authorize(cursor, user, spec, payload)
    if spec["confirm"] and not confirmed:
        summary = spec["summarize"](cursor, payload)
        confirmation_id = _insert_confirmation(conn, user, spec["name"], summary, payload)
        return {
            "needs_confirmation": True,
            "confirmation_id": confirmation_id,
            "action": spec["name"],
            "summary": summary,
            "message": f"{summary}。尚未执行，请在对话里点击确认执行。",
        }
    result = spec["run"](cursor, user, payload, ip or "", optional_active_user_id)
    conn.commit()
    result = result or {}
    result.setdefault("needs_confirmation", False)
    result.setdefault("message", spec["title"] + "已完成")
    result["data"] = _plain(result.get("data"))
    return _plain(result)


def confirm_action(conn, user, confirmation_id, ip="", optional_active_user_id=None):
    """人工确认后执行。先占住确认单，失败则退回待确认。"""
    cursor = conn.cursor()
    row = _pending_row(cursor, user, confirmation_id)
    cursor.execute(
        "UPDATE assistant_confirmations SET status = 'done' WHERE id = ? AND status = 'pending'",
        (int(row["id"]),),
    )
    if cursor.rowcount != 1:
        raise ValueError("该确认已处理")
    conn.commit()
    payload = json.loads(row["payload_json"])
    try:
        return perform_action(
            conn,
            user,
            row["action"],
            payload,
            ip,
            confirmed=True,
            optional_active_user_id=optional_active_user_id,
        )
    except Exception:
        conn.rollback()
        cursor.execute(
            "UPDATE assistant_confirmations SET status = 'pending' WHERE id = ? AND status = 'done'",
            (int(row["id"]),),
        )
        conn.commit()
        raise


def cancel_action(conn, user, confirmation_id):
    cursor = conn.cursor()
    _pending_row(cursor, user, confirmation_id)
    cursor.execute(
        "UPDATE assistant_confirmations SET status = 'cancelled' WHERE id = ? AND user_id = ? AND status = 'pending'",
        (int(confirmation_id), int(user["id"])),
    )
    conn.commit()
    return {"message": "已取消，未执行"}


def list_pending_actions(cursor, user):
    cursor.execute(
        """
        SELECT id, action, summary, created_at
        FROM assistant_confirmations
        WHERE user_id = ? AND status = 'pending' AND expires_at > ?
        ORDER BY id
        """,
        (int(user["id"]), now_text()),
    )
    return [dict(row) for row in cursor.fetchall()]


def peel_action_block(answer):
    """编码校验只处理正文，避免改写动作参数里的物料编码。"""
    text = str(answer or "")
    match = _ACTION_BLOCK.search(text)
    if not match:
        return text, ""
    body = (text[: match.start()] + text[match.end() :]).strip()
    return body, match.group(0)


def restore_action_block(answer, block):
    if not block:
        return str(answer or "")
    return (str(answer or "").rstrip() + "\n\n" + block).strip()


def apply_chat_actions(conn, user, answer, ip="", optional_active_user_id=None):
    """从模型回复里取出动作块。确认编号只留给页面，不写回对话正文。"""
    text = str(answer or "")
    blocks = list(_ACTION_BLOCK.finditer(text))
    cleaned = _ACTION_BLOCK.sub("", text).strip()
    if not blocks:
        return cleaned, []
    notes = []
    confirmations = []
    raw = blocks[0].group(1).strip()
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError("动作必须是对象")
        result = perform_action(
            conn,
            user,
            str(body.get("action") or "").strip(),
            body.get("payload") or {},
            ip,
            confirmed=False,
            optional_active_user_id=optional_active_user_id,
        )
        notes.append(result.get("message") or "已处理")
        if result.get("needs_confirmation"):
            confirmations.append(
                {
                    "id": result["confirmation_id"],
                    "action": result["action"],
                    "summary": result["summary"],
                }
            )
    except Exception as exc:
        notes.append(f"没有执行：{exc}")
    if len(blocks) > 1:
        notes.append("一次只能办理一个操作，其余未执行。")
    if notes:
        cleaned = (cleaned + "\n\n" + "\n".join(notes)).strip()
    return cleaned, confirmations


def _spec(action):
    for spec in REGISTRY:
        if spec["name"] == action:
            return spec
    names = "、".join(spec["name"] for spec in REGISTRY)
    raise ValueError(f"不支持该操作，可用动作：{names}")


def _authorize(cursor, user, spec, payload):
    if spec.get("allow"):
        spec["allow"](cursor, user, payload)
        return
    permission = spec.get("permission")
    if permission == "admin":
        _require_admin(user)
    elif permission and not user_has_permission(cursor, user, permission):
        raise PermissionError("当前账号没有该操作权限")


def _require_admin(user):
    if "admin" not in (user.get("role_codes") or []):
        raise PermissionError("当前账号没有该操作权限")


def _allow_permission(permission):
    def allow(cursor, user, payload):
        if not user_has_permission(cursor, user, permission):
            raise PermissionError("当前账号没有该操作权限")

    return allow


def _allow_temporary(permission):
    def allow(cursor, user, payload):
        require_temporary_inventory_enabled(cursor)
        if not user_has_permission(cursor, user, permission):
            raise PermissionError("当前账号没有该操作权限")

    return allow


def _allow_admin(cursor, user, payload):
    _require_admin(user)


def _allow_reset_password(cursor, user, payload):
    _require_admin(user)
    if not str(payload.get("password") or "").strip():
        raise ValueError("请输入新密码")


def _summarize_status(cursor, payload):
    row = _user_row(cursor, _required_int(payload, "user_id", "id"))
    active = payload.get("is_active", True) not in (False, 0, "0", "false", "False")
    action = "启用" if active else "停用"
    return f"{action}账号 {_user_label(row)}"


def _insert_confirmation(conn, user, action, summary, payload):
    created = datetime.now()
    expires = created + timedelta(minutes=CONFIRM_MINUTES)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO assistant_confirmations
            (user_id, action, summary, payload_json, status, created_at, expires_at)
        VALUES (?, ?, ?, ?, 'pending', ?, ?)
        """,
        (
            int(user["id"]),
            action,
            summary,
            json.dumps(payload, ensure_ascii=False),
            created.strftime("%Y-%m-%d %H:%M:%S"),
            expires.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    confirmation_id = int(cursor.lastrowid)
    conn.commit()
    return confirmation_id


def _pending_row(cursor, user, confirmation_id):
    cursor.execute("SELECT * FROM assistant_confirmations WHERE id = ?", (int(confirmation_id),))
    row = cursor.fetchone()
    if not row or int(row["user_id"]) != int(user["id"]):
        raise ValueError("确认单不存在")
    if row["status"] != "pending":
        raise ValueError("该确认已处理")
    if str(row["expires_at"]) <= now_text():
        raise ValueError("确认已过期，请重新发起")
    return row


def _required_int(payload, *names):
    for name in names:
        if payload.get(name) not in (None, ""):
            try:
                return int(payload.get(name))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} 必须是整数") from exc
    raise ValueError("缺少 " + " 或 ".join(names))


def _limit(payload):
    try:
        value = int(payload.get("limit") or 20)
    except (TypeError, ValueError):
        value = 20
    return max(1, min(20, value))


def _plain(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return str(value)


def _lines(rows, render, empty):
    body = "\n".join(render(row) for row in rows[:20])
    return body or empty


def _form_row(cursor, form_id):
    cursor.execute("SELECT id, form_no, title, form_type FROM workflow_forms WHERE id = ?", (int(form_id),))
    row = cursor.fetchone()
    if not row:
        raise ValueError("流程不存在")
    return row


def _form_summary(cursor, payload):
    row = _form_row(cursor, _required_int(payload, "form_id"))
    label = f"{row['form_no'] or ''} {row['title'] or ''}".strip()
    return f"删除流程 {label or row['id']}"


def _allow_delete_workflow(cursor, user, payload):
    row = _form_row(cursor, _required_int(payload, "form_id"))
    cursor.execute("SELECT * FROM workflow_forms WHERE id = ?", (int(row["id"]),))
    form = cursor.fetchone()
    require_workflow_edit_or_applicant(cursor, dict(form), user)


def _material_row(cursor, material_id):
    cursor.execute("SELECT id, material_code, name FROM materials WHERE id = ?", (int(material_id),))
    row = cursor.fetchone()
    if not row:
        raise ValueError("物料不存在")
    return row


def _material_summary(prefix, cursor, payload):
    row = _material_row(cursor, _required_int(payload, "material_id", "id"))
    label = f"{row['material_code'] or ''} {row['name'] or ''}".strip()
    return f"{prefix} {label or row['id']}"


def _user_row(cursor, user_id):
    cursor.execute("SELECT id, username, display_name FROM users WHERE id = ?", (int(user_id),))
    row = cursor.fetchone()
    if not row:
        raise ValueError("用户不存在")
    return row


def _user_label(row):
    return row["display_name"] or row["username"] or str(row["id"])


def _run_list_temporary_inventory(cursor, user, payload, ip, optional_active_user_id):
    result = temporary_inventory_rows(cursor, q=str(payload.get("q") or ""), page=1, page_size=_limit(payload))
    items = [
        {
            "material_id": item.get("material_id"),
            "material_code": item.get("material_code") or "",
            "name": item.get("material_name") or "",
            "spec": item.get("spec") or "",
            "quantity": item.get("temporary_quantity") or 0,
            "unit": item.get("unit") or "",
        }
        for item in result["items"]
    ]
    message = "临时库：\n" + _lines(
        items,
        lambda item: f"{item['material_id']} {item['material_code']} {item['name']} 数量 {item['quantity']}{item['unit']}",
        "没有记录",
    )
    return {"message": message, "data": {"total": result["total"], "items": items}}


def _run_list_temporary_batches(cursor, user, payload, ip, optional_active_user_id):
    material_id = _required_int(payload, "material_id")
    rows = temporary_batch_rows(cursor, material_id)
    items = [
        {
            "id": row.get("id"),
            "version": row.get("version"),
            "batch_no": row.get("batch_no") or "",
            "quantity": row.get("quantity") or 0,
            "inventory_status": row.get("inventory_status") or "",
            "transfer_blocked": row.get("transfer_blocked") or 0,
        }
        for row in rows[:20]
    ]
    message = "临时批次：\n" + _lines(
        items,
        lambda item: f"批次 {item['id']} 版本 {item['version']} 数量 {item['quantity']} 状态 {item['inventory_status']}",
        "没有批次",
    )
    return {"message": message, "data": {"items": items}}


def _run_list_temporary_records(cursor, user, payload, ip, optional_active_user_id):
    result = temporary_record_rows(
        cursor,
        q=str(payload.get("q") or ""),
        page=1,
        page_size=_limit(payload),
        material_id=int(payload.get("material_id") or 0),
    )
    items = [
        {
            "id": item.get("id"),
            "material_code": item.get("material_code") or "",
            "material_name": item.get("material_name") or "",
            "quantity": item.get("quantity") or 0,
            "business_type": item.get("business_type") or "",
            "operation_date": item.get("operation_date") or "",
            "remark": item.get("remark") or "",
        }
        for item in result["items"]
    ]
    message = "临时库流水：\n" + _lines(
        items,
        lambda item: f"{item['operation_date']} {item['material_code']} {item['material_name']} {item['business_type']} {item['quantity']}",
        "没有流水",
    )
    return {"message": message, "data": {"total": result["total"], "items": items}}


def _run_list_temporary_acceptance_forms(cursor, user, payload, ip, optional_active_user_id):
    forms = list_temporary_acceptance_forms(cursor)[:_limit(payload)]
    message = "临时验收单：\n" + _lines(
        forms,
        lambda item: f"{item['id']} {item['form_no']} {item['title']} 状态 {item['status']} 已转正式 {('是' if item['promoted'] else '否')}",
        "没有验收单",
    )
    return {"message": message, "data": {"items": forms}}


def _run_create_temporary_material(cursor, user, payload, ip, optional_active_user_id):
    result = create_temporary_material(cursor, payload, user, ip)
    material = result.get("material") or {}
    label = f"{material.get('material_code') or ''} {material.get('name') or ''}".strip()
    return {"message": f"已新建临时库物料 {label}", "data": {"material_id": material.get("id"), "batch_id": result.get("batch_id")}}


def _run_update_temporary_material(cursor, user, payload, ip, optional_active_user_id):
    material_id = _required_int(payload, "material_id", "id")
    material = update_temporary_material(cursor, material_id, payload, user, ip)
    label = f"{material.get('material_code') or ''} {material.get('name') or ''}".strip()
    return {"message": f"已修改临时库物料 {label}", "data": {"material_id": material_id}}


def _run_create_temporary_batch(cursor, user, payload, ip, optional_active_user_id):
    result = create_temporary_batch(cursor, payload, user, ip)
    return {"message": f"已新增临时批次 {result.get('batch_id')}", "data": result}


def _run_adjust_temporary_batch(cursor, user, payload, ip, optional_active_user_id):
    batch_id = _required_int(payload, "batch_id", "id")
    result = adjust_temporary_batch(cursor, batch_id, payload, user, ip)
    return {"message": f"已调整临时批次 {batch_id}", "data": result}


def _run_split_acceptance_claims(cursor, user, payload, ip, optional_active_user_id):
    form_id = _required_int(payload, "form_id")
    result = split_acceptance_form_claims(cursor, form_id, user, ip)
    return {"message": f"已拆分挡住验收单 {form_id} 的申领，库存数量未变，也未转入正式库", "data": result}


def _run_delete_temporary_material(cursor, user, payload, ip, optional_active_user_id):
    material_id = _required_int(payload, "material_id", "id")
    row = _material_row(cursor, material_id)
    delete_temporary_material(cursor, material_id, user, ip)
    label = f"{row['material_code'] or ''} {row['name'] or ''}".strip()
    return {"message": f"已删除临时库物料 {label}", "data": {"material_id": material_id}}


def _run_promote_temporary_material(cursor, user, payload, ip, optional_active_user_id):
    material_id = _required_int(payload, "material_id")
    result = promote_temporary_material(cursor, material_id, payload.get("batches") or [], user, ip)
    return {"message": f"已把物料 {material_id} 的所选批次转入正式库", "data": result}


def _run_promote_acceptance_form(cursor, user, payload, ip, optional_active_user_id):
    form_id = _required_int(payload, "form_id")
    row = _form_row(cursor, form_id)
    result = promote_acceptance_form(cursor, form_id, user, ip)
    return {"message": f"已把验收单 {row['form_no']} 转入正式库", "data": result}


def _summarize_promote_material(cursor, payload):
    return _material_summary("把临时库物料转入正式库", cursor, payload)


def _summarize_promote_form(cursor, payload):
    row = _form_row(cursor, _required_int(payload, "form_id"))
    return f"把验收单 {row['form_no'] or row['id']} 转入正式库"


def _run_list_workflows(cursor, user, payload, ip, optional_active_user_id):
    rows = list_workflow_briefs(
        cursor,
        user,
        str(payload.get("q") or payload.get("keyword") or ""),
        str(payload.get("form_type") or payload.get("type") or ""),
        _limit(payload),
    )
    message = "流程：\n" + _lines(
        rows,
        lambda item: f"{item['id']} {item['form_no']} {item['form_type']} {item['title'] or ''} {item['status']}",
        "没有可见流程",
    )
    return {"message": message, "data": {"items": rows}}


def _run_get_workflow(cursor, user, payload, ip, optional_active_user_id):
    form_id = _required_int(payload, "form_id")
    require_form_view(cursor, form_id)
    form = serialize_form(cursor, form_id)
    if not form:
        raise ValueError("流程不存在")
    brief = {
        "id": form.get("id"),
        "form_no": form.get("form_no") or "",
        "form_type": form.get("form_type") or "",
        "title": form.get("title") or "",
        "status": form.get("status") or "",
        "current_step": form.get("current_step") or "",
    }
    all_items = form.get("items") or []
    offset = max(0, int(payload.get("offset") or 0))
    items = []
    for item in all_items[offset:offset + 20]:
        items.append(
            {
                "material_name": item.get("material_name") or item.get("name") or "",
                "request_quantity": item.get("request_quantity"),
                "qualified_quantity": item.get("qualified_quantity"),
                "material_code": item.get("material_code") or "",
                "unit": item.get("unit") or "",
                "brand_model": item.get("brand_model") or "",
                "spec": item.get("spec") or "",
            }
        )
    result = {"form": brief, "items": items, "offset": offset, "total_items": len(all_items)}
    text = json.dumps(result, ensure_ascii=False)
    return {"message": f"流程内容：\n{text}", "data": brief}


def _summarize_new_workflow(cursor, payload):
    """确认卡显示完整的待提交字段。"""
    return "请核对后提交：\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def _run_create_workflow(cursor, user, payload, ip, optional_active_user_id):
    form = create_workflow_record(cursor.connection, payload, optional_active_user_id)
    form_no = (form or {}).get("form_no") or ""
    form_type = (form or {}).get("form_type") or payload.get("form_type") or ""
    return {"message": f"已新建流程 {form_no}（{form_type}）", "data": {"id": (form or {}).get("id"), "form_no": form_no, "form_type": form_type}}


def _run_update_workflow(cursor, user, payload, ip, optional_active_user_id):
    form_id = _required_int(payload, "form_id")
    form = update_workflow_record(cursor.connection, form_id, payload)
    form_no = (form or {}).get("form_no") or form_id
    return {"message": f"已修改流程 {form_no}", "data": {"id": form_id, "form_no": form_no}}


def _run_delete_workflow(cursor, user, payload, ip, optional_active_user_id):
    form_id = _required_int(payload, "form_id")
    row = _form_row(cursor, form_id)
    delete_workflow_record(cursor.connection, form_id)
    return {"message": f"已删除流程 {row['form_no'] or form_id}", "data": {"form_id": form_id}}


def _allow_save_material(cursor, user, payload):
    material_id = int(payload.get("id") or payload.get("material_id") or 0)
    code = str(payload.get("material_code") or "").strip()
    existing = bool(material_id)
    if not existing and code:
        cursor.execute("SELECT id FROM materials WHERE material_code = ?", (code,))
        existing = bool(cursor.fetchone())
    permission = "edit_material" if existing else "add_material"
    if not user_has_permission(cursor, user, permission):
        raise PermissionError("当前账号没有该操作权限")


def _run_save_material(cursor, user, payload, ip, optional_active_user_id):
    material_id = int(payload.get("id") or payload.get("material_id") or 0)
    if material_id:
        material = update_material_master_by_id(cursor, material_id, payload)
    else:
        material = upsert_material_master(cursor, payload)
    label = f"{(material or {}).get('material_code') or ''} {(material or {}).get('name') or ''}".strip()
    return {"message": f"已保存物料 {label}", "data": {"id": (material or {}).get("id"), "material_code": (material or {}).get("material_code") or ""}}


def _summarize_delete_material(cursor, payload):
    return _material_summary("删除物料", cursor, payload)


def _run_delete_material(cursor, user, payload, ip, optional_active_user_id):
    material_id = _required_int(payload, "material_id", "id")
    deleted = delete_material_record(cursor, material_id, user.get("id"))
    label = f"{deleted['material_code']} {deleted['name']}".strip()
    return {"message": f"已删除物料 {label}", "data": deleted}


def _run_delete_semifinished(cursor, user, payload, ip, optional_active_user_id):
    inventory_id = _required_int(payload, "inventory_id", "id")
    deleted = delete_semifinished_inventory_record(cursor, inventory_id, user.get("id"))
    return {"message": f"已删除半成品 {deleted['serial_no']} {deleted['name']}".strip(), "data": deleted}


def _run_disassemble(cursor, user, payload, ip, optional_active_user_id):
    inventory_id = _required_int(payload, "inventory_id", "id")
    begin_inventory_transaction(cursor.connection)
    disassemble_semifinished_inventory(cursor, inventory_id, user.get("id"))
    return {"message": f"已拆解半成品库存 {inventory_id}", "data": {"inventory_id": inventory_id}}


def _run_delete_finished(cursor, user, payload, ip, optional_active_user_id):
    inventory_id = _required_int(payload, "inventory_id", "id")
    deleted = delete_finished_inventory_record(cursor, inventory_id, user.get("id"))
    return {"message": f"已删除成品 {deleted['serial_no']} {deleted['name']}".strip(), "data": deleted}


def _run_revoke_semifinished(cursor, user, payload, ip, optional_active_user_id):
    form_id = _required_int(payload, "form_id")
    begin_inventory_transaction(cursor.connection)
    cursor.execute("SELECT * FROM workflow_forms WHERE id = ?", (form_id,))
    form = cursor.fetchone()
    if not form:
        raise ValueError("流程不存在")
    require_workflow_edit_or_applicant(cursor, dict(form), user)
    revoke_completed_semifinished(cursor, form_id)
    return {"message": f"已撤回半成品流程 {form['form_no']}", "data": {"form_id": form_id}}


def _summarize_inventory(prefix, table, name_field, cursor, payload):
    inventory_id = _required_int(payload, "inventory_id", "id")
    cursor.execute(f"SELECT serial_no, {name_field} AS item_name FROM {table} WHERE id = ?", (inventory_id,))
    row = cursor.fetchone()
    if not row:
        raise ValueError("库存不存在")
    label = f"{row['serial_no'] or ''} {row['item_name'] or ''}".strip()
    return f"{prefix} {label or inventory_id}"


def _summarize_revoke(cursor, payload):
    row = _form_row(cursor, _required_int(payload, "form_id"))
    return f"撤回已办结半成品流程 {row['form_no'] or row['id']}"


def _allow_revoke(cursor, user, payload):
    cursor.execute("SELECT * FROM workflow_forms WHERE id = ?", (_required_int(payload, "form_id"),))
    form = cursor.fetchone()
    if not form:
        raise ValueError("流程不存在")
    require_workflow_edit_or_applicant(cursor, dict(form), user)


def _run_list_users(cursor, user, payload, ip, optional_active_user_id):
    rows = list_user_rows(cursor)[:_limit(payload)]
    message = "账号：\n" + _lines(
        rows,
        lambda item: f"{item['id']} {item['username']} {item['display_name'] or ''} {item['department'] or ''} 角色 {item['role_codes'] or ''} 启用 {item['is_active']}",
        "没有账号",
    )
    return {"message": message, "data": {"items": rows}}


def _run_list_departments(cursor, user, payload, ip, optional_active_user_id):
    rows = list_departments(cursor)
    message = "部门：\n" + _lines(rows, lambda item: f"{item['id']} {item['name']}", "没有部门")
    return {"message": message, "data": {"items": rows}}


def _allow_save_user(cursor, user, payload):
    _require_admin(user)
    if int(payload.get("id") or 0) and str(payload.get("password") or "").strip():
        raise ValueError("修改已有账号的密码要使用 reset_user_password，并经人工确认")
    if int(payload.get("id") or 0) and payload.get("is_active") in (False, 0, "0", "false", "False"):
        raise ValueError("停用账号要使用 set_user_status，并经人工确认")


def _run_save_user(cursor, user, payload, ip, optional_active_user_id):
    user_id = save_user_account(cursor, payload)
    return {"message": f"已保存账号 {payload.get('username') or user_id}", "data": {"user_id": user_id}}


def _summarize_user(prefix, cursor, payload):
    row = _user_row(cursor, _required_int(payload, "user_id", "id"))
    return f"{prefix} {_user_label(row)}"


def _run_delete_user(cursor, user, payload, ip, optional_active_user_id):
    deleted = delete_user_account(cursor, user, _required_int(payload, "user_id", "id"))
    return {"message": f"已删除账号 {_user_label(deleted)}", "data": deleted}


def _run_reset_password(cursor, user, payload, ip, optional_active_user_id):
    updated = reset_user_password(cursor, _required_int(payload, "user_id", "id"), payload.get("password"))
    return {"message": f"已重置账号 {_user_label(updated)} 的密码", "data": {"user_id": updated["id"]}}


def _run_set_user_status(cursor, user, payload, ip, optional_active_user_id):
    active = payload.get("is_active", True) not in (False, 0, "0", "false", "False")
    updated = set_user_active(cursor, user, _required_int(payload, "user_id", "id"), active)
    state = "启用" if updated["is_active"] else "停用"
    return {"message": f"已{state}账号 {_user_label(updated)}", "data": updated}


def _allow_save_department(cursor, user, payload):
    if int(payload.get("id") or 0):
        if not user_has_permission(cursor, user, "edit_department"):
            raise PermissionError("当前账号没有该操作权限")
    else:
        _require_admin(user)


def _run_save_department(cursor, user, payload, ip, optional_active_user_id):
    department_id = write_department(cursor, payload)
    return {"message": f"已保存部门 {payload.get('name') or department_id}", "data": {"department_id": department_id}}


def _summarize_department(cursor, payload):
    department_id = _required_int(payload, "department_id", "id")
    cursor.execute("SELECT name FROM departments WHERE id = ?", (department_id,))
    row = cursor.fetchone()
    if not row:
        raise ValueError("部门不存在")
    return f"删除部门 {row['name']}"


def _run_delete_department(cursor, user, payload, ip, optional_active_user_id):
    deleted = remove_department(cursor, user, _required_int(payload, "department_id", "id"))
    return {"message": f"已删除部门 {deleted['name']}", "data": deleted}


def _run_save_project(cursor, user, payload, ip, optional_active_user_id):
    project_id = int(payload.get("id") or payload.get("project_id") or 0) or None
    saved_id = save_project(cursor, payload, project_id)
    return {"message": f"已保存项目 {payload.get('code') or saved_id}", "data": {"id": saved_id}}


def _summarize_project(cursor, payload):
    project_id = _required_int(payload, "project_id", "id")
    cursor.execute("SELECT code FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    if not row:
        raise ValueError("项目不存在")
    return f"删除项目 {row['code']}"


def _run_delete_project(cursor, user, payload, ip, optional_active_user_id):
    project_id = _required_int(payload, "project_id", "id")
    cursor.execute("SELECT code FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    delete_project(cursor, project_id)
    code = row["code"] if row else project_id
    return {"message": f"已删除项目 {code}", "data": {"id": project_id}}


def _run_list_projects(cursor, user, payload, ip, optional_active_user_id):
    rows = list_projects(cursor, active_only=False)[:_limit(payload)]
    items = [
        {"id": row.get("id"), "code": row.get("code") or "", "active": bool(row.get("active")), "owner_name": row.get("owner_name") or ""}
        for row in rows
    ]
    message = "项目：\n" + _lines(
        items,
        lambda item: f"{item['id']} {item['code']} 负责人 {item['owner_name']} 启用 {('是' if item['active'] else '否')}",
        "没有项目",
    )
    return {"message": message, "data": {"items": items}}


REGISTRY = [
    {"name": "list_temporary_inventory", "title": "查询临时库", "params": "q 可选", "confirm": False, "allow": _allow_temporary("view_temporary_inventory"), "run": _run_list_temporary_inventory},
    {"name": "list_temporary_batches", "title": "查询临时批次", "params": "material_id", "confirm": False, "allow": _allow_temporary("view_temporary_inventory"), "run": _run_list_temporary_batches},
    {"name": "list_temporary_records", "title": "查询临时库流水", "params": "q、material_id 可选", "confirm": False, "allow": _allow_temporary("view_temporary_inventory"), "run": _run_list_temporary_records},
    {"name": "list_temporary_acceptance_forms", "title": "查询可转正式库的验收单", "params": "无", "confirm": False, "allow": _allow_temporary("view_temporary_inventory"), "run": _run_list_temporary_acceptance_forms},
    {"name": "create_temporary_material", "title": "新建临时库物料", "params": "与临时库新建接口相同，并带唯一 operation_key", "confirm": False, "allow": _allow_temporary("manage_temporary_inventory"), "run": _run_create_temporary_material},
    {"name": "update_temporary_material", "title": "修改临时库物料", "params": "material_id 及要修改的物料字段", "confirm": False, "allow": _allow_temporary("manage_temporary_inventory"), "run": _run_update_temporary_material},
    {"name": "create_temporary_batch", "title": "新增临时批次", "params": "material_id、quantity、total_amount、warehouse_type、shelf_id、唯一 operation_key", "confirm": False, "allow": _allow_temporary("manage_temporary_inventory"), "run": _run_create_temporary_batch},
    {"name": "adjust_temporary_batch", "title": "调整临时批次数量", "params": "batch_id、adjustment_quantity、reason、唯一 operation_key", "confirm": False, "allow": _allow_temporary("manage_temporary_inventory"), "run": _run_adjust_temporary_batch},
    {"name": "split_acceptance_claims", "title": "拆分挡住验收单的申领", "params": "form_id。不改库存，也不转入正式库", "confirm": False, "allow": _allow_temporary("transfer_temporary_inventory"), "run": _run_split_acceptance_claims},
    {"name": "delete_temporary_material", "title": "删除临时库物料", "params": "material_id", "confirm": True, "allow": _allow_temporary("manage_temporary_inventory"), "summarize": lambda cursor, payload: _material_summary("删除临时库物料", cursor, payload), "run": _run_delete_temporary_material},
    {"name": "promote_temporary_material", "title": "临时库批次转入正式库", "params": "material_id、batches（每项含 id 和 version）", "confirm": True, "allow": _allow_temporary("transfer_temporary_inventory"), "summarize": _summarize_promote_material, "run": _run_promote_temporary_material},
    {"name": "promote_acceptance_form", "title": "验收单转入正式库", "params": "form_id", "confirm": True, "allow": _allow_temporary("transfer_temporary_inventory"), "summarize": _summarize_promote_form, "run": _run_promote_acceptance_form},
    {"name": "list_workflows", "title": "查询可见流程", "params": "q、form_type 可选。类型含 acceptance、semifinished、finished、claim、semifinished_claim、special_claim、borrow、purchase_request、supply、common_material", "confirm": False, "run": _run_list_workflows},
    {"name": "get_workflow", "title": "读取一条流程", "params": "form_id、offset 可选。每次最多读取 20 条明细", "confirm": False, "run": _run_get_workflow},
    {"name": "create_workflow", "title": "新建流程", "params": "form_type 及该流程原发起接口的字段，确认前列出全部内容", "confirm": True, "summarize": _summarize_new_workflow, "run": _run_create_workflow},
    {"name": "update_workflow", "title": "修改流程", "params": "form_id 及原修改接口的字段", "confirm": False, "run": _run_update_workflow},
    {"name": "delete_workflow", "title": "删除流程", "params": "form_id", "confirm": True, "allow": _allow_delete_workflow, "summarize": _form_summary, "run": _run_delete_workflow},
    {"name": "save_material", "title": "保存物料主数据", "params": "新建带 material_code 等字段；修改带 id", "confirm": False, "allow": _allow_save_material, "run": _run_save_material},
    {"name": "delete_material", "title": "删除物料", "params": "material_id", "confirm": True, "allow": _allow_permission("edit_material"), "summarize": _summarize_delete_material, "run": _run_delete_material},
    {"name": "delete_semifinished_inventory", "title": "删除半成品库存", "params": "inventory_id", "confirm": True, "allow": _allow_permission("write_semifinished_inventory"), "summarize": lambda cursor, payload: _summarize_inventory("删除半成品", "semifinished_inventory", "name", cursor, payload), "run": _run_delete_semifinished},
    {"name": "disassemble_semifinished", "title": "拆解半成品", "params": "inventory_id", "confirm": True, "allow": _allow_permission("write_semifinished_inventory"), "summarize": lambda cursor, payload: _summarize_inventory("拆解半成品", "semifinished_inventory", "name", cursor, payload), "run": _run_disassemble},
    {"name": "delete_finished_inventory", "title": "删除成品库存", "params": "inventory_id", "confirm": True, "allow": _allow_permission("write_finished_inventory"), "summarize": lambda cursor, payload: _summarize_inventory("删除成品", "finished_good_inventory", "product_name", cursor, payload), "run": _run_delete_finished},
    {"name": "revoke_semifinished", "title": "撤回已办结半成品流程", "params": "form_id", "confirm": True, "allow": _allow_revoke, "summarize": _summarize_revoke, "run": _run_revoke_semifinished},
    {"name": "list_users", "title": "查询账号", "params": "无", "confirm": False, "allow": _allow_admin, "run": _run_list_users},
    {"name": "list_departments", "title": "查询部门", "params": "无", "confirm": False, "allow": _allow_admin, "run": _run_list_departments},
    {"name": "save_user", "title": "保存账号", "params": "username、display_name、department、roles。不能用它改密码或停用已有账号", "confirm": False, "allow": _allow_save_user, "run": _run_save_user},
    {"name": "delete_user", "title": "删除账号", "params": "user_id", "confirm": True, "allow": _allow_admin, "summarize": lambda cursor, payload: _summarize_user("删除账号", cursor, payload), "run": _run_delete_user},
    {"name": "reset_user_password", "title": "重置密码", "params": "user_id、password", "confirm": True, "allow": _allow_reset_password, "summarize": lambda cursor, payload: _summarize_user("重置账号密码", cursor, payload), "run": _run_reset_password},
    {"name": "set_user_status", "title": "启用或停用账号", "params": "user_id、is_active", "confirm": True, "allow": _allow_admin, "summarize": _summarize_status, "run": _run_set_user_status},
    {"name": "save_department", "title": "保存部门", "params": "name、description；修改时带 id", "confirm": False, "allow": _allow_save_department, "run": _run_save_department},
    {"name": "delete_department", "title": "删除部门", "params": "department_id", "confirm": True, "permission": "edit_department", "summarize": _summarize_department, "run": _run_delete_department},
    {"name": "list_projects", "title": "查询项目", "params": "无", "confirm": False, "permission": "view_projects", "run": _run_list_projects},
    {"name": "save_project", "title": "保存项目", "params": "code、owner_user_id、active、member_ids；修改时带 id", "confirm": False, "permission": "manage_projects", "run": _run_save_project},
    {"name": "delete_project", "title": "删除停用项目", "params": "project_id", "confirm": True, "permission": "manage_projects", "summarize": _summarize_project, "run": _run_delete_project},
]
