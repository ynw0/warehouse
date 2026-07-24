"""Helpers for reading runtime logs."""

from __future__ import annotations

import os
from collections import deque


def log_tail(path, limit=300, error_only=False):
    if not os.path.exists(path):
        return []
    needles = ("error", "exception", "traceback", "failed", "失败")
    lines = deque(maxlen=max(1, min(2000, int(limit or 300))))
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as file:
            for line in file:
                text = line.rstrip("\n")
                if error_only and not any(needle in text.lower() for needle in needles):
                    continue
                lines.append(text)
    except OSError:
        return []
    return list(lines)

