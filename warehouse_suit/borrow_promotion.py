"""按实际批次拆分借用、归还和借用转领用。"""

from collections import defaultdict
import json

from warehouse_suit.borrow_service import (
    borrow_form_snapshot, borrow_origin_workflow_item_id, borrow_outbound_batches, borrow_settled_status,
)
from warehouse_suit.claim_return_service import RETURN_DAMAGED
from warehouse_suit.claim_split_service import copy_tasks, create_split_form, insert_copy
from warehouse_suit.inventory_constants import (
    BUSINESS_TYPE_BORROW_OUTBOUND, BUSINESS_TYPE_BORROW_RETURN_INBOUND, STOCK_SOURCE_FORMAL,
)
from warehouse_suit.issued_material_service import take_batch_slice
from warehouse_suit.settings import parse_json


def _batch_id(batch):
    return int(batch.get("batch_id") or batch["id"])


def _quantity(rows):
    return sum(float(row["quantity"]) for row in rows)


def _partition(rows, selected, batch_id):
    groups = defaultdict(list)
    for row in rows:
        groups[batch_id(row) in selected].append(row)
    return groups


def _new_form(cursor, form, prefix, date_value):
    target = create_split_form(cursor, form, date_value=date_value, prefix=prefix)
    copy_tasks(cursor, form["id"], target["id"])
    return target


def _save(cursor, table, row, values, copy=False):
    if copy:
        return insert_copy(cursor, table, row, values)
    columns = ", ".join(f"{key} = ?" for key in values)
    cursor.execute(f"UPDATE {table} SET {columns} WHERE id = ?", [*values.values(), row["id"]])
    return row["id"]


def split_borrows_for_promotion(cursor, batch_ids, date_value):
    selected = set(batch_ids)
    marks = ",".join("?" for _ in selected)
    cursor.execute(
        f"""
        SELECT DISTINCT f.* FROM stock_records sr
        JOIN workflow_items wi ON wi.id = sr.workflow_item_id
        JOIN workflow_forms f ON f.id = wi.form_id
        WHERE f.form_type = 'borrow' AND sr.business_type = ? AND sr.batch_id IN ({marks})
        ORDER BY f.id
        """,
        [BUSINESS_TYPE_BORROW_OUTBOUND, *sorted(selected)],
    )
    for form in map(dict, cursor.fetchall()):
        _split_borrow_form(cursor, form, selected, date_value)


def _split_borrow_form(cursor, form, selected, date_value):
    cursor.execute(
        "SELECT * FROM workflow_items WHERE form_id = ? ORDER BY id", (form["id"],),
    )
    items = [dict(row) for row in cursor.fetchall()]
    cursor.execute(
        "SELECT * FROM borrow_records WHERE borrow_form_id = ? ORDER BY id", (form["id"],),
    )
    records_by_item = defaultdict(list)
    for record in map(dict, cursor.fetchall()):
        records_by_item[borrow_origin_workflow_item_id(cursor, record)].append(record)

    item_groups = {}
    for item in items:
        cursor.execute(
            "SELECT * FROM stock_records WHERE workflow_item_id = ? AND business_type = ? ORDER BY id",
            (item["id"], BUSINESS_TYPE_BORROW_OUTBOUND),
        )
        ledger = [dict(row) for row in cursor.fetchall()]
        item_groups[item["id"]] = _partition(ledger, selected, lambda row: row["batch_id"]) or {False: []}
    if not any(False in groups for groups in item_groups.values()):
        return
    if form["status"] != "completed":
        raise ValueError(f"借用单 {form['form_no']} 尚未完成，不能拆分")

    target = _new_form(cursor, form, "JY", date_value)
    for item in items:
        groups = item_groups[item["id"]]
        if True not in groups:
            continue
        targets = _move_borrow_item(cursor, item, form, target, groups, selected)
        record_parts = {}
        for record in records_by_item[item["id"]]:
            parts = _split_borrow_record(cursor, record, targets, selected, date_value, record_parts)
            record_parts[record["id"]] = parts
        for borrow_form, item_id in targets.values():
            cursor.execute(
                """
                SELECT SUM(br.claimed_quantity)
                FROM borrow_records br
                WHERE br.borrow_form_id = ? AND (
                    br.workflow_item_id = ?
                    OR json_extract(br.data_json, '$.origin_workflow_item_id') = ?
                )
                """,
                (borrow_form["id"], item_id, item_id),
            )
            claimed = cursor.fetchone()[0] or 0
            cursor.execute(
                """
                UPDATE workflow_items
                SET data_json = json_set(data_json, '$.claimed_quantity', ?)
                WHERE id = ?
                """,
                (claimed, item_id),
            )


def _move_borrow_item(cursor, item, form, target, groups, selected):
    data = parse_json(item["data_json"], {})
    slices = data.get("borrow_outbound", {}).get("consumed_batches", [])
    by_side = _partition(slices, selected, _batch_id)
    if len(groups) == 2 and (
        set(groups) != set(by_side)
        or any(abs(_quantity(rows) - _quantity(by_side[side])) > 1e-6 for side, rows in groups.items())
        or abs(float(item["request_quantity"]) - _quantity(slices)) > 1e-6
    ):
        raise ValueError(f"借用单 {form['form_no']} 的出库批次与数量不一致")
    targets = {}
    for side, rows in sorted(groups.items()):
        new_form = target if side else form
        values = {"form_id": new_form["id"]}
        if side:
            values["stock_source"] = STOCK_SOURCE_FORMAL
        if len(groups) == 2:
            batches = by_side[side]
            qty = _quantity(batches)
            payload = dict(data)
            payload["borrow_outbound"] = {
                **data["borrow_outbound"], "consumed_batches": batches,
                "unit_price": sum(float(entry["unit_price"]) * float(entry["quantity"]) for entry in batches) / qty,
            }
            values.update(request_quantity=qty, outbound_quantity=qty,
                          approved_quantity=qty if item["approved_quantity"] else 0,
                          data_json=json.dumps(payload, ensure_ascii=False))
        piece = _save(cursor, "workflow_items", item, values, copy=side and False in groups)
        targets[side] = (new_form, piece)
        if side:
            cursor.executemany(
                "UPDATE stock_records SET form_no = ?, workflow_item_id = ?, remark = replace(remark, ?, ?) WHERE id = ?",
                [(new_form["form_no"], piece, form["form_no"], new_form["form_no"], row["id"]) for row in rows],
            )
            cursor.execute(
                """
                UPDATE material_attachments SET workflow_form_id = ?, workflow_item_id = ?
                WHERE workflow_item_id = ? AND material_batch_id IN (
                    SELECT batch_id FROM stock_records WHERE workflow_item_id = ?
                )
                """,
                (new_form["id"], piece, item["id"], piece),
            )
    return targets


def _borrow_children(cursor, record_id):
    cursor.execute(
        """
        SELECT f.* FROM workflow_forms f
        WHERE form_type IN ('borrow_claim', 'borrow_return')
          AND CAST(json_extract(data_json, '$.borrow_record_id') AS INTEGER) = ?
        ORDER BY f.id
        """,
        (record_id,),
    )
    children = []
    for form in map(dict, cursor.fetchall()):
        cursor.execute("SELECT * FROM workflow_items WHERE form_id = ?", (form["id"],))
        items = [dict(row) for row in cursor.fetchall()]
        if len(items) != 1:
            raise ValueError(f"单据 {form['form_no']} 的借用明细数量不正确")
        children.append((form, items[0]))
    return children


def _split_borrow_record(cursor, record, targets, selected, date_value, record_parts):
    data = parse_json(record["data_json"], {})
    slices = borrow_outbound_batches(cursor, record)
    qty = float(record["quantity"])
    owned_slices = take_batch_slice(slices, 0, qty)
    groups = _partition(owned_slices, selected, _batch_id)
    if not groups and qty:
        raise ValueError(f"借用单 {record['borrow_no']} 缺少可核对的批次明细")
    if not groups:
        groups = {next(iter(targets)): []}
    if not groups.keys() <= targets.keys() or abs(sum(_quantity(rows) for rows in groups.values()) - qty) > 1e-6:
        raise ValueError(f"借用单 {record['borrow_no']} 的批次归属不一致")

    children = _borrow_children(cursor, record["id"])
    returned = data.get("returned_batch_quantities", {})
    claimed = defaultdict(float)
    for form, item in children:
        if form["form_type"] == "borrow_claim":
            for batch in parse_json(item["data_json"], {}).get("consumed_batches", []):
                claimed[_batch_id(batch) in selected] += float(batch["quantity"])
    if len(groups) == 2 and (
        abs(sum(float(q) for q in returned.values()) - float(record["returned_quantity"])) > 1e-6
        or abs(sum(claimed.values()) - float(record["claimed_quantity"])) > 1e-6
    ):
        raise ValueError(f"借用单 {record['borrow_no']} 的已归还或转领用数量无法按批次核对")

    destinations = {}
    source_slices = _partition(slices, selected, _batch_id)
    parent_id = data.get("transfer_from_record_id")
    for side, batches in sorted(groups.items()):
        form, item_id = targets[side]
        part_qty = _quantity(batches)
        returned_batches = {key: value for key, value in returned.items() if (int(key) in selected) == side}
        payload = {**data, "consumed_batches": source_slices.get(side, []), "returned_batch_quantities": returned_batches}
        if parent_id in record_parts:
            parent_piece = record_parts[parent_id].get(side)
            if parent_piece:
                payload["transfer_from_record_id"] = parent_piece
            elif record["status"] == "transferring":
                # 原持有人没有这一侧的借用记录，拒绝转借后由当前分片继续持有。
                payload["split_transfer_from_record_id"] = payload.pop("transfer_from_record_id")
        if not record["workflow_item_id"]:
            payload["origin_workflow_item_id"] = item_id
        values = {
            "borrow_form_id": form["id"], "borrow_no": form["form_no"],
            "workflow_item_id": item_id if record["workflow_item_id"] else None,
            "stock_source": STOCK_SOURCE_FORMAL if side else record["stock_source"],
        }
        if len(groups) == 2:
            values.update(quantity=part_qty, returned_quantity=sum(float(q) for q in returned_batches.values()),
                          claimed_quantity=claimed[side], return_form_id=None)
            if not values["returned_quantity"]:
                values["return_date"] = ""
            if values["returned_quantity"] + values["claimed_quantity"] - part_qty > 1e-6:
                raise ValueError(f"借用单 {record['borrow_no']} 的分批归还或转领用数量超出出库数量")
            if record["status"] == "transferring":
                payload["transfer_quantity"] = part_qty
            else:
                values["status"] = borrow_settled_status(values)
        values["data_json"] = json.dumps(payload, ensure_ascii=False)
        record_id = _save(cursor, "borrow_records", record, values, copy=side and False in groups)
        destinations[side] = (form, record_id, {**record, **values})

    for form, item in children:
        _move_borrow_child(cursor, form, item, destinations, selected, date_value, owned_slices)
    return {side: result[1] for side, result in destinations.items()}


def _move_borrow_child(cursor, form, item, destinations, selected, date_value, owned_slices):
    data = parse_json(item["data_json"], {})
    claim = form["form_type"] == "borrow_claim"
    ledger = defaultdict(list)
    slices = _partition(data.get("consumed_batches", []), selected, _batch_id) if claim else {}
    if not claim:
        cursor.execute(
            "SELECT * FROM stock_records WHERE form_no = ? AND business_type = ? ORDER BY id",
            (form["form_no"], BUSINESS_TYPE_BORROW_RETURN_INBOUND),
        )
        ledger = _partition([dict(row) for row in cursor.fetchall()], selected, lambda row: row["batch_id"])
    quantities = {side: _quantity(rows) for side, rows in (slices if claim else ledger).items()}
    if len(destinations) == 1:
        side = next(iter(destinations))
        if set(quantities) - {side}:
            raise ValueError(f"单据 {form['form_no']} 的批次与借用记录不一致")
        quantities = {side: float(item["request_quantity"])}
    elif not quantities and form["status"] == "return_inbound":
        quantities = _pending_return_quantities(item, destinations, selected, owned_slices)
    elif not quantities and form["status"] == "rejected":
        quantities = {False: float(item["request_quantity"])}
    if not quantities or not quantities.keys() <= destinations.keys():
        raise ValueError(f"单据 {form['form_no']} 的归还或转领用批次归属不一致")
    if len(quantities) == 2 and abs(sum(quantities.values()) - float(item["request_quantity"])) > 1e-6:
        raise ValueError(f"单据 {form['form_no']} 的申请数量与实际批次数量不一致")
    if len(quantities) == 1:
        side = next(iter(quantities))
        quantities[side] = float(item["request_quantity"])

    for side, qty in sorted(quantities.items()):
        borrow_form, record_id, _ = destinations[side]
        copied = side and False in quantities
        target = _new_form(cursor, form, "ZL" if claim else "GH", date_value) if copied else form
        form_data = parse_json(target["data_json"], {})
        item_data = {**data, "borrow_record_id": record_id}
        form_data["borrow_record_id"] = record_id
        if claim:
            cursor.execute("SELECT * FROM borrow_records WHERE id = ?", (record_id,))
            snapshot = borrow_form_snapshot(cursor, dict(cursor.fetchone()))
            for payload in (form_data, item_data):
                payload.update(borrow_form_id=borrow_form["id"], borrow_form_no=borrow_form["form_no"])
            form_data.update(claimed_quantity=qty, borrow_snapshot=snapshot)
            item_data["consumed_batches"] = slices.get(side, [])
        if side:
            item_data["stock_source"] = STOCK_SOURCE_FORMAL
            form_data["stock_source"] = STOCK_SOURCE_FORMAL
        _save(cursor, "workflow_forms", target, {
            "data_json": json.dumps(form_data, ensure_ascii=False),
            **({"origin_ref_id": borrow_form["id"]} if claim else {}),
        })
        item_id = _save(cursor, "workflow_items", item, {
            "form_id": target["id"], "request_quantity": qty,
            "outbound_quantity": qty if len(quantities) == 2 and item["outbound_quantity"] else item["outbound_quantity"],
            "approved_quantity": qty if len(quantities) == 2 and item["approved_quantity"] else item["approved_quantity"],
            "stock_source": item_data.get("stock_source", item["stock_source"]),
            "data_json": json.dumps(item_data, ensure_ascii=False),
        }, copy=copied)
        if claim and copied:
            _retarget_claim_sources(cursor, item["id"], item_id, {_batch_id(batch) for batch in slices[side]})
            cursor.execute(
                """
                UPDATE stock_records SET form_no = ?
                WHERE operation_key IN (
                    SELECT 'claim-return:' || operation_key FROM issued_return_requests
                    WHERE source_kind = 'borrow_claim' AND source_id = ?
                )
                """,
                (target["form_no"], item_id),
            )
            cursor.execute(
                """
                UPDATE material_batches SET source_form_no = ?
                WHERE id IN (
                    SELECT sr.batch_id FROM stock_records sr
                    JOIN issued_return_requests req ON sr.operation_key = 'claim-return:' || req.operation_key
                    WHERE req.source_kind = 'borrow_claim' AND req.source_id = ?
                )
                """,
                (target["form_no"], item_id),
            )
        if not claim and form["status"] == "completed":
            for row in ledger.get(side, []):
                cursor.execute(
                    "UPDATE stock_records SET form_no = ?, remark = replace(remark, ?, ?) WHERE id = ?",
                    (target["form_no"], form["form_no"], target["form_no"], row["id"]),
                )
            cursor.execute(
                "UPDATE borrow_records SET return_form_id = ? WHERE id = ?",
                (target["id"], record_id),
            )



def _retarget_claim_sources(cursor, old_item_id, new_item_id, batch_ids):
    for batch_id in sorted(batch_ids):
        cursor.execute(
            """
            SELECT form_id, user_id, condition_status, status
            FROM issued_return_requests
            WHERE source_kind = 'borrow_claim' AND source_id = ? AND batch_id = ?
            """,
            (old_item_id, batch_id),
        )
        for request in cursor.fetchall():
            for table, key in (("workflow_forms", "id"), ("workflow_items", "form_id")):
                cursor.execute(
                    f"UPDATE {table} SET data_json = json_set(data_json, '$.source_id', ?) WHERE {key} = ?",
                    (new_item_id, request["form_id"]),
                )
            if request["condition_status"] == RETURN_DAMAGED and request["status"] == "completed":
                cursor.execute(
                    """
                    SELECT 1 FROM issued_return_requests
                    WHERE source_kind = 'claim_outbound' AND source_id = ? AND batch_id = ?
                      AND user_id = ? AND condition_status = ? AND status = 'completed'
                    """,
                    (old_item_id, batch_id, request["user_id"], RETURN_DAMAGED),
                )
                if cursor.fetchone():
                    raise ValueError(f"批次 {batch_id} 的损坏退库记录来源不明确")
                cursor.execute(
                    """
                    UPDATE defective_inventory SET source_ref_id = ?
                    WHERE source_type = 'claim_return' AND source_ref_id = ?
                      AND source_batch_id = ? AND created_by = ?
                    """,
                    (new_item_id, old_item_id, batch_id, request["user_id"]),
                )
        for table in ("issued_return_requests", "claim_consumptions"):
            cursor.execute(
                f"UPDATE {table} SET source_id = ? WHERE source_kind = 'borrow_claim' AND source_id = ? AND batch_id = ?",
                (new_item_id, old_item_id, batch_id),
            )

def _pending_return_quantities(item, destinations, selected, owned_slices):
    left = float(item["request_quantity"])
    quantities = defaultdict(float)
    available = {}
    returned = {}
    for side, (_, _, record) in destinations.items():
        available[side] = float(record["quantity"]) - float(record["returned_quantity"]) - float(record["claimed_quantity"])
        returned.update(parse_json(record["data_json"], {}).get("returned_batch_quantities", {}))
    # 依照借用出库的批次顺序分配，扣除已归还数量和各拆分单已转领用的余额。
    for batch in owned_slices:
        batch_id = _batch_id(batch)
        side = batch_id in selected
        quantity = float(batch["quantity"])
        already_returned = min(quantity, float(returned.get(str(batch_id), 0)))
        returned[str(batch_id)] = float(returned.get(str(batch_id), 0)) - already_returned
        take = min(left, available[side], quantity - already_returned)
        if take > 1e-9:
            quantities[side] += take
            available[side] -= take
            left -= take
    if left > 1e-6:
        raise ValueError("归还申请量超过各批次未归还数量")
    return quantities
