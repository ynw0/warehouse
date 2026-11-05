"""探测 OpenAI 兼容服务声明或实际体现的思考模式。"""

from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError

from warehouse_suit.ai_client import openai_chat_completion, openai_model_list


_LABELS = {
    "none": "关闭", "minimal": "极低", "low": "低", "medium": "中",
    "high": "高", "xhigh": "很高", "max": "最高", "ultra": "极致",
}
_EFFORTS = tuple(_LABELS)


def _chat(base_url, api_key, model, fields):
    request = {
        "model": model, "messages": [{"role": "user", "content": "计算 12 加 19，回答结果。"}],
        "max_tokens": 128, "stream": False,
    }
    request.update(fields)
    try:
        response = openai_chat_completion(base_url, api_key, request, timeout=15)
        choices = response.get("choices") or []
        if not choices:
            return {"status": "failed"}
        message = choices[0].get("message") or {}
        return {
            "status": "accepted",
            "reasoning": bool(message.get("reasoning_content") or message.get("reasoning")),
        }
    except HTTPError as exc:
        if 400 <= exc.code < 500:
            return {"status": "rejected"}
        return {"status": "failed"}
    except Exception:
        return {"status": "failed"}


def detect_thinking_options(base_url, api_key, model, request_parameters=None):
    """只使用服务声明的挡位，或通过拒绝无效值验证参数校验后的挡位。"""
    common = request_parameters or {}
    if not isinstance(common, dict) or set(common) & {"model", "messages", "tools", "stream", "max_tokens"}:
        raise ValueError("模型通用参数格式不正确")
    models = openai_model_list(base_url, api_key)
    selected = next((entry for entry in models if entry["id"] == model), None)
    if not selected:
        raise ValueError("模型不在服务返回的列表中")
    declared = selected.get("supported_reasoning_efforts") or selected.get("reasoning_efforts")
    verified = []
    method = ""
    if declared:
        verified = [
            value for value in declared
            if value and len(value) <= 40 and all(ch.isalnum() or ch in "_-" for ch in value)
        ]
        verified = sorted(set(verified), key=lambda value: (
            _EFFORTS.index(value) if value in _EFFORTS else len(_EFFORTS), value,
        ))
        method = "模型服务声明"
    else:
        candidates = [*_EFFORTS, "__invalid_effort_probe__"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(
                lambda value: _chat(base_url, api_key, model, {**common, "reasoning_effort": value}),
                candidates,
            ))
        # 有些兼容服务忽略未知参数，必须确认它会拒绝无效值。
        if replies[-1]["status"] == "rejected":
            verified = [value for value, result in zip(_EFFORTS, replies) if result["status"] == "accepted"]
            method = "请求参数校验"

    options = [{"id": "off", "label": "服务默认", "enabled": True, "fields": {}}]
    if verified:
        options.extend({
            "id": value, "label": _LABELS.get(value, value), "enabled": True,
            "fields": {"reasoning_effort": value},
        } for value in verified)
    else:
        # 只有开关请求的思考内容出现明显差异，才认定为可观察到的两档。
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(
                lambda enabled: _chat(base_url, api_key, model, {
                    **common,
                    "chat_template_kwargs": {**common.get("chat_template_kwargs", {}), "enable_thinking": enabled},
                }),
                (False, True),
            ))
        if all(reply["status"] == "accepted" for reply in replies) and not replies[0]["reasoning"] and replies[1]["reasoning"]:
            options = [
                {"id": "off", "label": "关闭", "enabled": True, "fields": {"chat_template_kwargs": {"enable_thinking": False}}},
                {"id": "on", "label": "开启", "enabled": True, "fields": {"chat_template_kwargs": {"enable_thinking": True}}},
            ]
            method = "思考开关响应"
    if len(options) == 1:
        method = "服务未提供可验证的思考挡位"
    return {"model": model, "thinking_options": options, "method": method,
            "default_thinking": "medium" if any(item["id"] == "medium" for item in options) else options[-1]["id"]}
