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
    borrow_form_ids = set()
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
        if record.get("borrow_form_id"):
            form_ids.add(int(record["borrow_form_id"]))
            borrow_form_ids.add(int(record["borrow_form_id"]))
        if record.get("return_form_id"):
            form_ids.add(int(record["return_form_id"]))
        # 转领用：挂在该借用记录上
        cursor.execute(
            """
            SELECT id FROM workflow_forms
            WHERE form_type = 'borrow_claim'
              AND (
                    CAST(COALESCE(json_extract(data_json, '$.borrow_record_id'), 0) AS INTEGER) = ?
                 OR CAST(COALESCE(json_extract(data_json, '$.borrow_form_id'), 0) AS INTEGER) = ?
              )
            """,
            (int(record["id"]), int(record.get("borrow_form_id") or 0)),
        )
        for row in cursor.fetchall():
            form_ids.add(int(row["id"]))

    # 领用退库：source_form_id / source_form_no 指向树内领用单
    if form_ids:
        claim_marks = _placeholders(form_ids)
        cursor.execute(
            f"""
            SELECT id, form_no FROM workflow_forms
            WHERE id IN ({claim_marks}) AND form_type IN ('claim', 'special_claim', 'borrow_claim')
            """,
            list(form_ids),
        )
        source_forms = [dict(row) for row in cursor.fetchall()]
        source_ids = [int(row["id"]) for row in source_forms]
        source_nos = [str(row["form_no"] or "") for row in source_forms if row["form_no"]]
        if source_ids:
            cursor.execute(
                f"""
                SELECT id FROM workflow_forms
                WHERE form_type = 'claim_return'
                  AND CAST(COALESCE(json_extract(data_json, '$.source_form_id'), 0) AS INTEGER)
                      IN ({_placeholders(source_ids)})
                """,
                source_ids,
            )
            for row in cursor.fetchall():
                form_ids.add(int(row["id"]))
        for source_no in source_nos:
            cursor.execute(
                """
                SELECT id FROM workflow_forms
                WHERE form_type = 'claim_return'
                  AND COALESCE(json_extract(data_json, '$.source_form_no'), '') = ?
                """,
                (source_no,),
            )
            for row in cursor.fetchall():
                form_ids.add(int(row["id"]))
    # 退库入库流水挂本批
    cursor.execute(
        f"""
        SELECT DISTINCT wi.form_id
        FROM stock_records sr
        JOIN workflow_items wi ON wi.id = sr.workflow_item_id
        JOIN workflow_forms f ON f.id = wi.form_id
        WHERE sr.batch_id IN ({marks})
          AND sr.business_type = ?
          AND f.form_type = 'claim_return'
        """,
        [*ids, BUSINESS_TYPE_CLAIM_RETURN_INBOUND],
    )
    for row in cursor.fetchall():
        if row["form_id"]:
            form_ids.add(int(row["form_id"]))

    # 验收行上的 inbound_no
    if form_ids:
        cursor.execute(
            f"""
            SELECT data_json FROM workflow_items
            WHERE form_id IN ({_placeholders(form_ids)})
            """,
            list(form_ids),
        )
        for row in cursor.fetchall():
            data = _load_json(row["data_json"])
            inbound_no = str(data.get("inbound_no") or "").strip()
            parsed = parse_daily_no(inbound_no)
            if parsed and parsed[0] == "RK":
                rk_nos.add(inbound_no)
            for batch in data.get("inbound_batches") or []:
                if isinstance(batch, dict):
                    inbound_no = str(batch.get("inbound_no") or batch.get("form_no") or "").strip()
                    parsed = parse_daily_no(inbound_no)
                    if parsed and parsed[0] == "RK":
                        rk_nos.add(inbound_no)

    return {"form_ids": form_ids, "rk_nos": rk_nos, "batch_ids": ids}


def _max_serial_for_stem(cursor, table, column, stem):
    cursor.execute(
        f"""
        SELECT COALESCE(MAX(CAST(substr({column}, ?) AS INTEGER)), 0)
        FROM {table}
        WHERE {column} GLOB ?
        """,
        (len(stem) + 1, f"{stem}[0-9]*"),
    )
    return int(cursor.fetchone()[0] or 0)


def _list_stem_form_nos(cursor, stem):
    cursor.execute(
        """
        SELECT id, form_no, title, form_type
        FROM workflow_forms
        WHERE form_no GLOB ?
        ORDER BY CAST(substr(form_no, ?) AS INTEGER), id
        """,
        (f"{stem}[0-9]*", len(stem) + 1),
    )
    return [dict(row) for row in cursor.fetchall()]


def _collect_rk_domain_nos(cursor, stem):
    """RK 不进 workflow_forms，从批次与流水收集当日域。"""
    nos = set()
    cursor.execute(
        "SELECT DISTINCT source_form_no AS no FROM material_batches WHERE source_form_no GLOB ?",
        (f"{stem}[0-9]*",),
    )
    for row in cursor.fetchall():
        if row["no"]:
            nos.add(str(row["no"]))
    cursor.execute(
        "SELECT DISTINCT form_no AS no FROM stock_records WHERE form_no GLOB ?",
        (f"{stem}[0-9]*",),
    )
    for row in cursor.fetchall():
        if row["no"]:
            nos.add(str(row["no"]))
    return nos


def build_rename_map(cursor, form_ids, rk_nos, promote_date_text):
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
            next_serial[key] = _max_serial_for_stem(cursor, "workflow_forms", "form_no", stem) + 1
        serial = next_serial[key]
        next_serial[key] = serial + 1
        return format_daily_no(prefix, promote8, serial)

    vacated = {}  # (prefix, date8) -> set of old_nos removed
    for _form_id, old_no, prefix, date8, _serial in sorted(moving, key=lambda item: item[0]):
        new_no = alloc(prefix)
        rename_map[old_no] = new_no
        vacated.setdefault((prefix, date8), set()).add(old_no)

    # RK：迁到转正日 + 记录 vacated
    rk_moving = []
    for old_no in sorted(rk_nos):
        parsed = parse_daily_no(old_no)
        if not parsed or parsed[0] != "RK":
            continue
        prefix, date8, serial = parsed
        if date8 == promote8:
            continue
        rk_moving.append((old_no, prefix, date8, serial))

    rk_next = None

    def alloc_rk():
        nonlocal rk_next
        stem = f"RK{promote8}"
        if rk_next is None:
            # 真实域 MAX
            domain = _collect_rk_domain_nos(cursor, stem)
            max_serial = 0
            for no in domain:
                parsed = parse_daily_no(no)
                if parsed:
                    max_serial = max(max_serial, parsed[2])
            # 也看 workflow_forms 以免预览占号
            max_serial = max(max_serial, _max_serial_for_stem(cursor, "workflow_forms", "form_no", stem))
            rk_next = max_serial + 1
        serial = rk_next
        rk_next = serial + 1
        return format_daily_no("RK", promote8, serial)

    for old_no, prefix, date8, _serial in rk_moving:
        rename_map[old_no] = alloc_rk()
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


def _replace_text(value, rename_map):
    text = str(value)
    # 长单号优先，避免短串误伤
    for old_no in sorted(rename_map.keys(), key=len, reverse=True):
        new_no = rename_map[old_no]
        if old_no in text:
            text = text.replace(old_no, new_no)
    return text


def _replace_json_value(value, rename_map):
    if isinstance(value, dict):
        return {key: _replace_json_value(item, rename_map) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_json_value(item, rename_map) for item in value]
    if isinstance(value, str):
        return _replace_text(value, rename_map)
    return value


def _two_phase_update_form_nos(cursor, form_rows, rename_map, timestamp):
    """先占位再写最终号，规避 UNIQUE。"""
    pending = []
    for row in form_rows:
        old_no = row["form_no"]
        new_no = rename_map.get(old_no)
        if not new_no or new_no == old_no:
            continue
        pending.append((row["id"], old_no, new_no, row.get("title") or ""))

    for form_id, _old_no, _new_no, _title in pending:
        cursor.execute(
            "UPDATE workflow_forms SET form_no = ?, updated_at = ? WHERE id = ?",
            (f"__RENUM__{form_id}", timestamp, form_id),
        )

    for form_id, old_no, new_no, title in pending:
        new_title = _replace_text(title, {old_no: new_no}) if title else new_no
        # 标题里可能还有其它被补位的单号
        new_title = _replace_text(new_title, rename_map)
        cursor.execute(
            "UPDATE workflow_forms SET form_no = ?, title = ?, updated_at = ? WHERE id = ?",
            (new_no, new_title, timestamp, form_id),
        )


def apply_rename_map(cursor, rename_map, timestamp=None):
    """把 rename_map 刷到流程单及所有字符串准外键。"""
    if not rename_map:
        return 0
    timestamp = timestamp or now_text()

    # 所有被改到的旧单号对应的流程行（含原日补位的无关单）
    old_nos = list(rename_map.keys())
    marks = _placeholders(old_nos)
    cursor.execute(
        f"SELECT id, form_no, title FROM workflow_forms WHERE form_no IN ({marks})",
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
        new_remark = _replace_text(remark, rename_map) if remark else remark
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

    # borrow_records.borrow_no（含转借子行同号）
    cursor.execute(
        f"SELECT id, borrow_no, data_json FROM borrow_records WHERE borrow_no IN ({marks})",
        old_nos,
    )
    for row in cursor.fetchall():
        row = dict(row)
        data = _replace_json_value(_load_json(row.get("data_json")), rename_map)
        cursor.execute(
            "UPDATE borrow_records SET borrow_no = ?, data_json = ?, updated_at = ? WHERE id = ?",
            (rename_map[row["borrow_no"]], json.dumps(data, ensure_ascii=False), timestamp, row["id"]),
        )

    # workflow_forms.data_json（全表扫含旧号代价大：先扫树外补位涉及的行）
    cursor.execute("SELECT id, data_json, title FROM workflow_forms")
    for row in cursor.fetchall():
        row = dict(row)
        raw = row.get("data_json") or ""
        title = row.get("title") or ""
        if not any(old in raw or old in title for old in rename_map):
            continue
        data = _replace_json_value(_load_json(raw), rename_map)
        new_title = _replace_text(title, rename_map) if title else title
        cursor.execute(
            "UPDATE workflow_forms SET data_json = ?, title = ?, updated_at = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), new_title, timestamp, row["id"]),
        )

    # workflow_items / tasks / claim_outbound_operations payload
    cursor.execute("SELECT id, data_json FROM workflow_items")
    for row in cursor.fetchall():
        raw = row["data_json"] or ""
        if not any(old in raw for old in rename_map):
            continue
        data = _replace_json_value(_load_json(raw), rename_map)
        cursor.execute(
            "UPDATE workflow_items SET data_json = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), row["id"]),
        )

    cursor.execute("SELECT id, data_json FROM workflow_tasks WHERE COALESCE(data_json, '') <> ''")
    for row in cursor.fetchall():
        raw = row["data_json"] or ""
        if not any(old in raw for old in rename_map):
            continue
        data = _replace_json_value(_load_json(raw), rename_map)
        cursor.execute(
            "UPDATE workflow_tasks SET data_json = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), row["id"]),
        )

    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'claim_outbound_operations'"
    )
    if cursor.fetchone():
        cursor.execute(
            "SELECT id, payload_json FROM claim_outbound_operations WHERE COALESCE(payload_json, '') <> ''"
        )
        for row in cursor.fetchall():
            raw = row["payload_json"] or ""
            if not any(old in raw for old in rename_map):
                continue
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                payload = raw
            if isinstance(payload, (dict, list)):
                payload = _replace_json_value(payload, rename_map)
                text = json.dumps(payload, ensure_ascii=False)
            else:
                text = _replace_text(str(payload), rename_map)
            cursor.execute(
                "UPDATE claim_outbound_operations SET payload_json = ? WHERE id = ?",
                (text, row["id"]),
            )

    return len(rename_map)


def sync_batch_numbers(cursor, batch_ids, promote_date_text, timestamp=None):
    """按转正日重算 batch_no，并刷 JSON 快照中的批号。"""
    ids = [int(item) for item in batch_ids if int(item or 0)]
    if not ids:
        return {}
    timestamp = timestamp or now_text()
    date_value = promote_date_text or today_text()
    marks = _placeholders(ids)
    cursor.execute(
        f"""
        SELECT b.id, b.batch_no, b.material_id, m.material_code
        FROM material_batches b
        JOIN materials m ON m.id = b.material_id
        WHERE b.id IN ({marks})
        """,
        ids,
    )
    batch_no_map = {}
    for row in cursor.fetchall():
        row = dict(row)
        new_no = default_edit_batch_no(date_value, row["material_code"])
        old_no = row["batch_no"] or ""
        if old_no != new_no:
            batch_no_map[old_no] = new_no
        cursor.execute(
            """
            UPDATE material_batches
            SET batch_no = ?, received_date = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_no, date_value, timestamp, row["id"]),
        )

    if not batch_no_map:
        return batch_no_map

    # 刷 items / borrow JSON 中的批号文本
    cursor.execute("SELECT id, data_json FROM workflow_items")
    for row in cursor.fetchall():
        raw = row["data_json"] or ""
        if not any(old in raw for old in batch_no_map):
            continue
        data = _replace_json_value(_load_json(raw), batch_no_map)
        cursor.execute(
            "UPDATE workflow_items SET data_json = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), row["id"]),
        )

    cursor.execute("SELECT id, data_json FROM borrow_records WHERE COALESCE(data_json, '') <> ''")
    for row in cursor.fetchall():
        raw = row["data_json"] or ""
        if not any(old in raw for old in batch_no_map):
            continue
        data = _replace_json_value(_load_json(raw), batch_no_map)
        cursor.execute(
            "UPDATE borrow_records SET data_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), timestamp, row["id"]),
        )
    return batch_no_map


def shift_tree_times(cursor, form_ids, batch_ids, date_value, timestamp):
    """树内流程与流水时间对齐转正日（含借用/归还）。"""
    ids = [int(item) for item in form_ids]
    if ids:
        marks = _placeholders(ids)
        cursor.execute(
            f"UPDATE workflow_forms SET created_at = ?, updated_at = ? WHERE id IN ({marks})",
            [timestamp, timestamp, *ids],
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
            f"UPDATE claim_outbound_operations SET created_at = ? WHERE form_id IN ({marks})",
            [timestamp, *ids],
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


def apply_promote_renumber(cursor, batch_ids, acceptance_form_id=None, date_value=None, timestamp=None):
    """转正后执行：发现树 → 改号补位 → 批号 → 时间线。返回摘要。"""
    date_value = date_value or today_text()
    timestamp = timestamp or now_text()
    tree = discover_promote_tree(cursor, batch_ids, acceptance_form_id=acceptance_form_id)
    rename_map = build_rename_map(cursor, tree["form_ids"], tree["rk_nos"], date_value)
    applied = apply_rename_map(cursor, rename_map, timestamp=timestamp)
    batch_no_map = sync_batch_numbers(cursor, tree["batch_ids"], date_value, timestamp=timestamp)
    shift_tree_times(cursor, tree["form_ids"], tree["batch_ids"], date_value, timestamp)
    return {
        "form_ids": sorted(tree["form_ids"]),
        "rk_nos": sorted(tree["rk_nos"]),
        "rename_map": rename_map,
        "rename_count": applied,
        "batch_no_map": batch_no_map,
    }


def find_acceptance_form_ids_for_batches(cursor, batch_ids):
    """从入库流水反查验收单 id。"""
    ids = [int(item) for item in batch_ids if int(item or 0)]
    found = set()
    if not ids:
        return found
    cursor.execute(
        f"""
        SELECT operation_key FROM stock_records
        WHERE batch_id IN ({_placeholders(ids)})
          AND operation_key GLOB 'acceptance:*:inbound'
        """,
        ids,
    )
    for row in cursor.fetchall():
        parts = str(row["operation_key"] or "").split(":")
        if len(parts) >= 2 and parts[1].isdigit():
            found.add(int(parts[1]))
    return found
