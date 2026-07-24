"""System settings, validation settings, and password policy helpers."""

from __future__ import annotations

import json


MAX_QUANTITY_DECIMALS = 6
MAX_PRICE_DECIMALS = 4
MAX_CODE_LENGTH = 64


def parse_json(value, default=None):
    if default is None:
        default = {}
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default


def get_setting(cursor, key, default=""):
    cursor.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row[0] if row else default


def set_setting(cursor, key, value):
    cursor.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def password_policy(cursor):
    defaults = {
        "min_length": 6,
        "require_digit": False,
        "require_lower": False,
        "require_upper": False,
        "require_symbol": False,
        "force_change_on_first_login": True,
    }
    stored = parse_json(get_setting(cursor, "password_policy", "{}"), {})
    if isinstance(stored, dict):
        defaults.update(stored)
    defaults["min_length"] = max(1, int(float(defaults.get("min_length") or 6)))
    for key in ["require_digit", "require_lower", "require_upper", "require_symbol"]:
        defaults[key] = bool(defaults.get(key))
    defaults["force_change_on_first_login"] = bool(defaults.get("force_change_on_first_login"))
    return defaults


def password_policy_text(policy):
    parts = [f"至少 {int(policy.get('min_length') or 6)} 位"]
    if policy.get("require_digit"):
        parts.append("包含数字")
    if policy.get("require_lower"):
        parts.append("包含小写字母")
    if policy.get("require_upper"):
        parts.append("包含大写字母")
    if policy.get("require_symbol"):
        parts.append("包含符号")
    return "密码要求：" + "、".join(parts) + "。"


def validate_password_policy(password, policy):
    text = str(password or "")
    errors = []
    if len(text) < int(policy.get("min_length") or 6):
        errors.append(f"密码长度至少 {int(policy.get('min_length') or 6)} 位")
    if policy.get("require_digit") and not any(ch.isdigit() for ch in text):
        errors.append("密码必须包含数字")
    if policy.get("require_lower") and not any("a" <= ch <= "z" for ch in text):
        errors.append("密码必须包含小写字母")
    if policy.get("require_upper") and not any("A" <= ch <= "Z" for ch in text):
        errors.append("密码必须包含大写字母")
    if policy.get("require_symbol") and not any(not ch.isalnum() for ch in text):
        errors.append("密码必须包含符号")
    if errors:
        raise ValueError("；".join(errors))


WORKFLOW_STEP_DEFINITIONS = {
    "acceptance": ["acceptance", "leader_acceptance", "inbound"],
    "claim": ["leader_claim", "outbound"],
    "borrow": ["leader_borrow", "borrow_outbound"],
    "borrow_return": ["return_inbound"],
    "common_material": ["leader_common_material"],
    "supply": ["leader_supply", "supply_outbound"],
    "supply_return": ["supply_return_inbound"],
    "supply_extension": ["leader_supply_extension"],
    "semifinished": ["acceptance", "leader_acceptance", "inbound"],
    "finished": ["acceptance", "leader_acceptance", "inbound"],
}


def normalize_workflow_step_assignees(value):
    if not isinstance(value, dict):
        value = {}
    normalized = {}
    for form_type, steps in WORKFLOW_STEP_DEFINITIONS.items():
        normalized[form_type] = {}
        incoming_steps = value.get(form_type) if isinstance(value.get(form_type), dict) else {}
        for step_code in steps:
            raw_config = incoming_steps.get(step_code) if isinstance(incoming_steps, dict) else {}
            if isinstance(raw_config, dict):
                raw_roles = raw_config.get("roles") or []
                raw_ids = raw_config.get("users") or raw_config.get("user_ids") or []
            else:
                raw_roles = []
                raw_ids = raw_config or []
            roles = []
            for raw_role in raw_roles or []:
                role = str(raw_role or "").strip()
                if role and role not in roles:
                    roles.append(role)
            ids = []
            for raw_id in raw_ids or []:
                try:
                    user_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if user_id and user_id not in ids:
                    ids.append(user_id)
            normalized[form_type][step_code] = {"roles": roles, "users": ids}
    return normalized


def workflow_settings(cursor):
    defaults = {
        "claim_leader_same_department": True,
        "acceptance_leader_locked_after_first_inspect": True,
        "default_stocktake_reminder_day": 25,
        "allow_multi_claim_leaders": False,
        "dashboard_metrics": ["total_amount", "month_in", "month_out"],
        "card_button_roles": ["admin", "warehouse"],
        "ai_welcome_message": "您好我是仓库管理小助手，我可以帮你查询物料，辅助编写新编码。",
        "notification_inbound_template": "有新的物料：“{name}”“{brand_model}”“{spec}”入库了，请按需领取。",
        "notification_inbound_participant_template": "您验收的物料：“{name}”“{brand_model}”“{spec}”已入库，请按需领取。",
        "workflow_step_assignees": normalize_workflow_step_assignees({}),
        "single_item_inbound_enabled": False,
        "acceptance_material_photo_required": False,
        "acceptance_document_required": False,
        "temporary_inventory_material_photo_required": False,
        "temporary_inventory_document_required": False,
        "recycle_retention_days": 30,
        "notification_retention_days": 90,
        "duplicate_acceptance_check_days": 7,
        "allow_manual_approval_leader": False,
        "temporary_inventory_enabled": False,
        "project_codes": [],
    }
    stored = parse_json(get_setting(cursor, "workflow_settings", "{}"), {})
    defaults.update(stored if isinstance(stored, dict) else {})
    defaults["workflow_step_assignees"] = normalize_workflow_step_assignees(defaults.get("workflow_step_assignees"))
    defaults["single_item_inbound_enabled"] = bool(defaults.get("single_item_inbound_enabled"))
    defaults["acceptance_material_photo_required"] = bool(defaults.get("acceptance_material_photo_required"))
    defaults["acceptance_document_required"] = bool(defaults.get("acceptance_document_required"))
    defaults["temporary_inventory_material_photo_required"] = bool(defaults.get("temporary_inventory_material_photo_required"))
    defaults["temporary_inventory_document_required"] = bool(defaults.get("temporary_inventory_document_required"))
    try:
        defaults["recycle_retention_days"] = int(defaults.get("recycle_retention_days") or 30)
    except (TypeError, ValueError):
        defaults["recycle_retention_days"] = 30
    if defaults["recycle_retention_days"] not in {7, 15, 30, 60, 90}:
        defaults["recycle_retention_days"] = 30
    try:
        defaults["notification_retention_days"] = int(defaults.get("notification_retention_days") or 90)
    except (TypeError, ValueError):
        defaults["notification_retention_days"] = 90
    if defaults["notification_retention_days"] not in {7, 15, 30, 90, 180}:
        defaults["notification_retention_days"] = 90
    try:
        defaults["duplicate_acceptance_check_days"] = int(defaults.get("duplicate_acceptance_check_days") or 7)
    except (TypeError, ValueError):
        defaults["duplicate_acceptance_check_days"] = 7
    if defaults["duplicate_acceptance_check_days"] not in {7, 15, 30, 90}:
        defaults["duplicate_acceptance_check_days"] = 7
    defaults["allow_manual_approval_leader"] = bool(defaults.get("allow_manual_approval_leader"))
    defaults["temporary_inventory_enabled"] = bool_setting(defaults.get("temporary_inventory_enabled"))
    if isinstance(defaults.get("project_codes"), str):
        defaults["project_codes"] = [item.strip() for item in defaults["project_codes"].replace("；", ",").split(",") if item.strip()]
    if not isinstance(defaults.get("project_codes"), list):
        defaults["project_codes"] = []
    return defaults


def temporary_inventory_enabled(cursor):
    return bool(workflow_settings(cursor).get("temporary_inventory_enabled"))


def default_data_validation_settings():
    return {
        "enabled": True,
        "material_code": {"enabled": True, "length": 14, "digits_only": True},
        "quantity": {"enabled": True, "min_value": 0, "max_decimals": MAX_QUANTITY_DECIMALS},
        "price": {"enabled": True, "min_value": 0, "max_decimals": MAX_PRICE_DECIMALS},
        "project_code": {"enabled": True, "max_length": 50, "allow_control_chars": False},
        "batch_no": {"enabled": True, "required": True, "max_length": MAX_CODE_LENGTH, "allow_control_chars": False},
        "serial_no": {
            "enabled": True,
            "required": True,
            "max_length": MAX_CODE_LENGTH,
            "allow_control_chars": False,
            "count_within_acceptance": True,
            "unique_in_payload": True,
            "unique_in_database": True,
        },
        "maker_user": {"enabled": True},
        "workflow_bounds": {"enabled": True},
    }


def merge_dict_settings(base, incoming):
    if not isinstance(incoming, dict):
        return base
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key].update(value)
        elif key in base:
            base[key] = value
    return base


def bool_setting(value, default=False):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "启用", "是"}
    return bool(default) if value is None else bool(value)


def int_setting(value, default, minimum=None, maximum=None):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def float_setting(value, default, minimum=None, maximum=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def normalize_data_validation_settings(value=None):
    settings = merge_dict_settings(default_data_validation_settings(), value or {})
    settings["enabled"] = bool_setting(settings.get("enabled"), True)
    settings["material_code"]["enabled"] = bool_setting(settings["material_code"].get("enabled"), True)
    settings["material_code"]["length"] = int_setting(settings["material_code"].get("length"), 14, 1, 64)
    settings["material_code"]["digits_only"] = bool_setting(settings["material_code"].get("digits_only"), True)
    settings["quantity"]["enabled"] = bool_setting(settings["quantity"].get("enabled"), True)
    settings["quantity"]["min_value"] = float_setting(settings["quantity"].get("min_value"), 0, 0)
    settings["quantity"]["max_decimals"] = int_setting(settings["quantity"].get("max_decimals"), MAX_QUANTITY_DECIMALS, 0, 8)
    settings["price"]["enabled"] = bool_setting(settings["price"].get("enabled"), True)
    settings["price"]["min_value"] = float_setting(settings["price"].get("min_value"), 0, 0)
    settings["price"]["max_decimals"] = int_setting(settings["price"].get("max_decimals"), MAX_PRICE_DECIMALS, 0, 8)
    for key, default_length in [("project_code", 50), ("batch_no", MAX_CODE_LENGTH), ("serial_no", MAX_CODE_LENGTH)]:
        settings[key]["enabled"] = bool_setting(settings[key].get("enabled"), True)
        settings[key]["max_length"] = int_setting(settings[key].get("max_length"), default_length, 1, 200)
        settings[key]["allow_control_chars"] = bool_setting(settings[key].get("allow_control_chars"), False)
    settings["batch_no"]["required"] = bool_setting(settings["batch_no"].get("required"), True)
    settings["serial_no"]["required"] = bool_setting(settings["serial_no"].get("required"), True)
    settings["serial_no"]["count_within_acceptance"] = bool_setting(settings["serial_no"].get("count_within_acceptance"), True)
    settings["serial_no"]["unique_in_payload"] = bool_setting(settings["serial_no"].get("unique_in_payload"), True)
    settings["serial_no"]["unique_in_database"] = bool_setting(settings["serial_no"].get("unique_in_database"), True)
    settings["maker_user"]["enabled"] = bool_setting(settings["maker_user"].get("enabled"), True)
    settings["workflow_bounds"]["enabled"] = bool_setting(settings["workflow_bounds"].get("enabled"), True)
    return settings


def data_validation_settings(cursor):
    stored = parse_json(get_setting(cursor, "data_validation_settings", "{}"), {})
    return normalize_data_validation_settings(stored if isinstance(stored, dict) else {})


def recycle_retention_days(cursor):
    return workflow_settings(cursor).get("recycle_retention_days", 30)

