"""
Tests for portguard/pattern_engine.py (new pattern_store-based API).

Run with:
    python -m pytest portguard/tests/test_pattern_engine.py -v

All tests use in-memory PatternDB instances (SQLite :memory:).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from portguard.pattern_db import PatternDB
from portguard.pattern_engine import (
    apply_pattern_adjustments,
    get_pattern_stats,
    record_feedback,
    record_signals,
    reset_patterns,
    _normalize_key,
    _route_key,
    _value_bucket,
    _value_anomaly_key,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ORG = "test@example.com"


def _make_db() -> PatternDB:
    return PatternDB(":memory:")


def _analysis(
    shipper="Acme Corp",
    origin="CN",
    dest="US",
    decision="APPROVE",
    risk_score=0.3,
    value_usd=10_000.0,
) -> dict:
    return {
        "exporter": shipper,
        "origin_iso2": origin,
        "destination_iso2": dest,
        "final_decision": decision,
        "final_risk_score": risk_score,
        "declared_value_usd": value_usd,
    }


def _row_count(db: PatternDB, signal_type: str | None = None) -> int:
    """Return count of pattern_store rows, optionally filtered by signal_type."""
    with db._engine.connect() as conn:
        if signal_type:
            return conn.execute(
                text("SELECT COUNT(*) FROM pattern_store WHERE organization_email=:org AND signal_type=:st"),
                {"org": ORG, "st": signal_type},
            ).scalar()
        return conn.execute(
            text("SELECT COUNT(*) FROM pattern_store WHERE organization_email=:org"),
            {"org": ORG},
        ).scalar()


def _get_row(db: PatternDB, signal_type: str, signal_key: str) -> dict | None:
    """Fetch a single pattern_store row as a dict."""
    with db._engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT occurrence_count, flag_count, fraud_confirmed_count,
                       cleared_count, avg_risk_score, last_decision
                FROM pattern_store
                WHERE organization_email=:org AND signal_type=:st AND signal_key=:sk
            """),
            {"org": ORG, "st": signal_type, "sk": signal_key},
        ).fetchone()
    if row is None:
        return None
    return {
        "occurrence_count":      row[0],
        "flag_count":            row[1],
        "fraud_confirmed_count": row[2],
        "cleared_count":         row[3],
        "avg_risk_score":        row[4],
        "last_decision":         row[5],
    }


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------


class TestNormalizeKey:
    def test_lowercases(self):
        assert _normalize_key("Acme CORP") == "acme corp"

    def test_strips_unicode(self):
        result = _normalize_key("Café Exports")
        assert "caf" in result

    def test_empty_string(self):
        assert _normalize_key("") == ""

    def test_collapses_whitespace(self):
        assert _normalize_key("  Foo   Bar  ") == "foo bar"


class TestRouteKey:
    def test_basic(self):
        assert _route_key("CN", "US") == "CN→US"

    def test_lowercases_to_upper(self):
        assert _route_key("cn", "us") == "CN→US"

    def test_none_origin(self):
        assert _route_key(None, "US") == "??→US"

    def test_none_destination_defaults_us(self):
        assert _route_key("CN", None) == "CN→US"


class TestValueBucket:
    def test_low(self):
        assert _value_bucket(1_000.0) == "LOW"
        assert _value_bucket(4_999.99) == "LOW"

    def test_medium(self):
        assert _value_bucket(5_000.0) == "MEDIUM"
        assert _value_bucket(49_999.0) == "MEDIUM"

    def test_high(self):
        assert _value_bucket(50_000.0) == "HIGH"
        assert _value_bucket(499_999.0) == "HIGH"

    def test_very_high(self):
        assert _value_bucket(500_000.0) == "VERY_HIGH"
        assert _value_bucket(1_000_000.0) == "VERY_HIGH"

    def test_none_is_low(self):
        assert _value_bucket(None) == "LOW"

    def test_negative_is_low(self):
        assert _value_bucket(-500.0) == "LOW"


class TestValueAnomalyKey:
    def test_format(self):
        assert _value_anomaly_key("HIGH", "CN") == "HIGH:CN"

    def test_none_origin(self):
        assert _value_anomaly_key("LOW", None) == "LOW:??"


# ---------------------------------------------------------------------------
# record_signals
# ---------------------------------------------------------------------------


class TestRecordSignals:
    def test_creates_shipper_rep_row(self):
        db = _make_db()
        record_signals(db, ORG, _analysis())
        assert _row_count(db, "SHIPPER_REP") == 1
        db.close()

    def test_creates_route_risk_row(self):
        db = _make_db()
        record_signals(db, ORG, _analysis())
        assert _row_count(db, "ROUTE_RISK") == 1
        db.close()

    def test_creates_value_anomaly_row(self):
        db = _make_db()
        record_signals(db, ORG, _analysis())
        assert _row_count(db, "VALUE_ANOMALY") == 1
        db.close()

    def test_creates_three_rows_per_analysis(self):
        db = _make_db()
        record_signals(db, ORG, _analysis())
        assert _row_count(db) == 3
        db.close()

    def test_increments_occurrence_count_on_repeat(self):
        db = _make_db()
        for _ in range(5):
            record_signals(db, ORG, _analysis())
        row = _get_row(db, "SHIPPER_REP", _normalize_key("Acme Corp"))
        assert row["occurrence_count"] == 5
        db.close()

    def test_flag_count_zero_for_approve(self):
        db = _make_db()
        record_signals(db, ORG, _analysis(decision="APPROVE"))
        row = _get_row(db, "SHIPPER_REP", _normalize_key("Acme Corp"))
        assert row["flag_count"] == 0
        db.close()

    def test_flag_count_incremented_for_flag_for_inspection(self):
        db = _make_db()
        record_signals(db, ORG, _analysis(decision="FLAG_FOR_INSPECTION"))
        row = _get_row(db, "SHIPPER_REP", _normalize_key("Acme Corp"))
        assert row["flag_count"] == 1
        db.close()

    def test_flag_count_incremented_for_request_more_info(self):
        db = _make_db()
        record_signals(db, ORG, _analysis(decision="REQUEST_MORE_INFO"))
        row = _get_row(db, "SHIPPER_REP", _normalize_key("Acme Corp"))
        assert row["flag_count"] == 1
        db.close()

    def test_flag_count_accumulates(self):
        db = _make_db()
        record_signals(db, ORG, _analysis(decision="FLAG_FOR_INSPECTION"))
        record_signals(db, ORG, _analysis(decision="APPROVE"))
        record_signals(db, ORG, _analysis(decision="FLAG_FOR_INSPECTION"))
        row = _get_row(db, "SHIPPER_REP", _normalize_key("Acme Corp"))
        assert row["occurrence_count"] == 3
        assert row["flag_count"] == 2
        db.close()

    def test_avg_risk_score_is_rolling_average(self):
        db = _make_db()
        record_signals(db, ORG, _analysis(risk_score=0.2))
        record_signals(db, ORG, _analysis(risk_score=0.4))
        row = _get_row(db, "SHIPPER_REP", _normalize_key("Acme Corp"))
        assert abs(row["avg_risk_score"] - 0.3) < 0.01
        db.close()

    def test_last_decision_updated(self):
        db = _make_db()
        record_signals(db, ORG, _analysis(decision="APPROVE"))
        record_signals(db, ORG, _analysis(decision="FLAG_FOR_INSPECTION"))
        row = _get_row(db, "SHIPPER_REP", _normalize_key("Acme Corp"))
        assert row["last_decision"] == "FLAG_FOR_INSPECTION"
        db.close()

    def test_no_shipper_skips_shipper_rep(self):
        db = _make_db()
        record_signals(db, ORG, _analysis(shipper=None))
        assert _row_count(db, "SHIPPER_REP") == 0
        db.close()

    def test_no_origin_skips_route_and_value(self):
        db = _make_db()
        record_signals(db, ORG, _analysis(origin=None))
        assert _row_count(db, "ROUTE_RISK") == 0
        assert _row_count(db, "VALUE_ANOMALY") == 0
        db.close()

    def test_org_isolation(self):
        db = _make_db()
        record_signals(db, "org1@example.com", _analysis())
        record_signals(db, "org2@example.com", _analysis())
        with db._engine.connect() as conn:
            c1 = conn.execute(
                text("SELECT COUNT(*) FROM pattern_store WHERE organization_email='org1@example.com'")
            ).scalar()
            c2 = conn.execute(
                text("SELECT COUNT(*) FROM pattern_store WHERE organization_email='org2@example.com'")
            ).scalar()
        assert c1 == 3
        assert c2 == 3
        db.close()

    def test_does_not_raise_on_bad_input(self):
        db = _make_db()
        record_signals(db, ORG, None)  # should not raise
        record_signals(db, ORG, {})
        db.close()

    def test_value_anomaly_key_uses_correct_bucket(self):
        db = _make_db()
        record_signals(db, ORG, _analysis(value_usd=100_000.0, origin="CN"))
        assert _get_row(db, "VALUE_ANOMALY", "HIGH:CN") is not None
        db.close()


# ---------------------------------------------------------------------------
# apply_pattern_adjustments
# ---------------------------------------------------------------------------


class TestApplyPatternAdjustments:
    def test_unknown_shipper_returns_zero_adjustment(self):
        db = _make_db()
        adj = apply_pattern_adjustments(db, ORG, _analysis())
        assert adj["total_adjustment"] == 0.0
        assert not adj["hard_flag"]
        assert adj["pattern_warnings"] == []
        db.close()

    def test_low_flag_rate_no_adjustment(self):
        db = _make_db()
        # 2/10 = 20% flag rate, below 30% threshold
        for i in range(10):
            record_signals(db, ORG, _analysis(decision="FLAG_FOR_INSPECTION" if i < 2 else "APPROVE"))
        adj = apply_pattern_adjustments(db, ORG, _analysis())
        assert adj["shipper_adjustment"] == 0.0
        db.close()

    def test_moderate_flag_rate_gives_1_5_adjustment(self):
        db = _make_db()
        # 4/10 = 40% flag rate → +1.5
        for i in range(10):
            record_signals(db, ORG, _analysis(decision="FLAG_FOR_INSPECTION" if i < 4 else "APPROVE"))
        adj = apply_pattern_adjustments(db, ORG, _analysis())
        assert adj["shipper_adjustment"] == 1.5
        db.close()

    def test_high_flag_rate_gives_2_5_adjustment(self):
        db = _make_db()
        # 7/10 = 70% flag rate → +2.5
        for i in range(10):
            record_signals(db, ORG, _analysis(decision="FLAG_FOR_INSPECTION" if i < 7 else "APPROVE"))
        adj = apply_pattern_adjustments(db, ORG, _analysis())
        assert adj["shipper_adjustment"] == 2.5
        db.close()

    def test_cleared_history_reduces_adjustment(self):
        db = _make_db()
        # Build moderate risk
        for i in range(10):
            record_signals(db, ORG, _analysis(decision="FLAG_FOR_INSPECTION" if i < 4 else "APPROVE"))
        # Apply cleared feedback
        adj_before = apply_pattern_adjustments(db, ORG, _analysis())
        # manually set cleared_count > 2 in DB
        with db._engine.begin() as conn:
            conn.execute(
                text("UPDATE pattern_store SET cleared_count=3 WHERE signal_type='SHIPPER_REP' AND organization_email=:org"),
                {"org": ORG},
            )
        adj_after = apply_pattern_adjustments(db, ORG, _analysis())
        assert adj_after["shipper_adjustment"] < adj_before["shipper_adjustment"]
        db.close()

    def test_hard_flag_when_fraud_confirmed(self):
        db = _make_db()
        record_signals(db, ORG, _analysis(decision="FLAG_FOR_INSPECTION"))
        with db._engine.begin() as conn:
            conn.execute(
                text("UPDATE pattern_store SET fraud_confirmed_count=1 WHERE signal_type='SHIPPER_REP' AND organization_email=:org"),
                {"org": ORG},
            )
        adj = apply_pattern_adjustments(db, ORG, _analysis())
        assert adj["hard_flag"] is True
        assert adj["hard_flag_reason"] is not None
        assert "Acme Corp" in adj["hard_flag_reason"]
        assert any("⚠" in w for w in adj["pattern_warnings"])
        db.close()

    def test_route_adjustment_requires_min_3_occurrences(self):
        db = _make_db()
        # Only 2 analyses on this route
        record_signals(db, ORG, _analysis(decision="FLAG_FOR_INSPECTION"))
        record_signals(db, ORG, _analysis(decision="FLAG_FOR_INSPECTION"))
        adj = apply_pattern_adjustments(db, ORG, _analysis())
        assert adj["route_adjustment"] == 0.0
        db.close()

    def test_route_high_flag_rate_gives_2_0_adjustment(self):
        db = _make_db()
        # 4/5 = 80% flag rate on CN→US with 5 occurrences → +2.0
        for i in range(5):
            record_signals(db, ORG, _analysis(decision="FLAG_FOR_INSPECTION" if i < 4 else "APPROVE"))
        adj = apply_pattern_adjustments(db, ORG, _analysis())
        assert adj["route_adjustment"] == 2.0
        db.close()

    def test_value_anomaly_requires_min_4_occurrences(self):
        db = _make_db()
        for _ in range(3):
            record_signals(db, ORG, _analysis(decision="FLAG_FOR_INSPECTION", value_usd=100_000.0))
        adj = apply_pattern_adjustments(db, ORG, _analysis(value_usd=100_000.0))
        assert adj["value_adjustment"] == 0.0
        db.close()

    def test_value_anomaly_gives_1_5_adjustment(self):
        db = _make_db()
        # 3/4 = 75% flag rate on HIGH:CN with 4 occurrences → +1.5
        for i in range(4):
            record_signals(db, ORG, _analysis(
                decision="FLAG_FOR_INSPECTION" if i < 3 else "APPROVE",
                value_usd=100_000.0,
            ))
        adj = apply_pattern_adjustments(db, ORG, _analysis(value_usd=100_000.0))
        assert adj["value_adjustment"] == 1.5
        db.close()

    def test_total_adjustment_is_sum_clamped_at_zero(self):
        db = _make_db()
        adj = apply_pattern_adjustments(db, ORG, _analysis())
        assert adj["total_adjustment"] >= 0.0
        db.close()

    def test_total_adjustment_sums_all_signals(self):
        db = _make_db()
        # Build high shipper risk (70%)
        for i in range(10):
            record_signals(db, ORG, _analysis(decision="FLAG_FOR_INSPECTION" if i < 7 else "APPROVE", value_usd=100_000.0))
        # value anomaly: force 4 occurrences with 75% flag rate already done above
        adj = apply_pattern_adjustments(db, ORG, _analysis(value_usd=100_000.0))
        assert adj["total_adjustment"] == adj["shipper_adjustment"] + adj["route_adjustment"] + adj["value_adjustment"]
        db.close()

    def test_org_isolation(self):
        db = _make_db()
        # Only other org has risk data
        for i in range(10):
            record_signals(db, "other@example.com", _analysis(decision="FLAG_FOR_INSPECTION" if i < 7 else "APPROVE"))
        adj = apply_pattern_adjustments(db, ORG, _analysis())
        assert adj["total_adjustment"] == 0.0
        db.close()

    def test_does_not_raise_on_bad_input(self):
        db = _make_db()
        adj = apply_pattern_adjustments(db, ORG, None)
        assert isinstance(adj, dict)
        assert "total_adjustment" in adj
        db.close()

    def test_pattern_warnings_are_strings(self):
        db = _make_db()
        for i in range(10):
            record_signals(db, ORG, _analysis(decision="FLAG_FOR_INSPECTION" if i < 7 else "APPROVE"))
        adj = apply_pattern_adjustments(db, ORG, _analysis())
        for w in adj["pattern_warnings"]:
            assert isinstance(w, str)
        db.close()


# ---------------------------------------------------------------------------
# record_feedback
# ---------------------------------------------------------------------------


class TestRecordFeedback:
    def _write_shipment_row(self, db: PatternDB, analysis_id: str, shipper: str, origin: str) -> None:
        """Insert a minimal shipment_history row for testing record_feedback."""
        from portguard.pattern_db import ShipmentFingerprint
        fp = ShipmentFingerprint(
            shipper_name=shipper,
            origin_iso2=origin,
            destination_iso2="US",
            rule_risk_score=0.3,
            rule_decision="APPROVE",
            rule_confidence="LOW",
            final_risk_score=0.3,
            final_decision="APPROVE",
            final_confidence="LOW",
        )
        with db._engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO shipment_history
                        (analysis_id, analyzed_at, shipper_name, origin_iso2, destination_iso2,
                         rule_risk_score, rule_decision, rule_confidence,
                         final_risk_score, final_decision, final_confidence,
                         pattern_cold_start)
                    VALUES
                        (:aid, '2026-01-01T00:00:00+00:00', :sn, :orig, 'US',
                         0.3, 'APPROVE', 'LOW', 0.3, 'APPROVE', 'LOW', 1)
                """),
                {"aid": analysis_id, "sn": shipper, "orig": origin},
            )

    def test_increments_fraud_confirmed_count(self):
        db = _make_db()
        record_signals(db, ORG, _analysis())
        self._write_shipment_row(db, "test-001", "Acme Corp", "CN")
        record_feedback(db, ORG, "test-001", "CONFIRMED_FRAUD")
        row = _get_row(db, "SHIPPER_REP", _normalize_key("Acme Corp"))
        assert row["fraud_confirmed_count"] == 1
        db.close()

    def test_increments_cleared_count(self):
        db = _make_db()
        record_signals(db, ORG, _analysis())
        self._write_shipment_row(db, "test-002", "Acme Corp", "CN")
        record_feedback(db, ORG, "test-002", "CLEARED")
        row = _get_row(db, "SHIPPER_REP", _normalize_key("Acme Corp"))
        assert row["cleared_count"] == 1
        db.close()

    def test_also_updates_route_risk_row(self):
        db = _make_db()
        record_signals(db, ORG, _analysis())
        self._write_shipment_row(db, "test-003", "Acme Corp", "CN")
        record_feedback(db, ORG, "test-003", "CONFIRMED_FRAUD")
        route_row = _get_row(db, "ROUTE_RISK", "CN→US")
        assert route_row["fraud_confirmed_count"] == 1
        db.close()

    def test_unknown_shipment_id_does_not_raise(self):
        db = _make_db()
        record_feedback(db, ORG, "no-such-id", "CONFIRMED_FRAUD")  # should not raise
        db.close()

    def test_invalid_feedback_type_does_not_raise(self):
        db = _make_db()
        record_feedback(db, ORG, "test-004", "BAD_TYPE")  # should not raise
        db.close()

    def test_no_pattern_row_no_crash(self):
        # Shipment exists in history but no pattern_store row — UPDATE is a no-op
        db = _make_db()
        self._write_shipment_row(db, "test-005", "New Corp", "DE")
        record_feedback(db, ORG, "test-005", "CONFIRMED_FRAUD")  # no-op, not a crash
        db.close()

    def test_multiple_feedbacks_accumulate(self):
        db = _make_db()
        record_signals(db, ORG, _analysis())
        self._write_shipment_row(db, "test-006", "Acme Corp", "CN")
        self._write_shipment_row(db, "test-007", "Acme Corp", "CN")
        record_feedback(db, ORG, "test-006", "CONFIRMED_FRAUD")
        record_feedback(db, ORG, "test-007", "CONFIRMED_FRAUD")
        row = _get_row(db, "SHIPPER_REP", _normalize_key("Acme Corp"))
        assert row["fraud_confirmed_count"] == 2
        db.close()


# ---------------------------------------------------------------------------
# get_pattern_stats
# ---------------------------------------------------------------------------


class TestGetPatternStats:
    def test_empty_db_returns_defaults(self):
        db = _make_db()
        stats = get_pattern_stats(db, ORG)
        assert stats["total_shipments_screened"] == 0
        assert stats["unique_shippers_tracked"] == 0
        assert stats["has_history"] is False
        db.close()

    def test_has_history_after_recording(self):
        db = _make_db()
        record_signals(db, ORG, _analysis())
        stats = get_pattern_stats(db, ORG)
        assert stats["has_history"] is True
        db.close()

    def test_counts_unique_shippers(self):
        db = _make_db()
        record_signals(db, ORG, _analysis(shipper="Shipper A"))
        record_signals(db, ORG, _analysis(shipper="Shipper B"))
        record_signals(db, ORG, _analysis(shipper="Shipper A"))
        stats = get_pattern_stats(db, ORG)
        assert stats["unique_shippers_tracked"] == 2
        db.close()

    def test_total_shipments_screened_sums_occurrences(self):
        db = _make_db()
        for _ in range(5):
            record_signals(db, ORG, _analysis(shipper="Alpha Corp"))
        for _ in range(3):
            record_signals(db, ORG, _analysis(shipper="Beta Corp"))
        stats = get_pattern_stats(db, ORG)
        assert stats["total_shipments_screened"] == 8
        db.close()

    def test_counts_unique_routes(self):
        db = _make_db()
        record_signals(db, ORG, _analysis(origin="CN"))
        record_signals(db, ORG, _analysis(origin="VN"))
        stats = get_pattern_stats(db, ORG)
        assert stats["unique_routes_tracked"] == 2
        db.close()

    def test_confirmed_fraud_count(self):
        db = _make_db()
        record_signals(db, ORG, _analysis())
        with db._engine.begin() as conn:
            conn.execute(
                text("UPDATE pattern_store SET fraud_confirmed_count=3 WHERE signal_type='SHIPPER_REP' AND organization_email=:org"),
                {"org": ORG},
            )
        stats = get_pattern_stats(db, ORG)
        assert stats["confirmed_fraud_count"] == 3
        db.close()

    def test_high_risk_shippers_counted(self):
        db = _make_db()
        # 7/10 = 70% flag rate → high risk
        for i in range(10):
            record_signals(db, ORG, _analysis(decision="FLAG_FOR_INSPECTION" if i < 7 else "APPROVE"))
        stats = get_pattern_stats(db, ORG)
        assert stats["high_risk_shippers"] >= 1
        db.close()

    def test_cleared_shippers_counted(self):
        db = _make_db()
        record_signals(db, ORG, _analysis())
        with db._engine.begin() as conn:
            conn.execute(
                text("UPDATE pattern_store SET cleared_count=1 WHERE signal_type='SHIPPER_REP' AND organization_email=:org"),
                {"org": ORG},
            )
        stats = get_pattern_stats(db, ORG)
        assert stats["cleared_shippers"] == 1
        db.close()

    def test_org_isolation(self):
        db = _make_db()
        for _ in range(5):
            record_signals(db, "other@example.com", _analysis())
        stats = get_pattern_stats(db, ORG)
        assert stats["has_history"] is False
        assert stats["total_shipments_screened"] == 0
        db.close()

    def test_returns_dict_keys(self):
        db = _make_db()
        stats = get_pattern_stats(db, ORG)
        expected_keys = {
            "total_shipments_screened", "unique_shippers_tracked",
            "unique_routes_tracked", "confirmed_fraud_count",
            "high_risk_shippers", "high_risk_routes",
            "cleared_shippers", "has_history",
        }
        assert expected_keys.issubset(stats.keys())
        db.close()


# ---------------------------------------------------------------------------
# reset_patterns
# ---------------------------------------------------------------------------


class TestResetPatterns:
    def test_deletes_all_org_rows(self):
        db = _make_db()
        for _ in range(3):
            record_signals(db, ORG, _analysis())
        assert _row_count(db) > 0
        reset_patterns(db, ORG)
        assert _row_count(db) == 0
        db.close()

    def test_returns_deleted_count(self):
        db = _make_db()
        record_signals(db, ORG, _analysis())  # creates 3 rows
        count = reset_patterns(db, ORG)
        assert count == 3
        db.close()

    def test_does_not_delete_other_org_rows(self):
        db = _make_db()
        record_signals(db, ORG, _analysis())
        record_signals(db, "other@example.com", _analysis())
        reset_patterns(db, ORG)
        with db._engine.connect() as conn:
            remaining = conn.execute(
                text("SELECT COUNT(*) FROM pattern_store WHERE organization_email='other@example.com'")
            ).scalar()
        assert remaining == 3
        db.close()

    def test_empty_db_returns_zero(self):
        db = _make_db()
        count = reset_patterns(db, ORG)
        assert count == 0
        db.close()

    def test_idempotent(self):
        db = _make_db()
        record_signals(db, ORG, _analysis())
        reset_patterns(db, ORG)
        count = reset_patterns(db, ORG)
        assert count == 0
        db.close()
