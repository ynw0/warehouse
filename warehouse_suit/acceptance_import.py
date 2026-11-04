"""验收导入只填当前验收单，不入库。"""

from __future__ import annotations

import csv
import io

from warehouse_suit.project_service import active_project_codes
from warehouse_suit.usage_kind import pack_quantity_value, validate_usage_kind
from warehouse_suit.validation import quantity_value


TEMPLATE_HEADERS = [
    "物料编号或名称",
    "品牌型号",
    "技术规格",
    "采购申请人",
    "采购申请项目",
    "用料形式",
    "采购数量",
    "到货数量",
    "每单位数量",
    "单位",
    "总金额",
]


def acceptance_import_template():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(TEMPLATE_HEADERS)
    return buffer.getvalue()


def parse_acceptance_import(cursor, text):
    reader = csv.DictReader(io.StringIO(str(text or "")))
    if not reader.fieldnames or [name.strip() for name in reader.fieldnames] != TEMPLATE_HEADERS:
        raise ValueError("导入模板列不正确")
    allowed = set(active_project_codes(cursor))
    rows = []
    for index, raw in enumerate(reader, start=2):
        keyword = str(raw.get("物料编号或名称") or "").strip()
        if not keyword:
            raise ValueError(f"第{index}行物料编号或名称不能为空")
        cursor.execute(
            """
            SELECT id, material_code, name, brand_model, spec, unit
            FROM materials
            WHERE material_code = ? OR name = ?
            ORDER BY CASE WHEN material_code = ? THEN 0 ELSE 1 END, id
            """,
            (keyword, keyword, keyword),
        )
        matches = [dict(row) for row in cursor.fetchall()]
        if not matches:
            raise ValueError(f"第{index}行物料不存在，整份不导入")
        material = matches[0]
        project = str(raw.get("采购申请项目") or "").strip()
        if project not in allowed:
            raise ValueError(f"第{index}行采购申请项目不可选，整份不导入")
        try:
            usage_kind = validate_usage_kind(raw.get("用料形式"))
            pack_quantity = pack_quantity_value(raw.get("每单位数量"))
            purchase_quantity = quantity_value(raw.get("采购数量"), "采购数量", positive=True)
            arrival_quantity = quantity_value(raw.get("到货数量"), "到货数量", positive=True)
        except ValueError as exc:
            raise ValueError(f"第{index}行{exc}，整份不导入") from exc
        rows.append(
            {
                "material_id": int(material["id"]),
                "material_code": material["material_code"],
                "material_name": material["name"],
                "brand_model": str(raw.get("品牌型号") or material.get("brand_model") or "").strip(),
                "spec": str(raw.get("技术规格") or material.get("spec") or "").strip(),
                "purchase_applicant": str(raw.get("采购申请人") or "").strip(),
                "purchase_project": project,
                "usage_kind": usage_kind,
                "purchase_quantity": purchase_quantity,
                "arrival_quantity": arrival_quantity,
                "pack_quantity": pack_quantity,
                "arrival_unit": str(raw.get("单位") or "").strip(),
                "unit": material.get("unit") or "",
                "total_amount": str(raw.get("总金额") or "").strip(),
            }
        )
    if not rows:
        raise ValueError("导入文件没有物料行")
    return rows
