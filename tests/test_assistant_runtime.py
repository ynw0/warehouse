# -*- coding: utf-8 -*-
"""物料小助手运行时：记忆隔离、确认单、压缩、思考参数和工具范围。"""


def _login(client, username, password):
    resp = client.post("/api/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["user"]


def _user(db, username):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = dict(cursor.fetchone())
    cursor.execute(
        """
        SELECT r.code
        FROM roles r
        JOIN user_roles ur ON ur.role_id = r.id
        WHERE ur.user_id = ?
        """,
        (user["id"],),
    )
    user["role_codes"] = [row["code"] for row in cursor.fetchall()]
    return user


def test_memory_is_isolated_by_user(client):
    _login(client, "admin", "admin")
    resp = client.put("/api/ai/assistant/memory", json={"body": "管理员记得蓝色货架"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["memory"] == "管理员记得蓝色货架"

    _login(client, "testuser", "test")
    resp = client.get("/api/ai/assistant/memory")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["memory"] == ""

    _login(client, "admin", "admin")
    resp = client.get("/api/ai/assistant/memory")
    assert resp.get_json()["memory"] == "管理员记得蓝色货架"


def test_delete_material_only_creates_confirmation(client, db):
    _login(client, "admin", "admin")
    resp = client.post(
        "/api/material-master",
        json={"material_code": "10200100018881", "name": "确认单物料", "unit": "个"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    material_id = resp.get_json()["material"]["id"]
    from warehouse_suit.assistant.tools import execute_tool

    result = execute_tool(
        "delete_material",
        {"material_id": material_id},
        {
            "conn": db,
            "user": _user(db, "admin"),
            "ip": "",
            "optional_active_user_id": None,
            "allowed": ["delete_material"],
            "depth": 0,
        },
    )
    assert result["ok"] is True
    assert result["confirmations"][0]["action"] == "delete_material"
    cursor = db.cursor()
    cursor.execute("SELECT COUNT(*) AS n FROM materials WHERE id = ?", (material_id,))
    assert int(cursor.fetchone()["n"]) == 1
    cursor.execute(
        "SELECT status FROM assistant_confirmations WHERE action = 'delete_material'"
    )
    assert cursor.fetchone()["status"] == "pending"


def test_compact_keeps_tool_pair_and_respects_limit():
    from warehouse_suit.assistant.compact import keep_tail, needs_compact

    rows = [
        {"role": "user", "id": 1, "content": "a"},
        {"role": "assistant", "id": 2, "content": "b"},
        {"role": "tool", "id": 3, "content": "c"},
        {"role": "user", "id": 4, "content": "d"},
        {"role": "assistant", "id": 5, "content": "e"},
        {"role": "tool", "id": 6, "content": "f"},
    ]
    older, kept = keep_tail(rows, 4)
    assert [row["id"] for row in older] == [1]
    assert kept[0]["role"] == "assistant"
    profile = {"context_tokens": 32768, "max_output_tokens": 4096}
    assert needs_compact(rows, profile) is False
    huge = [dict(row) for row in rows]
    huge[0]["content"] = "字" * 60000
    assert needs_compact(huge, profile) is True


def test_thinking_fields_follow_level_and_style():
    from warehouse_suit.assistant.profile import thinking_fields

    profile = {
        "capabilities": {"reasoning": True},
        "thinking_levels": ["off", "low", "medium", "high"],
        "thinking_style": "reasoning_effort",
    }
    assert thinking_fields(profile, "off") == {}
    assert thinking_fields(profile, "high") == {"reasoning_effort": "high"}
    enabled = dict(profile)
    enabled["thinking_style"] = "enable_thinking"
    assert thinking_fields(enabled, "high") == {"enable_thinking": True, "reasoning_effort": "high"}
    disabled = dict(profile)
    disabled["capabilities"] = {"reasoning": False}
    assert thinking_fields(disabled, "high") == {}


def test_child_tools_cannot_delete_or_spawn(db):
    from warehouse_suit.assistant.tools import execute_tool, tool_names

    assert "delete_material" not in tool_names("coding")
    assert "task" not in tool_names("coding")
    assert "delete_material" not in tool_names("lookup")
    assert "task" not in tool_names("lookup")
    assert "delete_material" in tool_names("parent")
    assert "task" in tool_names("parent")
    denied = execute_tool("delete_material", {}, {"allowed": tool_names("lookup"), "conn": db})
    assert denied["ok"] is False


def test_attachment_stays_with_its_owner(db, tmp_path, monkeypatch):
    monkeypatch.setattr("warehouse_suit.assistant.files.DATA_DIR", tmp_path)
    from warehouse_suit.assistant.files import _resolve_relative, load_files, save_upload

    saved = save_upload(db, 1, "note.txt", "只要这个用户能读".encode("utf-8"))
    assert str(saved["id"])
    loaded = load_files(db.cursor(), 1, [saved["id"]])
    assert loaded[0]["original_name"] == "note.txt"
    try:
        load_files(db.cursor(), 2, [saved["id"]])
    except ValueError as exc:
        assert "附件不存在" in str(exc)
    else:
        raise AssertionError("其他用户不该读到这份附件")
    try:
        _resolve_relative("../secret.txt")
    except ValueError as exc:
        assert "附件路径无效" in str(exc)
    else:
        raise AssertionError("附件路径不能跳出数据目录")


def test_chat_sends_thinking_only_when_enabled(db):
    from warehouse_suit.assistant.loop import iter_chat

    user = _user(db, "admin")
    calls = []

    def complete_events(payload):
        calls.append(payload)
        if len(calls) == 1:
            return [{
                "type": "_message",
                "message": {
                    "content": "",
                    "reasoning": "先看记忆",
                    "tool_calls": [{"id": "call_1", "name": "memory_get", "arguments": "{}"}],
                    "usage": {"input": 10, "output": 2, "reasoning": 1},
                },
            }]
        return [{
            "type": "_message",
            "message": {
                "content": "记得你喜欢蓝色货架",
                "reasoning": "",
                "tool_calls": [],
                "usage": {"input": 12, "output": 4, "reasoning": 0},
            },
        }]

    def quiet(payload):
        raise AssertionError("这轮不该再请求模型")

    events = list(iter_chat(
        db,
        user,
        question="我有什么偏好",
        session_id=None,
        file_ids=[],
        thinking_level="high",
        model="test-model",
        client_ip="",
        optional_active_user_id=None,
        complete_events=complete_events,
        complete_quiet=quiet,
    ))
    assert calls[0]["reasoning_effort"] == "high"
    assert any(event.get("type") == "text" and "蓝色货架" in event.get("text", "") for event in events)

    calls.clear()
    list(iter_chat(
        db,
        user,
        question="再问一次",
        session_id=None,
        file_ids=[],
        thinking_level="off",
        model="test-model",
        client_ip="",
        optional_active_user_id=None,
        complete_events=complete_events,
        complete_quiet=quiet,
    ))
    assert "reasoning_effort" not in calls[0]
    assert "enable_thinking" not in calls[0]


def test_custom_thinking_levels_send_configured_payload(client, db):
    from warehouse_suit.assistant.profile import load_profile, thinking_fields

    _login(client, "admin", "admin")
    response = client.post("/api/ai/config", json={"ai_model_profile": {
        "context_tokens": 32768,
        "max_output_tokens": 4096,
        "capabilities": {"reasoning": True, "tools": True, "vision": False},
        "thinking_options": [
            {"id": "off", "label": "关闭", "enabled": True, "fields": {"chat_template_kwargs": {"enable_thinking": False}}},
            {"id": "deep", "label": "深度", "enabled": True, "fields": {"reasoning_effort": "xhigh"}},
            {"id": "disabled", "label": "停用", "enabled": False, "fields": {}},
        ],
        "default_thinking": "deep",
        "request_parameters": {"temperature": 0.6, "top_p": 0.9},
    }})
    assert response.status_code == 200, response.get_data(as_text=True)
    profile = load_profile(db.cursor())
    assert profile["thinking_levels"] == ["off", "deep"]
    assert thinking_fields(profile, "off") == {"chat_template_kwargs": {"enable_thinking": False}}
    assert thinking_fields(profile, "deep") == {"reasoning_effort": "xhigh"}
    session = client.get("/api/ai/assistant/session").get_json()
    assert session["profile"]["thinking_options"][1]["label"] == "深度"

    from warehouse_suit.assistant.loop import iter_chat
    calls = []
    def complete(payload):
        calls.append(payload)
        return [{"type": "_message", "message": {"content": "收到", "tool_calls": [], "usage": {}}}]
    list(iter_chat(
        db, _user(db, "admin"), question="你好", session_id=None, file_ids=[],
        thinking_level="deep", model="test-model", client_ip="", optional_active_user_id=None,
        complete_events=complete, complete_quiet=lambda payload: "",
    ))
    assert calls[0]["temperature"] == 0.6
    assert calls[0]["top_p"] == 0.9
    assert calls[0]["reasoning_effort"] == "xhigh"


def test_detect_thinking_rejects_ignored_parameters(client, monkeypatch):
    from warehouse_suit.assistant import model_probe

    _login(client, "admin", "admin")
    monkeypatch.setattr(model_probe, "openai_model_list", lambda *args: [{"id": "test-model"}])
    monkeypatch.setattr(model_probe, "_chat", lambda *args: {"status": "accepted", "reasoning": False})
    response = client.post("/api/ai/model-detect", json={
        "ai_base_url": "http://model.test/v1", "model": "test-model",
    })
    assert response.status_code == 200, response.get_data(as_text=True)
    data = response.get_json()
    assert [item["id"] for item in data["thinking_options"]] == ["off"]
    assert "未提供可验证" in data["method"]


def test_detect_thinking_uses_service_validated_levels(client, monkeypatch):
    from warehouse_suit.assistant import model_probe

    _login(client, "admin", "admin")
    monkeypatch.setattr(model_probe, "openai_model_list", lambda *args: [{"id": "test-model"}])
    def probe(base_url, key, model, fields):
        effort = fields.get("reasoning_effort")
        return {"status": "accepted" if effort in {"low", "high"} else "rejected", "reasoning": False}
    monkeypatch.setattr(model_probe, "_chat", probe)
    response = client.post("/api/ai/model-detect", json={
        "ai_base_url": "http://model.test/v1", "model": "test-model",
    })
    assert response.status_code == 200
    assert [item["id"] for item in response.get_json()["thinking_options"]] == ["off", "low", "high"]


def test_claim_confirmation_uses_existing_code_and_office_project(client, db):
    import json
    from warehouse_suit.assistant.tools import execute_tool

    user = _login(client, "warehouse", "test")
    material = client.post("/api/material-master", json={
        "material_code": "10103901010060", "name": "电池", "brand_model": "南孚",
        "spec": "7号电池", "unit": "粒",
    })
    assert material.status_code == 200, material.get_data(as_text=True)
    material_id = material.get_json()["material"]["id"]
    inbound = client.post("/api/stock/in", json={
        "material_id": material_id, "quantity": 22, "total_amount": 22, "purchase_project": "办公",
    })
    assert inbound.status_code == 200, inbound.get_data(as_text=True)
    before = db.execute("SELECT COUNT(*) FROM workflow_forms").fetchone()[0]
    result = execute_tool("create_workflow", {
        "form_type": "claim", "project_code": "办公",
        "items": [{"material_code": "10103901010060", "material_name": "电池",
                   "brand_model": "南孚", "spec": "7号电池", "request_quantity": 2}],
    }, {
        "conn": db, "user": _user(db, "warehouse"), "ip": "",
        "optional_active_user_id": None, "allowed": ["create_workflow"], "depth": 0,
    })
    assert result["ok"] is True, result
    confirmation = result["confirmations"][0]
    assert confirmation["action"] == "create_workflow"
    assert "10103901010060" in confirmation["summary"]
    assert db.execute("SELECT COUNT(*) FROM workflow_forms").fetchone()[0] == before
    stored = db.execute("SELECT payload_json FROM assistant_confirmations WHERE id = ?", (confirmation["id"],)).fetchone()
    payload = json.loads(stored["payload_json"])
    assert payload["items"][0]["material_id"] == material_id
    assert payload["items"][0]["purchase_project"] == "办公"
    assert confirmation["preview"] == payload
    pending = client.get("/api/ai/assistant/confirmations").get_json()["items"]
    restored = next(item for item in pending if item["id"] == confirmation["id"])
    assert restored["preview"] == payload
    confirmed = client.post(f"/api/ai/assistant/confirmations/{confirmation['id']}/confirm", json={})
    assert confirmed.status_code == 200, confirmed.get_data(as_text=True)
    form = db.execute("SELECT id FROM workflow_forms WHERE form_type = 'claim' ORDER BY id DESC").fetchone()
    line = db.execute("SELECT material_code, request_quantity, purchase_project FROM workflow_items WHERE form_id = ?", (form["id"],)).fetchone()
    assert tuple(line) == ("10103901010060", 2, "办公")


def test_claim_confirmation_rejects_incorrect_material_code(client, db):
    from warehouse_suit.assistant.tools import execute_tool

    _login(client, "admin", "admin")
    result = execute_tool("create_workflow", {
        "form_type": "claim", "project_code": "办公",
        "items": [{"material_code": "10103901010080", "material_name": "电池", "request_quantity": 2}],
    }, {
        "conn": db, "user": _user(db, "admin"), "ip": "",
        "optional_active_user_id": None, "allowed": ["create_workflow"], "depth": 0,
    })
    assert result["ok"] is False
    assert "不存在" in result["text"]


def test_existing_material_code_is_not_rewritten_in_chat(db):
    from warehouse_suit.assistant.loop import iter_chat

    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO materials (material_code, name, unit, created_at, updated_at) VALUES (?, ?, ?, datetime('now'), datetime('now'))",
        ("10103901010060", "电池", "粒"),
    )
    db.commit()
    def complete(payload):
        return [{"type": "_message", "message": {
            "content": "7号电池编码为 10103901010060", "tool_calls": [], "usage": {},
        }}]
    def quiet(payload):
        raise AssertionError("现有物料编码不应被改写")
    events = list(iter_chat(
        db, _user(db, "admin"), question="查询7号电池", session_id=None, file_ids=[],
        thinking_level="off", model="test-model", client_ip="", optional_active_user_id=None,
        complete_events=complete, complete_quiet=quiet,
    ))
    assert any(event.get("text") == "7号电池编码为 10103901010060" for event in events)


def test_workflow_search_results_survive_session_restore():
    import json
    from warehouse_suit.assistant.session_store import present_messages

    rows = [
        {"id": 1, "role": "assistant", "content": "", "tool_calls": [{"name": "list_workflows"}]},
        {"id": 2, "role": "tool", "content": json.dumps({"data": {"items": [
            {"id": 42, "form_no": "YS2026110501", "status": "completed"},
        ]}}), "tool_calls": {"name": "list_workflows"}},
    ]
    shown = present_messages(rows)
    assert shown[0]["workflows"][0]["form_no"] == "YS2026110501"
