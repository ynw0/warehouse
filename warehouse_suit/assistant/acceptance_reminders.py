"""查询验收超期任务，并按确认名单发送催办。"""

import json
from datetime import datetime, timedelta

from warehouse_suit.notifications import create_notification
from warehouse_suit.temporary_inventory_visibility import append_workflow_visibility
from warehouse_suit.workflow_service import workflow_view_scope


ACCEPTANCE_TYPES = {"acceptance": "物料验收", "semifinished": "半成品验收", "finished": "成品验收"}


def overdue_acceptance_tasks(cursor, user, task_ids=None, *, limit=None, offset=0):
    # 未办理的验收任务在建立或改派时写入 updated_at。
    scope, scope_params = workflow_view_scope(cursor, user)
    cutoff = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
    where = [
        scope, "f.form_type IN ('acceptance', 'semifinished', 'finished')",
        "f.status = 'acceptance'", "f.current_step = 'acceptance'",
        "t.step_code = f.current_step", "t.status = 'pending'",
        "u.is_active = 1", "t.updated_at < ?", "datetime(t.updated_at) IS NOT NULL",
    ]
    params = [*scope_params, cutoff]
    append_workflow_visibility(cursor, where, params, "f")
    if task_ids is not None:
        where.append("t.id IN (SELECT value FROM json_each(?))")
        params.append(json.dumps(task_ids))
    paging = ""
    if limit is not None:
        paging = "LIMIT ? OFFSET ?"
        params.extend((limit, offset))
    cursor.execute(f"""
        SELECT t.id AS task_id, t.assignee_id, u.display_name AS assignee_name,
               t.updated_at AS assigned_at, f.id AS form_id, f.form_no, f.form_type
        FROM workflow_tasks t JOIN workflow_forms f ON f.id = t.form_id
        JOIN users u ON u.id = t.assignee_id
        WHERE {' AND '.join(where)}
        ORDER BY u.display_name, t.assignee_id, t.updated_at, t.id
        {paging}
    """, params)
    return [dict(row, form_type_label=ACCEPTANCE_TYPES[row["form_type"]]) for row in cursor.fetchall()]


def preview_acceptance_reminders(cursor, user, payload):
    ids = payload.get("task_ids")
    if not isinstance(ids, list) or not ids or any(type(value) is not int or value < 1 for value in ids):
        raise ValueError("请先查询超期验收名单，再提供需要催办的待办 ID")
    tasks = overdue_acceptance_tasks(cursor, user, list(dict.fromkeys(ids)))
    if not tasks:
        raise ValueError("所选待办当前没有符合条件的超期验收任务")
    return {"tasks": tasks}


def send_acceptance_reminders(cursor, user, payload):
    snapshot = {item["task_id"]: item for item in payload["tasks"]}
    tasks = overdue_acceptance_tasks(cursor, user, list(snapshot))
    sent = []
    for task in tasks:
        before = snapshot[task["task_id"]]
        if any(task[key] != before[key] for key in ("assignee_id", "assigned_at", "form_id")):
            continue
        create_notification(
            cursor, task["assignee_id"], "验收催办",
            f"{task['form_no']} 的{task['form_type_label']}已待办理超过 48 小时，请及时处理。",
            {"form_id": task["form_id"], "task_id": task["task_id"], "sender_user_id": user["id"]},
            category="assistant",
        )
        sent.append(task)
    skipped = len(snapshot) - len(sent)
    recipients = len({task["assignee_id"] for task in sent})
    return {
        "message": f"已向 {recipients} 人发送 {len(sent)} 条验收催办，跳过 {skipped} 条状态已变化的待办。",
        "data": {"sent": len(sent), "recipients": recipients, "skipped": skipped},
    }
