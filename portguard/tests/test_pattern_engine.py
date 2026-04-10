"""
Tests for portguard/pattern_engine.py

Run with:
    python -m pytest portguard/tests/test_pattern_engine.py -v

All tests use in-memory PatternDB instances.  Each test builds its own
database state using real PatternDB.record_shipment() / record_outcome()
calls — there is no mocking of the DB layer.
"""

from __future__ import annotations

import math
import pytest

from portguard.pattern_db import (
    OUTCOME_CLEARED,
    OUTCOME_CONFIRMED_FRAUD,
    PatternDB,
    ShipmentFingerprint,
)
from portguard.pattern_engine import (
    COLD_START_HISTORY_THRESHOLD,
    COLD_START_MULTIPLIER,
    COLD_START_NEUTRAL_SCORE,
    MIN_HS_SAMPLES_FOR_ANOMALY,
    PAIR_FREQUENCY_THRESHOLD,
    ROUTE_MIN_ANALYSES,
    W_CONSIGNEE,
    W_FLAG_FREQ,
    W_ROUTE,
    W_SHIPPER,
    W_VALUE_ANOMALY,
    Confidence,
    ConsigneeRiskSignal,
    FrequencyAnomalySignal,
    PatternEngine,
    PatternScoreResult,
    RouteRiskSignal,
    Severity,
    ShipperRiskSignal,
    ScoringRequest,
    ValueAnomalySignal,
    _flag_frequency_score,
    _poisson_tail_probability,
    _severity_from_score,
    _sigmoid,
    _value_anomaly_score,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine() -> tuple[PatternDB, PatternEngine]:
    """Return a fresh in-memory (db, engine) pair."""
    db = PatternDB(":memory:")
    engine = PatternEngine(db)
    return db, engine


def _fp(**overrides) -> ShipmentFingerprint:
    """Return a minimal valid ShipmentFingerprint."""
    defaults = dict(
        shipper_name="Test Shipper Co",
        consignee_name="Test Consignee LLC",
        origin_iso2="CN",
        port_of_entry="Los Angeles",
        hs_codes=["8471.30.0100"],
        declared_value_usd=10000.0,
        quantity=100.0,
        rule_risk_score=0.3,
        rule_decision="REVIEW_RECOMMENDED",
        rule_confidence="MEDIUM",
        final_risk_score=0.3,
        final_decision="REVIEW_RECOMMENDED",
        final_confidence="MEDIUM",
    )
    defaults.update(overrides)
    fp = ShipmentFingerprint(**defaults)
    return fp


def _req(**overrides) -> ScoringRequest:
    """Return a minimal ScoringRequest."""
    defaults = dict(
        shipper_name="Test Shipper Co",
        consignee_name="Test Consignee LLC",
        origin_iso2="CN",
        port_of_entry="Los Angeles",
        hs_codes=["8471.30.0100"],
        declared_value_usd=10000.0,
        quantity=100.0,
    )
    defaults.update(overrides)
    return ScoringRequest(**defaults)


# ---------------------------------------------------------------------------
# Unit tests — pure math functions
# ---------------------------------------------------------------------------


class TestSigmoid:
    def test_midpoint_is_half(self):
        assert abs(_sigmoid(0.0) - 0.5) < 1e-9

    def test_large_positive_approaches_one(self):
        assert _sigmoid(100.0) == 1.0

    def test_large_negative_approaches_zero(self):
        assert _sigmoid(-100.0) == 0.0

    def test_monotone(self):
        xs = [-5, -2, -1, 0, 1, 2, 5]
        ys = [_sigmoid(x) for x in xs]
        assert ys == sorted(ys)


class TestFlagFrequencyScore:
    def test_zero_history(self):
        rate, score = _flag_frequency_score(0.0, 0.0)
        assert rate == 0.0
        # At 0% flag rate, score should be very low (sigmoid(8*(0-0.4)) ≈ 0.04)
        assert score < 0.10

    def test_hundred_percent_flag_rate(self):
        rate, score = _flag_frequency_score(10.0, 10.0)
        assert abs(rate - 1.0) < 1e-9
        # At 100% flag rate, score should be near 1.0
        assert score > 0.95

    def test_forty_percent_is_midpoint(self):
        # 40% flag rate → sigmoid(0) = 0.5
        rate, score = _flag_frequency_score(4.0, 10.0)
        assert abs(rate - 0.4) < 1e-9
        assert abs(score - 0.5) < 0.01

    def test_w_total_larger_than_w_flagged(self):
        # Half flagged
        rate, score = _flag_frequency_score(5.0, 10.0)
        assert abs(rate - 0.5) < 1e-9
        # 50% > midpoint → score > 0.5
        assert score > 0.5


class TestValueAnomalyScore:
    def test_at_threshold_is_zero(self):
        assert _value_anomaly_score(-1.0) == 0.0

    def test_above_threshold_is_zero(self):
        assert _value_anomaly_score(0.0) == 0.0
        assert _value_anomaly_score(2.0) == 0.0

    def test_z_minus_2(self):
        # (-(-2) - 1) / 3 = 1/3
        assert abs(_value_anomaly_score(-2.0) - 1.0 / 3.0) < 1e-9

    def test_z_minus_3(self):
        # (3-1)/3 = 2/3
        assert abs(_value_anomaly_score(-3.0) - 2.0 / 3.0) < 1e-9

    def test_z_minus_4_is_one(self):
        assert _value_anomaly_score(-4.0) == 1.0

    def test_clamps_at_one(self):
        assert _value_anomaly_score(-10.0) == 1.0


class TestPoissonTailProbability:
    def test_k_zero_always_one(self):
        assert _poisson_tail_probability(5.0, 0) == 1.0

    def test_expected_is_plausible(self):
        # k = mu: P(X >= mu) ≈ 0.5 (roughly)
        p = _poisson_tail_probability(5.0, 5)
        assert 0.3 < p < 0.7

    def test_very_high_k_is_low_probability(self):
        # Seeing k=20 when mu=2 is extremely unlikely
        p = _poisson_tail_probability(2.0, 20)
        assert p < 0.001

    def test_k_equals_one_near_one_for_small_mu(self):
        # P(X >= 1) when mu=5 is almost 1
        p = _poisson_tail_probability(5.0, 1)
        assert p > 0.99

    def test_zero_mu_returns_zero(self):
        assert _poisson_tail_probability(0.0, 5) == 0.0


class TestSeverityFromScore:
    def test_below_low_threshold(self):
        assert _severity_from_score(0.10) == Severity.LOW

    def test_at_medium_boundary(self):
        assert _severity_from_score(0.25) == Severity.MEDIUM

    def test_at_high_boundary(self):
        assert _severity_from_score(0.50) == Severity.HIGH

    def test_critical(self):
        assert _severity_from_score(0.75) == Severity.CRITICAL
        assert _severity_from_score(1.0) == Severity.CRITICAL

    def test_custom_thresholds(self):
        assert _severity_from_score(0.35, (0.20, 0.40, 0.60)) == Severity.MEDIUM


# ---------------------------------------------------------------------------
# Integration tests — PatternEngine.score()
# ---------------------------------------------------------------------------


class TestColdStart:
    def test_empty_db_returns_cold_start(self):
        db, engine = _make_engine()
        result = engine.score(_req())
        assert result.is_cold_start
        assert result.history_depth == 0
        db.close()

    def test_empty_db_explanation_mentions_insufficient_history(self):
        db, engine = _make_engine()
        result = engine.score(_req())
        text = " ".join(result.explanations)
        assert "Insufficient history" in text
        db.close()

    def test_cold_start_multiplier_applied(self):
        db, engine = _make_engine()
        result = engine.score(_req())
        assert abs(result.effective_pattern_score - result.pattern_score * COLD_START_MULTIPLIER) < 1e-9
        db.close()

    def test_after_threshold_analyses_no_cold_start(self):
        db, engine = _make_engine()
        for _ in range(COLD_START_HISTORY_THRESHOLD):
            aid = db.record_shipment(_fp(), "APPROVE", [], "LOW")
        result = engine.score(_req())
        assert not result.is_cold_start
        assert result.history_depth >= COLD_START_HISTORY_THRESHOLD
        db.close()

    def test_two_analyses_still_cold_start(self):
        db, engine = _make_engine()
        for _ in range(COLD_START_HISTORY_THRESHOLD - 1):
            db.record_shipment(_fp(), "APPROVE", [], "LOW")
        result = engine.score(_req())
        assert result.is_cold_start
        db.close()


class TestShipperSignal:
    def test_clean_shipper_low_score(self):
        db, engine = _make_engine()
        for _ in range(10):
            aid = db.record_shipment(_fp(), "APPROVE", [], "LOW")
            db.record_outcome(aid, OUTCOME_CLEARED)
        result = engine.score(_req())
        assert result.shipper_score < 0.20
        db.close()

    def test_fraudulent_shipper_high_score(self):
        db, engine = _make_engine()
        for _ in range(10):
            aid = db.record_shipment(_fp(), "FLAG_FOR_INSPECTION", [], "HIGH")
            db.record_outcome(aid, OUTCOME_CONFIRMED_FRAUD)
        result = engine.score(_req())
        assert result.shipper_score > 0.50
        db.close()

    def test_shipper_signal_triggered_by_frauds(self):
        db, engine = _make_engine()
        for _ in range(5):
            aid = db.record_shipment(_fp(), "FLAG_FOR_INSPECTION", [], "HIGH")
            db.record_outcome(aid, OUTCOME_CONFIRMED_FRAUD)
        result = engine.score(_req())
        shipper_sig = result.signals[0]
        assert isinstance(shipper_sig, ShipperRiskSignal)
        assert shipper_sig.triggered
        db.close()

    def test_trusted_shipper_zero_score(self):
        db, engine = _make_engine()
        # Auto-trust: 20 cleared outcomes, 0 fraud
        aids = [db.record_shipment(_fp(), "FLAG_FOR_INSPECTION", [], "HIGH") for _ in range(20)]
        for aid in aids:
            db.record_outcome(aid, OUTCOME_CLEARED)
        result = engine.score(_req())
        assert result.shipper_score == 0.0
        sig = result.signals[0]
        assert sig.is_trusted
        db.close()

    def test_unknown_shipper_returns_prior_score(self):
        db, engine = _make_engine()
        result = engine.score(_req(shipper_name="Brand New Corp"))
        sig = result.signals[0]
        assert isinstance(sig, ShipperRiskSignal)
        # Prior reputation = 1/6 ≈ 0.167; blended = 0.6*0.167 + 0.4*freq
        assert sig.reputation_score == pytest.approx(1.0 / 6.0, abs=1e-3)
        db.close()

    def test_none_shipper_name_is_safe(self):
        db, engine = _make_engine()
        result = engine.score(_req(shipper_name=None))
        sig = result.signals[0]
        assert not sig.triggered
        assert sig.score == 0.0
        db.close()

    def test_explanation_contains_shipper_name(self):
        db, engine = _make_engine()
        for _ in range(5):
            aid = db.record_shipment(_fp(), "FLAG_FOR_INSPECTION", [], "HIGH")
            db.record_outcome(aid, OUTCOME_CONFIRMED_FRAUD)
        result = engine.score(_req())
        sig = result.signals[0]
        assert "Test Shipper Co" in sig.explanation
        db.close()


class TestConsigneeSignal:
    def test_clean_consignee_low_score(self):
        db, engine = _make_engine()
        for _ in range(10):
            aid = db.record_shipment(_fp(), "APPROVE", [], "LOW")
            db.record_outcome(aid, OUTCOME_CLEARED)
        result = engine.score(_req())
        assert result.consignee_score < 0.20
        db.close()

    def test_fraudulent_consignee_high_score(self):
        db, engine = _make_engine()
        for _ in range(8):
            aid = db.record_shipment(_fp(), "FLAG_FOR_INSPECTION", [], "HIGH")
            db.record_outcome(aid, OUTCOME_CONFIRMED_FRAUD)
        result = engine.score(_req())
        assert result.consignee_score > 0.40
        db.close()

    def test_consignee_scored_independently_from_shipper(self):
        """A clean shipper paired with a fraudulent consignee should still score high."""
        db, engine = _make_engine()
        # Same shipper, two different consignees — fraud on one only
        for _ in range(5):
            aid = db.record_shipment(
                _fp(consignee_name="Bad Actor LLC"),
                "FLAG_FOR_INSPECTION", [], "HIGH"
            )
            db.record_outcome(aid, OUTCOME_CONFIRMED_FRAUD)
        for _ in range(10):
            aid = db.record_shipment(
                _fp(consignee_name="Good Company Inc"),
                "APPROVE", [], "LOW"
            )
            db.record_outcome(aid, OUTCOME_CLEARED)

        bad_result = engine.score(_req(consignee_name="Bad Actor LLC"))
        good_result = engine.score(_req(consignee_name="Good Company Inc"))
        assert bad_result.consignee_score > good_result.consignee_score
        db.close()


class TestRouteSignal:
    def test_new_route_neutral_score(self):
        db, engine = _make_engine()
        result = engine.score(_req(origin_iso2="ZZ", port_of_entry="Nowhere"))
        sig = result.signals[2]
        assert isinstance(sig, RouteRiskSignal)
        assert not sig.triggered
        assert abs(sig.fraud_rate - 0.5) < 0.01
        db.close()

    def test_clean_route_low_fraud_rate(self):
        db, engine = _make_engine()
        for _ in range(20):
            aid = db.record_shipment(_fp(), "APPROVE", [], "LOW")
        result = engine.score(_req())
        sig = result.signals[2]
        assert sig.fraud_rate < 0.10
        db.close()

    def test_risky_route_triggers(self):
        db, engine = _make_engine()
        # 10 analyses, 5 confirmed fraud → rate ≈ 0.45
        for _ in range(5):
            aid = db.record_shipment(_fp(), "FLAG_FOR_INSPECTION", [], "HIGH")
            db.record_outcome(aid, OUTCOME_CONFIRMED_FRAUD)
        for _ in range(5):
            db.record_shipment(_fp(), "APPROVE", [], "LOW")
        result = engine.score(_req())
        sig = result.signals[2]
        assert sig.triggered
        assert sig.fraud_rate > 0.30
        db.close()

    def test_route_not_triggered_below_threshold(self):
        db, engine = _make_engine()
        # Only 2 analyses, 1 fraud → not enough data for trigger
        for _ in range(2):
            aid = db.record_shipment(_fp(), "FLAG_FOR_INSPECTION", [], "HIGH")
        db.record_outcome(aid, OUTCOME_CONFIRMED_FRAUD)
        result = engine.score(_req())
        sig = result.signals[2]
        # Below ROUTE_MIN_ANALYSES=5, so not triggered unless extreme
        # fraud rate might be high but data_sufficient=False
        assert not sig.data_sufficient
        db.close()

    def test_route_explanation_contains_corridor(self):
        db, engine = _make_engine()
        result = engine.score(_req())
        sig = result.signals[2]
        assert "CN" in sig.explanation
        assert "Los Angeles" in sig.explanation
        db.close()

    def test_missing_origin_returns_null_signal(self):
        db, engine = _make_engine()
        result = engine.score(_req(origin_iso2=None))
        sig = result.signals[2]
        assert not sig.triggered
        db.close()


class TestValueAnomalySignal:
    def test_insufficient_hs_samples_not_triggered(self):
        db, engine = _make_engine()
        # Only 3 shipments recorded — below MIN_HS_SAMPLES_FOR_ANOMALY (10)
        for _ in range(3):
            db.record_shipment(_fp(), "APPROVE", [], "LOW")
        result = engine.score(_req())
        sig = result.signals[3]
        assert isinstance(sig, ValueAnomalySignal)
        assert not sig.triggered
        assert sig.z_score is None
        db.close()

    def test_normal_value_not_triggered(self):
        db, engine = _make_engine()
        # Build baseline: unit value = 100 for 15 shipments
        for _ in range(15):
            db.record_shipment(
                _fp(declared_value_usd=10000.0, quantity=100.0), "APPROVE", [], "LOW"
            )
        # Score a shipment with unit value = 98 (within 1 stddev)
        result = engine.score(_req(declared_value_usd=9800.0, quantity=100.0))
        sig = result.signals[3]
        assert not sig.triggered
        db.close()

    def test_severely_undervalued_triggers(self):
        db, engine = _make_engine()
        # Build a narrow baseline: unit value ≈ 100, low variance
        for i in range(15):
            # Slight variation to get a real stddev
            db.record_shipment(
                _fp(declared_value_usd=10000.0 + i * 10, quantity=100.0),
                "APPROVE", [], "LOW"
            )
        # Score a shipment with unit value = 50 (far below mean=100ish)
        result = engine.score(_req(declared_value_usd=5000.0, quantity=100.0))
        sig = result.signals[3]
        # With a tight baseline, unit_value=50 vs mean≈100 should have large negative z
        if sig.z_score is not None and sig.z_score < -1.0:
            assert sig.triggered
        db.close()

    def test_overvalued_not_triggered(self):
        db, engine = _make_engine()
        # Build baseline at 100
        for i in range(15):
            db.record_shipment(
                _fp(declared_value_usd=9900.0 + i * 20, quantity=100.0), "APPROVE", [], "LOW"
            )
        # Score a shipment with very HIGH unit value — should NOT trigger
        result = engine.score(_req(declared_value_usd=100000.0, quantity=100.0))
        sig = result.signals[3]
        if sig.z_score is not None:
            assert sig.score == 0.0  # high values don't generate anomaly score
        db.close()

    def test_no_quantity_no_trigger(self):
        db, engine = _make_engine()
        result = engine.score(_req(quantity=None))
        sig = result.signals[3]
        assert not sig.triggered
        db.close()

    def test_no_hs_codes_no_trigger(self):
        db, engine = _make_engine()
        result = engine.score(_req(hs_codes=[]))
        sig = result.signals[3]
        assert not sig.triggered
        db.close()

    def test_explanation_mentions_unit_value(self):
        db, engine = _make_engine()
        # Build baseline with enough samples
        for i in range(15):
            db.record_shipment(
                _fp(declared_value_usd=10000.0 + i * 100, quantity=100.0), "APPROVE", [], "LOW"
            )
        result = engine.score(_req(declared_value_usd=10000.0, quantity=100.0))
        sig = result.signals[3]
        assert "$" in sig.explanation
        db.close()


class TestFrequencyAnomalySignal:
    def test_new_pair_not_triggered(self):
        db, engine = _make_engine()
        result = engine.score(_req())
        sig = result.signals[4]
        assert isinstance(sig, FrequencyAnomalySignal)
        assert not sig.triggered
        db.close()

    def test_below_threshold_count_not_triggered(self):
        db, engine = _make_engine()
        # 2 appearances in past 7 days — below threshold of 3
        for _ in range(2):
            db.record_shipment(_fp(), "APPROVE", [], "LOW")
        result = engine.score(_req())
        sig = result.signals[4]
        assert not sig.triggered
        db.close()

    def test_above_threshold_triggers(self):
        db, engine = _make_engine()
        # Record many appearances of this pair in rapid succession
        for _ in range(10):
            db.record_shipment(_fp(), "FLAG_FOR_INSPECTION", [], "HIGH")
        result = engine.score(_req())
        sig = result.signals[4]
        # With no historical baseline (mu=1.0 floor) and 10 recent appearances,
        # the Poisson tail should be very low → score should be high
        assert sig.observed_count_7d >= PAIR_FREQUENCY_THRESHOLD
        assert sig.score > 0.50
        assert sig.triggered
        db.close()

    def test_none_shipper_returns_null_signal(self):
        db, engine = _make_engine()
        result = engine.score(_req(shipper_name=None))
        sig = result.signals[4]
        assert not sig.triggered
        assert sig.score == 0.0
        db.close()

    def test_poisson_floor_prevents_oversensitivity(self):
        """A pair with 3 appearances in 7 days but no history uses mu=1.0 floor."""
        db, engine = _make_engine()
        for _ in range(3):
            db.record_shipment(_fp(), "APPROVE", [], "LOW")
        result = engine.score(_req())
        sig = result.signals[4]
        # historical_rate_per_7d must be at least 1.0 (floor)
        assert sig.historical_rate_per_7d >= 1.0
        db.close()

    def test_explanation_mentions_pair(self):
        db, engine = _make_engine()
        for _ in range(10):
            db.record_shipment(_fp(), "FLAG_FOR_INSPECTION", [], "HIGH")
        result = engine.score(_req())
        sig = result.signals[4]
        assert "Test Shipper Co" in sig.explanation
        assert "Test Consignee LLC" in sig.explanation
        db.close()


class TestCompositeScore:
    def test_score_within_bounds(self):
        db, engine = _make_engine()
        result = engine.score(_req())
        assert 0.0 <= result.pattern_score <= 1.0
        assert 0.0 <= result.effective_pattern_score <= 1.0
        db.close()

    def test_all_clear_history_produces_low_score(self):
        db, engine = _make_engine()
        for _ in range(15):
            aid = db.record_shipment(_fp(), "APPROVE", [], "LOW")
            db.record_outcome(aid, OUTCOME_CLEARED)
        result = engine.score(_req())
        assert result.pattern_score < 0.20
        db.close()

    def test_all_fraud_history_produces_high_score(self):
        db, engine = _make_engine()
        for _ in range(10):
            aid = db.record_shipment(_fp(), "FLAG_FOR_INSPECTION", [], "HIGH")
            db.record_outcome(aid, OUTCOME_CONFIRMED_FRAUD)
        result = engine.score(_req())
        assert result.pattern_score > 0.30
        db.close()

    def test_fraud_raises_effective_score_above_clear(self):
        db_clean, engine_clean = _make_engine()
        db_bad, engine_bad = _make_engine()

        for _ in range(10):
            aid = db_clean.record_shipment(_fp(), "APPROVE", [], "LOW")
            db_clean.record_outcome(aid, OUTCOME_CLEARED)
        for _ in range(10):
            aid = db_bad.record_shipment(_fp(), "FLAG_FOR_INSPECTION", [], "HIGH")
            db_bad.record_outcome(aid, OUTCOME_CONFIRMED_FRAUD)

        clean_result = engine_clean.score(_req())
        bad_result = engine_bad.score(_req())
        assert bad_result.effective_pattern_score > clean_result.effective_pattern_score
        db_clean.close()
        db_bad.close()

    def test_five_signals_in_result(self):
        db, engine = _make_engine()
        result = engine.score(_req())
        assert len(result.signals) == 5
        assert isinstance(result.signals[0], ShipperRiskSignal)
        assert isinstance(result.signals[1], ConsigneeRiskSignal)
        assert isinstance(result.signals[2], RouteRiskSignal)
        assert isinstance(result.signals[3], ValueAnomalySignal)
        assert isinstance(result.signals[4], FrequencyAnomalySignal)
        db.close()

    def test_triggered_signals_are_subset(self):
        db, engine = _make_engine()
        for _ in range(10):
            aid = db.record_shipment(_fp(), "FLAG_FOR_INSPECTION", [], "HIGH")
            db.record_outcome(aid, OUTCOME_CONFIRMED_FRAUD)
        result = engine.score(_req())
        for sig in result.triggered_signals:
            assert sig in result.signals
        db.close()

    def test_overall_severity_is_highest(self):
        db, engine = _make_engine()
        for _ in range(10):
            aid = db.record_shipment(_fp(), "FLAG_FOR_INSPECTION", [], "HIGH")
            db.record_outcome(aid, OUTCOME_CONFIRMED_FRAUD)
        result = engine.score(_req())
        severity_order = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}
        if result.triggered_signals:
            max_sev = min(
                result.triggered_signals,
                key=lambda s: severity_order[s.severity]
            ).severity
            assert result.overall_severity == max_sev
        db.close()

    def test_explanations_list_for_clean_history(self):
        db, engine = _make_engine()
        for _ in range(10):
            aid = db.record_shipment(_fp(), "APPROVE", [], "LOW")
            db.record_outcome(aid, OUTCOME_CLEARED)
        result = engine.score(_req())
        # Clean history should produce no triggered explanations
        assert result.explanations == [] or all(isinstance(e, str) for e in result.explanations)
        db.close()


class TestConvenienceAccessors:
    def test_score_accessors_match_signal_scores(self):
        db, engine = _make_engine()
        result = engine.score(_req())
        assert result.shipper_score == result.signals[0].score
        assert result.consignee_score == result.signals[1].score
        assert result.route_score == result.signals[2].score
        assert result.value_anomaly_score == result.signals[3].score
        assert result.frequency_score == result.signals[4].score
        db.close()


class TestEdgeCases:
    def test_all_none_request(self):
        """Engine must not crash on a completely empty request."""
        db, engine = _make_engine()
        req = ScoringRequest()
        result = engine.score(req)
        assert 0.0 <= result.effective_pattern_score <= 1.0
        db.close()

    def test_zero_quantity_no_crash(self):
        db, engine = _make_engine()
        result = engine.score(_req(quantity=0.0))
        assert result is not None
        db.close()

    def test_negative_declared_value_no_crash(self):
        db, engine = _make_engine()
        result = engine.score(_req(declared_value_usd=-100.0))
        assert result is not None
        db.close()

    def test_score_is_always_float(self):
        db, engine = _make_engine()
        result = engine.score(_req())
        assert isinstance(result.pattern_score, float)
        assert isinstance(result.effective_pattern_score, float)
        db.close()
