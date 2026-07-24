"""Transfer settlement, reserved outbound, and finalization services."""

from __future__ import annotations

from warehouse_suit.borrow_service import has_active_temporary_borrows
from warehouse_suit.claim_service import create_claim_workflow
from warehouse_suit.db import now_text, today_text
from warehouse_suit.inventory_constants import (
    AUTO_CLAIM_ORIGIN_TYPE,
    BUSINESS_TYPE_CLAIM_OUTBOUND,
    BUSINESS_TYPE_TEMPORARY_TRANSFER_CLOSE,
    INVENTORY_STATUS_AVAILABLE,
    INVENTORY_STATUS_TRANSFER_LOCKED,
    INVENTORY_STATUS_TRANSFERRED,
    STOCK_SOURCE_FORMAL,
    STOCK_SOURCE_TEMPORARY,
)
from warehouse_suit.inventory_service import begin_inventory_transaction, update_inventory_total
from warehouse_suit.material_repository import material_snapshot
from warehouse_suit.reservation_service import batch_reserved_quantity
from warehouse_suit.settings import temporary_inventory_enabled
from warehouse_suit.temporary_inventory_service import write_audit_log
from warehouse_suit.transfer_constants import (
    TRANSFER_STATUS_AUTO_CLAIM_CREATING,
    TRANSFER_STATUS_AUTO_CLAIM_EXCEPTION,
    TRANSFER_STATUS_AUTO_CLAIM_PENDING,
    TRANSFER_STATUS_COMPLETED,
    TRANSFER_STATUS_FORMAL_INBOUND_COMPLETE,
    TRANSFER_STATUS_PAUSED,
    TRANSFER_STATUS_RESERVING,
)
from warehouse_suit.transfer_service import (
    TransferConflict,
    _task_recipients,
    _task_row,
    _user_ids_with_permission,
    notify_transfer_event,
    serialize_transfer_task,
)
from warehouse_suit.workflow_service import resolve_department_leader, user_id_has_permission


EPSILON = 1e-9


def _set_task_status(cursor, task_id, status, code="", message=""):
    cursor.execute(
        """
        UPDATE inventory_transfer_tasks
        SET status = ?, error_code = ?, error_message = ?,
            version = version + 1, updated_at = ?
        WHERE id = ?
        """,
        (status, str(code or ""), str(message or ""), now_text(), int(task_id)),
    )


def _active_user(cursor, user_id):
    cursor.execute("SELECT * FROM users WHERE id = ?", (int(user_id),))
    row = cursor.fetchone()
    if not row:
        raise TransferConflict(f"历史临时领用用户 {int(user_id)} 不存在")
    user = dict(row)
    if not int(user.get("is_active") or 0):
        raise TransferConflict(f"用户 {user.get('display_name') or user.get('username')} 已停用")
    if not user_id_has_permission(cursor, int(user_id), "start_claim"):
        label = user.get("display_name") or user.get("username") or int(user_id)
        raise TransferConflict(f"用户 {label} 没有领用申请权限")
    return user


def _snapshot_obligations(cursor, task):
    cursor.execute(
        """
        SELECT x.obligation_id, x.pending_quantity_snapshot, x.applicant_id,
               o.material_id, o.issued_quantity, o.settled_quantity, o.status
        FROM inventory_transfer_obligations x
        JOIN temporary_issue_obligations o ON o.id = x.obligation_id
        WHERE x.task_id = ?
        ORDER BY x.applicant_id, x.obligation_id
        """,
        (int(task["id"]),),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    total = 0.0
    for row in rows:
        pending = float(row["issued_quantity"] or 0) - float(row["settled_quantity"] or 0)
        snapshot = float(row["pending_quantity_snapshot"] or 0)
        if int(row["material_id"]) != int(task["material_id"]):
            raise TransferConflict("待结算记录物料与转移任务不一致")
        if row["status"] not in {"pending", "reserved", "processing"}:
            raise TransferConflict(f"待结算记录 {row['obligation_id']} 状态不可结算")
        if abs(pending - snapshot) > 1e-6:
            raise TransferConflict(f"待结算记录 {row['obligation_id']} 数量已变化")
        total += snapshot
    if abs(total - float(task["obligation_quantity_snapshot"] or 0)) > 1e-6:
        raise TransferConflict("转移任务待结算快照总量不一致")
    return rows


def _formal_batches(cursor, task):
    cursor.execute(
        """
        SELECT l.formal_batch_id, l.linked_quantity, b.material_id, b.quantity,
               b.unit_price, b.received_date, b.stock_source, b.inventory_status
        FROM transfer_acceptance_links l
        JOIN material_batches b ON b.id = l.formal_batch_id
        WHERE l.task_id = ? AND l.formal_batch_id IS NOT NULL
          AND l.status = 'inbound'
        ORDER BY b.received_date, b.id, l.id
        """,
        (int(task["id"]),),
    )
    result = []
    seen = set()
    for raw in cursor.fetchall():
        row = dict(raw)
        batch_id = int(row["formal_batch_id"])
        if batch_id in seen:
            continue
        seen.add(batch_id)
        if (
            int(row["material_id"]) != int(task["material_id"])
            or row["stock_source"] != STOCK_SOURCE_FORMAL
            or row["inventory_status"] != INVENTORY_STATUS_AVAILABLE
        ):
            raise TransferConflict("关联正式批次来源、状态或物料不符合预留条件")
        row["reservable_quantity"] = max(
            0.0,
            min(float(row["quantity"] or 0), float(row["linked_quantity"] or 0))
            - batch_reserved_quantity(cursor, batch_id),
        )
        result.append(row)
    if not result:
        raise TransferConflict("转移任务没有可用于结算的关联正式批次")
    return result


def mark_auto_claim_exception(cursor, task_id, code, message, user=None, ip_address=""):
    begin_inventory_transaction(cursor.connection)
    task = _task_row(cursor, task_id)
    if task["status"] == TRANSFER_STATUS_COMPLETED:
        return serialize_transfer_task(cursor, task_id)
    _set_task_status(cursor, task_id, TRANSFER_STATUS_AUTO_CLAIM_EXCEPTION, code, message)
    if user:
        write_audit_log(
            cursor,
            user,
            "temporary_transfer.auto_claim_exception",
            "inventory_transfer_task",
            task_id,
            "自动领用结算异常",
            {"old_status": task["status"], "error_code": code, "error_message": message},
            ip_address,
        )
    updated = serialize_transfer_task(cursor, task_id)
    notify_transfer_event(
        cursor,
        updated,
        "auto_claim_exception",
        _task_recipients(updated),
        str(message or "自动领用结算异常"),
    )
    return updated


def _create_auto_claims(cursor, task, obligations):
    grouped = {}
    for row in obligations:
        grouped.setdefault(int(row["applicant_id"]), []).append(row)
    users = {}
    leaders = {}
    for applicant_id in sorted(grouped):
        users[applicant_id] = _active_user(cursor, applicant_id)
        leaders[applicant_id] = resolve_department_leader(
            cursor, users[applicant_id], "claim", "leader_claim"
        )
    timestamp = now_text()
    claims = []
    for applicant_id in sorted(grouped):
        quantity = sum(float(row["pending_quantity_snapshot"] or 0) for row in grouped[applicant_id])
        key = f"transfer_auto_claim:{int(task['id'])}:{applicant_id}"
        cursor.execute(
            """
            INSERT INTO transfer_auto_claims (
                task_id, applicant_id, material_id, quantity, status, attempt_no,
                idempotency_key, active_key, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'pending_create', 0, ?, ?, ?, ?)
            """,
            (
                int(task["id"]), applicant_id, int(task["material_id"]), quantity,
                key, key, timestamp, timestamp,
            ),
        )
        claim_id = int(cursor.lastrowid)
        for obligation in grouped[applicant_id]:
            cursor.execute(
                """
                INSERT INTO transfer_auto_claim_obligations (
                    auto_claim_id, task_id, obligation_id, settlement_quantity,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    claim_id, int(task["id"]), int(obligation["obligation_id"]),
                    float(obligation["pending_quantity_snapshot"]), timestamp, timestamp,
                ),
            )
        claims.append(
            {
                "id": claim_id,
                "applicant_id": applicant_id,
                "applicant": users[applicant_id],
                "leader_id": leaders[applicant_id],
                "quantity": quantity,
                "obligations": grouped[applicant_id],
                "attempt_no": 0,
            }
        )
    return claims


def _reserve_batches(cursor, task, claims, batches):
    capacities = {
        int(row["formal_batch_id"]): float(row["reservable_quantity"])
        for row in batches
    }
    required = sum(float(row["quantity"]) for row in claims)
    if sum(capacities.values()) + EPSILON < required:
        raise TransferConflict("本次转移关联正式批次数量不足，不能覆盖待结算领用")
    result = {int(row["id"]): [] for row in claims}
    timestamp = now_text()
    for claim in sorted(claims, key=lambda row: int(row["applicant_id"])):
        remain = float(claim["quantity"])
        for batch in batches:
            if remain <= EPSILON:
                break
            batch_id = int(batch["formal_batch_id"])
            take = min(remain, capacities[batch_id])
            if take <= EPSILON:
                continue
            key = f"transfer_reservation:{int(task['id'])}:{int(claim['id'])}:{batch_id}"
            cursor.execute(
                """
                INSERT INTO inventory_reservations (
                    task_id, auto_claim_id, formal_batch_id, material_id,
                    applicant_id, reserved_quantity, status, operation_key,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    int(task["id"]), int(claim["id"]), batch_id,
                    int(task["material_id"]), int(claim["applicant_id"]),
                    take, key, timestamp, timestamp,
                ),
            )
            result[int(claim["id"])].append(
                {"id": int(cursor.lastrowid), "formal_batch_id": batch_id, "quantity": take}
            )
            capacities[batch_id] -= take
            remain -= take
        if remain > EPSILON:
            raise TransferConflict(f"用户 {claim['applicant_id']} 的正式库存预留不足")
    if abs(
        sum(float(item["quantity"]) for rows in result.values() for item in rows)
        - required
    ) > 1e-6:
        raise TransferConflict("正式库存预留总量与待结算数量不一致")
    return result


def _create_workflows(cursor, task, claims, reservations):
    snapshot = material_snapshot(cursor, int(task["material_id"]), stock_source=STOCK_SOURCE_FORMAL)
    if not snapshot:
        raise TransferConflict("自动领用物料主数据不存在")
    for claim in claims:
        reservation_ids = [int(row["id"]) for row in reservations[int(claim["id"])]]
        obligation_ids = [int(row["obligation_id"]) for row in claim["obligations"]]
        allocation = {
            "material_id": int(task["material_id"]),
            "material": snapshot,
            "request_quantity": float(claim["quantity"]),
            "stock_source": STOCK_SOURCE_FORMAL,
            "data": {
                "transfer_task_id": int(task["id"]),
                "transfer_auto_claim_id": int(claim["id"]),
                "reservation_ids": reservation_ids,
                "obligation_ids": obligation_ids,
                "allocation_group_key": f"transfer-auto-claim:{int(task['id'])}:{int(claim['applicant_id'])}",
                "requested_quantity_snapshot": float(claim["quantity"]),
                "auto_generated": True,
                "immutable": True,
            },
        }
        attempt = int(claim.get("attempt_no") or 0) + 1
        created = create_claim_workflow(
            cursor,
            claim["applicant"],
            [allocation],
            int(claim["leader_id"]),
            leader_ids=[int(claim["leader_id"])],
            origin_type=AUTO_CLAIM_ORIGIN_TYPE,
            origin_ref_id=int(task["id"]),
            metadata={
                "transfer_task_id": int(task["id"]),
                "transfer_auto_claim_id": int(claim["id"]),
                "auto_claim_attempt_no": attempt,
            },
            immutable=True,
        )
        timestamp = now_text()
        cursor.execute(
            """
            UPDATE transfer_auto_claims
            SET current_claim_form_id = ?, status = 'approval_pending',
                attempt_no = ?, workflow_created_at = ?, updated_at = ?,
                error_code = '', error_message = ''
            WHERE id = ? AND status = 'pending_create'
            """,
            (
                int(created["form_id"]), attempt, timestamp, timestamp, int(claim["id"]),
            ),
        )
        if cursor.rowcount != 1:
            raise TransferConflict("自动领用逻辑状态已变化")


def _temporary_total(cursor, material_id):
    cursor.execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM material_batches WHERE material_id = ? AND stock_source = ?",
        (int(material_id), STOCK_SOURCE_TEMPORARY),
    )
    return float(cursor.fetchone()[0] or 0)


def _finalize(cursor, task, user=None, ip_address=""):
    task = _task_row(cursor, int(task["id"]))
    if task["status"] == TRANSFER_STATUS_COMPLETED:
        return serialize_transfer_task(cursor, int(task["id"]))
    target = float(task["target_acceptance_quantity"] or 0)
    temporary_snapshot = float(task["temporary_quantity_snapshot"] or 0)
    obligation_snapshot = float(task["obligation_quantity_snapshot"] or 0)
    if abs(target - temporary_snapshot - obligation_snapshot) > 1e-6:
        raise TransferConflict("转移目标数量对账失败")
    if abs(float(task["accepted_quantity"] or 0) - target) > 1e-6:
        raise TransferConflict("正式入库数量尚未达到转移目标")
    cursor.execute(
        """
        SELECT COALESCE(SUM(quantity), 0),
               SUM(CASE WHEN status <> 'outbound_completed' THEN 1 ELSE 0 END)
        FROM transfer_auto_claims WHERE task_id = ?
        """,
        (int(task["id"]),),
    )
    auto_claim_total, incomplete_claims = cursor.fetchone()
    if int(incomplete_claims or 0):
        raise TransferConflict("仍有自动领用未完成")
    if abs(float(auto_claim_total or 0) - obligation_snapshot) > 1e-6:
        raise TransferConflict("自动领用出库总量对账失败")
    cursor.execute(
        """
        SELECT COALESCE(SUM(reserved_quantity), 0),
               COALESCE(SUM(consumed_quantity), 0),
               COALESCE(SUM(released_quantity), 0),
               SUM(CASE WHEN status <> 'consumed' THEN 1 ELSE 0 END)
        FROM inventory_reservations WHERE task_id = ?
        """,
        (int(task["id"]),),
    )
    reserved, consumed, released, incomplete = cursor.fetchone()
    if abs(float(reserved or 0) - obligation_snapshot) > 1e-6:
        raise TransferConflict("正式库存预留数量对账失败")
    if (
        abs(float(consumed or 0) - obligation_snapshot) > 1e-6
        or float(released or 0) > EPSILON
        or int(incomplete or 0)
    ):
        raise TransferConflict("正式库存预留尚未全部正确消耗")
    cursor.execute(
        """
        SELECT COALESCE(SUM(m.settlement_quantity), 0),
               SUM(
                   CASE WHEN m.status <> 'settled' OR o.status <> 'settled'
                        THEN 1 ELSE 0 END
               )
        FROM transfer_auto_claim_obligations m
        JOIN temporary_issue_obligations o ON o.id = m.obligation_id
        WHERE m.task_id = ?
        """,
        (int(task["id"]),),
    )
    settled, unsettled = cursor.fetchone()
    if abs(float(settled or 0) - obligation_snapshot) > 1e-6 or int(unsettled or 0):
        raise TransferConflict("临时领用待结算记录尚未全部结算")
    if has_active_temporary_borrows(cursor, int(task["material_id"])):
        raise TransferConflict("该物料仍存在未归还临时借用，不能完成转移")
    cursor.execute(
        """
        SELECT x.*, b.quantity AS current_quantity,
               b.inventory_status AS current_status, b.version AS current_version,
               b.unit_price
        FROM inventory_transfer_items x
        JOIN material_batches b ON b.id = x.source_batch_id
        WHERE x.task_id = ? ORDER BY x.id
        """,
        (int(task["id"]),),
    )
    items = [dict(row) for row in cursor.fetchall()]
    closed_total = sum(float(row["quantity_snapshot"] or 0) for row in items)
    if abs(closed_total - temporary_snapshot) > 1e-6:
        raise TransferConflict("临时批次快照数量对账失败")
    timestamp = now_text()
    for item in items:
        quantity = float(item["quantity_snapshot"] or 0)
        version = int(item["batch_version_snapshot"] or 0) + 1
        if (
            item["current_status"] != INVENTORY_STATUS_TRANSFER_LOCKED
            or abs(float(item["current_quantity"] or 0) - quantity) > 1e-6
            or int(item["current_version"] or 0) != version
        ):
            raise TransferConflict("临时批次状态、数量或版本已变化，不能完成转移")
        cursor.execute(
            """
            UPDATE material_batches
            SET quantity = 0, inventory_status = ?, version = version + 1,
                updated_at = ?
            WHERE id = ? AND stock_source = ? AND inventory_status = ?
              AND quantity = ? AND version = ?
            """,
            (
                INVENTORY_STATUS_TRANSFERRED, timestamp, int(item["source_batch_id"]),
                STOCK_SOURCE_TEMPORARY, INVENTORY_STATUS_TRANSFER_LOCKED,
                quantity, version,
            ),
        )
        if cursor.rowcount != 1:
            raise TransferConflict("临时批次关闭失败，请刷新后重试")
        cursor.execute(
            """
            INSERT INTO stock_records (
                material_id, operation_type, quantity, balance_after,
                operation_date, remark, batch_id, form_no, unit_price, amount,
                stock_source, business_type, operation_key, transfer_task_id,
                operator_id, created_at
            ) VALUES (?, 'out', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(task["material_id"]), quantity,
                _temporary_total(cursor, int(task["material_id"])), today_text(),
                f"转移任务 {task['transfer_no']} 关闭临时批次",
                int(item["source_batch_id"]), task["transfer_no"],
                float(item["unit_price"] or 0),
                quantity * float(item["unit_price"] or 0),
                STOCK_SOURCE_TEMPORARY, BUSINESS_TYPE_TEMPORARY_TRANSFER_CLOSE,
                f"temporary_transfer_close:{int(task['id'])}:{int(item['source_batch_id'])}",
                int(task["id"]), int(user["id"]) if user else None, timestamp,
            ),
        )
    cursor.execute(
        """
        UPDATE inventory_transfer_tasks
        SET status = ?, active_key = NULL, completed_at = ?, updated_at = ?,
            version = version + 1, error_code = '', error_message = ''
        WHERE id = ? AND status IN (?, ?, ?)
        """,
        (
            TRANSFER_STATUS_COMPLETED, timestamp, timestamp, int(task["id"]),
            TRANSFER_STATUS_AUTO_CLAIM_PENDING,
            TRANSFER_STATUS_FORMAL_INBOUND_COMPLETE,
            TRANSFER_STATUS_RESERVING,
        ),
    )
    if cursor.rowcount != 1:
        raise TransferConflict("转移任务状态已变化，不能完成")
    if user:
        write_audit_log(
            cursor, user, "temporary_transfer.completed",
            "inventory_transfer_task", task["id"], "临时物料转正式库全部完成",
            {
                "accepted_quantity": float(task["accepted_quantity"] or 0),
                "settled_quantity": float(settled or 0),
                "closed_temporary_quantity": closed_total,
            },
            ip_address,
        )
    completed = serialize_transfer_task(cursor, int(task["id"]))
    recipients = set(_task_recipients(completed))
    recipients.update(_user_ids_with_permission(cursor, "process_temporary_transfer"))
    recipients.update(
        int(row["applicant_id"])
        for row in cursor.execute(
            "SELECT applicant_id FROM transfer_auto_claims WHERE task_id = ?",
            (int(task["id"]),),
        )
    )
    notify_transfer_event(cursor, completed, "completed", recipients)
    return completed


def process_auto_claims(cursor, task_id, user=None, ip_address=""):
    begin_inventory_transaction(cursor.connection)
    if not temporary_inventory_enabled(cursor):
        raise TransferConflict("临时库功能已关闭")
    task = _task_row(cursor, task_id)
    if task["status"] in {TRANSFER_STATUS_COMPLETED, TRANSFER_STATUS_AUTO_CLAIM_PENDING}:
        result = serialize_transfer_task(cursor, task_id)
        result["idempotent"] = True
        return result
    if task["status"] == TRANSFER_STATUS_PAUSED and abs(
        float(task["accepted_quantity"] or 0)
        - float(task["target_acceptance_quantity"] or 0)
    ) <= 1e-6:
        _set_task_status(cursor, task_id, TRANSFER_STATUS_FORMAL_INBOUND_COMPLETE)
        task = _task_row(cursor, task_id)
    if task["status"] == TRANSFER_STATUS_AUTO_CLAIM_EXCEPTION:
        cursor.execute(
            "SELECT COUNT(*) FROM transfer_auto_claims WHERE task_id = ?",
            (int(task_id),),
        )
        if int(cursor.fetchone()[0] or 0):
            raise TransferConflict("已有自动领用数据，请使用自动领用重试")
        _set_task_status(cursor, task_id, TRANSFER_STATUS_FORMAL_INBOUND_COMPLETE)
        task = _task_row(cursor, task_id)
    if task["status"] != TRANSFER_STATUS_FORMAL_INBOUND_COMPLETE:
        raise TransferConflict("当前转移任务状态不能处理自动领用")
    if abs(
        float(task["accepted_quantity"] or 0)
        - float(task["target_acceptance_quantity"] or 0)
    ) > 1e-6:
        raise TransferConflict("正式入库数量与转移目标不一致")
    _set_task_status(cursor, task_id, TRANSFER_STATUS_RESERVING)
    task = _task_row(cursor, task_id)
    obligations = _snapshot_obligations(cursor, task)
    batches = _formal_batches(cursor, task)
    if not obligations:
        completed = _finalize(cursor, task, user, ip_address)
        completed["idempotent"] = False
        return completed
    _set_task_status(cursor, task_id, TRANSFER_STATUS_AUTO_CLAIM_CREATING)
    task = _task_row(cursor, task_id)
    claims = _create_auto_claims(cursor, task, obligations)
    reservations = _reserve_batches(cursor, task, claims, batches)
    _create_workflows(cursor, task, claims, reservations)
    _set_task_status(cursor, task_id, TRANSFER_STATUS_AUTO_CLAIM_PENDING)
    updated = serialize_transfer_task(cursor, task_id)
    if user:
        write_audit_log(
            cursor, user, "temporary_transfer.auto_claims_created",
            "inventory_transfer_task", task_id, "创建历史临时领用结算流程",
            {
                "auto_claim_count": len(claims),
                "reservation_quantity": sum(
                    float(item["quantity"]) for rows in reservations.values() for item in rows
                ),
            },
            ip_address,
        )
    notify_transfer_event(cursor, updated, "auto_claim_pending", _task_recipients(updated))
    updated["idempotent"] = False
    return updated


def _existing_outbound(cursor, prefix):
    escaped = str(prefix).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    cursor.execute(
        "SELECT * FROM stock_records WHERE operation_key LIKE ? ESCAPE '\\' ORDER BY id",
        (escaped + "%",),
    )
    return [dict(row) for row in cursor.fetchall()]


def consume_reserved_inventory(
    cursor,
    task_id,
    auto_claim_id,
    workflow_item_id,
    quantity,
    form_no,
    operator_id,
    operation_date=None,
):
    begin_inventory_transaction(cursor.connection)
    if not temporary_inventory_enabled(cursor):
        raise TransferConflict("临时库功能已关闭")
    cursor.execute(
        """
        SELECT ac.*, f.id AS form_id, f.origin_type, f.origin_ref_id
        FROM transfer_auto_claims ac
        JOIN workflow_forms f ON f.id = ac.current_claim_form_id
        WHERE ac.id = ? AND ac.task_id = ?
        """,
        (int(auto_claim_id), int(task_id)),
    )
    row = cursor.fetchone()
    if not row:
        raise TransferConflict("自动领用逻辑或当前流程不存在")
    claim = dict(row)
    if (
        claim["origin_type"] != AUTO_CLAIM_ORIGIN_TYPE
        or int(claim["origin_ref_id"] or 0) != int(task_id)
    ):
        raise TransferConflict("自动领用流程关联不一致")
    requested = float(quantity or 0)
    if abs(requested - float(claim["quantity"] or 0)) > 1e-6:
        raise TransferConflict("自动领用出库数量必须等于预留数量")
    prefix = f"transfer_auto_claim_out:{int(task_id)}:{int(auto_claim_id)}:{int(workflow_item_id)}:"
    existing = _existing_outbound(cursor, prefix)
    if claim["status"] == "outbound_completed":
        if abs(sum(float(item["quantity"] or 0) for item in existing) - requested) > 1e-6:
            raise TransferConflict("自动领用幂等流水数量不一致")
        return [
            {
                "batch_id": int(item["batch_id"]),
                "quantity": float(item["quantity"]),
                "unit_price": float(item["unit_price"] or 0),
                "stock_record_id": int(item["id"]),
            }
            for item in existing
        ]
    if claim["status"] not in {"approval_pending", "outbound_pending"}:
        raise TransferConflict("自动领用当前状态不能出库")
    cursor.execute(
        """
        SELECT r.*, b.batch_no, b.unit_price, b.stock_source, b.inventory_status
        FROM inventory_reservations r
        JOIN material_batches b ON b.id = r.formal_batch_id
        WHERE r.auto_claim_id = ? AND r.task_id = ?
        ORDER BY b.received_date, b.id, r.id
        """,
        (int(auto_claim_id), int(task_id)),
    )
    reservations = [dict(item) for item in cursor.fetchall()]
    remaining = sum(
        float(item["reserved_quantity"] or 0)
        - float(item["consumed_quantity"] or 0)
        - float(item["released_quantity"] or 0)
        for item in reservations
    )
    if abs(remaining - requested) > 1e-6:
        raise TransferConflict("自动领用有效预留数量不一致")
    consumed = []
    timestamp = now_text()
    for reservation in reservations:
        take = (
            float(reservation["reserved_quantity"] or 0)
            - float(reservation["consumed_quantity"] or 0)
            - float(reservation["released_quantity"] or 0)
        )
        if take <= EPSILON:
            continue
        batch_id = int(reservation["formal_batch_id"])
        cursor.execute(
            """
            UPDATE material_batches
            SET quantity = quantity - ?, version = version + 1, updated_at = ?
            WHERE id = ? AND stock_source = ? AND inventory_status = ?
              AND quantity >= ?
              AND quantity - COALESCE(
                  (
                      SELECT SUM(reserved_quantity - consumed_quantity - released_quantity)
                      FROM inventory_reservations
                      WHERE formal_batch_id = material_batches.id
                        AND status = 'active' AND auto_claim_id <> ?
                        AND reserved_quantity - consumed_quantity - released_quantity > 0
                  ),
                  0
              ) >= ?
            """,
            (
                take, timestamp, batch_id, STOCK_SOURCE_FORMAL,
                INVENTORY_STATUS_AVAILABLE, take, int(auto_claim_id), take,
            ),
        )
        if cursor.rowcount != 1:
            raise TransferConflict("预留正式批次库存已变化，请刷新后重试")
        cursor.execute(
            """
            UPDATE inventory_reservations
            SET consumed_quantity = consumed_quantity + ?,
                status = CASE
                    WHEN consumed_quantity + ? >= reserved_quantity - released_quantity
                    THEN 'consumed' ELSE status END,
                version = version + 1, consumed_at = ?, updated_at = ?
            WHERE id = ? AND status = 'active'
              AND consumed_quantity + released_quantity + ? <= reserved_quantity
            """,
            (take, take, timestamp, timestamp, int(reservation["id"]), take),
        )
        if cursor.rowcount != 1:
            raise TransferConflict("正式库存预留状态已变化")
        balance, _ = update_inventory_total(cursor, int(claim["material_id"]))
        cursor.execute(
            """
            INSERT INTO stock_records (
                material_id, operation_type, quantity, balance_after,
                operation_date, remark, batch_id, form_no, unit_price, amount,
                stock_source, business_type, operation_key, transfer_task_id,
                operator_id, workflow_item_id, transfer_auto_claim_id,
                inventory_reservation_id, created_at
            ) VALUES (?, 'out', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(claim["material_id"]), take, balance,
                operation_date or today_text(),
                f"自动领用单 {form_no} 预留库存出库",
                batch_id, form_no, float(reservation["unit_price"] or 0),
                take * float(reservation["unit_price"] or 0),
                STOCK_SOURCE_FORMAL, BUSINESS_TYPE_CLAIM_OUTBOUND,
                prefix + str(batch_id), int(task_id), int(operator_id),
                int(workflow_item_id), int(auto_claim_id),
                int(reservation["id"]), timestamp,
            ),
        )
        consumed.append(
            {
                "batch_id": batch_id,
                "batch_no": reservation.get("batch_no") or "",
                "quantity": take,
                "unit_price": float(reservation["unit_price"] or 0),
                "stock_record_id": int(cursor.lastrowid),
                "reservation_id": int(reservation["id"]),
            }
        )
    cursor.execute(
        """
        SELECT m.* FROM transfer_auto_claim_obligations m
        WHERE m.auto_claim_id = ? AND m.status = 'pending' ORDER BY m.id
        """,
        (int(auto_claim_id),),
    )
    mappings = [dict(item) for item in cursor.fetchall()]
    settled = 0.0
    for mapping in mappings:
        amount = float(mapping["settlement_quantity"] or 0)
        cursor.execute(
            """
            UPDATE temporary_issue_obligations
            SET settled_quantity = settled_quantity + ?,
                status = CASE WHEN settled_quantity + ? >= issued_quantity
                              THEN 'settled' ELSE 'processing' END,
                transfer_task_id = ?, auto_claim_form_id = ?,
                settled_at = ?, updated_at = ?
            WHERE id = ? AND status IN ('pending', 'reserved', 'processing')
              AND settled_quantity + ? <= issued_quantity
            """,
            (
                amount, amount, int(task_id), int(claim["form_id"]),
                timestamp, timestamp, int(mapping["obligation_id"]), amount,
            ),
        )
        if cursor.rowcount != 1:
            raise TransferConflict("临时领用待结算记录状态或数量已变化")
        cursor.execute(
            """
            UPDATE transfer_auto_claim_obligations
            SET status = 'settled', settled_at = ?, updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (timestamp, timestamp, int(mapping["id"])),
        )
        if cursor.rowcount != 1:
            raise TransferConflict("待结算映射已被重复处理")
        settled += amount
    if abs(settled - requested) > 1e-6:
        raise TransferConflict("自动领用结算数量与出库数量不一致")
    cursor.execute(
        """
        UPDATE transfer_auto_claims
        SET status = 'outbound_completed', outbound_completed_at = ?,
            completed_at = ?, active_key = NULL, updated_at = ?,
            error_code = '', error_message = ''
        WHERE id = ? AND status IN ('approval_pending', 'outbound_pending')
        """,
        (timestamp, timestamp, timestamp, int(auto_claim_id)),
    )
    if cursor.rowcount != 1:
        raise TransferConflict("自动领用状态已变化")
    return consumed


def finalize_transfer_if_ready(cursor, task_id, user=None, ip_address=""):
    task = _task_row(cursor, task_id)
    if task["status"] == TRANSFER_STATUS_COMPLETED:
        return serialize_transfer_task(cursor, task_id)
    cursor.execute(
        "SELECT COUNT(*) FROM transfer_auto_claims WHERE task_id = ? AND status <> 'outbound_completed'",
        (int(task_id),),
    )
    if int(cursor.fetchone()[0] or 0):
        return serialize_transfer_task(cursor, task_id)
    return _finalize(cursor, task, user, ip_address)


def mark_auto_claim_outbound_pending(cursor, claim_form_id):
    cursor.execute(
        """
        UPDATE transfer_auto_claims
        SET status = 'outbound_pending', updated_at = ?
        WHERE current_claim_form_id = ? AND status = 'approval_pending'
        """,
        (now_text(), int(claim_form_id)),
    )

def mark_auto_claim_outbound_exception(
    cursor, claim_form_id, message, user=None, ip_address=""
):
    begin_inventory_transaction(cursor.connection)
    cursor.execute(
        "SELECT * FROM transfer_auto_claims WHERE current_claim_form_id = ?",
        (int(claim_form_id),),
    )
    row = cursor.fetchone()
    if not row:
        return None
    claim = dict(row)
    if claim["status"] == "outbound_completed":
        return claim
    timestamp = now_text()
    if temporary_inventory_enabled(cursor):
        cursor.execute(
            """
            UPDATE transfer_auto_claims
            SET status = 'exception', error_code = 'outbound_failed',
                error_message = ?, updated_at = ?
            WHERE id = ? AND status IN ('approval_pending', 'outbound_pending')
            """,
            (str(message or "自动领用出库失败"), timestamp, int(claim["id"])),
        )
        task_status = TRANSFER_STATUS_AUTO_CLAIM_EXCEPTION
        error_code = "auto_claim_outbound_failed"
    else:
        task_status = TRANSFER_STATUS_PAUSED
        error_code = "feature_disabled"
    _set_task_status(
        cursor,
        int(claim["task_id"]),
        task_status,
        error_code,
        str(message or "自动领用出库失败"),
    )
    task = serialize_transfer_task(cursor, int(claim["task_id"]))
    if user:
        write_audit_log(
            cursor, user, "temporary_transfer.auto_claim_outbound_failed",
            "inventory_transfer_task", claim["task_id"], "自动领用出库失败",
            {"claim_form_id": int(claim_form_id), "error_message": str(message)},
            ip_address,
        )
    notify_transfer_event(
        cursor, task, "auto_claim_exception", _task_recipients(task), str(message)
    )
    return claim




def reject_auto_claim(cursor, claim_form_id, reason):
    cursor.execute(
        "SELECT * FROM transfer_auto_claims WHERE current_claim_form_id = ?",
        (int(claim_form_id),),
    )
    row = cursor.fetchone()
    if not row:
        return None
    claim = dict(row)
    timestamp = now_text()
    cursor.execute(
        """
        UPDATE transfer_auto_claims
        SET status = 'rejected', error_code = 'approval_rejected',
            error_message = ?, updated_at = ?
        WHERE id = ? AND status IN ('approval_pending', 'outbound_pending')
        """,
        (str(reason or "自动领用审批被拒绝"), timestamp, int(claim["id"])),
    )
    _set_task_status(
        cursor,
        int(claim["task_id"]),
        TRANSFER_STATUS_AUTO_CLAIM_EXCEPTION,
        "auto_claim_rejected",
        str(reason or "自动领用审批被拒绝"),
    )
    task = serialize_transfer_task(cursor, int(claim["task_id"]))
    notify_transfer_event(
        cursor, task, "auto_claim_exception", _task_recipients(task),
        str(reason or "自动领用审批被拒绝"),
    )
    return claim


def retry_auto_claims(cursor, task_id, user=None, ip_address=""):
    begin_inventory_transaction(cursor.connection)
    if not temporary_inventory_enabled(cursor):
        raise TransferConflict("临时库功能已关闭")
    task = _task_row(cursor, task_id)
    if task["status"] != TRANSFER_STATUS_AUTO_CLAIM_EXCEPTION:
        raise TransferConflict("当前转移任务状态不能重试自动领用")
    cursor.execute(
        "SELECT * FROM transfer_auto_claims WHERE task_id = ? ORDER BY applicant_id",
        (int(task_id),),
    )
    retry_rows = []
    for row in map(dict, cursor.fetchall()):
        if row["status"] == "outbound_completed":
            continue
        if row["status"] not in {"rejected", "exception"}:
            raise TransferConflict("仍有进行中的自动领用，不能重复重试")
        applicant = _active_user(cursor, int(row["applicant_id"]))
        leader_id = resolve_department_leader(cursor, applicant, "claim", "leader_claim")
        cursor.execute(
            """
            UPDATE transfer_auto_claims
            SET status = 'pending_create', error_code = '', error_message = '',
                updated_at = ?
            WHERE id = ? AND status IN ('rejected', 'exception')
            """,
            (now_text(), int(row["id"])),
        )
        cursor.execute(
            "SELECT obligation_id FROM transfer_auto_claim_obligations WHERE auto_claim_id = ? AND status = 'pending' ORDER BY id",
            (int(row["id"]),),
        )
        retry_rows.append(
            {
                **row,
                "applicant": applicant,
                "leader_id": leader_id,
                "obligations": [dict(item) for item in cursor.fetchall()],
            }
        )
    if not retry_rows:
        raise TransferConflict("没有需要重试的自动领用")
    reservations = {}
    for row in retry_rows:
        cursor.execute(
            """
            SELECT id, formal_batch_id, reserved_quantity AS quantity
            FROM inventory_reservations
            WHERE auto_claim_id = ? AND status = 'active' ORDER BY id
            """,
            (int(row["id"]),),
        )
        reservations[int(row["id"])] = [dict(item) for item in cursor.fetchall()]
        if abs(
            sum(float(item["quantity"] or 0) for item in reservations[int(row["id"])])
            - float(row["quantity"] or 0)
        ) > 1e-6:
            raise TransferConflict("自动领用重试时预留数量不完整")
    _create_workflows(cursor, task, retry_rows, reservations)
    _set_task_status(cursor, task_id, TRANSFER_STATUS_AUTO_CLAIM_PENDING)
    updated = serialize_transfer_task(cursor, task_id, user)
    if user:
        write_audit_log(
            cursor, user, "temporary_transfer.auto_claim_retry",
            "inventory_transfer_task", task_id, "重试自动领用流程",
            {"auto_claim_ids": [int(row["id"]) for row in retry_rows]}, ip_address,
        )
    updated["idempotent"] = False
    return updated


def settlement_summary(cursor, task_id, user=None):
    task = serialize_transfer_task(cursor, task_id, user)
    cursor.execute(
        """
        SELECT ac.*, u.display_name AS applicant_name, f.form_no,
               f.status AS workflow_status, f.current_step AS workflow_step
        FROM transfer_auto_claims ac
        JOIN users u ON u.id = ac.applicant_id
        LEFT JOIN workflow_forms f ON f.id = ac.current_claim_form_id
        WHERE ac.task_id = ? ORDER BY ac.applicant_id
        """,
        (int(task_id),),
    )
    claims = [dict(row) for row in cursor.fetchall()]
    for claim in claims:
        cursor.execute(
            """
            SELECT id, formal_batch_id, reserved_quantity, consumed_quantity,
                   released_quantity, status, operation_key
            FROM inventory_reservations WHERE auto_claim_id = ? ORDER BY id
            """,
            (int(claim["id"]),),
        )
        claim["reservations"] = [dict(row) for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT m.obligation_id, m.settlement_quantity, m.status,
                   o.issued_quantity, o.settled_quantity
            FROM transfer_auto_claim_obligations m
            JOIN temporary_issue_obligations o ON o.id = m.obligation_id
            WHERE m.auto_claim_id = ? ORDER BY m.id
            """,
            (int(claim["id"]),),
        )
        claim["obligations"] = [dict(row) for row in cursor.fetchall()]
    cursor.execute(
        """
        SELECT COALESCE(SUM(reserved_quantity), 0) AS reserved_quantity,
               COALESCE(SUM(consumed_quantity), 0) AS consumed_quantity,
               COALESCE(SUM(released_quantity), 0) AS released_quantity
        FROM inventory_reservations WHERE task_id = ?
        """,
        (int(task_id),),
    )
    return {
        "task": task,
        "obligation_quantity": float(task["obligation_quantity_snapshot"] or 0),
        "reservation_totals": dict(cursor.fetchone()),
        "auto_claims": claims,
        "temporary_batches_closed": all(
            item.get("current_inventory_status") == INVENTORY_STATUS_TRANSFERRED
            for item in task.get("items") or []
        ),
        "available_actions": task.get("available_actions") or [],
    }

