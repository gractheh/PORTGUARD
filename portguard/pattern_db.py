"""
portguard/pattern_db.py — SQLAlchemy-backed data layer for the Localized Pattern Learning system.

This module owns all database I/O for the LPL system.  The PatternDB class exposes a
clean, typed Python API that the rest of the system uses; no SQL leaks out of this file.

Architecture overview (from docs/pattern_learning_architecture.md)
------------------------------------------------------------------
Five tables:

  shipment_history      — canonical record for every analysis run (one row per POST /analyze)
  pattern_outcomes      — officer-recorded verdicts (CONFIRMED_FRAUD | CLEARED | UNRESOLVED)
  shipper_profiles      — Bayesian reputation scores and decay-weighted event counts per shipper
  consignee_profiles    — same structure as shipper_profiles, keyed on consignee
  route_risk_profiles   — fraud rates per origin-country → port-of-entry corridor
  hs_code_baselines     — Welford running mean/variance of declared unit values per HS prefix
  schema_migrations     — forward-only migration log; tracks applied migrations by name

Public API
----------
PatternDB(db_path)
    .record_shipment(fingerprint, decision, rules_fired, confidence)  → analysis_id (str)
    .record_outcome(analysis_id, outcome, officer_id, notes, case_ref)
    .get_shipper_profile(shipper_name)    → ShipperProfile
    .get_consignee_profile(consignee_name) → ConsigneeProfile
    .get_route_risk(origin, destination)   → RouteRisk
    .get_hs_baseline(hs_code_prefix)       → HSBaseline
    .close()

Thread safety
-------------
SQLAlchemy's connection pool manages concurrency.  Each write operation uses
engine.begin() for automatic commit/rollback.  Read operations use engine.connect().

Exceptions
----------
PatternDBError      — base class
RecordNotFoundError — analysis_id lookup failed
DuplicateOutcomeError — outcome already recorded for this analysis (409-equivalent)
InvalidOutcomeError — outcome value not in VALID_OUTCOMES

Design notes
------------
- WAL mode and foreign keys are applied via the SQLite connect event listener in db.py.
- All timestamps are ISO-8601 UTC strings.  datetime objects are never stored.
- Entity names are normalized and SHA-256-hashed before any DB lookup.
- Temporal decay uses λ = 0.023 (30-day half-life).
- Welford's algorithm is used for HS value running statistics.
- Bayesian Beta scoring:
    Shipper/consignee  — informative innocent prior (α₀=1, β₀=5)
    Route              — Jeffrey's non-informative prior (α₀=β₀=0.5)
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError as _SQLAlchemyError

from portguard.db import adapt_stmt, get_engine, split_migration_sql

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DECAY_LAMBDA: float = 0.023          # 30-day half-life: exp(-0.023*30) ≈ 0.50
PATTERN_WEIGHT: float = 0.35         # max fraction pattern learning can add to rule score
COLD_START_THRESHOLD: int = 3        # fewer than this many analyses → cold start
COLD_START_PENALTY: float = 0.5      # reduce pattern contribution by this fraction when cold
AUTO_TRUST_CLEARED_THRESHOLD: float = 20.0   # weighted clears before auto-trust fires
MIN_HS_SAMPLES_FOR_ANOMALY: int = 10  # below this, value anomaly score is 0

# Outcome constants
OUTCOME_CONFIRMED_FRAUD = "CONFIRMED_FRAUD"
OUTCOME_CLEARED = "CLEARED"
OUTCOME_UNRESOLVED = "UNRESOLVED"
VALID_OUTCOMES = frozenset({OUTCOME_CONFIRMED_FRAUD, OUTCOME_CLEARED, OUTCOME_UNRESOLVED})

# Legal suffixes stripped during entity name normalization (§2.9 of architecture doc)
_LEGAL_SUFFIXES: frozenset[str] = frozenset({
    "ltd", "limited", "co", "corp", "corporation", "inc", "incorporated",
    "llc", "pte", "sa", "gmbh", "bv", "nv", "srl", "pty", "plc",
})

# ---------------------------------------------------------------------------
# Return-value dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ShipperProfile:
    """Aggregated reputation data for a single shipper entity."""

    shipper_key: str
    shipper_name: str
    reputation_score: float
    history_count: int
    weighted_analyses: float
    total_confirmed_fraud: int
    total_cleared: int
    is_trusted: bool
    exists: bool


@dataclass
class ConsigneeProfile:
    """Aggregated reputation data for a single consignee entity."""

    consignee_key: str
    consignee_name: str
    reputation_score: float
    history_count: int
    weighted_analyses: float
    total_confirmed_fraud: int
    total_cleared: int
    is_trusted: bool
    exists: bool


@dataclass
class RouteRisk:
    """Historical fraud rate for an origin → port-of-entry corridor."""

    route_key: str
    origin_iso2: str
    port_of_entry: str
    fraud_rate: float
    total_analyses: int
    total_confirmed_fraud: int
    exists: bool


@dataclass
class HSBaseline:
    """Running distribution of declared unit values for an HS code prefix."""

    hs_prefix: str
    sample_count: int
    mean_unit_value: float
    std_dev: Optional[float]
    min_value: Optional[float]
    max_value: Optional[float]
    exists: bool


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PatternDBError(Exception):
    """Base class for all PatternDB errors."""


class RecordNotFoundError(PatternDBError):
    """Raised when an analysis_id is not found in shipment_history."""


class DuplicateOutcomeError(PatternDBError):
    """Raised when a resolved outcome already exists for the given analysis_id."""


class InvalidOutcomeError(PatternDBError):
    """Raised when the outcome value is not one of VALID_OUTCOMES."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_since(iso_timestamp: str) -> float:
    then = datetime.fromisoformat(iso_timestamp)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - then
    return max(0.0, delta.total_seconds() / 86400.0)


def _decay_weight(days_ago: float) -> float:
    return math.exp(-DECAY_LAMBDA * days_ago)


def _normalize_entity_name(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", errors="ignore").decode("ascii")
    lowered = ascii_str.lower()
    cleaned = re.sub(r"[^\w\s-]", " ", lowered)
    tokens = cleaned.split()
    filtered = [t for t in tokens if t not in _LEGAL_SUFFIXES]
    return " ".join(filtered).strip()


def _entity_key(name: str) -> str:
    normalized = _normalize_entity_name(name)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _compute_shipper_reputation(
    weighted_confirmed_fraud: float,
    weighted_cleared: float,
) -> float:
    alpha = weighted_confirmed_fraud + 1.0
    beta = weighted_cleared + 5.0
    return alpha / (alpha + beta)


def _compute_route_fraud_rate(
    weighted_confirmed_fraud: float,
    weighted_analyses: float,
) -> float:
    alpha = weighted_confirmed_fraud + 0.5
    beta = (weighted_analyses - weighted_confirmed_fraud) + 0.5
    beta = max(beta, 0.5)
    return alpha / (alpha + beta)


def _welford_update(
    n: int,
    mean: float,
    m2: float,
    new_value: float,
) -> tuple[int, float, float]:
    n += 1
    delta = new_value - mean
    mean += delta / n
    delta2 = new_value - mean
    m2 += delta * delta2
    return n, mean, m2


def _welford_stddev(n: int, m2: float) -> Optional[float]:
    if n < 2:
        return None
    variance = m2 / (n - 1)
    return math.sqrt(max(variance, 0.0))


# ---------------------------------------------------------------------------
# Schema — DDL migrations
# ---------------------------------------------------------------------------
#
# Each migration is a (name, sql_or_stmts) tuple.
#   - str  → split_migration_sql() splits it into individual statements
#   - list[str] → each statement adapted individually via adapt_stmt()
#
# Migrations are applied in order and recorded by name in schema_migrations for
# idempotency.  NEVER modify a migration that has already been applied.

_MIGRATIONS: list[tuple[str, str | list[str]]] = [
    (
        "001_initial_schema",
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            migration_name  TEXT PRIMARY KEY,
            applied_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS shipment_history (
            analysis_id             TEXT PRIMARY KEY,
            analyzed_at             TEXT NOT NULL,
            shipper_name            TEXT,
            shipper_key             TEXT,
            consignee_name          TEXT,
            consignee_key           TEXT,
            origin_iso2             TEXT,
            destination_iso2        TEXT DEFAULT 'US',
            port_of_entry           TEXT,
            route_key               TEXT,
            carrier                 TEXT,
            hs_codes                TEXT NOT NULL DEFAULT '[]',
            hs_chapter_primary      TEXT,
            declared_value_usd      REAL,
            quantity                REAL,
            unit_value_usd          REAL,
            gross_weight_kg         REAL,
            incoterms               TEXT,
            rule_risk_score         REAL NOT NULL,
            rule_decision           TEXT NOT NULL,
            rule_confidence         TEXT NOT NULL,
            rules_fired             TEXT NOT NULL DEFAULT '[]',
            inconsistency_count     INTEGER NOT NULL DEFAULT 0,
            missing_field_count     INTEGER NOT NULL DEFAULT 0,
            pattern_score               REAL,
            pattern_shipper_score       REAL,
            pattern_consignee_score     REAL,
            pattern_route_score         REAL,
            pattern_value_z_score       REAL,
            pattern_flag_frequency      REAL,
            pattern_history_depth       INTEGER,
            pattern_cold_start          INTEGER NOT NULL DEFAULT 1,
            final_risk_score        REAL NOT NULL,
            final_decision          TEXT NOT NULL,
            final_confidence        TEXT NOT NULL,
            outcome_cleared         INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_history_shipper_key
            ON shipment_history(shipper_key, analyzed_at);
        CREATE INDEX IF NOT EXISTS idx_history_consignee_key
            ON shipment_history(consignee_key, analyzed_at);
        CREATE INDEX IF NOT EXISTS idx_history_route_key
            ON shipment_history(route_key, analyzed_at);
        CREATE INDEX IF NOT EXISTS idx_history_hs_primary
            ON shipment_history(hs_chapter_primary, analyzed_at);
        CREATE INDEX IF NOT EXISTS idx_history_analyzed_at
            ON shipment_history(analyzed_at);

        CREATE TABLE IF NOT EXISTS pattern_outcomes (
            outcome_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_id     TEXT NOT NULL UNIQUE
                                REFERENCES shipment_history(analysis_id) ON DELETE CASCADE,
            recorded_at     TEXT NOT NULL,
            officer_id      TEXT,
            outcome         TEXT NOT NULL
                                CHECK(outcome IN ('CONFIRMED_FRAUD','CLEARED','UNRESOLVED')),
            outcome_notes   TEXT,
            case_reference  TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_outcomes_analysis
            ON pattern_outcomes(analysis_id);
        CREATE INDEX IF NOT EXISTS idx_outcomes_outcome
            ON pattern_outcomes(outcome, recorded_at);

        CREATE TABLE IF NOT EXISTS shipper_profiles (
            shipper_key                 TEXT PRIMARY KEY,
            shipper_name                TEXT,
            first_seen_at               TEXT NOT NULL,
            last_seen_at                TEXT NOT NULL,
            total_analyses              INTEGER NOT NULL DEFAULT 0,
            total_flagged               INTEGER NOT NULL DEFAULT 0,
            total_confirmed_fraud       INTEGER NOT NULL DEFAULT 0,
            total_cleared               INTEGER NOT NULL DEFAULT 0,
            total_unresolved            INTEGER NOT NULL DEFAULT 0,
            weighted_analyses           REAL NOT NULL DEFAULT 0.0,
            weighted_flagged            REAL NOT NULL DEFAULT 0.0,
            weighted_confirmed_fraud    REAL NOT NULL DEFAULT 0.0,
            weighted_cleared            REAL NOT NULL DEFAULT 0.0,
            reputation_score            REAL NOT NULL DEFAULT 0.16666666666,
            last_score_update           TEXT NOT NULL,
            is_trusted                  INTEGER NOT NULL DEFAULT 0,
            trust_set_at                TEXT,
            trust_set_by                TEXT
        );

        CREATE TABLE IF NOT EXISTS consignee_profiles (
            consignee_key               TEXT PRIMARY KEY,
            consignee_name              TEXT,
            first_seen_at               TEXT NOT NULL,
            last_seen_at                TEXT NOT NULL,
            total_analyses              INTEGER NOT NULL DEFAULT 0,
            total_flagged               INTEGER NOT NULL DEFAULT 0,
            total_confirmed_fraud       INTEGER NOT NULL DEFAULT 0,
            total_cleared               INTEGER NOT NULL DEFAULT 0,
            total_unresolved            INTEGER NOT NULL DEFAULT 0,
            weighted_analyses           REAL NOT NULL DEFAULT 0.0,
            weighted_flagged            REAL NOT NULL DEFAULT 0.0,
            weighted_confirmed_fraud    REAL NOT NULL DEFAULT 0.0,
            weighted_cleared            REAL NOT NULL DEFAULT 0.0,
            reputation_score            REAL NOT NULL DEFAULT 0.16666666666,
            last_score_update           TEXT NOT NULL,
            is_trusted                  INTEGER NOT NULL DEFAULT 0,
            trust_set_at                TEXT,
            trust_set_by                TEXT
        );

        CREATE TABLE IF NOT EXISTS route_risk_profiles (
            route_key               TEXT PRIMARY KEY,
            origin_iso2             TEXT NOT NULL,
            port_of_entry           TEXT NOT NULL,
            first_seen_at           TEXT NOT NULL,
            last_seen_at            TEXT NOT NULL,
            total_analyses          INTEGER NOT NULL DEFAULT 0,
            total_flagged           INTEGER NOT NULL DEFAULT 0,
            total_confirmed_fraud   INTEGER NOT NULL DEFAULT 0,
            total_cleared           INTEGER NOT NULL DEFAULT 0,
            weighted_analyses       REAL NOT NULL DEFAULT 0.0,
            weighted_flagged        REAL NOT NULL DEFAULT 0.0,
            weighted_confirmed_fraud REAL NOT NULL DEFAULT 0.0,
            weighted_cleared        REAL NOT NULL DEFAULT 0.0,
            fraud_rate              REAL NOT NULL DEFAULT 0.5,
            last_score_update       TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS hs_code_baselines (
            hs_prefix           TEXT PRIMARY KEY,
            sample_count        INTEGER NOT NULL DEFAULT 0,
            running_mean        REAL NOT NULL DEFAULT 0.0,
            running_m2          REAL NOT NULL DEFAULT 0.0,
            running_min         REAL,
            running_max         REAL,
            cached_stddev       REAL,
            last_updated        TEXT NOT NULL
        );
        """,
    ),
    (
        "002_add_organization_id_columns",
        """
        ALTER TABLE shipment_history ADD COLUMN organization_id TEXT NOT NULL DEFAULT '__system__';
        ALTER TABLE pattern_outcomes ADD COLUMN organization_id TEXT NOT NULL DEFAULT '__system__';
        CREATE INDEX IF NOT EXISTS idx_history_org_id ON shipment_history(organization_id);
        CREATE INDEX IF NOT EXISTS idx_outcomes_org_id ON pattern_outcomes(organization_id);
        """,
    ),
    (
        "003_rebuild_profile_tables_composite_pk",
        [
            # ---- shipper_profiles ----
            """CREATE TABLE IF NOT EXISTS shipper_profiles_new (
                organization_id             TEXT NOT NULL DEFAULT '__system__',
                shipper_key                 TEXT NOT NULL,
                shipper_name                TEXT,
                first_seen_at               TEXT NOT NULL,
                last_seen_at                TEXT NOT NULL,
                total_analyses              INTEGER NOT NULL DEFAULT 0,
                total_flagged               INTEGER NOT NULL DEFAULT 0,
                total_confirmed_fraud       INTEGER NOT NULL DEFAULT 0,
                total_cleared               INTEGER NOT NULL DEFAULT 0,
                total_unresolved            INTEGER NOT NULL DEFAULT 0,
                weighted_analyses           REAL NOT NULL DEFAULT 0.0,
                weighted_flagged            REAL NOT NULL DEFAULT 0.0,
                weighted_confirmed_fraud    REAL NOT NULL DEFAULT 0.0,
                weighted_cleared            REAL NOT NULL DEFAULT 0.0,
                reputation_score            REAL NOT NULL DEFAULT 0.16666666666,
                last_score_update           TEXT NOT NULL,
                is_trusted                  INTEGER NOT NULL DEFAULT 0,
                trust_set_at                TEXT,
                trust_set_by                TEXT,
                PRIMARY KEY (organization_id, shipper_key)
            )""",
            """INSERT OR IGNORE INTO shipper_profiles_new
               SELECT '__system__', shipper_key, shipper_name,
                      first_seen_at, last_seen_at,
                      total_analyses, total_flagged,
                      total_confirmed_fraud, total_cleared, total_unresolved,
                      weighted_analyses, weighted_flagged,
                      weighted_confirmed_fraud, weighted_cleared,
                      reputation_score, last_score_update,
                      is_trusted, trust_set_at, trust_set_by
               FROM shipper_profiles""",
            "DROP TABLE shipper_profiles",
            "ALTER TABLE shipper_profiles_new RENAME TO shipper_profiles",

            # ---- consignee_profiles ----
            """CREATE TABLE IF NOT EXISTS consignee_profiles_new (
                organization_id             TEXT NOT NULL DEFAULT '__system__',
                consignee_key               TEXT NOT NULL,
                consignee_name              TEXT,
                first_seen_at               TEXT NOT NULL,
                last_seen_at                TEXT NOT NULL,
                total_analyses              INTEGER NOT NULL DEFAULT 0,
                total_flagged               INTEGER NOT NULL DEFAULT 0,
                total_confirmed_fraud       INTEGER NOT NULL DEFAULT 0,
                total_cleared               INTEGER NOT NULL DEFAULT 0,
                total_unresolved            INTEGER NOT NULL DEFAULT 0,
                weighted_analyses           REAL NOT NULL DEFAULT 0.0,
                weighted_flagged            REAL NOT NULL DEFAULT 0.0,
                weighted_confirmed_fraud    REAL NOT NULL DEFAULT 0.0,
                weighted_cleared            REAL NOT NULL DEFAULT 0.0,
                reputation_score            REAL NOT NULL DEFAULT 0.16666666666,
                last_score_update           TEXT NOT NULL,
                is_trusted                  INTEGER NOT NULL DEFAULT 0,
                trust_set_at                TEXT,
                trust_set_by                TEXT,
                PRIMARY KEY (organization_id, consignee_key)
            )""",
            """INSERT OR IGNORE INTO consignee_profiles_new
               SELECT '__system__', consignee_key, consignee_name,
                      first_seen_at, last_seen_at,
                      total_analyses, total_flagged,
                      total_confirmed_fraud, total_cleared, total_unresolved,
                      weighted_analyses, weighted_flagged,
                      weighted_confirmed_fraud, weighted_cleared,
                      reputation_score, last_score_update,
                      is_trusted, trust_set_at, trust_set_by
               FROM consignee_profiles""",
            "DROP TABLE consignee_profiles",
            "ALTER TABLE consignee_profiles_new RENAME TO consignee_profiles",

            # ---- route_risk_profiles ----
            """CREATE TABLE IF NOT EXISTS route_risk_profiles_new (
                organization_id         TEXT NOT NULL DEFAULT '__system__',
                route_key               TEXT NOT NULL,
                origin_iso2             TEXT NOT NULL,
                port_of_entry           TEXT NOT NULL,
                first_seen_at           TEXT NOT NULL,
                last_seen_at            TEXT NOT NULL,
                total_analyses          INTEGER NOT NULL DEFAULT 0,
                total_flagged           INTEGER NOT NULL DEFAULT 0,
                total_confirmed_fraud   INTEGER NOT NULL DEFAULT 0,
                total_cleared           INTEGER NOT NULL DEFAULT 0,
                weighted_analyses       REAL NOT NULL DEFAULT 0.0,
                weighted_flagged        REAL NOT NULL DEFAULT 0.0,
                weighted_confirmed_fraud REAL NOT NULL DEFAULT 0.0,
                weighted_cleared        REAL NOT NULL DEFAULT 0.0,
                fraud_rate              REAL NOT NULL DEFAULT 0.5,
                last_score_update       TEXT NOT NULL,
                PRIMARY KEY (organization_id, route_key)
            )""",
            """INSERT OR IGNORE INTO route_risk_profiles_new
               SELECT '__system__', route_key, origin_iso2, port_of_entry,
                      first_seen_at, last_seen_at,
                      total_analyses, total_flagged,
                      total_confirmed_fraud, total_cleared,
                      weighted_analyses, weighted_flagged,
                      weighted_confirmed_fraud, weighted_cleared,
                      fraud_rate, last_score_update
               FROM route_risk_profiles""",
            "DROP TABLE route_risk_profiles",
            "ALTER TABLE route_risk_profiles_new RENAME TO route_risk_profiles",

            # ---- hs_code_baselines ----
            """CREATE TABLE IF NOT EXISTS hs_code_baselines_new (
                organization_id TEXT NOT NULL DEFAULT '__system__',
                hs_prefix       TEXT NOT NULL,
                sample_count    INTEGER NOT NULL DEFAULT 0,
                running_mean    REAL NOT NULL DEFAULT 0.0,
                running_m2      REAL NOT NULL DEFAULT 0.0,
                running_min     REAL,
                running_max     REAL,
                cached_stddev   REAL,
                last_updated    TEXT NOT NULL,
                PRIMARY KEY (organization_id, hs_prefix)
            )""",
            """INSERT OR IGNORE INTO hs_code_baselines_new
               SELECT '__system__', hs_prefix, sample_count,
                      running_mean, running_m2,
                      running_min, running_max, cached_stddev, last_updated
               FROM hs_code_baselines""",
            "DROP TABLE hs_code_baselines",
            "ALTER TABLE hs_code_baselines_new RENAME TO hs_code_baselines",

            # Recreate indexes on rebuilt tables
            "CREATE INDEX IF NOT EXISTS idx_shipper_profiles_org ON shipper_profiles(organization_id)",
            "CREATE INDEX IF NOT EXISTS idx_consignee_profiles_org ON consignee_profiles(organization_id)",
            "CREATE INDEX IF NOT EXISTS idx_route_profiles_org ON route_risk_profiles(organization_id)",
            "CREATE INDEX IF NOT EXISTS idx_hs_baselines_org ON hs_code_baselines(organization_id)",
        ],
    ),
    (
        "004_add_report_payload",
        "ALTER TABLE shipment_history ADD COLUMN report_payload TEXT;",
    ),
]


# ---------------------------------------------------------------------------
# Shipment fingerprint dataclass
# ---------------------------------------------------------------------------


@dataclass
class ShipmentFingerprint:
    """All fields extracted from a shipment document that the pattern engine uses."""

    organization_id: str = "__system__"
    shipper_name: Optional[str] = None
    consignee_name: Optional[str] = None
    origin_iso2: Optional[str] = None
    destination_iso2: Optional[str] = "US"
    port_of_entry: Optional[str] = None
    carrier: Optional[str] = None
    hs_codes: List[str] = field(default_factory=list)
    declared_value_usd: Optional[float] = None
    quantity: Optional[float] = None
    gross_weight_kg: Optional[float] = None
    incoterms: Optional[str] = None
    inconsistency_count: int = 0
    missing_field_count: int = 0
    rule_risk_score: float = 0.0
    rule_decision: str = "APPROVE"
    rule_confidence: str = "LOW"
    rules_fired: List[dict] = field(default_factory=list)
    pattern_score: Optional[float] = None
    pattern_shipper_score: Optional[float] = None
    pattern_consignee_score: Optional[float] = None
    pattern_route_score: Optional[float] = None
    pattern_value_z_score: Optional[float] = None
    pattern_flag_frequency: Optional[float] = None
    pattern_history_depth: Optional[int] = None
    pattern_cold_start: bool = True
    final_risk_score: float = 0.0
    final_decision: str = "APPROVE"
    final_confidence: str = "LOW"


# ---------------------------------------------------------------------------
# PatternDB
# ---------------------------------------------------------------------------


class PatternDB:
    """SQLAlchemy-backed data layer for the Localized Pattern Learning system.

    Uses PostgreSQL when DATABASE_URL is set; falls back to SQLite at
    *db_path* for local development.

    Parameters
    ----------
    db_path:
        Path to the SQLite file.  Ignored when DATABASE_URL is set to a
        PostgreSQL URL.  Pass ``":memory:"`` for an in-memory database
        (useful in tests with SQLite only).
    """

    def __init__(self, db_path: str | Path = "portguard_patterns.db") -> None:
        self._db_path = str(db_path)
        self._engine, self._dialect = get_engine(self._db_path)
        self._run_migrations()
        logger.info(
            "PatternDB initialized (dialect=%s, path=%s)",
            self._dialect,
            self._db_path if self._dialect == "sqlite" else "postgresql",
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Dispose the engine connection pool."""
        try:
            self._engine.dispose()
        except Exception:
            pass

    def __enter__(self) -> "PatternDB":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Migrations
    # ------------------------------------------------------------------

    def _run_migrations(self) -> None:
        """Apply any pending schema migrations in order.

        Each migration is identified by a unique name stored in
        ``schema_migrations``.  If the migration has already been applied,
        it is skipped.  Migrations run inside individual transactions so a
        failure leaves the database in a consistent prior state.
        """
        # Bootstrap: ensure schema_migrations exists before querying it.
        bootstrap = adapt_stmt(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_name  TEXT PRIMARY KEY,
                applied_at      TEXT NOT NULL
            )""",
            self._dialect,
        )
        with self._engine.begin() as conn:
            conn.execute(text(bootstrap))

        # Fetch already-applied migrations.
        with self._engine.connect() as conn:
            result = conn.execute(text("SELECT migration_name FROM schema_migrations"))
            applied = {row[0] for row in result}

        for name, sql_or_stmts in _MIGRATIONS:
            if name in applied:
                continue
            logger.info("Applying migration: %s", name)
            try:
                with self._engine.begin() as conn:
                    if isinstance(sql_or_stmts, list):
                        for stmt in sql_or_stmts:
                            adapted = adapt_stmt(stmt.strip(), self._dialect)
                            if adapted:
                                conn.execute(text(adapted))
                    else:
                        for stmt in split_migration_sql(sql_or_stmts, self._dialect):
                            conn.execute(text(stmt))

                    conn.execute(
                        text(
                            "INSERT INTO schema_migrations(migration_name, applied_at)"
                            " VALUES (:name, :at)"
                        ),
                        {"name": name, "at": _utcnow()},
                    )
                logger.info("Migration applied: %s", name)
            except PatternDBError:
                raise
            except Exception as exc:
                raise PatternDBError(
                    f"Failed to apply migration '{name}': {exc}"
                ) from exc

    # ------------------------------------------------------------------
    # Public API — write
    # ------------------------------------------------------------------

    def record_shipment(
        self,
        fingerprint: ShipmentFingerprint,
        decision: str,
        rules_fired: list[dict],
        confidence: str,
    ) -> str:
        """Record a completed analysis to shipment_history and update profiles.

        Returns
        -------
        str
            The ``analysis_id`` (UUID v4) assigned to this analysis.

        Raises
        ------
        PatternDBError
            If the database write fails for any reason.
        """
        analysis_id = str(uuid.uuid4())
        now = _utcnow()

        shipper_key = _entity_key(fingerprint.shipper_name) if fingerprint.shipper_name else None
        consignee_key = _entity_key(fingerprint.consignee_name) if fingerprint.consignee_name else None

        route_key: Optional[str] = None
        if fingerprint.origin_iso2 and fingerprint.port_of_entry:
            route_key = f"{fingerprint.origin_iso2}|{fingerprint.port_of_entry}"

        hs_chapter_primary: Optional[str] = None
        if fingerprint.hs_codes:
            hs_chapter_primary = fingerprint.hs_codes[0][:2]

        unit_value_usd: Optional[float] = None
        if fingerprint.declared_value_usd and fingerprint.quantity and fingerprint.quantity > 0:
            unit_value_usd = fingerprint.declared_value_usd / fingerprint.quantity

        fingerprint.final_decision = decision
        fingerprint.final_confidence = confidence
        is_flagged = decision != "APPROVE"

        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text("""
                    INSERT INTO shipment_history (
                        analysis_id, analyzed_at, organization_id,
                        shipper_name, shipper_key,
                        consignee_name, consignee_key,
                        origin_iso2, destination_iso2, port_of_entry, route_key,
                        carrier, hs_codes, hs_chapter_primary,
                        declared_value_usd, quantity, unit_value_usd,
                        gross_weight_kg, incoterms,
                        rule_risk_score, rule_decision, rule_confidence,
                        rules_fired, inconsistency_count, missing_field_count,
                        pattern_score, pattern_shipper_score, pattern_consignee_score,
                        pattern_route_score, pattern_value_z_score,
                        pattern_flag_frequency, pattern_history_depth, pattern_cold_start,
                        final_risk_score, final_decision, final_confidence
                    ) VALUES (
                        :analysis_id, :analyzed_at, :organization_id,
                        :shipper_name, :shipper_key,
                        :consignee_name, :consignee_key,
                        :origin_iso2, :destination_iso2, :port_of_entry, :route_key,
                        :carrier, :hs_codes, :hs_chapter_primary,
                        :declared_value_usd, :quantity, :unit_value_usd,
                        :gross_weight_kg, :incoterms,
                        :rule_risk_score, :rule_decision, :rule_confidence,
                        :rules_fired, :inconsistency_count, :missing_field_count,
                        :pattern_score, :pattern_shipper_score, :pattern_consignee_score,
                        :pattern_route_score, :pattern_value_z_score,
                        :pattern_flag_frequency, :pattern_history_depth, :pattern_cold_start,
                        :final_risk_score, :final_decision, :final_confidence
                    )
                    """),
                    {
                        "analysis_id": analysis_id,
                        "analyzed_at": now,
                        "organization_id": fingerprint.organization_id,
                        "shipper_name": fingerprint.shipper_name,
                        "shipper_key": shipper_key,
                        "consignee_name": fingerprint.consignee_name,
                        "consignee_key": consignee_key,
                        "origin_iso2": fingerprint.origin_iso2,
                        "destination_iso2": fingerprint.destination_iso2,
                        "port_of_entry": fingerprint.port_of_entry,
                        "route_key": route_key,
                        "carrier": fingerprint.carrier,
                        "hs_codes": json.dumps(fingerprint.hs_codes),
                        "hs_chapter_primary": hs_chapter_primary,
                        "declared_value_usd": fingerprint.declared_value_usd,
                        "quantity": fingerprint.quantity,
                        "unit_value_usd": unit_value_usd,
                        "gross_weight_kg": fingerprint.gross_weight_kg,
                        "incoterms": fingerprint.incoterms,
                        "rule_risk_score": fingerprint.rule_risk_score,
                        "rule_decision": fingerprint.rule_decision,
                        "rule_confidence": fingerprint.rule_confidence,
                        "rules_fired": json.dumps(rules_fired),
                        "inconsistency_count": fingerprint.inconsistency_count,
                        "missing_field_count": fingerprint.missing_field_count,
                        "pattern_score": fingerprint.pattern_score,
                        "pattern_shipper_score": fingerprint.pattern_shipper_score,
                        "pattern_consignee_score": fingerprint.pattern_consignee_score,
                        "pattern_route_score": fingerprint.pattern_route_score,
                        "pattern_value_z_score": fingerprint.pattern_value_z_score,
                        "pattern_flag_frequency": fingerprint.pattern_flag_frequency,
                        "pattern_history_depth": fingerprint.pattern_history_depth,
                        "pattern_cold_start": int(fingerprint.pattern_cold_start),
                        "final_risk_score": fingerprint.final_risk_score,
                        "final_decision": decision,
                        "final_confidence": confidence,
                    },
                )

                org_id = fingerprint.organization_id

                if shipper_key and fingerprint.shipper_name:
                    self._upsert_entity_profile(
                        conn,
                        table="shipper_profiles",
                        key_col="shipper_key",
                        name_col="shipper_name",
                        key_val=shipper_key,
                        name_val=fingerprint.shipper_name,
                        is_flagged=is_flagged,
                        now=now,
                        organization_id=org_id,
                    )

                if consignee_key and fingerprint.consignee_name:
                    self._upsert_entity_profile(
                        conn,
                        table="consignee_profiles",
                        key_col="consignee_key",
                        name_col="consignee_name",
                        key_val=consignee_key,
                        name_val=fingerprint.consignee_name,
                        is_flagged=is_flagged,
                        now=now,
                        organization_id=org_id,
                    )

                if route_key and fingerprint.origin_iso2 and fingerprint.port_of_entry:
                    self._upsert_route_profile(
                        conn,
                        route_key=route_key,
                        origin_iso2=fingerprint.origin_iso2,
                        port_of_entry=fingerprint.port_of_entry,
                        is_flagged=is_flagged,
                        now=now,
                        organization_id=org_id,
                    )

                if unit_value_usd is not None and unit_value_usd > 0:
                    for hs_code in fingerprint.hs_codes:
                        hs_prefix = hs_code[:7].rstrip(".")
                        if hs_prefix:
                            self._update_hs_baseline(
                                conn, hs_prefix, unit_value_usd, now, organization_id=org_id
                            )

            logger.debug("Recorded analysis %s (decision=%s)", analysis_id, decision)
            return analysis_id

        except _SQLAlchemyError as exc:
            raise PatternDBError(f"Failed to record shipment: {exc}") from exc

    def record_outcome(
        self,
        analysis_id: str,
        outcome: str,
        officer_id: Optional[str] = None,
        notes: Optional[str] = None,
        case_reference: Optional[str] = None,
    ) -> None:
        """Record an officer's verdict for a previously analyzed shipment.

        Raises
        ------
        InvalidOutcomeError, RecordNotFoundError, DuplicateOutcomeError, PatternDBError
        """
        if outcome not in VALID_OUTCOMES:
            raise InvalidOutcomeError(
                f"Invalid outcome '{outcome}'. Must be one of: {sorted(VALID_OUTCOMES)}"
            )

        try:
            with self._engine.begin() as conn:
                row = conn.execute(
                    text(
                        "SELECT analysis_id, shipper_key, consignee_key, route_key, "
                        "       hs_codes, unit_value_usd, analyzed_at, final_decision, "
                        "       organization_id "
                        "FROM shipment_history WHERE analysis_id = :analysis_id"
                    ),
                    {"analysis_id": analysis_id},
                ).mappings().fetchone()

                if row is None:
                    raise RecordNotFoundError(f"No analysis found with id '{analysis_id}'")

                existing = conn.execute(
                    text(
                        "SELECT outcome_id, outcome FROM pattern_outcomes"
                        " WHERE analysis_id = :analysis_id"
                    ),
                    {"analysis_id": analysis_id},
                ).mappings().fetchone()

                if existing is not None:
                    if existing["outcome"] != OUTCOME_UNRESOLVED:
                        raise DuplicateOutcomeError(
                            f"Analysis '{analysis_id}' already has a resolved outcome "
                            f"('{existing['outcome']}').  Resolved outcomes are immutable."
                        )
                    update_existing = True
                else:
                    update_existing = False

                now = _utcnow()
                days_ago = _days_since(row["analyzed_at"])
                dw = _decay_weight(days_ago)

                if update_existing:
                    conn.execute(
                        text(
                            """UPDATE pattern_outcomes
                               SET outcome = :outcome,
                                   recorded_at = :now,
                                   officer_id = :officer_id,
                                   outcome_notes = :notes,
                                   case_reference = :case_ref
                               WHERE analysis_id = :analysis_id"""
                        ),
                        {
                            "outcome": outcome,
                            "now": now,
                            "officer_id": officer_id,
                            "notes": notes,
                            "case_ref": case_reference,
                            "analysis_id": analysis_id,
                        },
                    )
                else:
                    conn.execute(
                        text(
                            """INSERT INTO pattern_outcomes
                               (analysis_id, organization_id, recorded_at, officer_id, outcome,
                                outcome_notes, case_reference)
                               VALUES (:analysis_id, :org_id, :now, :officer_id, :outcome,
                                       :notes, :case_ref)"""
                        ),
                        {
                            "analysis_id": analysis_id,
                            "org_id": row["organization_id"],
                            "now": now,
                            "officer_id": officer_id,
                            "outcome": outcome,
                            "notes": notes,
                            "case_ref": case_reference,
                        },
                    )

                if outcome == OUTCOME_CONFIRMED_FRAUD:
                    self._apply_fraud_outcome(conn, row, dw, now)
                elif outcome == OUTCOME_CLEARED:
                    self._apply_cleared_outcome(conn, row, dw, now)
                    conn.execute(
                        text(
                            "UPDATE shipment_history SET outcome_cleared = 1"
                            " WHERE analysis_id = :analysis_id"
                        ),
                        {"analysis_id": analysis_id},
                    )

                logger.info(
                    "Outcome %s recorded for analysis %s (officer=%s)",
                    outcome, analysis_id, officer_id,
                )

        except PatternDBError:
            raise
        except _SQLAlchemyError as exc:
            raise PatternDBError(f"Failed to record outcome: {exc}") from exc

    # ------------------------------------------------------------------
    # Public API — read
    # ------------------------------------------------------------------

    def get_shipper_profile(
        self, shipper_name: str, organization_id: str = "__system__"
    ) -> ShipperProfile:
        """Retrieve the pattern profile for a shipper entity."""
        key = _entity_key(shipper_name)
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT * FROM shipper_profiles"
                    " WHERE organization_id = :org_id AND shipper_key = :key"
                ),
                {"org_id": organization_id, "key": key},
            ).mappings().fetchone()

        if row is None:
            return ShipperProfile(
                shipper_key=key,
                shipper_name=shipper_name,
                reputation_score=_compute_shipper_reputation(0.0, 0.0),
                history_count=0,
                weighted_analyses=0.0,
                total_confirmed_fraud=0,
                total_cleared=0,
                is_trusted=False,
                exists=False,
            )

        return ShipperProfile(
            shipper_key=row["shipper_key"],
            shipper_name=row["shipper_name"] or shipper_name,
            reputation_score=row["reputation_score"],
            history_count=row["total_analyses"],
            weighted_analyses=row["weighted_analyses"],
            total_confirmed_fraud=row["total_confirmed_fraud"],
            total_cleared=row["total_cleared"],
            is_trusted=bool(row["is_trusted"]),
            exists=True,
        )

    def get_consignee_profile(
        self, consignee_name: str, organization_id: str = "__system__"
    ) -> ConsigneeProfile:
        """Retrieve the pattern profile for a consignee entity."""
        key = _entity_key(consignee_name)
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT * FROM consignee_profiles"
                    " WHERE organization_id = :org_id AND consignee_key = :key"
                ),
                {"org_id": organization_id, "key": key},
            ).mappings().fetchone()

        if row is None:
            return ConsigneeProfile(
                consignee_key=key,
                consignee_name=consignee_name,
                reputation_score=_compute_shipper_reputation(0.0, 0.0),
                history_count=0,
                weighted_analyses=0.0,
                total_confirmed_fraud=0,
                total_cleared=0,
                is_trusted=False,
                exists=False,
            )

        return ConsigneeProfile(
            consignee_key=row["consignee_key"],
            consignee_name=row["consignee_name"] or consignee_name,
            reputation_score=row["reputation_score"],
            history_count=row["total_analyses"],
            weighted_analyses=row["weighted_analyses"],
            total_confirmed_fraud=row["total_confirmed_fraud"],
            total_cleared=row["total_cleared"],
            is_trusted=bool(row["is_trusted"]),
            exists=True,
        )

    def get_route_risk(
        self, origin: str, destination: str, organization_id: str = "__system__"
    ) -> RouteRisk:
        """Retrieve the historical fraud rate for a route corridor."""
        route_key = f"{origin}|{destination}"
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT * FROM route_risk_profiles"
                    " WHERE organization_id = :org_id AND route_key = :route_key"
                ),
                {"org_id": organization_id, "route_key": route_key},
            ).mappings().fetchone()

        if row is None:
            return RouteRisk(
                route_key=route_key,
                origin_iso2=origin,
                port_of_entry=destination,
                fraud_rate=0.5,
                total_analyses=0,
                total_confirmed_fraud=0,
                exists=False,
            )

        return RouteRisk(
            route_key=row["route_key"],
            origin_iso2=row["origin_iso2"],
            port_of_entry=row["port_of_entry"],
            fraud_rate=row["fraud_rate"],
            total_analyses=row["total_analyses"],
            total_confirmed_fraud=row["total_confirmed_fraud"],
            exists=True,
        )

    def get_hs_baseline(
        self, hs_code_prefix: str, organization_id: str = "__system__"
    ) -> HSBaseline:
        """Retrieve the running value distribution for an HS code prefix."""
        prefix = hs_code_prefix[:7].rstrip(".")
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT * FROM hs_code_baselines"
                    " WHERE organization_id = :org_id AND hs_prefix = :prefix"
                ),
                {"org_id": organization_id, "prefix": prefix},
            ).mappings().fetchone()

        if row is None or row["sample_count"] == 0:
            return HSBaseline(
                hs_prefix=prefix,
                sample_count=0,
                mean_unit_value=0.0,
                std_dev=None,
                min_value=None,
                max_value=None,
                exists=False,
            )

        return HSBaseline(
            hs_prefix=row["hs_prefix"],
            sample_count=row["sample_count"],
            mean_unit_value=row["running_mean"],
            std_dev=row["cached_stddev"],
            min_value=row["running_min"],
            max_value=row["running_max"],
            exists=True,
        )

    def reset(self, organization_id: str = "__system__") -> int:
        """Delete all learned data for one organization from every pattern table.

        Returns
        -------
        int
            Number of rows deleted from shipment_history.
        """
        with self._engine.begin() as conn:
            count_row = conn.execute(
                text(
                    "SELECT COUNT(*) FROM shipment_history WHERE organization_id = :org_id"
                ),
                {"org_id": organization_id},
            ).fetchone()
            count: int = count_row[0] if count_row else 0

            for table in (
                "pattern_outcomes",
                "shipment_history",
                "shipper_profiles",
                "consignee_profiles",
                "route_risk_profiles",
                "hs_code_baselines",
            ):
                conn.execute(
                    text(f"DELETE FROM {table} WHERE organization_id = :org_id"),
                    {"org_id": organization_id},
                )

        # WAL checkpoint for SQLite only — reduces file size after bulk delete.
        if self._dialect == "sqlite":
            try:
                with self._engine.begin() as conn:
                    conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            except Exception:
                pass

        return count

    def get_summary_stats(self, organization_id: str = "__system__") -> dict:
        """Return aggregate statistics for the Pattern History panel."""
        with self._engine.connect() as conn:
            total_shipments: int = conn.execute(
                text(
                    "SELECT COUNT(*) FROM shipment_history WHERE organization_id = :org_id"
                ),
                {"org_id": organization_id},
            ).fetchone()[0]

            total_confirmed_fraud: int = conn.execute(
                text(
                    "SELECT COUNT(*) FROM pattern_outcomes"
                    " WHERE organization_id = :org_id AND outcome = 'CONFIRMED_FRAUD'"
                ),
                {"org_id": organization_id},
            ).fetchone()[0]

            shipper_rows = conn.execute(
                text(
                    """SELECT shipper_name, reputation_score,
                              total_analyses, total_confirmed_fraud
                       FROM shipper_profiles
                       WHERE organization_id = :org_id AND total_analyses > 0
                       ORDER BY reputation_score DESC
                       LIMIT 5"""
                ),
                {"org_id": organization_id},
            ).mappings().fetchall()

            route_rows = conn.execute(
                text(
                    """SELECT origin_iso2, port_of_entry, fraud_rate, total_analyses
                       FROM route_risk_profiles
                       WHERE organization_id = :org_id AND total_analyses > 0
                       ORDER BY fraud_rate DESC
                       LIMIT 5"""
                ),
                {"org_id": organization_id},
            ).mappings().fetchall()

        top_riskiest_shippers = [
            {
                "name": r["shipper_name"],
                "reputation_score": round(r["reputation_score"], 4),
                "total_analyses": r["total_analyses"],
                "confirmed_fraud_count": r["total_confirmed_fraud"],
            }
            for r in shipper_rows
        ]
        top_riskiest_routes = [
            {
                "origin_iso2": r["origin_iso2"],
                "port_of_entry": r["port_of_entry"],
                "fraud_rate": round(r["fraud_rate"], 4),
                "total_analyses": r["total_analyses"],
            }
            for r in route_rows
        ]

        return {
            "total_shipments": total_shipments,
            "total_confirmed_fraud": total_confirmed_fraud,
            "top_riskiest_shippers": top_riskiest_shippers,
            "top_riskiest_routes": top_riskiest_routes,
        }

    # ------------------------------------------------------------------
    # Report payload storage and retrieval
    # ------------------------------------------------------------------

    def store_report_payload(
        self,
        analysis_id: str,
        payload_json: str,
        organization_id: str = "__system__",
    ) -> None:
        """Persist a serialised AnalyzeResponse JSON blob for PDF generation."""
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text(
                        """UPDATE shipment_history
                              SET report_payload = :payload
                            WHERE analysis_id = :analysis_id
                              AND organization_id = :org_id"""
                    ),
                    {
                        "payload": payload_json,
                        "analysis_id": analysis_id,
                        "org_id": organization_id,
                    },
                )
        except _SQLAlchemyError as exc:
            raise PatternDBError(f"Failed to store report payload: {exc}") from exc

    def get_report_payload(
        self,
        analysis_id: str,
        organization_id: str = "__system__",
    ) -> Optional[str]:
        """Retrieve the stored AnalyzeResponse JSON for a shipment."""
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text(
                        """SELECT report_payload
                             FROM shipment_history
                            WHERE analysis_id = :analysis_id
                              AND organization_id = :org_id"""
                    ),
                    {"analysis_id": analysis_id, "org_id": organization_id},
                ).mappings().fetchone()
            if row is None:
                return None
            return row["report_payload"]
        except _SQLAlchemyError as exc:
            logger.warning("get_report_payload(%s) failed: %s", analysis_id, exc)
            return None

    # ------------------------------------------------------------------
    # Internal helpers — profile upsert (called within engine.begin() conn)
    # ------------------------------------------------------------------

    def _upsert_entity_profile(
        self,
        conn: Any,
        table: str,
        key_col: str,
        name_col: str,
        key_val: str,
        name_val: str,
        is_flagged: bool,
        now: str,
        organization_id: str = "__system__",
    ) -> None:
        existing = conn.execute(
            text(
                f"SELECT * FROM {table}"
                f" WHERE organization_id = :org_id AND {key_col} = :key_val"
            ),
            {"org_id": organization_id, "key_val": key_val},
        ).mappings().fetchone()

        if existing is None:
            initial_score = _compute_shipper_reputation(0.0, 0.0)
            conn.execute(
                text(
                    f"""INSERT INTO {table} (
                        organization_id, {key_col}, {name_col}, first_seen_at, last_seen_at,
                        total_analyses, total_flagged,
                        weighted_analyses, weighted_flagged,
                        reputation_score, last_score_update
                    ) VALUES (
                        :org_id, :key_val, :name_val, :now, :now,
                        1, :flagged_int,
                        1.0, :flagged_float,
                        :score, :now
                    )"""
                ),
                {
                    "org_id": organization_id,
                    "key_val": key_val,
                    "name_val": name_val,
                    "now": now,
                    "flagged_int": int(is_flagged),
                    "flagged_float": float(is_flagged),
                    "score": initial_score,
                },
            )
        else:
            days_elapsed = _days_since(existing["last_score_update"])
            decay = _decay_weight(days_elapsed)

            w_analyses = existing["weighted_analyses"] * decay + 1.0
            w_flagged = existing["weighted_flagged"] * decay + float(is_flagged)
            w_fraud = existing["weighted_confirmed_fraud"] * decay
            w_cleared = existing["weighted_cleared"] * decay

            rep_score = _compute_shipper_reputation(w_fraud, w_cleared)
            if existing["is_trusted"]:
                rep_score = 0.0

            conn.execute(
                text(
                    f"""UPDATE {table} SET
                        {name_col} = :name_val,
                        last_seen_at = :now,
                        total_analyses = total_analyses + 1,
                        total_flagged = total_flagged + :flagged_int,
                        weighted_analyses = :w_analyses,
                        weighted_flagged = :w_flagged,
                        weighted_confirmed_fraud = :w_fraud,
                        weighted_cleared = :w_cleared,
                        reputation_score = :rep_score,
                        last_score_update = :now
                    WHERE organization_id = :org_id AND {key_col} = :key_val"""
                ),
                {
                    "name_val": name_val,
                    "now": now,
                    "flagged_int": int(is_flagged),
                    "w_analyses": w_analyses,
                    "w_flagged": w_flagged,
                    "w_fraud": w_fraud,
                    "w_cleared": w_cleared,
                    "rep_score": rep_score,
                    "org_id": organization_id,
                    "key_val": key_val,
                },
            )

    def _upsert_route_profile(
        self,
        conn: Any,
        route_key: str,
        origin_iso2: str,
        port_of_entry: str,
        is_flagged: bool,
        now: str,
        organization_id: str = "__system__",
    ) -> None:
        existing = conn.execute(
            text(
                "SELECT * FROM route_risk_profiles"
                " WHERE organization_id = :org_id AND route_key = :route_key"
            ),
            {"org_id": organization_id, "route_key": route_key},
        ).mappings().fetchone()

        if existing is None:
            initial_rate = _compute_route_fraud_rate(0.0, 1.0)
            conn.execute(
                text(
                    """INSERT INTO route_risk_profiles (
                        organization_id, route_key, origin_iso2, port_of_entry,
                        first_seen_at, last_seen_at,
                        total_analyses, total_flagged,
                        weighted_analyses, weighted_flagged,
                        fraud_rate, last_score_update
                    ) VALUES (
                        :org_id, :route_key, :origin_iso2, :port_of_entry,
                        :now, :now,
                        1, :flagged_int,
                        1.0, :flagged_float,
                        :rate, :now
                    )"""
                ),
                {
                    "org_id": organization_id,
                    "route_key": route_key,
                    "origin_iso2": origin_iso2,
                    "port_of_entry": port_of_entry,
                    "now": now,
                    "flagged_int": int(is_flagged),
                    "flagged_float": float(is_flagged),
                    "rate": initial_rate,
                },
            )
        else:
            days_elapsed = _days_since(existing["last_score_update"])
            decay = _decay_weight(days_elapsed)

            w_analyses = existing["weighted_analyses"] * decay + 1.0
            w_flagged = existing["weighted_flagged"] * decay + float(is_flagged)
            w_fraud = existing["weighted_confirmed_fraud"] * decay
            w_cleared = existing["weighted_cleared"] * decay

            fraud_rate = _compute_route_fraud_rate(w_fraud, w_analyses)

            conn.execute(
                text(
                    """UPDATE route_risk_profiles SET
                        last_seen_at = :now,
                        total_analyses = total_analyses + 1,
                        total_flagged = total_flagged + :flagged_int,
                        weighted_analyses = :w_analyses,
                        weighted_flagged = :w_flagged,
                        weighted_confirmed_fraud = :w_fraud,
                        weighted_cleared = :w_cleared,
                        fraud_rate = :fraud_rate,
                        last_score_update = :now
                    WHERE organization_id = :org_id AND route_key = :route_key"""
                ),
                {
                    "now": now,
                    "flagged_int": int(is_flagged),
                    "w_analyses": w_analyses,
                    "w_flagged": w_flagged,
                    "w_fraud": w_fraud,
                    "w_cleared": w_cleared,
                    "fraud_rate": fraud_rate,
                    "org_id": organization_id,
                    "route_key": route_key,
                },
            )

    def _update_hs_baseline(
        self,
        conn: Any,
        hs_prefix: str,
        unit_value_usd: float,
        now: str,
        organization_id: str = "__system__",
    ) -> None:
        existing = conn.execute(
            text(
                "SELECT * FROM hs_code_baselines"
                " WHERE organization_id = :org_id AND hs_prefix = :prefix"
            ),
            {"org_id": organization_id, "prefix": hs_prefix},
        ).mappings().fetchone()

        if existing is None:
            conn.execute(
                text(
                    """INSERT INTO hs_code_baselines
                       (organization_id, hs_prefix, sample_count, running_mean, running_m2,
                        running_min, running_max, cached_stddev, last_updated)
                       VALUES (:org_id, :prefix, 1, :mean, 0.0, :val, :val, NULL, :now)"""
                ),
                {
                    "org_id": organization_id,
                    "prefix": hs_prefix,
                    "mean": unit_value_usd,
                    "val": unit_value_usd,
                    "now": now,
                },
            )
        else:
            n, mean, m2 = _welford_update(
                existing["sample_count"],
                existing["running_mean"],
                existing["running_m2"],
                unit_value_usd,
            )
            stddev = _welford_stddev(n, m2)
            new_min = min(v for v in [existing["running_min"], unit_value_usd] if v is not None)
            new_max = max(v for v in [existing["running_max"], unit_value_usd] if v is not None)

            conn.execute(
                text(
                    """UPDATE hs_code_baselines SET
                       sample_count = :n,
                       running_mean = :mean,
                       running_m2 = :m2,
                       running_min = :new_min,
                       running_max = :new_max,
                       cached_stddev = :stddev,
                       last_updated = :now
                       WHERE organization_id = :org_id AND hs_prefix = :prefix"""
                ),
                {
                    "n": n,
                    "mean": mean,
                    "m2": m2,
                    "new_min": new_min,
                    "new_max": new_max,
                    "stddev": stddev,
                    "now": now,
                    "org_id": organization_id,
                    "prefix": hs_prefix,
                },
            )

    # ------------------------------------------------------------------
    # Internal helpers — outcome profile updates
    # ------------------------------------------------------------------

    def _apply_fraud_outcome(
        self, conn: Any, analysis_row: Any, decay_weight: float, now: str
    ) -> None:
        shipper_key = analysis_row["shipper_key"]
        consignee_key = analysis_row["consignee_key"]
        route_key = analysis_row["route_key"]
        org_id = analysis_row["organization_id"]

        if shipper_key:
            self._apply_entity_fraud(conn, "shipper_profiles", "shipper_key", shipper_key, decay_weight, now, org_id)
        if consignee_key:
            self._apply_entity_fraud(conn, "consignee_profiles", "consignee_key", consignee_key, decay_weight, now, org_id)
        if route_key:
            self._apply_route_fraud(conn, route_key, decay_weight, now, organization_id=org_id)

    def _apply_cleared_outcome(
        self, conn: Any, analysis_row: Any, decay_weight: float, now: str
    ) -> None:
        shipper_key = analysis_row["shipper_key"]
        consignee_key = analysis_row["consignee_key"]
        org_id = analysis_row["organization_id"]

        if shipper_key:
            self._apply_entity_cleared(conn, "shipper_profiles", "shipper_key", shipper_key, decay_weight, now, org_id)
        if consignee_key:
            self._apply_entity_cleared(conn, "consignee_profiles", "consignee_key", consignee_key, decay_weight, now, org_id)

    def _apply_entity_fraud(
        self,
        conn: Any,
        table: str,
        key_col: str,
        key_val: str,
        decay_weight: float,
        now: str,
        organization_id: str = "__system__",
    ) -> None:
        existing = conn.execute(
            text(
                f"SELECT * FROM {table}"
                f" WHERE organization_id = :org_id AND {key_col} = :key_val"
            ),
            {"org_id": organization_id, "key_val": key_val},
        ).mappings().fetchone()
        if existing is None:
            return

        days_elapsed = _days_since(existing["last_score_update"])
        time_decay = _decay_weight(days_elapsed)

        w_fraud = existing["weighted_confirmed_fraud"] * time_decay + decay_weight
        w_cleared = existing["weighted_cleared"] * time_decay
        w_analyses = existing["weighted_analyses"] * time_decay
        w_flagged = existing["weighted_flagged"] * time_decay

        rep_score = _compute_shipper_reputation(w_fraud, w_cleared)

        conn.execute(
            text(
                f"""UPDATE {table} SET
                    total_confirmed_fraud = total_confirmed_fraud + 1,
                    weighted_confirmed_fraud = :w_fraud,
                    weighted_analyses = :w_analyses,
                    weighted_flagged = :w_flagged,
                    weighted_cleared = :w_cleared,
                    reputation_score = :rep_score,
                    last_score_update = :now
                WHERE organization_id = :org_id AND {key_col} = :key_val"""
            ),
            {
                "w_fraud": w_fraud,
                "w_analyses": w_analyses,
                "w_flagged": w_flagged,
                "w_cleared": w_cleared,
                "rep_score": rep_score,
                "now": now,
                "org_id": organization_id,
                "key_val": key_val,
            },
        )

    def _apply_entity_cleared(
        self,
        conn: Any,
        table: str,
        key_col: str,
        key_val: str,
        decay_weight: float,
        now: str,
        organization_id: str = "__system__",
    ) -> None:
        existing = conn.execute(
            text(
                f"SELECT * FROM {table}"
                f" WHERE organization_id = :org_id AND {key_col} = :key_val"
            ),
            {"org_id": organization_id, "key_val": key_val},
        ).mappings().fetchone()
        if existing is None:
            return

        days_elapsed = _days_since(existing["last_score_update"])
        time_decay = _decay_weight(days_elapsed)

        w_fraud = existing["weighted_confirmed_fraud"] * time_decay
        w_cleared = existing["weighted_cleared"] * time_decay + decay_weight
        w_analyses = existing["weighted_analyses"] * time_decay
        w_flagged = existing["weighted_flagged"] * time_decay

        rep_score = _compute_shipper_reputation(w_fraud, w_cleared)

        # Auto-trust: >=20 weighted clears AND 0 confirmed fraud → system trust.
        auto_trust = (
            w_cleared >= AUTO_TRUST_CLEARED_THRESHOLD - 1e-6
            and existing["total_confirmed_fraud"] == 0
            and not existing["is_trusted"]
        )
        if auto_trust:
            rep_score = 0.0
            logger.info(
                "Auto-trust threshold reached for %s key=%s (weighted_cleared=%.1f)",
                table, key_val, w_cleared,
            )

        base_params = {
            "w_cleared": w_cleared,
            "w_fraud": w_fraud,
            "w_analyses": w_analyses,
            "w_flagged": w_flagged,
            "rep_score": rep_score,
            "now": now,
            "org_id": organization_id,
            "key_val": key_val,
        }

        if auto_trust:
            conn.execute(
                text(
                    f"""UPDATE {table} SET
                        total_cleared = total_cleared + 1,
                        weighted_cleared = :w_cleared,
                        weighted_confirmed_fraud = :w_fraud,
                        weighted_analyses = :w_analyses,
                        weighted_flagged = :w_flagged,
                        reputation_score = :rep_score,
                        is_trusted = 1,
                        trust_set_at = :now,
                        trust_set_by = 'system_auto',
                        last_score_update = :now
                    WHERE organization_id = :org_id AND {key_col} = :key_val"""
                ),
                base_params,
            )
        else:
            conn.execute(
                text(
                    f"""UPDATE {table} SET
                        total_cleared = total_cleared + 1,
                        weighted_cleared = :w_cleared,
                        weighted_confirmed_fraud = :w_fraud,
                        weighted_analyses = :w_analyses,
                        weighted_flagged = :w_flagged,
                        reputation_score = :rep_score,
                        last_score_update = :now
                    WHERE organization_id = :org_id AND {key_col} = :key_val"""
                ),
                base_params,
            )

    def _apply_route_fraud(
        self,
        conn: Any,
        route_key: str,
        decay_weight: float,
        now: str,
        organization_id: str = "__system__",
    ) -> None:
        existing = conn.execute(
            text(
                "SELECT * FROM route_risk_profiles"
                " WHERE organization_id = :org_id AND route_key = :route_key"
            ),
            {"org_id": organization_id, "route_key": route_key},
        ).mappings().fetchone()
        if existing is None:
            return

        days_elapsed = _days_since(existing["last_score_update"])
        time_decay = _decay_weight(days_elapsed)

        w_fraud = existing["weighted_confirmed_fraud"] * time_decay + decay_weight
        w_analyses = existing["weighted_analyses"] * time_decay
        w_flagged = existing["weighted_flagged"] * time_decay
        w_cleared = existing["weighted_cleared"] * time_decay

        fraud_rate = _compute_route_fraud_rate(w_fraud, w_analyses)

        conn.execute(
            text(
                """UPDATE route_risk_profiles SET
                   total_confirmed_fraud = total_confirmed_fraud + 1,
                   weighted_confirmed_fraud = :w_fraud,
                   weighted_analyses = :w_analyses,
                   weighted_flagged = :w_flagged,
                   weighted_cleared = :w_cleared,
                   fraud_rate = :fraud_rate,
                   last_score_update = :now
                WHERE organization_id = :org_id AND route_key = :route_key"""
            ),
            {
                "w_fraud": w_fraud,
                "w_analyses": w_analyses,
                "w_flagged": w_flagged,
                "w_cleared": w_cleared,
                "fraud_rate": fraud_rate,
                "now": now,
                "org_id": organization_id,
                "route_key": route_key,
            },
        )
