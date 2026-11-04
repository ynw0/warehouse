"""一轮对话：压缩上下文、调用工具，再把结果写回会话。"""

import json
import time

from warehouse_suit.ai_materials import validate_and_correct_codes
from warehouse_suit.assistant.compact import (
    keep_tail,
    needs_compact,
    summary_payload,
    usage_report,
)
from warehouse_suit.assistant.files import compose_user_content, file_view, load_files
from warehouse_suit.assistant.memory import get_memory
from warehouse_suit.assistant.profile import load_profile, resolve_thinking, thinking_fields
from warehouse_suit.assistant.session_store import (
    add_message,
    create_session,
    get_preference,
    get_session,
    list_messages,
    set_preference,
    set_summary,
    set_thinking,
    touch_title,
    update_message_content,
)
from warehouse_suit.assistant.tools import execute_tool, tool_definitions, tool_names


MAX_ROUNDS = 6
MAX_SECONDS = 120


def iter_chat(
    conn,
    user,
    *,
    question,
    session_id,
    file_ids,
    thinking_level,
    model,
    client_ip,
    optional_active_user_id,
    complete_events,
    complete_quiet,
):
    """生成浮窗需要的事件。调用方负责最后的 done。"""
    cursor = conn.cursor()
    profile = load_profile(cursor)
    preference = get_preference(cursor, user["id"])
    level = resolve_thinking(profile, thinking_level, preference)
    files = load_files(cursor, user["id"], file_ids)
    if session_id:
        session = get_session(cursor, session_id, user["id"])
        if not session:
            raise ValueError("会话不存在")
    else:
        session = create_session(conn, user["id"], level)
    if session["thinking_level"] != level:
        set_thinking(conn, session["id"], user["id"], level)
        session = get_session(cursor, session["id"], user["id"])
    if preference != level:
        set_preference(conn, user["id"], level)
    yield {"type": "session", "session_id": session["id"], "thinking_level": level}

    attachments = [file_view(row) for row in files]
    add_message(conn, session["id"], "user", question, attachments=attachments)
    touch_title(conn, session["id"], question)
    deadline = time.monotonic() + MAX_SECONDS
    messages = list_messages(cursor, session["id"])
    if needs_compact(messages, profile):
        if time.monotonic() >= deadline:
            raise TimeoutError("本轮对话已超过 120 秒")
        older, kept = keep_tail(messages)
        if older:
            conn.commit()
            summary_message = complete_quiet(summary_payload(model, session.get("summary") or "", older))
            summary = str(summary_message.get("content") or "").strip()
            if not summary:
                raise RuntimeError("上下文压缩没有返回摘要")
            set_summary(conn, session["id"], summary, [row["id"] for row in kept])
            session = get_session(cursor, session["id"], user["id"])
            messages = list_messages(cursor, session["id"])
            yield {"type": "compact", "summary": summary}

    prompt = system_prompt(profile, get_memory(cursor, user["id"]), session.get("summary") or "")
    api_messages = model_messages(cursor, user["id"], profile, prompt, messages)
    ctx = {
        "conn": conn,
        "user": user,
        "ip": client_ip,
        "optional_active_user_id": optional_active_user_id,
        "vision": bool(profile["capabilities"]["vision"]),
        "allowed": tool_names("parent"),
        "depth": 0,
        "profile": profile,
        "thinking_level": level,
        "model": model,
        "deadline": deadline,
    }

    def run_task(agent, task_prompt):
        from warehouse_suit.assistant.subagent import run_subagent

        return run_subagent(agent, task_prompt, ctx, complete_events)

    ctx["run_task"] = run_task

    def execute(name, arguments):
        return execute_tool(name, arguments, ctx)

    def record(role, content, reasoning, tool_meta, usage):
        if role == "assistant":
            return add_message(
                conn,
                session["id"],
                "assistant",
                content,
                reasoning=reasoning,
                tool_calls=tool_meta,
                usage=usage,
            )
        return add_message(conn, session["id"], "tool", content, tool_calls=tool_meta)

    conn.commit()
    result = None
    for event in run_loop(
        messages=api_messages,
        profile=profile,
        thinking_level=level,
        model=model,
        tool_defs=tool_definitions("parent") if profile["capabilities"]["tools"] else [],
        execute=execute,
        complete_events=complete_events,
        record=record,
        deadline=deadline,
        max_rounds=MAX_ROUNDS,
    ):
        if event.get("type") == "_result":
            result = event["result"]
            continue
        yield event
    if result is None:
        raise RuntimeError("模型没有返回内容")
    conn.commit()
    answer = correct_codes(
        cursor,
        result.get("content") or "",
        prompt,
        question,
        complete_quiet,
        model,
    )
    if result.get("message_id"):
        update_message_content(conn, result["message_id"], answer)
    if answer:
        yield {"type": "text", "text": answer}
    stored = list_messages(cursor, session["id"])
    report = usage_report(result.get("usage") or {}, profile, stored)
    yield {"type": "usage", **report}


def run_loop(*, messages, profile, thinking_level, model, tool_defs, execute, complete_events, record, deadline, max_rounds):
    """调用模型并执行工具。思考事件会直接向外抛出。"""
    confirmations = []
    usage = {}
    content = ""
    message_id = None
    for round_index in range(max_rounds):
        if time.monotonic() >= deadline:
            raise TimeoutError("本轮对话已超过 120 秒")
        payload = {
            "model": model,
            "temperature": 0.3,
            "max_tokens": int(profile["max_output_tokens"]),
            "messages": messages,
        }
        if tool_defs:
            payload["tools"] = tool_defs
        payload.update(thinking_fields(profile, thinking_level))
        message = None
        for event in complete_events(payload):
            if event.get("type") == "_message":
                message = event["message"]
            elif event.get("type") == "thinking" and event.get("text"):
                yield event
        if not message:
            raise RuntimeError("模型没有返回内容")
        usage = message.get("usage") or usage
        content = str(message.get("content") or "")
        reasoning = str(message.get("reasoning") or "")
        calls = [] if not tool_defs else _calls(message.get("tool_calls") or [], round_index)
        assistant_message = {"role": "assistant", "content": content}
        if calls:
            assistant_message["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": call["arguments"]},
                }
                for call in calls
            ]
        messages.append(assistant_message)
        message_id = record("assistant", content, reasoning, calls, usage)
        if not calls:
            yield _done(content, usage, message_id, confirmations)
            return
        for call in calls:
            yield {"type": "tool", "name": call["name"], "status": "start"}
            outcome = execute(call["name"], call["arguments"])
            for extra in outcome.get("events") or []:
                yield extra
            yield {"type": "tool", "name": call["name"], "status": "done", "ok": bool(outcome.get("ok", True))}
            for item in outcome.get("confirmations") or []:
                confirmations.append(item)
                yield {
                    "type": "confirmation",
                    "id": item["id"],
                    "action": item["action"],
                    "summary": item["summary"],
                }
            tool_text = str(outcome.get("text") or "")
            record("tool", tool_text, "", {"tool_call_id": call["id"], "name": call["name"]}, {})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": tool_text})
    if not content:
        content = "工具调用已达到上限，请换个问法或发送「继续」。"
    yield _done(content, usage, message_id, confirmations)


def system_prompt(profile, memory, summary):
    """本轮系统提示。分类规则改由工具按需读取。"""
    if profile["capabilities"]["tools"]:
        lines = [
            "你是仓库物料管理系统的物料小助手。",
            "用工具查询物料、库存、流程、项目和分类规则，不要凭空编造编码和库存。",
            "办理新建、修改、删除时调用对应工具。需要人工确认的工具不会立即执行，要告诉用户在对话里点确认执行。口头同意不算确认。",
            "一次只办理一个写操作。缺必填项时先问，不要猜。",
            "编物料编码时先用工具读取分类规则和已有编码。分类只以工具返回的规则为准。已有相同物料时列出已有编码，不要另编。",
        ]
    else:
        lines = [
            "你是仓库物料管理系统的物料小助手。",
            "当前模型未开启工具，不能查询仓库，也不能办理业务。只根据对话、附件和用户记忆回答，并说明无法核验实时数据。",
        ]
    lines.append("用户记忆：\n" + (str(memory or "").strip() or "无"))
    if str(summary or "").strip():
        lines.append("此前对话摘要：\n" + str(summary).strip())
    return "\n\n".join(lines)


def model_messages(cursor, user_id, profile, prompt, rows):
    """把已保存的会话收成上游消息。图片只附在最近一条用户消息上。"""
    latest_user_id = 0
    for row in rows:
        if row["role"] == "user":
            latest_user_id = row["id"]
    vision = bool(profile["capabilities"]["vision"])
    messages = [{"role": "system", "content": prompt}]
    for row in rows:
        if row["role"] == "user":
            messages.append(
                {
                    "role": "user",
                    "content": compose_user_content(
                        cursor,
                        user_id,
                        row.get("content") or "",
                        row.get("attachments") or [],
                        vision,
                        row["id"] == latest_user_id,
                    ),
                }
            )
        elif row["role"] == "assistant":
            message = {"role": "assistant", "content": row.get("content") or ""}
            calls = row.get("tool_calls") or []
            if isinstance(calls, list) and calls:
                message["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {"name": call["name"], "arguments": call.get("arguments") or "{}"},
                    }
                    for call in calls
                ]
            messages.append(message)
        elif row["role"] == "tool":
            meta = row.get("tool_calls") or {}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(meta.get("tool_call_id") or ""),
                    "content": row.get("content") or "",
                }
            )
    return messages


def correct_codes(cursor, answer, prompt, question, complete_quiet, model):
    """沿用编码校验。改正请求失败时保留带说明的原文。"""
    current = str(answer or "")
    for _round in range(2):
        validated = validate_and_correct_codes(cursor, current)
        if validated == current:
            return current
        marker = "\n\n---\n## 编码自动校验\n"
        parts = validated.split(marker)
        if len(parts) != 2:
            return validated
        try:
            corrected = complete_quiet(
                {
                    "model": model,
                    "temperature": 0.2,
                    "max_tokens": 2048,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": question},
                        {"role": "assistant", "content": parts[0].strip()},
                        {
                            "role": "user",
                            "content": f"上面的编码有问题：\n{parts[1].strip()}\n\n按问题改正，直接给出改后的完整回复。",
                        },
                    ],
                }
            )
        except Exception:
            return validated
        text = str((corrected or {}).get("content") or "").strip()
        if not text:
            return validated
        current = text
    return current


def _calls(calls, round_index):
    normalized = []
    for index, call in enumerate(calls):
        if not isinstance(call, dict):
            continue
        name = str(call.get("name") or "").strip()
        if not name:
            continue
        arguments = call.get("arguments")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments or {}, ensure_ascii=False)
        call_id = str(call.get("id") or "").strip() or f"call_{round_index}_{index}"
        normalized.append({"id": call_id, "name": name, "arguments": arguments or "{}"})
    return normalized


def _done(content, usage, message_id, confirmations):
    return {
        "type": "_result",
        "result": {
            "content": content,
            "usage": usage,
            "message_id": message_id,
            "confirmations": confirmations,
        },
    }
