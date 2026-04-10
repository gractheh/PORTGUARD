"""
portguard/pattern_db.py — SQLite-backed data layer for the Localized Pattern Learning system.

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

All methods are thread-safe via the internal connection pool (check_same_thread=False +
serialized writes through a threading.Lock).

Exceptions
----------
PatternDBError      — base class
RecordNotFoundError — analysis_id lookup failed
DuplicateOutcomeError — outcome already recorded for this analysis (409-equivalent)
InvalidOutcomeError — outcome value not in VALID_OUTCOMES

Design notes
------------
- WAL mode is enabled on every connection for concurrent read throughput.
- All timestamps are ISO-8601 UTC strings.  datetime objects are never stored.
- Entity names are normalized and SHA-256-hashed before any DB lookup.  The plain-text
  name is also stored for officer readability.
- Temporal decay uses λ = 0.023 (30-day half-life).
- Welford's algorithm is used for HS value running statistics (O(1) memory, numerically
  stable for large sample counts).
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
import sqlite3
import threading
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

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
    """Aggregated reputation data for a single shipper entity.

    Attributes
    ----------
    shipper_key:
        SHA-256 hash of the normalized shipper name — stable across name variants.
    shipper_name:
        Most-recently-seen plain-text name for display purposes.
    reputation_score:
        Bayesian Beta score in [0.0, 1.0].  Higher = more suspicious.
        Starts at ~0.167 (innocent prior α=1, β=5) for unknown shippers.
    history_count:
        Total number of analyses recorded for this shipper (integer, unweighted).
    weighted_analyses:
        Decay-weighted analysis count.  Reflects recency, not just volume.
    total_confirmed_fraud:
        Unweighted count of CONFIRMED_FRAUD outcomes linked to this shipper.
    total_cleared:
        Unweighted count of CLEARED outcomes.
    is_trusted:
        True if an officer manually marked this shipper as trusted, or if the
        system auto-trusted them (>=20 weighted clears, 0 confirmed fraud).
    exists:
        False if no profile row exists yet; all numeric fields will be at their
        prior / default values.
    """

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
    """Aggregated reputation data for a single consignee entity.

    Same structure and semantics as ShipperProfile — see that class for
    full attribute documentation.  Consignees and shippers are scored
    independently because a legitimate consignee may receive goods from a
    fraudulent shipper and vice versa.
    """

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
    """Historical fraud rate for an origin → port-of-entry corridor.

    Attributes
    ----------
    route_key:
        Compound key: ``<origin_iso2>|<port_of_entry>`` e.g. ``"CN|Los Angeles"``.
    origin_iso2:
        2-letter ISO country code for the origin.
    port_of_entry:
        Destination port name as a free-text string.
    fraud_rate:
        Bayesian estimate of P(fraud | this route) using Jeffrey's prior.
        Starts at 0.50 for a completely unknown route (α=0.5, β=0.5).
    total_analyses:
        Unweighted count of all shipments on this route.
    total_confirmed_fraud:
        Unweighted count of CONFIRMED_FRAUD outcomes on this route.
    exists:
        False if no profile row exists yet.
    """

    route_key: str
    origin_iso2: str
    port_of_entry: str
    fraud_rate: float
    total_analyses: int
    total_confirmed_fraud: int
    exists: bool


@dataclass
class HSBaseline:
    """Running distribution of declared unit values for an HS code prefix.

    Attributes
    ----------
    hs_prefix:
        6-digit HS prefix used as the key, e.g. ``"8471.30"``.
    sample_count:
        Number of shipments that contributed to the statistics.
    mean_unit_value:
        Running mean of declared unit value in USD.
    std_dev:
        Running standard deviation (None if sample_count < 2).
    min_value:
        Minimum declared unit value observed.
    max_value:
        Maximum declared unit value observed.
    exists:
        False if no baseline row exists yet for this prefix.
    """

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
    """Raised when a resolved outcome already exists for the given analysis_id.

    Per §4.4 of the architecture document, resolved outcomes (CONFIRMED_FRAUD
    or CLEARED) are immutable.  Only UNRESOLVED outcomes can be updated.
    """


class InvalidOutcomeError(PatternDBError):
    """Raised when the outcome value is not one of VALID_OUTCOMES."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Example: ``"2025-04-10T14:32:01.123456+00:00"``
    """
    return datetime.now(timezone.utc).isoformat()


def _days_since(iso_timestamp: str) -> float:
    """Return the number of fractional days between *iso_timestamp* and now.

    Parameters
    ----------
    iso_timestamp:
        An ISO-8601 UTC string produced by :func:`_utcnow`.

    Returns
    -------
    float
        Non-negative fractional day count.  Clamped to 0 if the timestamp is
        in the future (handles clock skew without crashing).
    """
    then = datetime.fromisoformat(iso_timestamp)
    if then.tzinfo is None:
        # Treat naive timestamps as UTC (legacy rows)
        then = then.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - then
    return max(0.0, delta.total_seconds() / 86400.0)


def _decay_weight(days_ago: float) -> float:
    """Compute the exponential decay weight for an event *days_ago* days in the past.

    Uses λ = 0.023, giving a 30-day half-life.

    Parameters
    ----------
    days_ago:
        Fractional days since the event.

    Returns
    -------
    float
        Weight in (0.0, 1.0].  An event today has weight 1.0; an event 30 days
        ago has weight ≈ 0.50; 90 days ago ≈ 0.125.
    """
    return math.exp(-DECAY_LAMBDA * days_ago)


def _normalize_entity_name(name: str) -> str:
    """Normalize an entity name to a canonical form for stable hashing.

    Implements the normalization pipeline from §2.9 of the architecture doc:
    1. Unicode NFKD normalization → ASCII
    2. Lowercase
    3. Strip punctuation except hyphens
    4. Remove common legal suffixes (ltd, inc, llc, etc.)
    5. Collapse whitespace
    6. Strip leading/trailing whitespace

    Parameters
    ----------
    name:
        Raw entity name as it appears on the trade document.

    Returns
    -------
    str
        Normalized name, ready to be passed to :func:`_entity_key`.

    Examples
    --------
    >>> _normalize_entity_name("Viet Star Electronics Manufacturing Co., Ltd.")
    'viet star electronics manufacturing'
    >>> _normalize_entity_name("ACME Corp.")
    'acme'
    """
    # Step 1 — Unicode NFKD → ASCII
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", errors="ignore").decode("ascii")

    # Step 2 — Lowercase
    lowered = ascii_str.lower()

    # Step 3 — Strip punctuation except hyphens
    cleaned = re.sub(r"[^\w\s-]", " ", lowered)

    # Step 4 — Remove legal suffixes (whole-word match)
    tokens = cleaned.split()
    filtered = [t for t in tokens if t not in _LEGAL_SUFFIXES]

    # Step 5 & 6 — Collapse and strip whitespace
    return " ".join(filtered).strip()


def _entity_key(name: str) -> str:
    """Return the SHA-256 hex digest of the normalized entity name.

    This is the stable identifier used in all pattern DB lookups.  Two entity
    names that normalize to the same string share a key and thus a profile.

    Parameters
    ----------
    name:
        Raw entity name (not yet normalized).

    Returns
    -------
    str
        64-character lowercase hex string.
    """
    normalized = _normalize_entity_name(name)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _compute_shipper_reputation(
    weighted_confirmed_fraud: float,
    weighted_cleared: float,
) -> float:
    """Compute Bayesian Beta reputation score with informative innocent prior.

    Uses α₀ = 1, β₀ = 5 (§3.5 of architecture doc).

    Parameters
    ----------
    weighted_confirmed_fraud:
        Decay-weighted sum of CONFIRMED_FRAUD outcomes.
    weighted_cleared:
        Decay-weighted sum of CLEARED outcomes.

    Returns
    -------
    float
        Score in [0.0, 1.0].  A higher score means more suspicious.
        Unknown entity (both weights = 0) → 1/6 ≈ 0.167.
    """
    alpha = weighted_confirmed_fraud + 1.0
    beta = weighted_cleared + 5.0
    return alpha / (alpha + beta)


def _compute_route_fraud_rate(
    weighted_confirmed_fraud: float,
    weighted_analyses: float,
) -> float:
    """Compute Bayesian Beta fraud rate with Jeffrey's non-informative prior.

    Uses α₀ = β₀ = 0.5 (§3.4 of architecture doc).

    Parameters
    ----------
    weighted_confirmed_fraud:
        Decay-weighted sum of confirmed fraud analyses on this route.
    weighted_analyses:
        Decay-weighted total analyses on this route.

    Returns
    -------
    float
        Fraud rate in [0.0, 1.0].  Unknown route → 0.50.
    """
    alpha = weighted_confirmed_fraud + 0.5
    beta = (weighted_analyses - weighted_confirmed_fraud) + 0.5
    # Guard against negative beta from floating-point noise
    beta = max(beta, 0.5)
    return alpha / (alpha + beta)


def _welford_update(
    n: int,
    mean: float,
    m2: float,
    new_value: float,
) -> tuple[int, float, float]:
    """Apply one step of Welford's online algorithm.

    Welford's algorithm computes exact running mean and variance in a single
    pass with O(1) memory.  It is numerically stable for very large sample
    counts (see Knuth TAOCP Vol. 2, §4.2.2).

    Parameters
    ----------
    n:
        Current sample count (before adding new_value).
    mean:
        Current running mean.
    m2:
        Current sum of squared deviations from the mean (M2 accumulator).
    new_value:
        The new data point to incorporate.

    Returns
    -------
    (n+1, new_mean, new_m2)
        Updated statistics after incorporating new_value.
    """
    n += 1
    delta = new_value - mean
    mean += delta / n
    delta2 = new_value - mean
    m2 += delta * delta2
    return n, mean, m2


def _welford_stddev(n: int, m2: float) -> Optional[float]:
    """Compute sample standard deviation from Welford accumulator state.

    Parameters
    ----------
    n:
        Number of samples.
    m2:
        Sum of squared deviations (M2 accumulator).

    Returns
    -------
    float or None
        Sample standard deviation, or None if n < 2 (undefined for < 2 samples).
    """
    if n < 2:
        return None
    variance = m2 / (n - 1)
    return math.sqrt(max(variance, 0.0))  # clamp for floating-point edge cases


# ---------------------------------------------------------------------------
# Schema — DDL and migrations
# ---------------------------------------------------------------------------

# Each migration is a (name, sql) tuple.  Migrations are applied in order and
# each is recorded by name in schema_migrations to ensure idempotency.
# NEVER modify a migration that has already been applied — add a new one instead.

_MIGRATIONS: list[tuple[str, str]] = [
    (
        "001_initial_schema",
        """
        -- Migration 001: initial schema

        CREATE TABLE IF NOT EXISTS schema_migrations (
            migration_name  TEXT PRIMARY KEY,
            applied_at      TEXT NOT NULL
        );

        -- ----------------------------------------------------------------
        -- shipment_history: canonical record for every analysis run
        -- ----------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS shipment_history (
            analysis_id             TEXT PRIMARY KEY,
            analyzed_at             TEXT NOT NULL,

            -- Shipment fingerprint (extracted from the submitted document)
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

            -- Rule engine snapshot (immutable after recording)
            rule_risk_score         REAL NOT NULL,
            rule_decision           TEXT NOT NULL,
            rule_confidence         TEXT NOT NULL,
            rules_fired             TEXT NOT NULL DEFAULT '[]',
            inconsistency_count     INTEGER NOT NULL DEFAULT 0,
            missing_field_count     INTEGER NOT NULL DEFAULT 0,

            -- Pattern engine snapshot (NULL when insufficient history)
            pattern_score               REAL,
            pattern_shipper_score       REAL,
            pattern_consignee_score     REAL,
            pattern_route_score         REAL,
            pattern_value_z_score       REAL,
            pattern_flag_frequency      REAL,
            pattern_history_depth       INTEGER,
            pattern_cold_start          INTEGER NOT NULL DEFAULT 1,

            -- Final blended output
            final_risk_score        REAL NOT NULL,
            final_decision          TEXT NOT NULL,
            final_confidence        TEXT NOT NULL,

            -- Denormalized flag: set to 1 when outcome = CLEARED, for flag_frequency exclusion
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

        -- ----------------------------------------------------------------
        -- pattern_outcomes: officer-recorded verdicts for flagged shipments
        -- ----------------------------------------------------------------
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

        -- ----------------------------------------------------------------
        -- shipper_profiles: Bayesian reputation per shipper entity
        -- ----------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS shipper_profiles (
            shipper_key                 TEXT PRIMARY KEY,
            shipper_name                TEXT,
            first_seen_at               TEXT NOT NULL,
            last_seen_at                TEXT NOT NULL,

            -- Unweighted counts (for reporting/display)
            total_analyses              INTEGER NOT NULL DEFAULT 0,
            total_flagged               INTEGER NOT NULL DEFAULT 0,
            total_confirmed_fraud       INTEGER NOT NULL DEFAULT 0,
            total_cleared               INTEGER NOT NULL DEFAULT 0,
            total_unresolved            INTEGER NOT NULL DEFAULT 0,

            -- Decay-weighted running sums (used for live score computation)
            weighted_analyses           REAL NOT NULL DEFAULT 0.0,
            weighted_flagged            REAL NOT NULL DEFAULT 0.0,
            weighted_confirmed_fraud    REAL NOT NULL DEFAULT 0.0,
            weighted_cleared            REAL NOT NULL DEFAULT 0.0,

            -- Bayesian Beta reputation score (informative innocent prior α=1, β=5)
            reputation_score            REAL NOT NULL DEFAULT 0.16666666666,

            -- Trust override
            last_score_update           TEXT NOT NULL,
            is_trusted                  INTEGER NOT NULL DEFAULT 0,
            trust_set_at                TEXT,
            trust_set_by                TEXT
        );

        -- ----------------------------------------------------------------
        -- consignee_profiles: identical structure to shipper_profiles
        -- ----------------------------------------------------------------
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

        -- ----------------------------------------------------------------
        -- route_risk_profiles: Bayesian fraud rate per origin → port lane
        -- ----------------------------------------------------------------
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
            -- Jeffrey's prior (α₀=β₀=0.5): new route starts at 0.50
            fraud_rate              REAL NOT NULL DEFAULT 0.5,
            last_score_update       TEXT NOT NULL
        );

        -- ----------------------------------------------------------------
        -- hs_code_baselines: Welford running stats of unit values per HS prefix
        -- ----------------------------------------------------------------
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
]


# ---------------------------------------------------------------------------
# Shipment fingerprint dataclass
# ---------------------------------------------------------------------------


@dataclass
class ShipmentFingerprint:
    """All fields extracted from a shipment document that the pattern engine uses.

    Pass this to :meth:`PatternDB.record_shipment`.  Fields that are unknown or
    not present in the document should be left as None.

    Attributes
    ----------
    shipper_name:
        Raw shipper name from the document.
    consignee_name:
        Raw consignee name.
    origin_iso2:
        2-letter ISO country code of origin (e.g. ``"CN"``).
    destination_iso2:
        2-letter ISO country code of destination (always ``"US"`` for now).
    port_of_entry:
        Destination port (e.g. ``"Los Angeles"``).
    carrier:
        Shipping carrier name.
    hs_codes:
        List of HTS codes for the goods (e.g. ``["8471.30.0100", "8542.31"]``).
    declared_value_usd:
        Total declared customs value in USD.
    quantity:
        Number of units (used to compute unit_value_usd).
    gross_weight_kg:
        Gross weight of the shipment.
    incoterms:
        Trade terms (e.g. ``"FOB"``, ``"CIF"``).
    inconsistency_count:
        Number of inconsistencies found by the rule engine.
    missing_field_count:
        Number of required fields absent from the document.
    pattern_score:
        Pattern score at decision time (None if no history).
    pattern_shipper_score:
        Shipper component of the pattern score.
    pattern_consignee_score:
        Consignee component.
    pattern_route_score:
        Route component.
    pattern_value_z_score:
        Z-score of unit value vs HS baseline.
    pattern_flag_frequency:
        Shipper's recent flag frequency.
    pattern_history_depth:
        Number of prior analyses that informed pattern scores.
    pattern_cold_start:
        Whether cold-start penalty was applied.
    final_risk_score:
        Blended risk score submitted to the decision engine.
    final_decision:
        Final decision string (e.g. ``"FLAG_FOR_INSPECTION"``).
    final_confidence:
        Confidence string (``"HIGH"`` | ``"MEDIUM"`` | ``"LOW"``).
    """

    # Shipment identity
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

    # Rule engine snapshot (required)
    rule_risk_score: float = 0.0
    rule_decision: str = "APPROVE"
    rule_confidence: str = "LOW"
    rules_fired: List[dict] = field(default_factory=list)

    # Pattern engine snapshot (filled in by PatternEngine before calling record_shipment)
    pattern_score: Optional[float] = None
    pattern_shipper_score: Optional[float] = None
    pattern_consignee_score: Optional[float] = None
    pattern_route_score: Optional[float] = None
    pattern_value_z_score: Optional[float] = None
    pattern_flag_frequency: Optional[float] = None
    pattern_history_depth: Optional[int] = None
    pattern_cold_start: bool = True

    # Final blended output (required)
    final_risk_score: float = 0.0
    final_decision: str = "APPROVE"
    final_confidence: str = "LOW"


# ---------------------------------------------------------------------------
# PatternDB
# ---------------------------------------------------------------------------


class PatternDB:
    """SQLite-backed data layer for the Localized Pattern Learning system.

    This class manages the SQLite connection, runs schema migrations on first
    use, and exposes a clean Python API for recording shipment analyses,
    recording officer outcomes, and querying entity/route/HS profiles.

    Thread safety
    -------------
    A single :class:`PatternDB` instance is safe to use from multiple threads.
    SQLite is opened with ``check_same_thread=False`` and a :class:`threading.Lock`
    serializes all write operations.  Read operations use a separate connection
    created per-call to avoid blocking writers (WAL mode allows this).

    Parameters
    ----------
    db_path:
        Path to the SQLite file.  The parent directory must exist.  Pass
        ``":memory:"`` for an in-memory database (useful for tests).

    Examples
    --------
    >>> db = PatternDB("portguard_patterns.db")
    >>> analysis_id = db.record_shipment(fingerprint, "FLAG_FOR_INSPECTION", [...], "HIGH")
    >>> db.record_outcome(analysis_id, "CONFIRMED_FRAUD", officer_id="officer_1")
    >>> profile = db.get_shipper_profile("Acme Corp")
    >>> db.close()
    """

    def __init__(self, db_path: str | Path = "portguard_patterns.db") -> None:
        """Open (or create) the PatternDB at *db_path* and run pending migrations.

        Parameters
        ----------
        db_path:
            Filesystem path or ``":memory:"``.
        """
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = self._open_connection()
        self._run_migrations()
        logger.info("PatternDB initialized at %s", self._db_path)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _open_connection(self) -> sqlite3.Connection:
        """Open a new SQLite connection with WAL mode and foreign keys enabled.

        Returns
        -------
        sqlite3.Connection
            Configured connection ready for use.
        """
        conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
            isolation_level=None,   # autocommit by default; we use explicit transactions
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")   # safe + faster than FULL in WAL mode
        return conn

    def close(self) -> None:
        """Close the database connection.

        After calling this method, the :class:`PatternDB` instance must not be
        used.  Calling this is optional — the connection will be closed when the
        object is garbage-collected — but it is good practice in long-running
        services.
        """
        try:
            self._conn.close()
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
        it is skipped.  Migrations are applied inside individual transactions
        so a failure leaves the database in a consistent prior state.

        This method is called once during ``__init__`` and is idempotent.
        """
        # Bootstrap: the schema_migrations table may not exist yet on a fresh DB
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_name  TEXT PRIMARY KEY,
                applied_at      TEXT NOT NULL
            )
        """)
        self._conn.commit()

        applied = {
            row["migration_name"]
            for row in self._conn.execute("SELECT migration_name FROM schema_migrations")
        }

        for name, sql in _MIGRATIONS:
            if name in applied:
                continue
            logger.info("Applying migration: %s", name)
            try:
                self._conn.executescript(sql)
                self._conn.execute(
                    "INSERT INTO schema_migrations(migration_name, applied_at) VALUES (?,?)",
                    (name, _utcnow()),
                )
                self._conn.commit()
                logger.info("Migration applied: %s", name)
            except sqlite3.Error as exc:
                self._conn.rollback()
                raise PatternDBError(f"Failed to apply migration '{name}': {exc}") from exc

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
        """Record a completed analysis to shipment_history and update HS baselines.

        This method captures an immutable snapshot of the analysis at decision
        time.  The snapshot is used later by the feedback loop and by the pattern
        detection engine for historical queries.

        After writing the analysis row, this method also updates the HS value
        baseline for each HS code prefix present in the shipment, incorporating
        the declared unit value into Welford's running statistics.

        Shipper, consignee, and route profile rows are upserted (created if new,
        otherwise their ``last_seen_at`` and weighted counts are incremented).

        Parameters
        ----------
        fingerprint:
            Populated :class:`ShipmentFingerprint` containing all fields
            extracted from the document plus rule-engine and pattern-engine
            snapshots.
        decision:
            Final decision string, e.g. ``"FLAG_FOR_INSPECTION"``.  This is
            stored in ``final_decision``; ``fingerprint.rule_decision`` holds
            the pre-pattern rule decision separately.
        rules_fired:
            List of rule-firing dicts, each with at minimum ``{"type": ...,
            "severity": ..., "score": ...}``.  Serialized to JSON.
        confidence:
            Final confidence string (``"HIGH"`` | ``"MEDIUM"`` | ``"LOW"``).

        Returns
        -------
        str
            The ``analysis_id`` (UUID v4) assigned to this analysis.  Callers
            should store this to submit outcomes later via :meth:`record_outcome`.

        Raises
        ------
        PatternDBError
            If the database write fails for any reason.
        """
        analysis_id = str(uuid.uuid4())
        now = _utcnow()

        # Derived fields
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

        # Coerce fingerprint fields so stored values match
        fingerprint.final_decision = decision
        fingerprint.final_confidence = confidence

        is_flagged = decision != "APPROVE"

        with self._lock:
            try:
                self._conn.execute("BEGIN")

                # Insert analysis row
                self._conn.execute(
                    """
                    INSERT INTO shipment_history (
                        analysis_id, analyzed_at,
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
                        ?,?,  ?,?,  ?,?,  ?,?,?,?,  ?,?,?,  ?,?,?,  ?,?,
                        ?,?,?,  ?,?,?,
                        ?,?,?,  ?,?,  ?,?,?,  ?,?,?
                    )
                    """,
                    (
                        analysis_id, now,
                        fingerprint.shipper_name, shipper_key,
                        fingerprint.consignee_name, consignee_key,
                        fingerprint.origin_iso2, fingerprint.destination_iso2,
                        fingerprint.port_of_entry, route_key,
                        fingerprint.carrier, json.dumps(fingerprint.hs_codes),
                        hs_chapter_primary,
                        fingerprint.declared_value_usd, fingerprint.quantity,
                        unit_value_usd, fingerprint.gross_weight_kg, fingerprint.incoterms,
                        fingerprint.rule_risk_score, fingerprint.rule_decision,
                        fingerprint.rule_confidence,
                        json.dumps(rules_fired),
                        fingerprint.inconsistency_count, fingerprint.missing_field_count,
                        fingerprint.pattern_score, fingerprint.pattern_shipper_score,
                        fingerprint.pattern_consignee_score, fingerprint.pattern_route_score,
                        fingerprint.pattern_value_z_score, fingerprint.pattern_flag_frequency,
                        fingerprint.pattern_history_depth,
                        int(fingerprint.pattern_cold_start),
                        fingerprint.final_risk_score, decision, confidence,
                    ),
                )

                # Upsert shipper profile
                if shipper_key and fingerprint.shipper_name:
                    self._upsert_entity_profile(
                        table="shipper_profiles",
                        key_col="shipper_key",
                        name_col="shipper_name",
                        key_val=shipper_key,
                        name_val=fingerprint.shipper_name,
                        is_flagged=is_flagged,
                        now=now,
                    )

                # Upsert consignee profile
                if consignee_key and fingerprint.consignee_name:
                    self._upsert_entity_profile(
                        table="consignee_profiles",
                        key_col="consignee_key",
                        name_col="consignee_name",
                        key_val=consignee_key,
                        name_val=fingerprint.consignee_name,
                        is_flagged=is_flagged,
                        now=now,
                    )

                # Upsert route profile
                if route_key and fingerprint.origin_iso2 and fingerprint.port_of_entry:
                    self._upsert_route_profile(
                        route_key=route_key,
                        origin_iso2=fingerprint.origin_iso2,
                        port_of_entry=fingerprint.port_of_entry,
                        is_flagged=is_flagged,
                        now=now,
                    )

                # Update HS value baselines
                if unit_value_usd is not None and unit_value_usd > 0:
                    for hs_code in fingerprint.hs_codes:
                        hs_prefix = hs_code[:7].rstrip(".")  # 6-digit prefix, e.g. "8471.30"
                        if hs_prefix:
                            self._update_hs_baseline(hs_prefix, unit_value_usd, now)

                self._conn.execute("COMMIT")
                logger.debug("Recorded analysis %s (decision=%s)", analysis_id, decision)
                return analysis_id

            except sqlite3.Error as exc:
                self._conn.execute("ROLLBACK")
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

        This is the write path for the feedback loop (§4 of the architecture
        doc).  After writing the outcome row, this method updates the shipper,
        consignee, and route profiles with the decay-weighted outcome signal.

        Outcome semantics:
        - ``CONFIRMED_FRAUD``: increases risk weight for shipper/consignee/route.
        - ``CLEARED``: increases trust weight; may trigger auto-trust if threshold met.
        - ``UNRESOLVED``: stored but excluded from score updates.

        Idempotency rules (§4.4):
        - Only one outcome is allowed per analysis.
        - A resolved outcome (CONFIRMED_FRAUD or CLEARED) is immutable.
        - An UNRESOLVED outcome can be updated to CONFIRMED_FRAUD or CLEARED.

        Parameters
        ----------
        analysis_id:
            UUID returned by :meth:`record_shipment`.
        outcome:
            One of ``"CONFIRMED_FRAUD"``, ``"CLEARED"``, or ``"UNRESOLVED"``.
        officer_id:
            Optional officer identifier for audit logging.
        notes:
            Free-text notes (evidence summary, case reference, etc.).
        case_reference:
            Optional external case or seizure number.

        Raises
        ------
        InvalidOutcomeError
            If *outcome* is not one of the three valid values.
        RecordNotFoundError
            If *analysis_id* does not exist in ``shipment_history``.
        DuplicateOutcomeError
            If a resolved outcome already exists for this analysis_id.
        PatternDBError
            If the database write fails for any reason.
        """
        if outcome not in VALID_OUTCOMES:
            raise InvalidOutcomeError(
                f"Invalid outcome '{outcome}'. Must be one of: {sorted(VALID_OUTCOMES)}"
            )

        with self._lock:
            # --- Pre-condition checks ---
            row = self._conn.execute(
                "SELECT analysis_id, shipper_key, consignee_key, route_key, "
                "       hs_codes, unit_value_usd, analyzed_at, final_decision "
                "FROM shipment_history WHERE analysis_id = ?",
                (analysis_id,),
            ).fetchone()

            if row is None:
                raise RecordNotFoundError(
                    f"No analysis found with id '{analysis_id}'"
                )

            existing = self._conn.execute(
                "SELECT outcome_id, outcome FROM pattern_outcomes WHERE analysis_id = ?",
                (analysis_id,),
            ).fetchone()

            if existing is not None:
                if existing["outcome"] != OUTCOME_UNRESOLVED:
                    raise DuplicateOutcomeError(
                        f"Analysis '{analysis_id}' already has a resolved outcome "
                        f"('{existing['outcome']}').  Resolved outcomes are immutable."
                    )
                # Existing UNRESOLVED → allow update
                update_existing = True
            else:
                update_existing = False

            now = _utcnow()
            days_ago = _days_since(row["analyzed_at"])
            dw = _decay_weight(days_ago)

            try:
                self._conn.execute("BEGIN")

                if update_existing:
                    self._conn.execute(
                        """UPDATE pattern_outcomes
                           SET outcome=?, recorded_at=?, officer_id=?,
                               outcome_notes=?, case_reference=?
                           WHERE analysis_id=?""",
                        (outcome, now, officer_id, notes, case_reference, analysis_id),
                    )
                else:
                    self._conn.execute(
                        """INSERT INTO pattern_outcomes
                           (analysis_id, recorded_at, officer_id, outcome,
                            outcome_notes, case_reference)
                           VALUES (?,?,?,?,?,?)""",
                        (analysis_id, now, officer_id, outcome, notes, case_reference),
                    )

                # Apply profile updates for non-UNRESOLVED outcomes
                if outcome == OUTCOME_CONFIRMED_FRAUD:
                    self._apply_fraud_outcome(row, dw, now)
                elif outcome == OUTCOME_CLEARED:
                    self._apply_cleared_outcome(row, dw, now)
                    # Mark the analysis row so flag_frequency excludes it
                    self._conn.execute(
                        "UPDATE shipment_history SET outcome_cleared=1 WHERE analysis_id=?",
                        (analysis_id,),
                    )

                self._conn.execute("COMMIT")
                logger.info(
                    "Outcome %s recorded for analysis %s (officer=%s)",
                    outcome, analysis_id, officer_id,
                )

            except (sqlite3.Error, PatternDBError) as exc:
                self._conn.execute("ROLLBACK")
                if isinstance(exc, PatternDBError):
                    raise
                raise PatternDBError(f"Failed to record outcome: {exc}") from exc

    # ------------------------------------------------------------------
    # Public API — read
    # ------------------------------------------------------------------

    def get_shipper_profile(self, shipper_name: str) -> ShipperProfile:
        """Retrieve the pattern profile for a shipper entity.

        Parameters
        ----------
        shipper_name:
            Raw shipper name (normalized internally before lookup).

        Returns
        -------
        ShipperProfile
            Profile data.  If no profile row exists, returns a ShipperProfile
            with ``exists=False`` and all scores at their prior default values
            (reputation_score=0.167, history_count=0).

        Notes
        -----
        The ``reputation_score`` in the returned profile is the last-persisted
        value.  It reflects the state as of the most recent outcome or analysis
        write; it does not re-decay in real time.  For a fresh-computed score
        with current decay, use the :class:`PatternEngine` scoring methods.
        """
        key = _entity_key(shipper_name)
        row = self._conn.execute(
            "SELECT * FROM shipper_profiles WHERE shipper_key = ?", (key,)
        ).fetchone()

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

    def get_consignee_profile(self, consignee_name: str) -> ConsigneeProfile:
        """Retrieve the pattern profile for a consignee entity.

        Parameters
        ----------
        consignee_name:
            Raw consignee name (normalized internally before lookup).

        Returns
        -------
        ConsigneeProfile
            Profile data.  If no profile row exists, returns a ConsigneeProfile
            with ``exists=False`` and all scores at prior defaults.
        """
        key = _entity_key(consignee_name)
        row = self._conn.execute(
            "SELECT * FROM consignee_profiles WHERE consignee_key = ?", (key,)
        ).fetchone()

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

    def get_route_risk(self, origin: str, destination: str) -> RouteRisk:
        """Retrieve the historical fraud rate for a route corridor.

        Parameters
        ----------
        origin:
            2-letter ISO origin country code (e.g. ``"CN"``).
        destination:
            Port of entry name (e.g. ``"Los Angeles"``).  Note: this is the
            port name, not a country code, matching the ``route_key`` format
            ``<origin_iso2>|<port_of_entry>``.

        Returns
        -------
        RouteRisk
            Route statistics.  If no profile row exists, returns a RouteRisk
            with ``exists=False`` and ``fraud_rate=0.50`` (Jeffrey's prior,
            neutral — neither risky nor safe).
        """
        route_key = f"{origin}|{destination}"
        row = self._conn.execute(
            "SELECT * FROM route_risk_profiles WHERE route_key = ?", (route_key,)
        ).fetchone()

        if row is None:
            return RouteRisk(
                route_key=route_key,
                origin_iso2=origin,
                port_of_entry=destination,
                fraud_rate=0.5,   # Jeffrey's prior: neutral
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

    def get_hs_baseline(self, hs_code_prefix: str) -> HSBaseline:
        """Retrieve the running value distribution for an HS code prefix.

        Parameters
        ----------
        hs_code_prefix:
            The 6-digit HS prefix to query (e.g. ``"8471.30"``).  Longer codes
            are accepted and truncated to the first 7 characters.

        Returns
        -------
        HSBaseline
            Statistical baseline.  If no row exists or fewer than
            :data:`MIN_HS_SAMPLES_FOR_ANOMALY` samples have been recorded,
            returns an HSBaseline with ``exists=False`` and ``std_dev=None``.

        Notes
        -----
        The ``std_dev`` field is ``None`` until at least 2 samples have been
        recorded (variance is undefined for n < 2).
        """
        # Normalize the prefix
        prefix = hs_code_prefix[:7].rstrip(".")

        row = self._conn.execute(
            "SELECT * FROM hs_code_baselines WHERE hs_prefix = ?", (prefix,)
        ).fetchone()

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

    def reset(self) -> int:
        """Delete all learned data from every pattern learning table.

        Clears shipment_history, pattern_outcomes (via ON DELETE CASCADE),
        shipper_profiles, consignee_profiles, route_risk_profiles, and
        hs_code_baselines.  The schema_migrations table is intentionally
        preserved so that re-initialisation is safe and idempotent.

        Returns
        -------
        int
            Number of rows deleted from shipment_history (used for the
            audit log and the API response ``shipments_deleted`` field).

        Notes
        -----
        The operation runs in a single serialised write transaction.
        It is safe to call on a live database — in-flight reads see a
        consistent pre-reset snapshot; new reads after the commit see the
        empty state.
        """
        with self._lock:
            # Count before clearing so the audit entry and API response
            # reflect the actual number of records destroyed.
            count: int = self._conn.execute(
                "SELECT COUNT(*) FROM shipment_history"
            ).fetchone()[0]

            # Delete in dependency order inside a single explicit transaction.
            # pattern_outcomes has ON DELETE CASCADE from shipment_history, but
            # we delete it explicitly first to be safe with any FK constraint
            # enforcement state.  isolation_level=None (autocommit) means we
            # must issue BEGIN/COMMIT ourselves.
            self._conn.execute("BEGIN")
            try:
                self._conn.execute("DELETE FROM pattern_outcomes")
                self._conn.execute("DELETE FROM shipment_history")
                self._conn.execute("DELETE FROM shipper_profiles")
                self._conn.execute("DELETE FROM consignee_profiles")
                self._conn.execute("DELETE FROM route_risk_profiles")
                self._conn.execute("DELETE FROM hs_code_baselines")
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

            # Flush the WAL so the file size reflects the cleared state.
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        return count

    def get_summary_stats(self) -> dict:
        """Return aggregate statistics for the Pattern History panel.

        Returns
        -------
        dict with keys:
            total_shipments (int): total rows in shipment_history
            total_confirmed_fraud (int): rows with outcome=CONFIRMED_FRAUD
            top_riskiest_shippers (list[dict]): up to 5 shippers sorted by reputation_score desc
                each dict has: name, reputation_score, total_analyses, confirmed_fraud_count
            top_riskiest_routes (list[dict]): up to 5 routes sorted by fraud_rate desc,
                each dict has: origin_iso2, port_of_entry, fraud_rate, total_analyses
        """
        total_shipments: int = self._conn.execute(
            "SELECT COUNT(*) FROM shipment_history"
        ).fetchone()[0]

        total_confirmed_fraud: int = self._conn.execute(
            "SELECT COUNT(*) FROM pattern_outcomes WHERE outcome = 'CONFIRMED_FRAUD'"
        ).fetchone()[0]

        shipper_rows = self._conn.execute(
            """
            SELECT shipper_name, reputation_score, total_analyses, total_confirmed_fraud
            FROM shipper_profiles
            WHERE total_analyses > 0
            ORDER BY reputation_score DESC
            LIMIT 5
            """
        ).fetchall()
        top_riskiest_shippers = [
            {
                "name": r["shipper_name"],
                "reputation_score": round(r["reputation_score"], 4),
                "total_analyses": r["total_analyses"],
                "confirmed_fraud_count": r["total_confirmed_fraud"],
            }
            for r in shipper_rows
        ]

        route_rows = self._conn.execute(
            """
            SELECT origin_iso2, port_of_entry, fraud_rate, total_analyses
            FROM route_risk_profiles
            WHERE total_analyses > 0
            ORDER BY fraud_rate DESC
            LIMIT 5
            """
        ).fetchall()
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
    # Internal helpers — profile upsert
    # ------------------------------------------------------------------

    def _upsert_entity_profile(
        self,
        table: str,
        key_col: str,
        name_col: str,
        key_val: str,
        name_val: str,
        is_flagged: bool,
        now: str,
    ) -> None:
        """Create or update a shipper/consignee profile row.

        On INSERT, initializes the profile with the first analysis and computes
        the prior-based reputation score.  On UPDATE, increments all relevant
        counters and recomputes the decay-weighted running sums.

        Parameters
        ----------
        table:
            ``"shipper_profiles"`` or ``"consignee_profiles"``.
        key_col:
            Primary-key column name (``"shipper_key"`` or ``"consignee_key"``).
        name_col:
            Display-name column name.
        key_val:
            SHA-256 key for the entity.
        name_val:
            Raw entity name for display.
        is_flagged:
            True if the decision for this analysis was anything other than APPROVE.
        now:
            ISO-8601 timestamp string for this operation.
        """
        existing = self._conn.execute(
            f"SELECT * FROM {table} WHERE {key_col} = ?", (key_val,)
        ).fetchone()

        if existing is None:
            initial_score = _compute_shipper_reputation(0.0, 0.0)
            self._conn.execute(
                f"""INSERT INTO {table} (
                    {key_col}, {name_col}, first_seen_at, last_seen_at,
                    total_analyses, total_flagged,
                    weighted_analyses, weighted_flagged,
                    reputation_score, last_score_update
                ) VALUES (?,?,?,?,  ?,?,  ?,?,  ?,?)""",
                (
                    key_val, name_val, now, now,
                    1, int(is_flagged),
                    1.0, float(is_flagged),
                    initial_score, now,
                ),
            )
        else:
            # Decay existing weighted sums forward to now
            days_elapsed = _days_since(existing["last_score_update"])
            decay = _decay_weight(days_elapsed)

            w_analyses = existing["weighted_analyses"] * decay + 1.0
            w_flagged = existing["weighted_flagged"] * decay + float(is_flagged)
            w_fraud = existing["weighted_confirmed_fraud"] * decay
            w_cleared = existing["weighted_cleared"] * decay

            rep_score = _compute_shipper_reputation(w_fraud, w_cleared)
            if existing["is_trusted"]:
                rep_score = 0.0

            self._conn.execute(
                f"""UPDATE {table} SET
                    {name_col} = ?,
                    last_seen_at = ?,
                    total_analyses = total_analyses + 1,
                    total_flagged = total_flagged + ?,
                    weighted_analyses = ?,
                    weighted_flagged = ?,
                    weighted_confirmed_fraud = ?,
                    weighted_cleared = ?,
                    reputation_score = ?,
                    last_score_update = ?
                WHERE {key_col} = ?""",
                (
                    name_val, now,
                    int(is_flagged),
                    w_analyses, w_flagged, w_fraud, w_cleared,
                    rep_score, now,
                    key_val,
                ),
            )

    def _upsert_route_profile(
        self,
        route_key: str,
        origin_iso2: str,
        port_of_entry: str,
        is_flagged: bool,
        now: str,
    ) -> None:
        """Create or update a route risk profile row.

        Uses Jeffrey's prior (α₀=β₀=0.5) for the Bayesian fraud rate estimate.

        Parameters
        ----------
        route_key:
            Compound key string ``<origin_iso2>|<port_of_entry>``.
        origin_iso2:
            2-letter ISO origin country code.
        port_of_entry:
            Port of entry name.
        is_flagged:
            True if the decision was anything other than APPROVE.
        now:
            ISO-8601 timestamp string for this operation.
        """
        existing = self._conn.execute(
            "SELECT * FROM route_risk_profiles WHERE route_key = ?", (route_key,)
        ).fetchone()

        if existing is None:
            initial_rate = _compute_route_fraud_rate(0.0, 1.0)
            self._conn.execute(
                """INSERT INTO route_risk_profiles (
                    route_key, origin_iso2, port_of_entry,
                    first_seen_at, last_seen_at,
                    total_analyses, total_flagged,
                    weighted_analyses, weighted_flagged,
                    fraud_rate, last_score_update
                ) VALUES (?,?,?,?,?,  ?,?,  ?,?,  ?,?)""",
                (
                    route_key, origin_iso2, port_of_entry,
                    now, now,
                    1, int(is_flagged),
                    1.0, float(is_flagged),
                    initial_rate, now,
                ),
            )
        else:
            days_elapsed = _days_since(existing["last_score_update"])
            decay = _decay_weight(days_elapsed)

            w_analyses = existing["weighted_analyses"] * decay + 1.0
            w_flagged = existing["weighted_flagged"] * decay + float(is_flagged)
            w_fraud = existing["weighted_confirmed_fraud"] * decay
            w_cleared = existing["weighted_cleared"] * decay

            fraud_rate = _compute_route_fraud_rate(w_fraud, w_analyses)

            self._conn.execute(
                """UPDATE route_risk_profiles SET
                    last_seen_at = ?,
                    total_analyses = total_analyses + 1,
                    total_flagged = total_flagged + ?,
                    weighted_analyses = ?,
                    weighted_flagged = ?,
                    weighted_confirmed_fraud = ?,
                    weighted_cleared = ?,
                    fraud_rate = ?,
                    last_score_update = ?
                WHERE route_key = ?""",
                (
                    now,
                    int(is_flagged),
                    w_analyses, w_flagged, w_fraud, w_cleared,
                    fraud_rate, now,
                    route_key,
                ),
            )

    def _update_hs_baseline(
        self, hs_prefix: str, unit_value_usd: float, now: str
    ) -> None:
        """Incorporate a new unit value into the Welford running statistics.

        Parameters
        ----------
        hs_prefix:
            The HS code prefix key (e.g. ``"8471.30"``).
        unit_value_usd:
            Declared unit value in USD to add to the running statistics.
        now:
            ISO-8601 timestamp string.
        """
        existing = self._conn.execute(
            "SELECT * FROM hs_code_baselines WHERE hs_prefix = ?", (hs_prefix,)
        ).fetchone()

        if existing is None:
            self._conn.execute(
                """INSERT INTO hs_code_baselines
                   (hs_prefix, sample_count, running_mean, running_m2,
                    running_min, running_max, cached_stddev, last_updated)
                   VALUES (?,1,?,0.0,?,?,NULL,?)""",
                (hs_prefix, unit_value_usd, unit_value_usd, unit_value_usd, now),
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

            self._conn.execute(
                """UPDATE hs_code_baselines SET
                   sample_count=?, running_mean=?, running_m2=?,
                   running_min=?, running_max=?, cached_stddev=?, last_updated=?
                   WHERE hs_prefix=?""",
                (n, mean, m2, new_min, new_max, stddev, now, hs_prefix),
            )

    # ------------------------------------------------------------------
    # Internal helpers — outcome profile updates
    # ------------------------------------------------------------------

    def _apply_fraud_outcome(
        self, analysis_row: sqlite3.Row, decay_weight: float, now: str
    ) -> None:
        """Apply CONFIRMED_FRAUD signal to shipper, consignee, and route profiles.

        Called within an active transaction.

        Parameters
        ----------
        analysis_row:
            Full row from shipment_history for the analysis being resolved.
        decay_weight:
            Precomputed exp(-λ × days_since_analysis) for this outcome.
        now:
            ISO-8601 timestamp string.
        """
        shipper_key = analysis_row["shipper_key"]
        consignee_key = analysis_row["consignee_key"]
        route_key = analysis_row["route_key"]

        if shipper_key:
            self._apply_entity_fraud(
                table="shipper_profiles",
                key_col="shipper_key",
                key_val=shipper_key,
                decay_weight=decay_weight,
                now=now,
            )

        if consignee_key:
            self._apply_entity_fraud(
                table="consignee_profiles",
                key_col="consignee_key",
                key_val=consignee_key,
                decay_weight=decay_weight,
                now=now,
            )

        if route_key:
            self._apply_route_fraud(route_key, decay_weight, now)

    def _apply_cleared_outcome(
        self, analysis_row: sqlite3.Row, decay_weight: float, now: str
    ) -> None:
        """Apply CLEARED signal to shipper and consignee profiles.

        Route profiles are NOT updated on CLEARED outcomes (§4.3).

        Checks the auto-trust threshold after updating each profile:
        if weighted_cleared >= 20 and weighted_confirmed_fraud == 0,
        sets is_trusted = 1 automatically.

        Parameters
        ----------
        analysis_row:
            Full row from shipment_history for the analysis being resolved.
        decay_weight:
            Precomputed decay weight for this outcome.
        now:
            ISO-8601 timestamp string.
        """
        shipper_key = analysis_row["shipper_key"]
        consignee_key = analysis_row["consignee_key"]

        if shipper_key:
            self._apply_entity_cleared(
                table="shipper_profiles",
                key_col="shipper_key",
                key_val=shipper_key,
                decay_weight=decay_weight,
                now=now,
            )

        if consignee_key:
            self._apply_entity_cleared(
                table="consignee_profiles",
                key_col="consignee_key",
                key_val=consignee_key,
                decay_weight=decay_weight,
                now=now,
            )

    def _apply_entity_fraud(
        self,
        table: str,
        key_col: str,
        key_val: str,
        decay_weight: float,
        now: str,
    ) -> None:
        """Increment fraud weight on an entity profile and recompute reputation score.

        Parameters
        ----------
        table:
            ``"shipper_profiles"`` or ``"consignee_profiles"``.
        key_col:
            Primary key column name.
        key_val:
            Entity key value.
        decay_weight:
            Decay-weighted contribution of this fraud event.
        now:
            ISO-8601 timestamp for the score update.
        """
        existing = self._conn.execute(
            f"SELECT * FROM {table} WHERE {key_col} = ?", (key_val,)
        ).fetchone()
        if existing is None:
            return  # Profile may have been deleted; skip silently

        days_elapsed = _days_since(existing["last_score_update"])
        time_decay = _decay_weight(days_elapsed)

        w_fraud = existing["weighted_confirmed_fraud"] * time_decay + decay_weight
        w_cleared = existing["weighted_cleared"] * time_decay
        w_analyses = existing["weighted_analyses"] * time_decay
        w_flagged = existing["weighted_flagged"] * time_decay

        rep_score = _compute_shipper_reputation(w_fraud, w_cleared)

        self._conn.execute(
            f"""UPDATE {table} SET
                total_confirmed_fraud = total_confirmed_fraud + 1,
                weighted_confirmed_fraud = ?,
                weighted_analyses = ?,
                weighted_flagged = ?,
                weighted_cleared = ?,
                reputation_score = ?,
                last_score_update = ?
            WHERE {key_col} = ?""",
            (w_fraud, w_analyses, w_flagged, w_cleared, rep_score, now, key_val),
        )

    def _apply_entity_cleared(
        self,
        table: str,
        key_col: str,
        key_val: str,
        decay_weight: float,
        now: str,
    ) -> None:
        """Increment cleared weight on an entity profile and check auto-trust threshold.

        Parameters
        ----------
        table:
            ``"shipper_profiles"`` or ``"consignee_profiles"``.
        key_col:
            Primary key column name.
        key_val:
            Entity key value.
        decay_weight:
            Decay-weighted contribution of this cleared event.
        now:
            ISO-8601 timestamp for the score update.
        """
        existing = self._conn.execute(
            f"SELECT * FROM {table} WHERE {key_col} = ?", (key_val,)
        ).fetchone()
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
        # The 1e-6 epsilon guards against sub-millisecond floating-point decay
        # loss when many outcomes are recorded in rapid succession.
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

        self._conn.execute(
            f"""UPDATE {table} SET
                total_cleared = total_cleared + 1,
                weighted_cleared = ?,
                weighted_confirmed_fraud = ?,
                weighted_analyses = ?,
                weighted_flagged = ?,
                reputation_score = ?,
                is_trusted = CASE WHEN ? THEN 1 ELSE is_trusted END,
                trust_set_at = CASE WHEN ? THEN ? ELSE trust_set_at END,
                trust_set_by = CASE WHEN ? THEN 'system_auto' ELSE trust_set_by END,
                last_score_update = ?
            WHERE {key_col} = ?""",
            (
                w_cleared, w_fraud, w_analyses, w_flagged,
                rep_score,
                int(auto_trust), int(auto_trust), now, int(auto_trust),
                now, key_val,
            ),
        )

    def _apply_route_fraud(
        self, route_key: str, decay_weight: float, now: str
    ) -> None:
        """Increment confirmed fraud weight on a route profile and recompute fraud rate.

        Parameters
        ----------
        route_key:
            Compound route key string.
        decay_weight:
            Decay-weighted contribution of this fraud event.
        now:
            ISO-8601 timestamp for the score update.
        """
        existing = self._conn.execute(
            "SELECT * FROM route_risk_profiles WHERE route_key = ?", (route_key,)
        ).fetchone()
        if existing is None:
            return

        days_elapsed = _days_since(existing["last_score_update"])
        time_decay = _decay_weight(days_elapsed)

        w_fraud = existing["weighted_confirmed_fraud"] * time_decay + decay_weight
        w_analyses = existing["weighted_analyses"] * time_decay
        w_flagged = existing["weighted_flagged"] * time_decay
        w_cleared = existing["weighted_cleared"] * time_decay

        fraud_rate = _compute_route_fraud_rate(w_fraud, w_analyses)

        self._conn.execute(
            """UPDATE route_risk_profiles SET
               total_confirmed_fraud = total_confirmed_fraud + 1,
               weighted_confirmed_fraud = ?,
               weighted_analyses = ?,
               weighted_flagged = ?,
               weighted_cleared = ?,
               fraud_rate = ?,
               last_score_update = ?
            WHERE route_key = ?""",
            (w_fraud, w_analyses, w_flagged, w_cleared, fraud_rate, now, route_key),
        )
