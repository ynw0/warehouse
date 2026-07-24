# -*- coding: utf-8 -*-
"""Workflow permissions, assignment, aggregation, and serialization services."""

import json

from warehouse_suit.db import now_text, row_to_dict
from warehouse_suit.inventory_constants import STOCK_SOURCE_FORMAL, STOCK_SOURCE_TEMPORARY
from warehouse_suit.material_repository import build_fifo_plan, material_batch_rows, material_stock_total
from warehouse_suit.material_utils import locked_stock_quantity, numeric_or_none
from warehouse_suit.permissions import canonical_permission, role_permissions
from warehouse_suit.settings import (
    WORKFLOW_STEP_DEFINITIONS,
    parse_json,
    temporary_inventory_enabled,
    workflow_settings,
)
from warehouse_suit.temporary_inventory_visibility import temporary_workflow_sql, workflow_is_temporary


_current_user_provider = None
_user_by_id_provider = None


def configure_workflow_service(current_user_provider, user_by_id_provider):
    global _current_user_provider, _user_by_id_provider
    _current_user_provider = current_user_provider
    _user_by_id_provider = user_by_id_provider


def _current_user(cursor):
    if _current_user_provider is None:
        raise RuntimeError("current user provider is not configured")
    return _current_user_provider(cursor)


def _user_by_id(cursor, user_id):
    if _user_by_id_provider is None:
        raise RuntimeError("user lookup provider is not configured")
    return _user_by_id_provider(cursor, user_id)


def user_has_permission(cursor, user, permission):
    permission = canonical_permission(permission)
    if not user:
        return False
    if "admin" in user.get("role_codes", []):
        return True
    permissions = role_permissions(cursor)
    return any(permissions.get(role, {}).get(permission) for role in user.get("role_codes", []))


def user_id_has_permission(cursor, user_id, permission):
    user = _user_by_id(cursor, user_id)
    return user_has_permission(cursor, user, permission)


def validate_users_exist(cursor, user_ids):
    ids = []
    for user_id in user_ids or []:
        try:
            value = int(user_id)
        except (TypeError, ValueError):
            continue
        if value and value not in ids:
            ids.append(value)
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    cursor.execute(f"SELECT id FROM users WHERE is_active = 1 AND id IN ({placeholders})", ids)
    existing = {int(row["id"]) for row in cursor.fetchall()}
    missing = [user_id for user_id in ids if user_id not in existing]
    if missing:
        raise ValueError(f"可办理人不存在或已停用：{','.join(str(item) for item in missing)}")
    return ids


def configured_step_assignee_config(cursor, form_type, step_code):
    settings = workflow_settings(cursor)
    raw = (settings.get("workflow_step_assignees") or {}).get(form_type, {}).get(step_code, {})
    if isinstance(raw, dict):
        return {
            "roles": [str(role).strip() for role in raw.get("roles") or [] if str(role).strip()],
            "users": [int(value) for value in raw.get("users") or [] if int(value)],
        }
    return {"roles": [], "users": [int(value) for value in raw or [] if int(value)]}


def configured_step_assignee_ids(cursor, form_type, step_code):
    config = configured_step_assignee_config(cursor, form_type, step_code)
    ids = []
    for user_id in config.get("users") or []:
        if user_id and user_id not in ids:
            ids.append(user_id)
    roles = config.get("roles") or []
    if roles:
        placeholders = ",".join("?" for _ in roles)
        cursor.execute(
            f"""
            SELECT DISTINCT u.id
            FROM users u
            JOIN user_roles ur ON ur.user_id = u.id
            JOIN roles r ON r.id = ur.role_id
            WHERE u.is_active = 1 AND r.code IN ({placeholders})
            ORDER BY u.id
            """,
            roles,
        )
        for row in cursor.fetchall():
            user_id = int(row["id"])
            if user_id not in ids:
                ids.append(user_id)
    return ids


def validate_step_assignees(cursor, form_type, step_code, user_ids):
    ids = validate_users_exist(cursor, user_ids)
    allowed = configured_step_assignee_ids(cursor, form_type, step_code)
    if allowed:
        invalid = [user_id for user_id in ids if user_id not in allowed]
        if invalid and step_code in {"leader_acceptance", "leader_claim", "leader_borrow"}:
            invalid = [user_id for user_id in invalid if not user_has_role_code(cursor, user_id, "leader")]
        if invalid:
            raise ValueError(f"所选人员不在该流程步骤可办理人范围内：{','.join(str(item) for item in invalid)}")
        if not ids:
            ids = allowed[:]
    return ids


def validate_validator_users(cursor, user_ids, form_type="acceptance"):
    ids = validate_users_exist(cursor, user_ids)
    return ids


def workflow_assignees(cursor, form_type, step_code, user_ids):
    return validate_step_assignees(cursor, form_type, step_code, user_ids)


def user_has_role_code(cursor, user_id, role_code):
    cursor.execute(
        """
        SELECT 1
        FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id
        WHERE ur.user_id = ? AND r.code = ?
        LIMIT 1
        """,
        (user_id, role_code),
    )
    return bool(cursor.fetchone())


def department_leader_options(cursor, actor, form_type, step_code):
    department = (actor.get("department") if actor else "") or ""
    configured_ids = configured_step_assignee_ids(cursor, form_type, step_code)
    if configured_ids:
        placeholders = ",".join("?" for _ in configured_ids)
        params = configured_ids[:]
        sql = f"""
            SELECT DISTINCT u.id, u.display_name, u.department
            FROM users u
            JOIN user_roles ur ON ur.user_id = u.id
            JOIN roles r ON r.id = ur.role_id
            WHERE u.is_active = 1
              AND r.code = 'leader'
              AND u.id IN ({placeholders})
        """
    else:
        params = []
        sql = """
            SELECT DISTINCT u.id, u.display_name, u.department
            FROM users u
            JOIN user_roles ur ON ur.user_id = u.id
            JOIN roles r ON r.id = ur.role_id
            WHERE u.is_active = 1
              AND r.code = 'leader'
        """
    if department:
        sql += " AND u.department = ?"
        params.append(department)
    sql += " ORDER BY u.id"
    cursor.execute(sql, params)
    rows = [dict(row) for row in cursor.fetchall()]
    if rows or not configured_ids:
        return rows
    params = []
    sql = """
        SELECT DISTINCT u.id, u.display_name, u.department
        FROM users u
        JOIN user_roles ur ON ur.user_id = u.id
        JOIN roles r ON r.id = ur.role_id
        WHERE u.is_active = 1
          AND r.code = 'leader'
    """
    if department:
        sql += " AND u.department = ?"
        params.append(department)
    sql += " ORDER BY u.id"
    cursor.execute(sql, params)
    return [dict(row) for row in cursor.fetchall()]


def resolve_department_leader(cursor, actor, form_type, step_code, candidate_id=0):
    leaders = department_leader_options(cursor, actor, form_type, step_code)
    leader_ids = [int(item["id"]) for item in leaders]
    department = (actor.get("department") if actor else "") or ""
    if not leader_ids:
        label = f"{department}部门" if department else "当前用户"
        raise ValueError(f"{label}未配置审批领导")
    candidate_id = int(candidate_id or 0)
    if candidate_id:
        if candidate_id not in leader_ids:
            raise ValueError("审批领导必须选择当前用户所属部门的部门领导")
        return workflow_assignees(cursor, form_type, step_code, [candidate_id])[0]
    return workflow_assignees(cursor, form_type, step_code, [leader_ids[0]])[0]


def create_workflow_tasks(cursor, form_id, form_type, step_code, assignee_ids):
    ids = workflow_assignees(cursor, form_type, step_code, assignee_ids)
    for assignee_id in ids:
        cursor.execute(
            "INSERT INTO workflow_tasks (form_id, step_code, assignee_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (form_id, step_code, assignee_id, now_text(), now_text()),
        )
    return ids


def require_permission(cursor, permission):
    user = _current_user(cursor)
    if not user_has_permission(cursor, user, permission):
        raise PermissionError("当前账号没有该操作权限")
    return user


def require_any_permission(cursor, *permissions):
    user = _current_user(cursor)
    if not user or not any(user_has_permission(cursor, user, permission) for permission in permissions):
        raise PermissionError("当前账号没有该操作权限")
    return user


def require_inventory_permission(cursor, inventory_type, mode="read"):
    user = _current_user(cursor)
    if not user:
        raise PermissionError("login required")
    read_key = f"read_{inventory_type}_inventory"
    write_key = f"write_{inventory_type}_inventory"
    if mode == "write":
        allowed = user_has_permission(cursor, user, write_key)
    elif mode == "delete":
        allowed = user_has_permission(cursor, user, write_key)
    else:
        allowed = (
            user_has_permission(cursor, user, read_key)
            or user_has_permission(cursor, user, write_key)
        )
    if not allowed:
        raise PermissionError("inventory permission denied")
    return user


def require_task_assignee(cursor, user, form_id, step_code, task_id=0):
    if not user:
        raise PermissionError("请先登录")
    if not temporary_inventory_enabled(cursor) and workflow_is_temporary(cursor, form_id):
        raise PermissionError("临时库功能已关闭")
    params = [form_id, step_code]
    sql = "SELECT * FROM workflow_tasks WHERE form_id = ? AND step_code = ? AND status = 'pending'"
    if task_id:
        sql += " AND id = ?"
        params.append(task_id)
    if "admin" not in user.get("role_codes", []):
        sql += " AND assignee_id = ?"
        params.append(user["id"])
    sql += " ORDER BY id LIMIT 1"
    cursor.execute(sql, params)
    task = cursor.fetchone()
    if not task:
        raise PermissionError("当前账号没有该待办的办理权限，或待办已处理")
    return task


def require_form_status(cursor, form_id, form_type, *statuses):
    if not temporary_inventory_enabled(cursor) and workflow_is_temporary(cursor, form_id):
        raise PermissionError("临时库功能已关闭")
    cursor.execute("SELECT * FROM workflow_forms WHERE id = ? AND form_type = ?", (form_id, form_type))
    form = cursor.fetchone()
    if not form:
        raise ValueError("流程不存在")
    if statuses and form["status"] not in statuses:
        raise ValueError("当前流程状态不允许重复办理")
    return form


def user_can_view_form(cursor, user, form_id):
    if not user:
        return False
    if not temporary_inventory_enabled(cursor) and workflow_is_temporary(cursor, form_id):
        return False
    if "admin" in user.get("role_codes", []):
        return True
    cursor.execute(
        """
        SELECT 1
        FROM workflow_forms f
        WHERE f.id = ?
          AND (
            f.applicant_id = ?
            OR f.leader_id = ?
            OR f.warehouse_user_id = ?
            OR EXISTS (SELECT 1 FROM workflow_tasks t WHERE t.form_id = f.id AND t.assignee_id = ?)
          )
        """,
        (form_id, user["id"], user["id"], user["id"], user["id"]),
    )
    return bool(cursor.fetchone())


def require_form_view(cursor, form_id):
    user = _current_user(cursor)
    if not user_can_view_form(cursor, user, form_id):
        raise PermissionError("当前账号没有查看该流程的权限")
    return user


def aggregate_acceptance_results(cursor, form_id):
    cursor.execute("SELECT form_type FROM workflow_forms WHERE id = ?", (form_id,))
    form_row = cursor.fetchone()
    form_type = form_row["form_type"] if form_row else ""
    if is_production_form_type(form_type):
        return aggregate_production_acceptance_results(cursor, form_id)
    cursor.execute(
        """
        SELECT t.signature, t.data_json
        FROM workflow_tasks t
        WHERE t.form_id = ? AND t.step_code = 'acceptance' AND t.status = 'completed'
        ORDER BY t.id
        """,
        (form_id,),
    )
    aggregate = {}
    signatures = []
    for row in cursor.fetchall():
        if row["signature"]:
            signatures.append(row["signature"])
        task_data = parse_json(row["data_json"], {})
        for item in task_data.get("items") or []:
            item_id = int(item.get("id") or 0)
            if not item_id:
                continue
            target = aggregate.setdefault(item_id, {})
            for key in [
                "qualified_quantity",
                "unqualified_quantity",
                "package_ok_quantity",
                "appearance_ok_quantity",
                "name_spec_ok_quantity",
                "usage_ok_quantity",
            ]:
                value = float(item.get(key) or 0)
                target[key] = value if key not in target else min(float(target[key]), value)
            remarks = target.setdefault("remarks", [])
            if item.get("remark"):
                remarks.append(str(item.get("remark")))
    for item_id, values in aggregate.items():
        detail = {
            "package_ok_quantity": values.get("package_ok_quantity", 0),
            "appearance_ok_quantity": values.get("appearance_ok_quantity", 0),
            "name_spec_ok_quantity": values.get("name_spec_ok_quantity", 0),
            "usage_ok_quantity": values.get("usage_ok_quantity", 0),
            "remark": "；".join(values.get("remarks") or []),
        }
        cursor.execute(
            """
            UPDATE workflow_items
            SET qualified_quantity = ?, unqualified_quantity = ?, data_json = ?
            WHERE id = ? AND form_id = ?
            """,
            (
                values.get("qualified_quantity", 0),
                values.get("unqualified_quantity", 0),
                json.dumps(detail, ensure_ascii=False),
                item_id,
                form_id,
            ),
        )
    if signatures:
        deduped = list(dict.fromkeys(signatures))
        cursor.execute(
            """
            UPDATE workflow_forms
            SET data_json = json_set(COALESCE(NULLIF(data_json, ''), '{}'), '$.acceptance_signatures', ?)
            WHERE id = ?
            """,
            ("、".join(deduped), form_id),
        )


def aggregate_production_acceptance_results(cursor, form_id):
    cursor.execute(
        """
        SELECT t.signature, t.data_json
        FROM workflow_tasks t
        WHERE t.form_id = ? AND t.step_code = 'acceptance' AND t.status = 'completed'
        ORDER BY t.id
        """,
        (form_id,),
    )
    aggregate = {}
    signatures = []
    for row in cursor.fetchall():
        if row["signature"]:
            signatures.append(row["signature"])
        task_data = parse_json(row["data_json"], {})
        for item in task_data.get("items") or []:
            item_id = int(item.get("id") or 0)
            if not item_id:
                continue
            target = aggregate.setdefault(item_id, {"defects": [], "max_unqualified": -1})
            for key in [
                "appearance_ok_quantity",
                "function_ok_quantity",
                "performance_ok_quantity",
            ]:
                value = float(item.get(key) or 0)
                target[key] = value if key not in target else min(float(target[key]), value)
            unqualified = float(item.get("unqualified_quantity") or 0)
            if unqualified > float(target.get("max_unqualified") or -1):
                target["max_unqualified"] = unqualified
                target["defects"] = item.get("defects") or []
                target["serial_items"] = item.get("serial_items") or []
            remarks = target.setdefault("remarks", [])
            if item.get("remark"):
                remarks.append(str(item.get("remark")))
    for item_id, values in aggregate.items():
        cursor.execute("SELECT arrival_quantity, data_json FROM workflow_items WHERE id = ? AND form_id = ?", (item_id, form_id))
        item_row = cursor.fetchone()
        if not item_row:
            continue
        acceptance_quantity = float(item_row["arrival_quantity"] or 0)
        appearance_ok = float(values.get("appearance_ok_quantity") or 0)
        function_ok = float(values.get("function_ok_quantity") or 0)
        performance_ok = float(values.get("performance_ok_quantity") or 0)
        qualified = min(appearance_ok, function_ok, performance_ok)
        unqualified = max(0, acceptance_quantity - qualified)
        existing = parse_json(item_row["data_json"], {})
        existing.update(
            {
                "appearance_ok_quantity": appearance_ok,
                "function_ok_quantity": function_ok,
                "performance_ok_quantity": performance_ok,
                "remark": "；".join(values.get("remarks") or []),
                "defects": (values.get("defects") or [])[: int(round(unqualified))],
                "serial_items": values.get("serial_items") or existing.get("serial_items") or [],
            }
        )
        cursor.execute(
            """
            UPDATE workflow_items
            SET qualified_quantity = ?, unqualified_quantity = ?, data_json = ?
            WHERE id = ? AND form_id = ?
            """,
            (
                qualified,
                unqualified,
                json.dumps(existing, ensure_ascii=False),
                item_id,
                form_id,
            ),
        )
    if signatures:
        deduped = list(dict.fromkeys(signatures))
        cursor.execute(
            """
            UPDATE workflow_forms
            SET data_json = json_set(COALESCE(NULLIF(data_json, ''), '{}'), '$.acceptance_signatures', ?)
            WHERE id = ?
            """,
            ("、".join(deduped), form_id),
        )

PRODUCTION_FORM_TYPES = {"semifinished", "finished"}


def is_production_form_type(form_type):
    return form_type in PRODUCTION_FORM_TYPES


def workflow_edit_permission(form):
    if form["form_type"] == "acceptance" or is_production_form_type(form["form_type"]):
        return "edit_acceptance"
    if form["form_type"] == "claim":
        if form.get("status") in {"outbound", "completed"} or form.get("current_step") in {"outbound", "completed"}:
            return "edit_outbound"
        return "edit_claim"
    if form["form_type"] in {"borrow", "borrow_return"}:
        return "edit_borrow"
    return "edit_claim"


def workflow_applicant_can_modify(cursor, form_id, user):
    if not user:
        return False
    cursor.execute("SELECT applicant_id, status FROM workflow_forms WHERE id = ?", (form_id,))
    form = cursor.fetchone()
    if not form:
        return False
    if int(form["applicant_id"] or 0) != int(user["id"]):
        return False
    if form["status"] in {"completed", "cancelled", "rejected"}:
        return False
    if form["status"] == "applicant_revision":
        return True
    cursor.execute("SELECT 1 FROM workflow_tasks WHERE form_id = ? AND status = 'completed' LIMIT 1", (form_id,))
    return cursor.fetchone() is None


def require_workflow_edit_or_applicant(cursor, form, user):
    if user_has_permission(cursor, user, workflow_edit_permission(form)):
        return user
    if workflow_applicant_can_modify(cursor, form["id"], user):
        return user
    raise PermissionError("当前账号没有该流程的修改权限，或下一步办理人已处理")


def workflow_step_codes(form_type):
    return WORKFLOW_STEP_DEFINITIONS.get(form_type) or WORKFLOW_STEP_DEFINITIONS.get("acceptance", [])


def workflow_return_assignees(cursor, form, target_step):
    if target_step == "applicant_revision":
        return validate_users_exist(cursor, [int(form["applicant_id"])])
    cursor.execute(
        """
        SELECT DISTINCT assignee_id
        FROM workflow_tasks
        WHERE form_id = ? AND step_code = ? AND assignee_id IS NOT NULL
        ORDER BY id
        """,
        (form["id"], target_step),
    )
    ids = [int(row["assignee_id"]) for row in cursor.fetchall() if row["assignee_id"]]
    form_data = parse_json(form.get("data_json"), {})
    if not ids:
        if target_step == "acceptance":
            ids = [int(value) for value in form_data.get("validator_ids") or [] if int(value)]
        elif target_step in {"leader_acceptance", "leader_claim", "leader_borrow"} and form.get("leader_id"):
            ids = [int(form["leader_id"])]
        elif target_step in {"inbound", "outbound", "borrow_outbound", "return_inbound"} and form.get("warehouse_user_id"):
            ids = [int(form["warehouse_user_id"])]
    if not ids and form.get("applicant_id"):
        ids = [int(form["applicant_id"])]
    allowed = configured_step_assignee_ids(cursor, form["form_type"], target_step)
    if allowed:
        ids = [user_id for user_id in ids if user_id in allowed] or allowed[:]
    return validate_users_exist(cursor, ids)


def workflow_list_rows(cursor, where_sql, params):
    params = list(params)
    if not temporary_inventory_enabled(cursor):
        temporary_sql, temporary_params = temporary_workflow_sql("f")
        where_sql = f"({where_sql}) AND NOT {temporary_sql}"
        params.extend(temporary_params)
    sql = f"""
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
        WHERE {where_sql}
        ORDER BY f.id DESC
    """
    cursor.execute(sql, params)
    return [dict(row) for row in cursor.fetchall()]


def production_form_label(form_type):
    return "半成品验收" if form_type == "semifinished" else ("成品验收" if form_type == "finished" else form_type)


def workflow_generated_title(user, form_no):
    parts = [user.get("department") if user else "", user.get("display_name") if user else "", form_no]
    title = "".join(str(part or "").strip() for part in parts)
    return title or form_no


def serialize_form(cursor, form_id):
    cursor.execute(
        """
        SELECT f.*, u.display_name AS applicant_name, l.display_name AS leader_name,
               w.display_name AS warehouse_user_name
        FROM workflow_forms f
        LEFT JOIN users u ON u.id = f.applicant_id
        LEFT JOIN users l ON l.id = f.leader_id
        LEFT JOIN users w ON w.id = f.warehouse_user_id
        WHERE f.id = ?
        """,
        (form_id,),
    )
    form = row_to_dict(cursor.fetchone())
    if not form:
        return None
    form["data"] = parse_json(form.pop("data_json", "{}"), {})
    cursor.execute("SELECT * FROM workflow_items WHERE form_id = ? ORDER BY id", (form_id,))
    form["items"] = []
    for row in cursor.fetchall():
        item = dict(row)
        item["data"] = parse_json(item.pop("data_json", "{}"), {})
        if item.get("material_id"):
            stock_source = item.get("stock_source") or STOCK_SOURCE_FORMAL
            current_stock_quantity = material_stock_total(
                cursor,
                item["material_id"],
                stock_source=stock_source,
            )
            item["stock_source"] = stock_source
            item["stock_source_label"] = "临时库" if stock_source == STOCK_SOURCE_TEMPORARY else "正式库"
            item["allocation_group_key"] = item["data"].get("allocation_group_key") or ""
            item["requested_quantity_snapshot"] = item["data"].get("requested_quantity_snapshot")
            item["stock_quantity"] = locked_stock_quantity(item["data"], current_stock_quantity)
            item["current_stock_quantity"] = current_stock_quantity
            item["stock_quantity_locked"] = numeric_or_none(item["data"].get("stock_quantity_snapshot")) is not None or numeric_or_none(item["data"].get("available_quantity_snapshot")) is not None
            batches = material_batch_rows(cursor, item["material_id"], stock_source=stock_source)
            item["batches"] = batches
            plan, shortage = build_fifo_plan(
                cursor,
                item["material_id"],
                item.get("request_quantity") or 0,
                stock_source=stock_source,
            )
            item["suggested_batches"] = [
                {
                    "batch_id": batch["id"],
                    "batch_no": batch["batch_no"],
                    "quantity": batch.get("suggested_quantity") or 0,
                    "available_quantity": batch.get("quantity") or 0,
                    "received_date": batch.get("received_date") or "",
                    "age_days": batch.get("age_days") or 0,
                }
                for batch in plan
                if float(batch.get("suggested_quantity") or 0) > 0
            ]
            item["stock_shortage"] = max(0, shortage)
        form["items"].append(item)
    cursor.execute(
        """
        SELECT t.*, u.display_name AS assignee_name
        FROM workflow_tasks t
        LEFT JOIN users u ON u.id = t.assignee_id
        WHERE t.form_id = ?
        ORDER BY t.id
        """,
        (form_id,),
    )
    form["tasks"] = []
    for row in cursor.fetchall():
        task = dict(row)
        task["data"] = parse_json(task.pop("data_json", "{}"), {})
        form["tasks"].append(task)
    leader_signatures = [
        task.get("signature") or ""
        for task in form["tasks"]
        if task.get("step_code") in {"leader_acceptance", "leader_claim"} and task.get("signature")
    ]
    form["leader_signatures"] = "、".join(dict.fromkeys(leader_signatures))
    if (
        form.get("origin_type") == "temporary_transfer"
        and form.get("origin_ref_id")
    ):
        cursor.execute(
            """
            SELECT id, transfer_no, status, temporary_quantity_snapshot,
                   obligation_quantity_snapshot, target_acceptance_quantity,
                   accepted_quantity, assigned_buyer_id, created_at, updated_at
            FROM inventory_transfer_tasks
            WHERE id = ?
            """,
            (int(form["origin_ref_id"]),),
        )
        transfer_task = cursor.fetchone()
        form["transfer_task"] = dict(transfer_task) if transfer_task else None
        if form["transfer_task"]:
            form["transfer_task"]["remaining_quantity"] = max(
                0.0,
                float(form["transfer_task"]["target_acceptance_quantity"] or 0)
                - float(form["transfer_task"]["accepted_quantity"] or 0),
            )
    return form
