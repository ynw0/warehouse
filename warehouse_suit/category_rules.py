"""本库大中小类规则。原分类表和确认后的新分类存在同一张表。"""

import json
import os
from html import escape

from warehouse_suit.db import now_text


SEED_PATH = os.path.join(os.path.dirname(__file__), "category_rules_seed.json")


def load_seed():
    """读取随系统发布的原分类表。"""
    with open(SEED_PATH, encoding="utf-8") as handle:
        rows = json.load(handle)
    if not isinstance(rows, list) or not rows:
        raise ValueError("分类规则种子为空")
    return rows


def seed_rules(conn):
    """建表并写入原分类。已有的同一分类不覆盖。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS category_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            warehouse_code TEXT NOT NULL DEFAULT '',
            major_code TEXT NOT NULL,
            middle_code TEXT NOT NULL DEFAULT '',
            small_code TEXT NOT NULL DEFAULT '',
            major_name TEXT NOT NULL DEFAULT '',
            middle_name TEXT NOT NULL DEFAULT '',
            small_name TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            example_code TEXT NOT NULL DEFAULT '',
            origin TEXT NOT NULL DEFAULT 'seed',
            confirmed_by TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_category_rules_key
        ON category_rules(warehouse_code, major_code, middle_code, small_code)
        """
    )
    payload = [
        (
            row.get("warehouse_code") or "",
            row["major_code"],
            row.get("middle_code") or "",
            row.get("small_code") or "",
            row.get("major_name") or "",
            row.get("middle_name") or "",
            row.get("small_name") or "",
            row.get("note") or "",
            row.get("example_code") or "",
            row.get("origin") or "seed",
            "",
            "",
        )
        for row in load_seed()
    ]
    conn.executemany(
        """
        INSERT OR IGNORE INTO category_rules (
            warehouse_code, major_code, middle_code, small_code,
            major_name, middle_name, small_name, note, example_code,
            origin, confirmed_by, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )


def sync_seed_rules(conn):
    """补齐种子中的新分类，并更新仍为原规则的名称。已确认的分类不改。"""
    seed_rules(conn)
    for row in load_seed():
        conn.execute(
            """
            UPDATE category_rules
            SET major_name = ?, middle_name = ?, small_name = ?, note = ?
            WHERE warehouse_code = ? AND major_code = ? AND middle_code = ? AND small_code = ?
              AND origin = 'seed'
            """,
            (
                row.get("major_name") or "",
                row.get("middle_name") or "",
                row.get("small_name") or "",
                row.get("note") or "",
                row.get("warehouse_code") or "",
                row["major_code"],
                row.get("middle_code") or "",
                row.get("small_code") or "",
            ),
        )


def list_category_rules(cursor):
    """分类规则的结构化列表，供 AI 助手查询。"""
    cursor.execute(
        """
        SELECT warehouse_code, major_code, middle_code, small_code,
               major_name, middle_name, small_name, note, example_code, origin
        FROM category_rules
        ORDER BY warehouse_code, major_code, middle_code, small_code, id
        """
    )
    return [dict(row) for row in cursor.fetchall()]


def rules_text(cursor):
    """给模型的分类规则文本。只作参考。"""
    cursor.execute(
        """
        SELECT * FROM category_rules
        ORDER BY warehouse_code, major_code, middle_code, small_code, id
        """
    )
    lines = ["分类规则（只供参考，不能直接抄成结论）。"]
    for row in cursor.fetchall():
        lines.append(_rule_line(row))
    return "\n".join(lines)


def rules_html(cursor):
    """物料编码规则页。原分类和后确认的分类在同一张表。"""
    cursor.execute(
        """
        SELECT * FROM category_rules
        ORDER BY warehouse_code, major_code, middle_code, small_code, id
        """
    )
    rows = cursor.fetchall()
    if not rows:
        return "<p>暂无内容</p>"
    body = []
    for row in rows:
        warehouse = row["warehouse_code"] or "未分仓库"
        level = "大类"
        code = row["major_code"]
        name = row["major_name"]
        if row["small_code"]:
            level = "小类"
            code = "%s/%s/%s" % (row["major_code"], row["middle_code"], row["small_code"])
            name = row["small_name"]
        elif row["middle_code"]:
            level = "中类"
            code = "%s/%s" % (row["major_code"], row["middle_code"])
            name = row["middle_name"]
        origin = "已确认" if row["origin"] == "confirmed" else "原规则"
        body.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (
                escape(warehouse),
                escape(level),
                escape(code),
                escape(name or ""),
                escape(row["note"] or ""),
                escape(row["example_code"] or ""),
                escape(origin),
            )
        )
    return (
        "<table><thead><tr><th>仓库</th><th>层级</th><th>编码</th><th>名称</th>"
        "<th>说明</th><th>示例编码</th><th>来源</th></tr></thead><tbody>%s</tbody></table>"
        % "".join(body)
    )


def save_confirmed_rule(cursor, rule, example_code, operator_name):
    """把确认过的新分类写回同一张规则表。同一键再次确认时更新原行。"""
    if not isinstance(rule, dict):
        raise ValueError("新分类规则不完整")
    warehouse = _digits(rule.get("warehouse_code"), "仓库码")
    if warehouse not in {"10", "20"}:
        raise ValueError("仓库码只能是 10 或 20")
    major = _digits(rule.get("major_code"), "大类")
    middle = _digits(rule.get("middle_code"), "中类")
    small = _digits(rule.get("small_code"), "小类")
    major_name = str(rule.get("major_name") or "").strip()
    middle_name = str(rule.get("middle_name") or "").strip()
    small_name = str(rule.get("small_name") or "").strip()
    if not major_name or not middle_name or not small_name:
        raise ValueError("新分类需要大类、中类、小类名称")
    reason = str(rule.get("reason") or "").strip()
    example = str(example_code or "").strip()
    stamp = now_text()
    who = str(operator_name or "")
    _upsert(cursor, warehouse, major, "", "", major_name, "", "", "", "", who, stamp)
    _upsert(cursor, warehouse, major, middle, "", major_name, middle_name, "", "", "", who, stamp)
    _upsert(cursor, warehouse, major, middle, small, major_name, middle_name, small_name, reason, example, who, stamp)


def _upsert(cursor, warehouse, major, middle, small, major_name, middle_name, small_name, note, example, operator_name, stamp):
    cursor.execute(
        """
        INSERT INTO category_rules (
            warehouse_code, major_code, middle_code, small_code,
            major_name, middle_name, small_name, note, example_code,
            origin, confirmed_by, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?)
        ON CONFLICT(warehouse_code, major_code, middle_code, small_code) DO UPDATE SET
            major_name = CASE WHEN excluded.major_name != '' THEN excluded.major_name ELSE category_rules.major_name END,
            middle_name = CASE WHEN excluded.middle_name != '' THEN excluded.middle_name ELSE category_rules.middle_name END,
            small_name = CASE WHEN excluded.small_name != '' THEN excluded.small_name ELSE category_rules.small_name END,
            note = CASE WHEN excluded.note != '' THEN excluded.note ELSE category_rules.note END,
            example_code = CASE WHEN excluded.example_code != '' THEN excluded.example_code ELSE category_rules.example_code END,
            origin = 'confirmed',
            confirmed_by = excluded.confirmed_by,
            updated_at = excluded.updated_at
        """,
        (warehouse, major, middle, small, major_name, middle_name, small_name, note, example, operator_name, stamp),
    )


def _rule_line(row):
    warehouse = str(row["warehouse_code"] or "")
    head = ("仓库%s " % warehouse) if warehouse else ""
    note = str(row["note"] or "")
    example = str(row["example_code"] or "")
    if row["small_code"]:
        text = "%s小类 %s/%s/%s %s" % (head, row["major_code"], row["middle_code"], row["small_code"], row["small_name"] or "")
    elif row["middle_code"]:
        text = "%s中类 %s/%s %s" % (head, row["major_code"], row["middle_code"], row["middle_name"] or "")
    else:
        text = "%s大类 %s %s" % (head, row["major_code"], row["major_name"] or "")
    if note:
        text += " %s" % note
    if example:
        text += " 示例 %s" % example
    if row["origin"] == "confirmed":
        text += " （已确认）"
    return text


def _digits(value, label):
    text = str(value or "").strip()
    if len(text) != 2 or not text.isdigit():
        raise ValueError("%s必须是两位数字" % label)
    return text
