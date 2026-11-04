"""OpenAI 兼容接口。连接时先走 IPv4，避免不响应的 IPv6 占满超时。"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from http.client import HTTPConnection, HTTPSConnection


def ai_base_url(base_url):
    url = str(base_url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if url.endswith(suffix):
            return url[: -len(suffix)].rstrip("/")
    return url


def ai_chat_completions_url(base_url):
    return ai_base_url(base_url).rstrip("/") + "/chat/completions"


def normalize_openai_chat_payload(payload):
    normalized = dict(payload or {})
    messages = normalized.get("messages") or []
    system_parts = []
    ordered = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content") or ""
        if role == "system":
            if content:
                system_parts.append(str(content))
        else:
            ordered.append(message)
    if system_parts:
        normalized["messages"] = [{"role": "system", "content": "\n\n".join(system_parts)}] + ordered
    else:
        normalized["messages"] = ordered
    return normalized


def http_error_detail(exc):
    reason = getattr(exc, "reason", None)
    if isinstance(exc, urllib.error.URLError) and (
        isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower()
    ):
        return "连接模型服务超时"
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        if body:
            return f"HTTP {exc.code} {exc.reason}: {body[:1000]}"
        return f"HTTP {exc.code} {exc.reason}"
    return str(exc)


def openai_chat_completion(base_url, api_key, payload):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = normalize_openai_chat_payload(payload)
    request_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        ai_chat_completions_url(base_url),
        data=request_data,
        headers=headers,
        method="POST",
    )
    with ai_urlopen(req, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def openai_model_list(base_url, api_key):
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    req = urllib.request.Request(
        ai_base_url(base_url).rstrip("/") + "/models",
        headers=headers,
        method="GET",
    )
    with ai_urlopen(req, timeout=20) as response:
        body = json.loads(response.read().decode("utf-8"))
    raw_models = body.get("data") if isinstance(body, dict) else body
    if not isinstance(raw_models, list):
        raw_models = []
    models = []
    for item in raw_models:
        if isinstance(item, dict):
            model_id = item.get("id") or item.get("name") or item.get("model")
            owned_by = item.get("owned_by") or item.get("provider") or ""
        else:
            model_id = str(item)
            owned_by = ""
        if model_id:
            models.append({"id": str(model_id), "owned_by": str(owned_by or "")})
    return models


def ai_urlopen(req, timeout):
    """打开模型接口。直接连接，不走环境变量里的代理。同一主机先连 IPv4。"""
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _AIHTTPHandler(),
        _AIHTTPSHandler(),
    )
    return opener.open(req, timeout=timeout)


class _AIHTTPConnection(HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect_reachable


class _AIHTTPSConnection(HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect_reachable


class _AIHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_AIHTTPConnection, req)


class _AIHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_AIHTTPSConnection, req, context=self._context)


def _connect_reachable(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    """逐个地址连接。IPv4 优先，单个地址最多等 5 秒，避免死地址吃掉全部超时。"""
    host, port = address
    infos = socket.getaddrinfo(str(host), int(port), socket.AF_UNSPEC, socket.SOCK_STREAM)
    infos.sort(key=lambda item: 0 if item[0] == socket.AF_INET else 1)
    limited = timeout is not socket._GLOBAL_DEFAULT_TIMEOUT and timeout is not None
    deadline = time.monotonic() + float(timeout) if limited else None
    errors = []
    for family, socktype, proto, _canon, sockaddr in infos:
        if deadline is None:
            per_try = 5.0
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            per_try = min(5.0, remaining)
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(per_try)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            sock.settimeout(None if deadline is None else float(timeout))
            return sock
        except OSError as exc:
            errors.append(exc)
            sock.close()
    if errors and (deadline is None or time.monotonic() < deadline):
        raise errors[-1]
    raise TimeoutError("timed out")


def usage_from_openai(raw):
    """把上游 usage 收成 input、output、reasoning。"""
    if not isinstance(raw, dict):
        return {}
    details = raw.get("completion_tokens_details") or raw.get("output_tokens_details") or {}
    if not isinstance(details, dict):
        details = {}
    return {
        "input": _token_count(raw.get("prompt_tokens") or raw.get("input_tokens")),
        "output": _token_count(raw.get("completion_tokens") or raw.get("output_tokens")),
        "reasoning": _token_count(details.get("reasoning_tokens") or raw.get("reasoning_tokens")),
    }


def message_from_openai(body):
    """从非流式响应取出正文、思考、工具调用和用量。"""
    choice = (body.get("choices") or [{}])[0] if isinstance(body, dict) else {}
    message = choice.get("message") or {}
    calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments or {}, ensure_ascii=False)
        calls.append(
            {
                "id": str(call.get("id") or ""),
                "name": str(function.get("name") or ""),
                "arguments": arguments,
            }
        )
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    return {
        "content": message.get("content") or "",
        "reasoning": reasoning if isinstance(reasoning, str) else str(reasoning or ""),
        "tool_calls": calls,
        "usage": usage_from_openai(body.get("usage") if isinstance(body, dict) else {}),
    }


def openai_chat_events(base_url, api_key, payload, timeout=90):
    """流式读取。先抛出思考片段，最后抛出完整消息。"""
    prepared = _prepare_stream_payload(payload)
    try:
        response = _post_chat(base_url, api_key, prepared, timeout)
    except urllib.error.HTTPError as exc:
        detail = http_error_detail(exc)
        adjusted = _compat_payload(prepared, detail)
        if adjusted is None:
            raise RuntimeError(detail)
        try:
            response = _post_chat(base_url, api_key, adjusted, timeout)
        except urllib.error.HTTPError as retry_exc:
            raise RuntimeError(http_error_detail(retry_exc))
    try:
        yield from _read_chat_response(response)
    finally:
        response.close()


def openai_chat_message(base_url, api_key, payload, timeout=90):
    """读完整轮模型结果。压缩和编码改正用这条，不把过程推到页面。"""
    found = None
    for event in openai_chat_events(base_url, api_key, payload, timeout=timeout):
        if event.get("type") == "_message":
            found = event["message"]
    if found is None:
        raise RuntimeError("模型没有返回内容")
    return found


def resolve_model_name(base_url, api_key, model):
    """模型名为空时从 /model 或 /models 取一个。"""
    chosen = str(model or "").strip()
    if chosen or not base_url:
        return chosen
    errors = []
    for suffix in ("/model", "/models"):
        try:
            req = urllib.request.Request(
                ai_base_url(base_url).rstrip("/") + suffix,
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                method="GET",
            )
            with ai_urlopen(req, timeout=8) as response:
                body = json.loads(response.read().decode("utf-8"))
            chosen = _first_model_id(body)
            if chosen:
                return chosen
        except Exception as exc:
            errors.append(str(exc))
    detail = f"：{errors[-1]}" if errors else ""
    raise ValueError("AI 模型名为空，且未能从 /model 或 /models 自动获取" + detail)


def _prepare_stream_payload(payload):
    prepared = normalize_openai_chat_payload(payload)
    prepared["stream"] = True
    prepared.setdefault("stream_options", {"include_usage": True})
    return prepared


def _compat_payload(payload, detail):
    """只调整输出长度、温度和流式用量字段。思考参数保持原样。"""
    lower = str(detail or "").lower()
    updated = dict(payload)
    changed = False
    if "max_tokens" in lower and "max_completion_tokens" in lower and "max_tokens" in updated:
        updated["max_completion_tokens"] = updated.pop("max_tokens")
        changed = True
    if "temperature" in lower and "temperature" in updated:
        updated.pop("temperature", None)
        changed = True
    if "stream_options" in lower and "stream_options" in updated:
        updated.pop("stream_options", None)
        changed = True
    return updated if changed else None


def _post_chat(base_url, api_key, payload, timeout):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        ai_chat_completions_url(base_url),
        data=data,
        headers=headers,
        method="POST",
    )
    return ai_urlopen(req, timeout=timeout)


def _read_chat_response(response):
    first = response.read(1)
    if not first:
        raise RuntimeError("模型没有返回内容")
    if first == b"{":
        body = json.loads((first + response.read()).decode("utf-8", errors="replace"))
        message = message_from_openai(body)
        if message["reasoning"]:
            yield {"type": "thinking", "text": message["reasoning"]}
        yield {"type": "_message", "message": message}
        return
    accumulator = _StreamAccumulator()
    buffer = first
    while True:
        chunk = response.read(1024)
        if chunk:
            buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            event = _sse_event(line)
            if event is None:
                continue
            if event == "done":
                yield {"type": "_message", "message": accumulator.finish()}
                return
            piece = accumulator.feed(event)
            if piece:
                yield piece
        if not chunk:
            break
    yield {"type": "_message", "message": accumulator.finish()}


def _sse_event(line):
    text = line.strip()
    if not text or text.startswith(b":"):
        return None
    if text.startswith(b"data:"):
        text = text[5:].strip()
    if text == b"[DONE]":
        return "done"
    try:
        return json.loads(text.decode("utf-8", errors="replace"))
    except ValueError:
        return None


class _StreamAccumulator:
    """拼起流式增量里的正文、思考和工具参数。"""

    def __init__(self):
        self.content = []
        self.reasoning = []
        self.calls = {}
        self.usage = {}

    def feed(self, event):
        if not isinstance(event, dict):
            return None
        if event.get("usage"):
            self.usage = usage_from_openai(event["usage"])
        choice = (event.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        piece = None
        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
        if reasoning:
            self.reasoning.append(str(reasoning))
            piece = {"type": "thinking", "text": str(reasoning)}
        if delta.get("content"):
            self.content.append(str(delta["content"]))
        for call in delta.get("tool_calls") or []:
            index = int(call.get("index") or 0)
            slot = self.calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if call.get("id"):
                slot["id"] = str(call["id"])
            function = call.get("function") or {}
            if function.get("name"):
                slot["name"] += str(function["name"])
            if function.get("arguments"):
                slot["arguments"] += str(function["arguments"])
        return piece

    def finish(self):
        return {
            "content": "".join(self.content),
            "reasoning": "".join(self.reasoning),
            "tool_calls": [self.calls[index] for index in sorted(self.calls)],
            "usage": self.usage,
        }


def _first_model_id(body):
    if isinstance(body, dict):
        if isinstance(body.get("id"), str) and body.get("id"):
            return body["id"]
        models = body.get("data") or body.get("models") or []
    elif isinstance(body, list):
        models = body
    else:
        return ""
    if not isinstance(models, list) or not models:
        return ""
    usable = [item for item in models if "embed" not in str(item).lower()]
    first = (usable or models)[0]
    if isinstance(first, dict):
        return str(first.get("id") or first.get("name") or "")
    return str(first or "")


def _token_count(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

