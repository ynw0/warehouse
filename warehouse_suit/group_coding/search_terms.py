"""集团分类检索用的确定性分词。"""

from __future__ import annotations

import re


def terms(text: str) -> set[str]:
    """生成两个分类召回器共同使用的确定性检索词。"""

    normalized = re.sub(r"\s+", "", text.lower())
    latin = re.findall(r"[a-z]+\d*[a-z\d-]*|\d+(?:\.\d+)?", normalized)
    chinese = [
        normalized[index : index + size]
        for size in (2, 3)
        for index in range(len(normalized) - size + 1)
    ]
    return {term for term in (*latin, *chinese) if term}

