"""Temporary-to-formal inventory transfer task services."""

from __future__ import annotations

from warehouse_suit.acceptance_service import create_acceptance_workflow
from warehouse_suit.borrow_service import has_active_temporary_borrows
from warehouse_suit.db import now_text
from warehouse_suit.inventory_constants import (
    INVENTORY_STATUS_AVAILABLE,
    INVENTORY_STATUS_TRANSFER_LOCKED,
    STOCK_SOURCE_FORMAL,
    STOCK_SOURCE_TEMPORARY,
)
from warehouse_suit.inventory_service import begin_inventory_transaction
from warehouse_suit.notifications import create_notification
from warehouse_suit.numbering import next_table_no
from warehouse_suit.settings import temporary_inventory_enabled
from warehouse_suit.temporary_inventory_service import write_audit_log
from warehouse_suit.transfer_constants import (
    TRANSFER_ORIGIN_TYPE,
    TRANSFER_STATUS_ACCEPTANCE_FAILED,
    TRANSFER_STATUS_ACCEPTANCE_IN_PROGRESS,
    TRANSFER_STATUS_AWAITING_PURCHASE,
    TRANSFER_STATUS_AUTO_CLAIM_EXCEPTION,
    TRANSFER_STATUS_AUTO_CLAIM_CREATING,
    TRANSFER_STATUS_AUTO_CLAIM_PENDING,
    TRANSFER_STATUS_RESERVING,
    TRANSFER_STATUS_CANCELLED,
    TRANSFER_STATUS_EXCEPTION,
    TRANSFER_STATUS_FORMAL_INBOUND_COMPLETE,
    TRANSFER_STATUS_FORMAL_INBOUND_PARTIAL,
    TRANSFER_STATUS_LABELS,
    TRANSFER_STATUS_PAUSED,
)
from warehouse_suit.workflow_service import user_has_permission, user_id_has_permission


TRANSFERABLE_OBLIGATION_STATUSES = ("pending", "reserved")


class TransferConflict(RuntimeError):
    pass


class TransferNotFound(LookupError):
    pass


def active_transfer_key(material_id):
    return f"material:{int(material_id)}"


def material_has_active_transfer(cursor, material_id):
    cursor.execute(
        """
        SELECT id
        FROM inventory_transfer_tasks
        WHERE material_id = ? AND active_key = ?
        LIMIT 1
        """,
        (int(material_id), active_transfer_key(material_id)),
    )
    row = cursor.fetchone()
    return int(row["id"]) if row else 0


def _task_row(cursor, task_id):
    cursor.execute(
        """
        SELECT t.*, m.material_code, m.name AS material_name, m.brand_model, m.spec, m.unit,
               requester.display_name AS requested_by_name,
               buyer.display_name AS assigned_buyer_name
        FROM inventory_transfer_tasks t
        JOIN materials m ON m.id = t.material_id
        LEFT JOIN users requester ON requester.id = t.requested_by
        LEFT JOIN users buyer ON buyer.id = t.assigned_buyer_id
        WHERE t.id = ?
        """,
        (int(task_id),),
    )
    row = cursor.fetchone()
    if not row:
        raise TransferNotFound("转移任务不存在")
    return dict(row)


def _is_admin(user):
    return bool(user and "admin" in (user.get("role_codes") or []))


def _can_process(cursor, user):
    return user_has_permission(cursor, user, "process_temporary_transfer")


def _can_request(cursor, user):
    return user_has_permission(cursor, user, "transfer_temporary_inventory")


def _can_view_task(cursor, user, task):
    if _is_admin(user) or _can_process(cursor, user):
        return True
    return _can_request(cursor, user) and int(task["requested_by"]) == int(user.get("id") or 0)


def _in_progress_link_count(cursor, task_id):
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM transfer_acceptance_links l
        JOIN workflow_forms f ON f.id = l.acceptance_form_id
        WHERE l.task_id = ?
          AND l.status = 'in_progress'
          AND f.status NOT IN ('completed', 'rejected', 'cancelled')
        """,
        (int(task_id),),
    )
    return int(cursor.fetchone()[0] or 0)


def serialize_transfer_task(cursor, task_id, user=None):
    task = _task_row(cursor, task_id)
    if user is not None and not _can_view_task(cursor, user, task):
        raise PermissionError("当前账号无权查看该转移任务")
    cursor.execute(
        """
        SELECT ti.*, b.batch_no, b.quantity AS current_quantity,
               b.inventory_status AS current_inventory_status, b.version AS current_version
        FROM inventory_transfer_items ti
        JOIN material_batches b ON b.id = ti.source_batch_id
        WHERE ti.task_id = ?
        ORDER BY ti.id
        """,
        (int(task_id),),
    )
    task["items"] = [dict(row) for row in cursor.fetchall()]
    cursor.execute(
        """
        SELECT tio.*, o.status AS obligation_status, o.issued_quantity, o.settled_quantity
        FROM inventory_transfer_obligations tio
        JOIN temporary_issue_obligations o ON o.id = tio.obligation_id
        WHERE tio.task_id = ?
        ORDER BY tio.id
        """,
        (int(task_id),),
    )
    task["obligations"] = [dict(row) for row in cursor.fetchall()]
    cursor.execute(
        """
        SELECT l.*, f.form_no AS acceptance_form_no, f.status AS acceptance_form_status
        FROM transfer_acceptance_links l
        JOIN workflow_forms f ON f.id = l.acceptance_form_id
        WHERE l.task_id = ?
        ORDER BY l.id
        """,
        (int(task_id),),
    )
    task["acceptance_links"] = [dict(row) for row in cursor.fetchall()]
    task["status_label"] = TRANSFER_STATUS_LABELS.get(task["status"], task["status"])
    task["remaining_quantity"] = max(
        0.0,
        float(task["target_acceptance_quantity"] or 0)
        - float(task["accepted_quantity"] or 0),
    )
    task["temporary_inventory_enabled"] = bool(temporary_inventory_enabled(cursor))
    task["available_actions"] = []
    if user is not None:
        enabled = task["temporary_inventory_enabled"]
        admin = _is_admin(user)
        process = _can_process(cursor, user)
        request = _can_request(cursor, user)
        user_id = int(user.get("id") or 0)
        assigned = int(task.get("assigned_buyer_id") or 0)
        in_progress = _in_progress_link_count(cursor, task_id)
        if enabled and process and task["status"] == TRANSFER_STATUS_AWAITING_PURCHASE and (
            not assigned or assigned == user_id
        ):
            task["available_actions"].append("claim")
        if (
            enabled
            and process
            and (admin or assigned == user_id)
            and task["status"] in {
                TRANSFER_STATUS_AWAITING_PURCHASE,
                TRANSFER_STATUS_ACCEPTANCE_FAILED,
                TRANSFER_STATUS_FORMAL_INBOUND_PARTIAL,
            }
            and not in_progress
        ):
            task["available_actions"].append("start_acceptance")
        if (
            (admin or (request and int(task["requested_by"]) == user_id))
            and task["status"] in {
                TRANSFER_STATUS_AWAITING_PURCHASE,
                TRANSFER_STATUS_ACCEPTANCE_FAILED,
            }
            and float(task["accepted_quantity"] or 0) <= 1e-9
            and not in_progress
        ):
            task["available_actions"].append("cancel")
        if enabled and process and task["status"] in {
            TRANSFER_STATUS_PAUSED,
            TRANSFER_STATUS_EXCEPTION,
        }:
            task["available_actions"].append("retry")
        if enabled and process and task["status"] == TRANSFER_STATUS_FORMAL_INBOUND_COMPLETE:
            task["available_actions"].append("process_auto_claims")
        if enabled and process and task["status"] == TRANSFER_STATUS_AUTO_CLAIM_EXCEPTION:
            task["available_actions"].append("retry_auto_claims")
    return task


def _user_ids_with_permission(cursor, permission):
    cursor.execute("SELECT id FROM users WHERE is_active = 1 ORDER BY id")
    return [
        int(row["id"])
        for row in cursor.fetchall()
        if user_id_has_permission(cursor, int(row["id"]), permission)
    ]


def _notify_once(cursor, user_id, title, body, data, event_key):
    cursor.execute(
        """
        SELECT id
        FROM notifications
        WHERE user_id = ?
          AND json_valid(data_json)
          AND json_extract(data_json, '$.event_key') = ?
        LIMIT 1
        """,
        (int(user_id), str(event_key)),
    )
    if cursor.fetchone():
        return False
    payload = dict(data or {})
    payload["event_key"] = str(event_key)
    payload.setdefault("business_type", "temporary_transfer")
    payload.setdefault("origin_type", TRANSFER_ORIGIN_TYPE)
    create_notification(cursor, int(user_id), title, body, payload)
    return True


def notify_transfer_event(cursor, task, event, recipients=None, body=""):
    recipient_ids = sorted(set(int(value) for value in (recipients or []) if int(value or 0)))
    data = {
        "business_type": "temporary_transfer",
        "origin_type": "temporary_transfer",
        "transfer_task_id": int(task["id"]),
        "material_id": int(task["material_id"]),
        "target_quantity": float(task["target_acceptance_quantity"] or 0),
        "temporary_quantity": float(task["temporary_quantity_snapshot"] or 0),
        "obligation_quantity": float(task["obligation_quantity_snapshot"] or 0),
        "status": task["status"],
    }
    if event == "completed":
        cursor.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(quantity), 0)
            FROM transfer_auto_claims WHERE task_id = ?
            """,
            (int(task["id"]),),
        )
        auto_claim_count, settled_quantity = cursor.fetchone()
        data.update(
            {
                "business_type": "temporary_transfer_completed",
                "accepted_quantity": float(task["accepted_quantity"] or 0),
                "settled_quantity": float(settled_quantity or 0),
                "closed_temporary_quantity": float(
                    task["temporary_quantity_snapshot"] or 0
                ),
                "auto_claim_count": int(auto_claim_count or 0),
                "completed_at": task.get("completed_at") or now_text(),
            }
        )
    title = {
        "created": "有新的临时物料需要转正式库处理",
        "claimed": "临时物料转正式库任务已认领",
        "acceptance_started": "临时物料转正式库验收已发起",
        "acceptance_failed": "临时物料转正式库验收失败",
        "formal_inbound_partial": "正式入库数量不足，需要补充验收",
        "formal_inbound_complete": "已完成正式入库，等待后续历史领用结算",
        "auto_claim_pending": "历史临时领用结算流程已创建",
        "auto_claim_exception": "历史临时领用结算异常",
        "completed": "临时物料转正式库已全部完成",
        "cancelled": "临时物料转正式库任务已取消",
        "exception": "临时物料转正式库任务异常",
    }.get(event, "临时物料转正式库任务更新")
    default_body = (
        f"{task.get('material_code') or ''} {task.get('material_name') or ''}，"
        f"临时现存 {float(task['temporary_quantity_snapshot'] or 0):g}，"
        f"待结算 {float(task['obligation_quantity_snapshot'] or 0):g}，"
        f"目标验收 {float(task['target_acceptance_quantity'] or 0):g}。"
    )
    for user_id in recipient_ids:
        _notify_once(
            cursor,
            user_id,
            title,
            body or default_body,
            data,
            f"temporary_transfer:{task['id']}:{event}:{user_id}",
        )


def transfer_preview(cursor, material_id):
    material_id = int(material_id or 0)
    cursor.execute(
        "SELECT id, material_code, name, spec, unit FROM materials WHERE id = ?",
        (material_id,),
    )
    material = cursor.fetchone()
    if not material:
        raise ValueError("物料不存在")
    cursor.execute(
        """
        SELECT COALESCE(SUM(quantity), 0), COUNT(*)
        FROM material_batches
        WHERE material_id = ? AND stock_source = ?
          AND inventory_status = ? AND quantity > 0
        """,
        (material_id, STOCK_SOURCE_TEMPORARY, INVENTORY_STATUS_AVAILABLE),
    )
    temporary_quantity, batch_count = cursor.fetchone()
    placeholders = ",".join("?" for _ in TRANSFERABLE_OBLIGATION_STATUSES)
    cursor.execute(
        f"""
        SELECT COALESCE(SUM(issued_quantity - settled_quantity), 0), COUNT(*)
        FROM temporary_issue_obligations
        WHERE material_id = ? AND status IN ({placeholders})
          AND issued_quantity - settled_quantity > 0
        """,
        [material_id, *TRANSFERABLE_OBLIGATION_STATUSES],
    )
    obligation_quantity, obligation_count = cursor.fetchone()
    active_task_id = material_has_active_transfer(cursor, material_id)
    active_borrows = has_active_temporary_borrows(cursor, material_id)
    target_quantity = float(temporary_quantity or 0) + float(
        obligation_quantity or 0
    )
    return {
        "material": dict(material),
        "temporary_quantity": float(temporary_quantity or 0),
        "temporary_batch_count": int(batch_count or 0),
        "obligation_quantity": float(obligation_quantity or 0),
        "obligation_count": int(obligation_count or 0),
        "target_acceptance_quantity": target_quantity,
        "has_active_temporary_borrows": bool(active_borrows),
        "active_transfer_task_id": int(active_task_id or 0),
        "can_transfer": bool(
            target_quantity > 1e-9 and not active_borrows and not active_task_id
        ),
    }


def create_transfer_task(cursor, material_id, idempotency_key, user, ip_address=""):
    material_id = int(material_id or 0)
    if material_id <= 0:
        raise ValueError("物料不能为空")
    key = str(idempotency_key or "").strip()
    if not key:
        raise ValueError("idempotency_key 不能为空")
    begin_inventory_transaction(cursor.connection)
    cursor.execute(
        "SELECT id, material_id FROM inventory_transfer_tasks WHERE idempotency_key = ?",
        (key,),
    )
    existing = cursor.fetchone()
    if existing:
        if int(existing["material_id"]) != material_id:
            raise TransferConflict("幂等键已被其他物料的转移任务使用")
        result = serialize_transfer_task(cursor, int(existing["id"]), user)
        result["idempotent"] = True
        return result

    cursor.execute("SELECT * FROM materials WHERE id = ?", (material_id,))
    material_row = cursor.fetchone()
    if not material_row:
        raise ValueError("物料不存在")
    material = dict(material_row)
    if has_active_temporary_borrows(cursor, material_id):
        raise TransferConflict("该物料存在未归还的临时借用，暂不能转移到正式库")
    if material_has_active_transfer(cursor, material_id):
        raise TransferConflict("该物料已有进行中的转正式库任务")
    cursor.execute(
        """
        SELECT *
        FROM material_batches
        WHERE material_id = ? AND stock_source = ?
          AND inventory_status = ? AND quantity > 0
        ORDER BY received_date, id
        """,
        (material_id, STOCK_SOURCE_TEMPORARY, INVENTORY_STATUS_AVAILABLE),
    )
    batches = [dict(row) for row in cursor.fetchall()]
    placeholders = ",".join("?" for _ in TRANSFERABLE_OBLIGATION_STATUSES)
    cursor.execute(
        f"""
        SELECT *, issued_quantity - settled_quantity AS pending_quantity
        FROM temporary_issue_obligations
        WHERE material_id = ? AND status IN ({placeholders})
          AND issued_quantity - settled_quantity > 0
        ORDER BY id
        """,
        [material_id, *TRANSFERABLE_OBLIGATION_STATUSES],
    )
    obligations = [dict(row) for row in cursor.fetchall()]
    temporary_quantity = sum(float(row["quantity"] or 0) for row in batches)
    obligation_quantity = sum(float(row["pending_quantity"] or 0) for row in obligations)
    target_quantity = temporary_quantity + obligation_quantity
    if target_quantity <= 1e-9:
        raise TransferConflict("该物料没有可转移的临时库存或待结算领用记录")

    transfer_no = next_table_no(
        cursor, "inventory_transfer_tasks", "transfer_no", "ZY"
    )
    timestamp = now_text()
    cursor.execute(
        """
        INSERT INTO inventory_transfer_tasks (
            transfer_no, material_id, requested_by, status,
            temporary_quantity_snapshot, obligation_quantity_snapshot,
            target_acceptance_quantity, accepted_quantity, active_key,
            idempotency_key, version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 0, ?, ?)
        """,
        (
            transfer_no,
            material_id,
            int(user["id"]),
            TRANSFER_STATUS_AWAITING_PURCHASE,
            temporary_quantity,
            obligation_quantity,
            target_quantity,
            active_transfer_key(material_id),
            key,
            timestamp,
            timestamp,
        ),
    )
    task_id = int(cursor.lastrowid)
    for batch in batches:
        cursor.execute(
            """
            INSERT INTO inventory_transfer_items (
                task_id, source_batch_id, material_id, quantity_snapshot,
                inventory_status_snapshot, batch_version_snapshot, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                int(batch["id"]),
                material_id,
                float(batch["quantity"]),
                batch["inventory_status"],
                int(batch["version"] or 0),
                timestamp,
                timestamp,
            ),
        )
    for obligation in obligations:
        cursor.execute(
            """
            INSERT INTO inventory_transfer_obligations (
                task_id, obligation_id, pending_quantity_snapshot,
                applicant_id, material_id, source_batch_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                int(obligation["id"]),
                float(obligation["pending_quantity"]),
                int(obligation["applicant_id"]),
                material_id,
                int(obligation["source_batch_id"]),
                timestamp,
            ),
        )
    for batch in batches:
        cursor.execute(
            """
            UPDATE material_batches
            SET inventory_status = ?, version = version + 1, updated_at = ?
            WHERE id = ? AND material_id = ? AND stock_source = ?
              AND inventory_status = ? AND version = ? AND quantity = ?
            """,
            (
                INVENTORY_STATUS_TRANSFER_LOCKED,
                timestamp,
                int(batch["id"]),
                material_id,
                STOCK_SOURCE_TEMPORARY,
                INVENTORY_STATUS_AVAILABLE,
                int(batch["version"] or 0),
                float(batch["quantity"]),
            ),
        )
        if cursor.rowcount != 1:
            raise TransferConflict("临时批次状态已变化，请刷新后重试")

    task = serialize_transfer_task(cursor, task_id, user)
    write_audit_log(
        cursor,
        user,
        "temporary_transfer.created",
        "inventory_transfer_task",
        task_id,
        "发起临时物料转正式库",
        {
            "material_id": material_id,
            "temporary_quantity": temporary_quantity,
            "obligation_quantity": obligation_quantity,
            "target_quantity": target_quantity,
            "batch_ids": [int(row["id"]) for row in batches],
            "old_status": "",
            "new_status": TRANSFER_STATUS_AWAITING_PURCHASE,
        },
        ip_address,
    )
    notify_transfer_event(
        cursor,
        task,
        "created",
        _user_ids_with_permission(cursor, "process_temporary_transfer"),
        (
            f"{material.get('name') or ''}（{material.get('material_code') or ''}）"
            f"需要转正式库；临时现存 {temporary_quantity:g}，"
            f"历史待结算 {obligation_quantity:g}，目标验收 {target_quantity:g}。"
        ),
    )
    task["idempotent"] = False
    return task


def list_transfer_tasks(
    cursor,
    user,
    page=1,
    page_size=20,
    status="",
    material_id=0,
    assigned_to_me=False,
    q="",
):
    if not (_is_admin(user) or _can_process(cursor, user) or _can_request(cursor, user)):
        raise PermissionError("当前账号无权查看转移任务")
    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 20)))
    where = []
    params = []
    if not (_is_admin(user) or _can_process(cursor, user)):
        where.append("t.requested_by = ?")
        params.append(int(user["id"]))
    if status:
        where.append("t.status = ?")
        params.append(str(status))
    if material_id:
        where.append("t.material_id = ?")
        params.append(int(material_id))
    if assigned_to_me:
        where.append("t.assigned_buyer_id = ?")
        params.append(int(user["id"]))
    keyword = str(q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        where.append(
            "(t.transfer_no LIKE ? OR m.material_code LIKE ? OR m.name LIKE ? OR m.spec LIKE ?)"
        )
        params.extend([like] * 4)
    where_sql = " AND ".join(where) if where else "1 = 1"
    cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM inventory_transfer_tasks t
        JOIN materials m ON m.id = t.material_id
        WHERE {where_sql}
        """,
        params,
    )
    total = int(cursor.fetchone()[0] or 0)
    cursor.execute(
        f"""
        SELECT t.id
        FROM inventory_transfer_tasks t
        JOIN materials m ON m.id = t.material_id
        WHERE {where_sql}
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, page_size, (page - 1) * page_size],
    )
    items = [
        serialize_transfer_task(cursor, int(row["id"]), user)
        for row in cursor.fetchall()
    ]
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


def claim_transfer_task(cursor, task_id, user, ip_address=""):
    begin_inventory_transaction(cursor.connection)
    task = _task_row(cursor, task_id)
    if not _can_process(cursor, user):
        raise PermissionError("当前账号没有处理转移任务的权限")
    assigned = int(task.get("assigned_buyer_id") or 0)
    if assigned == int(user["id"]):
        result = serialize_transfer_task(cursor, task_id, user)
        result["idempotent"] = True
        return result
    if assigned:
        raise TransferConflict("任务已被其他采购人员认领")
    if task["status"] != TRANSFER_STATUS_AWAITING_PURCHASE:
        raise TransferConflict("当前任务状态不能认领")
    cursor.execute(
        """
        UPDATE inventory_transfer_tasks
        SET assigned_buyer_id = ?, claimed_at = ?,
            version = version + 1, updated_at = ?
        WHERE id = ? AND assigned_buyer_id IS NULL AND status = ?
        """,
        (
            int(user["id"]),
            now_text(),
            now_text(),
            int(task_id),
            TRANSFER_STATUS_AWAITING_PURCHASE,
        ),
    )
    if cursor.rowcount != 1:
        raise TransferConflict("任务已被其他采购人员认领")
    updated = serialize_transfer_task(cursor, task_id, user)
    write_audit_log(
        cursor,
        user,
        "temporary_transfer.claimed",
        "inventory_transfer_task",
        task_id,
        "采购人员认领转移任务",
        {
            "material_id": task["material_id"],
            "old_status": task["status"],
            "new_status": task["status"],
            "assigned_buyer_id": user["id"],
        },
        ip_address,
    )
    recipients = set(
        _user_ids_with_permission(cursor, "transfer_temporary_inventory")
    )
    recipients.update({int(task["requested_by"]), int(user["id"])})
    notify_transfer_event(cursor, updated, "claimed", recipients)
    updated["idempotent"] = False
    return updated


def cancel_transfer_task(cursor, task_id, user, reason="", ip_address=""):
    begin_inventory_transaction(cursor.connection)
    task = _task_row(cursor, task_id)
    if task["status"] == TRANSFER_STATUS_CANCELLED:
        result = serialize_transfer_task(cursor, task_id, user)
        result["idempotent"] = True
        return result
    can_cancel = _is_admin(user) or (
        _can_request(cursor, user)
        and int(task["requested_by"]) == int(user.get("id") or 0)
    )
    if not can_cancel:
        raise PermissionError("当前账号无权取消该转移任务")
    if task["status"] not in {
        TRANSFER_STATUS_AWAITING_PURCHASE,
        TRANSFER_STATUS_ACCEPTANCE_FAILED,
    }:
        raise TransferConflict("当前任务状态不允许取消")
    if float(task["accepted_quantity"] or 0) > 1e-9:
        raise TransferConflict("任务已有正式入库事实，不能取消")
    if _in_progress_link_count(cursor, task_id):
        raise TransferConflict("任务存在进行中的验收流程，不能取消")
    timestamp = now_text()
    cursor.execute(
        """
        UPDATE inventory_transfer_tasks
        SET status = ?, active_key = NULL, cancelled_at = ?,
            error_code = '', error_message = ?,
            version = version + 1, updated_at = ?
        WHERE id = ? AND status IN (?, ?) AND accepted_quantity = 0
        """,
        (
            TRANSFER_STATUS_CANCELLED,
            timestamp,
            str(reason or ""),
            timestamp,
            int(task_id),
            TRANSFER_STATUS_AWAITING_PURCHASE,
            TRANSFER_STATUS_ACCEPTANCE_FAILED,
        ),
    )
    if cursor.rowcount != 1:
        raise TransferConflict("任务状态已变化，请刷新后重试")
    cursor.execute(
        """
        SELECT source_batch_id, batch_version_snapshot
        FROM inventory_transfer_items
        WHERE task_id = ?
        ORDER BY id
        """,
        (int(task_id),),
    )
    for item in cursor.fetchall():
        cursor.execute(
            """
            UPDATE material_batches
            SET inventory_status = ?, version = version + 1, updated_at = ?
            WHERE id = ? AND stock_source = ?
              AND inventory_status = ? AND version = ?
            """,
            (
                INVENTORY_STATUS_AVAILABLE,
                timestamp,
                int(item["source_batch_id"]),
                STOCK_SOURCE_TEMPORARY,
                INVENTORY_STATUS_TRANSFER_LOCKED,
                int(item["batch_version_snapshot"] or 0) + 1,
            ),
        )
        if cursor.rowcount != 1:
            raise TransferConflict("锁定批次状态已变化，取消操作已回滚")
    updated = serialize_transfer_task(cursor, task_id, user)
    write_audit_log(
        cursor,
        user,
        "temporary_transfer.cancelled",
        "inventory_transfer_task",
        task_id,
        "取消临时物料转正式库任务并解除批次锁定",
        {
            "material_id": task["material_id"],
            "old_status": task["status"],
            "new_status": TRANSFER_STATUS_CANCELLED,
            "reason": str(reason or ""),
        },
        ip_address,
    )
    recipients = {int(task["requested_by"])}
    if task.get("assigned_buyer_id"):
        recipients.add(int(task["assigned_buyer_id"]))
    notify_transfer_event(cursor, updated, "cancelled", recipients)
    updated["idempotent"] = False
    return updated


def pause_active_transfer_tasks(cursor, user=None, ip_address=""):
    cursor.execute(
        """
        SELECT id, status
        FROM inventory_transfer_tasks
        WHERE active_key IS NOT NULL AND status NOT IN (?, ?)
        ORDER BY id
        """,
        (TRANSFER_STATUS_PAUSED, TRANSFER_STATUS_CANCELLED),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    timestamp = now_text()
    for row in rows:
        cursor.execute(
            """
            UPDATE inventory_transfer_tasks
            SET paused_from_status = status, status = ?,
                version = version + 1, error_code = 'feature_disabled',
                error_message = '临时库功能已关闭，任务处理已暂停',
                updated_at = ?
            WHERE id = ? AND status = ?
            """,
            (
                TRANSFER_STATUS_PAUSED,
                timestamp,
                int(row["id"]),
                row["status"],
            ),
        )
        if cursor.rowcount == 1 and user:
            write_audit_log(
                cursor,
                user,
                "temporary_transfer.paused",
                "inventory_transfer_task",
                row["id"],
                "关闭临时库功能，暂停转移任务",
                {
                    "old_status": row["status"],
                    "new_status": TRANSFER_STATUS_PAUSED,
                },
                ip_address,
            )
    return len(rows)

def _task_recipients(task):
    recipients = {int(task["requested_by"])}
    if task.get("assigned_buyer_id"):
        recipients.add(int(task["assigned_buyer_id"]))
    return recipients


def start_transfer_acceptance(
    cursor,
    task_id,
    user,
    data,
    idempotency_key,
    ip_address="",
):
    key = str(idempotency_key or "").strip()
    if not key:
        raise ValueError("idempotency_key 不能为空")
    begin_inventory_transaction(cursor.connection)
    if not temporary_inventory_enabled(cursor):
        raise TransferConflict("临时库功能已关闭")
    task = _task_row(cursor, task_id)
    if not _can_process(cursor, user):
        raise PermissionError("当前账号没有处理转移任务的权限")
    operation_key = f"transfer_acceptance:{int(task_id)}:{key}"
    cursor.execute(
        """
        SELECT acceptance_form_id
        FROM transfer_acceptance_links
        WHERE operation_key = ?
        """,
        (operation_key,),
    )
    existing = cursor.fetchone()
    if existing:
        result = serialize_transfer_task(cursor, task_id, user)
        result["acceptance_form_id"] = int(existing["acceptance_form_id"])
        result["idempotent"] = True
        return result

    assigned = int(task.get("assigned_buyer_id") or 0)
    if not assigned and _is_admin(user):
        cursor.execute(
            """
            UPDATE inventory_transfer_tasks
            SET assigned_buyer_id = ?, claimed_at = ?, version = version + 1,
                updated_at = ?
            WHERE id = ? AND assigned_buyer_id IS NULL
            """,
            (int(user["id"]), now_text(), now_text(), int(task_id)),
        )
        assigned = int(user["id"])
    if not (_is_admin(user) or assigned == int(user["id"])):
        raise PermissionError("只有任务认领人可以发起验收")
    if task["status"] not in {
        TRANSFER_STATUS_AWAITING_PURCHASE,
        TRANSFER_STATUS_ACCEPTANCE_FAILED,
        TRANSFER_STATUS_FORMAL_INBOUND_PARTIAL,
    }:
        raise TransferConflict("当前任务状态不能发起验收")
    if _in_progress_link_count(cursor, task_id):
        raise TransferConflict("该任务已有进行中的验收流程")

    remaining = float(task["target_acceptance_quantity"] or 0) - float(
        task["accepted_quantity"] or 0
    )
    if remaining <= 1e-9:
        raise TransferConflict("转移任务已达到目标验收数量")
    acceptance_data = dict(data or {})
    created = create_acceptance_workflow(
        cursor,
        user,
        [
            {
                "material_id": int(task["material_id"]),
                "purchase_quantity": remaining,
                "arrival_quantity": remaining,
                "unit_price": acceptance_data.get("unit_price") or 0,
                "purchase_applicant": acceptance_data.get("purchase_applicant")
                or task.get("requested_by_name")
                or "",
                "attachment_tokens": acceptance_data.get("attachment_tokens") or [],
                "data": {
                    "transfer_task_id": int(task_id),
                    "transfer_no": task["transfer_no"],
                    "system_target_quantity": remaining,
                },
            }
        ],
        validator_ids=acceptance_data.get("validator_ids") or [],
        origin_type=TRANSFER_ORIGIN_TYPE,
        origin_ref_id=int(task_id),
        form_data={
            "transfer_task_id": int(task_id),
            "transfer_no": task["transfer_no"],
            "temporary_quantity_snapshot": float(
                task["temporary_quantity_snapshot"] or 0
            ),
            "obligation_quantity_snapshot": float(
                task["obligation_quantity_snapshot"] or 0
            ),
            "target_acceptance_quantity": float(
                task["target_acceptance_quantity"] or 0
            ),
            "accepted_quantity_before": float(task["accepted_quantity"] or 0),
        },
    )
    acceptance_item_id = int(created["item_ids"][0])
    timestamp = now_text()
    cursor.execute(
        """
        INSERT INTO transfer_acceptance_links (
            task_id, acceptance_form_id, acceptance_item_id,
            linked_quantity, status, operation_key, created_at, updated_at
        ) VALUES (?, ?, ?, 0, 'in_progress', ?, ?, ?)
        """,
        (
            int(task_id),
            int(created["form_id"]),
            acceptance_item_id,
            operation_key,
            timestamp,
            timestamp,
        ),
    )
    cursor.execute(
        """
        UPDATE inventory_transfer_tasks
        SET status = ?, assigned_buyer_id = COALESCE(assigned_buyer_id, ?),
            acceptance_started_at = ?, error_code = '', error_message = '',
            version = version + 1, updated_at = ?
        WHERE id = ? AND status IN (?, ?, ?)
        """,
        (
            TRANSFER_STATUS_ACCEPTANCE_IN_PROGRESS,
            int(user["id"]),
            timestamp,
            timestamp,
            int(task_id),
            TRANSFER_STATUS_AWAITING_PURCHASE,
            TRANSFER_STATUS_ACCEPTANCE_FAILED,
            TRANSFER_STATUS_FORMAL_INBOUND_PARTIAL,
        ),
    )
    if cursor.rowcount != 1:
        raise TransferConflict("任务状态已变化，验收创建已回滚")
    updated = serialize_transfer_task(cursor, task_id, user)
    write_audit_log(
        cursor,
        user,
        "temporary_transfer.acceptance_started",
        "inventory_transfer_task",
        task_id,
        "从转移任务发起验收流程",
        {
            "material_id": task["material_id"],
            "old_status": task["status"],
            "new_status": TRANSFER_STATUS_ACCEPTANCE_IN_PROGRESS,
            "quantity": remaining,
            "acceptance_form_id": created["form_id"],
        },
        ip_address,
    )
    notify_transfer_event(
        cursor,
        updated,
        "acceptance_started",
        _task_recipients(updated),
    )
    updated["acceptance_form_id"] = int(created["form_id"])
    updated["acceptance_form_no"] = created["form_no"]
    updated["idempotent"] = False
    return updated


def _accepted_quantity(cursor, task_id):
    cursor.execute(
        """
        SELECT COALESCE(SUM(linked_quantity), 0)
        FROM transfer_acceptance_links
        WHERE task_id = ? AND status = 'inbound'
        """,
        (int(task_id),),
    )
    return float(cursor.fetchone()[0] or 0)


def _set_transfer_status(
    cursor,
    task,
    status,
    accepted_quantity,
    error_code="",
    error_message="",
):
    timestamp = now_text()
    cursor.execute(
        """
        UPDATE inventory_transfer_tasks
        SET status = ?, accepted_quantity = ?, error_code = ?, error_message = ?,
            formal_inbound_at = CASE WHEN ? = ? THEN ? ELSE formal_inbound_at END,
            version = version + 1, updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            float(accepted_quantity),
            str(error_code or ""),
            str(error_message or ""),
            status,
            TRANSFER_STATUS_FORMAL_INBOUND_COMPLETE,
            timestamp,
            timestamp,
            int(task["id"]),
        ),
    )


def mark_transfer_acceptance_failed(
    cursor,
    acceptance_form_id,
    reason,
    user=None,
    ip_address="",
):
    cursor.execute(
        """
        SELECT l.task_id
        FROM transfer_acceptance_links l
        WHERE l.acceptance_form_id = ?
        ORDER BY l.id DESC
        LIMIT 1
        """,
        (int(acceptance_form_id),),
    )
    link = cursor.fetchone()
    if not link:
        return None
    task = _task_row(cursor, int(link["task_id"]))
    cursor.execute(
        """
        UPDATE transfer_acceptance_links
        SET status = 'failed', updated_at = ?
        WHERE acceptance_form_id = ? AND status = 'in_progress'
        """,
        (now_text(), int(acceptance_form_id)),
    )
    accepted = _accepted_quantity(cursor, task["id"])
    status = (
        TRANSFER_STATUS_FORMAL_INBOUND_PARTIAL
        if accepted > 1e-9
        else TRANSFER_STATUS_ACCEPTANCE_FAILED
    )
    _set_transfer_status(
        cursor,
        task,
        status,
        accepted,
        "acceptance_failed",
        str(reason or "关联验收未通过"),
    )
    updated = serialize_transfer_task(cursor, task["id"])
    if user:
        write_audit_log(
            cursor,
            user,
            "temporary_transfer.acceptance_failed",
            "inventory_transfer_task",
            task["id"],
            "关联验收失败",
            {
                "material_id": task["material_id"],
                "old_status": task["status"],
                "new_status": status,
                "acceptance_form_id": int(acceptance_form_id),
                "reason": str(reason or ""),
            },
            ip_address,
        )
    notify_transfer_event(
        cursor,
        updated,
        "formal_inbound_partial" if accepted > 1e-9 else "acceptance_failed",
        _task_recipients(updated),
    )
    return updated


def record_transfer_formal_inbound(
    cursor,
    acceptance_form_id,
    acceptance_item_id,
    formal_batch_id,
    quantity,
    user=None,
    ip_address="",
):
    cursor.execute(
        """
        SELECT l.*, t.material_id
        FROM transfer_acceptance_links l
        JOIN inventory_transfer_tasks t ON t.id = l.task_id
        WHERE l.acceptance_form_id = ? AND l.acceptance_item_id = ?
        """,
        (int(acceptance_form_id), int(acceptance_item_id)),
    )
    link = cursor.fetchone()
    if not link:
        return None
    link = dict(link)
    quantity = float(quantity or 0)
    cursor.execute(
        """
        SELECT material_id, stock_source
        FROM material_batches
        WHERE id = ?
        """,
        (int(formal_batch_id),),
    )
    batch = cursor.fetchone()
    if (
        not batch
        or int(batch["material_id"]) != int(link["material_id"])
        or batch["stock_source"] != STOCK_SOURCE_FORMAL
    ):
        raise TransferConflict("转移验收必须关联同一物料的正式库存批次")
    if link.get("formal_batch_id"):
        if (
            int(link["formal_batch_id"]) == int(formal_batch_id)
            and abs(float(link["linked_quantity"] or 0) - quantity) <= 1e-9
        ):
            return serialize_transfer_task(cursor, int(link["task_id"]))
        raise TransferConflict("验收关联已绑定其他正式批次")

    cursor.execute(
        """
        UPDATE transfer_acceptance_links
        SET formal_batch_id = ?, linked_quantity = ?, status = 'inbound',
            updated_at = ?
        WHERE id = ? AND formal_batch_id IS NULL AND status = 'in_progress'
        """,
        (int(formal_batch_id), quantity, now_text(), int(link["id"])),
    )
    if cursor.rowcount != 1:
        raise TransferConflict("验收入库关联状态已变化，请刷新后重试")
    task = _task_row(cursor, int(link["task_id"]))
    accepted = _accepted_quantity(cursor, task["id"])
    target = float(task["target_acceptance_quantity"] or 0)
    if accepted > target + 1e-9:
        status = TRANSFER_STATUS_EXCEPTION
        error_code = "acceptance_over_target"
        error_message = "正式入库累计数量超过转移目标，需人工核对"
        event = "exception"
    elif not temporary_inventory_enabled(cursor):
        status = TRANSFER_STATUS_PAUSED
        error_code = "feature_disabled"
        error_message = "正式入库已记录，临时库功能关闭，后续同步已暂停"
        event = ""
    elif accepted >= target - 1e-9:
        status = TRANSFER_STATUS_FORMAL_INBOUND_COMPLETE
        error_code = ""
        error_message = ""
        event = "formal_inbound_complete"
    else:
        status = TRANSFER_STATUS_FORMAL_INBOUND_PARTIAL
        error_code = ""
        error_message = ""
        event = "formal_inbound_partial"
    _set_transfer_status(
        cursor,
        task,
        status,
        accepted,
        error_code,
        error_message,
    )
    updated = serialize_transfer_task(cursor, task["id"])
    if user:
        write_audit_log(
            cursor,
            user,
            f"temporary_transfer.{status}",
            "inventory_transfer_task",
            task["id"],
            "关联验收正式入库",
            {
                "material_id": task["material_id"],
                "old_status": task["status"],
                "new_status": status,
                "quantity": quantity,
                "accepted_quantity": accepted,
                "acceptance_form_id": int(acceptance_form_id),
                "formal_batch_id": int(formal_batch_id),
            },
            ip_address,
        )
    if event:
        notify_transfer_event(cursor, updated, event, _task_recipients(updated))
    return updated


def sync_transfer_task(cursor, task_id, user=None, ip_address=""):
    task = _task_row(cursor, task_id)
    cursor.execute(
        """
        UPDATE transfer_acceptance_links
        SET status = 'failed', updated_at = ?
        WHERE task_id = ? AND status = 'in_progress'
          AND acceptance_form_id IN (
              SELECT id FROM workflow_forms
              WHERE status IN ('rejected', 'cancelled')
          )
        """,
        (now_text(), int(task_id)),
    )
    accepted = _accepted_quantity(cursor, task_id)
    target = float(task["target_acceptance_quantity"] or 0)
    if not temporary_inventory_enabled(cursor):
        status = TRANSFER_STATUS_PAUSED
        code = "feature_disabled"
        message = "临时库功能已关闭，任务处理已暂停"
    elif accepted > target + 1e-9:
        status = TRANSFER_STATUS_EXCEPTION
        code = "acceptance_over_target"
        message = "正式入库累计数量超过转移目标，需人工核对"
    elif accepted >= target - 1e-9:
        status = TRANSFER_STATUS_FORMAL_INBOUND_COMPLETE
        code = ""
        message = ""
    elif accepted > 1e-9:
        status = TRANSFER_STATUS_FORMAL_INBOUND_PARTIAL
        code = ""
        message = ""
    elif _in_progress_link_count(cursor, task_id):
        status = TRANSFER_STATUS_ACCEPTANCE_IN_PROGRESS
        code = ""
        message = ""
    else:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM transfer_acceptance_links
            WHERE task_id = ? AND status = 'failed'
            """,
            (int(task_id),),
        )
        if int(cursor.fetchone()[0] or 0):
            status = TRANSFER_STATUS_ACCEPTANCE_FAILED
            code = "acceptance_failed"
            message = task.get("error_message") or "关联验收未通过"
        else:
            status = TRANSFER_STATUS_AWAITING_PURCHASE
            code = ""
            message = ""
    _set_transfer_status(cursor, task, status, accepted, code, message)
    updated = serialize_transfer_task(cursor, task_id, user)
    if user:
        write_audit_log(
            cursor,
            user,
            "temporary_transfer.synced",
            "inventory_transfer_task",
            task_id,
            "同步临时物料转正式库任务状态",
            {
                "material_id": task["material_id"],
                "old_status": task["status"],
                "new_status": status,
                "accepted_quantity": accepted,
            },
            ip_address,
        )
    return updated


def resume_paused_transfer_tasks(cursor, user=None, ip_address=""):
    cursor.execute(
        """
        SELECT id, paused_from_status
        FROM inventory_transfer_tasks
        WHERE active_key IS NOT NULL AND status = ?
        ORDER BY id
        """,
        (TRANSFER_STATUS_PAUSED,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    settlement_statuses = {
        TRANSFER_STATUS_RESERVING,
        TRANSFER_STATUS_AUTO_CLAIM_CREATING,
        TRANSFER_STATUS_AUTO_CLAIM_PENDING,
        TRANSFER_STATUS_AUTO_CLAIM_EXCEPTION,
    }
    for row in rows:
        previous = str(row.get("paused_from_status") or "")
        if previous in settlement_statuses:
            cursor.execute(
                """
                UPDATE inventory_transfer_tasks
                SET status = ?, paused_from_status = NULL,
                    error_code = '', error_message = '',
                    version = version + 1, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    previous,
                    now_text(),
                    int(row["id"]),
                    TRANSFER_STATUS_PAUSED,
                ),
            )
        else:
            sync_transfer_task(cursor, int(row["id"]), user, ip_address)
    return len(rows)
