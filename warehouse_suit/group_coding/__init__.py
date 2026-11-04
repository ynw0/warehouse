"""加载集团码表，对外只提供分类和补充两个函数。"""

from __future__ import annotations

import threading
from pathlib import Path

from warehouse_suit.group_coding.catalogs import (
    CategoryCatalog,
    SelectionCatalog,
    ServiceCatalog,
    TemplateRepository,
)
from warehouse_suit.group_coding.pipeline import GroupPipeline


_PIPELINE = None
_LOCK = threading.Lock()
DATA_DIR = Path(__file__).resolve().parent / "data"


def get_pipeline():
    """码表只加载一次。"""
    global _PIPELINE
    if _PIPELINE is not None:
        return _PIPELINE
    with _LOCK:
        if _PIPELINE is None:
            catalog = CategoryCatalog.from_json(DATA_DIR / "material_categories.json")
            selection = SelectionCatalog.from_json(DATA_DIR / "category_selection.json", catalog)
            templates = TemplateRepository.from_json(DATA_DIR / "material_description_templates.json")
            services = ServiceCatalog.from_json(DATA_DIR / "service_categories.json")
            _PIPELINE = GroupPipeline(catalog, selection, templates, services)
    return _PIPELINE


def classify_group_material(text, complete):
    """按名称、品牌型号、技术规格和单位做集团分类。"""
    return get_pipeline().process(str(text or "").strip(), complete)


def supplement_group_material(previous, user_input, complete):
    """补充属性后继续，已选定的六位分类保持不变。"""
    return get_pipeline().supplement(previous, str(user_input or "").strip(), complete)
