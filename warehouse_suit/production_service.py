# -*- coding: utf-8 -*-
"""Production acceptance, serial, component, and consumption services."""

from warehouse_suit.db import now_text, today_text
from warehouse_suit.inventory_constants import STOCK_SOURCE_FORMAL
from warehouse_suit.settings import parse_json
from warehouse_suit.validation import data_validation_rule, quantity_value, validate_serial_no
from warehouse_suit.workflow_service import PRODUCTION_FORM_TYPES


def next_production_serials(cursor, kind, name, count):
    name = str(name or ("半成品" if kind == "semifinished" else "成品")).strip()
    prefix = ("BP" if kind == "semifinished" else "CP") + name
    max_serial = 0
    lookup = [("semifinished_inventory", "name"), ("finished_good_inventory", "product_name")]
    if kind == "finished":
        lookup.append(("defective_finished_goods", "product_name"))
    else:
        lookup.append(("defective_semifinished_goods", "name"))
    for table, column in lookup:
        cursor.execute(f"SELECT serial_no FROM {table} WHERE {column} = ? AND serial_no LIKE ?", (name, f"{prefix}%"))
        for row in cursor.fetchall():
            suffix = str(row["serial_no"] or "")[-3:]
            if suffix.isdigit():
                max_serial = max(max_serial, int(suffix))
    return [f"{prefix}{serial:03d}" for serial in range(max_serial + 1, max_serial + int(count) + 1)]


def ensure_production_serials_available(cursor, kind, serial_numbers):
    rule = data_validation_rule("serial_no")
    if not rule.get("enabled", True) or not rule.get("unique_in_database", True):
        return
    conflict_tables = (
        [("semifinished_inventory", "半成品"), ("defective_semifinished_goods", "不合格半成品")]
        if kind == "semifinished"
        else [("finished_good_inventory", "成品"), ("defective_finished_goods", "不合格成品")]
    )
    for serial_no in [str(value or "").strip() for value in serial_numbers if str(value or "").strip()]:
        for table, label in conflict_tables:
            cursor.execute(f"SELECT id FROM {table} WHERE serial_no = ? LIMIT 1", (serial_no,))
            if cursor.fetchone():
                raise ValueError(f"{label}编号 {serial_no} 已存在")


def normalize_production_serial_items(cursor, kind, product_name, acceptance_quantity, incoming):
    count = int(round(float(acceptance_quantity or 0)))
    if abs(count - float(acceptance_quantity or 0)) > 1e-6:
        raise ValueError("半成品/成品验收数量必须是整数，才能逐个编号验收")
    raw_items = incoming.get("serial_items") or incoming.get("serials") or []
    serial_rule = data_validation_rule("serial_no")
    if serial_rule.get("enabled", True) and serial_rule.get("count_within_acceptance", True) and raw_items and len(raw_items) > count:
        raise ValueError("半成品/成品编号数量不能大于验收数量")
    serial_items = []
    generated = next_production_serials(cursor, kind, product_name, count)
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
    ensure_production_serials_available(cursor, kind, [item["serial_no"] for item in serial_items])
    return serial_items


def claimed_material_pool(cursor):
    batch_rows = claimed_material_batch_pool(cursor)
    grouped = {}
    for batch in batch_rows:
        material_id = int(batch["material_id"])
        target = grouped.setdefault(
            material_id,
            {
                "material_id": material_id,
                "material_code": batch.get("material_code") or "",
                "name": batch.get("name") or "",
                "brand_model": batch.get("brand_model") or "",
                "spec": batch.get("spec") or "",
                "unit": batch.get("unit") or "",
                "claim_applicant": batch.get("claim_applicant") or "",
                "claim_applicant_id": batch.get("claim_applicant_id") or "",
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
        target["batches"].append(batch)
    pool = []
    for item in grouped.values():
        item["unit_cost"] = item["claimed_amount"] / item["claimed_quantity"] if item["claimed_quantity"] > 0 else 0
        if item["available_quantity"] > 1e-9:
            pool.append(item)
    return sorted(pool, key=lambda item: item["material_code"])


def claimed_material_batch_pool(cursor):
    cursor.execute(
        """
        SELECT wi.material_id, wi.data_json, m.material_code, m.name, m.brand_model, m.spec, m.unit,
               COALESCE(u.display_name, u.username, '') AS claim_applicant,
               COALESCE(u.username, '') AS claim_applicant_username
        FROM workflow_items wi
        JOIN workflow_forms f ON f.id = wi.form_id
        JOIN materials m ON m.id = wi.material_id
        LEFT JOIN users u ON u.id = f.applicant_id
        WHERE f.form_type = 'claim'
          AND f.status = 'completed'
          AND COALESCE(wi.outbound_quantity, 0) > 0
        ORDER BY m.material_code
        """
    )
    claimed = {}
    for row in cursor.fetchall():
        item = dict(row)
        data = parse_json(item.pop("data_json", "{}"), {})
        item["claim_applicant"] = data.get("claim_applicant_name") or item.get("claim_applicant") or ""
        item["claim_applicant_id"] = data.get("claim_applicant_id") or ""
        for batch in data.get("consumed_batches") or []:
            batch_id = int(batch.get("batch_id") or batch.get("id") or 0)
            quantity = float(batch.get("quantity") or 0)
            if not batch_id or quantity <= 0:
                continue
            key = (int(item["material_id"]), batch_id)
            target = claimed.setdefault(
                key,
                {
                    **item,
                    "batch_id": batch_id,
                    "batch_no": batch.get("batch_no") or "",
                    "claimed_quantity": 0,
                },
            )
            target["claimed_quantity"] += quantity
    if not claimed:
        return []
    batch_ids = sorted({batch_id for _, batch_id in claimed})
    placeholders = ",".join("?" for _ in batch_ids)
    cursor.execute(
        f"SELECT id, batch_no, unit_price, received_date FROM material_batches WHERE stock_source = ? AND id IN ({placeholders})",
        [STOCK_SOURCE_FORMAL, *batch_ids],
    )
    batch_info = {int(row["id"]): dict(row) for row in cursor.fetchall()}
    cursor.execute(
        """
        SELECT material_id, batch_id, SUM(quantity) AS used_quantity
        FROM production_material_consumptions
        WHERE batch_id IS NOT NULL
        GROUP BY material_id, batch_id
        """
    )
    used_map = {
        (int(row["material_id"]), int(row["batch_id"])): float(row["used_quantity"] or 0)
        for row in cursor.fetchall()
    }
    pool = []
    for key, row in claimed.items():
        material_id, batch_id = key
        info = batch_info.get(batch_id, {})
        claimed_quantity = float(row["claimed_quantity"] or 0)
        used_quantity = used_map.get(key, 0)
        available_quantity = max(0, claimed_quantity - used_quantity)
        unit_cost = float(info.get("unit_price") or 0)
        claimed_amount = claimed_quantity * unit_cost
        row.update(
            {
                "batch_no": info.get("batch_no") or row.get("batch_no") or "",
                "received_date": info.get("received_date") or "",
                "claimed_quantity": claimed_quantity,
                "used_quantity": used_quantity,
                "available_quantity": available_quantity,
                "claimed_amount": claimed_amount,
                "unit_cost": unit_cost,
            }
        )
        if available_quantity > 1e-9:
            pool.append(row)
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


def prepare_material_component_consumptions(cursor, components, acceptance_quantity):
    pool = {(int(item["material_id"]), int(item["batch_id"])): item for item in claimed_material_batch_pool(cursor)}
    legacy_pool = {int(item["material_id"]): item for item in claimed_material_pool(cursor)}
    combined = {}
    for item in components or []:
        material_id = int(item.get("material_id") or 0)
        batch_id = int(item.get("batch_id") or 0)
        per_unit_quantity = quantity_value(item.get("per_unit_quantity") or item.get("quantity"), "单台所用物料数量")
        if not material_id and batch_id:
            for row in pool.values():
                if int(row["batch_id"]) == batch_id:
                    material_id = int(row["material_id"])
                    break
        if material_id and per_unit_quantity > 0:
            key = (material_id, batch_id)
            combined[key] = combined.get(key, 0) + per_unit_quantity
    prepared = []
    for (material_id, batch_id), per_unit_quantity in combined.items():
        material = pool.get((material_id, batch_id)) if batch_id else legacy_pool.get(material_id)
        if not material:
            raise ValueError("所选物料没有已领用未消耗余量")
        total_quantity = per_unit_quantity * acceptance_quantity
        if total_quantity > float(material.get("available_quantity") or 0) + 1e-9:
            raise ValueError(f"{material['name']} 已领用未消耗数量不足")
        unit_cost = float(material.get("unit_cost") or 0)
        prepared.append(
            {
                "material_id": material_id,
                "batch_id": batch_id or material.get("batch_id"),
                "batch_no": material.get("batch_no") or "",
                "material_code": material.get("material_code") or "",
                "material_name": material.get("name") or "",
                "brand_model": material.get("brand_model") or "",
                "spec": material.get("spec") or "",
                "unit": material.get("unit") or "",
                "per_unit_quantity": per_unit_quantity,
                "total_quantity": total_quantity,
                "available_quantity": float(material.get("available_quantity") or 0),
                "unit_cost": unit_cost,
                "amount": total_quantity * unit_cost,
            }
        )
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


def semifinished_pool(cursor):
    cursor.execute(
        """
        SELECT si.*,
               s.name AS shelf_name,
               COALESCE(u.display_name, u.username, '') AS maker,
               MAX(0, COALESCE(si.quantity, 0) - COALESCE(si.used_quantity, 0) - COALESCE(si.borrowed_quantity, 0)) AS remaining_quantity
        FROM semifinished_inventory si
        LEFT JOIN semifinished_acceptances sa ON sa.id = si.acceptance_id
        LEFT JOIN users u ON u.id = sa.applicant_id
        LEFT JOIN shelves s ON s.id = si.shelf_id
        WHERE COALESCE(si.quantity, 0) - COALESCE(si.used_quantity, 0) - COALESCE(si.borrowed_quantity, 0) > 0
        ORDER BY si.id DESC
        """
    )
    rows = []
    for row in cursor.fetchall():
        item = dict(row)
        item["components"] = parse_json(item.pop("components_json", "[]"), [])
        rows.append(item)
    return rows


def prepare_semifinished_component_consumptions(cursor, components, acceptance_quantity):
    pool = {int(item["id"]): item for item in semifinished_pool(cursor)}
    prepared = []
    for inventory_id, per_unit_quantity in combine_component_quantities(components, "semifinished_inventory_id").items():
        semifinished = pool.get(inventory_id)
        if not semifinished:
            raise ValueError("所选半成品没有可用于成品的余量")
        total_quantity = per_unit_quantity * acceptance_quantity
        if total_quantity > float(semifinished.get("remaining_quantity") or 0) + 1e-9:
            raise ValueError(f"{semifinished['name']} 半成品余量不足")
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
        cursor.execute(
            """
            UPDATE semifinished_inventory
            SET used_quantity = used_quantity + ?, updated_at = ?
            WHERE id = ?
            """,
            (item["total_quantity"], now_text(), item["semifinished_inventory_id"]),
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
    material_prepared = prepare_material_component_consumptions(cursor, material_components, acceptance_quantity)
    semifinished_prepared = []
    if kind == "finished":
        semifinished_prepared = prepare_semifinished_component_consumptions(cursor, semifinished_components, acceptance_quantity)
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
