"""临时库转正式库：单号迁到转正日、原日同前缀补位、下游级联改号改日。

完善「转移至正式库」：验收/领用/借用/转借/归还/退库/转领用等进同一 rename_map。
"""

from __future__ import annotations

import json
import re

from warehouse_suit.db import now_text, today_text
from warehouse_suit.inventory_constants import (
    BUSINESS_TYPE_BORROW_OUTBOUND,
    BUSINESS_TYPE_BORROW_RETURN_INBOUND,
    BUSINESS_TYPE_CLAIM_OUTBOUND,
    BUSINESS_TYPE_CLAIM_RETURN_INBOUND,
    STOCK_SOURCE_FORMAL,
)
from warehouse_suit.material_utils import default_edit_batch_no
from warehouse_suit.numbering import max_daily_serial

# 标准日流水：前缀 + YYYYMMDD + 序号（至少两位）
_FORM_NO_RE = re.compile(r"^([A-Z]+)(\d{8})(\d+)$")

# 参与依赖树发现的出库/入库业务类型
_TREE_BUSINESS_TYPES = (
    BUSINESS_TYPE_CLAIM_OUTBOUND,
    BUSINESS_TYPE_CLAIM_RETURN_INBOUND,
    BUSINESS_TYPE_BORROW_OUTBOUND,
    BUSINESS_TYPE_BORROW_RETURN_INBOUND,
)

# 树内流程类型（含特殊用料条件纳入）
_TREE_FORM_TYPES = (
    "acceptance",
    "claim",
    "borrow",
    "borrow_return",
    "claim_return",
    "borrow_claim",
    "special_claim",
)


def parse_daily_no(form_no):
    """解析日流水单号，返回 (prefix, date8, serial) 或 None。"""
    match = _FORM_NO_RE.match(str(form_no or "").strip())
    if not match:
        return None
    return match.group(1), match.group(2), int(match.group(3))


def format_daily_no(prefix, date8, serial):
    """按至少两位流水格式化单号。"""
    width = max(2, len(str(int(serial))))
    return f"{prefix}{date8}{int(serial):0{width}d}"


def _load_json(text):
    try:
        data = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _date8(date_text=None):
    return (date_text or today_text()).replace("-", "")


def _placeholders(items):
    return ",".join("?" for _ in items)


def discover_promote_tree(cursor, batch_ids, acceptance_form_id=None):
    """从转正批次闭包发现相关流程单、RK 号。

    返回:
      form_ids: set[int]
      rk_nos: set[str]
      batch_ids: list[int]
    """
    ids = [int(item) for item in batch_ids if int(item or 0)]
    form_ids = set()
    rk_nos = set()
    if acceptance_form_id:
        form_ids.add(int(acceptance_form_id))
    if not ids:
        return {"form_ids": form_ids, "rk_nos": rk_nos, "batch_ids": []}

    marks = _placeholders(ids)

    # 验收入库流水 → 验收单；顺带收 RK
    cursor.execute(
        f"""
        SELECT operation_key, form_no
        FROM stock_records
        WHERE batch_id IN ({marks})
        """,
        ids,
    )
    for row in cursor.fetchall():
        key = str(row["operation_key"] or "")
        if key.startswith("acceptance:") and ":inbound" in key:
            parts = key.split(":")
            if len(parts) >= 2 and parts[1].isdigit():
                form_ids.add(int(parts[1]))
        form_no = str(row["form_no"] or "").strip()
        parsed = parse_daily_no(form_no)
        if parsed and parsed[0] == "RK":
            rk_nos.add(form_no)

    cursor.execute(
        f"SELECT source_form_no FROM material_batches WHERE id IN ({marks})",
        ids,
    )
    for row in cursor.fetchall():
        form_no = str(row["source_form_no"] or "").strip()
        parsed = parse_daily_no(form_no)
        if parsed and parsed[0] == "RK":
            rk_nos.add(form_no)

    # 出库/退库/借用流水挂到的流程单
    cursor.execute(
        f"""
        SELECT DISTINCT wi.form_id
        FROM stock_records sr
        JOIN workflow_items wi ON wi.id = sr.workflow_item_id
        JOIN workflow_forms f ON f.id = wi.form_id
        WHERE sr.batch_id IN ({marks})
          AND sr.business_type IN ({_placeholders(_TREE_BUSINESS_TYPES)})
          AND f.form_type IN ({_placeholders(_TREE_FORM_TYPES)})
        """,
        [*ids, *_TREE_BUSINESS_TYPES, *_TREE_FORM_TYPES],
    )
    for row in cursor.fetchall():
        if row["form_id"]:
            form_ids.add(int(row["form_id"]))

    # 按出库单号反查（部分流水可能无 workflow_item_id）
    cursor.execute(
        f"""
        SELECT DISTINCT f.id
        FROM stock_records sr
        JOIN workflow_forms f ON f.form_no = sr.form_no
        WHERE sr.batch_id IN ({marks})
          AND COALESCE(sr.form_no, '') <> ''
          AND f.form_type IN ({_placeholders(_TREE_FORM_TYPES)})
        """,
        [*ids, *_TREE_FORM_TYPES],
    )
    for row in cursor.fetchall():
        form_ids.add(int(row["id"]))

    # 借用记录 → 借用单 / 归还单 / 转领用
    cursor.execute(
        """
        SELECT id, borrow_form_id, return_form_id, borrow_no, data_json
        FROM borrow_records
        WHERE item_type = 'material'
        """
    )
    borrow_record_ids = set()
    for record in cursor.fetchall():
        record = dict(record)
        data = _load_json(record.get("data_json"))
        touched = False
        for batch in data.get("consumed_batches") or []:
            if not isinstance(batch, dict):
                continue
            batch_id = int(batch.get("batch_id") or batch.get("id") or 0)
            if batch_id in ids:
                touched = True
                break
        if not touched:
            continue
        borrow_record_ids.add(int(record["id"]))
        if record.get("borrow_form_id"):
            form_ids.add(int(record["borrow_form_id"]))
        if record.get("return_form_id"):
            form_ids.add(int(record["return_form_id"]))
    if borrow_record_ids:
        # 归还申请创建时已关联借用记录，入库前还没有归还流水。
        cursor.execute(
            f"""
            SELECT id FROM workflow_forms
            WHERE form_type IN ('borrow_return', 'borrow_claim')
              AND CAST(json_extract(data_json, '$.borrow_record_id') AS INTEGER)
                  IN ({_placeholders(borrow_record_ids)})
            """,
            sorted(borrow_record_ids),
        )
        form_ids.update(int(row["id"]) for row in cursor.fetchall())

    # 退库申请保存原批次 ID，入库后生成的退库批次与原批次独立。
    cursor.execute(
        f"""
        SELECT form_id FROM issued_return_requests
        WHERE batch_id IN ({marks})
          AND source_kind IN ('claim_outbound', 'borrow_claim')
          AND form_id IS NOT NULL
        """,
        ids,
    )
    form_ids.update(int(row["form_id"]) for row in cursor.fetchall())
    return {"form_ids": form_ids, "rk_nos": rk_nos, "batch_ids": ids}


def _list_stem_form_nos(cursor, stem):
    cursor.execute(
        """
        SELECT form_no
        FROM workflow_forms
        WHERE form_no GLOB ?
        ORDER BY CAST(substr(form_no, ?) AS INTEGER), id
        """,
        (f"{stem}[0-9]*", len(stem) + 1),
    )
    return [dict(row) for row in cursor.fetchall()]


def _collect_rk_domain_nos(cursor, stem):
    """RK 当日流水由批次和库存流水共同记录。"""
    cursor.execute(
        """
        SELECT source_form_no AS no FROM material_batches WHERE source_form_no GLOB ?
        UNION
        SELECT form_no FROM stock_records WHERE form_no GLOB ?
        """,
        (f"{stem}[0-9]*", f"{stem}[0-9]*"),
    )
    return {row["no"] for row in cursor.fetchall()}


def build_rename_map(cursor, form_ids, vacated_rk, promote_date_text):
    """构建 old_no -> new_no（含迁到转正日 + 原日补位）。"""
    promote8 = _date8(promote_date_text)
    rename_map = {}
    # 需要迁走的标准单号（树内）
    moving = []  # (form_id, old_no, prefix, date8, serial)
    for form_id in sorted(form_ids):
        cursor.execute("SELECT id, form_no FROM workflow_forms WHERE id = ?", (int(form_id),))
        row = cursor.fetchone()
        if not row:
            continue
        old_no = str(row["form_no"] or "")
        parsed = parse_daily_no(old_no)
        if not parsed:
            continue
        prefix, date8, serial = parsed
        if date8 == promote8:
            # 已是转正日号：仍参与原日池逻辑时可不动；这里跳过迁号
            continue
        moving.append((int(row["id"]), old_no, prefix, date8, serial))

    # 转正日各前缀下一流水（事务内计数）
    next_serial = {}

    def alloc(prefix):
        key = (prefix, promote8)
        if key not in next_serial:
            stem = f"{prefix}{promote8}"
            next_serial[key] = max_daily_serial(cursor, "workflow_forms", "form_no", stem) + 1
        serial = next_serial[key]
        next_serial[key] = serial + 1
        return format_daily_no(prefix, promote8, serial)

    vacated = {}  # (prefix, date8) -> set of old_nos removed
    for _form_id, old_no, prefix, date8, _serial in sorted(moving, key=lambda item: item[0]):
        new_no = alloc(prefix)
        rename_map[old_no] = new_no
        vacated.setdefault((prefix, date8), set()).add(old_no)

    for old_no in vacated_rk:
        parsed = parse_daily_no(old_no)
        if parsed:
            prefix, date8, _ = parsed
            vacated.setdefault((prefix, date8), set()).add(old_no)

    def _gap_fill_serials(old_nos, removed_nos, prefix, date8):
        """大于被迁走流水的号依次减 1，填掉空位（例 09 迁走后 10→09、11→10）。"""
        removed_serials = sorted(
            {
                parse_daily_no(no)[2]
                for no in removed_nos
                if parse_daily_no(no) and parse_daily_no(no)[0] == prefix and parse_daily_no(no)[1] == date8
            }
        )
        if not removed_serials:
            return
        for old_no in sorted(old_nos, key=lambda no: parse_daily_no(no)[2] if parse_daily_no(no) else 0):
            if old_no in removed_nos or old_no in rename_map:
                continue
            parsed = parse_daily_no(old_no)
            if not parsed:
                continue
            serial = parsed[2]
            delta = sum(1 for removed in removed_serials if removed < serial)
            if delta <= 0:
                continue
            new_no = format_daily_no(prefix, date8, serial - delta)
            if new_no != old_no:
                rename_map[old_no] = new_no

    # 原日补位：workflow_forms（只把大于迁走号的流水前移，不整日重排成 01..N）
    for (prefix, date8), removed in sorted(vacated.items()):
        if prefix == "RK":
            continue
        stem = f"{prefix}{date8}"
        day_rows = _list_stem_form_nos(cursor, stem)
        day_nos = [row["form_no"] for row in day_rows]
        _gap_fill_serials(day_nos, removed, prefix, date8)

    # 原日 RK 补位
    for (prefix, date8), removed in sorted(vacated.items()):
        if prefix != "RK":
            continue
        stem = f"RK{date8}"
        day_nos = list(_collect_rk_domain_nos(cursor, stem))
        _gap_fill_serials(day_nos, removed, "RK", date8)

    return rename_map


def _replace_json_value(value, replace_text):
    if isinstance(value, dict):
        return {key: _replace_json_value(item, replace_text) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_json_value(item, replace_text) for item in value]
    if isinstance(value, str):
        return replace_text(value)
    return value


def _two_phase_update_form_nos(cursor, form_rows, rename_map, timestamp):
    """先占位再写最终号，规避 UNIQUE。"""
    pending = []
    for row in form_rows:
        old_no = row["form_no"]
        new_no = rename_map.get(old_no)
        if not new_no or new_no == old_no:
            continue
        pending.append((row["id"], new_no))

    for form_id, _ in pending:
        cursor.execute(
            "UPDATE workflow_forms SET form_no = ?, updated_at = ? WHERE id = ?",
            (f"__RENUM__{form_id}", timestamp, form_id),
        )

    for form_id, new_no in pending:
        cursor.execute(
            "UPDATE workflow_forms SET form_no = ?, updated_at = ? WHERE id = ?",
            (new_no, timestamp, form_id),
        )


def apply_rename_map(cursor, rename_map, timestamp=None):
    """把 rename_map 刷到流程单及所有字符串准外键。"""
    if not rename_map:
        return 0
    timestamp = timestamp or now_text()

    # 所有被改到的旧单号对应的流程行（含原日补位的无关单）
    old_nos = list(rename_map)
    marks = _placeholders(old_nos)
    # 从原文匹配一次，保留连在部门或姓名后的单号，并避免命中更长流水。
    pattern = re.compile(
        "(?:" + "|".join(re.escape(no) for no in sorted(old_nos, key=len, reverse=True)) + r")(?![0-9])"
    )

    def replace_text(value):
        return pattern.sub(lambda match: rename_map[match[0]], value)

    cursor.execute(
        f"SELECT id, form_no FROM workflow_forms WHERE form_no IN ({marks})",
        old_nos,
    )
    form_rows = [dict(row) for row in cursor.fetchall()]
    _two_phase_update_form_nos(cursor, form_rows, rename_map, timestamp)

    # stock_records.form_no / remark
    cursor.execute(
        f"""
        SELECT id, form_no, remark FROM stock_records
        WHERE form_no IN ({marks})
           OR remark IS NOT NULL
        """,
        old_nos,
    )
    for row in cursor.fetchall():
        row = dict(row)
        new_form_no = rename_map.get(row["form_no"] or "", row["form_no"])
        remark = row.get("remark") or ""
        new_remark = replace_text(remark) if remark else remark
        if new_form_no != row["form_no"] or new_remark != remark:
            cursor.execute(
                "UPDATE stock_records SET form_no = ?, remark = ? WHERE id = ?",
                (new_form_no, new_remark, row["id"]),
            )

    # material_batches.source_form_no
    cursor.execute(
        f"SELECT id, source_form_no FROM material_batches WHERE source_form_no IN ({marks})",
        old_nos,
    )
    for row in cursor.fetchall():
        cursor.execute(
            "UPDATE material_batches SET source_form_no = ?, updated_at = ? WHERE id = ?",
            (rename_map[row["source_form_no"]], timestamp, row["id"]),
        )

    # 转借记录可能共用借用单号，快照也可能引用其他改号的流程。
    cursor.execute("SELECT id, borrow_no, data_json FROM borrow_records")
    for row in cursor.fetchall():
        borrow_no = rename_map.get(row["borrow_no"], row["borrow_no"])
        raw = row["data_json"]
        if pattern.search(raw):
            raw = json.dumps(_replace_json_value(json.loads(raw), replace_text), ensure_ascii=False)
        if borrow_no != row["borrow_no"] or raw != row["data_json"]:
            cursor.execute(
                "UPDATE borrow_records SET borrow_no = ?, data_json = ?, updated_at = ? WHERE id = ?",
                (borrow_no, raw, timestamp, row["id"]),
            )

    # 标题和流程数据从原值一起更新，补位单也只替换一次。
    cursor.execute("SELECT id, data_json, title FROM workflow_forms")
    for row in cursor.fetchall():
        row = dict(row)
        raw = row.get("data_json") or ""
        title = row.get("title") or ""
        if not (pattern.search(raw) or pattern.search(title)):
            continue
        data = _replace_json_value(_load_json(raw), replace_text)
        new_title = replace_text(title) if title else title
        cursor.execute(
            "UPDATE workflow_forms SET data_json = ?, title = ?, updated_at = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), new_title, timestamp, row["id"]),
        )

    # workflow_items / tasks / claim_outbound_operations payload
    cursor.execute("SELECT id, data_json FROM workflow_items")
    for row in cursor.fetchall():
        raw = row["data_json"] or ""
        if not pattern.search(raw):
            continue
        data = _replace_json_value(_load_json(raw), replace_text)
        cursor.execute(
            "UPDATE workflow_items SET data_json = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), row["id"]),
        )

    cursor.execute("SELECT id, data_json FROM workflow_tasks WHERE COALESCE(data_json, '') <> ''")
    for row in cursor.fetchall():
        raw = row["data_json"] or ""
        if not pattern.search(raw):
            continue
        data = _replace_json_value(_load_json(raw), replace_text)
        cursor.execute(
            "UPDATE workflow_tasks SET data_json = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), row["id"]),
        )

    cursor.execute(
        "SELECT id, payload_json FROM claim_outbound_operations WHERE COALESCE(payload_json, '') <> ''"
    )
    for row in cursor.fetchall():
        raw = row["payload_json"]
        if not pattern.search(raw):
            continue
        payload = _replace_json_value(json.loads(raw), replace_text)
        cursor.execute(
            "UPDATE claim_outbound_operations SET payload_json = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), row["id"]),
        )

    return len(rename_map)


def _sync_batch_snapshot(node, numbers, date_value):
    """按批次 ID 更新实际引用的批号及已有入库日期。"""
    if isinstance(node, list):
        for item in node:
            _sync_batch_snapshot(item, numbers, date_value)
        return
    if not isinstance(node, dict):
        return
    for id_key, number_key in (("batch_id", "batch_no"), ("inbound_batch_id", "inbound_batch_no")):
        batch_id = node.get(id_key)
        number = numbers.get(str(batch_id))
        if number is None:
            continue
        if number_key in node:
            node[number_key] = number
        if node.get("received_date"):
            node["received_date"] = date_value
    for value in node.values():
        _sync_batch_snapshot(value, numbers, date_value)


def sync_batch_numbers(cursor, batch_ids, date_value, timestamp):
    """更新批次主记录和通过 ID 引用该批次的流程快照。"""
    if not batch_ids:
        return {}
    marks = _placeholders(batch_ids)
    cursor.execute(
        f"""
        SELECT b.id, b.batch_no, m.material_code
        FROM material_batches b JOIN materials m ON m.id = b.material_id
        WHERE b.id IN ({marks})
        """,
        batch_ids,
    )
    batch_no_map = {}
    numbers = {}
    for row in cursor.fetchall():
        batch_id = row["id"]
        new_no = default_edit_batch_no(date_value, row["material_code"])
        numbers[str(batch_id)] = new_no
        if row["batch_no"] != new_no:
            batch_no_map[batch_id] = {"old": row["batch_no"], "new": new_no}
        cursor.execute(
            "UPDATE material_batches SET batch_no = ?, received_date = ?, updated_at = ? WHERE id = ?",
            (new_no, date_value, timestamp, batch_id),
        )

    for table, column in (
        ("workflow_forms", "data_json"),
        ("workflow_items", "data_json"),
        ("workflow_tasks", "data_json"),
        ("borrow_records", "data_json"),
        ("claim_outbound_operations", "payload_json"),
    ):
        cursor.execute(
            f"""
            SELECT entry.id, entry.{column} AS payload FROM {table} AS entry
            WHERE json_valid(entry.{column}) AND EXISTS (
                SELECT 1 FROM json_tree(entry.{column}) AS field
                WHERE field.key IN ('batch_id', 'inbound_batch_id')
                  AND CAST(field.value AS TEXT) IN ({marks})
            )
            """,
            list(numbers),
        )
        for row in cursor.fetchall():
            payload = json.loads(row["payload"])
            _sync_batch_snapshot(payload, numbers, date_value)
            cursor.execute(
                f"UPDATE {table} SET {column} = ? WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), row["id"]),
            )
    return batch_no_map


def shift_tree_times(cursor, form_ids, batch_ids, date_value, timestamp):
    """树内流程与流水时间对齐转正日（含借用/归还）。"""
    ids = [int(item) for item in form_ids]
    if ids:
        marks = _placeholders(ids)
        cursor.execute(
            f"""
            UPDATE workflow_forms SET created_at = ?, updated_at = ?,
                data_json = CASE WHEN form_type = 'borrow_claim'
                                 AND COALESCE(json_extract(data_json, '$.claimed_date'), '') <> ''
                    THEN json_set(data_json, '$.claimed_date', ?) ELSE data_json END
            WHERE id IN ({marks})
            """,
            [timestamp, timestamp, date_value, *ids],
        )
        cursor.execute(
            f"""
            UPDATE workflow_tasks
            SET created_at = ?, updated_at = ?,
                signed_at = CASE WHEN COALESCE(signed_at, '') <> '' THEN ? ELSE signed_at END
            WHERE form_id IN ({marks})
            """,
            [timestamp, timestamp, date_value, *ids],
        )
        cursor.execute(
            f"""
            UPDATE workflow_items
            SET data_json = json_set(data_json, '$.claimed_date', ?)
            WHERE form_id IN ({marks}) AND COALESCE(json_extract(data_json, '$.claimed_date'), '') <> ''
            """,
            [date_value, *ids],
        )
        cursor.execute(
            f"""
            UPDATE claim_outbound_operations SET created_at = ?,
                payload_json = CASE WHEN COALESCE(json_extract(payload_json, '$.signed_at'), '') <> ''
                    THEN json_set(payload_json, '$.signed_at', ?) ELSE payload_json END
            WHERE form_id IN ({marks})
            """,
            [timestamp, date_value, *ids],
        )
        cursor.execute(
            f"""
            SELECT id, data_json FROM workflow_tasks
            WHERE form_id IN ({marks}) AND json_type(data_json, '$.outbound_operations') = 'array'
            """,
            ids,
        )
        for row in cursor.fetchall():
            data = _load_json(row["data_json"])
            for operation in data["outbound_operations"]:
                if operation.get("signed_at"):
                    operation["signed_at"] = date_value
            cursor.execute(
                "UPDATE workflow_tasks SET data_json = ? WHERE id = ?",
                (json.dumps(data, ensure_ascii=False), row["id"]),
            )
        cursor.execute(
            f"""
            UPDATE temporary_issue_obligations
            SET created_at = ?, updated_at = ?,
                settled_at = CASE WHEN COALESCE(settled_at, '') <> '' THEN ? ELSE settled_at END
            WHERE claim_form_id IN ({marks})
            """,
            [timestamp, timestamp, timestamp, *ids],
        )
        cursor.execute(
            f"""
            UPDATE borrow_records
            SET outbound_date = CASE WHEN COALESCE(outbound_date, '') <> '' THEN ? ELSE outbound_date END,
                return_date = CASE WHEN COALESCE(return_date, '') <> '' THEN ? ELSE return_date END,
                updated_at = ?
            WHERE borrow_form_id IN ({marks}) OR return_form_id IN ({marks})
            """,
            [date_value, date_value, timestamp, *ids, *ids],
        )

    batch_list = [int(item) for item in batch_ids]
    if batch_list:
        marks = _placeholders(batch_list)
        cursor.execute(
            f"""
            UPDATE stock_records
            SET operation_date = ?, created_at = ?, stock_source = ?
            WHERE batch_id IN ({marks})
            """,
            [date_value, timestamp, STOCK_SOURCE_FORMAL, *batch_list],
        )
        # 退库会另建临时批次，流水改期时沿用其独立的库存来源。
        cursor.execute(
            f"""
            UPDATE stock_records SET operation_date = ?, created_at = ?
            WHERE id IN (
                SELECT ret.inbound_record_id FROM claim_returns ret
                JOIN stock_records outbound ON outbound.id = ret.outbound_record_id
                WHERE outbound.batch_id IN ({marks})
            ) OR operation_key IN (
                SELECT 'claim-return:' || operation_key FROM issued_return_requests
                WHERE batch_id IN ({marks})
                  AND source_kind IN ('claim_outbound', 'borrow_claim')
            )
            """,
            [date_value, timestamp, *batch_list, *batch_list],
        )


def apply_promote_renumber(cursor, batch_ids, acceptance_form_id=None, date_value=None, timestamp=None):
    """转正后执行：发现树 → 改号补位 → 批号 → 时间线。返回摘要。"""
    date_value = date_value or today_text()
    timestamp = timestamp or now_text()
    tree = discover_promote_tree(cursor, batch_ids, acceptance_form_id=acceptance_form_id)
    from warehouse_suit.acceptance_promotion import migrate_receipt_numbers

    rk_changes, vacated_rk = migrate_receipt_numbers(cursor, tree["batch_ids"], date_value)
    rename_map = build_rename_map(cursor, tree["form_ids"], vacated_rk, date_value)
    applied = apply_rename_map(cursor, rename_map, timestamp=timestamp)
    batch_no_map = sync_batch_numbers(cursor, tree["batch_ids"], date_value, timestamp=timestamp)
    shift_tree_times(cursor, tree["form_ids"], tree["batch_ids"], date_value, timestamp)
    for form_id in tree["form_ids"]:
        cursor.execute(
            """
            UPDATE workflow_items SET stock_source = ?
            WHERE form_id = ? AND EXISTS (
                SELECT 1 FROM workflow_forms f
                WHERE f.id = workflow_items.form_id AND f.form_type IN ('acceptance','claim','special_claim','borrow_claim')
            )
            """,
            (STOCK_SOURCE_FORMAL, form_id),
        )
        cursor.execute(
            """
            UPDATE workflow_forms SET data_json = json_set(data_json, '$.promoted_to_formal', json('true'),
                                                               '$.promoted_at', ?)
            WHERE id = ? AND form_type = 'acceptance'
            """,
            (date_value, form_id),
        )
    return {
        "rk_changes": rk_changes,
        "form_ids": sorted(tree["form_ids"]),
        "rk_nos": sorted(tree["rk_nos"]),
        "rename_map": rename_map,
        "rename_count": applied,
        "batch_no_map": batch_no_map,
    }
