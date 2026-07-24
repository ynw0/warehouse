"""OpenAI-compatible HTTP helpers for the AI material assistant."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


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
    with urllib.request.urlopen(req, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def openai_model_list(base_url, api_key):
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    req = urllib.request.Request(
        ai_base_url(base_url).rstrip("/") + "/models",
        headers=headers,
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=20) as response:
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

