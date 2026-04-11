"""
portguard/pattern_engine.py — Pattern detection engine for the Localized Pattern Learning system.

This module is the intelligence layer that sits between PatternDB (raw storage) and
the compliance analysis pipeline (api/app.py).  It is a *read-only* query layer: it
reads from PatternDB and returns structured signal objects.  No writes happen here —
all writes go through PatternDB.record_shipment() and PatternDB.record_outcome().

Architecture reference: docs/pattern_learning_architecture.md §§3–3.7
Integration point: §5 (injection after _assess_risk(), before _compute_score())

Public API
----------
PatternEngine(db: PatternDB)
    .score(request: ScoringRequest) → PatternScoreResult

All five signals from the architecture document are implemented:

  ShipperRiskSignal      — Bayesian reputation + recent flag frequency (§3.2, §3.5)
  ConsigneeRiskSignal    — same as shipper, independent profile (§3.6)
  RouteRiskSignal        — Bayesian fraud rate on origin→port corridor (§3.4)
  ValueAnomalySignal     — z-score vs HS-code historical baseline (§3.3)
  FrequencyAnomalySignal — shipper+consignee pair density in a rolling window (user spec)

Mathematical foundations
------------------------
Every numeric score in this module has an equation, not just a threshold:

  Shipper/consignee reputation:
      α = weighted_confirmed_fraud + 1  (informative innocent prior)
      β = weighted_cleared + 5
      score = α / (α + β)

  Route fraud rate:
      α = weighted_confirmed_fraud + 0.5  (Jeffrey's non-informative prior)
      β = (weighted_analyses - weighted_confirmed_fraud) + 0.5
      score = α / (α + β)

  Flag frequency (shipper):
      rate = W_flagged / W_total  (decay-weighted counts, 90-day window)
      score = sigmoid(8 × (rate - 0.40))   where sigmoid(x) = 1/(1+exp(-x))

  Value anomaly (z-score mapped to [0,1]):
      z = (unit_value - mean) / stddev
      if z >= -1.0: score = 0.0
      if z < -1.0:  score = min(1.0, (-z - 1.0) / 3.0)

  Pair frequency anomaly (Poisson-based p-value):
      Expected rate μ = historical_avg_appearances_per_7d (minimum floor 1.0)
      Observed k = decay-weighted count of this pair in past 7 days
      p = P(X ≥ k) under Poisson(μ)  — tail probability
      score = 1 - p  (low p = unusual = high score)

Composite score (§3.7):
      pattern_score = (
          0.30 × shipper_score +
          0.20 × consignee_score +
          0.20 × route_score +
          0.15 × flag_frequency_score +
          0.15 × value_anomaly_score
      )

Cold start (§3.7):
      if history_depth < 3: effective_pattern_score = pattern_score × 0.5

Confidence tiers are based on sample sizes for the entity/route/HS being scored,
so the output always reports how much data backed each signal.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from portguard.pattern_db import (
    DECAY_LAMBDA,
    MIN_HS_SAMPLES_FOR_ANOMALY,
    PatternDB,
    _compute_route_fraud_rate,
    _compute_shipper_reputation,
    _days_since,
    _decay_weight,
    _entity_key,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Composite score weights (must sum to 1.0)
W_SHIPPER: float = 0.30
W_CONSIGNEE: float = 0.20
W_ROUTE: float = 0.20
W_FLAG_FREQ: float = 0.15
W_VALUE_ANOMALY: float = 0.15

# Cold-start threshold: fewer prior analyses → apply 50% penalty
COLD_START_HISTORY_THRESHOLD: int = 3
COLD_START_MULTIPLIER: float = 0.5

# Flag frequency: sigmoid steepness and midpoint (§3.2)
SIGMOID_K: float = 8.0        # steepness
SIGMOID_MID: float = 0.40     # midpoint — 40% flag rate = score 0.50

# Route risk: minimum analyses before the score is considered reliable
ROUTE_MIN_ANALYSES: int = 5

# Value anomaly: minimum HS samples and z-score threshold (§3.3)
VALUE_ANOMALY_Z_THRESHOLD: float = -1.0    # z-scores above this = no signal
VALUE_ANOMALY_SCALE: float = 3.0           # z = -4 → score 1.0

# Frequency anomaly: rolling window and threshold
PAIR_FREQUENCY_WINDOW_DAYS: int = 7
PAIR_FREQUENCY_THRESHOLD: int = 3          # ≥ this many appearances triggers signal

# Confidence sample-size tiers
CONFIDENCE_HIGH_MIN_SAMPLES: int = 20
CONFIDENCE_MEDIUM_MIN_SAMPLES: int = 5

# Neutral score returned when insufficient history exists
COLD_START_NEUTRAL_SCORE: float = 0.5


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    """Signal severity levels, ordered low→critical."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Confidence(str, Enum):
    """How much historical data backed the signal computation."""

    LOW = "LOW"        # fewer than 5 samples
    MEDIUM = "MEDIUM"  # 5–19 samples
    HIGH = "HIGH"      # 20+ samples


# ---------------------------------------------------------------------------
# Signal dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ShipperRiskSignal:
    """Pattern signal derived from the shipper entity's historical profile.

    Combines two sub-signals:
    1. *Reputation score* — Bayesian Beta estimate of how often this shipper's
       shipments are confirmed as fraudulent vs cleared (§3.5).
    2. *Flag frequency score* — sigmoid-amplified decay-weighted flag rate over
       the past 90 days (§3.2).

    The signal score is a blend: 0.60 × reputation + 0.40 × flag_frequency,
    so a shipper that is recently problematic but has a clean long-term record
    still triggers (flag_freq dominates), while a shipper that looks clean
    recently but has confirmed historical fraud still registers (reputation
    dominates).

    Attributes
    ----------
    triggered:
        True if the score exceeds a meaningful threshold (> 0.25) or
        specific threshold triggers fired (≥2 flags/7d, ≥3 flags/30d, ≥5/90d).
    score:
        Blended signal score in [0.0, 1.0].
    reputation_score:
        Raw Bayesian Beta reputation score.
    flag_frequency_score:
        Sigmoid-amplified flag rate.
    flag_rate:
        Raw decay-weighted flag rate (W_flagged / W_total) over 90 days.
    total_analyses:
        Total number of prior analyses for this shipper (unweighted).
    confirmed_fraud_count:
        Number of confirmed fraud outcomes recorded for this shipper.
    recent_flag_count:
        Count of flagged shipments (decision != APPROVE) in the past 90 days
        (unweighted integer, for threshold trigger display).
    flags_7d:
        Count of flags in the past 7 days.
    flags_30d:
        Count of flags in the past 30 days.
    severity:
        Derived from score: < 0.25 → LOW, < 0.50 → MEDIUM, < 0.75 → HIGH,
        ≥ 0.75 → CRITICAL.
    confidence:
        Based on total_analyses sample size.
    explanation:
        Plain-English explanation string suitable for display to a compliance
        officer, always including specific numeric data points.
    is_trusted:
        True if the shipper has been manually or automatically trusted.
    """

    triggered: bool
    score: float
    reputation_score: float
    flag_frequency_score: float
    flag_rate: float
    total_analyses: int
    confirmed_fraud_count: int
    recent_flag_count: int
    flags_7d: int
    flags_30d: int
    severity: Severity
    confidence: Confidence
    explanation: str
    is_trusted: bool = False


@dataclass
class ConsigneeRiskSignal:
    """Pattern signal for the consignee entity.

    Identical algorithm to :class:`ShipperRiskSignal` but applied independently
    to consignee_profiles.  See ShipperRiskSignal for full attribute docs.

    Consignees and shippers are scored independently because a legitimate
    consignee may receive goods from a fraudulent shipper and vice versa.
    """

    triggered: bool
    score: float
    reputation_score: float
    flag_frequency_score: float
    flag_rate: float
    total_analyses: int
    confirmed_fraud_count: int
    recent_flag_count: int
    flags_7d: int
    flags_30d: int
    severity: Severity
    confidence: Confidence
    explanation: str
    is_trusted: bool = False


@dataclass
class RouteRiskSignal:
    """Pattern signal for the origin → port-of-entry corridor.

    Uses a Bayesian Beta estimate of P(fraud | this route) with Jeffrey's
    non-informative prior (α₀ = β₀ = 0.5).  The signal only fires if the
    corridor has at least :data:`ROUTE_MIN_ANALYSES` total analyses, or if the
    fraud rate is extreme enough to warrant flagging even on sparse data.

    Attributes
    ----------
    triggered:
        True if the corridor has ≥ ROUTE_MIN_ANALYSES history AND fraud_rate > 0.30,
        or if fraud_rate > 0.60 regardless of sample size (extreme signal).
    score:
        Bayesian fraud rate in [0.0, 1.0].
    fraud_rate:
        Bayesian estimate of P(confirmed_fraud | this route).
    total_analyses:
        Total shipments on this corridor.
    confirmed_fraud_count:
        Confirmed fraud outcomes on this corridor.
    weighted_confirmed_fraud:
        Decay-weighted confirmed fraud count used in the Bayesian computation.
    weighted_analyses:
        Decay-weighted total analyses used in the Bayesian computation.
    data_sufficient:
        True if total_analyses >= ROUTE_MIN_ANALYSES.
    severity:
        LOW < 0.20, MEDIUM < 0.40, HIGH < 0.60, CRITICAL ≥ 0.60.
    confidence:
        Based on total_analyses sample size.
    explanation:
        Plain-English explanation including numeric fraud rate and sample size.
    """

    triggered: bool
    score: float
    fraud_rate: float
    total_analyses: int
    confirmed_fraud_count: int
    weighted_confirmed_fraud: float
    weighted_analyses: float
    data_sufficient: bool
    severity: Severity
    confidence: Confidence
    explanation: str


@dataclass
class ValueAnomalySignal:
    """Pattern signal for declared value anomaly vs HS-code historical baseline.

    Only detects *undervaluation* (z-score < −1.0).  Overvaluation is not
    penalized — it is not a fraud indicator for customs purposes.

    Mapping (§3.3):
      z ≥ −1.0  → score = 0.0  (normal range)
      z = −2.0  → score = 0.33
      z = −3.0  → score = 0.67
      z = −4.0  → score = 1.00

    Attributes
    ----------
    triggered:
        True if z_score < VALUE_ANOMALY_Z_THRESHOLD (−1.0) and sample_count
        meets the minimum threshold.
    score:
        Anomaly score in [0.0, 1.0].
    z_score:
        Standardized deviation of this shipment's unit value from the
        historical mean for the HS prefix.  None if insufficient history.
    unit_value_usd:
        Declared unit value in USD for this shipment.
    hs_mean:
        Historical mean unit value for this HS prefix.
    hs_stddev:
        Historical standard deviation.
    hs_sample_count:
        Number of prior shipments in the HS baseline.
    hs_prefix:
        The HS code prefix used for the lookup.
    pct_below_mean:
        How far below the mean this value is, as a percentage.
    severity:
        LOW score < 0.25, MEDIUM < 0.50, HIGH < 0.75, CRITICAL ≥ 0.75.
    confidence:
        Based on hs_sample_count.
    explanation:
        Plain-English explanation including the unit value, mean, z-score,
        and what undervaluation implies for customs purposes.
    """

    triggered: bool
    score: float
    z_score: Optional[float]
    unit_value_usd: float
    hs_mean: float
    hs_stddev: Optional[float]
    hs_sample_count: int
    hs_prefix: str
    pct_below_mean: float
    severity: Severity
    confidence: Confidence
    explanation: str


@dataclass
class FrequencyAnomalySignal:
    """Pattern signal for anomalous appearance frequency of a shipper+consignee pair.

    A legitimate shipping pair might appear weekly or monthly.  A pair that
    appears more than 3 times in 7 days with consistent high-risk indicators
    may represent split-shipment evasion, where a large fraudulent shipment is
    broken into many small ones to stay under screening thresholds.

    This signal uses a Poisson model to estimate how surprising the observed
    frequency is relative to the pair's historical baseline rate.

    Algorithm:
      1. Compute historical average appearances per 7 days for this pair
         (from shipment_history, decay-weighted over the past 90 days,
         excluding the current shipment).
      2. Model the historical rate as a Poisson process with parameter μ.
         Use a floor of μ ≥ 1.0 to prevent over-sensitivity on new pairs.
      3. Count observed k = decay-weighted appearances in the past 7 days
         (excluding current).
      4. Compute tail probability p = P(X ≥ k | Poisson(μ)).
         Low p = unusual = high anomaly score.
      5. score = 1 − p, clamped to [0.0, 1.0].

    The score is clamped to 0.0 when k < PAIR_FREQUENCY_THRESHOLD (3) to
    avoid false positives for legitimate regular shipping partners.

    Attributes
    ----------
    triggered:
        True if decay-weighted appearances in the past 7 days ≥
        PAIR_FREQUENCY_THRESHOLD AND score > 0.50.
    score:
        Poisson tail probability complement in [0.0, 1.0].
    observed_count_7d:
        Decay-weighted count of this shipper+consignee pair in the past 7 days.
    historical_rate_per_7d:
        Historical average appearances per 7-day window.
    poisson_p_value:
        P(X ≥ observed_count | Poisson(historical_rate)).
    shipper_name:
        Shipper name for display.
    consignee_name:
        Consignee name for display.
    severity:
        LOW < 0.25, MEDIUM < 0.50, HIGH < 0.75, CRITICAL ≥ 0.75.
    confidence:
        Based on how much historical pair data is available.
    explanation:
        Plain-English explanation including the pair, count, historical rate,
        and why the frequency is unusual.
    """

    triggered: bool
    score: float
    observed_count_7d: float
    historical_rate_per_7d: float
    poisson_p_value: float
    shipper_name: str
    consignee_name: str
    severity: Severity
    confidence: Confidence
    explanation: str


# ---------------------------------------------------------------------------
# Scoring request and result
# ---------------------------------------------------------------------------


@dataclass
class ScoringRequest:
    """All inputs required by PatternEngine.score().

    This is assembled from the same data that populates ShipmentFingerprint,
    but is kept separate so the engine can be called before the full
    fingerprint is built (e.g., for pre-screening).

    Attributes
    ----------
    shipper_name:
        Raw shipper name from the document.
    consignee_name:
        Raw consignee name.
    origin_iso2:
        2-letter ISO origin country code (e.g. ``"CN"``).
    port_of_entry:
        Destination port name (e.g. ``"Los Angeles"``).
    hs_codes:
        List of HTS codes for the goods.
    declared_value_usd:
        Total declared customs value in USD.
    quantity:
        Number of units (used to compute unit_value_usd for anomaly detection).
    """

    # Multi-tenant scope — set to the authenticated org's UUID.
    # Defaults to '__system__' for backward compatibility.
    organization_id: str = "__system__"

    shipper_name: Optional[str] = None
    consignee_name: Optional[str] = None
    origin_iso2: Optional[str] = None
    port_of_entry: Optional[str] = None
    hs_codes: List[str] = field(default_factory=list)
    declared_value_usd: Optional[float] = None
    quantity: Optional[float] = None


@dataclass
class PatternScoreResult:
    """Full output of PatternEngine.score().

    Attributes
    ----------
    pattern_score:
        Composite score in [0.0, 1.0] — the weighted sum of all active
        signal scores, after cold-start adjustment.
    effective_pattern_score:
        Pattern score after cold-start penalty (= pattern_score if not cold
        start, = pattern_score × 0.5 if cold start applies).
    is_cold_start:
        True if history_depth < COLD_START_HISTORY_THRESHOLD (3).
    history_depth:
        Number of prior analyses found for the shipper entity.
    signals:
        All five signal objects, in the order:
        [ShipperRiskSignal, ConsigneeRiskSignal, RouteRiskSignal,
         ValueAnomalySignal, FrequencyAnomalySignal]
    triggered_signals:
        Subset of signals where .triggered is True.
    explanations:
        List of plain-English explanation strings from triggered signals,
        in descending severity order.
    overall_severity:
        The highest severity among triggered signals, or LOW if none triggered.
    shipper_score:
        The shipper signal's score (convenience accessor).
    consignee_score:
        The consignee signal's score.
    route_score:
        The route signal's score.
    value_anomaly_score:
        The value anomaly signal's score.
    frequency_score:
        The frequency anomaly signal's score.
    """

    pattern_score: float
    effective_pattern_score: float
    is_cold_start: bool
    history_depth: int
    signals: list
    triggered_signals: list
    explanations: List[str]
    overall_severity: Severity
    shipper_score: float
    consignee_score: float
    route_score: float
    value_anomaly_score: float
    frequency_score: float


# ---------------------------------------------------------------------------
# Internal math helpers
# ---------------------------------------------------------------------------


def _sigmoid(x: float) -> float:
    """Numerically stable sigmoid: 1 / (1 + exp(-x)).

    For very large positive x this avoids overflow by clipping.

    Parameters
    ----------
    x:
        Input value.

    Returns
    -------
    float
        Value in (0.0, 1.0).
    """
    if x >= 20.0:
        return 1.0
    if x <= -20.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def _flag_frequency_score(w_flagged: float, w_total: float) -> tuple[float, float]:
    """Compute decay-weighted flag rate and its sigmoid-amplified score.

    Parameters
    ----------
    w_flagged:
        Decay-weighted count of flagged analyses.
    w_total:
        Decay-weighted count of all analyses.

    Returns
    -------
    (rate, score)
        rate  — raw flag rate in [0.0, 1.0]
        score — sigmoid(8 × (rate − 0.40)), also in [0.0, 1.0]
    """
    if w_total <= 0.0:
        return 0.0, _sigmoid(SIGMOID_K * (0.0 - SIGMOID_MID))
    rate = min(1.0, w_flagged / w_total)
    score = _sigmoid(SIGMOID_K * (rate - SIGMOID_MID))
    return rate, score


def _value_anomaly_score(z: float) -> float:
    """Map a z-score to a value anomaly score in [0.0, 1.0].

    Only undervaluation (negative z) generates a score.  See §3.3.

    Parameters
    ----------
    z:
        Standardized deviation: (observed - mean) / stddev.

    Returns
    -------
    float
        0.0 if z ≥ -1.0; linearly scaled up to 1.0 at z = -4.0.
    """
    if z >= VALUE_ANOMALY_Z_THRESHOLD:
        return 0.0
    return min(1.0, (-z - 1.0) / VALUE_ANOMALY_SCALE)


def _poisson_tail_probability(mu: float, k: float) -> float:
    """Compute P(X ≥ k) for a Poisson-distributed random variable X with mean mu.

    Uses the exact Poisson CDF for small k, switching to a normal approximation
    for large k to avoid numerical overflow.

    Parameters
    ----------
    mu:
        Poisson rate parameter (> 0).
    k:
        Observed count.  Treated as floor(k) for CDF computation.

    Returns
    -------
    float
        Tail probability in [0.0, 1.0].  Low value = more unusual.
    """
    if mu <= 0.0:
        return 0.0
    k_int = max(0, int(math.floor(k)))
    if k_int == 0:
        return 1.0  # P(X ≥ 0) = 1.0 always

    # Exact Poisson CDF: P(X < k) = sum_{i=0}^{k-1} exp(-mu) * mu^i / i!
    # Use log-space for numerical stability
    if k_int > 200 or mu > 500:
        # Normal approximation: X ~ N(mu, mu)
        z = (k_int - mu) / math.sqrt(mu)
        # P(X ≥ k) ≈ 1 - Φ(z)  where Φ is the normal CDF
        p_less = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
        return max(0.0, min(1.0, 1.0 - p_less))

    log_mu = math.log(mu)
    log_factorial = 0.0
    log_poisson_pmf_sum = []

    for i in range(k_int):
        if i > 0:
            log_factorial += math.log(i)
        log_pmf = -mu + i * log_mu - log_factorial
        log_poisson_pmf_sum.append(log_pmf)

    # sum in log-space with the log-sum-exp trick
    if not log_poisson_pmf_sum:
        p_less = 0.0
    else:
        max_log = max(log_poisson_pmf_sum)
        p_less = math.exp(max_log) * sum(math.exp(lp - max_log) for lp in log_poisson_pmf_sum)
        p_less = min(1.0, p_less)

    return max(0.0, 1.0 - p_less)


def _severity_from_score(
    score: float,
    thresholds: tuple[float, float, float] = (0.25, 0.50, 0.75),
) -> Severity:
    """Map a numeric score to a Severity level.

    Parameters
    ----------
    score:
        Score in [0.0, 1.0].
    thresholds:
        (low_cutoff, medium_cutoff, high_cutoff).  Scores above high_cutoff
        are CRITICAL.

    Returns
    -------
    Severity
    """
    low, med, hi = thresholds
    if score < low:
        return Severity.LOW
    if score < med:
        return Severity.MEDIUM
    if score < hi:
        return Severity.HIGH
    return Severity.CRITICAL


def _confidence_from_samples(n: int) -> Confidence:
    """Map a sample count to a Confidence level.

    Parameters
    ----------
    n:
        Number of historical samples.

    Returns
    -------
    Confidence
        HIGH if n ≥ 20, MEDIUM if 5 ≤ n < 20, LOW otherwise.
    """
    if n >= CONFIDENCE_HIGH_MIN_SAMPLES:
        return Confidence.HIGH
    if n >= CONFIDENCE_MEDIUM_MIN_SAMPLES:
        return Confidence.MEDIUM
    return Confidence.LOW


# ---------------------------------------------------------------------------
# PatternEngine
# ---------------------------------------------------------------------------


class PatternEngine:
    """Read-only pattern detection engine that queries PatternDB and scores signals.

    Each call to :meth:`score` performs a series of read queries against the
    PatternDB to compute five independent risk signals for a shipment, combines
    them into a composite score, and returns a fully explained
    :class:`PatternScoreResult`.

    This class is stateless between calls (all state lives in PatternDB).

    Parameters
    ----------
    db:
        An open :class:`~portguard.pattern_db.PatternDB` instance.

    Examples
    --------
    >>> engine = PatternEngine(db)
    >>> request = ScoringRequest(
    ...     shipper_name="Acme Corp",
    ...     consignee_name="Widget Imports LLC",
    ...     origin_iso2="CN",
    ...     port_of_entry="Los Angeles",
    ...     hs_codes=["8471.30.0100"],
    ...     declared_value_usd=500.0,
    ...     quantity=100.0,
    ... )
    >>> result = engine.score(request)
    >>> print(result.effective_pattern_score)
    >>> print(result.explanations)
    """

    def __init__(self, db: PatternDB) -> None:
        """Initialise the engine with a PatternDB connection.

        Parameters
        ----------
        db:
            Open PatternDB instance.  The engine does not take ownership;
            the caller is responsible for closing it.
        """
        self._db = db

    def score(self, request: ScoringRequest) -> PatternScoreResult:
        """Compute all five pattern signals for a shipment and return a composite result.

        This is the single entry point for the pattern detection engine.  The
        caller passes a :class:`ScoringRequest` built from the document fields
        extracted by the analysis pipeline; the engine returns a fully scored
        and explained :class:`PatternScoreResult`.

        The method is read-only — it does not modify any database rows.

        Cold start handling:
            If fewer than :data:`COLD_START_HISTORY_THRESHOLD` prior analyses
            exist for the shipper, ``is_cold_start=True`` is set and
            ``effective_pattern_score = pattern_score × 0.5``.  If the shipper
            is completely unknown (0 analyses), the neutral explanation
            "Insufficient history for pattern analysis" is returned and the
            score is 0.50 × 0.5 = 0.25 effective (half of the neutral prior).

        Parameters
        ----------
        request:
            Populated :class:`ScoringRequest` with shipment fields.

        Returns
        -------
        PatternScoreResult
            Fully scored result with all five signals and plain-English
            explanations.  Never raises — errors in individual signal
            computations are caught and logged; the affected signal defaults
            to a safe zero score.
        """
        # Compute all five signals independently
        shipper_sig = self._compute_shipper_signal(request)
        consignee_sig = self._compute_consignee_signal(request)
        route_sig = self._compute_route_signal(request)
        value_sig = self._compute_value_anomaly_signal(request)
        freq_sig = self._compute_frequency_anomaly_signal(request)

        all_signals = [shipper_sig, consignee_sig, route_sig, value_sig, freq_sig]

        # Composite weighted score (§3.7)
        pattern_score = (
            W_SHIPPER * shipper_sig.score
            + W_CONSIGNEE * consignee_sig.score
            + W_ROUTE * route_sig.score
            + W_FLAG_FREQ * freq_sig.score      # flag_freq maps to freq signal
            + W_VALUE_ANOMALY * value_sig.score
        )
        pattern_score = max(0.0, min(1.0, pattern_score))

        # Cold-start adjustment
        history_depth = shipper_sig.total_analyses
        is_cold_start = history_depth < COLD_START_HISTORY_THRESHOLD
        if is_cold_start:
            effective_score = pattern_score * COLD_START_MULTIPLIER
        else:
            effective_score = pattern_score

        # Collect triggered signals and explanations
        triggered = [s for s in all_signals if s.triggered]
        severity_order = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}
        triggered_sorted = sorted(triggered, key=lambda s: severity_order.get(s.severity, 3))

        explanations: list[str] = [s.explanation for s in triggered_sorted]

        if not explanations and history_depth == 0:
            explanations = ["Insufficient history for pattern analysis."]

        overall_severity = (
            triggered_sorted[0].severity if triggered_sorted else Severity.LOW
        )

        return PatternScoreResult(
            pattern_score=pattern_score,
            effective_pattern_score=effective_score,
            is_cold_start=is_cold_start,
            history_depth=history_depth,
            signals=all_signals,
            triggered_signals=triggered_sorted,
            explanations=explanations,
            overall_severity=overall_severity,
            shipper_score=shipper_sig.score,
            consignee_score=consignee_sig.score,
            route_score=route_sig.score,
            value_anomaly_score=value_sig.score,
            frequency_score=freq_sig.score,
        )

    # ------------------------------------------------------------------
    # Individual signal computers
    # ------------------------------------------------------------------

    def _compute_shipper_signal(self, request: ScoringRequest) -> ShipperRiskSignal:
        """Compute the shipper risk signal.

        Retrieves the shipper profile from PatternDB and queries the 90-day
        flag history to compute both reputation and flag frequency scores.

        Parameters
        ----------
        request:
            Scoring request (only shipper_name is used).

        Returns
        -------
        ShipperRiskSignal
            Fully scored signal.  Returns a safe low-score signal if
            shipper_name is None or any DB query fails.
        """
        if not request.shipper_name:
            return _null_shipper_signal("(no shipper name provided)")

        try:
            org_id = request.organization_id
            profile = self._db.get_shipper_profile(request.shipper_name, organization_id=org_id)
            shipper_key = profile.shipper_key

            # Retrieve 90-day flag history from shipment_history
            rows = self._db._conn.execute(
                """
                SELECT analyzed_at, final_decision, outcome_cleared
                FROM shipment_history
                WHERE organization_id = ? AND shipper_key = ?
                  AND analyzed_at >= datetime('now', '-90 days')
                ORDER BY analyzed_at DESC
                """,
                (org_id, shipper_key),
            ).fetchall()

            # Decay-weighted counts and threshold triggers
            w_total = 0.0
            w_flagged = 0.0
            count_7d = 0
            count_30d = 0
            count_90d = 0
            flags_7d = 0
            flags_30d = 0

            for row in rows:
                days_ago = _days_since(row["analyzed_at"])
                dw = _decay_weight(days_ago)
                is_flagged = row["final_decision"] != "APPROVE"
                is_cleared = bool(row["outcome_cleared"])

                w_total += dw
                if days_ago <= 7:
                    count_7d += 1
                    if is_flagged:
                        flags_7d += 1
                if days_ago <= 30:
                    count_30d += 1
                    if is_flagged:
                        flags_30d += 1
                if days_ago <= 90:
                    count_90d += 1

                # Exclude cleared flags from frequency numerator (§4.3)
                if is_flagged and not is_cleared:
                    w_flagged += dw

            flag_rate, freq_score = _flag_frequency_score(w_flagged, w_total)
            recent_flag_count = sum(
                1 for row in rows
                if row["final_decision"] != "APPROVE"
                and not row["outcome_cleared"]
            )

            # Trusted override: clamp reputation to 0.0
            rep_score = profile.reputation_score
            if profile.is_trusted:
                rep_score = 0.0
                freq_score = 0.0

            # Blended signal score: reputation is the long-term view, frequency
            # is the short-term view.  60/40 split.
            blended = 0.60 * rep_score + 0.40 * freq_score
            blended = max(0.0, min(1.0, blended))

            # Threshold triggers
            triggered = (
                profile.is_trusted is False and (
                    blended > 0.25
                    or flags_7d >= 2
                    or flags_30d >= 3
                    or recent_flag_count >= 5
                )
            )

            severity = _severity_from_score(blended)
            confidence = _confidence_from_samples(profile.history_count)

            explanation = _shipper_explanation(
                name=request.shipper_name,
                profile=profile,
                rep_score=rep_score,
                freq_score=freq_score,
                blended=blended,
                flag_rate=flag_rate,
                flags_7d=flags_7d,
                flags_30d=flags_30d,
                recent_flag_count=recent_flag_count,
                triggered=triggered,
            )

            return ShipperRiskSignal(
                triggered=triggered,
                score=blended,
                reputation_score=rep_score,
                flag_frequency_score=freq_score,
                flag_rate=flag_rate,
                total_analyses=profile.history_count,
                confirmed_fraud_count=profile.total_confirmed_fraud,
                recent_flag_count=recent_flag_count,
                flags_7d=flags_7d,
                flags_30d=flags_30d,
                severity=severity,
                confidence=confidence,
                explanation=explanation,
                is_trusted=profile.is_trusted,
            )

        except Exception as exc:
            logger.warning("Shipper signal computation failed: %s", exc, exc_info=True)
            return _null_shipper_signal(request.shipper_name or "")

    def _compute_consignee_signal(self, request: ScoringRequest) -> ConsigneeRiskSignal:
        """Compute the consignee risk signal.

        Identical algorithm to :meth:`_compute_shipper_signal` but applied to
        consignee_profiles.

        Parameters
        ----------
        request:
            Scoring request (only consignee_name is used).

        Returns
        -------
        ConsigneeRiskSignal
            Fully scored signal.
        """
        if not request.consignee_name:
            return _null_consignee_signal("(no consignee name provided)")

        try:
            org_id = request.organization_id
            profile = self._db.get_consignee_profile(request.consignee_name, organization_id=org_id)
            consignee_key = profile.consignee_key

            rows = self._db._conn.execute(
                """
                SELECT analyzed_at, final_decision, outcome_cleared
                FROM shipment_history
                WHERE organization_id = ? AND consignee_key = ?
                  AND analyzed_at >= datetime('now', '-90 days')
                ORDER BY analyzed_at DESC
                """,
                (org_id, consignee_key),
            ).fetchall()

            w_total = 0.0
            w_flagged = 0.0
            flags_7d = 0
            flags_30d = 0
            recent_flag_count = 0

            for row in rows:
                days_ago = _days_since(row["analyzed_at"])
                dw = _decay_weight(days_ago)
                is_flagged = row["final_decision"] != "APPROVE"
                is_cleared = bool(row["outcome_cleared"])

                w_total += dw
                if days_ago <= 7 and is_flagged:
                    flags_7d += 1
                if days_ago <= 30 and is_flagged:
                    flags_30d += 1
                if is_flagged and not is_cleared:
                    w_flagged += dw
                    recent_flag_count += 1

            flag_rate, freq_score = _flag_frequency_score(w_flagged, w_total)

            rep_score = profile.reputation_score
            if profile.is_trusted:
                rep_score = 0.0
                freq_score = 0.0

            blended = max(0.0, min(1.0, 0.60 * rep_score + 0.40 * freq_score))

            triggered = (
                not profile.is_trusted and (
                    blended > 0.25
                    or flags_7d >= 2
                    or flags_30d >= 3
                    or recent_flag_count >= 5
                )
            )

            severity = _severity_from_score(blended)
            confidence = _confidence_from_samples(profile.history_count)

            explanation = _consignee_explanation(
                name=request.consignee_name,
                profile=profile,
                rep_score=rep_score,
                freq_score=freq_score,
                blended=blended,
                flag_rate=flag_rate,
                flags_7d=flags_7d,
                flags_30d=flags_30d,
                recent_flag_count=recent_flag_count,
                triggered=triggered,
            )

            return ConsigneeRiskSignal(
                triggered=triggered,
                score=blended,
                reputation_score=rep_score,
                flag_frequency_score=freq_score,
                flag_rate=flag_rate,
                total_analyses=profile.history_count,
                confirmed_fraud_count=profile.total_confirmed_fraud,
                recent_flag_count=recent_flag_count,
                flags_7d=flags_7d,
                flags_30d=flags_30d,
                severity=severity,
                confidence=confidence,
                explanation=explanation,
                is_trusted=profile.is_trusted,
            )

        except Exception as exc:
            logger.warning("Consignee signal computation failed: %s", exc, exc_info=True)
            return _null_consignee_signal(request.consignee_name or "")

    def _compute_route_signal(self, request: ScoringRequest) -> RouteRiskSignal:
        """Compute the route risk signal for the origin → port-of-entry corridor.

        Retrieves the route profile from PatternDB and applies the Bayesian
        Beta fraud rate formula (Jeffrey's prior, §3.4).  The signal is
        considered reliable only when total_analyses ≥ ROUTE_MIN_ANALYSES.

        Parameters
        ----------
        request:
            Scoring request (origin_iso2 and port_of_entry are used).

        Returns
        -------
        RouteRiskSignal
            Fully scored signal.
        """
        if not request.origin_iso2 or not request.port_of_entry:
            return _null_route_signal("(missing origin or port)")

        try:
            org_id = request.organization_id
            route = self._db.get_route_risk(request.origin_iso2, request.port_of_entry, organization_id=org_id)
            route_key = f"{request.origin_iso2}|{request.port_of_entry}"

            # Re-compute from live weighted values for accuracy
            if route.exists:
                # Read the actual weighted columns for a fresh Bayesian estimate
                row = self._db._conn.execute(
                    "SELECT weighted_confirmed_fraud, weighted_analyses, "
                    "       total_analyses, total_confirmed_fraud "
                    "FROM route_risk_profiles WHERE organization_id = ? AND route_key = ?",
                    (org_id, route_key),
                ).fetchone()
                if row:
                    wf = row["weighted_confirmed_fraud"]
                    wa = row["weighted_analyses"]
                    fraud_rate = _compute_route_fraud_rate(wf, wa)
                    total_analyses = row["total_analyses"]
                    total_fraud = row["total_confirmed_fraud"]
                else:
                    wf, wa = 0.0, 0.0
                    fraud_rate = route.fraud_rate
                    total_analyses = route.total_analyses
                    total_fraud = route.total_confirmed_fraud
            else:
                wf, wa = 0.0, 0.0
                fraud_rate = 0.50  # Jeffrey's prior: neutral
                total_analyses = 0
                total_fraud = 0

            data_sufficient = total_analyses >= ROUTE_MIN_ANALYSES

            # Trigger: (sufficient data AND rate > 30%) OR extreme rate > 60%
            triggered = (
                (data_sufficient and fraud_rate > 0.30)
                or fraud_rate > 0.60
            )

            severity = _severity_from_score(fraud_rate, thresholds=(0.20, 0.40, 0.60))
            confidence = _confidence_from_samples(total_analyses)

            explanation = _route_explanation(
                origin=request.origin_iso2,
                port=request.port_of_entry,
                fraud_rate=fraud_rate,
                total_analyses=total_analyses,
                total_fraud=total_fraud,
                data_sufficient=data_sufficient,
                triggered=triggered,
            )

            return RouteRiskSignal(
                triggered=triggered,
                score=fraud_rate,
                fraud_rate=fraud_rate,
                total_analyses=total_analyses,
                confirmed_fraud_count=total_fraud,
                weighted_confirmed_fraud=wf,
                weighted_analyses=wa,
                data_sufficient=data_sufficient,
                severity=severity,
                confidence=confidence,
                explanation=explanation,
            )

        except Exception as exc:
            logger.warning("Route signal computation failed: %s", exc, exc_info=True)
            return _null_route_signal(
                f"{request.origin_iso2}|{request.port_of_entry}"
            )

    def _compute_value_anomaly_signal(self, request: ScoringRequest) -> ValueAnomalySignal:
        """Compute the declared value anomaly signal vs HS code baseline.

        Retrieves the HS code baseline from PatternDB and computes the z-score
        of the declared unit value.  Only detects undervaluation (z < −1.0).

        Parameters
        ----------
        request:
            Scoring request.  declared_value_usd, quantity, and hs_codes are
            used.  Requires at least one HS code and a positive unit value.

        Returns
        -------
        ValueAnomalySignal
            Fully scored signal.  Returns an untriggered zero-score signal
            if the request lacks the required fields or if the HS baseline
            has insufficient history.
        """
        # Compute unit value
        unit_value: Optional[float] = None
        if (
            request.declared_value_usd is not None
            and request.quantity is not None
            and request.quantity > 0
        ):
            unit_value = request.declared_value_usd / request.quantity

        if unit_value is None or unit_value <= 0:
            return _null_value_signal("(no declared value or quantity)")

        if not request.hs_codes:
            return _null_value_signal("(no HS codes provided)")

        # Use the first HS code for baseline lookup
        hs_prefix = request.hs_codes[0][:7].rstrip(".")

        try:
            baseline = self._db.get_hs_baseline(hs_prefix, organization_id=request.organization_id)

            if (
                not baseline.exists
                or baseline.sample_count < MIN_HS_SAMPLES_FOR_ANOMALY
                or baseline.std_dev is None
                or baseline.std_dev == 0.0
            ):
                return ValueAnomalySignal(
                    triggered=False,
                    score=0.0,
                    z_score=None,
                    unit_value_usd=unit_value,
                    hs_mean=baseline.mean_unit_value if baseline.exists else 0.0,
                    hs_stddev=None,
                    hs_sample_count=baseline.sample_count,
                    hs_prefix=hs_prefix,
                    pct_below_mean=0.0,
                    severity=Severity.LOW,
                    confidence=Confidence.LOW,
                    explanation=(
                        f"Insufficient HS code baseline data for '{hs_prefix}' "
                        f"({baseline.sample_count} sample(s); need {MIN_HS_SAMPLES_FOR_ANOMALY}). "
                        "Value anomaly check skipped."
                    ),
                )

            z = (unit_value - baseline.mean_unit_value) / baseline.std_dev
            score = _value_anomaly_score(z)
            triggered = score > 0.0

            pct_below = 0.0
            if baseline.mean_unit_value > 0 and unit_value < baseline.mean_unit_value:
                pct_below = ((baseline.mean_unit_value - unit_value) / baseline.mean_unit_value) * 100.0

            severity = _severity_from_score(score)
            confidence = _confidence_from_samples(baseline.sample_count)

            explanation = _value_explanation(
                hs_prefix=hs_prefix,
                unit_value=unit_value,
                mean=baseline.mean_unit_value,
                stddev=baseline.std_dev,
                z=z,
                score=score,
                sample_count=baseline.sample_count,
                pct_below=pct_below,
                triggered=triggered,
            )

            return ValueAnomalySignal(
                triggered=triggered,
                score=score,
                z_score=z,
                unit_value_usd=unit_value,
                hs_mean=baseline.mean_unit_value,
                hs_stddev=baseline.std_dev,
                hs_sample_count=baseline.sample_count,
                hs_prefix=hs_prefix,
                pct_below_mean=pct_below,
                severity=severity,
                confidence=confidence,
                explanation=explanation,
            )

        except Exception as exc:
            logger.warning("Value anomaly signal failed: %s", exc, exc_info=True)
            return _null_value_signal(hs_prefix)

    def _compute_frequency_anomaly_signal(
        self, request: ScoringRequest
    ) -> FrequencyAnomalySignal:
        """Compute the shipper+consignee pair frequency anomaly signal.

        Models historical pair appearance rate as a Poisson process and
        computes the tail probability of the observed 7-day count.

        A high score indicates the pair is appearing far more frequently than
        their historical rate would predict — consistent with split-shipment
        evasion patterns.

        Parameters
        ----------
        request:
            Scoring request.  shipper_name and consignee_name are required.

        Returns
        -------
        FrequencyAnomalySignal
            Fully scored signal.
        """
        if not request.shipper_name or not request.consignee_name:
            return _null_frequency_signal(
                request.shipper_name or "(unknown)",
                request.consignee_name or "(unknown)",
            )

        try:
            org_id = request.organization_id
            shipper_key = _entity_key(request.shipper_name)
            consignee_key = _entity_key(request.consignee_name)

            # --- Count appearances in the past 7 days (decay-weighted) ---
            rows_7d = self._db._conn.execute(
                """
                SELECT analyzed_at FROM shipment_history
                WHERE organization_id = ? AND shipper_key = ? AND consignee_key = ?
                  AND analyzed_at >= datetime('now', '-7 days')
                ORDER BY analyzed_at DESC
                """,
                (org_id, shipper_key, consignee_key),
            ).fetchall()

            observed_w = sum(
                _decay_weight(_days_since(r["analyzed_at"])) for r in rows_7d
            )

            # --- Estimate historical rate from the past 90 days ---
            # Count how many 7-day windows in the past 90 days had ≥1 appearance,
            # then compute the average count per 7-day window.
            rows_90d = self._db._conn.execute(
                """
                SELECT analyzed_at FROM shipment_history
                WHERE organization_id = ? AND shipper_key = ? AND consignee_key = ?
                  AND analyzed_at >= datetime('now', '-90 days')
                ORDER BY analyzed_at DESC
                """,
                (org_id, shipper_key, consignee_key),
            ).fetchall()

            # Total decay-weighted count over 90 days / 90 * 7 = avg per 7-day window
            total_w_90d = sum(
                _decay_weight(_days_since(r["analyzed_at"])) for r in rows_90d
            )
            historical_rate = total_w_90d / 90.0 * 7.0  # avg per 7-day window
            # Floor at 1.0 to prevent over-sensitivity for new pairs
            mu = max(1.0, historical_rate)

            # --- Poisson tail probability ---
            p_value = _poisson_tail_probability(mu, observed_w)
            score = 1.0 - p_value

            # Clamp to 0.0 if below threshold — avoid signaling on normal patterns
            if observed_w < PAIR_FREQUENCY_THRESHOLD:
                score = 0.0

            triggered = (
                observed_w >= PAIR_FREQUENCY_THRESHOLD
                and score > 0.50
            )

            severity = _severity_from_score(score)
            confidence = _confidence_from_samples(len(rows_90d))

            explanation = _frequency_explanation(
                shipper=request.shipper_name,
                consignee=request.consignee_name,
                observed_w=observed_w,
                mu=mu,
                p_value=p_value,
                score=score,
                triggered=triggered,
            )

            return FrequencyAnomalySignal(
                triggered=triggered,
                score=score,
                observed_count_7d=observed_w,
                historical_rate_per_7d=mu,
                poisson_p_value=p_value,
                shipper_name=request.shipper_name,
                consignee_name=request.consignee_name,
                severity=severity,
                confidence=confidence,
                explanation=explanation,
            )

        except Exception as exc:
            logger.warning("Frequency anomaly signal failed: %s", exc, exc_info=True)
            return _null_frequency_signal(
                request.shipper_name or "", request.consignee_name or ""
            )


# ---------------------------------------------------------------------------
# Null (safe default) signal constructors
# ---------------------------------------------------------------------------

def _null_shipper_signal(name: str) -> ShipperRiskSignal:
    """Return a safe zero-score ShipperRiskSignal for error paths."""
    return ShipperRiskSignal(
        triggered=False, score=0.0, reputation_score=1.0 / 6.0,
        flag_frequency_score=0.0, flag_rate=0.0,
        total_analyses=0, confirmed_fraud_count=0,
        recent_flag_count=0, flags_7d=0, flags_30d=0,
        severity=Severity.LOW, confidence=Confidence.LOW,
        explanation=f"No pattern history available for shipper '{name}'.",
        is_trusted=False,
    )


def _null_consignee_signal(name: str) -> ConsigneeRiskSignal:
    """Return a safe zero-score ConsigneeRiskSignal for error paths."""
    return ConsigneeRiskSignal(
        triggered=False, score=0.0, reputation_score=1.0 / 6.0,
        flag_frequency_score=0.0, flag_rate=0.0,
        total_analyses=0, confirmed_fraud_count=0,
        recent_flag_count=0, flags_7d=0, flags_30d=0,
        severity=Severity.LOW, confidence=Confidence.LOW,
        explanation=f"No pattern history available for consignee '{name}'.",
        is_trusted=False,
    )


def _null_route_signal(route_key: str) -> RouteRiskSignal:
    """Return a safe neutral-score RouteRiskSignal for error/missing paths."""
    return RouteRiskSignal(
        triggered=False, score=0.5, fraud_rate=0.5,
        total_analyses=0, confirmed_fraud_count=0,
        weighted_confirmed_fraud=0.0, weighted_analyses=0.0,
        data_sufficient=False,
        severity=Severity.LOW, confidence=Confidence.LOW,
        explanation=f"No route history available for corridor '{route_key}'.",
    )


def _null_value_signal(context: str) -> ValueAnomalySignal:
    """Return a safe zero-score ValueAnomalySignal for error/insufficient-data paths."""
    return ValueAnomalySignal(
        triggered=False, score=0.0, z_score=None,
        unit_value_usd=0.0, hs_mean=0.0, hs_stddev=None,
        hs_sample_count=0, hs_prefix="", pct_below_mean=0.0,
        severity=Severity.LOW, confidence=Confidence.LOW,
        explanation=f"Value anomaly check not available: {context}.",
    )


def _null_frequency_signal(shipper: str, consignee: str) -> FrequencyAnomalySignal:
    """Return a safe zero-score FrequencyAnomalySignal for error paths."""
    return FrequencyAnomalySignal(
        triggered=False, score=0.0,
        observed_count_7d=0.0, historical_rate_per_7d=1.0, poisson_p_value=1.0,
        shipper_name=shipper, consignee_name=consignee,
        severity=Severity.LOW, confidence=Confidence.LOW,
        explanation="Insufficient pair history for frequency analysis.",
    )


# ---------------------------------------------------------------------------
# Plain-English explanation builders
# ---------------------------------------------------------------------------


def _shipper_explanation(
    *,
    name: str,
    profile,
    rep_score: float,
    freq_score: float,
    blended: float,
    flag_rate: float,
    flags_7d: int,
    flags_30d: int,
    recent_flag_count: int,
    triggered: bool,
) -> str:
    """Build a plain-English explanation for the shipper signal.

    Parameters
    ----------
    name:
        Shipper display name.
    profile:
        ShipperProfile from PatternDB.
    rep_score:
        Bayesian reputation score.
    freq_score:
        Sigmoid flag frequency score.
    blended:
        Blended signal score (0.6 × rep + 0.4 × freq).
    flag_rate:
        Decay-weighted flag rate over 90 days.
    flags_7d:
        Count of flags in the past 7 days.
    flags_30d:
        Count of flags in the past 30 days.
    recent_flag_count:
        Total uncleared flags in 90-day window.
    triggered:
        Whether this signal is considered triggered.

    Returns
    -------
    str
        Human-readable explanation.
    """
    if profile.is_trusted:
        return (
            f"Shipper '{name}' is marked as trusted "
            f"(total analyses: {profile.history_count}; "
            f"confirmed fraud: {profile.total_confirmed_fraud}; "
            f"cleared: {profile.total_cleared}). Reputation score: 0.0 (overridden)."
        )

    if profile.history_count == 0:
        return (
            f"Shipper '{name}' has no prior history in this system. "
            "Prior reputation score: 0.167 (informative innocent prior, α=1, β=5)."
        )

    parts = [
        f"Shipper '{name}': {profile.history_count} prior shipment(s) on record."
    ]

    if profile.total_confirmed_fraud > 0:
        parts.append(
            f"{profile.total_confirmed_fraud} confirmed fraud outcome(s) recorded "
            f"(Bayesian reputation score: {rep_score:.3f})."
        )

    if recent_flag_count > 0:
        parts.append(
            f"{recent_flag_count} flag(s) in the past 90 days "
            f"(decay-weighted flag rate: {flag_rate:.1%}; "
            f"frequency score: {freq_score:.3f})."
        )

    if flags_7d >= 2:
        parts.append(f"ALERT: {flags_7d} flag(s) in the past 7 days.")

    if flags_30d >= 3:
        parts.append(f"ALERT: {flags_30d} flag(s) in the past 30 days.")

    parts.append(f"Blended signal score: {blended:.3f}.")

    if not triggered:
        parts.append("No threshold triggers fired — pattern within expected range.")

    return " ".join(parts)


def _consignee_explanation(
    *,
    name: str,
    profile,
    rep_score: float,
    freq_score: float,
    blended: float,
    flag_rate: float,
    flags_7d: int,
    flags_30d: int,
    recent_flag_count: int,
    triggered: bool,
) -> str:
    """Build a plain-English explanation for the consignee signal.

    Same structure as :func:`_shipper_explanation` but with consignee framing.
    """
    if profile.is_trusted:
        return (
            f"Consignee '{name}' is marked as trusted "
            f"(analyses: {profile.history_count}; fraud: {profile.total_confirmed_fraud}; "
            f"cleared: {profile.total_cleared}). Reputation score: 0.0 (overridden)."
        )

    if profile.history_count == 0:
        return (
            f"Consignee '{name}' has no prior history. "
            "Prior reputation score: 0.167 (innocent prior)."
        )

    parts = [
        f"Consignee '{name}': {profile.history_count} prior shipment(s) on record."
    ]

    if profile.total_confirmed_fraud > 0:
        parts.append(
            f"{profile.total_confirmed_fraud} confirmed fraud outcome(s) "
            f"(reputation score: {rep_score:.3f})."
        )

    if recent_flag_count > 0:
        parts.append(
            f"{recent_flag_count} flag(s) in 90 days "
            f"(flag rate: {flag_rate:.1%}; frequency score: {freq_score:.3f})."
        )

    if flags_7d >= 2:
        parts.append(f"ALERT: {flags_7d} flag(s) in the past 7 days.")

    if flags_30d >= 3:
        parts.append(f"ALERT: {flags_30d} flag(s) in the past 30 days.")

    parts.append(f"Blended signal score: {blended:.3f}.")
    return " ".join(parts)


def _route_explanation(
    *,
    origin: str,
    port: str,
    fraud_rate: float,
    total_analyses: int,
    total_fraud: int,
    data_sufficient: bool,
    triggered: bool,
) -> str:
    """Build a plain-English explanation for the route signal."""
    corridor = f"{origin} → {port}"

    if total_analyses == 0:
        return (
            f"Route '{corridor}' has no prior history. "
            "Neutral Bayesian prior applied (Jeffrey's, α=β=0.5): fraud rate = 50.0%."
        )

    confidence_note = (
        f"{total_analyses} shipment(s) on record"
        if data_sufficient
        else f"Only {total_analyses} shipment(s) on record (need ≥{ROUTE_MIN_ANALYSES} for reliable estimate)"
    )

    parts = [
        f"Route '{corridor}': {confidence_note}.",
        f"Confirmed fraud: {total_fraud} / {total_analyses} "
        f"(Bayesian fraud rate: {fraud_rate:.1%}).",
    ]

    if triggered:
        parts.append(
            f"This corridor's historical fraud rate of {fraud_rate:.1%} "
            "exceeds the 30% alert threshold."
        )
    elif not data_sufficient:
        parts.append("Insufficient history for a reliable route risk estimate.")

    return " ".join(parts)


def _value_explanation(
    *,
    hs_prefix: str,
    unit_value: float,
    mean: float,
    stddev: float,
    z: float,
    score: float,
    sample_count: int,
    pct_below: float,
    triggered: bool,
) -> str:
    """Build a plain-English explanation for the value anomaly signal."""
    if not triggered:
        return (
            f"Declared unit value ${unit_value:,.2f} is within normal range "
            f"for HS '{hs_prefix}' (mean: ${mean:,.2f}, σ: ${stddev:,.2f}, "
            f"z={z:.2f}; based on {sample_count} historical shipments)."
        )

    return (
        f"Declared unit value ${unit_value:,.2f} for HS '{hs_prefix}' is "
        f"{pct_below:.1f}% below the historical mean of ${mean:,.2f} "
        f"(σ=${stddev:,.2f}, z={z:.2f}). "
        f"Anomaly score: {score:.3f} "
        f"(based on {sample_count} historical shipments). "
        "Undervaluation of this magnitude is a common indicator of customs fraud "
        "and/or duty evasion."
    )


def _frequency_explanation(
    *,
    shipper: str,
    consignee: str,
    observed_w: float,
    mu: float,
    p_value: float,
    score: float,
    triggered: bool,
) -> str:
    """Build a plain-English explanation for the frequency anomaly signal."""
    pair = f"'{shipper}' → '{consignee}'"

    if not triggered:
        return (
            f"Pair {pair}: {observed_w:.1f} decay-weighted appearance(s) "
            f"in the past 7 days (historical rate: {mu:.2f}/week; "
            f"Poisson p={p_value:.3f}). Frequency within expected range."
        )

    return (
        f"Pair {pair} has appeared {observed_w:.1f} time(s) (decay-weighted) "
        f"in the past 7 days. Historical rate: {mu:.2f} appearance(s)/week. "
        f"Poisson tail probability: p={p_value:.4f} — this frequency is "
        f"statistically unusual (anomaly score: {score:.3f}). "
        "Elevated shipment frequency between the same shipper-consignee pair "
        "may indicate split-shipment evasion tactics."
    )
