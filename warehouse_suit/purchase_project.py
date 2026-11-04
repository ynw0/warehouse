"""采购申请项目：选项校验、用途推导、按项目汇总库存。"""

from __future__ import annotations

from warehouse_suit.inventory_constants import STOCK_SOURCE_FORMAL, STOCK_SOURCE_TEMPORARY
from warehouse_suit.material_repository import material_stock_total
from warehouse_suit.validation import validate_project_code

OFFICE_PURCHASE_PROJECT = "办公"


def normalize_purchase_project(value):
    return str(value or "").strip()


def allowed_purchase_projects(cursor):
    """下拉选项来自项目设置中仍启用的代号。"""
    from warehouse_suit.project_service import active_project_codes

    return active_project_codes(cursor)


def validate_purchase_project(cursor, value, *, required=True):
    text = normalize_purchase_project(value)
    if not text:
        if required:
            raise ValueError("采购申请项目不能为空")
        return ""
    if text not in allowed_purchase_projects(cursor):
        raise ValueError("采购申请项目不在可选范围内")
    return text


def purpose_fields_from_purchase_project(purchase_project):
    purchase_project = normalize_purchase_project(purchase_project)
    if purchase_project == OFFICE_PURCHASE_PROJECT:
        return {
            "purpose": "办公",
            "rd_item_kind": "",
            "project_code": "",
            "purchase_project": purchase_project,
        }
    return {
        "purpose": "研发",
        "rd_item_kind": "项目物料",
        "project_code": purchase_project,
        "purchase_project": purchase_project,
    }


def require_same_purchase_project(values):
    projects = [normalize_purchase_project(value) for value in values]
    if not projects or any(not item for item in projects):
        raise ValueError("采购申请项目不能为空")
    unique = set(projects)
    if len(unique) > 1:
        raise ValueError("一张单据只允许一个采购申请项目")
    return projects[0]


def apply_purpose_payload(cursor, data, purchase_project):
    """由表头覆盖研发类型（可改为辅料），其余必须与采购项目一致。"""
    purchase_project = validate_purchase_project(cursor, purchase_project)
    derived = purpose_fields_from_purchase_project(purchase_project)
    purpose = str((data or {}).get("purpose") or derived["purpose"]).strip()
    if purpose not in {"办公", "研发"}:
        raise ValueError("用途必须选择办公或研发")
    if purchase_project == OFFICE_PURCHASE_PROJECT:
        if purpose != "办公":
            raise ValueError("办公采购项目的用途必须是办公")
        return {
            "purpose": "办公",
            "rd_item_kind": "",
            "project_code": "",
            "purchase_project": purchase_project,
            "project_material_kind": "",
        }
    if purpose != "研发":
        raise ValueError("项目采购的用途必须是研发")
    rd_item_kind = str((data or {}).get("rd_item_kind") or derived["rd_item_kind"]).strip()
    if rd_item_kind not in {"项目物料", "辅料"}:
        raise ValueError("研发用途需要选择项目物料或辅料")
    if rd_item_kind == "项目物料":
        project_code = validate_project_code(
            (data or {}).get("project_code") or derived["project_code"]
        )
        if project_code != purchase_project:
            raise ValueError("项目代号必须与采购申请项目一致")
    else:
        project_code = validate_project_code((data or {}).get("project_code") or "")
    return {
        "purpose": purpose,
        "rd_item_kind": rd_item_kind,
        "project_code": project_code,
        "purchase_project": purchase_project,
        "project_material_kind": "",
    }


def require_stored_purchase_project(value, label="单据"):
    text = normalize_purchase_project(value)
    if not text:
        raise ValueError(f"{label}没有采购申请项目，不能转领用")
    return text


def purchase_project_from_acceptance_code(project_code):
    text = normalize_purchase_project(project_code)
    return text or OFFICE_PURCHASE_PROJECT


def stock_source_quantities_for_project(
    cursor, material_id, purchase_project, temporary_enabled, usage_kind=None
):
    purchase_project = normalize_purchase_project(purchase_project)
    formal = material_stock_total(
        cursor,
        material_id,
        stock_source=STOCK_SOURCE_FORMAL,
        purchase_project=purchase_project,
        usage_kind=usage_kind,
    )
    temporary = (
        material_stock_total(
            cursor,
            material_id,
            stock_source=STOCK_SOURCE_TEMPORARY,
            purchase_project=purchase_project,
            usage_kind=usage_kind,
        )
        if temporary_enabled
        else 0.0
    )
    return {
        "formal": float(formal or 0),
        "temporary": float(temporary or 0),
        "total": float(formal or 0) + float(temporary or 0),
    }
