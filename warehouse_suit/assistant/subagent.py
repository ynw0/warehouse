"""子助手。只读，不能再派生子助手。"""

from warehouse_suit.assistant.tools import execute_tool, tool_definitions, tool_names
from warehouse_suit.assistant.limits import CHILD_MAX_ROUNDS


AGENT_PROMPTS = {
    "lookup": "你是物料检索子助手。只根据工具结果用中文说明查到的物料、库存、项目或流程。不要编造。",
    "coding": "你是物料编码子助手。查询集团物料编码的所属分类编码和物品和服务分类代码时调用 get_group_material_codes，缺信息时列出 missing。仓库编码分类只以工具返回的规则为准，先查规则和已有编码，再给出建议。不要办理写操作。",
}


def run_subagent(agent, prompt, parent_ctx, complete_events):
    """跑完一个子助手，把文字结果交回父工具。"""
    if agent not in AGENT_PROMPTS:
        return _result(False, "子助手只能是 lookup 或 coding")
    task = str(prompt or "").strip()
    if not task:
        return _result(False, "子助手任务不能为空")
    from warehouse_suit.assistant.loop import run_loop

    child_ctx = dict(parent_ctx)
    child_ctx["depth"] = int(parent_ctx.get("depth") or 0) + 1
    child_ctx["allowed"] = tool_names(agent)

    def execute(name, arguments):
        return execute_tool(name, arguments, child_ctx)

    events = []
    outcome = None
    for event in run_loop(
        messages=[
            {"role": "system", "content": AGENT_PROMPTS[agent]},
            {"role": "user", "content": task},
        ],
        profile=parent_ctx["profile"],
        thinking_level=parent_ctx["thinking_level"],
        model=parent_ctx["model"],
        tool_defs=tool_definitions(agent),
        execute=execute,
        complete_events=complete_events,
        record=lambda *_args, **_kwargs: 0,
        deadline=parent_ctx["deadline"],
        max_rounds=CHILD_MAX_ROUNDS,
    ):
        if event.get("type") == "_result":
            outcome = event["result"]
            continue
        if event.get("type") == "tool":
            event = dict(event)
            event["name"] = f"{agent}/{event.get('name') or ''}"
        events.append(event)
    text = str((outcome or {}).get("content") or "").strip() or "子助手没有返回内容"
    return {
        "ok": True,
        "text": text,
        "confirmations": (outcome or {}).get("confirmations") or [],
        "events": events,
    }


def _result(ok, text):
    return {"ok": ok, "text": text, "confirmations": [], "events": []}
