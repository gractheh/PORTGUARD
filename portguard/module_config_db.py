"""
portguard/module_config_db.py — Thin read/write adapter for organization module settings.

Wraps the auth DB's organization_modules table so the agent pipeline can query
module config without importing from portguard.auth (avoids circular imports).

Public API
----------
ModuleConfigDB(db_path)
    .get_enabled_modules(org_id)                   → list[str]
    .set_module_enabled(org_id, module_id, enabled) → None
    .set_modules_bulk(org_id, updates)             → int (count updated)
    .get_all_module_states(org_id)                 → dict[str, bool]
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from portguard.db import get_engine

logger = logging.getLogger(__name__)

# Default auth DB path — same as auth.py uses.
_DEFAULT_AUTH_DB = os.getenv("PORTGUARD_AUTH_DB_PATH", "portguard_auth.db")


class ModuleConfigDB:
    """Read/write adapter for the organization_modules table in the auth DB.

    Falls back gracefully on all errors — if the DB is unavailable the pipeline
    runs with Layer 1 only, which is the safest default.
    """

    def __init__(self, db_path: str = _DEFAULT_AUTH_DB) -> None:
        try:
            self._engine, self._dialect = get_engine(db_path)
        except Exception as exc:
            logger.warning("ModuleConfigDB: could not connect to %s: %s", db_path, exc)
            self._engine = None
            self._dialect = "sqlite"

    @staticmethod
    def _utcnow() -> str:
        return datetime.now(timezone.utc).isoformat()

    # Modules that should be enabled by default for new or un-configured orgs.
    # Kept in sync with AuthDB._DEFAULT_ENABLED_MODULES.
    DEFAULT_ENABLED_MODULES: frozenset = frozenset({
        "FSC_COC",
        "RAINFOREST_ALLIANCE",
        "RSPO",
        "WRAP",
        "CONFLICT_MINERALS",
        "ISO_9001",
        "CE_MARKING",
    })

    def bootstrap_defaults(self, org_id: str) -> None:
        """Enable the default module set for an org that has no enabled modules.

        Idempotent — only runs if the org currently has zero enabled modules
        AND has existing rows that all have ``enabled_at IS NULL`` (i.e. they
        were inserted by the old all-disabled default before this change).
        Safe to call on every startup or settings load.
        """
        if self._engine is None:
            return
        try:
            from portguard.db import adapt_stmt
            with self._engine.begin() as conn:
                # Count enabled modules for this org
                enabled_count = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM organization_modules "
                        "WHERE organization_id = :org_id AND enabled = 1"
                    ),
                    {"org_id": org_id},
                ).scalar() or 0
                if enabled_count > 0:
                    return  # Already has some enabled — respect current state

                # Count rows that were never explicitly enabled (never-touched defaults)
                never_set = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM organization_modules "
                        "WHERE organization_id = :org_id AND enabled_at IS NULL"
                    ),
                    {"org_id": org_id},
                ).scalar() or 0
                if never_set == 0:
                    return  # Org either has no rows or intentionally disabled everything

                # Bootstrap: enable the default set
                now = self._utcnow()
                for module_id in self.DEFAULT_ENABLED_MODULES:
                    upd = (
                        "UPDATE organization_modules SET enabled = 1, set_by = 'system_default', "
                        "enabled_at = :now WHERE organization_id = :org_id AND module_id = :module_id"
                    )
                    conn.execute(
                        text(adapt_stmt(upd, self._dialect)),
                        {"now": now, "org_id": org_id, "module_id": module_id},
                    )
                logger.info(
                    "ModuleConfigDB: bootstrapped %d default modules for org %s",
                    len(self.DEFAULT_ENABLED_MODULES),
                    org_id,
                )
        except Exception as exc:
            logger.warning("ModuleConfigDB.bootstrap_defaults failed for %s: %s", org_id, exc)

    def get_enabled_modules(self, org_id: str) -> list[str]:
        """Return list of enabled module IDs for the given organization.

        Returns empty list (Layer 1 only) on any DB error.
        """
        if self._engine is None:
            return []
        try:
            with self._engine.connect() as conn:
                result = conn.execute(
                    text(
                        "SELECT module_id FROM organization_modules "
                        "WHERE organization_id = :org_id AND enabled = 1"
                    ),
                    {"org_id": org_id},
                )
                return [row[0] for row in result.fetchall()]
        except Exception as exc:
            logger.warning(
                "ModuleConfigDB.get_enabled_modules failed for org %s: %s", org_id, exc
            )
            return []

    def get_all_module_states(self, org_id: str) -> dict[str, bool]:
        """Return {module_id: enabled} dict for all modules in this org's config."""
        if self._engine is None:
            return {}
        try:
            with self._engine.connect() as conn:
                result = conn.execute(
                    text(
                        "SELECT module_id, enabled FROM organization_modules "
                        "WHERE organization_id = :org_id"
                    ),
                    {"org_id": org_id},
                )
                return {row[0]: bool(row[1]) for row in result.fetchall()}
        except Exception as exc:
            logger.warning(
                "ModuleConfigDB.get_all_module_states failed for org %s: %s", org_id, exc
            )
            return {}

    def set_module_enabled(
        self, org_id: str, module_id: str, enabled: bool, set_by: str = "user"
    ) -> None:
        """Enable or disable a single module for the given organization."""
        if self._engine is None:
            return
        now = self._utcnow()
        try:
            with self._engine.begin() as conn:
                # Upsert — insert row if not exists, then update
                raw_sql = (
                    "INSERT OR IGNORE INTO organization_modules "
                    "(organization_id, module_id, enabled, set_by) "
                    "VALUES (:org_id, :module_id, :enabled, :set_by)"
                )
                from portguard.db import adapt_stmt
                conn.execute(
                    text(adapt_stmt(raw_sql, self._dialect)),
                    {"org_id": org_id, "module_id": module_id,
                     "enabled": int(enabled), "set_by": set_by},
                )
                enabled_at_col = "enabled_at" if enabled else "disabled_at"
                conn.execute(
                    text(
                        f"UPDATE organization_modules "
                        f"SET enabled = :enabled, set_by = :set_by, {enabled_at_col} = :now "
                        f"WHERE organization_id = :org_id AND module_id = :module_id"
                    ),
                    {"enabled": int(enabled), "set_by": set_by,
                     "now": now, "org_id": org_id, "module_id": module_id},
                )
        except SQLAlchemyError as exc:
            logger.warning(
                "ModuleConfigDB.set_module_enabled failed (%s/%s): %s",
                org_id, module_id, exc,
            )

    def set_modules_bulk(
        self, org_id: str, updates: dict[str, bool], set_by: str = "user"
    ) -> int:
        """Bulk-update module enabled states.

        Parameters
        ----------
        updates:
            {module_id: enabled} dict.  Only toggleable modules are updated;
            Layer 1 module IDs in updates are silently ignored.

        Returns
        -------
        int
            Count of module rows actually updated.
        """
        from portguard.data.certification_modules import MODULE_BY_ID
        count = 0
        for module_id, enabled in updates.items():
            module = MODULE_BY_ID.get(module_id)
            if module is None or not module.toggleable:
                continue
            self.set_module_enabled(org_id, module_id, enabled, set_by=set_by)
            count += 1
        return count
