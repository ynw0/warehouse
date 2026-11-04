"""把升级前未分项目的库存补录为办公，使其重新进入申领/借用池。"""

OFFICE = "办公"
_FORM_TYPES = ("claim", "borrow", "borrow_claim")


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _json_doc(column):
    return f"CASE WHEN json_valid(COALESCE({column}, '')) THEN {column} ELSE '{{}}' END"


def upgrade(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "material_batches" in tables and "purchase_project" in _columns(conn, "material_batches"):
        conn.execute(
            "UPDATE material_batches SET purchase_project = ? WHERE TRIM(COALESCE(purchase_project, '')) = ''",
            (OFFICE,),
        )
    if "materials" in tables and "purchase_project" in _columns(conn, "materials"):
        conn.execute(
            "UPDATE materials SET purchase_project = ? WHERE TRIM(COALESCE(purchase_project, '')) = ''",
            (OFFICE,),
        )
    if "workflow_items" in tables and "purchase_project" in _columns(conn, "workflow_items"):
        conn.execute(
            "UPDATE workflow_items SET purchase_project = ? WHERE TRIM(COALESCE(purchase_project, '')) = ''",
            (OFFICE,),
        )
    if "workflow_forms" not in tables:
        return
    form_cols = _columns(conn, "workflow_forms")
    if "data_json" not in form_cols or "form_type" not in form_cols:
        return
    doc = _json_doc("data_json")
    placeholders = ",".join("?" for _ in _FORM_TYPES)
    conn.execute(
        f"""
        UPDATE workflow_forms
        SET data_json = json_set({doc}, '$.purchase_project', ?)
        WHERE form_type IN ({placeholders})
          AND TRIM(COALESCE(json_extract({doc}, '$.purchase_project'), '')) = ''
        """,
        (OFFICE, *_FORM_TYPES),
    )
    conn.execute(
        f"""
        UPDATE workflow_forms
        SET data_json = json_set({doc}, '$.purpose', ?)
        WHERE form_type IN ({placeholders})
          AND json_extract({doc}, '$.purchase_project') = ?
          AND TRIM(COALESCE(json_extract({doc}, '$.purpose'), '')) = ''
        """,
        (OFFICE, *_FORM_TYPES, OFFICE),
    )


MIGRATION = {
    "version": "2026092103",
    "name": "backfill office purchase project",
    "upgrade": upgrade,
}
