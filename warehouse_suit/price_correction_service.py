# -*- coding: utf-8 -*-
"""验收单更正单价：按入库批次改写出库快照，并重算半成品、成品成本。"""

from __future__ import annotations

import json

from warehouse_suit.db import now_text, row_to_dict
from warehouse_suit.inventory_service import begin_inventory_transaction, update_inventory_total
from warehouse_suit.settings import parse_json
from warehouse_suit.temporary_inventory_service import write_audit_log
from warehouse_suit.validation import price_value


def acceptance_price_correction_preview(cursor, form_id):
    """列出验收明细、入库批次，以及会跟着改价的出库单和制作单。"""
    form = _acceptance_form(cursor, form_id)
    items = []
    for item in _acceptance_items(cursor, form_id):
        batch_ids, block_reason = _inbound_batch_ids(cursor, item)
        affected = _affected_documents(cursor, batch_ids) if batch_ids else {
            "outbound_form_nos": [],
            "semifinished_form_nos": [],
            "finished_form_nos": [],
        }
        items.append(
            {
                "id": int(item["id"]),
                "material_code": item.get("material_code") or "",
                "material_name": item.get("material_name") or "",
                "unit_price": float(item.get("unit_price") or 0),
                "batch_ids": batch_ids,
                "correctable": not block_reason,
                "block_reason": block_reason,
                **affected,
            }
        )
    return {"form_id": int(form["id"]), "form_no": form.get("form_no") or "", "items": items}


def correct_acceptance_unit_prices(cursor, form_id, corrections, user, ip_address=""):
    """把指定验收明细的单价改到入库批次及其抄本上。任一明细没有入库批次则整次失败。"""
    begin_inventory_transaction(cursor.connection)
    form = _acceptance_form(cursor, form_id)
    items = {int(item["id"]): item for item in _acceptance_items(cursor, form_id)}
    changes = []
    batch_prices = {}
    for raw in corrections or []:
        if not isinstance(raw, dict):
            raise ValueError("更正明细格式不正确")
        item_id = int(raw.get("id") or 0)
        item = items.get(item_id)
        if not item:
            raise ValueError("验收明细不存在")
        new_price = price_value(raw.get("unit_price"), "单价")
        old_price = float(item.get("unit_price") or 0)
        if abs(new_price - old_price) <= 1e-9:
            continue
        batch_ids, block_reason = _inbound_batch_ids(cursor, item)
        if block_reason:
            raise ValueError(block_reason)
        for batch_id in batch_ids:
            if batch_id in batch_prices and abs(batch_prices[batch_id] - new_price) > 1e-9:
                raise ValueError("同一入库批次不能一次更正成两个单价")
            batch_prices[batch_id] = new_price
        changes.append(
            {
                "item_id": item_id,
                "material_name": item.get("material_name") or "",
                "old_unit_price": old_price,
                "new_unit_price": new_price,
                "batch_ids": batch_ids,
            }
        )
    if not changes:
        raise ValueError("没有需要更正的单价")

    for change in changes:
        cursor.execute(
            "UPDATE workflow_items SET unit_price = ? WHERE id = ? AND form_id = ?",
            (change["new_unit_price"], change["item_id"], form_id),
        )
    _recompute_acceptance_total(cursor, form_id)
    for batch_id, new_price in batch_prices.items():
        cursor.execute(
            "UPDATE material_batches SET unit_price = ?, updated_at = ? WHERE id = ?",
            (new_price, now_text(), batch_id),
        )
        cursor.execute(
            """
            UPDATE stock_records
            SET unit_price = ?, amount = quantity * ?
            WHERE batch_id = ?
            """,
            (new_price, new_price, batch_id),
        )
    _rewrite_outbound_snapshots(cursor, batch_prices)
    _refresh_inventory_totals(cursor, batch_prices)
    production = _recalculate_production_costs(cursor, batch_prices)
    affected = _affected_documents(cursor, list(batch_prices))
    write_audit_log(
        cursor,
        user,
        "acceptance.correct_unit_price",
        "workflow",
        form_id,
        f"{form.get('form_no') or form_id} 单价更正",
        {
            "form_no": form.get("form_no") or "",
            "items": changes,
            **affected,
            **production,
        },
        ip_address,
    )
    return {
        "form_no": form.get("form_no") or "",
        "items": changes,
        **affected,
        **production,
    }


def _acceptance_form(cursor, form_id):
    cursor.execute("SELECT * FROM workflow_forms WHERE id = ?", (int(form_id),))
    form = row_to_dict(cursor.fetchone())
    if not form:
        raise ValueError("流程不存在")
    if form.get("form_type") != "acceptance":
        raise ValueError("只有验收单可以更正单价")
    return form


def _acceptance_items(cursor, form_id):
    cursor.execute(
        "SELECT * FROM workflow_items WHERE form_id = ? ORDER BY id",
        (int(form_id),),
    )
    return [dict(row) for row in cursor.fetchall()]


def _inbound_batch_ids(cursor, item):
    """只认这条验收明细上记录的入库批次。没有记录或批次对不上就拒绝。"""
    data = parse_json(item.get("data_json"), {})
    batch_ids = []
    inbound_id = int(data.get("inbound_batch_id") or 0)
    if inbound_id:
        batch_ids.append(inbound_id)
    for batch in data.get("inbound_batches") or []:
        if not isinstance(batch, dict):
            continue
        batch_id = int(batch.get("batch_id") or 0)
        if batch_id and batch_id not in batch_ids:
            batch_ids.append(batch_id)
    name = item.get("material_name") or "该物料"
    if not batch_ids:
        return [], f"{name}：这条验收明细没有入库批次，不能更正单价"
    material_id = int(item.get("material_id") or 0)
    for batch_id in batch_ids:
        cursor.execute(
            "SELECT id, material_id FROM material_batches WHERE id = ?",
            (batch_id,),
        )
        row = row_to_dict(cursor.fetchone())
        if not row or int(row.get("material_id") or 0) != material_id:
            return [], f"{name}：入库批次不存在，不能更正单价"
    return batch_ids, ""


def _recompute_acceptance_total(cursor, form_id):
    """验收单总金额按创建时的实际数量乘单价重算。"""
    total = 0.0
    for item in _acceptance_items(cursor, form_id):
        data = parse_json(item.get("data_json"), {})
        if data.get("actual_quantity") not in (None, ""):
            quantity = float(data.get("actual_quantity") or 0)
        else:
            quantity = float(item.get("arrival_quantity") or 0) * float(data.get("pack_quantity") or 1)
        total += quantity * float(item.get("unit_price") or 0)
    cursor.execute(
        "UPDATE workflow_forms SET total_amount = ?, updated_at = ? WHERE id = ?",
        (total, now_text(), int(form_id)),
    )
    return total


def _rewrite_outbound_snapshots(cursor, batch_prices):
    """领用、借用明细里已出库批次的单价改成新单价。"""
    batch_ids = list(batch_prices)
    if not batch_ids:
        return
    marks = ",".join("?" for _ in batch_ids)
    cursor.execute(
        f"""
        SELECT DISTINCT wi.id
        FROM stock_records sr
        JOIN workflow_forms f ON f.form_no = sr.form_no
        JOIN workflow_items wi ON wi.form_id = f.id
        WHERE sr.batch_id IN ({marks})
        """,
        batch_ids,
    )
    item_ids = {int(row[0]) for row in cursor.fetchall() if row[0]}
    cursor.execute(
        f"""
        SELECT DISTINCT workflow_item_id
        FROM stock_records
        WHERE batch_id IN ({marks}) AND workflow_item_id IS NOT NULL
        """,
        batch_ids,
    )
    item_ids.update(int(row[0]) for row in cursor.fetchall() if row[0])
    for item_id in item_ids:
        cursor.execute("SELECT data_json FROM workflow_items WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        if not row:
            continue
        data = parse_json(row["data_json"], {})
        if not _rewrite_snapshot_prices(data, batch_prices):
            continue
        cursor.execute(
            "UPDATE workflow_items SET data_json = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), item_id),
        )


def _rewrite_snapshot_prices(node, batch_prices):
    changed = False
    if isinstance(node, dict):
        batch_id = int(node.get("batch_id") or 0)
        if batch_id in batch_prices and "unit_price" in node:
            node["unit_price"] = batch_prices[batch_id]
            changed = True
        for value in node.values():
            if _rewrite_snapshot_prices(value, batch_prices):
                changed = True
        batches = node.get("consumed_batches")
        touched = isinstance(batches, list) and any(
            isinstance(batch, dict) and int(batch.get("batch_id") or 0) in batch_prices
            for batch in batches
        )
        if touched and "unit_price" in node and not batch_id:
            quantity = 0.0
            amount = 0.0
            for batch in batches:
                if not isinstance(batch, dict):
                    continue
                take = float(batch.get("quantity") or 0)
                quantity += take
                amount += take * float(batch.get("unit_price") or 0)
            if quantity > 1e-9:
                node["unit_price"] = amount / quantity
                changed = True
    elif isinstance(node, list):
        for value in node:
            if _rewrite_snapshot_prices(value, batch_prices):
                changed = True
    return changed


def _refresh_inventory_totals(cursor, batch_prices):
    if not batch_prices:
        return
    marks = ",".join("?" for _ in batch_prices)
    cursor.execute(
        f"""
        SELECT DISTINCT material_id, stock_source
        FROM material_batches
        WHERE id IN ({marks})
        """,
        list(batch_prices),
    )
    for row in cursor.fetchall():
        update_inventory_total(cursor, int(row["material_id"]), row["stock_source"])


def _affected_documents(cursor, batch_ids):
    outbound = _form_numbers(
        cursor,
        """
        SELECT DISTINCT form_no
        FROM stock_records
        WHERE batch_id IN ({marks})
          AND operation_type = 'out'
          AND TRIM(COALESCE(form_no, '')) != ''
        ORDER BY form_no
        """,
        batch_ids,
    )
    semifinished = _form_numbers(
        cursor,
        """
        SELECT DISTINCT sa.acceptance_no
        FROM production_material_consumptions pmc
        JOIN semifinished_acceptances sa ON sa.id = pmc.source_id
        WHERE pmc.source_type = 'semifinished' AND pmc.batch_id IN ({marks})
        ORDER BY sa.acceptance_no
        """,
        batch_ids,
    )
    finished_direct = _form_numbers(
        cursor,
        """
        SELECT DISTINCT fa.acceptance_no
        FROM production_material_consumptions pmc
        JOIN finished_acceptances fa ON fa.id = pmc.source_id
        WHERE pmc.source_type = 'finished' AND pmc.batch_id IN ({marks})
        ORDER BY fa.acceptance_no
        """,
        batch_ids,
    )
    finished_via_semi = _form_numbers(
        cursor,
        """
        SELECT DISTINCT fa.acceptance_no
        FROM production_material_consumptions pmc
        JOIN semifinished_inventory si ON si.acceptance_id = pmc.source_id
        JOIN semifinished_consumptions sc ON sc.semifinished_inventory_id = si.id
        JOIN finished_acceptances fa ON fa.id = sc.finished_acceptance_id
        WHERE pmc.source_type = 'semifinished' AND pmc.batch_id IN ({marks})
        ORDER BY fa.acceptance_no
        """,
        batch_ids,
    )
    return {
        "outbound_form_nos": outbound,
        "semifinished_form_nos": semifinished,
        "finished_form_nos": _unique(finished_direct + finished_via_semi),
    }


def _form_numbers(cursor, sql, batch_ids):
    if not batch_ids:
        return []
    marks = ",".join("?" for _ in batch_ids)
    cursor.execute(sql.format(marks=marks), list(batch_ids))
    return _unique(str(row[0] or "").strip() for row in cursor.fetchall())


def _unique(values):
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _recalculate_production_costs(cursor, batch_prices):
    """先改物料消耗，再重算半成品成本，最后按新半成品成本重算成品。"""
    batch_ids = list(batch_prices)
    if not batch_ids:
        return {"semifinished_form_nos": [], "finished_form_nos": []}
    for batch_id, new_price in batch_prices.items():
        cursor.execute(
            """
            UPDATE production_material_consumptions
            SET unit_cost = ?, amount = quantity * ?
            WHERE batch_id = ?
            """,
            (new_price, new_price, batch_id),
        )
    _rewrite_component_batches(cursor, batch_prices)
    semi_ids = _source_ids(cursor, "semifinished", batch_ids)
    inventory_costs = {}
    semifinished_form_nos = []
    for acceptance_id in semi_ids:
        form_no, inventory_ids = _write_semifinished_cost(cursor, acceptance_id)
        if form_no:
            semifinished_form_nos.append(form_no)
        inventory_costs.update(inventory_ids)
    if inventory_costs:
        _rewrite_semifinished_consumptions(cursor, inventory_costs)
    finished_ids = set(_source_ids(cursor, "finished", batch_ids))
    if inventory_costs:
        marks = ",".join("?" for _ in inventory_costs)
        cursor.execute(
            f"""
            SELECT DISTINCT finished_acceptance_id
            FROM semifinished_consumptions
            WHERE semifinished_inventory_id IN ({marks})
            """,
            list(inventory_costs),
        )
        finished_ids.update(int(row[0]) for row in cursor.fetchall() if row[0])
    finished_form_nos = []
    for acceptance_id in sorted(finished_ids):
        form_no = _write_finished_cost(cursor, acceptance_id)
        if form_no:
            finished_form_nos.append(form_no)
    return {
        "semifinished_form_nos": _unique(semifinished_form_nos),
        "finished_form_nos": _unique(finished_form_nos),
    }


def _source_ids(cursor, source_type, batch_ids):
    marks = ",".join("?" for _ in batch_ids)
    cursor.execute(
        f"""
        SELECT DISTINCT source_id
        FROM production_material_consumptions
        WHERE source_type = ? AND batch_id IN ({marks})
        ORDER BY source_id
        """,
        [source_type, *batch_ids],
    )
    return [int(row[0]) for row in cursor.fetchall() if row[0]]


def _write_semifinished_cost(cursor, acceptance_id):
    cursor.execute(
        "SELECT acceptance_no, acceptance_quantity FROM semifinished_acceptances WHERE id = ?",
        (int(acceptance_id),),
    )
    acceptance = row_to_dict(cursor.fetchone())
    if not acceptance:
        return "", {}
    total = _consumption_amount(cursor, "semifinished", acceptance_id)
    quantity = float(acceptance.get("acceptance_quantity") or 0)
    cost_price = total / quantity if quantity > 1e-9 else 0.0
    cursor.execute(
        "UPDATE semifinished_acceptances SET cost_price = ?, updated_at = ? WHERE id = ?",
        (cost_price, now_text(), int(acceptance_id)),
    )
    cursor.execute(
        "UPDATE semifinished_inventory SET cost_price = ?, updated_at = ? WHERE acceptance_id = ?",
        (cost_price, now_text(), int(acceptance_id)),
    )
    cursor.execute(
        "SELECT id FROM semifinished_inventory WHERE acceptance_id = ?",
        (int(acceptance_id),),
    )
    inventory_costs = {int(row[0]): cost_price for row in cursor.fetchall() if row[0]}
    _write_production_workflow_cost(cursor, "semifinished", acceptance_id, cost_price, total)
    return acceptance.get("acceptance_no") or "", inventory_costs


def _write_finished_cost(cursor, acceptance_id):
    cursor.execute(
        "SELECT acceptance_no, acceptance_quantity FROM finished_acceptances WHERE id = ?",
        (int(acceptance_id),),
    )
    acceptance = row_to_dict(cursor.fetchone())
    if not acceptance:
        return ""
    material_total = _consumption_amount(cursor, "finished", acceptance_id)
    cursor.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM semifinished_consumptions WHERE finished_acceptance_id = ?",
        (int(acceptance_id),),
    )
    semi_total = float(cursor.fetchone()[0] or 0)
    total = material_total + semi_total
    quantity = float(acceptance.get("acceptance_quantity") or 0)
    cost_price = total / quantity if quantity > 1e-9 else 0.0
    cursor.execute(
        "UPDATE finished_acceptances SET cost_price = ?, updated_at = ? WHERE id = ?",
        (cost_price, now_text(), int(acceptance_id)),
    )
    cursor.execute(
        "UPDATE finished_good_inventory SET cost_price = ?, updated_at = ? WHERE acceptance_id = ?",
        (cost_price, now_text(), int(acceptance_id)),
    )
    _write_production_workflow_cost(cursor, "finished", acceptance_id, cost_price, total)
    return acceptance.get("acceptance_no") or ""


def _consumption_amount(cursor, source_type, source_id):
    cursor.execute(
        """
        SELECT COALESCE(SUM(amount), 0)
        FROM production_material_consumptions
        WHERE source_type = ? AND source_id = ?
        """,
        (source_type, int(source_id)),
    )
    return float(cursor.fetchone()[0] or 0)


def _write_production_workflow_cost(cursor, form_type, acceptance_id, cost_price, total_amount):
    cursor.execute(
        """
        SELECT wi.id, wi.form_id
        FROM workflow_items wi
        JOIN workflow_forms f ON f.id = wi.form_id
        WHERE f.form_type = ?
          AND CAST(json_extract(wi.data_json, '$.production_acceptance_id') AS INTEGER) = ?
        """,
        (form_type, int(acceptance_id)),
    )
    rows = cursor.fetchall()
    form_ids = set()
    for row in rows:
        cursor.execute("UPDATE workflow_items SET unit_price = ? WHERE id = ?", (cost_price, int(row["id"])))
        form_ids.add(int(row["form_id"]))
    for form_id in form_ids:
        cursor.execute(
            "UPDATE workflow_forms SET total_amount = ?, updated_at = ? WHERE id = ?",
            (total_amount, now_text(), form_id),
        )


def _rewrite_component_batches(cursor, batch_prices):
    _rewrite_json_column(
        cursor,
        "semifinished_acceptances",
        "components_json",
        lambda components: _replace_batch_costs(components, batch_prices),
    )
    _rewrite_json_column(
        cursor,
        "semifinished_inventory",
        "components_json",
        lambda components: _replace_batch_costs(components, batch_prices),
    )
    _rewrite_json_column(
        cursor,
        "finished_acceptances",
        "material_components_json",
        lambda components: _replace_batch_costs(components, batch_prices),
    )


def _rewrite_semifinished_consumptions(cursor, inventory_costs):
    for inventory_id, cost_price in inventory_costs.items():
        cursor.execute(
            """
            UPDATE semifinished_consumptions
            SET unit_cost = ?, amount = quantity * ?
            WHERE semifinished_inventory_id = ?
            """,
            (cost_price, cost_price, int(inventory_id)),
        )
    _rewrite_json_column(
        cursor,
        "finished_acceptances",
        "semifinished_components_json",
        lambda components: _replace_semifinished_costs(components, inventory_costs),
    )


def _rewrite_json_column(cursor, table, column, replace):
    cursor.execute(f"SELECT id, {column} FROM {table}")
    for row in cursor.fetchall():
        components = parse_json(row[column], [])
        if not isinstance(components, list):
            continue
        if not replace(components):
            continue
        cursor.execute(
            f"UPDATE {table} SET {column} = ?, updated_at = ? WHERE id = ?",
            (json.dumps(components, ensure_ascii=False), now_text(), int(row["id"])),
        )


def _replace_batch_costs(components, batch_prices):
    changed = False
    for component in components:
        if not isinstance(component, dict):
            continue
        batch_id = int(component.get("batch_id") or 0)
        if batch_id not in batch_prices:
            continue
        price = batch_prices[batch_id]
        quantity = float(component.get("total_quantity") or 0)
        component["unit_cost"] = price
        component["amount"] = quantity * price
        changed = True
    return changed


def _replace_semifinished_costs(components, inventory_costs):
    changed = False
    for component in components:
        if not isinstance(component, dict):
            continue
        inventory_id = int(component.get("semifinished_inventory_id") or 0)
        if inventory_id not in inventory_costs:
            continue
        price = inventory_costs[inventory_id]
        quantity = float(component.get("total_quantity") or 0)
        component["unit_cost"] = price
        component["amount"] = quantity * price
        changed = True
    return changed
