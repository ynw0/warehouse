"""模型能力、上下文长度和思考等级。"""

import json

from warehouse_suit.settings import get_setting, set_setting


PROFILE_KEY = "ai_model_profile"
THINKING_LEVELS = ("off", "low", "medium", "high")
THINKING_STYLES = ("reasoning_effort", "enable_thinking")
LEVEL_LABELS = {"off": "关闭", "low": "低", "medium": "中", "high": "高"}

_DEFAULT = {
    "context_tokens": 32768,
    "max_output_tokens": 4096,
    "capabilities": {"tools": True, "vision": False, "reasoning": True},
    "thinking_levels": ["off", "low", "medium", "high"],
    "default_thinking": "medium",
    "thinking_style": "reasoning_effort",
}


def load_profile(cursor):
    """读取管理员保存的模型档案。缺项用默认值。"""
    stored = {}
    raw = get_setting(cursor, PROFILE_KEY, "")
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = {}
        if isinstance(parsed, dict):
            stored = parsed
    return normalize_profile(_merge(_DEFAULT, stored), strict=False)


def save_profile(cursor, incoming):
    """校验并保存模型档案。"""
    if not isinstance(incoming, dict):
        raise ValueError("模型档案格式不正确")
    merged = _merge(load_profile(cursor), incoming)
    profile = normalize_profile(merged, strict=True)
    set_setting(cursor, PROFILE_KEY, json.dumps(profile, ensure_ascii=False))
    return profile


def normalize_profile(data, strict=True):
    """整理档案。strict 为真时，不合法的值直接报错。"""
    source = data if isinstance(data, dict) else {}
    try:
        context_tokens = int(source.get("context_tokens") or _DEFAULT["context_tokens"])
        max_output_tokens = int(source.get("max_output_tokens") or _DEFAULT["max_output_tokens"])
    except (TypeError, ValueError):
        if strict:
            raise ValueError("上下文长度和最大输出必须是整数")
        context_tokens = _DEFAULT["context_tokens"]
        max_output_tokens = _DEFAULT["max_output_tokens"]
    if context_tokens < 1024 or context_tokens > 2000000:
        if strict:
            raise ValueError("上下文长度须在 1024 到 2000000 之间")
        context_tokens = _DEFAULT["context_tokens"]
    if max_output_tokens < 256 or max_output_tokens > 128000:
        if strict:
            raise ValueError("最大输出须在 256 到 128000 之间")
        max_output_tokens = _DEFAULT["max_output_tokens"]
    if max_output_tokens >= context_tokens:
        if strict:
            raise ValueError("最大输出必须小于上下文长度")
        max_output_tokens = min(_DEFAULT["max_output_tokens"], context_tokens - 1)

    capabilities = source.get("capabilities") if isinstance(source.get("capabilities"), dict) else {}
    caps = {
        "tools": bool(capabilities.get("tools", _DEFAULT["capabilities"]["tools"])),
        "vision": bool(capabilities.get("vision", _DEFAULT["capabilities"]["vision"])),
        "reasoning": bool(capabilities.get("reasoning", _DEFAULT["capabilities"]["reasoning"])),
    }
    levels = []
    raw_levels = source.get("thinking_levels")
    if not isinstance(raw_levels, list) or not raw_levels:
        raw_levels = list(_DEFAULT["thinking_levels"])
    for item in raw_levels:
        level = str(item or "").strip()
        if level in THINKING_LEVELS and level not in levels:
            levels.append(level)
    if not levels:
        if strict:
            raise ValueError("至少选择一个思考等级")
        levels = ["off"]
    default_thinking = str(source.get("default_thinking") or "").strip()
    if default_thinking not in levels:
        if strict and source.get("default_thinking"):
            raise ValueError("默认思考等级必须是已启用的等级")
        default_thinking = "medium" if "medium" in levels else levels[0]
    style = str(source.get("thinking_style") or _DEFAULT["thinking_style"]).strip()
    if style not in THINKING_STYLES:
        if strict:
            raise ValueError("思考参数样式不正确")
        style = _DEFAULT["thinking_style"]
    return {
        "context_tokens": context_tokens,
        "max_output_tokens": max_output_tokens,
        "capabilities": caps,
        "thinking_levels": levels,
        "default_thinking": default_thinking,
        "thinking_style": style,
    }


def resolve_thinking(profile, requested, preference=""):
    """选出本次实际使用的思考等级。未开启思考时固定为关闭。"""
    if not profile["capabilities"]["reasoning"]:
        return "off"
    levels = profile["thinking_levels"]
    for candidate in (requested, preference, profile["default_thinking"], "off"):
        text = str(candidate or "").strip()
        if text in levels:
            return text
    return levels[0]


def thinking_fields(profile, level):
    """把思考等级写成上游请求字段。关闭时不发送。"""
    if not profile["capabilities"]["reasoning"]:
        return {}
    text = str(level or "").strip()
    if text not in profile["thinking_levels"] or text == "off":
        return {}
    if profile["thinking_style"] == "enable_thinking":
        return {"enable_thinking": True, "reasoning_effort": text}
    return {"reasoning_effort": text}


def _merge(base, incoming):
    merged = {
        "context_tokens": base.get("context_tokens"),
        "max_output_tokens": base.get("max_output_tokens"),
        "capabilities": dict(base.get("capabilities") or {}),
        "thinking_levels": list(base.get("thinking_levels") or []),
        "default_thinking": base.get("default_thinking"),
        "thinking_style": base.get("thinking_style"),
    }
    if not isinstance(incoming, dict):
        return merged
    for key in ("context_tokens", "max_output_tokens", "default_thinking", "thinking_style"):
        if key in incoming and incoming[key] not in (None, ""):
            merged[key] = incoming[key]
    if isinstance(incoming.get("capabilities"), dict):
        merged["capabilities"].update(incoming["capabilities"])
    if isinstance(incoming.get("thinking_levels"), list):
        merged["thinking_levels"] = incoming["thinking_levels"]
    return merged
