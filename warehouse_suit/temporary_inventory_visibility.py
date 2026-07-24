"""Shared SQL visibility rules for temporary-inventory workflows and notifications."""

from __future__ import annotations

from warehouse_suit.inventory_constants import (
    STOCK_SOURCE_TEMPORARY,
    TEMPORARY_WORKFLOW_ORIGIN_TYPES,
)
from warehouse_suit.settings import temporary_inventory_enabled


def _safe_alias(alias):
    value = str(alias or "").strip()
    if not value.replace("_", "").isalnum():
        raise ValueError("invalid SQL alias")
    return value


def temporary_workflow_sql(form_alias="f"):
    alias = _safe_alias(form_alias)
    origins = sorted(TEMPORARY_WORKFLOW_ORIGIN_TYPES)
    placeholders = ", ".join("?" for _ in origins)
    sql = f"""(
        (
            {alias}.form_type <> 'borrow_return'
            AND EXISTS (
                SELECT 1
                FROM workflow_items wi_temporary
                WHERE wi_temporary.form_id = {alias}.id
                  AND wi_temporary.stock_source = ?
            )
        )
        OR (
            {alias}.origin_type IN ({placeholders})
            AND NOT (
                {alias}.form_type = 'acceptance'
                AND {alias}.origin_type = 'temporary_transfer'
                AND EXISTS (
                    SELECT 1
                    FROM transfer_acceptance_links tal_visible
                    WHERE tal_visible.acceptance_form_id = {alias}.id
                )
            )
        )
    )"""
    return sql, [STOCK_SOURCE_TEMPORARY, *origins]


def append_workflow_visibility(cursor, where, params, form_alias="f"):
    if temporary_inventory_enabled(cursor):
        return
    sql, values = temporary_workflow_sql(form_alias)
    where.append(f"NOT {sql}")
    params.extend(values)


def workflow_is_temporary(cursor, form_id):
    sql, params = temporary_workflow_sql("f")
    cursor.execute(
        f"SELECT 1 FROM workflow_forms f WHERE f.id = ? AND {sql}",
        [int(form_id), *params],
    )
    return bool(cursor.fetchone())


def notification_visibility_sql(notification_alias="n"):
    alias = _safe_alias(notification_alias)
    flow_sql, flow_params = temporary_workflow_sql("f_notification")
    origins = sorted(TEMPORARY_WORKFLOW_ORIGIN_TYPES)
    placeholders = ", ".join("?" for _ in origins)
    sql = f"""NOT (
        (
            json_valid({alias}.data_json)
            AND (
                COALESCE(json_extract({alias}.data_json, '$.stock_source'), '') = ?
                OR COALESCE(json_extract({alias}.data_json, '$.origin_type'), '') IN ({placeholders})
                OR COALESCE(json_extract({alias}.data_json, '$.business_type'), '') LIKE 'temporary_%'
                OR EXISTS (
                    SELECT 1
                    FROM workflow_forms f_notification
                    WHERE f_notification.id = CAST(json_extract({alias}.data_json, '$.form_id') AS INTEGER)
                      AND {flow_sql}
                )
            )
        )
    )"""
    return sql, [STOCK_SOURCE_TEMPORARY, *origins, *flow_params]


def append_notification_visibility(cursor, where, params, notification_alias="n"):
    if temporary_inventory_enabled(cursor):
        return
    sql, values = notification_visibility_sql(notification_alias)
    where.append(sql)
    params.extend(values)
