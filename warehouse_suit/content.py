"""Text-file and lightweight Markdown rendering helpers."""

from __future__ import annotations

import os
from html import escape


def read_text_prefix(path, limit):
    try:
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as file:
                return file.read()[:limit]
    except OSError:
        pass
    return ""


def markdownish_to_html(text):
    blocks = []
    list_open = False
    for raw_line in str(text or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if list_open:
                blocks.append("</ul>")
                list_open = False
            continue
        if stripped.startswith("#"):
            if list_open:
                blocks.append("</ul>")
                list_open = False
            level = min(4, len(stripped) - len(stripped.lstrip("#")))
            content = stripped[level:].strip()
            blocks.append(f"<h{level}>{escape(content)}</h{level}>")
            continue
        if stripped.startswith(("- ", "* ")):
            if not list_open:
                blocks.append("<ul>")
                list_open = True
            blocks.append(f"<li>{escape(stripped[2:])}</li>")
            continue
        if list_open:
            blocks.append("</ul>")
            list_open = False
        blocks.append(f"<p>{escape(stripped)}</p>")
    if list_open:
        blocks.append("</ul>")
    return "\n".join(blocks)
