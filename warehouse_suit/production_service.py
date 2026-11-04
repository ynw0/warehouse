# -*- coding: utf-8 -*-
"""Production acceptance, serial, component, and consumption services."""

import json

from warehouse_suit.db import now_text, today_text
from warehouse_suit.issued_material_service import (
    PRODUCTION_HOLD_FINISHED,
    PRODUCTION_HOLD_SEMI,
    PRODUCTION_SOURCE_SEMI,
    allocate_claim_consumptions,
    list_issued_lots,
    production_consumption_claimants,
    release_production_material_hold,
)
from warehouse_suit.recycle import recycle_table_row
from warehouse_suit.numbering import next_form_no
from warehouse_suit.settings import parse_json
from warehouse_suit.validation import (
    data_validation_rule,
    quantity_value,
    validate_project_code,
    validate_serial_no,
)
from warehouse_suit.workflow_service import (
    PRODUCTION_FORM_TYPES,
    create_workflow_tasks,
    validate_validator_users,
    workflow_assignees,
    workflow_generated_title,
)


def _production_serial_prefix(kind, name):
    name = str(name or ("半成品" if kind == "semifinished" else "成品")).strip()
    return ("BP" if kind == "semifinished" else "CP") + name, name


def _serial_suffix(serial_no, prefix):
    text = str(serial_no or "")
    if not text.startswith(prefix):
        return 0
    suffix = text[-3:]
    return int(suffix) if suffix.isdigit() else 0


def _inventory_serials_for_name(cursor, kind, name, prefix):
    lookup = [("semifinished_inventory", "name"), ("finished_good_inventory", "product_name")]
    if kind == "finished":
        lookup.append(("defective_finished_goods", "product_name"))
    else:
        lookup.append(("defective_semifinished_goods", "name"))
    serials = []
    for table, column in lookup:
        cursor.execute(
            f"SELECT serial_no FROM {table} WHERE {column} = ? AND serial_no LIKE ?",
            (name, f"{prefix}%"),
        )
        serials.extend(str(row["serial_no"] or "").strip() for row in cursor.fetchall())
    return serials


def _in_flight_workflow_serials(cursor, kind, name, exclude_form_id=None):
    """其它未取消流程已写入的同名编号，不计入本单。"""
    sql = """
        SELECT wi.data_json
        FROM workflow_items wi
        JOIN workflow_forms f ON f.id = wi.form_id
        WHERE f.form_type = ?
          AND wi.material_name = ?
          AND f.status NOT IN ('cancelled', 'rejected', 'completed', 'draft')
    """
    params = [kind, name]
    if exclude_form_id:
        sql += " AND wi.form_id != ?"
        params.append(int(exclude_form_id))
    cursor.execute(sql, params)
    serials = []
    for row in cursor.fetchall():
        data = parse_json(row["data_json"], {})
        if not isinstance(data, dict):
            continue
        for item in data.get("serial_items") or []:
            if isinstance(item, dict):
                serial_no = str(item.get("serial_no") or "").strip()
                if serial_no:
                    serials.append(serial_no)
    return serials


def occupied_production_serial_numbers(cursor, kind, name, exclude_form_id=None):
    prefix, name = _production_serial_prefix(kind, name)
    occupied = set(_inventory_serials_for_name(cursor, kind, name, prefix))
    occupied.update(_in_flight_workflow_serials(cursor, kind, name, exclude_form_id))
    return occupied


def next_production_serials(cursor, kind, name, count, exclude_form_id=None):
    prefix, name = _production_serial_prefix(kind, name)
    max_serial = 0
    for serial_no in occupied_production_serial_numbers(cursor, kind, name, exclude_form_id):
        max_serial = max(max_serial, _serial_suffix(serial_no, prefix))
    return [f"{prefix}{serial:03d}" for serial in range(max_serial + 1, max_serial + int(count) + 1)]


def _inventory_conflict_label(cursor, kind, serial_no):
    conflict_tables = (
        [("semifinished_inventory", "半成品"), ("defective_semifinished_goods", "不合格半成品")]
        if kind == "semifinished"
        else [("finished_good_inventory", "成品"), ("defective_finished_goods", "不合格成品")]
    )
    for table, label in conflict_tables:
        cursor.execute(f"SELECT id FROM {table} WHERE serial_no = ? LIMIT 1", (serial_no,))
        if cursor.fetchone():
            return label
    return ""


def taken_production_serials(cursor, kind, name, serial_numbers, exclude_form_id=None):
    occupied = occupied_production_serial_numbers(cursor, kind, name, exclude_form_id)
    taken = []
    for serial_no in [str(value or "").strip() for value in serial_numbers if str(value or "").strip()]:
        if _inventory_conflict_label(cursor, kind, serial_no) or serial_no in occupied:
            taken.append(serial_no)
    return taken


def ensure_production_serials_available(cursor, kind, serial_numbers, name="", exclude_form_id=None):
    rule = data_validation_rule("serial_no")
    if not rule.get("enabled", True) or not rule.get("unique_in_database", True):
        return
    default_label = "半成品" if kind == "semifinished" else "成品"
    occupied = occupied_production_serial_numbers(cursor, kind, name, exclude_form_id)
    for serial_no in [str(value or "").strip() for value in serial_numbers if str(value or "").strip()]:
        label = _inventory_conflict_label(cursor, kind, serial_no) or (default_label if serial_no in occupied else "")
        if label:
            raise ValueError(f"{label}编号 {serial_no} 已存在")


def reassign_occupied_production_serials(cursor, kind, name, serial_items, exclude_form_id=None):
    """任一条编号已被占用时整批换成下一组空号，合格/异常按行保留。"""
    serials = [str(item.get("serial_no") or "").strip() for item in serial_items]
    if not taken_production_serials(cursor, kind, name, serials, exclude_form_id):
        return serial_items
    generated = next_production_serials(cursor, kind, name, len(serial_items), exclude_form_id=exclude_form_id)
    for index, item in enumerate(serial_items):
        item["serial_no"] = generated[index]
    return serial_items


def normalize_production_serial_items(cursor, kind, product_name, acceptance_quantity, incoming, exclude_form_id=None):
    count = int(round(float(acceptance_quantity or 0)))
    if abs(count - float(acceptance_quantity or 0)) > 1e-6:
        raise ValueError("半成品/成品验收数量必须是整数，才能逐个编号验收")
    raw_items = incoming.get("serial_items") or incoming.get("serials") or []
    serial_rule = data_validation_rule("serial_no")
    if serial_rule.get("enabled", True) and serial_rule.get("count_within_acceptance", True) and raw_items and len(raw_items) > count:
        raise ValueError("半成品/成品编号数量不能大于验收数量")
    serial_items = []
    generated = next_production_serials(cursor, kind, product_name, count, exclude_form_id=exclude_form_id)
    for index in range(count):
        raw = raw_items[index] if index < len(raw_items) and isinstance(raw_items[index], dict) else {}
        serial_no = validate_serial_no(raw.get("serial_no") or raw.get("code") or generated[index])
        qualified_raw = raw.get("qualified")
        if qualified_raw is None:
            status = str(raw.get("status") or "qualified").strip()
            qualified = status not in {"unqualified", "不合格", "ng", "NG", "false", "0"}
        else:
            qualified = bool(qualified_raw)
        abnormal = raw.get("abnormal_conditions") or raw.get("abnormal") or []
        if isinstance(abnormal, str):
            abnormal = [item.strip() for item in abnormal.replace("；", ",").replace("、", ",").split(",") if item.strip()]
        abnormal = [str(item).strip() for item in abnormal if str(item).strip()]
        if not qualified and not abnormal:
            abnormal = ["不合格"]
        serial_items.append(
            {
                "serial_no": serial_no,
                "qualified": qualified,
                "abnormal_conditions": abnormal,
                "remark": raw.get("remark") or "",
            }
        )
    if serial_rule.get("enabled", True) and serial_rule.get("unique_in_payload", True) and len({item["serial_no"] for item in serial_items}) != len(serial_items):
        raise ValueError("半成品/成品编号不能重复")
    ensure_production_serials_available(
        cursor,
        kind,
        [item["serial_no"] for item in serial_items],
        name=product_name,
        exclude_form_id=exclude_form_id,
    )
    return serial_items


OFFICE_PROJECT = "办公"
ACCEPTANCE_MATERIAL_LIMIT = "只能选择本项目、办公或辅料的已领用物料"


def claimed_lot_allowed(lot, purchase_project):
    """验收可选：本项目领用、办公领用、辅料领用。"""
    wanted = str(purchase_project or "").strip()
    project = str(lot.get("purchase_project") or "").strip() or OFFICE_PROJECT
    if not wanted or project == wanted:
        return True
    if project == OFFICE_PROJECT or str(lot.get("purpose") or "").strip() == OFFICE_PROJECT:
        return True
    return str(lot.get("rd_item_kind") or "").strip() == "辅料"


def _fill_missing_batch_projects(cursor, lots):
    """领用明细没带项目时，用批次上的采购项目。"""
    missing_ids = sorted({
        int(lot.get("batch_id") or 0)
        for lot in lots
        if int(lot.get("batch_id") or 0) and not str(lot.get("purchase_project") or "").strip()
    })
    if not missing_ids:
        return
    placeholders = ",".join("?" for _ in missing_ids)
    cursor.execute(
        f"SELECT id, purchase_project FROM material_batches WHERE id IN ({placeholders})",
        missing_ids,
    )
    projects = {int(row["id"]): str(row["purchase_project"] or "").strip() for row in cursor.fetchall()}
    for lot in lots:
        if str(lot.get("purchase_project") or "").strip():
            continue
        project = projects.get(int(lot.get("batch_id") or 0), "")
        if project:
            lot["purchase_project"] = project


def claimed_material_pool(cursor, purchase_project=None):
    batch_rows = claimed_material_batch_pool(cursor, purchase_project=purchase_project)
    grouped = {}
    for batch in batch_rows:
        material_id = int(batch["material_id"])
        project = str(batch.get("purchase_project") or "").strip() or "办公"
        target = grouped.setdefault(
            (material_id, project),
            {
                "material_id": material_id,
                "purchase_project": project,
                "material_code": batch.get("material_code") or "",
                "name": batch.get("name") or "",
                "brand_model": batch.get("brand_model") or "",
                "spec": batch.get("spec") or "",
                "unit": batch.get("unit") or "",
                "claim_applicant": batch.get("claim_applicant") or "",
                "claim_applicant_id": batch.get("claim_applicant_id") or "",
                "rd_item_kind": "",
                "claimed_quantity": 0,
                "used_quantity": 0,
                "available_quantity": 0,
                "claimed_amount": 0,
                "batches": [],
            },
        )
        target["claimed_quantity"] += float(batch.get("claimed_quantity") or 0)
        target["used_quantity"] += float(batch.get("used_quantity") or 0)
        target["available_quantity"] += float(batch.get("available_quantity") or 0)
        target["claimed_amount"] += float(batch.get("claimed_amount") or 0)
        if str(batch.get("rd_item_kind") or "").strip() == "辅料":
            target["rd_item_kind"] = "辅料"
        target["batches"].append(batch)
    pool = []
    for item in grouped.values():
        item["batches"].sort(key=_consumption_batch_sort_key)
        item["unit_cost"] = item["claimed_amount"] / item["claimed_quantity"] if item["claimed_quantity"] > 0 else 0
        if item["available_quantity"] > 1e-9:
            pool.append(item)
    return sorted(pool, key=lambda item: item["material_code"])


def claimed_material_batch_pool(cursor, purchase_project=None):
    wanted = str(purchase_project or "").strip()
    lots = [
        lot
        for lot in list_issued_lots(cursor)
        if int(lot.get("material_id") or 0) and int(lot.get("batch_id") or 0)
    ]
    _fill_missing_batch_projects(cursor, lots)
    lots = [
        lot
        for lot in lots
        if float(lot.get("available_quantity") or 0) > 1e-9 and claimed_lot_allowed(lot, wanted)
    ]
    batch_ids = sorted({int(lot["batch_id"]) for lot in lots})
    batch_info = {}
    if batch_ids:
        placeholders = ",".join("?" for _ in batch_ids)
        cursor.execute(
            f"SELECT id, batch_no, unit_price, received_date, stock_source FROM material_batches WHERE id IN ({placeholders})",
            batch_ids,
        )
        batch_info = {int(row["id"]): dict(row) for row in cursor.fetchall()}
    grouped = {}
    for lot in lots:
        material_id = int(lot["material_id"])
        batch_id = int(lot["batch_id"])
        info = batch_info.get(batch_id, {})
        key = (material_id, batch_id)
        remaining = float(lot.get("available_quantity") or 0)
        claimed_quantity = float(lot.get("issued_quantity") or 0)
        used_quantity = float(lot.get("consumed_quantity") or 0)
        unit_cost = float(info.get("unit_price") or lot.get("unit_price") or 0)
        target = grouped.setdefault(
            key,
            {
                "material_id": material_id,
                "batch_id": batch_id,
                "batch_no": info.get("batch_no") or lot.get("batch_no") or "",
                "received_date": info.get("received_date") or "",
                "material_code": lot.get("material_code") or "",
                "name": lot.get("material_name") or "",
                "brand_model": lot.get("brand_model") or "",
                "spec": lot.get("spec") or "",
                "unit": lot.get("unit") or "",
                "claim_applicant": lot.get("claim_applicant") or "",
                "claim_applicant_id": lot.get("applicant_id") or "",
                "purchase_project": str(lot.get("purchase_project") or "").strip() or OFFICE_PROJECT,
                "purpose": str(lot.get("purpose") or "").strip(),
                "rd_item_kind": str(lot.get("rd_item_kind") or "").strip(),
                "claimed_quantity": 0.0,
                "used_quantity": 0.0,
                "available_quantity": 0.0,
                "claimed_amount": 0.0,
                "unit_cost": unit_cost,
            },
        )
        if not target["claim_applicant"] and lot.get("claim_applicant"):
            target["claim_applicant"] = lot.get("claim_applicant") or ""
            target["claim_applicant_id"] = lot.get("applicant_id") or ""
        target["claimed_quantity"] += claimed_quantity
        target["used_quantity"] += used_quantity
        target["available_quantity"] += remaining
        target["claimed_amount"] += claimed_quantity * unit_cost
        if unit_cost and not target["unit_cost"]:
            target["unit_cost"] = unit_cost
    pool = [row for row in grouped.values() if float(row.get("available_quantity") or 0) > 1e-9]
    return sorted(pool, key=lambda item: (item.get("material_code") or "", item.get("batch_no") or ""))


def production_quality_from_payload(data):
    acceptance_quantity = quantity_value(data.get("acceptance_quantity"), "验收数量", positive=True)
    appearance_ok = quantity_value(data.get("appearance_ok_quantity"), "外观合格数量")
    function_ok = quantity_value(data.get("function_ok_quantity"), "功能合格数量")
    performance_ok = quantity_value(data.get("performance_ok_quantity"), "性能合格数量")
    for label, value in [
        ("外观合格数量", appearance_ok),
        ("功能合格数量", function_ok),
        ("性能合格数量", performance_ok),
    ]:
        if value < 0 or value > acceptance_quantity + 1e-9:
            raise ValueError(f"{label}必须在 0 到验收数量之间")
    qualified_quantity = min(appearance_ok, function_ok, performance_ok)
    unqualified_quantity = max(0, acceptance_quantity - qualified_quantity)
    return {
        "acceptance_quantity": acceptance_quantity,
        "appearance_ok_quantity": appearance_ok,
        "function_ok_quantity": function_ok,
        "performance_ok_quantity": performance_ok,
        "qualified_quantity": qualified_quantity,
        "unqualified_quantity": unqualified_quantity,
    }


def combine_component_quantities(components, id_key, qty_key="per_unit_quantity"):
    combined = {}
    for item in components or []:
        target_id = int(item.get(id_key) or 0)
        per_unit_quantity = quantity_value(item.get(qty_key) or item.get("quantity"), "单台用量")
        if target_id and per_unit_quantity > 0:
            combined[target_id] = combined.get(target_id, 0) + per_unit_quantity
    return combined


def _consumption_batch_sort_key(batch):
    return (str(batch.get("received_date") or ""), int(batch.get("batch_id") or 0))


def _parse_component_batch_ids(item):
    """解析勾选批次。显式 batch_ids/batch_id 不扩成该物料全部批次。"""
    if "batch_ids" in item:
        raw = item.get("batch_ids") or []
        if isinstance(raw, str):
            raw = parse_json(raw, [])
        ids = []
        for value in raw:
            batch_id = int(value or 0)
            if batch_id and batch_id not in ids:
                ids.append(batch_id)
        return ids, True
    batch_id = int(item.get("batch_id") or 0)
    if batch_id:
        return [batch_id], True
    return [], False


def _issued_batch_has_remainder(cursor, material_id, batch_id):
    for row in claimed_material_batch_pool(cursor):
        if int(row["material_id"]) == int(material_id) and int(row["batch_id"]) == int(batch_id):
            return True
    return False


def prepare_material_component_consumptions(cursor, components, acceptance_quantity, purchase_project=None):
    wanted = str(purchase_project or "").strip()
    batch_pool = claimed_material_batch_pool(cursor, purchase_project=wanted or None)
    pool = {(int(item["material_id"]), int(item["batch_id"])): item for item in batch_pool}
    grouped = {}
    for item in components or []:
        material_id = int(item.get("material_id") or 0)
        batch_ids, explicit = _parse_component_batch_ids(item)
        per_unit_quantity = quantity_value(item.get("per_unit_quantity") or item.get("quantity"), "单台所用物料数量")
        if not material_id and explicit and batch_ids:
            for row in batch_pool:
                if int(row["batch_id"]) == batch_ids[0]:
                    material_id = int(row["material_id"])
                    break
        if not material_id or per_unit_quantity <= 0:
            continue
        if explicit and not batch_ids:
            raise ValueError("请勾选消耗批次")
        target = grouped.setdefault(
            material_id,
            {"per_unit_quantity": 0, "batch_ids": [], "explicit": False},
        )
        target["per_unit_quantity"] += per_unit_quantity
        if explicit:
            target["explicit"] = True
            for batch_id in batch_ids:
                if batch_id not in target["batch_ids"]:
                    target["batch_ids"].append(batch_id)
    prepared = []
    for material_id, spec in grouped.items():
        material_batches = [row for row in batch_pool if int(row["material_id"]) == material_id]
        if spec["explicit"]:
            selected = []
            for batch_id in spec["batch_ids"]:
                row = pool.get((material_id, batch_id))
                if not row:
                    if wanted and _issued_batch_has_remainder(cursor, material_id, batch_id):
                        raise ValueError(ACCEPTANCE_MATERIAL_LIMIT)
                    raise ValueError("勾选的消耗批次没有已领用未消耗余量")
                selected.append(row)
        else:
            selected = material_batches
        if not selected:
            raise ValueError(ACCEPTANCE_MATERIAL_LIMIT if wanted else "所选物料没有已领用未消耗余量")
        total_quantity = spec["per_unit_quantity"] * acceptance_quantity
        available_quantity = sum(float(row.get("available_quantity") or 0) for row in selected)
        material_name = selected[0].get("name") or ""
        if total_quantity > available_quantity + 1e-9:
            raise ValueError(f"{material_name} 已领用未消耗数量不足")
        remaining = total_quantity
        for row in sorted(selected, key=_consumption_batch_sort_key):
            if remaining <= 1e-9:
                break
            take = min(remaining, float(row.get("available_quantity") or 0))
            if take <= 1e-9:
                continue
            unit_cost = float(row.get("unit_cost") or 0)
            prepared.append(
                {
                    "material_id": material_id,
                    "batch_id": int(row["batch_id"]),
                    "batch_no": row.get("batch_no") or "",
                    "material_code": row.get("material_code") or "",
                    "material_name": row.get("name") or "",
                    "brand_model": row.get("brand_model") or "",
                    "spec": row.get("spec") or "",
                    "unit": row.get("unit") or "",
                    "per_unit_quantity": take / acceptance_quantity if acceptance_quantity else 0,
                    "total_quantity": take,
                    "available_quantity": float(row.get("available_quantity") or 0),
                    "unit_cost": unit_cost,
                    "amount": take * unit_cost,
                    "selected_batch_ids": [int(item["batch_id"]) for item in selected],
                }
            )
            remaining -= take
        if remaining > 1e-9:
            raise ValueError(f"{material_name} 已领用未消耗数量不足")
    return prepared


def insert_material_consumptions(cursor, source_type, source_id, prepared):
    for item in prepared:
        cursor.execute(
            """
            INSERT INTO production_material_consumptions
                (source_type, source_id, material_id, batch_id, quantity, unit_cost, amount, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_type,
                source_id,
                item["material_id"],
                int(item.get("batch_id") or 0) or None,
                item["total_quantity"],
                item["unit_cost"],
                item["amount"],
                now_text(),
            ),
        )
    allocate_claim_consumptions(cursor, source_type, source_id, prepared)


def semifinished_unit_components(cursor, item_row):
    """按验收数量把整单消耗折成本台组成物料，并带上原领用人。"""
    raw = parse_json(item_row.get("components_json"), [])
    if not isinstance(raw, list):
        raise ValueError("组成物料格式不正确")
    if not raw:
        return []
    acceptance_id = int(item_row.get("acceptance_id") or 0)
    if not acceptance_id:
        raise ValueError("半成品缺少验收记录，无法列出组成物料")
    cursor.execute(
        "SELECT acceptance_quantity FROM semifinished_acceptances WHERE id = ?",
        (acceptance_id,),
    )
    acceptance = cursor.fetchone()
    if not acceptance:
        raise ValueError("半成品验收记录不存在，无法列出组成物料")
    acceptance_quantity = float(acceptance["acceptance_quantity"] or 0)
    if acceptance_quantity <= 1e-9:
        raise ValueError("半成品验收数量不正确")
    claimants = production_consumption_claimants(cursor, PRODUCTION_SOURCE_SEMI, acceptance_id)
    rows = []
    for component in raw:
        if not isinstance(component, dict):
            raise ValueError("组成物料格式不正确")
        material_id = int(component.get("material_id") or 0)
        batch_id = int(component.get("batch_id") or 0)
        if material_id <= 0 or batch_id <= 0:
            raise ValueError("组成物料批次不完整")
        has_total = component.get("total_quantity") is not None and component.get("total_quantity") != ""
        has_per = component.get("per_unit_quantity") is not None and component.get("per_unit_quantity") != ""
        if not has_total and not has_per:
            raise ValueError("组成物料缺少数量")
        total_qty = float(component.get("total_quantity") or 0) if has_total else None
        per_qty = float(component.get("per_unit_quantity") or 0) if has_per else None
        if total_qty is None:
            total_qty = per_qty * acceptance_quantity
        if per_qty is None:
            per_qty = total_qty / acceptance_quantity
        if abs(per_qty * acceptance_quantity - total_qty) > 1e-9:
            raise ValueError("组成物料数量与验收数量对不上")
        claimed = claimants.get((material_id, batch_id))
        if not claimed or abs(float(claimed["quantity"]) - total_qty) > 1e-9:
            raise ValueError("组成物料与已领用占用对不上")
        applicants = [name for name in claimed.get("applicants") or [] if name]
        if not applicants:
            raise ValueError("组成物料缺少领用人")
        rows.append(
            {
                "material_id": material_id,
                "material_code": component.get("material_code") or "",
                "material_name": component.get("material_name") or component.get("name") or "",
                "batch_id": batch_id,
                "batch_no": component.get("batch_no") or "",
                "unit": component.get("unit") or "",
                "per_unit_quantity": per_qty,
                "claim_applicant": " / ".join(applicants),
            }
        )
    return rows


def _component_original_totals(raw, acceptance_quantity):
    """把整单组成 JSON 折成物料+批次的原始总量。"""
    if not isinstance(raw, list):
        raise ValueError("组成物料格式不正确")
    totals = {}
    for component in raw:
        if not isinstance(component, dict):
            raise ValueError("组成物料格式不正确")
        material_id = int(component.get("material_id") or 0)
        batch_id = int(component.get("batch_id") or 0)
        if material_id <= 0 or batch_id <= 0:
            raise ValueError("组成物料批次不完整")
        has_total = component.get("total_quantity") is not None and component.get("total_quantity") != ""
        has_per = component.get("per_unit_quantity") is not None and component.get("per_unit_quantity") != ""
        if not has_total and not has_per:
            raise ValueError("组成物料缺少数量")
        total_qty = float(component.get("total_quantity") or 0) if has_total else None
        per_qty = float(component.get("per_unit_quantity") or 0) if has_per else None
        if total_qty is None:
            total_qty = per_qty * acceptance_quantity
        if per_qty is None:
            per_qty = total_qty / acceptance_quantity
        if abs(per_qty * acceptance_quantity - total_qty) > 1e-9:
            raise ValueError("组成物料数量与验收数量对不上")
        key = (material_id, batch_id)
        totals[key] = totals.get(key, 0.0) + total_qty
    return totals


def _group_consumption_rows(rows):
    grouped = {}
    for row in rows:
        key = (int(row.get("material_id") or 0), int(row.get("batch_id") or 0))
        grouped.setdefault(key, []).append(row)
    return grouped


def _consumption_qty(rows):
    return sum(float(row.get("quantity") or 0) for row in rows)


def _reduce_consumption_rows(cursor, table, rows, take_qty):
    remaining = float(take_qty or 0)
    if remaining <= 1e-9:
        return
    for row in sorted(rows, key=lambda item: int(item["id"]), reverse=True):
        if remaining <= 1e-9:
            break
        qty = float(row.get("quantity") or 0)
        if qty <= 1e-9:
            continue
        take = min(qty, remaining)
        new_qty = qty - take
        row_id = int(row["id"])
        if table == "production_material_consumptions":
            if new_qty <= 1e-9:
                cursor.execute("DELETE FROM production_material_consumptions WHERE id = ?", (row_id,))
            else:
                unit_cost = float(row.get("unit_cost") or 0)
                cursor.execute(
                    "UPDATE production_material_consumptions SET quantity = ?, amount = ? WHERE id = ?",
                    (new_qty, new_qty * unit_cost, row_id),
                )
        else:
            if new_qty <= 1e-9:
                cursor.execute("DELETE FROM claim_consumptions WHERE id = ?", (row_id,))
            else:
                cursor.execute(
                    "UPDATE claim_consumptions SET quantity = ? WHERE id = ?",
                    (new_qty, row_id),
                )
        remaining -= take
    if remaining > 1e-9:
        raise ValueError("占用数量不足，无法拆解")


def _semifinished_occupancy_rows(cursor, acceptance_id):
    cursor.execute(
        """
        SELECT id, material_id, batch_id, quantity, unit_cost, amount
        FROM production_material_consumptions
        WHERE source_type = ? AND source_id = ?
        ORDER BY id
        """,
        (PRODUCTION_SOURCE_SEMI, acceptance_id),
    )
    pmc_rows = [dict(row) for row in cursor.fetchall()]
    cursor.execute(
        """
        SELECT id, material_id, batch_id, quantity
        FROM claim_consumptions
        WHERE production_source_type = ? AND production_source_id = ?
        ORDER BY id
        """,
        (PRODUCTION_SOURCE_SEMI, acceptance_id),
    )
    cc_rows = [dict(row) for row in cursor.fetchall()]
    return pmc_rows, cc_rows


def semifinished_acceptance_has_occupancy(cursor, acceptance_id):
    pmc_rows, cc_rows = _semifinished_occupancy_rows(cursor, acceptance_id)
    return _consumption_qty(pmc_rows) > 1e-9 or _consumption_qty(cc_rows) > 1e-9


def ensure_semifinished_inventory_plain_delete(cursor, item):
    """有制作占用时禁止普通删除，避免半成品消失而物料仍记已消耗。"""
    if float(item.get("used_quantity") or 0) > 0:
        raise ValueError("semifinished inventory has been consumed by finished goods")
    if float(item.get("borrowed_quantity") or 0) > 0:
        raise ValueError("该半成品已借用，不能删除")
    acceptance_id = int(item.get("acceptance_id") or 0)
    if acceptance_id and semifinished_acceptance_has_occupancy(cursor, acceptance_id):
        raise ValueError("该半成品仍有制作占用，请拆解")


def delete_semifinished_inventory_record(cursor, inventory_id, user_id):
    """删除一条半成品库存。有制作占用时拒绝，调用方负责提交事务。"""
    cursor.execute("SELECT * FROM semifinished_inventory WHERE id = ?", (inventory_id,))
    row = cursor.fetchone()
    if not row:
        raise ValueError("semifinished inventory not found")
    item = dict(row)
    ensure_semifinished_inventory_plain_delete(cursor, item)
    _remove_semifinished_inventory_row(cursor, item, user_id)
    return {"id": int(item["id"]), "serial_no": item.get("serial_no") or "", "name": item.get("name") or ""}


def delete_finished_inventory_record(cursor, inventory_id, user_id):
    """删除一条成品库存。调用方负责提交事务。"""
    cursor.execute("SELECT * FROM finished_good_inventory WHERE id = ?", (inventory_id,))
    row = cursor.fetchone()
    if not row:
        raise ValueError("finished inventory not found")
    item = dict(row)
    recycle_table_row(
        cursor,
        "finished_inventory",
        "finished_good_inventory",
        inventory_id,
        ["serial_no", "product_name", "spec"],
        user_id,
    )
    cursor.execute("DELETE FROM finished_good_inventory WHERE id = ?", (inventory_id,))
    return {"id": int(item["id"]), "serial_no": item.get("serial_no") or "", "name": item.get("product_name") or ""}


def _remove_semifinished_inventory_row(cursor, item, user_id):
    inventory_id = int(item["id"])
    recycle_table_row(
        cursor,
        "semifinished_inventory",
        "semifinished_inventory",
        inventory_id,
        ["serial_no", "name", "spec"],
        user_id,
    )
    cursor.execute("DELETE FROM semifinished_inventory WHERE id = ?", (inventory_id,))


def disassemble_semifinished_inventory(cursor, inventory_id, user_id=None):
    """拆一台合格半成品：按验收份额回冲占用，物料回到已领用未消耗。"""
    cursor.execute("SELECT * FROM semifinished_inventory WHERE id = ?", (inventory_id,))
    item = cursor.fetchone()
    if not item:
        raise ValueError("半成品库存不存在")
    item = dict(item)
    if float(item.get("used_quantity") or 0) > 0:
        raise ValueError("该半成品已用于成品，不能拆解")
    if float(item.get("borrowed_quantity") or 0) > 0:
        raise ValueError("该半成品已借用，不能拆解")
    unit_qty = float(item.get("quantity") or 0)
    if unit_qty <= 1e-9:
        raise ValueError("半成品数量不正确")
    acceptance_id = int(item.get("acceptance_id") or 0)
    pmc_rows, cc_rows = ([], [])
    acceptance = None
    if acceptance_id:
        cursor.execute("SELECT * FROM semifinished_acceptances WHERE id = ?", (acceptance_id,))
        acceptance = cursor.fetchone()
        if not acceptance:
            raise ValueError("半成品验收记录不存在，无法拆解")
        acceptance = dict(acceptance)
        pmc_rows, cc_rows = _semifinished_occupancy_rows(cursor, acceptance_id)
    inventory_components = parse_json(item.get("components_json"), [])
    if not inventory_components and acceptance:
        inventory_components = parse_json(acceptance.get("components_json"), [])
    has_components = bool(inventory_components)
    has_occupancy = bool(pmc_rows or cc_rows)
    if not acceptance_id:
        if has_components:
            raise ValueError("半成品缺少验收记录，无法拆解")
        _remove_semifinished_inventory_row(cursor, item, user_id)
        return item
    acceptance_quantity = float(acceptance.get("acceptance_quantity") or 0)
    if not has_occupancy and not has_components:
        _remove_semifinished_inventory_row(cursor, item, user_id)
        return item
    if acceptance_quantity <= 1e-9:
        raise ValueError("半成品验收数量不正确")
    if unit_qty > acceptance_quantity + 1e-9:
        raise ValueError("半成品数量超出验收数量")
    originals = _component_original_totals(inventory_components, acceptance_quantity) if has_components else {}
    pmc_grouped = _group_consumption_rows(pmc_rows)
    cc_grouped = _group_consumption_rows(cc_rows)
    occupancy_keys = set(pmc_grouped) | set(cc_grouped)
    extra_occupancy = occupancy_keys - set(originals) if originals else set()
    if originals and extra_occupancy:
        raise ValueError("制作消耗与组成物料对不上")
    keys = set(originals) | occupancy_keys
    cursor.execute(
        """
        SELECT COALESCE(SUM(quantity), 0)
        FROM semifinished_inventory
        WHERE acceptance_id = ? AND id != ?
        """,
        (acceptance_id, inventory_id),
    )
    other_qty = float(cursor.fetchone()[0] or 0)
    cursor.execute(
        "SELECT COUNT(*) FROM defective_semifinished_goods WHERE semifinished_acceptance_id = ?",
        (acceptance_id,),
    )
    defective_count = int(cursor.fetchone()[0] or 0)
    remaining_heads = other_qty + unit_qty + defective_count
    clear_remainder = other_qty <= 1e-9 and defective_count == 0
    share = unit_qty / acceptance_quantity
    for key in keys:
        material_id, _batch_id = key
        if material_id <= 0:
            raise ValueError("组成物料批次不完整")
        pmc_key_rows = pmc_grouped.get(key, [])
        cc_key_rows = cc_grouped.get(key, [])
        pmc_qty = _consumption_qty(pmc_key_rows)
        cc_qty = _consumption_qty(cc_key_rows)
        if pmc_qty > 1e-9 and cc_qty > 1e-9 and abs(pmc_qty - cc_qty) > 1e-6:
            raise ValueError("制作消耗与已领用占用对不上")
        original = originals.get(key)
        if clear_remainder:
            pmc_take = pmc_qty
            cc_take = cc_qty
        elif original is not None:
            need = original * share
            if pmc_qty > 1e-9 and pmc_qty + 1e-9 < need:
                raise ValueError("组成物料与制作消耗对不上")
            if cc_qty > 1e-9 and cc_qty + 1e-9 < need:
                raise ValueError("组成物料与已领用占用对不上")
            pmc_take = min(need, pmc_qty) if pmc_qty > 1e-9 else 0.0
            cc_take = min(need, cc_qty) if cc_qty > 1e-9 else 0.0
        else:
            if remaining_heads <= 1e-9:
                raise ValueError("半成品数量不正确")
            pmc_take = pmc_qty * (unit_qty / remaining_heads) if pmc_qty > 1e-9 else 0.0
            cc_take = cc_qty * (unit_qty / remaining_heads) if cc_qty > 1e-9 else 0.0
        _reduce_consumption_rows(cursor, "production_material_consumptions", pmc_key_rows, pmc_take)
        _reduce_consumption_rows(cursor, "claim_consumptions", cc_key_rows, cc_take)
    _remove_semifinished_inventory_row(cursor, item, user_id)
    return item


def semifinished_pool(cursor, purchase_project=None):
    """成品可用半成品：已领用、未消耗，并且仍在合格库。"""
    wanted = str(purchase_project or "").strip()
    where = "COALESCE(si.claimed_quantity, 0) - COALESCE(si.used_quantity, 0) > 0"
    params = []
    if wanted:
        where += " AND TRIM(COALESCE(sa.project_code, '')) = ?"
        params.append(wanted)
    cursor.execute(
        f"""
        SELECT si.*,
               s.name AS shelf_name,
               TRIM(COALESCE(sa.project_code, '')) AS project_code,
               COALESCE(u.display_name, u.username, '') AS maker,
               MAX(0, COALESCE(si.claimed_quantity, 0) - COALESCE(si.used_quantity, 0)) AS remaining_quantity
        FROM semifinished_inventory si
        LEFT JOIN semifinished_acceptances sa ON sa.id = si.acceptance_id
        LEFT JOIN users u ON u.id = sa.applicant_id
        LEFT JOIN shelves s ON s.id = si.shelf_id
        WHERE {where}
        ORDER BY si.id DESC
        """,
        params,
    )
    rows = []
    for row in cursor.fetchall():
        item = dict(row)
        item["components"] = parse_json(item.pop("components_json", "[]"), [])
        rows.append(item)
    return rows


def prepare_semifinished_component_consumptions(cursor, components, acceptance_quantity, purchase_project=None):
    wanted = str(purchase_project or "").strip()
    pool = {int(item["id"]): item for item in semifinished_pool(cursor, purchase_project=wanted or None)}
    prepared = []
    for inventory_id, per_unit_quantity in combine_component_quantities(components, "semifinished_inventory_id").items():
        semifinished = pool.get(inventory_id)
        if not semifinished:
            raise ValueError("没有已领用未消耗的合格半成品" if wanted else "所选半成品没有已领用未消耗的合格数量")
        total_quantity = per_unit_quantity * acceptance_quantity
        if total_quantity > float(semifinished.get("remaining_quantity") or 0) + 1e-9:
            raise ValueError(f"{semifinished['name']} 已领用未消耗数量不足")
        unit_cost = float(semifinished.get("cost_price") or 0)
        prepared.append(
            {
                "semifinished_inventory_id": inventory_id,
                "name": semifinished.get("name") or "",
                "spec": semifinished.get("spec") or "",
                "unit": semifinished.get("unit") or "",
                "maker": semifinished.get("maker") or "",
                "per_unit_quantity": per_unit_quantity,
                "total_quantity": total_quantity,
                "remaining_quantity": float(semifinished.get("remaining_quantity") or 0),
                "unit_cost": unit_cost,
                "amount": total_quantity * unit_cost,
            }
        )
    return prepared


def insert_semifinished_consumptions(cursor, finished_acceptance_id, prepared):
    for item in prepared:
        cursor.execute(
            """
            UPDATE semifinished_inventory
            SET used_quantity = used_quantity + ?, updated_at = ?
            WHERE id = ?
              AND COALESCE(claimed_quantity, 0) - COALESCE(used_quantity, 0) >= ?
            """,
            (item["total_quantity"], now_text(), item["semifinished_inventory_id"], item["total_quantity"]),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"{item['name']} 已领用未消耗数量已变化，请刷新后重试")
        cursor.execute(
            """
            INSERT INTO semifinished_consumptions
                (finished_acceptance_id, semifinished_inventory_id, quantity, unit_cost, amount, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                finished_acceptance_id,
                item["semifinished_inventory_id"],
                item["total_quantity"],
                item["unit_cost"],
                item["amount"],
                now_text(),
            ),
        )


def next_defect_serials(cursor, product_name, count):
    product_name = str(product_name or "").strip()
    prefix = f"CP{product_name}"
    cursor.execute(
        """
        SELECT serial_no
        FROM defective_finished_goods
        WHERE product_name = ? AND serial_no LIKE ?
        """,
        (product_name, f"{prefix}%"),
    )
    max_serial = 0
    for row in cursor.fetchall():
        suffix = str(row["serial_no"] or "")[-3:]
        if suffix.isdigit():
            max_serial = max(max_serial, int(suffix))
    return [f"{prefix}{serial:03d}" for serial in range(max_serial + 1, max_serial + int(count) + 1)]


def require_production_kind(kind):
    if kind not in PRODUCTION_FORM_TYPES:
        raise ValueError("生产验收类型错误")
    return kind


def production_components_from_payload(cursor, kind, data, acceptance_quantity):
    material_components = data.get("material_components")
    if material_components is None:
        material_components = data.get("components") or []
    semifinished_components = data.get("semifinished_components") or []
    project = str(data.get("project_code") or "").strip()
    if not project:
        raise ValueError("请先选择项目代号")
    material_prepared = prepare_material_component_consumptions(
        cursor, material_components, acceptance_quantity, purchase_project=project
    )
    semifinished_prepared = []
    if kind == "finished":
        semifinished_prepared = prepare_semifinished_component_consumptions(
            cursor, semifinished_components, acceptance_quantity, purchase_project=project
        )
        if not material_prepared and not semifinished_prepared:
            raise ValueError("请至少选择所用物料或所用半成品")
    elif not material_prepared:
        raise ValueError("请至少选择一种单台所用物料")
    total_cost = sum(float(item.get("amount") or 0) for item in material_prepared + semifinished_prepared)
    return material_components or [], semifinished_components, material_prepared, semifinished_prepared, total_cost


def production_item_payload(kind, data):
    name = str(data.get("name") or data.get("product_name") or "").strip()
    if not name:
        raise ValueError("请填写半成品名称" if kind == "semifinished" else "请填写成品名称")
    acceptance_quantity = quantity_value(data.get("acceptance_quantity"), "验收数量", positive=True)
    return {
        "name": name,
        "spec": data.get("spec") or "",
        "unit": data.get("unit") or "台",
        "acceptance_quantity": acceptance_quantity,
        "acceptance_date": data.get("acceptance_date") or today_text(),
        "maker": data.get("maker") or "",
        "project_code": validate_project_code(data.get("project_code")),
    }


def validate_finished_defects(defects, unqualified_quantity):
    unqualified_count = int(round(float(unqualified_quantity or 0)))
    if abs(unqualified_count - float(unqualified_quantity or 0)) > 1e-6:
        raise ValueError("成品不合格数量必须是整数，才能逐个记录流水号")
    defects = defects or []
    if len(defects) != unqualified_count:
        raise ValueError("不合格成品明细数量必须等于不合格数量")
    normalized = []
    for index, defect in enumerate(defects, start=1):
        abnormal = defect.get("abnormal_conditions") or defect.get("abnormal") or []
        if isinstance(abnormal, str):
            abnormal = [abnormal]
        abnormal = [str(item).strip() for item in abnormal if str(item).strip()]
        if not abnormal:
            raise ValueError(f"第 {index} 个不合格品请至少选择一个异常情况")
        normalized.append({"abnormal_conditions": abnormal})
    return normalized


def production_maker(cursor, user, maker_value, optional_active_user_id):
    """Resolve the active maker recorded on a production acceptance."""
    maker_id = optional_active_user_id(cursor, maker_value, "制作者")
    if not maker_id:
        raise ValueError("请选择制作人")
    cursor.execute("SELECT display_name, username FROM users WHERE id = ?", (maker_id,))
    maker = cursor.fetchone()
    if not maker:
        raise ValueError("制作者不存在或已停用")
    return maker_id, maker["display_name"] or maker["username"] or ""


def execute_create_production(cursor, user, kind, data, optional_active_user_id):
    """Create a production workflow from a submit payload without committing."""
    kind = require_production_kind(kind)
    payload = production_item_payload(kind, data)
    acceptance_quantity = payload["acceptance_quantity"]
    maker_id, maker_name = production_maker(cursor, user, data.get("maker_id"), optional_active_user_id)
    material_components, semifinished_components, material_prepared, semifinished_prepared, total_cost = production_components_from_payload(cursor, kind, data, acceptance_quantity)
    # 半成品 BY，成品 CP。常用物料申请单独用 CY。
    prefix = "BY" if kind == "semifinished" else "CP"
    form_no = next_form_no(cursor, prefix)
    validator_ids = [int(value) for value in data.get("validator_ids") or [] if int(value)]
    if not validator_ids:
        validator_ids = [user["id"]]
    validator_ids = validate_validator_users(cursor, validator_ids, kind)
    validator_ids = workflow_assignees(cursor, kind, "acceptance", validator_ids)
    unit_cost = total_cost / acceptance_quantity if acceptance_quantity > 0 else 0
    title = workflow_generated_title(user, form_no)
    form_data = {
        "validator_ids": validator_ids,
        "production_type": kind,
        "acceptance_date": payload["acceptance_date"],
        "maker_id": maker_id,
        "maker": maker_name,
        "project_code": payload["project_code"],
    }
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, leader_id,
             total_amount, data_json, created_at, updated_at)
        VALUES (?, ?, ?, 'acceptance', 'acceptance', ?, ?, ?, ?, ?, ?)
        """,
        (
            form_no,
            kind,
            title,
            user["id"],
            None,
            total_cost,
            json.dumps(form_data, ensure_ascii=False),
            now_text(),
            now_text(),
        ),
    )
    form_id = cursor.lastrowid
    item_data = {
        "production_type": kind,
        "acceptance_date": payload["acceptance_date"],
        "maker_id": maker_id,
        "maker": maker_name,
        "project_code": payload["project_code"],
        "material_components": material_components,
        "semifinished_components": semifinished_components,
        "estimated_material_components": material_prepared,
        "estimated_semifinished_components": semifinished_prepared,
    }
    cursor.execute(
        """
        INSERT INTO workflow_items
            (form_id, material_id, material_code, material_name, brand_model, spec, unit,
             request_quantity, arrival_quantity, unit_price, data_json)
        VALUES (?, NULL, ?, ?, '', ?, ?, ?, ?, ?, ?)
        """,
        (
            form_id,
            form_no,
            payload["name"],
            payload["spec"],
            payload["unit"],
            acceptance_quantity,
            acceptance_quantity,
            unit_cost,
            json.dumps(item_data, ensure_ascii=False),
        ),
    )
    create_workflow_tasks(cursor, form_id, kind, "acceptance", validator_ids)
    bind_production_material_hold(cursor, form_id)
    return form_id


def bind_production_material_hold(cursor, form_id):
    """提交后的半成品/成品验收占住已领用未消耗数量。驳回、作废、完成后释放。"""
    form_id = int(form_id or 0)
    cursor.execute("SELECT id, form_type, status FROM workflow_forms WHERE id = ?", (form_id,))
    form = cursor.fetchone()
    if not form or form["form_type"] not in PRODUCTION_FORM_TYPES:
        return
    release_production_material_hold(cursor, form_id)
    if form["status"] in {"rejected", "cancelled", "completed", "draft"}:
        return
    cursor.execute(
        "SELECT arrival_quantity, data_json FROM workflow_items WHERE form_id = ? ORDER BY id LIMIT 1",
        (form_id,),
    )
    item = cursor.fetchone()
    if not item:
        return
    acceptance_quantity = float(item["arrival_quantity"] or 0)
    if acceptance_quantity <= 1e-9:
        return
    item_data = parse_json(item["data_json"], {})
    if item_data.get("production_acceptance_id"):
        return
    _, _, material_prepared, _, _ = production_components_from_payload(
        cursor,
        form["form_type"],
        {
            "project_code": item_data.get("project_code") or "",
            "material_components": item_data.get("material_components") or item_data.get("components") or [],
            "semifinished_components": item_data.get("semifinished_components") or [],
        },
        acceptance_quantity,
    )
    hold_type = PRODUCTION_HOLD_SEMI if form["form_type"] == "semifinished" else PRODUCTION_HOLD_FINISHED
    allocate_claim_consumptions(cursor, hold_type, form_id, material_prepared)


def revoke_completed_semifinished(cursor, form_id):
    """撤销已办结的半成品验收：加回本单待消耗，删掉本单半成品，流程退回待入库。"""
    form_id = int(form_id or 0)
    cursor.execute("SELECT * FROM workflow_forms WHERE id = ?", (form_id,))
    form = cursor.fetchone()
    if not form or form["form_type"] != "semifinished":
        raise ValueError("只能撤销半成品验收")
    form = dict(form)
    if form.get("status") != "completed":
        raise ValueError("只有已办结的半成品验收可以撤销")
    cursor.execute(
        "SELECT * FROM semifinished_acceptances WHERE acceptance_no = ?",
        (form.get("form_no"),),
    )
    acceptance = cursor.fetchone()
    if not acceptance:
        raise ValueError("找不到该流程的半成品验收记录")
    acceptance = dict(acceptance)
    acceptance_id = int(acceptance["id"])
    cursor.execute("SELECT * FROM semifinished_inventory WHERE acceptance_id = ?", (acceptance_id,))
    inventories = [dict(row) for row in cursor.fetchall()]
    for item in inventories:
        if float(item.get("used_quantity") or 0) > 1e-9:
            raise ValueError("该半成品已用于成品，不能撤销")
        if float(item.get("borrowed_quantity") or 0) > 1e-9:
            raise ValueError("该半成品已借用，不能撤销")
    if inventories:
        placeholders = ",".join("?" for _ in inventories)
        inventory_ids = [int(item["id"]) for item in inventories]
        cursor.execute(
            f"SELECT COUNT(*) FROM semifinished_consumptions WHERE semifinished_inventory_id IN ({placeholders})",
            inventory_ids,
        )
        if int(cursor.fetchone()[0] or 0) > 0:
            raise ValueError("该半成品已用于成品，不能撤销")
    cursor.execute(
        "SELECT COUNT(*) FROM scrapped_semifinished_goods WHERE acceptance_id = ?",
        (acceptance_id,),
    )
    if int(cursor.fetchone()[0] or 0) > 0:
        raise ValueError("该半成品已报废，不能撤销")
    cursor.execute(
        "SELECT * FROM defective_semifinished_goods WHERE semifinished_acceptance_id = ?",
        (acceptance_id,),
    )
    for row in cursor.fetchall():
        if float(dict(row).get("borrowed_quantity") or 0) > 1e-9:
            raise ValueError("不合格半成品已借用，不能撤销")

    cursor.execute(
        "DELETE FROM claim_consumptions WHERE production_source_type = ? AND production_source_id = ?",
        (PRODUCTION_SOURCE_SEMI, acceptance_id),
    )
    cursor.execute(
        "DELETE FROM production_material_consumptions WHERE source_type = ? AND source_id = ?",
        (PRODUCTION_SOURCE_SEMI, acceptance_id),
    )
    cursor.execute("DELETE FROM semifinished_inventory WHERE acceptance_id = ?", (acceptance_id,))
    cursor.execute(
        "DELETE FROM defective_semifinished_goods WHERE semifinished_acceptance_id = ?",
        (acceptance_id,),
    )
    cursor.execute("DELETE FROM semifinished_acceptances WHERE id = ?", (acceptance_id,))

    cursor.execute("SELECT id, data_json FROM workflow_items WHERE form_id = ?", (form_id,))
    for item in cursor.fetchall():
        item_data = parse_json(item["data_json"], {})
        item_data.pop("production_acceptance_id", None)
        cursor.execute(
            "UPDATE workflow_items SET data_json = ? WHERE id = ?",
            (json.dumps(item_data, ensure_ascii=False), item["id"]),
        )
    cursor.execute(
        "UPDATE workflow_forms SET status = 'inbound', current_step = 'inbound', updated_at = ? WHERE id = ?",
        (now_text(), form_id),
    )
    cursor.execute(
        """
        SELECT assignee_id FROM workflow_tasks
        WHERE form_id = ? AND step_code = 'inbound'
        ORDER BY id DESC LIMIT 1
        """,
        (form_id,),
    )
    task = cursor.fetchone()
    assignee_id = int(task["assignee_id"] or 0) if task else 0
    if not assignee_id:
        assignee_id = int(form.get("warehouse_user_id") or form.get("applicant_id") or 0)
    if not assignee_id:
        raise ValueError("找不到入库办理人，不能撤销")
    create_workflow_tasks(cursor, form_id, "semifinished", "inbound", [assignee_id])
    bind_production_material_hold(cursor, form_id)
