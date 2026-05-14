"""
portguard/pattern_engine.py — Pattern detection engine using the pattern_store table.

Reads from and writes to the ``pattern_store`` table (migration 010) via a PatternDB
instance.  All public functions are best-effort: exceptions are caught, logged as
warnings, and do not propagate to callers.

Public API
----------
record_signals(db, org_email, analysis_result)
    Upsert SHIPPER_REP / ROUTE_RISK / VALUE_ANOMALY after every completed analysis.

apply_pattern_adjustments(db, org_email, parsed_doc) -> dict
    Read patterns and return score adjustments before final scoring.

record_feedback(db, org_email, shipment_id, feedback_type, notes)
    Handle CONFIRMED_FRAUD / CLEARED feedback — increments fraud_confirmed_count
    or cleared_count on the matching SHIPPER_REP + ROUTE_RISK rows.

get_pattern_stats(db, org_email) -> dict
    Aggregate statistics for the pattern dashboard.

reset_patterns(db, org_email) -> int
    Delete all pattern_store rows for the org.  Returns row count deleted.

Value bucket thresholds (declared_value_usd)
--------------------------------------------
LOW        < $5,000
MEDIUM     $5,000 – $49,999
HIGH       $50,000 – $499,999
VERY_HIGH  >= $500,000

Signal scoring thresholds
--------------------------
SHIPPER_REP:
    fraud_confirmed_count > 0               → hard_flag = True
    flag_rate > 0.6                         → +2.5
    flag_rate > 0.3                         → +1.5
    cleared_count > 2                       → −0.5

ROUTE_RISK:
    flag_rate > 0.5 AND occurrence ≥ 3     → +2.0
    flag_rate > 0.3 AND occurrence ≥ 5     → +1.0

VALUE_ANOMALY:
    flag_rate > 0.5 AND occurrence ≥ 4     → +1.5

total_adjustment = max(0, sum of three adjustments)
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Value bucket thresholds (USD)
_LOW_THRESHOLD: float = 5_000.0
_MEDIUM_THRESHOLD: float = 50_000.0
_HIGH_THRESHOLD: float = 500_000.0

# Decisions that increment flag_count
_FLAGGED_DECISIONS = frozenset({"FLAG_FOR_INSPECTION", "REQUEST_MORE_INFO"})


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_key(name: str) -> str:
    """Normalize an entity name for use as signal_key (lowercase ASCII, no punctuation)."""
    if not name:
        return ""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", errors="ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_str.lower()).strip()


def _route_key(origin: Optional[str], destination: Optional[str]) -> str:
    """Return 'ORIGIN→DEST', e.g. 'CN→US'."""
    o = (origin or "??").upper()
    d = (destination or "US").upper()
    return f"{o}→{d}"


def _value_bucket(usd: Optional[float]) -> str:
    """Classify a declared_value_usd into a bucket label."""
    if usd is None or usd < 0:
        return "LOW"
    if usd < _LOW_THRESHOLD:
        return "LOW"
    if usd < _MEDIUM_THRESHOLD:
        return "MEDIUM"
    if usd < _HIGH_THRESHOLD:
        return "HIGH"
    return "VERY_HIGH"


def _value_anomaly_key(bucket: str, origin: Optional[str]) -> str:
    """Return 'BUCKET:ORIGIN', e.g. 'HIGH:CN'."""
    return f"{bucket}:{(origin or '??').upper()}"


def _get(obj: Any, *keys: str, default: Any = None) -> Any:
    """Safe multi-key get from a dict or object; returns first non-None value found."""
    for key in keys:
        v = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
        if v is not None:
            return v
    return default


def _extract_fields(doc: Any) -> dict:
    """Extract standard shipment fields from a dict or object."""
    d = doc if isinstance(doc, dict) else (vars(doc) if hasattr(doc, "__dict__") else {})
    return {
        "shipper":        _get(d, "exporter", "shipper_name", "shipper"),
        "consignee":      _get(d, "importer", "consignee_name", "consignee"),
        "origin_iso2":    _get(d, "origin_iso2", "origin_country_iso2"),
        "destination_iso2": _get(d, "destination_iso2") or "US",
        "declared_value_usd": _get(d, "declared_value_usd", "declared_value"),
        "decision":       _get(d, "final_decision", "decision"),
        "risk_score":     _get(d, "final_risk_score", "risk_score"),
    }


def _upsert_signal(
    conn,
    org_email: str,
    signal_type: str,
    signal_key: str,
    is_flagged: bool,
    risk_score: float,
    decision: Optional[str],
) -> None:
    """Insert or increment one pattern_store row.

    Works on both SQLite (>= 3.24) and PostgreSQL via the ``excluded`` pseudo-table
    reference, which both databases support in ON CONFLICT DO UPDATE clauses.
    """
    now = _utcnow()
    flag_inc = 1 if is_flagged else 0
    conn.execute(
        text("""
            INSERT INTO pattern_store
                (organization_email, signal_type, signal_key,
                 occurrence_count, flag_count, fraud_confirmed_count, cleared_count,
                 last_seen, first_seen, avg_risk_score, last_decision, notes)
            VALUES
                (:org, :stype, :skey,
                 1, :finc, 0, 0,
                 :now, :now, :score, :decision, NULL)
            ON CONFLICT(organization_email, signal_type, signal_key) DO UPDATE SET
                occurrence_count = occurrence_count + 1,
                flag_count       = flag_count + excluded.flag_count,
                last_seen        = excluded.last_seen,
                avg_risk_score   = (avg_risk_score * occurrence_count + excluded.avg_risk_score)
                                    / (occurrence_count + 1),
                last_decision    = excluded.last_decision
        """),
        {
            "org": org_email,
            "stype": signal_type,
            "skey": signal_key,
            "finc": flag_inc,
            "now": now,
            "score": risk_score,
            "decision": decision,
        },
    )


def _fetch_record(conn, org_email: str, signal_type: str, signal_key: str) -> Optional[dict]:
    """Return pattern_store row as a dict, or None if the row doesn't exist."""
    row = conn.execute(
        text("""
            SELECT occurrence_count, flag_count, fraud_confirmed_count, cleared_count,
                   avg_risk_score, last_decision
            FROM pattern_store
            WHERE organization_email = :org
              AND signal_type        = :stype
              AND signal_key         = :skey
        """),
        {"org": org_email, "stype": signal_type, "skey": signal_key},
    ).fetchone()
    if row is None:
        return None
    return {
        "occurrence_count":     row[0],
        "flag_count":           row[1],
        "fraud_confirmed_count": row[2],
        "cleared_count":        row[3],
        "avg_risk_score":       row[4],
        "last_decision":        row[5],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def record_signals(db, org_email: str, analysis_result: Any) -> None:
    """Upsert SHIPPER_REP, ROUTE_RISK, VALUE_ANOMALY after a completed analysis.

    ``flag_count`` is incremented when ``final_decision`` is FLAG_FOR_INSPECTION
    or REQUEST_MORE_INFO.  This function is non-fatal: any exception is caught
    and logged as a warning.
    """
    try:
        f = _extract_fields(analysis_result)
        decision = f["decision"] or ""
        risk_score = float(f["risk_score"] or 0.0)
        is_flagged = decision in _FLAGGED_DECISIONS

        with db._engine.begin() as conn:
            if f["shipper"]:
                _upsert_signal(
                    conn, org_email,
                    "SHIPPER_REP", _normalize_key(f["shipper"]),
                    is_flagged, risk_score, decision,
                )

            if f["origin_iso2"]:
                _upsert_signal(
                    conn, org_email,
                    "ROUTE_RISK", _route_key(f["origin_iso2"], f["destination_iso2"]),
                    is_flagged, risk_score, decision,
                )

            if f["declared_value_usd"] is not None and f["origin_iso2"]:
                bucket = _value_bucket(f["declared_value_usd"])
                _upsert_signal(
                    conn, org_email,
                    "VALUE_ANOMALY", _value_anomaly_key(bucket, f["origin_iso2"]),
                    is_flagged, risk_score, decision,
                )
    except Exception as exc:
        logger.warning("record_signals failed (non-fatal): %s", exc)


def apply_pattern_adjustments(db, org_email: str, parsed_doc: Any) -> dict:
    """Look up pattern_store and return score adjustments for a shipment being screened.

    Returns a dict with the following keys:

        shipper_adjustment  float    score delta from SHIPPER_REP history
        route_adjustment    float    score delta from ROUTE_RISK history
        value_adjustment    float    score delta from VALUE_ANOMALY history
        hard_flag           bool     True when shipper has confirmed fraud on record
        hard_flag_reason    str|None human-readable reason string for hard_flag
        pattern_warnings    list[str] warning messages for the findings panel
        pattern_boosts      list[str] positive signals (cleared history etc.)
        total_adjustment    float    max(0, shipper + route + value adjustments)

    The function is non-fatal: any exception returns the zeroed-out default dict.
    """
    result: dict = {
        "shipper_adjustment": 0.0,
        "route_adjustment":   0.0,
        "value_adjustment":   0.0,
        "hard_flag":          False,
        "hard_flag_reason":   None,
        "pattern_warnings":   [],
        "pattern_boosts":     [],
        "total_adjustment":   0.0,
    }

    try:
        f = _extract_fields(parsed_doc)

        with db._engine.connect() as conn:
            # ---- SHIPPER_REP ------------------------------------------------
            if f["shipper"]:
                skey = _normalize_key(f["shipper"])
                rec = _fetch_record(conn, org_email, "SHIPPER_REP", skey)
                if rec is not None:
                    occ   = rec["occurrence_count"]
                    flags = rec["flag_count"]
                    frauds = rec["fraud_confirmed_count"]
                    clears = rec["cleared_count"]
                    flag_rate = flags / occ if occ > 0 else 0.0

                    if frauds > 0:
                        reason = (
                            f"CONFIRMED FRAUD HISTORY: {f['shipper']} has "
                            f"{frauds} confirmed fraud outcome(s) on record "
                            "— manual review required"
                        )
                        result["hard_flag"] = True
                        result["hard_flag_reason"] = reason
                        result["pattern_warnings"].append(f"⚠ {reason}")
                    elif flag_rate > 0.6:
                        result["shipper_adjustment"] = 2.5
                        result["pattern_warnings"].append(
                            f"⚠ High-risk shipper: {f['shipper']} flagged on "
                            f"{flags}/{occ} shipments ({flag_rate:.0%} flag rate)"
                        )
                    elif flag_rate > 0.3:
                        result["shipper_adjustment"] = 1.5
                        result["pattern_warnings"].append(
                            f"Elevated-risk shipper: {f['shipper']} flagged on "
                            f"{flags}/{occ} shipments ({flag_rate:.0%} flag rate)"
                        )

                    if clears > 2 and not result["hard_flag"]:
                        result["shipper_adjustment"] = max(
                            0.0, result["shipper_adjustment"] - 0.5
                        )
                        result["pattern_boosts"].append(
                            f"Shipper {f['shipper']} has {clears} cleared shipments on record"
                        )

            # ---- ROUTE_RISK -------------------------------------------------
            if f["origin_iso2"]:
                rkey = _route_key(f["origin_iso2"], f["destination_iso2"])
                rec = _fetch_record(conn, org_email, "ROUTE_RISK", rkey)
                if rec is not None:
                    occ   = rec["occurrence_count"]
                    flags = rec["flag_count"]
                    flag_rate = flags / occ if occ > 0 else 0.0

                    if flag_rate > 0.5 and occ >= 3:
                        result["route_adjustment"] = 2.0
                        result["pattern_warnings"].append(
                            f"⚠ High-risk route {rkey}: {flags}/{occ} shipments flagged "
                            f"({flag_rate:.0%} flag rate)"
                        )
                    elif flag_rate > 0.3 and occ >= 5:
                        result["route_adjustment"] = 1.0
                        result["pattern_warnings"].append(
                            f"Elevated-risk route {rkey}: {flags}/{occ} shipments flagged "
                            f"({flag_rate:.0%} flag rate)"
                        )

            # ---- VALUE_ANOMALY ----------------------------------------------
            if f["declared_value_usd"] is not None and f["origin_iso2"]:
                bucket = _value_bucket(f["declared_value_usd"])
                vkey   = _value_anomaly_key(bucket, f["origin_iso2"])
                rec = _fetch_record(conn, org_email, "VALUE_ANOMALY", vkey)
                if rec is not None:
                    occ   = rec["occurrence_count"]
                    flags = rec["flag_count"]
                    flag_rate = flags / occ if occ > 0 else 0.0

                    if flag_rate > 0.5 and occ >= 4:
                        result["value_adjustment"] = 1.5
                        result["pattern_warnings"].append(
                            f"⚠ Value anomaly pattern: {bucket} value shipments from "
                            f"{f['origin_iso2']} flagged on {flags}/{occ} occasions"
                        )

        total = (
            result["shipper_adjustment"]
            + result["route_adjustment"]
            + result["value_adjustment"]
        )
        result["total_adjustment"] = max(0.0, total)

    except Exception as exc:
        logger.warning("apply_pattern_adjustments failed (non-fatal): %s", exc)

    return result


def record_feedback(
    db,
    org_email: str,
    shipment_id: str,
    feedback_type: str,
    notes: Optional[str] = None,
) -> None:
    """Increment fraud_confirmed_count or cleared_count on SHIPPER_REP + ROUTE_RISK rows.

    ``feedback_type`` must be ``'CONFIRMED_FRAUD'`` or ``'CLEARED'``.
    Only updates rows that already exist in pattern_store — never creates new rows.
    Non-fatal: exceptions are caught and logged.
    """
    if feedback_type not in ("CONFIRMED_FRAUD", "CLEARED"):
        logger.warning("record_feedback: unknown feedback_type %r — ignoring", feedback_type)
        return

    try:
        with db._engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT shipper_name, origin_iso2, destination_iso2
                    FROM shipment_history
                    WHERE analysis_id = :sid
                """),
                {"sid": shipment_id},
            ).fetchone()

        if row is None:
            logger.warning("record_feedback: shipment_id %r not found in shipment_history", shipment_id)
            return

        shipper_name  = row[0]
        origin        = row[1]
        destination   = row[2] or "US"
        now           = _utcnow()

        candidates = []
        if shipper_name:
            candidates.append(("SHIPPER_REP", _normalize_key(shipper_name)))
        if origin:
            candidates.append(("ROUTE_RISK", _route_key(origin, destination)))

        with db._engine.begin() as conn:
            for signal_type, signal_key in candidates:
                if feedback_type == "CONFIRMED_FRAUD":
                    conn.execute(
                        text("""
                            UPDATE pattern_store
                               SET fraud_confirmed_count = fraud_confirmed_count + 1,
                                   last_seen = :now,
                                   notes     = :notes
                             WHERE organization_email = :org
                               AND signal_type        = :stype
                               AND signal_key         = :skey
                        """),
                        {"org": org_email, "stype": signal_type, "skey": signal_key,
                         "now": now, "notes": notes},
                    )
                else:  # CLEARED
                    conn.execute(
                        text("""
                            UPDATE pattern_store
                               SET cleared_count = cleared_count + 1,
                                   last_seen     = :now,
                                   notes         = :notes
                             WHERE organization_email = :org
                               AND signal_type        = :stype
                               AND signal_key         = :skey
                        """),
                        {"org": org_email, "stype": signal_type, "skey": signal_key,
                         "now": now, "notes": notes},
                    )

    except Exception as exc:
        logger.warning("record_feedback failed (non-fatal): %s", exc)


def get_pattern_stats(db, org_email: str) -> dict:
    """Return aggregate statistics from pattern_store for a given org.

    Returns a dict with:
        total_shipments_screened  int        sum of occurrence_count across SHIPPER_REP rows
        unique_shippers_tracked   int        count of distinct SHIPPER_REP rows
        unique_routes_tracked     int        count of distinct ROUTE_RISK rows
        confirmed_fraud_count     int        sum of fraud_confirmed_count across SHIPPER_REP rows
        avg_org_risk_score        float      mean avg_risk_score across SHIPPER_REP rows
        total_flags_issued        int        sum of flag_count across SHIPPER_REP rows
        approval_rate             float      (1 - total_flags/total_screened) * 100
        high_risk_shippers        list[dict] SHIPPER_REP rows with flag_rate > 0.3 (top 10)
        high_risk_routes          list[dict] ROUTE_RISK rows with flag_rate > 0.3 and occ >= 3 (top 10)
        value_anomalies           list[dict] top 3 VALUE_ANOMALY rows by flag_count
        cleared_shippers          list[dict] SHIPPER_REP rows with cleared_count > 0 (top 10)
        has_history               bool       True when at least one SHIPPER_REP row exists

    Each item in high_risk_shippers has:
        signal_key, flag_count, occurrence_count, avg_risk_score, fraud_confirmed_count,
        last_seen, last_decision, flag_rate

    Each item in high_risk_routes has:
        signal_key, flag_count, occurrence_count, avg_risk_score, last_seen,
        last_decision, flag_rate

    Each item in value_anomalies has:
        signal_key, flag_count, occurrence_count, flag_rate

    Each item in cleared_shippers has:
        signal_key, cleared_count, occurrence_count
    """
    defaults: dict = {
        "total_shipments_screened": 0,
        "unique_shippers_tracked":  0,
        "unique_routes_tracked":    0,
        "confirmed_fraud_count":    0,
        "avg_org_risk_score":       0.0,
        "total_flags_issued":       0,
        "approval_rate":            100.0,
        "high_risk_shippers":       [],
        "high_risk_routes":         [],
        "value_anomalies":          [],
        "cleared_shippers":         [],
        "has_history":              False,
    }

    try:
        with db._engine.connect() as conn:
            # Aggregate totals
            row = conn.execute(
                text("""
                    SELECT
                        SUM(CASE WHEN signal_type = 'SHIPPER_REP'
                                 THEN occurrence_count ELSE 0 END),
                        COUNT(CASE WHEN signal_type = 'SHIPPER_REP' THEN 1 END),
                        COUNT(CASE WHEN signal_type = 'ROUTE_RISK'  THEN 1 END),
                        SUM(CASE WHEN signal_type = 'SHIPPER_REP'
                                 THEN fraud_confirmed_count ELSE 0 END),
                        SUM(CASE WHEN signal_type = 'SHIPPER_REP'
                                 THEN flag_count ELSE 0 END),
                        AVG(CASE WHEN signal_type = 'SHIPPER_REP'
                                 THEN avg_risk_score END)
                    FROM pattern_store
                    WHERE organization_email = :org
                """),
                {"org": org_email},
            ).fetchone()

            if row and row[1] is not None and int(row[1] or 0) > 0:
                total_screened = int(row[0] or 0)
                total_flags    = int(row[4] or 0)
                approval_rate  = round((1 - total_flags / max(total_screened, 1)) * 100, 1)
                defaults.update({
                    "total_shipments_screened": total_screened,
                    "unique_shippers_tracked":  int(row[1] or 0),
                    "unique_routes_tracked":    int(row[2] or 0),
                    "confirmed_fraud_count":    int(row[3] or 0),
                    "total_flags_issued":       total_flags,
                    "approval_rate":            max(0.0, approval_rate),
                    "avg_org_risk_score":       round(float(row[5] or 0.0), 1),
                    "has_history":              True,
                })

                # High-risk shippers (flag_rate > 0.3, top 10 by flag_rate then occurrence)
                hr_shippers = conn.execute(
                    text("""
                        SELECT signal_key, flag_count, occurrence_count, avg_risk_score,
                               fraud_confirmed_count, last_seen, last_decision
                        FROM pattern_store
                        WHERE organization_email = :org
                          AND signal_type = 'SHIPPER_REP'
                          AND occurrence_count > 0
                          AND CAST(flag_count AS REAL) / occurrence_count > 0.3
                        ORDER BY CAST(flag_count AS REAL) / occurrence_count DESC,
                                 occurrence_count DESC
                        LIMIT 10
                    """),
                    {"org": org_email},
                ).fetchall()
                defaults["high_risk_shippers"] = [
                    {
                        "signal_key":            r[0],
                        "flag_count":            r[1],
                        "occurrence_count":      r[2],
                        "avg_risk_score":        round(float(r[3] or 0.0), 1),
                        "fraud_confirmed_count": int(r[4] or 0),
                        "last_seen":             r[5],
                        "last_decision":         r[6],
                        "flag_rate":             round(r[1] / max(r[2], 1) * 100),
                    }
                    for r in hr_shippers
                ]

                # High-risk routes (flag_rate > 0.3, occ >= 3, top 10)
                hr_routes = conn.execute(
                    text("""
                        SELECT signal_key, flag_count, occurrence_count, avg_risk_score,
                               last_seen, last_decision
                        FROM pattern_store
                        WHERE organization_email = :org
                          AND signal_type = 'ROUTE_RISK'
                          AND occurrence_count >= 3
                          AND CAST(flag_count AS REAL) / occurrence_count > 0.3
                        ORDER BY CAST(flag_count AS REAL) / occurrence_count DESC,
                                 occurrence_count DESC
                        LIMIT 10
                    """),
                    {"org": org_email},
                ).fetchall()
                defaults["high_risk_routes"] = [
                    {
                        "signal_key":       r[0],
                        "flag_count":       r[1],
                        "occurrence_count": r[2],
                        "avg_risk_score":   round(float(r[3] or 0.0), 1),
                        "last_seen":        r[4],
                        "last_decision":    r[5],
                        "flag_rate":        round(r[1] / max(r[2], 1) * 100),
                    }
                    for r in hr_routes
                ]

                # Value anomalies (top 3 by flag_count)
                val_anomalies = conn.execute(
                    text("""
                        SELECT signal_key, flag_count, occurrence_count
                        FROM pattern_store
                        WHERE organization_email = :org
                          AND signal_type = 'VALUE_ANOMALY'
                        ORDER BY flag_count DESC, occurrence_count DESC
                        LIMIT 3
                    """),
                    {"org": org_email},
                ).fetchall()
                defaults["value_anomalies"] = [
                    {
                        "signal_key":       r[0],
                        "flag_count":       r[1],
                        "occurrence_count": r[2],
                        "flag_rate":        round(r[1] / max(r[2], 1) * 100),
                    }
                    for r in val_anomalies
                ]

                # Cleared shippers (cleared_count > 0, top 10 by cleared_count)
                cleared = conn.execute(
                    text("""
                        SELECT signal_key, cleared_count, occurrence_count
                        FROM pattern_store
                        WHERE organization_email = :org
                          AND signal_type = 'SHIPPER_REP'
                          AND cleared_count > 0
                        ORDER BY cleared_count DESC, occurrence_count DESC
                        LIMIT 10
                    """),
                    {"org": org_email},
                ).fetchall()
                defaults["cleared_shippers"] = [
                    {
                        "signal_key":       r[0],
                        "cleared_count":    r[1],
                        "occurrence_count": r[2],
                    }
                    for r in cleared
                ]

    except Exception as exc:
        logger.warning("get_pattern_stats failed (non-fatal): %s", exc)

    return defaults


def reset_patterns(db, org_email: str) -> int:
    """Delete all pattern_store rows for an org.  Returns the count of deleted rows."""
    try:
        with db._engine.begin() as conn:
            result = conn.execute(
                text("DELETE FROM pattern_store WHERE organization_email = :org"),
                {"org": org_email},
            )
            return result.rowcount
    except Exception as exc:
        logger.warning("reset_patterns failed (non-fatal): %s", exc)
        return 0
