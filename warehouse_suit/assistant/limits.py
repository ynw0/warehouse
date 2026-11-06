"""助手共享的调用轮数和整轮时间限制。"""

import time

MAX_ROUNDS = 16
CHILD_MAX_ROUNDS = 8
MAX_SECONDS = 600


def check_deadline(deadline):
    """主对话和嵌套分类共用一个截止时间。"""
    if time.monotonic() >= deadline:
        raise TimeoutError(f"本轮对话已超过 {MAX_SECONDS} 秒")
