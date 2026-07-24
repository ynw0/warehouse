"""Data validation helpers driven by configurable validation settings."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .settings import MAX_CODE_LENGTH, MAX_PRICE_DECIMALS, MAX_QUANTITY_DECIMALS, default_data_validation_settings, parse_json


_settings_provider = default_data_validation_settings


def configure_validation_settings_provider(provider):
    global _settings_provider
    _settings_provider = provider or default_data_validation_settings


def data_validation_rule(rule_key):
    settings = _settings_provider()
    rule = settings.get(rule_key) or {}
    if not isinstance(rule, dict):
        rule = {}
    return {"enabled": bool(rule.get("enabled", settings.get("enabled", True))), **rule}


def validation_rule_enabled(rule_key):
    return bool(data_validation_rule(rule_key).get("enabled", True))


def validated_number(value, label, default=0, required=False, min_value=None, max_value=None, integer=False, max_decimals=None):
    if value in (None, ""):
        if required:
            raise ValueError(f"{label}不能为空")
        return default
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}必须是有效数字")
    if not number.is_finite():
        raise ValueError(f"{label}必须是有效数字")
    if integer and number != number.to_integral_value():
        raise ValueError(f"{label}必须是整数")
    if min_value is not None and number < Decimal(str(min_value)):
        raise ValueError(f"{label}不能小于 {min_value:g}")
    if max_value is not None and number > Decimal(str(max_value)):
        raise ValueError(f"{label}不能大于 {max_value:g}")
    if max_decimals is not None:
        normalized = number.normalize()
        decimal_places = max(0, -normalized.as_tuple().exponent)
        if decimal_places > int(max_decimals):
            raise ValueError(f"{label}最多允许 {int(max_decimals)} 位小数")
    return int(number) if integer else float(number)


def quantity_value(value, label="数量", default=0, positive=False):
    rule = data_validation_rule("quantity")
    if not rule.get("enabled", True):
        number = validated_number(value, label, default=default)
        return number
    min_value = 1e-9 if positive else rule.get("min_value", 0)
    return validated_number(
        value,
        label,
        default=default,
        required=positive,
        min_value=min_value,
        max_decimals=rule.get("max_decimals", MAX_QUANTITY_DECIMALS),
    )


def price_value(value, label="单价", default=0):
    rule = data_validation_rule("price")
    if not rule.get("enabled", True):
        return validated_number(value, label, default=default)
    return validated_number(
        value,
        label,
        default=default,
        min_value=rule.get("min_value", 0),
        max_decimals=rule.get("max_decimals", MAX_PRICE_DECIMALS),
    )


def positive_int_value(value, label, default=None):
    number = validated_number(value, label, default=default, required=default is None, min_value=1, integer=True)
    return int(number) if number is not None else None


def nonnegative_int_value(value, label, default=0):
    number = validated_number(value, label, default=default, min_value=0, integer=True)
    return int(number)


def validate_material_code_value(value):
    rule = data_validation_rule("material_code")
    code = str(value or "").strip()
    if not rule.get("enabled", True):
        return code
    length = int(rule.get("length") or 14)
    if len(code) != length:
        suffix = "数字" if rule.get("digits_only", True) else ""
        raise ValueError(f"物料编号必须为 {length} 位{suffix}")
    if rule.get("digits_only", True) and not code.isdigit():
        raise ValueError("物料编号必须全部为数字")
    suffix = code[-4:] if len(code) >= 4 else ""
    if suffix and suffix.isdigit() and int(suffix) == 0:
        raise ValueError("物料编号后四位不能为 0000")
    return code


def validate_plain_text(value, label, max_length=MAX_CODE_LENGTH, required=False, rule_key=None):
    rule = data_validation_rule(rule_key) if rule_key else {"enabled": True}
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{label}不能为空")
    if not rule.get("enabled", True):
        return text
    max_length = int(rule.get("max_length") or max_length)
    if len(text) > max_length:
        raise ValueError(f"{label}长度不能超过 {max_length} 个字符")
    allow_control_chars = bool(rule.get("allow_control_chars", False))
    if not allow_control_chars and any(ord(char) < 32 for char in text):
        raise ValueError(f"{label}不能包含控制字符")
    return text


def validate_project_code(value):
    return validate_plain_text(value, "项目号", max_length=50, rule_key="project_code")


def validate_batch_no(value, required=False):
    return validate_plain_text(value, "批次号", max_length=MAX_CODE_LENGTH, required=required, rule_key="batch_no")


def validate_serial_no(value):
    return validate_plain_text(value, "编号", max_length=MAX_CODE_LENGTH, required=True, rule_key="serial_no")


def payload_float(value, default=0):
    return validated_number(value, "数值", default=default)


def payload_int_or_none(value):
    if value in (None, ""):
        return None
    return int(validated_number(value, "整数", min_value=0, integer=True))


def normalize_component_json(value, default="[]"):
    if value in (None, ""):
        return default
    if isinstance(value, str):
        parsed = parse_json(value, [])
    else:
        parsed = value
    if not isinstance(parsed, list):
        raise ValueError("组件明细必须是数组")
    return parsed
