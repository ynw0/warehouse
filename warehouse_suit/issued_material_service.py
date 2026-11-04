"""已领用物料台账：申领出库与借用转领用的余量、制作占用。"""

from __future__ import annotations

import json

from warehouse_suit.db import now_text
from warehouse_suit.inventory_constants import STOCK_SOURCE_FORMAL
from warehouse_suit.settings import parse_json, temporary_inventory_enabled
from warehouse_suit.usage_kind import pack_quantity_value
from warehouse_suit.validation import quantity_value
from warehouse_suit.temporary_inventory_visibility import append_workflow_visibility


SOURCE_CLAIM_OUTBOUND = "claim_outbound"
SOURCE_CLAIM_ITEM = "claim_item"
SOURCE_BORROW_CLAIM = "borrow_claim"


def _claim_kind_fields(form_data):
    """申领单上的用途和研发类型，辅料只记在这里。"""
    data = form_data if isinstance(form_data, dict) else parse_json(form_data, {})
    return {
        "purpose": str(data.get("purpose") or "").strip(),
        "rd_item_kind": str(data.get("rd_item_kind") or "").strip(),
    }

CONSUME_STATUS_SEMI = "已消耗-半成品制作"
CONSUME_STATUS_FINISHED = "已消耗-成品制作"

PRODUCTION_SOURCE_SEMI = "semifinished"
PRODUCTION_SOURCE_FINISHED = "finished"
# 验收单已提交、尚未入库时的占用。不能和真正消耗共用类型，否则流程号会和验收记录号撞在一起。
PRODUCTION_HOLD_SEMI = "semifinished_hold"
PRODUCTION_HOLD_FINISHED = "finished_hold"


def _table_exists(cursor, name):
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    )
    return bool(cursor.fetchone())


def _columns(cursor, table):
    cursor.execute(f'PRAGMA table_info("{table}")')
    return {row[1] for row in cursor.fetchall()}


def take_batch_slice(batches, skip_qty, take_qty):
    """从出库批次中跳过已归还/已转领用，切出本次数量。"""
    skip_qty = float(skip_qty or 0)
    take_qty = float(take_qty or 0)
    if take_qty <= 1e-9:
        return []
    source = [item for item in (batches or []) if isinstance(item, dict)]
    if not source:
        return []
    result = []
    skipped = 0.0
    taken = 0.0
    for batch in source:
        qty = float(batch.get("quantity") or 0)
        if qty <= 1e-9:
            continue
        if skipped + qty <= skip_qty + 1e-9:
            skipped += qty
            continue
        if skipped < skip_qty:
            qty -= skip_qty - skipped
            skipped = skip_qty
        if taken + qty > take_qty + 1e-9:
            qty = take_qty - taken
        if qty > 1e-9:
            result.append(
                {
                    "batch_id": int(batch.get("batch_id") or batch.get("id") or 0) or None,
                    "batch_no": batch.get("batch_no") or "",
                    "quantity": qty,
                    "unit_price": float(batch.get("unit_price") or 0),
                    "stock_record_id": int(batch.get("stock_record_id") or 0) or None,
                }
            )
            taken += qty
        if taken >= take_qty - 1e-9:
            break
    if taken + 1e-9 < take_qty:
        raise ValueError("借用出库批次不足以分配本次转领用数量")
    return result


def consume_status_text(semi_qty, finished_qty):
    labels = []
    if float(semi_qty or 0) > 1e-9:
        labels.append(CONSUME_STATUS_SEMI)
    if float(finished_qty or 0) > 1e-9:
        labels.append(CONSUME_STATUS_FINISHED)
    return " / ".join(labels)


def _consumption_map(cursor):
    mapping = {}
    if not _table_exists(cursor, "claim_consumptions"):
        return mapping
    cursor.execute(
        """
        SELECT c.source_kind, c.source_id, c.batch_id, c.production_source_type, SUM(c.quantity) AS quantity
        FROM claim_consumptions c
        LEFT JOIN workflow_forms f
          ON f.id = c.production_source_id
         AND c.production_source_type IN ('semifinished_hold', 'finished_hold')
        WHERE NOT (
            c.production_source_type IN ('semifinished_hold', 'finished_hold')
            AND COALESCE(f.status, '') = 'draft'
        )
        GROUP BY c.source_kind, c.source_id, c.batch_id, c.production_source_type
        """
    )
    for row in cursor.fetchall():
        key = (
            str(row["source_kind"] or ""),
            int(row["source_id"] or 0),
            int(row["batch_id"] or 0),
        )
        target = mapping.setdefault(key, {"semifinished": 0.0, "finished": 0.0})
        kind = str(row["production_source_type"] or "")
        if kind in {PRODUCTION_SOURCE_SEMI, PRODUCTION_HOLD_SEMI}:
            target["semifinished"] += float(row["quantity"] or 0)
        elif kind in {PRODUCTION_SOURCE_FINISHED, PRODUCTION_HOLD_FINISHED}:
            target["finished"] += float(row["quantity"] or 0)
    return mapping


def _apply_consumption(lot, mapping):
    key = (lot["source_kind"], int(lot["source_id"] or 0), int(lot.get("batch_id") or 0))
    used = mapping.get(key, {"semifinished": 0.0, "finished": 0.0})
    semi = float(used.get("semifinished") or 0)
    finished = float(used.get("finished") or 0)
    consumed = semi + finished
    issued = float(lot.get("issued_quantity") or 0)
    returned = float(lot.get("returned_quantity") or 0)
    remaining = max(0.0, issued - returned - consumed)
    lot["consumed_quantity"] = consumed
    lot["consumed_semifinished_quantity"] = semi
    lot["consumed_finished_quantity"] = finished
    lot["consume_status"] = consume_status_text(semi, finished)
    lot["returnable_quantity"] = remaining if lot["source_kind"] in {SOURCE_CLAIM_OUTBOUND, SOURCE_BORROW_CLAIM} else 0.0
    lot["available_quantity"] = remaining
    return lot


def _outbound_batch_slices(item):
    """出库流水缺批次时，用领用明细上的出库批次补全，避免制作消耗对不上。"""
    issued = float(item.get("issued_quantity") or 0)
    stock_batch_id = int(item.get("batch_id") or 0) or None
    if stock_batch_id:
        return [
            {
                "batch_id": stock_batch_id,
                "batch_no": item.get("batch_no") or "",
                "quantity": issued,
                "unit_price": float(item.get("unit_price") or 0),
            }
        ]
    data = parse_json(item.get("item_data_json"), {})
    consumed = [batch for batch in (data.get("consumed_batches") or []) if isinstance(batch, dict)]
    outbound_id = int(item.get("outbound_record_id") or 0)
    related = [
        batch
        for batch in consumed
        if int(batch.get("stock_record_id") or 0) == outbound_id
    ]
    if not related:
        related = consumed
    slices = []
    remaining = issued
    for batch in related:
        if remaining <= 1e-9:
            break
        quantity = min(remaining, float(batch.get("quantity") or 0))
        batch_id = int(batch.get("batch_id") or batch.get("id") or 0) or None
        if quantity <= 1e-9:
            continue
        slices.append(
            {
                "batch_id": batch_id,
                "batch_no": batch.get("batch_no") or item.get("batch_no") or "",
                "quantity": quantity,
                "unit_price": float(batch.get("unit_price") or item.get("unit_price") or 0),
            }
        )
        remaining -= quantity
    if remaining > 1e-9:
        slices.append(
            {
                "batch_id": None,
                "batch_no": item.get("batch_no") or "",
                "quantity": remaining,
                "unit_price": float(item.get("unit_price") or 0),
            }
        )
    if not slices:
        slices.append(
            {
                "batch_id": None,
                "batch_no": item.get("batch_no") or "",
                "quantity": issued,
                "unit_price": float(item.get("unit_price") or 0),
            }
        )
    return slices


def _fill_outbound_item_payload(item, payloads):
    """出库流水未挂明细时，用同单同物料的领用明细补批次。"""
    if item.get("item_data_json"):
        return item
    matches = payloads.get((int(item["form_id"]), int(item["material_id"] or 0)), [])
    outbound_id = int(item.get("outbound_record_id") or 0)
    chosen = None
    for match in matches:
        data = parse_json(match.get("data_json"), {})
        batches = [batch for batch in (data.get("consumed_batches") or []) if isinstance(batch, dict)]
        if any(int(batch.get("stock_record_id") or 0) == outbound_id for batch in batches):
            chosen = match
            break
    if chosen is None and len(matches) == 1:
        chosen = matches[0]
    if chosen is None:
        return item
    item["item_data_json"] = chosen.get("data_json")
    if not item.get("workflow_item_id"):
        item["workflow_item_id"] = chosen.get("id")
    return item


def _claim_outbound_lots(cursor, user_id=None):
    where = ["sr.operation_type = 'out'", "sr.quantity > 0", "f.form_type IN ('claim', 'special_claim')"]
    params = []
    if user_id is not None:
        where.append("f.applicant_id = ?")
        params.append(int(user_id))
    append_workflow_visibility(cursor, where, params, "f")
    if not temporary_inventory_enabled(cursor):
        where.append("COALESCE(b.stock_source, sr.stock_source) = 'formal'")
    cursor.execute(
        f"""
        SELECT sr.id AS outbound_record_id, sr.workflow_item_id, sr.material_id,
               sr.quantity AS issued_quantity, sr.operation_date, sr.form_no,
               sr.unit_price, sr.batch_id, sr.stock_source AS record_stock_source,
               f.id AS form_id, f.applicant_id, f.data_json AS form_data_json,
               m.material_code, m.name AS material_name, m.brand_model, m.spec, m.unit,
                b.batch_no, COALESCE(b.stock_source, sr.stock_source) AS stock_source,
               COALESCE(b.purchase_project, '') AS purchase_project,
               COALESCE(NULLIF(TRIM(b.purchase_applicant), ''), NULLIF(TRIM(wi.purchase_applicant), ''), NULLIF(TRIM(m.purchase_applicant), ''), '') AS purchase_applicant,
               b.inventory_status, b.warehouse_type, b.shelf_id, b.layer_number, b.zone_name,
               COALESCE(u.display_name, u.username, '') AS claim_applicant,
               COALESCE(r.returned_quantity, 0) AS returned_quantity,
               wi.data_json AS item_data_json
        FROM stock_records sr
        JOIN workflow_forms f ON f.form_no = sr.form_no AND f.form_type IN ('claim', 'special_claim')
        JOIN materials m ON m.id = sr.material_id
        LEFT JOIN workflow_items wi ON wi.id = sr.workflow_item_id
        LEFT JOIN users u ON u.id = f.applicant_id
        LEFT JOIN material_batches b ON b.id = sr.batch_id AND b.material_id = sr.material_id
        LEFT JOIN (
            SELECT outbound_record_id, SUM(quantity) AS returned_quantity
            FROM claim_returns GROUP BY outbound_record_id
        ) r ON r.outbound_record_id = sr.id
        WHERE {' AND '.join(where)}
        ORDER BY sr.id
        """,
        params,
    )
    rows = [dict(row) for row in cursor.fetchall()]
    form_ids = sorted({int(item["form_id"]) for item in rows})
    payloads = {}
    if form_ids:
        placeholders = ",".join("?" for _ in form_ids)
        cursor.execute(
            f"SELECT id, form_id, material_id, data_json FROM workflow_items WHERE form_id IN ({placeholders})",
            form_ids,
        )
        for row in cursor.fetchall():
            key = (int(row["form_id"]), int(row["material_id"] or 0))
            payloads.setdefault(key, []).append(dict(row))
    lots = []
    for row in rows:
        item = _fill_outbound_item_payload(row, payloads)
        claim_kind = _claim_kind_fields(item.get("form_data_json"))
        returned_left = float(item.get("returned_quantity") or 0)
        for slice_item in _outbound_batch_slices(item):
            issued_qty = float(slice_item["quantity"] or 0)
            returned_qty = min(returned_left, issued_qty)
            returned_left -= returned_qty
            lots.append(
                {
                    "source_kind": SOURCE_CLAIM_OUTBOUND,
                    "source_id": int(item["outbound_record_id"]),
                    "outbound_record_id": int(item["outbound_record_id"]),
                    "workflow_item_id": int(item["workflow_item_id"] or 0) or None,
                    "form_id": int(item["form_id"]),
                    "form_no": item.get("form_no") or "",
                    "applicant_id": int(item["applicant_id"] or 0),
                    "claim_applicant": item.get("claim_applicant") or "",
                    "material_id": int(item["material_id"]),
                    "material_code": item.get("material_code") or "",
                    "material_name": item.get("material_name") or "",
                    "brand_model": item.get("brand_model") or "",
                    "spec": item.get("spec") or "",
                    "unit": item.get("unit") or "",
                    "purchase_applicant": item.get("purchase_applicant") or "",
                    "batch_id": slice_item["batch_id"],
                    "batch_no": slice_item.get("batch_no") or "",
                    "issued_quantity": issued_qty,
                    "returned_quantity": returned_qty,
                    "operation_date": item.get("operation_date") or "",
                    "stock_source": item.get("stock_source") or STOCK_SOURCE_FORMAL,
                    "inventory_status": item.get("inventory_status"),
                    "warehouse_type": item.get("warehouse_type"),
                    "shelf_id": item.get("shelf_id"),
                    "layer_number": item.get("layer_number"),
                    "zone_name": item.get("zone_name"),
                    "unit_price": float(slice_item.get("unit_price") or 0),
                    "purchase_project": item.get("purchase_project") or "",
                    "purpose": claim_kind["purpose"],
                    "rd_item_kind": claim_kind["rd_item_kind"],
                }
            )
    return lots


def _claim_item_lots_without_stock(cursor, user_id=None, known_stock_ids=None, covered_item_ids=None, covered_pairs=None):
    known_stock_ids = known_stock_ids or set()
    covered_item_ids = covered_item_ids or set()
    covered_pairs = covered_pairs or set()
    where = [
        "f.form_type IN ('claim', 'special_claim')",
        "f.status = 'completed'",
        "COALESCE(wi.outbound_quantity, 0) > 0",
    ]
    params = []
    if user_id is not None:
        where.append("f.applicant_id = ?")
        params.append(int(user_id))
    append_workflow_visibility(cursor, where, params, "f")
    cursor.execute(
        f"""
        SELECT wi.id, wi.form_id, wi.material_id, wi.material_code, wi.material_name,
               wi.brand_model, wi.spec, wi.unit, wi.data_json, wi.stock_source,
               f.form_no, f.applicant_id, f.created_at, f.data_json AS form_data_json,
               m.material_code AS master_code, m.name AS master_name,
               m.brand_model AS master_brand, m.spec AS master_spec, m.unit AS master_unit,
               COALESCE(NULLIF(TRIM(wi.purchase_applicant), ''), NULLIF(TRIM(m.purchase_applicant), ''), '') AS purchase_applicant,
               COALESCE(u.display_name, u.username, '') AS claim_applicant
        FROM workflow_items wi
        JOIN workflow_forms f ON f.id = wi.form_id
        LEFT JOIN materials m ON m.id = wi.material_id
        LEFT JOIN users u ON u.id = f.applicant_id
        WHERE {' AND '.join(where)}
        ORDER BY wi.id
        """,
        params,
    )
    lots = []
    for row in cursor.fetchall():
        item = dict(row)
        if int(item["id"]) in covered_item_ids:
            continue
        if (int(item["form_id"]), int(item["material_id"] or 0)) in covered_pairs:
            continue
        data = parse_json(item.get("data_json"), {})
        claim_kind = _claim_kind_fields(item.get("form_data_json"))
        for batch in data.get("consumed_batches") or []:
            if not isinstance(batch, dict):
                continue
            stock_id = int(batch.get("stock_record_id") or 0)
            if stock_id and stock_id in known_stock_ids:
                continue
            quantity = float(batch.get("quantity") or 0)
            batch_id = int(batch.get("batch_id") or batch.get("id") or 0)
            if quantity <= 1e-9 or not batch_id:
                continue
            lots.append(
                {
                    "source_kind": SOURCE_CLAIM_ITEM,
                    "source_id": int(item["id"]),
                    "outbound_record_id": None,
                    "workflow_item_id": int(item["id"]),
                    "form_id": int(item["form_id"]),
                    "form_no": item.get("form_no") or "",
                    "applicant_id": int(item.get("applicant_id") or 0),
                    "claim_applicant": item.get("claim_applicant") or "",
                    "material_id": int(item["material_id"] or 0),
                    "material_code": item.get("master_code") or item.get("material_code") or "",
                    "material_name": item.get("master_name") or item.get("material_name") or "",
                    "brand_model": item.get("master_brand") or item.get("brand_model") or "",
                    "spec": item.get("master_spec") or item.get("spec") or "",
                    "unit": item.get("master_unit") or item.get("unit") or "",
                    "purchase_applicant": item.get("purchase_applicant") or "",
                    "batch_id": batch_id,
                    "batch_no": batch.get("batch_no") or "",
                    "issued_quantity": quantity,
                    "returned_quantity": 0.0,
                    "operation_date": item.get("created_at") or "",
                    "stock_source": item.get("stock_source") or STOCK_SOURCE_FORMAL,
                    "unit_price": float(batch.get("unit_price") or 0),
                    "purchase_project": str(batch.get("purchase_project") or "").strip(),
                    "purpose": claim_kind["purpose"],
                    "rd_item_kind": claim_kind["rd_item_kind"],
                }
            )
    return lots


def _borrow_claim_lots(cursor, user_id=None):
    if not _table_exists(cursor, "workflow_forms"):
        return []
    form_columns = _columns(cursor, "workflow_forms")
    if "form_type" not in form_columns:
        return []
    where = ["f.form_type = 'borrow_claim'", "f.status = 'completed'"]
    params = []
    if user_id is not None:
        where.append("f.applicant_id = ?")
        params.append(int(user_id))
    append_workflow_visibility(cursor, where, params, "f")
    cursor.execute(
        f"""
        SELECT wi.id, wi.form_id, wi.material_id, wi.material_code, wi.material_name,
               wi.brand_model, wi.spec, wi.unit, wi.request_quantity, wi.data_json, wi.stock_source,
               f.form_no, f.applicant_id, f.created_at, f.data_json AS form_data_json,
               m.material_code AS master_code, m.name AS master_name,
               m.brand_model AS master_brand, m.spec AS master_spec, m.unit AS master_unit,
               COALESCE(NULLIF(TRIM(wi.purchase_applicant), ''), NULLIF(TRIM(m.purchase_applicant), ''), '') AS purchase_applicant,
               COALESCE(u.display_name, u.username, '') AS claim_applicant
        FROM workflow_items wi
        JOIN workflow_forms f ON f.id = wi.form_id
        LEFT JOIN materials m ON m.id = wi.material_id
        LEFT JOIN users u ON u.id = f.applicant_id
        WHERE {' AND '.join(where)}
        ORDER BY wi.id
        """,
        params,
    )
    lots = []
    for row in cursor.fetchall():
        item = dict(row)
        data = parse_json(item.get("data_json"), {})
        form_data = parse_json(item.get("form_data_json"), {})
        operation_date = str(form_data.get("claimed_date") or item.get("created_at") or "")[:10]
        batches = [batch for batch in (data.get("consumed_batches") or []) if isinstance(batch, dict)]
        base = {
            "source_kind": SOURCE_BORROW_CLAIM,
            "source_id": int(item["id"]),
            "outbound_record_id": None,
            "workflow_item_id": int(item["id"]),
            "form_id": int(item["form_id"]),
            "form_no": item.get("form_no") or "",
            "applicant_id": int(item.get("applicant_id") or 0),
            "claim_applicant": item.get("claim_applicant") or "",
            "material_id": int(item["material_id"] or 0) or None,
            "material_code": item.get("master_code") or item.get("material_code") or "",
            "material_name": item.get("master_name") or item.get("material_name") or "",
            "brand_model": item.get("master_brand") or item.get("brand_model") or "",
            "spec": item.get("master_spec") or item.get("spec") or "",
            "unit": item.get("master_unit") or item.get("unit") or "",
            "purchase_applicant": item.get("purchase_applicant") or "",
            "returned_quantity": 0.0,
            "operation_date": operation_date,
            "stock_source": item.get("stock_source") or STOCK_SOURCE_FORMAL,
            "purpose": str(form_data.get("purpose") or "").strip(),
            "rd_item_kind": str(form_data.get("rd_item_kind") or "").strip(),
        }
        if batches:
            for batch in batches:
                quantity = float(batch.get("quantity") or 0)
                if quantity <= 1e-9:
                    continue
                lots.append(
                    {
                        **base,
                        "batch_id": int(batch.get("batch_id") or batch.get("id") or 0) or None,
                        "batch_no": batch.get("batch_no") or "",
                        "issued_quantity": quantity,
                        "unit_price": float(batch.get("unit_price") or 0),
                        "purchase_project": str(batch.get("purchase_project") or "").strip(),
                    }
                )
        else:
            lots.append(
                {
                    **base,
                    "batch_id": None,
                    "batch_no": "",
                    "issued_quantity": float(item.get("request_quantity") or 0),
                    "unit_price": 0.0,
                }
            )
    return lots


def _apply_return_holds(cursor, lots):
    """待入库和损坏退库占用可退数量。正常退库完成后仍以 claim_returns 为准。"""
    if not _table_exists(cursor, "issued_return_requests"):
        return lots
    cursor.execute(
        """
        SELECT source_kind, source_id, COALESCE(batch_id, 0) AS batch_id,
               SUM(CASE WHEN status = 'pending' THEN quantity ELSE 0 END) AS pending_qty,
               SUM(CASE
                     WHEN status = 'completed' AND condition_status = '损坏' AND source_kind != 'borrow_claim'
                     THEN quantity ELSE 0 END) AS damaged_qty,
               SUM(CASE
                     WHEN status = 'completed' AND source_kind = 'borrow_claim'
                     THEN quantity ELSE 0 END) AS borrow_returned
        FROM issued_return_requests
        WHERE status IN ('pending', 'completed')
        GROUP BY source_kind, source_id, COALESCE(batch_id, 0)
        """
    )
    holds = {
        (str(row["source_kind"]), int(row["source_id"] or 0), int(row["batch_id"] or 0)): row
        for row in cursor.fetchall()
    }
    for lot in lots:
        key = (lot["source_kind"], int(lot["source_id"] or 0), int(lot.get("batch_id") or 0))
        row = holds.get(key)
        if not row:
            continue
        extra = float(row["pending_qty"] or 0) + float(row["damaged_qty"] or 0) + float(row["borrow_returned"] or 0)
        if extra <= 1e-9:
            continue
        issued = float(lot.get("issued_quantity") or 0)
        consumed = float(lot.get("consumed_quantity") or 0)
        returned = float(lot.get("returned_quantity") or 0) + extra
        remaining = max(0.0, issued - returned - consumed)
        lot["returned_quantity"] = returned
        lot["returnable_quantity"] = remaining
        lot["available_quantity"] = remaining
    return lots


def list_issued_lots(cursor, user_id=None):
    """已领用批次台账，含制作占用后的剩余。"""
    outbound = _claim_outbound_lots(cursor, user_id)
    known_stock_ids = {int(lot["source_id"]) for lot in outbound}
    covered_item_ids = {
        int(lot["workflow_item_id"])
        for lot in outbound
        if lot.get("workflow_item_id")
    }
    covered_pairs = {
        (int(lot["form_id"]), int(lot["material_id"]))
        for lot in outbound
        if lot.get("batch_id")
    }
    lots = [
        *outbound,
        *_claim_item_lots_without_stock(
            cursor, user_id, known_stock_ids, covered_item_ids, covered_pairs
        ),
        *_borrow_claim_lots(cursor, user_id),
    ]
    mapping = _consumption_map(cursor)
    return _apply_return_holds(cursor, [_apply_consumption(lot, mapping) for lot in lots])


def allocate_claim_consumptions(cursor, production_source_type, production_source_id, prepared):
    """把制作消耗按批次 FIFO 记到具体已领用行。"""
    if production_source_type not in {
        PRODUCTION_SOURCE_SEMI,
        PRODUCTION_SOURCE_FINISHED,
        PRODUCTION_HOLD_SEMI,
        PRODUCTION_HOLD_FINISHED,
    }:
        raise ValueError("制作消耗类型不正确")
    lots = list_issued_lots(cursor)
    by_batch = {}
    for lot in lots:
        material_id = int(lot.get("material_id") or 0)
        batch_id = int(lot.get("batch_id") or 0)
        if not material_id or not batch_id:
            continue
        by_batch.setdefault((material_id, batch_id), []).append(lot)
    for group in by_batch.values():
        group.sort(key=lambda item: (str(item.get("operation_date") or ""), int(item.get("source_id") or 0)))
    stamp = now_text()
    for item in prepared or []:
        material_id = int(item.get("material_id") or 0)
        batch_id = int(item.get("batch_id") or 0)
        remaining = float(item.get("total_quantity") or 0)
        if remaining <= 1e-9:
            continue
        candidates = by_batch.get((material_id, batch_id), [])
        for lot in candidates:
            if remaining <= 1e-9:
                break
            available = float(lot.get("available_quantity") or 0)
            if available <= 1e-9:
                continue
            take = min(remaining, available)
            cursor.execute(
                """
                INSERT INTO claim_consumptions
                    (source_kind, source_id, batch_id, production_source_type, production_source_id,
                     material_id, quantity, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lot["source_kind"],
                    int(lot["source_id"]),
                    batch_id,
                    production_source_type,
                    int(production_source_id),
                    material_id,
                    take,
                    stamp,
                ),
            )
            lot["available_quantity"] = available - take
            remaining -= take
        if remaining > 1e-9:
            name = item.get("material_name") or f"物料{material_id}"
            raise ValueError(f"{name} 已领用未消耗数量不足")


def release_production_material_hold(cursor, form_id):
    """释放某张在途验收单占用的已领用未消耗数量。真正消耗不在这里。"""
    if not _table_exists(cursor, "claim_consumptions"):
        return
    cursor.execute(
        """
        DELETE FROM claim_consumptions
        WHERE production_source_type IN (?, ?) AND production_source_id = ?
        """,
        (PRODUCTION_HOLD_SEMI, PRODUCTION_HOLD_FINISHED, int(form_id or 0)),
    )


def production_consumption_claimants(cursor, production_source_type, production_source_id):
    """按物料+批次汇总该次制作占用对应的原领用人。"""
    mapping = {}
    if not _table_exists(cursor, "claim_consumptions"):
        return mapping
    production_source_type = str(production_source_type or "")
    production_source_id = int(production_source_id or 0)
    queries = [
        (
            """
            SELECT cc.material_id, cc.batch_id, cc.quantity,
                   COALESCE(u.display_name, u.username, '') AS claim_applicant
            FROM claim_consumptions cc
            JOIN stock_records sr ON sr.id = cc.source_id
            JOIN workflow_forms f ON f.form_no = sr.form_no AND f.form_type IN ('claim', 'special_claim')
            LEFT JOIN users u ON u.id = f.applicant_id
            WHERE cc.source_kind = ?
              AND cc.production_source_type = ?
              AND cc.production_source_id = ?
            """,
            (SOURCE_CLAIM_OUTBOUND, production_source_type, production_source_id),
        ),
        (
            """
            SELECT cc.material_id, cc.batch_id, cc.quantity,
                   COALESCE(u.display_name, u.username, '') AS claim_applicant
            FROM claim_consumptions cc
            JOIN workflow_items wi ON wi.id = cc.source_id
            JOIN workflow_forms f ON f.id = wi.form_id
            LEFT JOIN users u ON u.id = f.applicant_id
            WHERE cc.source_kind IN (?, ?)
              AND cc.production_source_type = ?
              AND cc.production_source_id = ?
            """,
            (SOURCE_CLAIM_ITEM, SOURCE_BORROW_CLAIM, production_source_type, production_source_id),
        ),
    ]
    for sql, params in queries:
        cursor.execute(sql, params)
        for row in cursor.fetchall():
            key = (int(row["material_id"] or 0), int(row["batch_id"] or 0))
            target = mapping.setdefault(key, {"quantity": 0.0, "applicants": []})
            target["quantity"] += float(row["quantity"] or 0)
            name = str(row["claim_applicant"] or "").strip()
            if name and name not in target["applicants"]:
                target["applicants"].append(name)
    return mapping


def backfill_claim_consumptions(cursor):
    """按现有制作消耗补记占用。缺台账则失败，避免 silently 丢数量。"""
    if not _table_exists(cursor, "production_material_consumptions"):
        return
    if not _table_exists(cursor, "claim_consumptions"):
        return
    cursor.execute("SELECT COUNT(*) FROM claim_consumptions")
    if int(cursor.fetchone()[0] or 0) > 0:
        return
    cursor.execute(
        """
        SELECT source_type, source_id, material_id, batch_id, quantity
        FROM production_material_consumptions
        WHERE batch_id IS NOT NULL AND quantity > 0
        ORDER BY id
        """
    )
    grouped = {}
    for row in cursor.fetchall():
        key = (str(row["source_type"] or ""), int(row["source_id"] or 0))
        grouped.setdefault(key, []).append(
            {
                "material_id": int(row["material_id"] or 0),
                "batch_id": int(row["batch_id"] or 0),
                "total_quantity": float(row["quantity"] or 0),
                "material_name": "",
            }
        )
    for (source_type, source_id), prepared in grouped.items():
        if source_type not in {PRODUCTION_SOURCE_SEMI, PRODUCTION_SOURCE_FINISHED}:
            raise ValueError("历史制作消耗类型无法分摊到已领用")
        allocate_claim_consumptions(cursor, source_type, source_id, prepared)


def _consumed_batch_key(batch):
    return (
        int(batch.get("stock_record_id") or 0),
        int(batch.get("batch_id") or batch.get("id") or 0),
    )


def _match_consumed_batch(batches, stock_record_id, batch_id):
    for batch in batches:
        if stock_record_id and _consumed_batch_key(batch)[0] == stock_record_id:
            return batch
    for batch in batches:
        if batch_id and _consumed_batch_key(batch)[1] == batch_id:
            return batch
    return None


def claim_outbound_edit_lines(cursor, form_no, item):
    """领用出库明细上的实际、每单位、账面。待消耗按实际数量，不读库存批次。"""
    item_id = int(item.get("id") or 0)
    material_id = int(item.get("material_id") or 0)
    consumed = [
        batch for batch in ((item.get("data") or {}).get("consumed_batches") or [])
        if isinstance(batch, dict)
    ]
    cursor.execute(
        """
        SELECT id, batch_id, material_id, quantity, unit_price, workflow_item_id
        FROM stock_records
        WHERE form_no = ? AND operation_type = 'out'
        ORDER BY id
        """,
        (form_no,),
    )
    records = [dict(row) for row in cursor.fetchall()]
    owned = [row for row in records if int(row.get("workflow_item_id") or 0) == item_id]
    if not owned:
        record_ids = {int(batch.get("stock_record_id") or 0) for batch in consumed}
        owned = [row for row in records if int(row["id"]) in record_ids]
    if not owned and material_id:
        owned = [row for row in records if int(row.get("material_id") or 0) == material_id]
    batch_ids = [int(row.get("batch_id") or 0) for row in owned if int(row.get("batch_id") or 0)]
    batch_nos = {}
    if batch_ids:
        placeholders = ",".join("?" for _ in batch_ids)
        cursor.execute(
            f"SELECT id, batch_no FROM material_batches WHERE id IN ({placeholders})",
            batch_ids,
        )
        batch_nos = {int(row["id"]): row["batch_no"] or "" for row in cursor.fetchall()}
    lines = []
    for row in owned:
        stock_record_id = int(row["id"])
        batch_id = int(row.get("batch_id") or 0)
        actual = float(row.get("quantity") or 0)
        matched = _match_consumed_batch(consumed, stock_record_id, batch_id) or {}
        pack = matched.get("pack_quantity")
        book = matched.get("book_quantity")
        pack_quantity = float(pack) if pack not in (None, "") else 1.0
        if pack_quantity < 1:
            pack_quantity = 1.0
        book_quantity = float(book) if book not in (None, "") else actual
        lines.append(
            {
                "stock_record_id": stock_record_id,
                "batch_id": batch_id,
                "batch_no": matched.get("batch_no") or batch_nos.get(batch_id) or "",
                "actual_quantity": actual,
                "pack_quantity": pack_quantity,
                "book_quantity": book_quantity,
            }
        )
    return lines


def _outbound_locked_quantity(cursor, stock_record_id):
    """已退和已占用不能被改小的实际数量盖住。"""
    returned = 0.0
    consumed = 0.0
    if _table_exists(cursor, "claim_returns"):
        cursor.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM claim_returns WHERE outbound_record_id = ?",
            (stock_record_id,),
        )
        returned = float(cursor.fetchone()[0] or 0)
    if _table_exists(cursor, "claim_consumptions"):
        cursor.execute(
            """
            SELECT COALESCE(SUM(quantity), 0) FROM claim_consumptions
            WHERE source_kind = ? AND source_id = ?
            """,
            (SOURCE_CLAIM_OUTBOUND, stock_record_id),
        )
        consumed = float(cursor.fetchone()[0] or 0)
    return returned + consumed


def update_claim_outbound_quantities(cursor, form, item_id, lines):
    """只改这张领用出库单的实际数量，待消耗跟着变。不改库存批次。"""
    if not isinstance(lines, list):
        raise ValueError("出库数量参数无效")
    item_id = int(item_id or 0)
    cursor.execute(
        "SELECT * FROM workflow_items WHERE id = ? AND form_id = ?",
        (item_id, int(form["id"])),
    )
    item = cursor.fetchone()
    if not item:
        raise ValueError("流程明细不存在")
    item = dict(item)
    item_data = parse_json(item.get("data_json"), {})
    consumed = [batch for batch in (item_data.get("consumed_batches") or []) if isinstance(batch, dict)]
    seen = set()
    record_columns = _columns(cursor, "stock_records")
    for line in lines:
        if not isinstance(line, dict):
            raise ValueError("出库数量参数无效")
        stock_record_id = int(line.get("stock_record_id") or 0)
        batch_id = int(line.get("batch_id") or 0)
        if not stock_record_id:
            raise ValueError("出库流水不存在")
        if stock_record_id in seen:
            raise ValueError("出库流水不能重复")
        seen.add(stock_record_id)
        cursor.execute("SELECT * FROM stock_records WHERE id = ?", (stock_record_id,))
        record = cursor.fetchone()
        if not record:
            raise ValueError("出库流水不存在")
        record = dict(record)
        if record.get("form_no") != form.get("form_no") or record.get("operation_type") != "out":
            raise ValueError("出库流水不属于该领用单")
        if int(record.get("material_id") or 0) != int(item.get("material_id") or 0):
            raise ValueError("出库流水与物料不一致")
        linked_item = int(record.get("workflow_item_id") or 0)
        if linked_item and linked_item != item_id:
            raise ValueError("出库流水不属于该明细")
        actual = quantity_value(line.get("actual_quantity"), "实际数量")
        pack = pack_quantity_value(line.get("pack_quantity") or 1)
        book = quantity_value(line.get("book_quantity"), "账面数量")
        locked = _outbound_locked_quantity(cursor, stock_record_id)
        if actual + 1e-9 < locked:
            raise ValueError("实际数量不能小于已退数量与已占用数量之和")
        unit_price = float(record.get("unit_price") or 0)
        assignments = ["quantity = ?"]
        params = [actual]
        if "amount" in record_columns:
            assignments.append("amount = ?")
            params.append(actual * unit_price)
        params.append(stock_record_id)
        cursor.execute(
            f"UPDATE stock_records SET {', '.join(assignments)} WHERE id = ?",
            params,
        )
        matched = _match_consumed_batch(consumed, stock_record_id, int(record.get("batch_id") or batch_id))
        if matched is None:
            matched = {
                "batch_id": int(record.get("batch_id") or batch_id) or None,
                "stock_record_id": stock_record_id,
                "unit_price": unit_price,
            }
            consumed.append(matched)
        matched["quantity"] = actual
        matched["pack_quantity"] = pack
        matched["book_quantity"] = book
        if not matched.get("stock_record_id"):
            matched["stock_record_id"] = stock_record_id
        if record.get("batch_id") and not matched.get("batch_id"):
            matched["batch_id"] = int(record["batch_id"])
    item_data["consumed_batches"] = consumed
    cursor.execute(
        """
        SELECT COALESCE(SUM(quantity), 0) FROM stock_records
        WHERE form_no = ? AND operation_type = 'out' AND workflow_item_id = ?
        """,
        (form.get("form_no"), item_id),
    )
    outbound_quantity = float(cursor.fetchone()[0] or 0)
    if not outbound_quantity and consumed:
        outbound_quantity = sum(float(batch.get("quantity") or 0) for batch in consumed)
    cursor.execute(
        "UPDATE workflow_items SET outbound_quantity = ?, data_json = ? WHERE id = ? AND form_id = ?",
        (outbound_quantity, json.dumps(item_data, ensure_ascii=False), item_id, int(form["id"])),
    )
