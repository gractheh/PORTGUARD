"""
Tests for portguard/pattern_db.py

Run with:
    python -m pytest portguard/tests/test_pattern_db.py -v

All tests use an in-memory SQLite database so no files are created or left
behind.  Each test function creates a fresh PatternDB instance.
"""

from __future__ import annotations

import math
import time
import pytest

from portguard.pattern_db import (
    AUTO_TRUST_CLEARED_THRESHOLD,
    OUTCOME_CLEARED,
    OUTCOME_CONFIRMED_FRAUD,
    OUTCOME_UNRESOLVED,
    ConsigneeProfile,
    DuplicateOutcomeError,
    HSBaseline,
    InvalidOutcomeError,
    PatternDB,
    PatternDBError,
    RecordNotFoundError,
    RouteRisk,
    ShipmentFingerprint,
    ShipperProfile,
    _compute_route_fraud_rate,
    _compute_shipper_reputation,
    _decay_weight,
    _entity_key,
    _normalize_entity_name,
    _welford_stddev,
    _welford_update,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db() -> PatternDB:
    """Return a fresh in-memory PatternDB."""
    return PatternDB(":memory:")


def _simple_fingerprint(**overrides) -> ShipmentFingerprint:
    """Return a minimal valid ShipmentFingerprint for use in tests."""
    fp = ShipmentFingerprint(
        shipper_name="Acme Corp",
        consignee_name="Widget Imports LLC",
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
    for k, v in overrides.items():
        setattr(fp, k, v)
    return fp


# ---------------------------------------------------------------------------
# Unit tests — pure functions
# ---------------------------------------------------------------------------


class TestNormalization:
    def test_strips_legal_suffixes(self):
        assert _normalize_entity_name("Acme Corp.") == "acme"

    def test_strips_ltd(self):
        assert _normalize_entity_name("Viet Star Electronics Manufacturing Co., Ltd.") == \
               "viet star electronics manufacturing"

    def test_lowercase(self):
        assert _normalize_entity_name("ACME INC") == "acme"

    def test_unicode_normalized(self):
        # Accented chars should become ASCII equivalents
        result = _normalize_entity_name("Société Générale SA")
        assert "societe" in result or "soci" in result  # accent stripped

    def test_hyphen_preserved(self):
        result = _normalize_entity_name("Smith-Jones Ltd")
        assert "smith-jones" in result

    def test_same_entity_same_key(self):
        k1 = _entity_key("Acme Corp.")
        k2 = _entity_key("ACME CORP")
        assert k1 == k2

    def test_different_entities_different_keys(self):
        assert _entity_key("Acme Corp") != _entity_key("Widget Inc")

    def test_key_is_64_char_hex(self):
        key = _entity_key("Test Company Ltd")
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)


class TestDecayWeight:
    def test_today_is_one(self):
        assert abs(_decay_weight(0.0) - 1.0) < 1e-9

    def test_thirty_days_is_half(self):
        # λ=0.023, half-life=30d
        assert abs(_decay_weight(30.0) - 0.5) < 0.01

    def test_ninety_days_is_eighth(self):
        assert abs(_decay_weight(90.0) - 0.125) < 0.02

    def test_always_positive(self):
        assert _decay_weight(1000.0) > 0.0


class TestBayesianScores:
    def test_innocent_prior_shipper(self):
        # α=1, β=5 → 1/6
        score = _compute_shipper_reputation(0.0, 0.0)
        assert abs(score - 1.0 / 6.0) < 1e-6

    def test_many_clears_low_score(self):
        score = _compute_shipper_reputation(0.0, 100.0)
        assert score < 0.02

    def test_many_frauds_high_score(self):
        score = _compute_shipper_reputation(20.0, 0.0)
        assert score > 0.7

    def test_route_neutral_prior(self):
        # Jeffrey's prior: 0.5/0.5 → 0.50
        rate = _compute_route_fraud_rate(0.0, 0.0)
        assert abs(rate - 0.5) < 1e-6

    def test_route_clean_corridor(self):
        rate = _compute_route_fraud_rate(0.0, 100.0)
        assert rate < 0.01

    def test_route_risky_corridor(self):
        rate = _compute_route_fraud_rate(30.0, 50.0)
        assert rate > 0.5


class TestWelford:
    def test_single_sample_no_stddev(self):
        n, mean, m2 = _welford_update(0, 0.0, 0.0, 50.0)
        assert n == 1
        assert abs(mean - 50.0) < 1e-9
        assert _welford_stddev(n, m2) is None

    def test_two_samples_correct_mean(self):
        n, mean, m2 = _welford_update(0, 0.0, 0.0, 10.0)
        n, mean, m2 = _welford_update(n, mean, m2, 20.0)
        assert n == 2
        assert abs(mean - 15.0) < 1e-9
        stddev = _welford_stddev(n, m2)
        assert stddev is not None
        assert abs(stddev - math.sqrt(50.0)) < 1e-6  # sample variance = 50

    def test_known_distribution(self):
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        n, mean, m2 = 0, 0.0, 0.0
        for v in values:
            n, mean, m2 = _welford_update(n, mean, m2, v)
        assert abs(mean - 30.0) < 1e-9
        stddev = _welford_stddev(n, m2)
        # Sample stddev of [10,20,30,40,50] = sqrt(250) ≈ 15.81
        assert abs(stddev - math.sqrt(250.0)) < 1e-6


# ---------------------------------------------------------------------------
# Integration tests — PatternDB
# ---------------------------------------------------------------------------


class TestSchemaAndMigrations:
    def test_tables_created(self):
        db = _make_db()
        tables = {
            row[0]
            for row in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        expected = {
            "schema_migrations", "shipment_history", "pattern_outcomes",
            "shipper_profiles", "consignee_profiles",
            "route_risk_profiles", "hs_code_baselines",
        }
        assert expected.issubset(tables)
        db.close()

    def test_migration_recorded(self):
        db = _make_db()
        migrations = [
            row[0]
            for row in db._conn.execute("SELECT migration_name FROM schema_migrations")
        ]
        assert "001_initial_schema" in migrations
        db.close()

    def test_idempotent_migration(self):
        """Opening a second PatternDB on the same DB does not re-apply migrations."""
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            db1 = PatternDB(path)
            db1.close()
            db2 = PatternDB(path)
            count = db2._conn.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0]
            assert count == 1  # migration applied once
            db2.close()
        finally:
            os.unlink(path)


class TestRecordShipment:
    def test_returns_uuid(self):
        db = _make_db()
        fp = _simple_fingerprint()
        aid = db.record_shipment(fp, "APPROVE", [], "LOW")
        assert len(aid) == 36  # UUID v4 format
        db.close()

    def test_row_written_to_db(self):
        db = _make_db()
        fp = _simple_fingerprint()
        aid = db.record_shipment(fp, "APPROVE", [], "LOW")
        row = db._conn.execute(
            "SELECT * FROM shipment_history WHERE analysis_id=?", (aid,)
        ).fetchone()
        assert row is not None
        assert row["shipper_name"] == "Acme Corp"
        assert row["origin_iso2"] == "CN"
        assert row["final_decision"] == "APPROVE"
        db.close()

    def test_unit_value_computed(self):
        db = _make_db()
        fp = _simple_fingerprint(declared_value_usd=1000.0, quantity=10.0)
        aid = db.record_shipment(fp, "APPROVE", [], "LOW")
        row = db._conn.execute(
            "SELECT unit_value_usd FROM shipment_history WHERE analysis_id=?", (aid,)
        ).fetchone()
        assert abs(row["unit_value_usd"] - 100.0) < 1e-6
        db.close()

    def test_route_key_set(self):
        db = _make_db()
        fp = _simple_fingerprint()
        aid = db.record_shipment(fp, "APPROVE", [], "LOW")
        row = db._conn.execute(
            "SELECT route_key FROM shipment_history WHERE analysis_id=?", (aid,)
        ).fetchone()
        assert row["route_key"] == "CN|Los Angeles"
        db.close()

    def test_hs_chapter_primary(self):
        db = _make_db()
        fp = _simple_fingerprint(hs_codes=["8471.30.0100"])
        aid = db.record_shipment(fp, "APPROVE", [], "LOW")
        row = db._conn.execute(
            "SELECT hs_chapter_primary FROM shipment_history WHERE analysis_id=?", (aid,)
        ).fetchone()
        assert row["hs_chapter_primary"] == "84"
        db.close()

    def test_shipper_profile_created(self):
        db = _make_db()
        fp = _simple_fingerprint()
        db.record_shipment(fp, "APPROVE", [], "LOW")
        profile = db.get_shipper_profile("Acme Corp")
        assert profile.exists
        assert profile.history_count == 1
        db.close()

    def test_consignee_profile_created(self):
        db = _make_db()
        fp = _simple_fingerprint()
        db.record_shipment(fp, "APPROVE", [], "LOW")
        profile = db.get_consignee_profile("Widget Imports LLC")
        assert profile.exists
        assert profile.history_count == 1
        db.close()

    def test_route_profile_created(self):
        db = _make_db()
        fp = _simple_fingerprint()
        db.record_shipment(fp, "APPROVE", [], "LOW")
        route = db.get_route_risk("CN", "Los Angeles")
        assert route.exists
        assert route.total_analyses == 1
        db.close()

    def test_hs_baseline_created(self):
        db = _make_db()
        fp = _simple_fingerprint(declared_value_usd=10000.0, quantity=100.0)
        db.record_shipment(fp, "APPROVE", [], "LOW")
        baseline = db.get_hs_baseline("8471.30")
        assert baseline.exists
        assert baseline.sample_count == 1
        assert abs(baseline.mean_unit_value - 100.0) < 1e-6
        db.close()

    def test_multiple_analyses_accumulate(self):
        db = _make_db()
        fp = _simple_fingerprint()
        db.record_shipment(fp, "APPROVE", [], "LOW")
        db.record_shipment(fp, "APPROVE", [], "LOW")
        db.record_shipment(fp, "APPROVE", [], "LOW")
        profile = db.get_shipper_profile("Acme Corp")
        assert profile.history_count == 3
        db.close()

    def test_flagged_shipment_updates_flagged_count(self):
        db = _make_db()
        fp = _simple_fingerprint()
        db.record_shipment(fp, "FLAG_FOR_INSPECTION", [], "HIGH")
        row = db._conn.execute(
            "SELECT total_flagged FROM shipper_profiles WHERE shipper_key=?",
            (_entity_key("Acme Corp"),),
        ).fetchone()
        assert row["total_flagged"] == 1
        db.close()

    def test_none_shipper_no_profile(self):
        db = _make_db()
        fp = _simple_fingerprint(shipper_name=None)
        db.record_shipment(fp, "APPROVE", [], "LOW")
        # No profile should be created for a None shipper
        row = db._conn.execute(
            "SELECT COUNT(*) FROM shipper_profiles"
        ).fetchone()[0]
        assert row == 0
        db.close()

    def test_hs_baseline_two_values_has_stddev(self):
        db = _make_db()
        fp1 = _simple_fingerprint(declared_value_usd=1000.0, quantity=10.0)  # unit = 100
        fp2 = _simple_fingerprint(declared_value_usd=2000.0, quantity=10.0)  # unit = 200
        db.record_shipment(fp1, "APPROVE", [], "LOW")
        db.record_shipment(fp2, "APPROVE", [], "LOW")
        baseline = db.get_hs_baseline("8471.30")
        assert baseline.sample_count == 2
        assert abs(baseline.mean_unit_value - 150.0) < 1e-6
        assert baseline.std_dev is not None
        db.close()


class TestGetProfileMisses:
    def test_unknown_shipper_returns_prior(self):
        db = _make_db()
        profile = db.get_shipper_profile("Ghost Company Ltd")
        assert not profile.exists
        assert profile.history_count == 0
        # Prior: α=1, β=5 → 1/6
        assert abs(profile.reputation_score - 1.0 / 6.0) < 1e-4
        db.close()

    def test_unknown_consignee_returns_prior(self):
        db = _make_db()
        profile = db.get_consignee_profile("Unknown Consignee")
        assert not profile.exists
        assert abs(profile.reputation_score - 1.0 / 6.0) < 1e-4
        db.close()

    def test_unknown_route_returns_neutral(self):
        db = _make_db()
        route = db.get_route_risk("ZZ", "Nowhere")
        assert not route.exists
        assert abs(route.fraud_rate - 0.5) < 1e-6
        db.close()

    def test_unknown_hs_returns_empty(self):
        db = _make_db()
        baseline = db.get_hs_baseline("9999.99")
        assert not baseline.exists
        assert baseline.sample_count == 0
        assert baseline.std_dev is None
        db.close()


class TestRecordOutcome:
    def test_confirmed_fraud_increases_shipper_reputation(self):
        db = _make_db()
        fp = _simple_fingerprint()
        aid = db.record_shipment(fp, "FLAG_FOR_INSPECTION", [], "HIGH")
        initial = db.get_shipper_profile("Acme Corp").reputation_score
        db.record_outcome(aid, OUTCOME_CONFIRMED_FRAUD)
        after = db.get_shipper_profile("Acme Corp").reputation_score
        assert after > initial
        db.close()

    def test_cleared_decreases_shipper_reputation(self):
        db = _make_db()
        fp = _simple_fingerprint()
        aid = db.record_shipment(fp, "FLAG_FOR_INSPECTION", [], "HIGH")
        initial = db.get_shipper_profile("Acme Corp").reputation_score
        db.record_outcome(aid, OUTCOME_CLEARED)
        after = db.get_shipper_profile("Acme Corp").reputation_score
        assert after < initial
        db.close()

    def test_confirmed_fraud_increases_route_fraud_rate(self):
        db = _make_db()
        fp = _simple_fingerprint()
        aid = db.record_shipment(fp, "FLAG_FOR_INSPECTION", [], "HIGH")
        initial = db.get_route_risk("CN", "Los Angeles").fraud_rate
        db.record_outcome(aid, OUTCOME_CONFIRMED_FRAUD)
        after = db.get_route_risk("CN", "Los Angeles").fraud_rate
        assert after > initial
        db.close()

    def test_cleared_does_not_change_route_fraud_rate(self):
        db = _make_db()
        fp = _simple_fingerprint()
        aid = db.record_shipment(fp, "FLAG_FOR_INSPECTION", [], "HIGH")
        initial = db.get_route_risk("CN", "Los Angeles").fraud_rate
        db.record_outcome(aid, OUTCOME_CLEARED)
        after = db.get_route_risk("CN", "Los Angeles").fraud_rate
        # Route fraud rate is unchanged for CLEARED outcomes (§4.3)
        assert abs(after - initial) < 1e-6
        db.close()

    def test_outcome_row_written(self):
        db = _make_db()
        fp = _simple_fingerprint()
        aid = db.record_shipment(fp, "FLAG_FOR_INSPECTION", [], "HIGH")
        db.record_outcome(aid, OUTCOME_CONFIRMED_FRAUD, officer_id="officer_1", notes="Seizure 123")
        row = db._conn.execute(
            "SELECT * FROM pattern_outcomes WHERE analysis_id=?", (aid,)
        ).fetchone()
        assert row is not None
        assert row["outcome"] == OUTCOME_CONFIRMED_FRAUD
        assert row["officer_id"] == "officer_1"
        assert row["outcome_notes"] == "Seizure 123"
        db.close()

    def test_unresolved_can_be_updated(self):
        db = _make_db()
        fp = _simple_fingerprint()
        aid = db.record_shipment(fp, "FLAG_FOR_INSPECTION", [], "HIGH")
        db.record_outcome(aid, OUTCOME_UNRESOLVED)
        # Update to resolved — should succeed
        db.record_outcome(aid, OUTCOME_CONFIRMED_FRAUD)
        row = db._conn.execute(
            "SELECT outcome FROM pattern_outcomes WHERE analysis_id=?", (aid,)
        ).fetchone()
        assert row["outcome"] == OUTCOME_CONFIRMED_FRAUD
        db.close()

    def test_resolved_outcome_is_immutable(self):
        db = _make_db()
        fp = _simple_fingerprint()
        aid = db.record_shipment(fp, "FLAG_FOR_INSPECTION", [], "HIGH")
        db.record_outcome(aid, OUTCOME_CONFIRMED_FRAUD)
        with pytest.raises(DuplicateOutcomeError):
            db.record_outcome(aid, OUTCOME_CLEARED)
        db.close()

    def test_cleared_outcome_is_immutable(self):
        db = _make_db()
        fp = _simple_fingerprint()
        aid = db.record_shipment(fp, "APPROVE", [], "LOW")
        db.record_outcome(aid, OUTCOME_CLEARED)
        with pytest.raises(DuplicateOutcomeError):
            db.record_outcome(aid, OUTCOME_CONFIRMED_FRAUD)
        db.close()

    def test_invalid_outcome_raises(self):
        db = _make_db()
        fp = _simple_fingerprint()
        aid = db.record_shipment(fp, "APPROVE", [], "LOW")
        with pytest.raises(InvalidOutcomeError):
            db.record_outcome(aid, "MAYBE")
        db.close()

    def test_missing_analysis_id_raises(self):
        db = _make_db()
        with pytest.raises(RecordNotFoundError):
            db.record_outcome("00000000-0000-0000-0000-000000000000", OUTCOME_CLEARED)
        db.close()

    def test_cleared_sets_outcome_cleared_flag(self):
        db = _make_db()
        fp = _simple_fingerprint()
        aid = db.record_shipment(fp, "FLAG_FOR_INSPECTION", [], "HIGH")
        db.record_outcome(aid, OUTCOME_CLEARED)
        row = db._conn.execute(
            "SELECT outcome_cleared FROM shipment_history WHERE analysis_id=?", (aid,)
        ).fetchone()
        assert row["outcome_cleared"] == 1
        db.close()

    def test_fraud_does_not_set_outcome_cleared_flag(self):
        db = _make_db()
        fp = _simple_fingerprint()
        aid = db.record_shipment(fp, "FLAG_FOR_INSPECTION", [], "HIGH")
        db.record_outcome(aid, OUTCOME_CONFIRMED_FRAUD)
        row = db._conn.execute(
            "SELECT outcome_cleared FROM shipment_history WHERE analysis_id=?", (aid,)
        ).fetchone()
        assert row["outcome_cleared"] == 0
        db.close()


class TestAutoTrust:
    def test_auto_trust_fires_at_threshold(self):
        """Shipper should be auto-trusted after enough CLEARED outcomes."""
        db = _make_db()
        fp = _simple_fingerprint()

        # Record enough cleared outcomes to cross the auto-trust threshold.
        # With decay weight ≈ 1.0 per event, we need 20 cleared events.
        aids = []
        for _ in range(20):
            aid = db.record_shipment(fp, "FLAG_FOR_INSPECTION", [], "HIGH")
            aids.append(aid)

        for aid in aids:
            db.record_outcome(aid, OUTCOME_CLEARED)

        profile = db.get_shipper_profile("Acme Corp")
        assert profile.is_trusted
        assert profile.reputation_score == 0.0
        db.close()

    def test_auto_trust_blocked_by_fraud(self):
        """A shipper with even one confirmed fraud should not be auto-trusted."""
        db = _make_db()
        fp = _simple_fingerprint()

        aids = []
        for _ in range(20):
            aid = db.record_shipment(fp, "FLAG_FOR_INSPECTION", [], "HIGH")
            aids.append(aid)

        # One fraud mixed in
        db.record_outcome(aids[0], OUTCOME_CONFIRMED_FRAUD)
        for aid in aids[1:]:
            db.record_outcome(aid, OUTCOME_CLEARED)

        profile = db.get_shipper_profile("Acme Corp")
        assert not profile.is_trusted
        db.close()


class TestTrustedShipperReputation:
    def test_trusted_shipper_has_zero_score(self):
        """Once trusted, reputation score should stay at 0.0."""
        db = _make_db()
        fp = _simple_fingerprint()

        # Record many analyses to build the profile normally
        aids = []
        for _ in range(20):
            aid = db.record_shipment(fp, "FLAG_FOR_INSPECTION", [], "HIGH")
            aids.append(aid)
        for aid in aids:
            db.record_outcome(aid, OUTCOME_CLEARED)

        profile = db.get_shipper_profile("Acme Corp")
        assert profile.is_trusted
        assert profile.reputation_score == 0.0
        db.close()


class TestContextManager:
    def test_context_manager(self):
        with PatternDB(":memory:") as db:
            fp = _simple_fingerprint()
            aid = db.record_shipment(fp, "APPROVE", [], "LOW")
            assert len(aid) == 36
        # After exiting, DB is closed; should not raise


class TestHSBaselineAccumulation:
    def test_running_stats_correct_after_many_values(self):
        db = _make_db()
        values = [float(i * 10) for i in range(1, 11)]  # 10, 20, ..., 100
        for v in values:
            fp = _simple_fingerprint(declared_value_usd=v, quantity=1.0)
            db.record_shipment(fp, "APPROVE", [], "LOW")
        baseline = db.get_hs_baseline("8471.30")
        assert baseline.sample_count == 10
        assert abs(baseline.mean_unit_value - 55.0) < 1e-6
        assert baseline.std_dev is not None
        assert baseline.min_value == 10.0
        assert baseline.max_value == 100.0
        db.close()


class TestEntityNameVariants:
    def test_name_variants_share_profile(self):
        """Two name variants that normalize to the same string share one profile."""
        db = _make_db()
        fp1 = _simple_fingerprint(shipper_name="Acme Corp.")
        fp2 = _simple_fingerprint(shipper_name="ACME CORP")
        db.record_shipment(fp1, "APPROVE", [], "LOW")
        db.record_shipment(fp2, "APPROVE", [], "LOW")
        profile1 = db.get_shipper_profile("Acme Corp.")
        profile2 = db.get_shipper_profile("ACME CORP")
        assert profile1.shipper_key == profile2.shipper_key
        assert profile1.history_count == 2
        db.close()
