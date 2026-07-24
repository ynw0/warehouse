"""Permission keys and role-permission settings."""

from __future__ import annotations

from .settings import get_setting, parse_json


PERMISSION_KEYS = [
    "view_query",
    "view_flow",
    "view_outbound",
    "view_stats",
    "view_stocktake",
    "view_borrow",
    "view_logs",
    "view_recycle",
    "view_my_inspections",
    "view_my_started",
    "start_acceptance",
    "start_claim",
    "start_borrow",
    "start_stocktake",
    "add_material",
    "edit_acceptance",
    "edit_claim",
    "edit_borrow",
    "edit_outbound",
    "edit_stocktake",
    "edit_department",
    "edit_material",
    "delete_material_attachment",
    "read_semifinished_inventory",
    "write_semifinished_inventory",
    "read_finished_inventory",
    "write_finished_inventory",
    "view_temporary_inventory",
    "manage_temporary_inventory",
    "transfer_temporary_inventory",
    "process_temporary_transfer",
    "view_defective_inventory",
    "manage_defective_inventory",
    "start_common_material",
    "view_supply",
    "start_supply",
    "edit_supply",
]

PERMISSION_ALIASES = {
    "delete_acceptance": "edit_acceptance",
    "delete_claim": "edit_claim",
    "delete_borrow": "edit_borrow",
    "delete_outbound": "edit_outbound",
    "delete_stocktake": "edit_stocktake",
    "delete_department": "edit_department",
    "delete_material": "edit_material",
    "delete_attachment": "delete_material_attachment",
    "delete_semifinished_inventory": "write_semifinished_inventory",
    "delete_finished_inventory": "write_finished_inventory",
}


def canonical_permission(permission):
    return PERMISSION_ALIASES.get(permission, permission)


def role_permissions(cursor):
    defaults = {
        "admin": {key: True for key in PERMISSION_KEYS},
        "warehouse": {
            "view_query": True,
            "view_flow": True,
            "view_outbound": True,
            "view_stats": True,
            "view_stocktake": True,
            "view_borrow": True,
            "view_my_inspections": True,
            "view_my_started": True,
            "view_defective_inventory": True,
            "start_common_material": True,
            "start_supply": True,
            "view_supply": True,
            "start_acceptance": True,
            "start_claim": True,
            "start_borrow": True,
            "start_stocktake": True,
            "add_material": True,
            "edit_stocktake": True,
            "edit_outbound": True,
            "edit_borrow": True,
            "edit_material": True,
            "delete_material_attachment": True,
            "read_semifinished_inventory": True,
            "write_semifinished_inventory": True,
            "read_finished_inventory": True,
            "start_common_material": True,
            "start_supply": True,
            "view_supply": True,
            "write_finished_inventory": True,
            "view_temporary_inventory": True,
            "manage_temporary_inventory": True,
            "transfer_temporary_inventory": True,
            "view_defective_inventory": True,
            "manage_defective_inventory": True,
            "start_common_material": True,
            "view_supply": True,
            "start_supply": True,
            "edit_supply": True,
        },
        "buyer": {
            "view_query": True,
            "view_flow": True,
            "view_stats": True,
            "start_acceptance": True,
            "start_claim": True,
            "start_borrow": True,
            "view_borrow": True,
            "view_my_inspections": True,
            "view_my_started": True,
            "read_semifinished_inventory": True,
            "read_finished_inventory": True,
            "process_temporary_transfer": True,
            "view_defective_inventory": True,
        },
        "leader": {
            "view_query": True,
            "view_flow": True,
            "view_stats": True,
            "view_borrow": True,
            "view_my_inspections": True,
            "view_my_started": True,
            "read_semifinished_inventory": True,
            "read_finished_inventory": True,
        },
        "user": {
            "view_query": True,
            "start_claim": True,
            "start_borrow": True,
            "view_borrow": True,
            "view_my_inspections": True,
            "view_my_started": True,
            "view_supply": True,
            "start_supply": True,
            "start_common_material": True,
            "view_defective_inventory": True,
        },
    }
    stored = parse_json(get_setting(cursor, "role_permissions", "{}"), {})
    if isinstance(stored, dict):
        for role, values in stored.items():
            defaults.setdefault(role, {})
            if isinstance(values, dict):
                defaults[role].update({key: bool(values.get(key)) for key in PERMISSION_KEYS if key in values})
                if values.get("acceptance_right") and "start_acceptance" not in values:
                    defaults[role]["start_acceptance"] = True
                for old_key, new_key in PERMISSION_ALIASES.items():
                    if values.get(old_key):
                        defaults[role][new_key] = True
    for role, values in defaults.items():
        if role == "admin":
            continue
        if values.get("write_semifinished_inventory"):
            values["read_semifinished_inventory"] = False
        if values.get("write_finished_inventory"):
            values["read_finished_inventory"] = False
        if values.get("manage_temporary_inventory"):
            values["view_temporary_inventory"] = True
    return defaults

