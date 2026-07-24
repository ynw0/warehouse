"""Lightweight, repeatable SQLite migration runner."""

from __future__ import annotations

import logging

from .v2026071301_inventory_source_foundation import MIGRATION as INVENTORY_SOURCE_FOUNDATION
from .v2026071401_temporary_inventory_management import MIGRATION as TEMPORARY_INVENTORY_MANAGEMENT
from .v2026071402_temporary_issue_obligations import MIGRATION as TEMPORARY_ISSUE_OBLIGATIONS
from .v2026071403_borrow_source_foundation import MIGRATION as BORROW_SOURCE_FOUNDATION
from .v2026071404_temporary_transfer_foundation import MIGRATION as TEMPORARY_TRANSFER_FOUNDATION
from .v2026071501_transfer_settlement_foundation import MIGRATION as TRANSFER_SETTLEMENT_FOUNDATION
from .v2026072401_defective_common_supply import MIGRATION as DEFECTIVE_COMMON_SUPPLY_FOUNDATION


LOGGER = logging.getLogger(__name__)


def available_migrations():
    return [
        INVENTORY_SOURCE_FOUNDATION,
        TEMPORARY_INVENTORY_MANAGEMENT,
        TEMPORARY_ISSUE_OBLIGATIONS,
        BORROW_SOURCE_FOUNDATION,
        TEMPORARY_TRANSFER_FOUNDATION,
        TRANSFER_SETTLEMENT_FOUNDATION,
        DEFECTIVE_COMMON_SUPPLY_FOUNDATION,
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
