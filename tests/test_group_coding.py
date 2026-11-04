"""集团分类工具、赋码校验和保存。"""

import json
from pathlib import Path

import pytest

from warehouse_suit.coding_agent import (
    confirm_category_rule,
    fail_stale_coding_jobs,
    judge_warehouse_code,
    parse_model_json,
    structured_smoke,
    verify_warehouse_code,
)
from warehouse_suit.db import now_text
from warehouse_suit.group_coding.catalogs import CategoryCatalog, SelectionCatalog, ServiceCatalog, TemplateRepository
from warehouse_suit.group_coding.pipeline import GroupPipeline
from warehouse_suit.purchase_request_service import save_summary_to_warehouse


DATA = Path(__file__).resolve().parents[1] / "warehouse_suit" / "group_coding" / "data"
LABELS = Path(__file__).parent / "fixtures" / "group_coding_labels.json"


def test_leaf_retrieval_keeps_the_whole_middle_class():
    catalog = CategoryCatalog.from_json(DATA / "material_categories.json")
    material = {
        "通用名称": "六角螺栓",
        "品牌": None,
        "型号": None,
        "材质": "钢",
        "用途": None,
        "规格": ["M10"],
        "其他可核实属性": {},
    }
    leaves = catalog.leaf_categories(["1801"])
    ranked = catalog.retrieve_leaf_categories(["1801"], material)
    assert ranked[0]["所属分类编码"] == "180101"
    assert len(ranked) == len(leaves)
    assert "180199" in {item["所属分类编码"] for item in ranked}


def test_group_pipeline_rejects_code_outside_candidates():
    records = [
        _record("100101", "冷轧板材", "10", "黑色金属材料", "1001", "板材", "320811"),
        _record("100199", "其他板材", "10", "黑色金属材料", "1001", "板材", "320899"),
    ]
    pipeline = _pipeline(records)
    calls = {"n": 0}

    def complete(system, user):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"general_name": "钢板", "object_head": "钢板", "object_form": "板材", "specifications": ["10mm"]}
        if calls["n"] == 2:
            return {"status": "matched", "ranked_categories": [{"code": "10"}], "rationale": "金属板"}
        if calls["n"] == 3:
            return {"status": "matched", "ranked_categories": [{"code": "1001"}], "rationale": "板材"}
        return {"status": "matched", "selected_category_code": "999999", "rationale": "编造"}

    result = pipeline.process("钢板 10mm", complete)
    assert result["status"] == "model_error"
    assert "候选范围外" in result["message"]
    assert result["group_category_code"] == ""


def test_missing_template_uses_part1_code():
    records = [_record("100104", "军工专用板材", "10", "黑色金属材料", "1001", "板材", "320899")]
    pipeline = _pipeline(records, templates=[])
    calls = {"n": 0}

    def complete(system, user):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"general_name": "军工板", "object_head": "钢板", "object_form": "板材"}
        if calls["n"] == 2:
            return {"status": "matched", "ranked_categories": [{"code": "10"}], "rationale": "金属"}
        if calls["n"] == 3:
            return {"status": "matched", "ranked_categories": [{"code": "1001"}], "rationale": "板"}
        return {"status": "matched", "selected_category_code": "100104", "rationale": "专用板"}

    result = pipeline.process("军工专用板材", complete)
    assert result["status"] == "master_data_missing"
    assert result["group_category_code"] == "100104"
    assert result["group_service_code"] == "320899"


def test_structured_smoke_and_invalid_json():
    with pytest.raises(ValueError):
        parse_model_json("请看这个编码 123")
    structured_smoke(lambda system, user: {"ok": True})
    with pytest.raises(ValueError):
        structured_smoke(lambda system, user: {"ok": False})


def test_manual_group_labels_skip_when_empty():
    rows = json.loads(LABELS.read_text(encoding="utf-8"))
    if not rows:
        pytest.skip("没有人工标尺")
    assert rows


def test_warehouse_code_is_not_rewritten(db):
    cursor = db.cursor()
    now = now_text()
    cursor.execute(
        "INSERT INTO materials (material_code, name, unit, created_at, updated_at) VALUES (?, '电容', '个', ?, ?)",
        ("10200101010010", now, now),
    )
    assert verify_warehouse_code(cursor, "10209901010010", "新物料") == "10209901010010"
    with pytest.raises(ValueError, match="另一条物料"):
        verify_warehouse_code(cursor, "10200101010010", "电阻")
    decision = {"material_code": "10209901010020", "same_as_existing": False, "rationale": "新规格加 10"}
    assert judge_warehouse_code(cursor, "新物料", decision) == "10209901010020"


def test_confirmed_category_is_written_into_the_rule_table(db):
    cursor = db.cursor()
    now = now_text()
    cursor.execute(
        """
        INSERT INTO coding_jobs
            (form_id, item_id, status, input_json, result_json, error, operator_name, created_at, updated_at)
        VALUES (1, 1, 'succeeded', '{}', ?, '', '', ?, ?)
        """,
        (json.dumps({
            "new_category": True,
            "material_code": "10200307010010",
            "proposed_rule": {
                "warehouse_code": "20",
                "major_code": "03",
                "middle_code": "07",
                "small_code": "01",
                "major_name": "核心模块类",
                "middle_name": "标定工具",
                "small_name": "标定支架",
                "reason": "原分类没有这一中类",
            },
        }, ensure_ascii=False), now, now),
    )
    job_id = cursor.lastrowid
    confirm_category_rule(cursor, job_id, "仓库管理员")
    confirm_category_rule(cursor, job_id, "仓库管理员")
    rows = cursor.execute(
        """
        SELECT small_name, note, example_code, origin FROM category_rules
        WHERE warehouse_code = '20' AND major_code = '03' AND middle_code = '07' AND small_code = '01'
        """
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["small_name"] == "标定支架"
    assert rows[0]["note"] == "原分类没有这一中类"
    assert rows[0]["example_code"] == "10200307010010"
    assert rows[0]["origin"] == "confirmed"
    original = cursor.execute(
        "SELECT origin FROM category_rules WHERE warehouse_code = '' AND major_code = '01' AND middle_code = '' AND small_code = ''"
    ).fetchone()
    assert original["origin"] == "seed"


def test_stale_coding_job_fails_on_startup(db):
    cursor = db.cursor()
    now = now_text()
    cursor.execute(
        """
        INSERT INTO coding_jobs
            (form_id, item_id, status, input_json, result_json, error, operator_name, created_at, updated_at)
        VALUES (1, 1, 'running', '{}', '{}', '', '', ?, ?)
        """,
        (now, now),
    )
    db.commit()
    assert fail_stale_coding_jobs(db) == 1
    status = db.execute("SELECT status, error FROM coding_jobs").fetchone()
    assert status["status"] == "failed"
    assert "中断" in status["error"]


def test_save_accepts_new_code_and_requires_job_for_group_codes(db):
    cursor = db.cursor()
    now = now_text()
    cursor.execute("SELECT id FROM users ORDER BY id LIMIT 1")
    user_id = cursor.fetchone()[0]
    cursor.execute(
        "INSERT INTO materials (material_code, name, unit, created_at, updated_at) VALUES ('10200101010010', '电容', '个', ?, ?)",
        (now, now),
    )
    form_id, item_id = _summary_line(cursor, user_id, "电阻", now)
    save_summary_to_warehouse(cursor, form_id, [{
        "id": item_id,
        "material_code": "10200101010020",
        "unit": "个",
    }])
    stored = cursor.execute("SELECT material_code FROM materials WHERE name = '电阻'").fetchone()
    assert stored["material_code"] == "10200101010020"

    form_id, item_id = _summary_line(cursor, user_id, "导线", now)
    with pytest.raises(ValueError, match="另一条物料"):
        save_summary_to_warehouse(cursor, form_id, [{
            "id": item_id,
            "material_code": "10200101010010",
            "unit": "个",
        }])

    form_id, item_id = _summary_line(cursor, user_id, "螺栓", now)
    with pytest.raises(ValueError, match="赋码任务"):
        save_summary_to_warehouse(cursor, form_id, [{
            "id": item_id,
            "material_code": "10200102010010",
            "unit": "个",
            "group_category_code": "180101",
            "group_service_code": "1",
        }])
    cursor.execute(
        """
        INSERT INTO coding_jobs
            (form_id, item_id, status, input_json, result_json, error, operator_name, created_at, updated_at)
        VALUES (?, ?, 'succeeded', '{}', ?, '', '', ?, ?)
        """,
        (
            form_id,
            item_id,
            json.dumps({
                "group_category_code": "180101",
                "group_service_code": "320101",
                "standard_description": "六角螺栓",
            }, ensure_ascii=False),
            now,
            now,
        ),
    )
    save_summary_to_warehouse(cursor, form_id, [{
        "id": item_id,
        "material_code": "10200102010010",
        "unit": "个",
        "group_category_code": "180101",
        "group_service_code": "320101",
    }])
    saved = cursor.execute("SELECT group_category_code, group_service_code, group_standard_description FROM materials WHERE material_code = '10200102010010'").fetchone()
    assert saved["group_category_code"] == "180101"
    assert saved["group_service_code"] == "320101"
    assert saved["group_standard_description"] == "六角螺栓"


def _summary_line(cursor, user_id, name, now):
    cursor.execute(
        """
        INSERT INTO workflow_forms
            (form_no, form_type, title, status, current_step, applicant_id, data_json, created_at, updated_at)
        VALUES (?, 'purchase_summary', '汇总', 'summarized', 'purchase_register', ?, '{}', ?, ?)
        """,
        ("HZ-%s-%s" % (name, now), user_id, now, now),
    )
    form_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO workflow_items
            (form_id, material_code, material_name, brand_model, spec, unit, request_quantity, data_json)
        VALUES (?, '', ?, '', '', '个', 1, '{}')
        """,
        (form_id, name),
    )
    return form_id, cursor.lastrowid


def _record(code, name, large, large_name, middle, middle_name, part1):
    return {
        "所属分类编码": code,
        "分类名称": name,
        "大类编码": large,
        "大类名称": large_name,
        "中类编码": middle,
        "中类名称": middle_name,
        "计量单位": "千克",
        "说明": name,
        "第1部分Q_CNG参考代码": part1,
        "第1部分Q_CNG参考名称": name,
    }


def _pipeline(records, templates=None):
    if templates is None:
        templates = [{
            "所属分类编码": "100101",
            "标准部分": 4,
            "属性": [{"属性名称": "牌号", "是否赋码项": True, "是否必填": True, "组合顺序": 1, "填写规则": "示例"}],
        }]
    selection = SelectionCatalog({
        "review_status": "approved",
        "large": {"10": {"名称": "黑色金属材料", "说明": "板", "选中": "金属", "不选中": "塑料"}},
        "middle": {"1001": {"名称": "板材", "说明": "板", "选中": "板", "不选中": "管"}},
        "leaf": {
            "100101": {"名称": "冷轧板材", "说明": "冷轧", "选中": "冷轧", "不选中": "热轧"},
            "100199": {"名称": "其他板材", "说明": "其他", "选中": "其他", "不选中": "具名"},
            "100104": {"名称": "军工专用板材", "说明": "军工", "选中": "军工", "不选中": "民用"},
        },
    }, records)
    services = ServiceCatalog([{
        "标准部分": 4,
        "物品和服务分类代码": "320811",
        "物品和服务分类名称": "冷轧薄板",
        "描述对象": "",
        "模板位置编码": "100101",
        "模板位置名称": "冷轧板材",
    }])
    return GroupPipeline(CategoryCatalog(records), selection, TemplateRepository(templates), services)
