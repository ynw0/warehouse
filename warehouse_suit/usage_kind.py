"""用料形式与每单位数量。"""

from __future__ import annotations

from warehouse_suit.validation import quantity_value


USAGE_RD = "研发用料"
USAGE_PRODUCTION = "生产用料"
USAGE_KINDS = (USAGE_RD, USAGE_PRODUCTION)


def validate_usage_kind(value, *, default=USAGE_RD):
    text = str(value or "").strip() or default
    if text not in USAGE_KINDS:
        raise ValueError("用料形式必须是研发用料或生产用料")
    return text


def pack_quantity_value(value, *, default=1):
    raw = value if value not in (None, "") else default
    number = quantity_value(raw, "每单位数量", positive=True)
    if number < 1 - 1e-9:
        raise ValueError("每单位数量不能小于 1")
    return number


def actual_quantity(arrival_quantity, pack_quantity):
    return float(arrival_quantity or 0) * float(pack_quantity or 1)
