"""采购申请与采购汇总。个人申请不填编码，保存至库房后状态为已登记。"""

from __future__ import annotations

import json

from warehouse_suit.db import now_text
from warehouse_suit.material_service import next_material_code, upsert_material_master
from warehouse_suit.coding_agent import assert_group_codes_match_job, standard_description_from_job
from warehouse_suit.numbering import next_form_no
from warehouse_suit.project_service import require_project_access
from warehouse_suit.usage_kind import pack_quantity_value, validate_usage_kind
from warehouse_suit.validation import quantity_value, validate_material_code_value
from warehouse_suit.workflow_service import create_workflow_tasks, workflow_generated_title


PENDING = "pending_summary"
RETURNED = "returned"
SUMMARIZED = "summarized"
REGISTERED = "registered"


def _json(value):
    return json.dumps(value or {}, ensure_ascii=False)


def _rows(cursor, sql, params=()):
    cursor.execute(sql, params)
    return [dict(row) for row in cursor.fetchall()]


def _line_from_payload(cursor, user, raw):
    name = str(raw.get("name") or raw.get("material_name") or "").strip()
    if not name:
        raise ValueError("请填写物料名称")
    brand_model = str(raw.get("brand_model") or "").strip()
    spec = str(raw.get("spec") or "").strip()
    purchase_project = require_project_access(cursor, user, raw.get("purchase_project"))
    usage_kind = validate_usage_kind(raw.get("usage_kind"))
    request_quantity = quantity_value(raw.get("request_quantity"), "申请数量", positive=True)
    budget_price = quantity_value(raw.get("budget_unit_price"), "预算单价", positive=False)
    budget_total = request_quantity * budget_price
    pack_quantity = pack_quantity_value(raw.get("pack_quantity") or 1)
    electronic = str(raw.get("electronic_purchase") or "否").strip()
    if electronic not in {"是", "否"}:
        raise ValueError("电子采购必须选择是或否")
    platform = str(raw.get("platform") or "").strip()
    if electronic == "是" and not platform:
        raise ValueError("电子采购为是时必须填写平台")
    if electronic == "否" and platform:
        raise ValueError("电子采购为否时平台必须为空")
    return {
        "name": name,
        "brand_model": brand_model,
        "spec": spec,
        "unit": str(raw.get("unit") or "").strip(),
        "purchase_project": purchase_project,
        "usage_kind": usage_kind,
        "request_quantity": request_quantity,
        "budget_unit_price": budget_price,
        "budget_total": budget_total,
        "pack_quantity": pack_quantity,
        "arrival_unit": str(raw.get("arrival_unit") or "").strip(),
        "electronic_purchase": electronic,
        "platform": platform,
        "purchase_applicant": user.get("display_name") or user.get("username") or "",
        "material_code": "",
    }


def create_purchase_request(cursor, user, payload):
    raw_items = payload.get("items") or []
    if not raw_items:
        raise ValueError("采购申请至少需要一行物料")
    lines = [_line_from_payload(cursor, user, raw) for raw in raw_items]
    form_no = next_form_no(cursor, "CG")
    timestamp = now_text()
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, data_json, created_at, updated_at)
        VALUES (?, 'purchase_request', ?, ?, 'pending_summary', ?, ?, ?, ?)
        """,
        (
            form_no,
            f"{form_no} 中光学（杭州）物料采购申请",
            PENDING,
            int(user["id"]),
            _json({"remark": str(payload.get("remark") or "").strip()}),
            timestamp,
            timestamp,
        ),
    )
    form_id = int(cursor.lastrowid)
    for line in lines:
        cursor.execute(
            """
            INSERT INTO workflow_items
                (form_id, material_code, material_name, brand_model, spec, unit,
                 request_quantity, unit_price, purchase_applicant, purchase_project, data_json)
            VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                form_id,
                line["name"],
                line["brand_model"],
                line["spec"],
                line["unit"],
                line["request_quantity"],
                line["budget_unit_price"],
                line["purchase_applicant"],
                line["purchase_project"],
                _json(line),
            ),
        )
    return form_id


def _request_rows(cursor, where, params):
    cursor.execute(
        f"""
        SELECT f.*, COALESCE(u.display_name, u.username, '') AS applicant_name
        FROM workflow_forms f
        LEFT JOIN users u ON u.id = f.applicant_id
        WHERE f.form_type = 'purchase_request' AND {where}
        ORDER BY f.id DESC
        """,
        params,
    )
    forms = [dict(row) for row in cursor.fetchall()]
    for form in forms:
        form["items"] = _rows(cursor, "SELECT * FROM workflow_items WHERE form_id = ? ORDER BY id", (form["id"],))
    return forms


def _annotate_purchase_acceptance(cursor, items):
    """给汇总行标上赋码和验收入库状态。"""
    if not items:
        return
    cursor.execute(
        """
        SELECT wi.data_json, f.status
        FROM workflow_items wi
        JOIN workflow_forms f ON f.id = wi.form_id
        WHERE f.form_type = 'acceptance'
          AND f.status NOT IN ('rejected', 'cancelled')
        """
    )
    linked = {}
    for row in cursor.fetchall():
        data = json.loads(row["data_json"] or "{}")
        purchase_item_id = int(data.get("purchase_item_id") or 0)
        status = row["status"] or ""
        if purchase_item_id and linked.get(purchase_item_id) != "completed":
            linked[purchase_item_id] = status
    codes = [str(item.get("material_code") or "").strip() for item in items if str(item.get("material_code") or "").strip()]
    material_ids = {}
    if codes:
        placeholders = ",".join("?" for _ in codes)
        cursor.execute(
            f"SELECT id, material_code FROM materials WHERE material_code IN ({placeholders})",
            codes,
        )
        material_ids = {row["material_code"]: row["id"] for row in cursor.fetchall()}
    for item in items:
        code = str(item.get("material_code") or "").strip()
        item["material_id"] = material_ids.get(code) or item.get("material_id") or ""
        acceptance_status = linked.get(int(item["id"]))
        if acceptance_status == "completed":
            item["coding_status"] = "已验收入库"
            item["acceptance_ready"] = False
        elif acceptance_status:
            item["coding_status"] = "验收中"
            item["acceptance_ready"] = False
        elif code:
            item["coding_status"] = "已赋码"
            item["acceptance_ready"] = True
        else:
            item["coding_status"] = "未赋码"
            item["acceptance_ready"] = False


def list_purchase_summaries(cursor, scope="history"):
    if scope == "coding":
        where, params = "f.status = ?", (SUMMARIZED,)
    else:
        where, params = "f.status IN (?, ?)", (SUMMARIZED, REGISTERED)
    cursor.execute(
        f"""
        SELECT f.*, COALESCE(u.display_name, u.username, '') AS applicant_name
        FROM workflow_forms f
        LEFT JOIN users u ON u.id = f.applicant_id
        WHERE f.form_type = 'purchase_summary' AND {where}
        ORDER BY f.id DESC
        """,
        params,
    )
    forms = [dict(row) for row in cursor.fetchall()]
    for form in forms:
        form["items"] = _rows(cursor, "SELECT * FROM workflow_items WHERE form_id = ? ORDER BY id", (form["id"],))
        if scope != "coding":
            _annotate_purchase_acceptance(cursor, form["items"])
    return forms


def list_purchase_requests(cursor, user, scope="mine"):
    from warehouse_suit.workflow_service import user_has_permission

    if scope == "pending" and user_has_permission(cursor, user, "manage_purchase_summary"):
        return _request_rows(cursor, "f.status = ?", (PENDING,))
    return _request_rows(cursor, "f.applicant_id = ?", (int(user["id"]),))


def return_purchase_request(cursor, user, form_id, reason):
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("退回必须填写原因")
    cursor.execute(
        "SELECT * FROM workflow_forms WHERE id = ? AND form_type = 'purchase_request'",
        (int(form_id),),
    )
    form = cursor.fetchone()
    if not form or form["status"] != PENDING:
        raise ValueError("只有待汇总的申请可以退回")
    cursor.execute(
        "UPDATE workflow_forms SET status = ?, current_step = ?, data_json = ?, updated_at = ? WHERE id = ?",
        (RETURNED, RETURNED, _json({"return_reason": reason}), now_text(), int(form_id)),
    )


def create_purchase_summary(cursor, user, request_ids):
    ids = []
    for raw in request_ids or []:
        value = int(raw)
        if value not in ids:
            ids.append(value)
    if not ids:
        raise ValueError("请选择采购申请")
    placeholders = ",".join("?" for _ in ids)
    forms = _rows(
        cursor,
        f"SELECT * FROM workflow_forms WHERE form_type = 'purchase_request' AND id IN ({placeholders})",
        ids,
    )
    if len(forms) != len(ids) or any(form["status"] != PENDING for form in forms):
        raise ValueError("只能汇总待汇总的采购申请")
    form_no = next_form_no(cursor, "HZ")
    timestamp = now_text()
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, data_json, created_at, updated_at)
        VALUES (?, 'purchase_summary', ?, ?, 'purchase_register', ?, ?, ?, ?)
        """,
        (
            form_no,
            workflow_generated_title(user, form_no),
            SUMMARIZED,
            int(user["id"]),
            _json({"request_ids": ids}),
            timestamp,
            timestamp,
        ),
    )
    summary_id = int(cursor.lastrowid)
    for request_id in ids:
        items = _rows(cursor, "SELECT * FROM workflow_items WHERE form_id = ? ORDER BY id", (request_id,))
        for item in items:
            data = json.loads(item.get("data_json") or "{}")
            data["source_request_id"] = request_id
            data["source_item_id"] = int(item["id"])
            cursor.execute(
                """
                INSERT INTO workflow_items
                    (form_id, material_code, material_name, brand_model, spec, unit,
                     request_quantity, unit_price, purchase_applicant, purchase_project, data_json)
                VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    summary_id,
                    item["material_name"],
                    item["brand_model"],
                    item["spec"],
                    item["unit"],
                    item["request_quantity"],
                    item["unit_price"],
                    item["purchase_applicant"],
                    item["purchase_project"],
                    _json(data),
                ),
            )
        cursor.execute(
            "UPDATE workflow_forms SET status = ?, current_step = ?, updated_at = ? WHERE id = ?",
            (SUMMARIZED, SUMMARIZED, timestamp, request_id),
        )
    warehouse_ids = _rows(
        cursor,
        """
        SELECT DISTINCT u.id
        FROM users u
        JOIN user_roles ur ON ur.user_id = u.id
        JOIN roles r ON r.id = ur.role_id
        WHERE u.is_active = 1 AND r.code = 'warehouse'
          AND NOT EXISTS (
              SELECT 1
              FROM user_roles ur2
              JOIN roles r2 ON r2.id = ur2.role_id
              WHERE ur2.user_id = u.id AND r2.code = 'admin'
          )
        """,
    )
    if not warehouse_ids:
        raise ValueError("未配置库房管理员")
    create_workflow_tasks(cursor, summary_id, "purchase_summary", "purchase_register", [row["id"] for row in warehouse_ids])
    return summary_id



def match_existing_materials(cursor, name, brand_model, spec):
    cursor.execute(
        """
        SELECT material_code, name, brand_model, spec, unit
        FROM materials
        WHERE TRIM(name) = ? AND TRIM(COALESCE(brand_model, '')) = ? AND TRIM(COALESCE(spec, '')) = ?
        ORDER BY material_code
        """,
        (str(name or "").strip(), str(brand_model or "").strip(), str(spec or "").strip()),
    )
    return [dict(row) for row in cursor.fetchall()]


def suggest_material_code(cursor, warehouse_code, major_code, middle_code, small_code, name, brand_model, spec):
    code, _step = next_material_code(cursor, warehouse_code, major_code, middle_code, small_code, name, brand_model, spec)
    return code


def _summary_form(cursor, form_id):
    cursor.execute(
        "SELECT * FROM workflow_forms WHERE id = ? AND form_type = 'purchase_summary'",
        (int(form_id),),
    )
    form = cursor.fetchone()
    if not form:
        raise ValueError("采购汇总不存在")
    return dict(form)


def remove_summary_line(cursor, form_id, item_id):
    form = _summary_form(cursor, form_id)
    if form["status"] == REGISTERED:
        raise ValueError("已登记的汇总不能撤行")
    cursor.execute("SELECT * FROM workflow_items WHERE id = ? AND form_id = ?", (int(item_id), int(form_id)))
    item = cursor.fetchone()
    if not item:
        raise ValueError("汇总行不存在")
    data = json.loads(item["data_json"] or "{}")
    source_id = int(data.get("source_request_id") or 0)
    cursor.execute("DELETE FROM workflow_items WHERE id = ?", (int(item_id),))
    if source_id:
        cursor.execute(
            "UPDATE workflow_forms SET status = ?, current_step = ?, updated_at = ? WHERE id = ? AND form_type = 'purchase_request'",
            (PENDING, PENDING, now_text(), source_id),
        )


def save_summary_to_warehouse(cursor, form_id, lines):
    form = _summary_form(cursor, form_id)
    if form["status"] == REGISTERED:
        raise ValueError("汇总已经登记")
    stored = _rows(cursor, "SELECT * FROM workflow_items WHERE form_id = ? ORDER BY id", (int(form_id),))
    if not stored:
        raise ValueError("汇总没有物料行")
    incoming = {int(line.get("id")): line for line in lines or []}
    if set(incoming) != {int(row["id"]) for row in stored}:
        raise ValueError("保存必须包含汇总的全部物料行")
    prepared = []
    for row in stored:
        patch = incoming[int(row["id"])]
        data = json.loads(row["data_json"] or "{}")
        code = str(patch.get("material_code") or "").strip()
        code = validate_material_code_value(code)
        cursor.execute("SELECT name FROM materials WHERE material_code = ?", (code,))
        existing = cursor.fetchone()
        if existing and str(existing["name"] or "").strip() and str(existing["name"] or "").strip() != str(row["material_name"] or "").strip():
            raise ValueError(f"{row['material_name']} 的物料编码已属于另一条物料")
        category_code, service_code = assert_group_codes_match_job(
            cursor,
            row["id"],
            patch.get("group_category_code"),
            patch.get("group_service_code"),
        )
        description = standard_description_from_job(cursor, row["id"]) if category_code else ""
        prepared.append((row, data, code, patch, category_code, service_code, description))
    for row, data, code, patch, category_code, service_code, description in prepared:
        upsert_material_master(
            cursor,
            {
                "material_code": code,
                "name": row["material_name"],
                "brand_model": row["brand_model"],
                "spec": row["spec"],
                "unit": patch.get("unit") or row["unit"] or data.get("unit") or "",
                "purchase_applicant": row["purchase_applicant"] or data.get("purchase_applicant") or "",
                "group_category_code": category_code,
                "group_service_code": service_code,
                "group_standard_description": description,
            },
        )
        data["material_code"] = code
        data["group_category_code"] = category_code
        data["group_service_code"] = service_code
        data["group_standard_description"] = description
        cursor.execute(
            "UPDATE workflow_items SET material_code = ?, data_json = ? WHERE id = ?",
            (code, _json(data), int(row["id"])),
        )
    cursor.execute(
        "UPDATE workflow_forms SET status = ?, current_step = ?, updated_at = ? WHERE id = ?",
        (REGISTERED, REGISTERED, now_text(), int(form_id)),
    )
    cursor.execute(
        "UPDATE workflow_tasks SET status = 'completed', decision = '已登记', updated_at = ? WHERE form_id = ? AND status = 'pending'",
        (now_text(), int(form_id)),
    )
