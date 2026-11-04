"""Lightweight, repeatable SQLite migration runner."""

from __future__ import annotations

import logging

from .v2026071301_inventory_source_foundation import MIGRATION as INVENTORY_SOURCE_FOUNDATION
from .v2026071401_temporary_inventory_management import MIGRATION as TEMPORARY_INVENTORY_MANAGEMENT
from .v2026071402_temporary_issue_obligations import MIGRATION as TEMPORARY_ISSUE_OBLIGATIONS
from .v2026071403_borrow_source_foundation import MIGRATION as BORROW_SOURCE_FOUNDATION
from .v2026072401_defective_common_supply import MIGRATION as DEFECTIVE_COMMON_SUPPLY_FOUNDATION
from .v2026082701_workflow_task_integrity import MIGRATION as WORKFLOW_TASK_INTEGRITY
from .v2026083101_batch_warehouse_alignment import MIGRATION as BATCH_WAREHOUSE_ALIGNMENT
from .v2026090701_claim_returns import MIGRATION as CLAIM_RETURNS
from .v2026090801_defective_borrow_quantities import MIGRATION as DEFECTIVE_BORROW_QUANTITIES
from .v2026090802_partial_claim_outbound import MIGRATION as PARTIAL_CLAIM_OUTBOUND
from .v2026090803_batch_transfer_controls import MIGRATION as BATCH_TRANSFER_CONTROLS
from .v2026091601_borrow_partial_claim import MIGRATION as BORROW_PARTIAL_CLAIM
from .v2026091602_workflow_drafts import MIGRATION as WORKFLOW_DRAFTS
from .v2026092101_borrow_claim_issued import MIGRATION as BORROW_CLAIM_ISSUED
from .v2026092102_purchase_project import MIGRATION as PURCHASE_PROJECT
from .v2026092103_backfill_office_purchase_project import MIGRATION as BACKFILL_OFFICE_PURCHASE_PROJECT
from .v2026092201_projects_usage_pack import MIGRATION as PROJECTS_USAGE_PACK
from .v2026092202_book_quantity import MIGRATION as BOOK_QUANTITY
from .v2026092301_batch_purchase_applicant import MIGRATION as BATCH_PURCHASE_APPLICANT
from .v2026100901_group_material_codes import MIGRATION as GROUP_MATERIAL_CODES
from .v2026100902_category_rules import MIGRATION as CATEGORY_RULES
from .v2026101201_semifinished_claim_returns import MIGRATION as SEMIFINISHED_CLAIM_RETURNS
from .v2026101202_supply_handover import MIGRATION as SUPPLY_HANDOVER
from .v2026101301_category_rule_names import MIGRATION as CATEGORY_RULE_NAMES
from .v2026101401_assistant_confirmations import MIGRATION as ASSISTANT_CONFIRMATIONS
from .v2026101501_access_tokens import MIGRATION as ACCESS_TOKENS
from .v2026101502_assistant_runtime import MIGRATION as ASSISTANT_RUNTIME
from .v2026101503_category_rule_glue_cable_notes import MIGRATION as CATEGORY_RULE_GLUE_CABLE_NOTES


LOGGER = logging.getLogger(__name__)


def available_migrations():
    return [
        INVENTORY_SOURCE_FOUNDATION,
        TEMPORARY_INVENTORY_MANAGEMENT,
        TEMPORARY_ISSUE_OBLIGATIONS,
        BORROW_SOURCE_FOUNDATION,
        DEFECTIVE_COMMON_SUPPLY_FOUNDATION,
        WORKFLOW_TASK_INTEGRITY,
        BATCH_WAREHOUSE_ALIGNMENT,
        CLAIM_RETURNS,
        DEFECTIVE_BORROW_QUANTITIES,
        PARTIAL_CLAIM_OUTBOUND,
        BATCH_TRANSFER_CONTROLS,
        BORROW_PARTIAL_CLAIM,
        WORKFLOW_DRAFTS,
        BORROW_CLAIM_ISSUED,
        PURCHASE_PROJECT,
        BACKFILL_OFFICE_PURCHASE_PROJECT,
        PROJECTS_USAGE_PACK,
        BOOK_QUANTITY,
        BATCH_PURCHASE_APPLICANT,
        GROUP_MATERIAL_CODES,
        CATEGORY_RULES,
        SEMIFINISHED_CLAIM_RETURNS,
        SUPPLY_HANDOVER,
        CATEGORY_RULE_NAMES,
        ASSISTANT_CONFIRMATIONS,
        ACCESS_TOKENS,
        ASSISTANT_RUNTIME,
        CATEGORY_RULE_GLUE_CABLE_NOTES,
    ]


def _ensure_migration_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def run_migrations(conn, migrations=None, logger=None):
    """Apply pending migrations, one transaction per migration."""
    logger = logger or LOGGER
    migration_list = list(migrations if migrations is not None else available_migrations())
    _ensure_migration_table(conn)
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

    for migration in migration_list:
        version = str(migration["version"])
        name = str(migration["name"])
        if version in applied:
            logger.debug("database migration %s (%s) already applied", version, name)
            continue
        logger.info("applying database migration %s (%s)", version, name)
        rebuilds_referenced_table = bool(migration.get("rebuilds_referenced_table"))
        foreign_keys_were_enabled = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
        if rebuilds_referenced_table and foreign_keys_were_enabled:
            conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            migration["upgrade"](conn)
            if rebuilds_referenced_table:
                violations = list(conn.execute("PRAGMA foreign_key_check"))
                if violations:
                    raise RuntimeError(
                        f"migration {version} would leave foreign-key violations: {violations[:5]}"
                    )
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (version, name),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("database migration %s (%s) failed and was rolled back", version, name)
            raise
        finally:
            if rebuilds_referenced_table and foreign_keys_were_enabled:
                conn.execute("PRAGMA foreign_keys = ON")
        logger.info("database migration %s (%s) applied", version, name)
        applied.add(version)

    return sorted(applied)
