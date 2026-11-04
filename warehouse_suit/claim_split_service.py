# -*- coding: utf-8 -*-
"""把挡住临时库转正式库的已完成申领单拆开。

只改明细归属和出库流水上的单号，不改出库数量、批次库存和出库日期。
我已领用、退库和出库编辑都按出库流水的单号找申领单，所以单号必须跟着明细走。
"""

import json
from collections import defaultdict

from warehouse_suit.db import now_text
from warehouse_suit.inventory_constants import (
    BUSINESS_TYPE_CLAIM_OUTBOUND,
    STOCK_SOURCE_FORMAL,
    STOCK_SOURCE_TEMPORARY,
)
from warehouse_suit.inventory_service import begin_inventory_transaction
from warehouse_suit.numbering import next_form_no
from warehouse_suit.promote_renumber import parse_daily_no


class ClaimSplitRequired(ValueError):
    """转移被已完成申领单挡住，可以先拆分。"""

    def __init__(self, claims):
        self.claims = [
            {"id": int(item["id"]), "form_no": item["form_no"], "reason": item["reason"]}
            for item in claims
        ]
        super().__init__(self.claims[0]["reason"])


def raise_blocking_claims(cursor, batch_ids, material_ids):
    """未完成的申领单直接拦住。已完成但混用或跨单的，交给拆分。"""
    problems = blocking_claim_problems(cursor, batch_ids, material_ids)
    hard = [item for item in problems if not item["splittable"]]
    if hard:
        raise ValueError(hard[0]["reason"])
    soft = [item for item in problems if item["splittable"]]
    if soft:
        raise ClaimSplitRequired(soft)


def split_blocking_claims(cursor, batch_ids, material_ids, user, ip_address=""):
    """拆开当前验收单碰到的全部可拆申领单。调用方负责提交。"""
    problems = blocking_claim_problems(cursor, batch_ids, material_ids)
    hard = [item for item in problems if not item["splittable"]]
    if hard:
        raise ValueError(hard[0]["reason"])
    soft = [item for item in problems if item["splittable"]]
    if not soft:
        raise ValueError("没有需要拆分的申领单")
    begin_inventory_transaction(cursor.connection)
    created = []
    for problem in soft:
        created.extend(split_claim(cursor, problem["id"]))
    _audit(
        cursor,
        user,
        "temporary_inventory.claims_split",
        "workflow_form",
        ",".join(str(item["id"]) for item in soft),
        "拆分挡住转正式库的申领单",
        {"claims": [item["form_no"] for item in soft], "created": [item["form_no"] for item in created]},
        ip_address,
    )
    return {
        "claims": [{"id": item["id"], "form_no": item["form_no"], "reason": item["reason"]} for item in soft],
        "created": created,
    }


def blocking_claim_problems(cursor, batch_ids, material_ids):
    """找出和这些临时批次有关、而且不能直接转正式库的申领单。"""
    if not batch_ids and not material_ids:
        return []
    material_marks = ",".join("?" for _ in material_ids) or "NULL"
    batch_marks = ",".join("?" for _ in batch_ids) or "NULL"
    cursor.execute(
        f"""
        SELECT DISTINCT f.id, f.form_no, f.status
        FROM workflow_forms f
        JOIN workflow_items wi ON wi.form_id = f.id
        WHERE f.form_type = 'claim'
          AND wi.stock_source = ?
          AND (
            wi.material_id IN ({material_marks})
            OR EXISTS (
                SELECT 1 FROM stock_records sr
                WHERE sr.workflow_item_id = wi.id AND sr.batch_id IN ({batch_marks})
            )
          )
        ORDER BY f.id
        """,
        [STOCK_SOURCE_TEMPORARY, *material_ids, *batch_ids],
    )
    problems = []
    for claim in cursor.fetchall():
        problem = _claim_problem(cursor, dict(claim))
        if problem:
            problems.append(problem)
    problems.sort(key=lambda item: (item["splittable"], item["id"]))
    return problems


def split_claim(cursor, claim_id):
    """原单留下正式库明细；没有正式库时留下一组临时库明细。其余按物料和验收单拆出去。"""
    form = _completed_claim(cursor, claim_id)
    formal_items, groups = _claim_groups(cursor, form)
    if not _needs_split(formal_items, groups):
        raise ValueError(f"申领单 {form['form_no']} 不需要拆分")
    ordered = {key: groups[key] for key in sorted(groups)}
    stay_key = None if formal_items else next(iter(ordered))
    return _split_claim_groups(cursor, form, ordered, stay_key)


def split_claims_for_promotion(cursor, batch_ids, date_value):
    """已完成领用单按本次选中的出库批次拆分。"""
    selected = set(batch_ids)
    marks = ",".join("?" for _ in selected)
    cursor.execute(
        f"""
        SELECT DISTINCT f.id, f.form_no, f.status
        FROM stock_records sr
        JOIN workflow_items wi ON wi.id = sr.workflow_item_id
        JOIN workflow_forms f ON f.id = wi.form_id
        WHERE f.form_type IN ('claim', 'special_claim') AND sr.business_type = ?
          AND sr.batch_id IN ({marks})
        ORDER BY f.id
        """,
        [BUSINESS_TYPE_CLAIM_OUTBOUND, *selected],
    )
    for row in cursor.fetchall():
        cursor.execute("SELECT * FROM workflow_items WHERE form_id = ? ORDER BY id", (row["id"],))
        groups = defaultdict(list)
        for item in map(dict, cursor.fetchall()):
            partitions = defaultdict(list)
            for record in _outbound_records(cursor, item["id"]):
                partitions[record["batch_id"] in selected].append(record)
            if len(partitions) == 2:
                _require_closed_quantity(row, item, partitions)
            for promote, records in (partitions or {False: []}).items():
                groups[promote].append({"item": item, "records": records})
        if True not in groups:
            continue
        if row["status"] != "completed":
            raise ValueError(f"申领单 {row['form_no']} 尚未完成，不能转移到正式库")
        if False in groups:
            cursor.execute("SELECT * FROM workflow_forms WHERE id = ?", (row["id"],))
            form = dict(cursor.fetchone())
            _split_claim_groups(cursor, form, groups, False, date_value)


def _split_claim_groups(cursor, form, ordered, stay_key, date_value=None):
    targets = {}
    created = []
    task_maps = {int(form["id"]): _task_identity(cursor, form["id"])}
    for key in ordered:
        if key == stay_key:
            targets[key] = form
            continue
        new_form = create_split_form(cursor, form, date_value=date_value)
        targets[key] = new_form
        task_maps[int(new_form["id"])] = copy_tasks(cursor, form["id"], new_form["id"])
        created.append({
            "form_id": int(new_form["id"]),
            "form_no": new_form["form_no"],
            "source_form_id": int(form["id"]),
            "source_form_no": form["form_no"],
        })
    derived = _apply_groups(cursor, form, ordered, targets, stay_key)
    _retarget_operations(cursor, form, task_maps, derived)
    _retarget_tasks(cursor, [form, *targets.values()], derived)
    if created:
        data = _load_json(form["data_json"])
        numbers = list(data.get("split_form_nos") or [])
        numbers.extend(item["form_no"] for item in created)
        data["split_form_nos"] = numbers
        cursor.execute(
            "UPDATE workflow_forms SET data_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), now_text(), form["id"]),
        )
    return created


def _claim_problem(cursor, claim):
    if claim["status"] not in {"completed", "rejected"}:
        return {
            "id": int(claim["id"]),
            "form_no": claim["form_no"],
            "splittable": False,
            "reason": f"申领单 {claim['form_no']} 尚未完成，不能转移到正式库",
        }
    cursor.execute(
        "SELECT stock_source, material_id FROM workflow_items WHERE form_id = ?",
        (claim["id"],),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    sources = {row["stock_source"] for row in rows}
    temporary_materials = {
        int(row["material_id"])
        for row in rows
        if row["stock_source"] == STOCK_SOURCE_TEMPORARY and row["material_id"]
    }
    splittable = claim["status"] == "completed"
    if STOCK_SOURCE_FORMAL in sources and STOCK_SOURCE_TEMPORARY in sources:
        reason = f"申领单 {claim['form_no']} 同时领用了临时库和正式库，不能转移到正式库"
    elif len(temporary_materials) > 1:
        reason = f"申领单 {claim['form_no']} 领用了两种临时库物料，不能转移到正式库"
    else:
        acceptance_ids = _claim_acceptance_ids(cursor, claim["id"])
        if len(acceptance_ids) <= 1:
            return None
        reason = f"申领单 {claim['form_no']} 领用了两张验收单的临时库物料，不能转移到正式库"
    return {
        "id": int(claim["id"]),
        "form_no": claim["form_no"],
        "splittable": splittable,
        "reason": reason,
    }


def _needs_split(formal_items, groups):
    if formal_items and groups:
        return True
    return len(groups) > 1


def _claim_groups(cursor, form):
    """临时库明细按物料和来源验收单分组。同一条明细跨两张验收单时，按出库流水切开。"""
    cursor.execute("SELECT * FROM workflow_items WHERE form_id = ? ORDER BY id", (form["id"],))
    items = [dict(row) for row in cursor.fetchall()]
    formal_items = [item for item in items if item.get("stock_source") != STOCK_SOURCE_TEMPORARY]
    groups = defaultdict(list)
    cache = {}
    for item in items:
        if item.get("stock_source") != STOCK_SOURCE_TEMPORARY:
            continue
        records = _outbound_records(cursor, item["id"])
        partitions = defaultdict(list)
        for record in records:
            acceptance_id = _acceptance_id(cursor, record.get("batch_id"), cache, form["form_no"])
            partitions[acceptance_id].append(record)
        if len(partitions) > 1:
            _require_closed_quantity(form, item, partitions)
        if not partitions:
            groups[(int(item["material_id"] or 0), 0)].append({"item": item, "records": []})
            continue
        for acceptance_id, partitioned in partitions.items():
            groups[(int(item["material_id"] or 0), int(acceptance_id))].append({
                "item": item,
                "records": partitioned,
            })
    return formal_items, groups


def _require_closed_quantity(form, item, partitions):
    total = sum(float(record.get("quantity") or 0) for records in partitions.values() for record in records)
    outbound = float(item.get("outbound_quantity") or 0)
    request = float(item.get("request_quantity") or 0)
    if abs(total - outbound) > 1e-4 or abs(request - outbound) > 1e-4:
        raise ValueError(f"申领单 {form['form_no']} 出库数量与流水不一致，不能拆分")


def _apply_groups(cursor, form, ordered, targets, stay_key):
    membership = defaultdict(list)
    for key, parts in ordered.items():
        for part in parts:
            membership[int(part["item"]["id"])].append((key, part))
    derived = defaultdict(list)
    for item_id, parts in membership.items():
        item = parts[0][1]["item"]
        if len(parts) == 1:
            key, part = parts[0]
            target = targets[key]
            if int(target["id"]) != int(form["id"]):
                _move_whole_item(cursor, item, form, target)
            continue
        keeper_part = next((part for part in parts if part[0] == stay_key), parts[0])[1]
        consumed = _consumed_batches(item)
        for key, part in parts:
            target = targets[key]
            quantity = sum(float(record.get("quantity") or 0) for record in part["records"])
            piece_consumed = _consumed_for(part["records"], consumed)
            if part is keeper_part:
                piece_id = int(item["id"])
                _rewrite_item(cursor, item, target["id"], quantity, piece_consumed)
            else:
                piece_id = _insert_split_item(cursor, item, target["id"], quantity, piece_consumed)
            _bind_records(cursor, part["records"], piece_id, target, form["form_no"])
            derived[int(item["id"])].append({
                "item_id": piece_id,
                "form_id": int(target["id"]),
                "quantity": quantity,
                "records": part["records"],
            })
    return derived


def _move_whole_item(cursor, item, source_form, target_form):
    cursor.execute(
        "UPDATE workflow_items SET form_id = ? WHERE id = ?",
        (target_form["id"], item["id"]),
    )
    cursor.execute(
        """
        UPDATE stock_records SET form_no = ?, remark = replace(remark, ?, ?)
        WHERE workflow_item_id = ? AND form_no = ?
        """,
        (target_form["form_no"], source_form["form_no"], target_form["form_no"], item["id"], source_form["form_no"]),
    )
    cursor.execute(
        "UPDATE temporary_issue_obligations SET claim_form_id = ? WHERE claim_item_id = ?",
        (target_form["id"], item["id"]),
    )
    cursor.execute(
        """
        UPDATE material_batches SET source_form_no = ?
        WHERE source_form_no = ? AND id IN (
            SELECT sr.batch_id FROM stock_records sr
            JOIN claim_returns ret ON ret.inbound_record_id = sr.id
            WHERE sr.workflow_item_id = ?
        )
        """,
        (target_form["form_no"], source_form["form_no"], item["id"]),
    )
    cursor.execute(
        "UPDATE material_attachments SET workflow_form_id = ? WHERE workflow_item_id = ?",
        (target_form["id"], item["id"]),
    )


def _rewrite_item(cursor, item, form_id, quantity, consumed):
    data = _load_json(item.get("data_json"))
    data["consumed_batches"] = consumed
    approved = quantity if abs(float(item.get("approved_quantity") or 0) - float(item.get("request_quantity") or 0)) <= 1e-4 else item.get("approved_quantity")
    cursor.execute(
        """
        UPDATE workflow_items
        SET form_id = ?, request_quantity = ?, outbound_quantity = ?, approved_quantity = ?, data_json = ?
        WHERE id = ?
        """,
        (form_id, quantity, quantity, approved, json.dumps(data, ensure_ascii=False), item["id"]),
    )
    cursor.execute(
        "UPDATE material_attachments SET workflow_form_id = ? WHERE workflow_item_id = ?",
        (form_id, item["id"]),
    )


def _insert_split_item(cursor, item, form_id, quantity, consumed):
    data = _load_json(item.get("data_json"))
    data["consumed_batches"] = consumed
    approved = quantity if abs(float(item.get("approved_quantity") or 0) - float(item.get("request_quantity") or 0)) <= 1e-4 else 0
    return insert_copy(cursor, "workflow_items", item, {
        "form_id": form_id,
        "request_quantity": quantity,
        "outbound_quantity": quantity,
        "approved_quantity": approved,
        "data_json": json.dumps(data, ensure_ascii=False),
    })


def _bind_records(cursor, records, item_id, form, old_no):
    for record in records:
        cursor.execute(
            "UPDATE stock_records SET workflow_item_id = ?, form_no = ?, remark = replace(remark, ?, ?) WHERE id = ?",
            (item_id, form["form_no"], old_no, form["form_no"], record["id"]),
        )
        cursor.execute(
            """
            UPDATE temporary_issue_obligations
            SET claim_item_id = ?, claim_form_id = ?
            WHERE stock_record_id = ?
            """,
            (item_id, form["id"], record["id"]),
        )
        cursor.execute(
            """
            UPDATE stock_records SET workflow_item_id = ?, form_no = ?, remark = replace(remark, ?, ?)
            WHERE id IN (SELECT inbound_record_id FROM claim_returns WHERE outbound_record_id = ?)
            """,
            (item_id, form["form_no"], old_no, form["form_no"], record["id"]),
        )
        cursor.execute(
            """
            UPDATE material_batches SET source_form_no = ?
            WHERE id IN (
                SELECT sr.batch_id FROM stock_records sr
                JOIN claim_returns ret ON ret.inbound_record_id = sr.id
                WHERE ret.outbound_record_id = ?
            )
            """,
            (form["form_no"], record["id"]),
        )


def _retarget_operations(cursor, form, task_maps, derived):
    cursor.execute(
        "SELECT * FROM claim_outbound_operations WHERE form_id = ? ORDER BY id",
        (form["id"],),
    )
    operations = [dict(row) for row in cursor.fetchall()]
    for operation in operations:
        payload = _load_json(operation.get("payload_json"))
        buckets = _bucket_items(cursor, payload.get("items") or [], derived, form, operation["operation_key"])
        stayed = buckets.get(int(form["id"])) or []
        if stayed:
            payload["items"] = stayed
            cursor.execute(
                "UPDATE claim_outbound_operations SET payload_json = ? WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), operation["id"]),
            )
        else:
            cursor.execute("DELETE FROM claim_outbound_operations WHERE id = ?", (operation["id"],))
        for form_id, items in buckets.items():
            if form_id == int(form["id"]) or not items:
                continue
            copied = dict(payload)
            copied["items"] = items
            old_task_id = int(operation.get("task_id") or 0)
            insert_copy(cursor, "claim_outbound_operations", operation, {
                "form_id": form_id,
                "task_id": task_maps.get(form_id, {}).get(old_task_id),
                "operation_key": f"{operation['operation_key']}:split:{form_id}",
                "payload_json": json.dumps(copied, ensure_ascii=False),
            })


def _retarget_tasks(cursor, forms, derived):
    seen = set()
    for form in forms:
        form_id = int(form["id"])
        if form_id in seen:
            continue
        seen.add(form_id)
        cursor.execute("SELECT id, data_json FROM workflow_tasks WHERE form_id = ? ORDER BY id", (form_id,))
        for task in cursor.fetchall():
            data = _load_json(task["data_json"])
            operations = data.get("outbound_operations")
            if not isinstance(operations, list):
                continue
            rewritten = []
            for operation in operations:
                if not isinstance(operation, dict):
                    continue
                items = _bucket_items(cursor, operation.get("items") or [], derived, form, operation.get("operation_key")).get(form_id) or []
                if not items:
                    continue
                copied = dict(operation)
                copied["items"] = items
                rewritten.append(copied)
            data["outbound_operations"] = rewritten
            cursor.execute(
                "UPDATE workflow_tasks SET data_json = ? WHERE id = ?",
                (json.dumps(data, ensure_ascii=False), task["id"]),
            )


def _bucket_items(cursor, entries, derived, form, operation_key):
    form_no = form["form_no"]
    buckets = defaultdict(list)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        source_id = int(entry.get("item_id") or 0)
        pieces = derived.get(source_id)
        if pieces:
            totals = []
            all_qty = sum(piece["quantity"] for piece in pieces)
            operation_qty = float(entry.get("quantity") or 0)
            key = f"claim:{form['id']}:{source_id}:outbound:{operation_key}"
            for piece in pieces:
                records = piece["records"]
                if abs(operation_qty - all_qty) > 1e-4:
                    records = [
                        row for row in records
                        if row["operation_key"] == key or row["operation_key"] == f"{key}:{row['batch_id']}"
                    ]
                if records:
                    last_id = max(row["id"] for row in records)
                    totals.append((piece, sum(row["quantity"] for row in records), last_id))
            if abs(sum(qty for _, qty, _ in totals) - operation_qty) > 1e-4:
                raise ValueError(f"申领单 {form_no} 出库操作记录与实际批次不一致，不能拆分")
            for piece, qty, last_id in totals:
                buckets[piece["form_id"]].append({
                    "item_id": piece["item_id"],
                    "quantity": qty,
                    "cumulative_quantity": sum(
                        row["quantity"] for row in piece["records"] if row["id"] <= last_id
                    ),
                })
            continue
        cursor.execute("SELECT form_id FROM workflow_items WHERE id = ?", (source_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"申领单 {form_no} 的出库明细不存在，不能拆分")
        kept = dict(entry)
        kept["item_id"] = source_id
        buckets[int(row["form_id"])].append(kept)
    return buckets


def create_split_form(cursor, form, *, date_value=None, prefix=None):
    data = _load_json(form.get("data_json"))
    data.pop("split_form_nos", None)
    data["split_from_form_no"] = form["form_no"]
    data["split_from_form_id"] = int(form["id"])
    parsed = parse_daily_no(form["form_no"])
    prefix = prefix or (parsed[0] if parsed else "CK")
    number = next_form_no(cursor, prefix, date_value)
    title = form["title"]
    title = title.replace(form["form_no"], number) if form["form_no"] in title else f"{title} {number}".strip()
    new_id = insert_copy(cursor, "workflow_forms", form, {
        "form_no": number,
        "title": title,
        "data_json": json.dumps(data, ensure_ascii=False),
        "updated_at": now_text(),
    })
    cursor.execute("SELECT * FROM workflow_forms WHERE id = ?", (new_id,))
    return dict(cursor.fetchone())


def copy_tasks(cursor, source_form_id, new_form_id):
    cursor.execute("SELECT * FROM workflow_tasks WHERE form_id = ? ORDER BY id", (source_form_id,))
    mapping = {}
    for task in cursor.fetchall():
        task = dict(task)
        mapping[int(task["id"])] = insert_copy(cursor, "workflow_tasks", task, {"form_id": new_form_id})
    return mapping


def _task_identity(cursor, form_id):
    cursor.execute("SELECT id FROM workflow_tasks WHERE form_id = ?", (form_id,))
    return {int(row["id"]): int(row["id"]) for row in cursor.fetchall()}


def _completed_claim(cursor, claim_id):
    cursor.execute(
        "SELECT * FROM workflow_forms WHERE id = ? AND form_type = 'claim'",
        (int(claim_id),),
    )
    form = cursor.fetchone()
    if not form:
        raise ValueError("申领单不存在")
    form = dict(form)
    if form["status"] != "completed":
        raise ValueError(f"申领单 {form['form_no']} 尚未完成，不能拆分")
    return form


def _outbound_records(cursor, item_id):
    cursor.execute(
        """
        SELECT * FROM stock_records
        WHERE workflow_item_id = ? AND business_type = ?
        ORDER BY id
        """,
        (int(item_id), BUSINESS_TYPE_CLAIM_OUTBOUND),
    )
    return [dict(row) for row in cursor.fetchall()]


def _claim_acceptance_ids(cursor, claim_form_id):
    found = set()
    cursor.execute(
        """
        SELECT DISTINCT sr.batch_id
        FROM stock_records sr
        JOIN workflow_items wi ON wi.id = sr.workflow_item_id
        WHERE wi.form_id = ? AND sr.business_type = ?
        """,
        (int(claim_form_id), BUSINESS_TYPE_CLAIM_OUTBOUND),
    )
    for row in cursor.fetchall():
        found.update(_acceptance_ids_of_batch(cursor, row["batch_id"]))
    return found


def _acceptance_id(cursor, batch_id, cache, form_no):
    batch_id = int(batch_id or 0)
    if batch_id not in cache:
        found = _acceptance_ids_of_batch(cursor, batch_id)
        if len(found) > 1:
            raise ValueError(f"申领单 {form_no} 的出库批次对应多张验收单，不能拆分")
        cache[batch_id] = next(iter(found)) if found else 0
    return cache[batch_id]


def _acceptance_ids_of_batch(cursor, batch_id):
    cursor.execute(
        """
        SELECT operation_key FROM stock_records
        WHERE batch_id = ? AND operation_key GLOB 'acceptance:*:*:inbound'
        """,
        (int(batch_id or 0),),
    )
    found = set()
    for row in cursor.fetchall():
        parts = str(row["operation_key"] or "").split(":")
        if len(parts) >= 4 and parts[0] == "acceptance" and parts[-1] == "inbound" and parts[1].isdigit():
            found.add(int(parts[1]))
    return found


def _consumed_batches(item):
    data = _load_json(item.get("data_json"))
    consumed = data.get("consumed_batches") or []
    return [batch for batch in consumed if isinstance(batch, dict)]


def _consumed_for(records, consumed):
    record_ids = {int(record["id"]) for record in records}
    batch_ids = {int(record.get("batch_id") or 0) for record in records}
    chosen = []
    for entry in consumed:
        stock_id = int(entry.get("stock_record_id") or 0)
        batch_id = int(entry.get("batch_id") or 0)
        if stock_id and stock_id in record_ids:
            chosen.append(entry)
        elif not stock_id and batch_id in batch_ids:
            chosen.append(entry)
    return chosen


def insert_copy(cursor, table, row, overrides):
    columns = [name for name in _table_columns(cursor, table) if name != "id"]
    data = dict(row)
    data.update(overrides)
    placeholders = ", ".join("?" for _ in columns)
    cursor.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        [data.get(column) for column in columns],
    )
    return int(cursor.lastrowid)


def _table_columns(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cursor.fetchall()]


def _load_json(text):
    if isinstance(text, dict):
        return dict(text)
    try:
        data = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _audit(cursor, user, action, target_type, target_id, summary, data, ip_address):
    cursor.execute(
        """
        INSERT INTO audit_logs
            (user_id, username, action, target_type, target_id, summary, data_json, ip_address, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user.get("id") if user else None,
            user.get("username") if user else "",
            action,
            target_type,
            str(target_id or ""),
            summary,
            json.dumps(data or {}, ensure_ascii=False),
            str(ip_address or ""),
            now_text(),
        ),
    )
