"""拆分部分转正的验收明细，按入库记录迁移 RK。"""

from collections import defaultdict
import json

from warehouse_suit.claim_split_service import copy_tasks, create_split_form
from warehouse_suit.numbering import next_form_no
from warehouse_suit.settings import parse_json


def _acceptance_items(cursor, form_id):
    cursor.execute("SELECT * FROM workflow_items WHERE form_id = ? ORDER BY id", (form_id,))
    items = [dict(row) for row in cursor.fetchall()]
    batches_by_item = {}
    for item in items:
        data = parse_json(item["data_json"], {})
        batches = {int(entry["batch_id"]) for entry in data.get("inbound_batches", []) if entry.get("batch_id")}
        if data.get("inbound_batch_id"):
            batches.add(int(data["inbound_batch_id"]))
        batches_by_item[item["id"]] = batches
    return items, batches_by_item


def prepare_acceptances(cursor, batch_ids, date_value, whole_form_id=None):
    """整张迁移时保留流程 ID，部分迁移时移动选中的验收明细。"""
    selected = set(batch_ids)
    marks = ",".join("?" for _ in selected)
    cursor.execute(
        f"""
        SELECT id, batch_id, operation_key FROM stock_records
        WHERE batch_id IN ({marks}) AND operation_key GLOB 'acceptance:*:*:inbound'
        ORDER BY id
        """,
        sorted(selected),
    )
    records = defaultdict(list)
    for row in cursor.fetchall():
        records[int(row["operation_key"].split(":")[1])].append(row)
    promoted_forms = {}
    for form_id, inbound in records.items():
        cursor.execute("SELECT * FROM workflow_forms WHERE id = ? AND form_type = 'acceptance'", (form_id,))
        form = dict(cursor.fetchone())
        if form["status"] != "completed":
            raise ValueError(f"验收单 {form['form_no']} 尚未完成入库，不能转入正式库")
        items, batches_by_item = _acceptance_items(cursor, form_id)
        moving = set()
        for item_id, batches in batches_by_item.items():
            if batches & selected:
                if batches - selected:
                    raise ValueError(f"验收单 {form['form_no']} 同一验收明细的批次需一起转正")
                moving.add(item_id)
        if not moving:
            raise ValueError(f"验收单 {form['form_no']} 缺少选中批次的入库明细")
        attached = set().union(*batches_by_item.values())
        if len(moving) == len(items) or (form_id == whole_form_id and attached <= selected):
            promoted_forms[form_id] = form_id
            continue
        split = create_split_form(cursor, form, date_value=date_value, prefix="YS")
        promoted_forms[form_id] = split["id"]
        copy_tasks(cursor, form_id, split["id"])
        for item_id in moving:
            cursor.execute("UPDATE workflow_items SET form_id = ? WHERE id = ?", (split["id"], item_id))
            cursor.execute(
                "UPDATE material_attachments SET workflow_form_id = ? WHERE workflow_item_id = ?",
                (split["id"], item_id),
            )
        for row in inbound:
            candidates = [item_id for item_id in moving if row["batch_id"] in batches_by_item[item_id]]
            if len(candidates) != 1:
                raise ValueError(f"验收单 {form['form_no']} 的批次 {row['batch_id']} 归属不明确")
            cursor.execute(
                """
                UPDATE stock_records SET operation_key = ?, workflow_item_id = ?,
                    remark = replace(remark, ?, ?)
                WHERE id = ?
                """,
                (
                    f"acceptance:{split['id']}:{candidates[0]}:inbound", candidates[0],
                    form["form_no"], split["form_no"], row["id"],
                ),
            )
        for target_id, keep in ((form_id, set(batches_by_item) - moving), (split["id"], moving)):
            _refresh_acceptance(cursor, target_id, [item for item in items if item["id"] in keep])

    return promoted_forms


def _refresh_acceptance(cursor, form_id, items):
    """保留各自的验收明细、金额和验收人填写的检查结果。"""
    item_ids = {item["id"] for item in items}
    total = sum(
        float(parse_json(item["data_json"], {}).get("actual_quantity", item["arrival_quantity"]))
        * float(item["unit_price"]) for item in items
    )
    cursor.execute("UPDATE workflow_forms SET total_amount = ? WHERE id = ?", (total, form_id))
    _refresh_inbound_number(cursor, form_id)
    cursor.execute("SELECT id, data_json FROM workflow_tasks WHERE form_id = ?", (form_id,))
    for row in cursor.fetchall():
        data = parse_json(row["data_json"], {})
        if "items" in data:
            data["items"] = [item for item in data["items"] if int(item["id"]) in item_ids]
            cursor.execute(
                "UPDATE workflow_tasks SET data_json = ? WHERE id = ?",
                (json.dumps(data, ensure_ascii=False), row["id"]),
            )


def _refresh_inbound_number(cursor, form_id):
    cursor.execute(
        """
        SELECT form_no FROM stock_records
        WHERE operation_key GLOB ? AND form_no GLOB 'RK[0-9]*'
        ORDER BY id DESC LIMIT 1
        """,
        (f"acceptance:{form_id}:*:inbound",),
    )
    row = cursor.fetchone()
    if row:
        cursor.execute(
            "UPDATE workflow_forms SET data_json = json_set(data_json, '$.inbound_no', ?) WHERE id = ?",
            (row["form_no"], form_id),
        )


def _rk_exists(cursor, number, exclude_batch_ids=()):
    marks = ",".join("?" for _ in exclude_batch_ids) or "NULL"
    batch_filter = f"AND id NOT IN ({marks})" if exclude_batch_ids else ""
    record_filter = f"AND (batch_id IS NULL OR batch_id NOT IN ({marks}))" if exclude_batch_ids else ""
    cursor.execute(
        f"""
        SELECT 1 FROM material_batches WHERE source_form_no = ? {batch_filter}
        UNION ALL
        SELECT 1 FROM stock_records WHERE form_no = ? {record_filter}
        LIMIT 1
        """,
        (number, *exclude_batch_ids, number, *exclude_batch_ids),
    )
    return cursor.fetchone() is not None


def migrate_receipt_numbers(cursor, batch_ids, date_value):
    """按验收入库归属迁移 RK，原号仍在使用时保留该流水位。"""
    marks = ",".join("?" for _ in batch_ids)
    cursor.execute(
        f"""
        SELECT batch_id, form_no, operation_key FROM stock_records
        WHERE batch_id IN ({marks}) AND form_no GLOB 'RK[0-9]*'
        ORDER BY id
        """,
        batch_ids,
    )
    groups = defaultdict(set)
    for row in cursor.fetchall():
        key = (row["operation_key"] or "").split(":")
        owner = int(key[1]) if key[0] == "acceptance" else 0
        groups[owner, row["form_no"]].add(row["batch_id"])
    cursor.execute(
        f"SELECT id, source_form_no FROM material_batches WHERE id IN ({marks}) AND source_form_no GLOB 'RK[0-9]*'",
        batch_ids,
    )
    known = {(batch_id, number) for (_, number), ids in groups.items() for batch_id in ids}
    for row in cursor.fetchall():
        if (row["id"], row["source_form_no"]) not in known:
            groups[0, row["source_form_no"]].add(row["id"])
    changes = []
    for (owner, old), ids in sorted(groups.items()):
        if old.startswith("RK" + date_value.replace("-", "")) and not _rk_exists(cursor, old, sorted(ids)):
            continue
        new = next_form_no(cursor, "RK", date_value)
        placeholders = ",".join("?" for _ in ids)
        cursor.execute(
            f"UPDATE stock_records SET form_no = ? WHERE batch_id IN ({placeholders}) AND form_no = ?",
            (new, *sorted(ids), old),
        )
        cursor.execute(
            f"UPDATE material_batches SET source_form_no = ? WHERE id IN ({placeholders}) AND source_form_no = ?",
            (new, *sorted(ids), old),
        )
        if owner:
            _sync_receipt_items(cursor, owner, ids, old, new)
        changes.append({"form_id": owner, "batch_ids": sorted(ids), "old": old, "new": new})
    vacated = {change["old"] for change in changes if not _rk_exists(cursor, change["old"])}
    return changes, vacated


def _sync_receipt_items(cursor, form_id, batch_ids, old, new):
    items, batches_by_item = _acceptance_items(cursor, form_id)
    for item in items:
        if not batch_ids & batches_by_item[item["id"]]:
            continue
        data = parse_json(item["data_json"], {})
        if data.get("inbound_no") in (None, "", old):
            data["inbound_no"] = new
        for batch in data.get("inbound_batches", []):
            if int(batch.get("batch_id") or 0) in batch_ids and batch.get("inbound_no") in (None, "", old):
                batch["inbound_no"] = new
        cursor.execute(
            "UPDATE workflow_items SET data_json = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), item["id"]),
        )
    _refresh_inbound_number(cursor, form_id)
