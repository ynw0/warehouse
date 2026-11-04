"""按上下文用量决定是否把更早的对话压成摘要。"""


RESERVE_TOKENS = 2000
KEEP_MESSAGES = 4


def compact_limit(profile):
    """超过该用量就压缩。给输出和固定余量留位置。"""
    window = int(profile["context_tokens"])
    output = int(profile["max_output_tokens"])
    return max(1, window - output - RESERVE_TOKENS)


def estimate_tokens(text):
    """没有上游用量时按字符粗算。中英文混排大约两字一个 token。"""
    return max(0, len(str(text or "")) // 2)


def context_used(messages):
    """返回 (用量, 是否估算)。优先用最近一条助手消息的真实用量。"""
    last_index = None
    for index in range(len(messages) - 1, -1, -1):
        usage = messages[index].get("usage") or {}
        if messages[index].get("role") == "assistant" and _usage_total(usage) > 0:
            last_index = index
            break
    if last_index is None:
        return _estimate_messages(messages), True
    used = _usage_total(messages[last_index].get("usage") or {})
    used += _estimate_messages(messages[last_index + 1 :])
    return used, False


def needs_compact(messages, profile):
    if len(messages) <= KEEP_MESSAGES:
        return False
    used, _estimated = context_used(messages)
    return used > compact_limit(profile)


def keep_tail(messages, count=KEEP_MESSAGES):
    """保留最近若干条，并避免把工具结果从对应的助手消息上拆开。"""
    if len(messages) <= count:
        return [], list(messages)
    start = len(messages) - count
    while start > 0 and messages[start].get("role") == "tool":
        start -= 1
    return list(messages[:start]), list(messages[start:])


def transcript(messages):
    """压摘要用的纯文本。单条和总长都有上限，避免摘要请求本身过长。"""
    lines = []
    total = 0
    for row in messages:
        role = {"user": "用户", "assistant": "助手", "tool": "工具"}.get(row.get("role"), "其他")
        content = str(row.get("content") or "").strip()
        if row.get("role") == "tool":
            name = ""
            meta = row.get("tool_calls") or {}
            if isinstance(meta, dict):
                name = str(meta.get("name") or "")
            content = f"{name} {content}".strip()
        content = content[:800]
        if not content:
            continue
        line = f"{role}：{content}"
        if total + len(line) > 12000:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def usage_report(usage, profile, messages):
    """浮窗用量条。没有真实用量时标明估算。"""
    total = _usage_total(usage or {})
    estimated = total <= 0
    if estimated:
        total = max(1, _estimate_messages(messages)) if messages else 0
    window = int(profile["context_tokens"])
    percent = min(100, int(round(total * 100 / window))) if window else 0
    return {
        "used": int(total),
        "window": window,
        "percent": percent,
        "estimated": estimated,
        "input": int((usage or {}).get("input") or 0),
        "output": int((usage or {}).get("output") or 0),
        "reasoning": int((usage or {}).get("reasoning") or 0),
    }


def summary_payload(model, prior_summary, older_messages):
    """压缩请求。不带工具，也不带思考参数。"""
    prior = str(prior_summary or "").strip()
    body = transcript(older_messages)
    if prior:
        body = f"已有摘要：\n{prior}\n\n新增对话：\n{body}"
    return {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 1024,
        "messages": [
            {
                "role": "system",
                "content": "你负责压缩仓库物料小助手的对话。只输出中文摘要，写明已确认事实、未完成事项和用户偏好。不要编造，不要调用工具。",
            },
            {"role": "user", "content": body or "没有更早的对话。"},
        ],
    }


def _usage_total(usage):
    return int(usage.get("input") or 0) + int(usage.get("output") or 0) + int(usage.get("reasoning") or 0)


def _estimate_messages(messages):
    chars = 0
    for row in messages:
        chars += len(str(row.get("content") or ""))
        chars += len(str(row.get("reasoning") or ""))
    return chars // 2
