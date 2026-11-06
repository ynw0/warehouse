"""集团分类工具的只读查询和模型连接回归。"""

import json
import time

from warehouse_suit.assistant.tools import execute_tool


def _context(db):
    return {
        "conn": db, "user": {"id": 1}, "allowed": ["get_group_material_codes"],
        "model": "current-model", "deadline": time.monotonic() + 120,
    }


def test_group_codes_return_saved_pair_without_model_or_write(db):
    db.execute(
        """INSERT INTO materials
           (material_code,name,unit,group_category_code,group_service_code,group_standard_description)
           VALUES ('10101701010010','党旗','个','123456','654321','测试描述')"""
    )
    db.commit()
    before = db.total_changes
    outcome = execute_tool("get_group_material_codes", {"material_code": "10101701010010"}, _context(db))
    assert outcome["ok"], outcome
    result = json.loads(outcome["text"])
    assert result["source"] == "material"
    assert result["material"]["name"] == "党旗"
    assert result["group_category_code"] == "123456"
    assert result["group_service_code"] == "654321"
    assert result["standard_description"] == "测试描述"
    assert db.total_changes == before


def test_group_codes_use_existing_pipeline_and_current_model_without_saving(db, monkeypatch):
    db.execute("INSERT INTO materials (material_code,name,spec,unit) VALUES ('10101701010010','党旗','布制','个')")
    db.commit()
    before = db.total_changes
    ctx = _context(db)
    calls = []
    def complete(payload):
        calls.append(payload)
        return {"content": '{"matched":true}'}
    ctx["complete_quiet"] = complete

    def classify(query, complete):
        material = json.loads(query)
        assert material["名称"] == "党旗"
        assert material["技术规格"] == "布制"
        assert material["补充信息"] == "尺寸待确认"
        assert complete("系统提示", "候选信息") == {"matched": True}
        return {"status": "information_missing", "group_category_code": "123456",
                "group_service_code": "", "missing": ["尺寸"], "message": "请补充尺寸"}

    monkeypatch.setattr("warehouse_suit.assistant.group_codes.classify_group_material", classify)
    outcome = execute_tool("get_group_material_codes", {
        "material_code": "10101701010010", "q": "尺寸待确认",
    }, ctx)
    assert outcome["ok"], outcome
    result = json.loads(outcome["text"])
    assert result["status"] == "information_missing"
    assert result["missing"] == ["尺寸"]
    assert calls[0]["model"] == "current-model"
    assert calls[0]["messages"][1]["content"] == "候选信息"
    assert db.total_changes == before
    assert db.execute("SELECT group_category_code FROM materials").fetchone()[0] == ""


def test_group_codes_accept_description_and_reject_missing_material(db, monkeypatch):
    queries = []
    def classify(query, complete):
        queries.append(query)
        return {"status": "category_no_match", "group_category_code": "", "group_service_code": ""}
    monkeypatch.setattr("warehouse_suit.assistant.group_codes.classify_group_material", classify)
    ctx = _context(db)
    outcome = execute_tool("get_group_material_codes", {"q": "党旗，布制，单位个"}, ctx)
    assert outcome["ok"]
    assert queries == ["党旗，布制，单位个"]
    assert not execute_tool("get_group_material_codes", {}, ctx)["ok"]
    assert not execute_tool("get_group_material_codes", {"material_code": "10101701010010"}, ctx)["ok"]
    assert len(queries) == 1
