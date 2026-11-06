"""小助手可调用的查询、办理和子助手工具。"""

import json
import logging
import time

from warehouse_suit.ai_materials import (
    ai_material_by_code,
    ai_ranked_prefixes,
    ai_similar_materials,
    ai_similar_production_items,
)
from warehouse_suit.assistant.files import read_attachment_text
from warehouse_suit.assistant.memory import get_memory, save_memory
from warehouse_suit.assistant_actions import REGISTRY, perform_action
from warehouse_suit.category_rules import rules_text
from warehouse_suit.material_service import next_material_code
from warehouse_suit.validation import validate_material_code_value
from warehouse_suit.todo_service import pending_tasks_for_user


LOGGER = logging.getLogger("warehouse_suit.assistant")
TOOL_TEXT_LIMIT = 24000

READ_NAMES = (
    "list_my_todos",
    "remember_note",
    "list_notes",
    "find_acceptance_items",
    "find_people",
    "complete_note",
    "search_materials",
    "get_material",
    "search_production",
    "search_prefixes",
    "next_material_code",
    "get_category_rules",
    "read_attachment",
    "memory_get",
    "memory_save",
)
LOOKUP_NAMES = (
    "list_my_todos",
    "search_materials",
    "get_material",
    "search_production",
    "list_projects",
    "list_workflows",
    "get_workflow",
    "read_attachment",
)
CODING_NAMES = (
    "get_category_rules",
    "search_prefixes",
    "next_material_code",
    "read_attachment",
)


def tool_names(kind):
    """某一类助手允许使用的工具名。"""
    if kind == "lookup":
        return list(LOOKUP_NAMES)
    if kind == "coding":
        return list(CODING_NAMES)
    names = []
    for name in list(READ_NAMES) + ["task"] + [spec["name"] for spec in REGISTRY]:
        if name not in names:
            names.append(name)
    return names


def tool_definitions(kind):
    """转成 OpenAI 兼容的工具定义。"""
    by_name = {}
    for item in _read_definitions() + _registry_definitions() + [_task_definition()]:
        by_name[item["function"]["name"]] = item
    return [by_name[name] for name in tool_names(kind) if name in by_name]


def execute_tool(name, arguments, ctx):
    """执行一个工具。失败时把原因交回模型，不抛出中断整轮对话。"""
    started = time.monotonic()
    allowed = set(ctx.get("allowed") or [])
    if name not in allowed:
        _log(name, started, "denied")
        return _fail("当前助手不能使用该工具")
    payload = _arguments(arguments)
    try:
        if name == "task":
            if ctx.get("depth"):
                result = _fail("子助手不能再派生子助手")
            else:
                result = ctx["run_task"](str(payload.get("agent") or ""), str(payload.get("prompt") or ""))
        elif name in READ_NAMES:
            result = _read(name, payload, ctx)
        else:
            result = _action(name, payload, ctx)
    except Exception as exc:
        _log(name, started, type(exc).__name__)
        return _fail(str(exc) or "工具执行失败")
    _log(name, started, "ok" if result.get("ok", True) else "error")
    result["text"] = _clip(result.get("text") or "")
    return result


def _read(name, payload, ctx):
    cursor = ctx["conn"].cursor()
    user = ctx["user"]
    if name in {"remember_note", "list_notes", "find_acceptance_items", "find_people", "complete_note"}:
        from warehouse_suit.assistant.notes import save_note, list_notes, find_acceptance_items, find_people, complete_note
        if name == "remember_note":
            try:
                return _ok(_dump(save_note(ctx["conn"], user, payload, ctx.get("message_id"))))
            except Exception:
                ctx["conn"].rollback()
                raise
        if name == "list_notes":
            return _ok(_dump(list_notes(cursor, user)))
        if name == "find_acceptance_items":
            return _ok(_dump(find_acceptance_items(cursor, user, payload.get("q"))))
        if name == "find_people":
            return _ok(_dump(find_people(cursor, payload.get("q"))))
        try:
            complete_note(ctx["conn"], user, payload.get("note_id"))
        except Exception:
            ctx["conn"].rollback()
            raise
        return _ok("事项已完成")
    if name == "list_my_todos":
        # “我的”始终按当前账号过滤，管理员也只看分配给自己的事项。
        try:
            offset = int(payload.get("offset") or 0)
        except (TypeError, ValueError):
            raise ValueError("待办起始位置必须是非负整数")
        if offset < 0:
            raise ValueError("待办起始位置必须是非负整数")
        size = _limit(payload.get("limit"), 50, 100)
        rows = pending_tasks_for_user(cursor, user, size + 1, own_only=True, offset=offset)
        items = rows[:size]
        return _ok(_dump({"items": items, "offset": offset, "has_more": len(rows) > size}))
    if name == "search_materials":
        rows = ai_similar_materials(cursor, str(payload.get("q") or ""), _limit(payload.get("limit"), 12, 20))
        return _ok(_dump([_material(item) for item in rows]))
    if name == "get_material":
        code = validate_material_code_value(payload.get("material_code"))
        item = ai_material_by_code(cursor, code)
        if not item:
            return _fail("物料不存在")
        return _ok(_dump(_material(item)))
    if name == "search_production":
        rows = ai_similar_production_items(cursor, str(payload.get("q") or ""), _limit(payload.get("limit"), 12, 20))
        return _ok(_dump([_production(item) for item in rows]))
    if name == "search_prefixes":
        rows = ai_ranked_prefixes(cursor, str(payload.get("q") or ""))
        size = _limit(payload.get("limit"), 12, 24)
        return _ok(_dump([_prefix(item) for item in rows[:size]]))
    if name == "next_material_code":
        warehouse = str(payload.get("warehouse_code") or "").strip()
        if warehouse not in {"10", "20"}:
            return _fail("仓库码只能是 10 或 20")
        code, step = next_material_code(
            cursor,
            warehouse,
            payload.get("major_code") or "",
            payload.get("middle_code") or "",
            payload.get("small_code") or "",
            payload.get("name") or "",
            payload.get("brand_model") or "",
            payload.get("spec") or "",
        )
        return _ok(_dump({"material_code": code, "detail_code": code[-4:], "step": step}))
    if name == "get_category_rules":
        return _ok(rules_text(cursor))
    if name == "read_attachment":
        return _ok(read_attachment_text(cursor, user["id"], payload.get("file_id"), bool(ctx.get("vision"))))
    if name == "memory_get":
        return _ok(get_memory(cursor, user["id"]) or "没有记忆")
    if name == "memory_save":
        saved = save_memory(ctx["conn"], user["id"], payload.get("body") or "")
        return _ok("已保存记忆" if saved else "已清空记忆")
    return _fail("不支持该工具")


def _action(name, payload, ctx):
    if isinstance(payload.get("payload"), dict) and len(payload) == 1:
        payload = payload["payload"]
    result = perform_action(
        ctx["conn"],
        ctx["user"],
        name,
        payload,
        ctx.get("ip") or "",
        confirmed=False,
        optional_active_user_id=ctx.get("optional_active_user_id"),
    )
    confirmations = []
    if result.get("needs_confirmation"):
        confirmations.append(
            {
                "id": result["confirmation_id"],
                "action": result["action"],
                "summary": result["summary"],
                "preview": result.get("preview"),
            }
        )
    events = []
    if name == "list_workflows":
        events.append({"type": "workflows", "items": result.get("data", {}).get("items", [])})
    elif name == "get_workflow":
        events.append({"type": "workflows", "items": [result.get("data", {})]})
    return {"ok": True, "text": _dump(result), "confirmations": confirmations, "events": events}


def _read_definitions():
    return [
        _function("remember_note", "保存用户要求记住的事项。提前取用物料需 kind=material_wait、数量和验收明细 ID，缺失时先保存并询问，补充时使用原 note_id 更新。只有用户明确要求通知对方时才填写 recipient_user_id。", {
            "content": _string("用户要求记住的完整事项"),
            "note_id": _integer("补充已有待完善事项时提供事项 ID"),
            "material_code": _string("已确认的物料编码"),
            "kind": _string("note 或 material_wait"),
            "quantity": {"type": "number", "description": "提前取用数量"},
            "acceptance_item_id": _integer("经用户确认的验收明细 ID"),
            "recipient_user_id": _integer("明确要求通知的接收人 ID"),
        }),
        _function("list_notes", "查询当前用户保存的事项和提醒。", {}),
        _function("find_acceptance_items", "按物料名称、编码或验收单号查找当前用户可查看的验收明细。", {"q": _string("物料名称、编码或验收单号")}, ["q"]),
        _function("find_people", "按姓名查找需要通知的在职账号。", {"q": _string("姓名")}, ["q"]),
        _function("complete_note", "将当前用户的事项标记为完成。", {"note_id": _integer("事项 ID")}, ["note_id"]),
        _function(
            "list_my_todos",
            "查询当前登录账号需要办理的待办流程及盘点。包括管理员本人；结果含待办环节和发起人。has_more 为真时用 offset 查询下一页。",
            {"limit": _integer("每页条数，最多 100"), "offset": _integer("起始位置，从 0 开始")},
        ),
        _function(
            "search_materials",
            "按名称、编码、品牌或规格搜索物料和库存位置。",
            {"q": _string("关键词"), "limit": _integer("条数，最多 20")},
        ),
        _function(
            "get_material",
            "按完整物料编码取一条物料。",
            {"material_code": _string("14 位物料编码")},
            ["material_code"],
        ),
        _function(
            "search_production",
            "搜索半成品和成品库存。",
            {"q": _string("关键词"), "limit": _integer("条数，最多 20")},
        ),
        _function(
            "search_prefixes",
            "查看已使用的物料编码前缀。",
            {"q": _string("关键词"), "limit": _integer("条数，最多 24")},
        ),
        _function(
            "next_material_code",
            "按仓库和大中小类取下一个可用物料编码。仓库码只能是 10 或 20。",
            {
                "warehouse_code": _string("仓库码"),
                "major_code": _string("大类"),
                "middle_code": _string("中类"),
                "small_code": _string("小类"),
                "name": _string("物料名称"),
                "brand_model": _string("品牌型号"),
                "spec": _string("规格"),
            },
            ["warehouse_code", "major_code", "middle_code", "small_code"],
        ),
        _function("get_category_rules", "读取当前仓库的分类规则。编码分类只以这个结果为准。", {}),
        _function(
            "read_attachment",
            "读取用户在本轮对话上传的附件文本。",
            {"file_id": _integer("附件编号")},
            ["file_id"],
        ),
        _function("memory_get", "读取当前用户的长期记忆。", {}),
        _function(
            "memory_save",
            "覆盖保存当前用户的长期记忆，最多 4000 字。",
            {"body": _string("记忆正文")},
            ["body"],
        ),
    ]


def _registry_definitions():
    tools = []
    for spec in REGISTRY:
        note = "执行前需要用户在页面上确认。" if spec["confirm"] else "可直接执行。"
        properties = {}
        required = None
        if spec["name"] == "create_workflow":
            properties = {
                "form_type": _string("流程类型，例如 acceptance、claim、borrow、purchase_request"),
                "items": {
                    "type": "array",
                    "description": "流程明细。申领时每行必须包含查询到的 material_code、request_quantity 和 purchase_project。办公项目的 purchase_project 填办公",
                    "items": {"type": "object", "properties": {
                        "material_code": _string("物料查询返回的准确编码"),
                        "request_quantity": {"type": "number", "description": "申请数量"},
                        "purchase_project": _string("采购申请项目，办公物料填办公"),
                        "material_name": _string("物料名称"),
                        "brand_model": _string("品牌型号"),
                        "spec": _string("规格"),
                    }, "additionalProperties": True},
                },
                "purchase_project": _string("明确适用于所有明细的采购申请项目"),
                "leader_id": _integer("审批人账号 ID，按实际权限和流程要求提供"),
                "validator_ids": {"type": "array", "items": {"type": "integer"}, "description": "验收人账号 ID"},
                "has_purchase_order": {"type": "boolean", "description": "验收单是否有采购订单"},
            }
            required = ["form_type"]
        elif spec["name"] == "get_workflow":
            properties = {"form_id": _integer("流程 ID"), "offset": _integer("明细起始位置")}
            required = ["form_id"]
        elif spec["name"] == "list_workflows":
            properties = {"q": _string("流程单号或标题"), "form_type": _string("流程类型")}
        tools.append(
            _function(
                spec["name"],
                f"{spec['title']}。{note}参数：{spec['params']}",
                properties,
                required,
                allow_extra=True,
            )
        )
    return tools


def _task_definition():
    return _function(
        "task",
        "把一段只读工作派给子助手。lookup 负责查物料、库存、项目和流程。coding 负责查分类规则、前缀和下一个编码。子助手不能办理写操作。",
        {
            "agent": {"type": "string", "enum": ["lookup", "coding"], "description": "子助手类型"},
            "prompt": _string("交给子助手的任务"),
        },
        ["agent", "prompt"],
    )


def _function(name, description, properties, required=None, allow_extra=False):
    schema = {
        "type": "object",
        "properties": properties,
        "additionalProperties": bool(allow_extra),
    }
    if required:
        schema["required"] = required
    return {"type": "function", "function": {"name": name, "description": description, "parameters": schema}}


def _string(description):
    return {"type": "string", "description": description}


def _integer(description):
    return {"type": "integer", "description": description}


def _arguments(value):
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            raise ValueError("工具参数不是合法的 JSON")
        if not isinstance(parsed, dict):
            raise ValueError("工具参数必须是对象")
        return parsed
    raise ValueError("工具参数必须是对象")


def _limit(value, default, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(1, min(maximum, number))


def _dump(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def _clip(text):
    if len(text) <= TOOL_TEXT_LIMIT:
        return text
    return text[:TOOL_TEXT_LIMIT] + "\n…已截断"


def _ok(text):
    return {"ok": True, "text": text, "confirmations": []}


def _fail(text):
    return {"ok": False, "text": str(text), "confirmations": []}


def _log(name, started, status):
    elapsed = (time.monotonic() - started) * 1000
    LOGGER.info("assistant tool %s %.0fms %s", name, elapsed, status)


def _material(item):
    return {
        "material_code": item.get("material_code") or "",
        "name": item.get("name") or "",
        "brand_model": item.get("brand_model") or "",
        "spec": item.get("spec") or "",
        "unit": item.get("unit") or "",
        "quantity": item.get("quantity") or 0,
        "shelf_name": item.get("shelf_name") or "",
        "layer_number": item.get("layer_number") or "",
        "zone_name": item.get("zone_name") or "",
    }


def _production(item):
    return {
        "item_type": item.get("item_type") or "",
        "item_code": item.get("item_code") or "",
        "item_name": item.get("item_name") or "",
        "spec": item.get("spec") or "",
        "quantity": item.get("quantity") or 0,
        "available_quantity": item.get("available_quantity") or 0,
    }


def _prefix(item):
    return {
        "prefix": item.get("prefix") or "",
        "warehouse_code": item.get("warehouse") or "",
        "major_code": item.get("major") or "",
        "middle_code": item.get("middle") or "",
        "small_code": item.get("small") or "",
        "count": item.get("count") or 0,
        "max_detail": item.get("max_detail") or 0,
        "names": (item.get("names") or [])[:5],
        "examples": (item.get("full_examples") or [])[:3],
    }
