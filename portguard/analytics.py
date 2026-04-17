"""
portguard/analytics.py — Read-only analytics layer for the PortGuard dashboard.

Provides DashboardAnalytics, a lightweight query class that opens its own
read connection against the pattern database and returns structured metrics
consumed by the dashboard API endpoints.

Design principles
-----------------
- **Read-only, no locks.** DashboardAnalytics never writes.
- **Tenant-isolated.** Every public method accepts ``organization_id`` and
  filters all queries accordingly.  No cross-org data can leak.
- **Never crashes the API.** Every public method catches all exceptions,
  logs a warning, and returns a safe empty/zero response.
- **Self-contained.** This module has no imports from pattern_db or
  pattern_engine — it queries the schema directly.

Thread safety
-------------
SQLAlchemy's connection pool handles concurrency for both SQLite and PostgreSQL.

Usage
-----
::

    from portguard.analytics import DashboardAnalytics

    db = DashboardAnalytics("portguard_patterns.db")

    summary  = db.get_summary_stats(organization_id="org-uuid-here")
    trend    = db.get_fraud_trend(organization_id="org-uuid-here", days=30)
    shippers = db.get_top_risky_shippers(organization_id="org-uuid-here", limit=10)
    db.close()
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from portguard.db import get_engine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTS chapter → human-readable label
# Covers the 20 most common chapters seen in US import data.
# ---------------------------------------------------------------------------

_HTS_CHAPTER_LABELS: dict[str, str] = {
    "01": "Ch.01 — Live Animals",
    "02": "Ch.02 — Meat",
    "03": "Ch.03 — Fish & Seafood",
    "04": "Ch.04 — Dairy & Eggs",
    "07": "Ch.07 — Vegetables",
    "08": "Ch.08 — Fruit",
    "09": "Ch.09 — Coffee/Tea/Spices",
    "10": "Ch.10 — Cereals/Grain",
    "16": "Ch.16 — Prepared Seafood",
    "20": "Ch.20 — Preserved Vegetables",
    "22": "Ch.22 — Beverages",
    "28": "Ch.28 — Industrial Chemicals",
    "29": "Ch.29 — Organic Chemicals",
    "30": "Ch.30 — Pharmaceuticals",
    "39": "Ch.39 — Plastics",
    "44": "Ch.44 — Wood",
    "48": "Ch.48 — Paper",
    "52": "Ch.52 — Cotton",
    "61": "Ch.61 — Knitted Apparel",
    "62": "Ch.62 — Woven Apparel",
    "63": "Ch.63 — Textiles",
    "72": "Ch.72 — Iron & Steel",
    "73": "Ch.73 — Steel Articles",
    "74": "Ch.74 — Copper",
    "76": "Ch.76 — Aluminum",
    "84": "Ch.84 — Machinery",
    "85": "Ch.85 — Electronics",
    "87": "Ch.87 — Vehicles",
    "90": "Ch.90 — Optics/Medical",
    "93": "Ch.93 — Firearms",
    "94": "Ch.94 — Furniture",
    "95": "Ch.95 — Toys",
}

# Decision values used by the rule engine (Entry Point 1)
_ALL_DECISIONS: tuple[str, ...] = (
    "APPROVE",
    "REVIEW_RECOMMENDED",
    "FLAG_FOR_INSPECTION",
    "REQUEST_MORE_INFORMATION",
    "REJECT",
)


# ---------------------------------------------------------------------------
# DashboardAnalytics
# ---------------------------------------------------------------------------


class DashboardAnalytics:
    """Read-only analytics query engine for the PortGuard dashboard.

    Uses SQLAlchemy so it works with both SQLite (local dev) and PostgreSQL
    (Render production).  All public methods are safe to call even when the
    database is empty — each returns a well-defined zero/empty response.

    Parameters
    ----------
    db_path:
        Filesystem path to ``portguard_patterns.db``.  Ignored when
        DATABASE_URL points to PostgreSQL.

    Raises
    ------
    Never.  All errors are caught internally and logged.

    Examples
    --------
    ::

        db = DashboardAnalytics("portguard_patterns.db")
        if db.available:
            stats = db.get_summary_stats("my-org-id")
        db.close()
    """

    def __init__(self, db_path: str | Path = "portguard_patterns.db") -> None:
        self._db_path = str(db_path)
        self._engine: Engine | None = None
        self.available: bool = False

        try:
            engine, _ = get_engine(self._db_path)
            self._engine = engine
            # Validate the connection by confirming the schema table exists.
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1 FROM shipment_history LIMIT 1"))
            self.available = True
            logger.info("DashboardAnalytics connected to %s", self._db_path)
        except Exception as exc:
            logger.warning(
                "DashboardAnalytics could not open %s: %s — analytics will return empty responses",
                self._db_path,
                exc,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _utc_cutoff(days: int) -> str:
        """Return an ISO-8601 UTC string *days* days in the past.

        Used as the lower bound for rolling-window queries.

        Parameters
        ----------
        days:
            Number of days to look back.

        Returns
        -------
        str
            ISO-8601 UTC timestamp string, e.g. ``"2026-03-12T00:00:00+00:00"``.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        # Truncate to midnight so DATE() grouping in SQLite aligns cleanly.
        cutoff = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
        return cutoff.isoformat()

    @staticmethod
    def _date_series(days: int) -> list[str]:
        """Return a list of ``YYYY-MM-DD`` strings for the last *days* calendar days.

        The list runs oldest-first and always includes today.  Used to fill
        gaps in the fraud-trend and risk-trend time series so the frontend
        receives a continuous axis without missing dates.

        Parameters
        ----------
        days:
            Number of calendar days to include (e.g. 30 → 30 entries).

        Returns
        -------
        list[str]
            ``["2026-03-13", "2026-03-14", ..., "2026-04-11"]``
        """
        today = datetime.now(timezone.utc).date()
        return [
            (today - timedelta(days=days - 1 - i)).isoformat()
            for i in range(days)
        ]

    def _query(self, sql: str, params: tuple = ()) -> list:
        """Execute a SELECT query and return all rows.

        Converts positional ``?`` placeholders to SQLAlchemy ``:p0, :p1, …``
        named params so the same query strings work with both SQLite and
        PostgreSQL engines.

        Returns an empty list if the engine is unavailable or the query fails.
        """
        if not self.available or self._engine is None:
            return []
        # Convert positional ? → :p0, :p1, … for SQLAlchemy text()
        named_sql = sql
        named_params: dict[str, Any] = {}
        for i, val in enumerate(params):
            named_sql = named_sql.replace("?", f":p{i}", 1)
            named_params[f"p{i}"] = val
        try:
            with self._engine.connect() as conn:
                result = conn.execute(text(named_sql), named_params)
                return result.mappings().fetchall()
        except Exception as exc:
            logger.warning("DashboardAnalytics query failed: %s | SQL: %.120s", exc, sql)
            return []

    def _scalar(self, sql: str, params: tuple = (), default: Any = 0) -> Any:
        """Execute a scalar SELECT (single value) and return the result.

        Returns *default* if the query returns no rows or fails.
        """
        rows = self._query(sql, params)
        if rows:
            val = next(iter(rows[0].values()), None)
            if val is not None:
                return val
        return default

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_summary_stats(self, organization_id: str = "__system__") -> dict:
        """Return high-level KPI metrics for the dashboard summary cards.

        Counts are scoped to *organization_id* across all time.  Average risk
        score and the fraud/cleared ratio reflect all shipments ever analyzed
        for this organization.

        Parameters
        ----------
        organization_id:
            Tenant scope.  Defaults to ``"__system__"`` for pre-auth data.

        Returns
        -------
        dict with keys:

            total_shipments (int):
                Total rows in ``shipment_history`` for this org.
            total_confirmed_fraud (int):
                Count of ``CONFIRMED_FRAUD`` outcomes in ``pattern_outcomes``.
            total_cleared (int):
                Count of ``CLEARED`` outcomes in ``pattern_outcomes``.
            total_unresolved (int):
                Count of ``UNRESOLVED`` outcomes.
            fraud_rate (float):
                ``confirmed_fraud / (confirmed_fraud + cleared)`` rounded to 4
                decimal places.  ``0.0`` when no resolved outcomes exist.
            avg_risk_score (float):
                Mean of ``final_risk_score`` across all shipments, rounded to 4
                decimal places.  ``0.0`` when no shipments exist.
            avg_pattern_score (float):
                Mean of ``pattern_score`` across shipments where the pattern
                engine contributed (i.e., ``pattern_cold_start = 0``).
                ``null`` / ``None`` when no warm-start shipments exist.
        """
        try:
            total_shipments: int = self._scalar(
                "SELECT COUNT(*) FROM shipment_history WHERE organization_id = ?",
                (organization_id,),
                default=0,
            )

            outcome_rows = self._query(
                """
                SELECT outcome, COUNT(*) AS cnt
                FROM   pattern_outcomes
                WHERE  organization_id = ?
                  AND  outcome IN ('CONFIRMED_FRAUD', 'CLEARED', 'UNRESOLVED')
                GROUP  BY outcome
                """,
                (organization_id,),
            )
            outcome_map: dict[str, int] = {r["outcome"]: r["cnt"] for r in outcome_rows}
            confirmed_fraud = outcome_map.get("CONFIRMED_FRAUD", 0)
            cleared         = outcome_map.get("CLEARED", 0)
            unresolved      = outcome_map.get("UNRESOLVED", 0)
            # Fraud rate = confirmed fraud / total shipments screened.
            # Using resolved_total (fraud + cleared) would give a misleading 100%
            # when no clearances have been submitted yet.
            fraud_rate = round(confirmed_fraud / total_shipments, 4) if total_shipments else 0.0

            avg_risk_row = self._query(
                """
                SELECT ROUND(AVG(final_risk_score), 4) AS avg_risk,
                       ROUND(AVG(CASE WHEN pattern_cold_start = 0
                                     THEN pattern_score END), 4) AS avg_pattern
                FROM   shipment_history
                WHERE  organization_id = ?
                """,
                (organization_id,),
            )
            avg_risk    = (avg_risk_row[0]["avg_risk"] or 0.0)  if avg_risk_row else 0.0
            avg_pattern = (avg_risk_row[0]["avg_pattern"])       if avg_risk_row else None

            return {
                "total_shipments":      total_shipments,
                "total_confirmed_fraud": confirmed_fraud,
                "total_cleared":        cleared,
                "total_unresolved":     unresolved,
                "fraud_rate":           fraud_rate,
                "avg_risk_score":       avg_risk,
                "avg_pattern_score":    avg_pattern,
            }

        except Exception as exc:
            logger.warning("get_summary_stats() failed: %s", exc)
            return {
                "total_shipments":      0,
                "total_confirmed_fraud": 0,
                "total_cleared":        0,
                "total_unresolved":     0,
                "fraud_rate":           0.0,
                "avg_risk_score":       0.0,
                "avg_pattern_score":    None,
            }

    def get_decision_breakdown(self, organization_id: str = "__system__") -> dict:
        """Return the count of each final decision type across all shipments.

        Covers all five decision values used by Entry Point 1:
        ``APPROVE``, ``REVIEW_RECOMMENDED``, ``FLAG_FOR_INSPECTION``,
        ``REQUEST_MORE_INFORMATION``, ``REJECT``.  Decision types with zero
        occurrences are always included in the response (never omitted),
        so the frontend can render a complete donut chart without special-casing.

        Parameters
        ----------
        organization_id:
            Tenant scope.

        Returns
        -------
        dict with keys:

            decisions (list[dict]):
                One entry per decision type, sorted highest-count-first.
                Each entry: ``{decision, label, count, percentage}``.
                ``label`` is a short human-readable string (e.g. ``"Flag"``).
                ``percentage`` is ``count / total * 100`` rounded to 1 dp.
            total (int):
                Sum of all decision counts.
        """
        _LABELS: dict[str, str] = {
            "APPROVE":                  "Approve",
            "REVIEW_RECOMMENDED":       "Review",
            "FLAG_FOR_INSPECTION":      "Flag",
            "REQUEST_MORE_INFORMATION": "More Info",
            "REJECT":                   "Reject",
        }
        try:
            rows = self._query(
                """
                SELECT final_decision, COUNT(*) AS cnt
                FROM   shipment_history
                WHERE  organization_id = ?
                GROUP  BY final_decision
                """,
                (organization_id,),
            )
            counts: dict[str, int] = {r["final_decision"]: r["cnt"] for r in rows}
            total = sum(counts.values())

            decisions = []
            for decision in _ALL_DECISIONS:
                cnt = counts.get(decision, 0)
                decisions.append({
                    "decision":   decision,
                    "label":      _LABELS.get(decision, decision),
                    "count":      cnt,
                    "percentage": round(cnt / total * 100, 1) if total else 0.0,
                })

            # Sort by count descending so the donut renders largest-slice-first.
            decisions.sort(key=lambda d: d["count"], reverse=True)

            return {"decisions": decisions, "total": total}

        except Exception as exc:
            logger.warning("get_decision_breakdown() failed: %s", exc)
            return {
                "decisions": [
                    {"decision": d, "label": _LABELS.get(d, d), "count": 0, "percentage": 0.0}
                    for d in _ALL_DECISIONS
                ],
                "total": 0,
            }

    def get_fraud_trend(
        self,
        organization_id: str = "__system__",
        days: int = 30,
    ) -> dict:
        """Return a daily time-series of fraud rate and analysis volume.

        The series always has exactly *days* entries (one per calendar day),
        oldest first.  Days with no recorded shipments receive
        ``total=0, fraud_count=0, fraud_rate=0.0`` so the frontend can plot
        a continuous x-axis without gap-handling logic.

        Parameters
        ----------
        organization_id:
            Tenant scope.
        days:
            Number of calendar days to include (default 30, max 365).

        Returns
        -------
        dict with keys:

            trend (list[dict]):
                One entry per day. Each entry:
                ``{day (YYYY-MM-DD), total, fraud_count, fraud_rate (0.0–1.0)}``.
            window_days (int):
                The *days* value used for this query.
            total_in_window (int):
                Sum of ``total`` across all entries (for summary display).
            fraud_in_window (int):
                Sum of ``fraud_count`` across all entries.
        """
        days = max(1, min(days, 365))
        try:
            cutoff = self._utc_cutoff(days)

            rows = self._query(
                """
                SELECT SUBSTR(sh.analyzed_at, 1, 10)                 AS day,
                       COUNT(DISTINCT sh.analysis_id)                AS total_analyzed,
                       COUNT(DISTINCT po.analysis_id)                AS fraud_count
                FROM   shipment_history sh
                LEFT   JOIN pattern_outcomes po
                         ON sh.analysis_id   = po.analysis_id
                        AND po.outcome        = 'CONFIRMED_FRAUD'
                        AND po.organization_id = ?
                WHERE  sh.organization_id = ?
                  AND  sh.analyzed_at     >= ?
                GROUP  BY day
                ORDER  BY day ASC
                """,
                (organization_id, organization_id, cutoff),
            )

            # Index DB results by day string for O(1) lookup during gap-fill.
            db_by_day: dict[str, dict] = {
                r["day"]: {
                    "total":       r["total_analyzed"],
                    "fraud_count": r["fraud_count"],
                }
                for r in rows
            }

            trend: list[dict] = []
            for day in self._date_series(days):
                entry = db_by_day.get(day, {"total": 0, "fraud_count": 0})
                total       = entry["total"]
                fraud_count = entry["fraud_count"]
                trend.append({
                    "day":        day,
                    "total":      total,
                    "fraud_count": fraud_count,
                    "fraud_rate": round(fraud_count / total, 4) if total else 0.0,
                })

            total_in_window = sum(e["total"]       for e in trend)
            fraud_in_window = sum(e["fraud_count"] for e in trend)

            return {
                "trend":            trend,
                "window_days":      days,
                "total_in_window":  total_in_window,
                "fraud_in_window":  fraud_in_window,
            }

        except Exception as exc:
            logger.warning("get_fraud_trend() failed: %s", exc)
            empty_trend = [
                {"day": d, "total": 0, "fraud_count": 0, "fraud_rate": 0.0}
                for d in self._date_series(days)
            ]
            return {
                "trend":           empty_trend,
                "window_days":     days,
                "total_in_window": 0,
                "fraud_in_window": 0,
            }

    def get_top_risky_shippers(
        self,
        organization_id: str = "__system__",
        limit: int = 10,
    ) -> dict:
        """Return the top riskiest shippers ranked by Bayesian reputation score.

        The ``reputation_score`` column in ``shipper_profiles`` is kept
        current by PatternDB on every ``record_shipment()`` and
        ``record_outcome()`` call, so no recomputation is needed here.

        Only shippers with at least one recorded analysis are returned.
        Trusted shippers (``is_trusted = 1``) are included so officers can
        see which entities have been auto-trusted and monitor for regressions.

        Parameters
        ----------
        organization_id:
            Tenant scope.
        limit:
            Maximum number of shippers to return (1–50).

        Returns
        -------
        dict with keys:

            shippers (list[dict]):
                Sorted by ``reputation_score`` descending.  Each entry:
                ``{name, reputation_score, total_analyses, total_confirmed_fraud,
                  total_cleared, is_trusted}``.
            total_profiles (int):
                Total number of shipper profiles for this org (regardless of
                the *limit*).
        """
        limit = max(1, min(limit, 50))
        try:
            rows = self._query(
                """
                SELECT shipper_name,
                       reputation_score,
                       total_analyses,
                       total_confirmed_fraud,
                       total_cleared,
                       is_trusted
                FROM   shipper_profiles
                WHERE  organization_id = ?
                  AND  total_analyses  >= 1
                ORDER  BY reputation_score DESC
                LIMIT  ?
                """,
                (organization_id, limit),
            )

            total_profiles: int = self._scalar(
                "SELECT COUNT(*) FROM shipper_profiles WHERE organization_id = ? AND total_analyses >= 1",
                (organization_id,),
                default=0,
            )

            shippers = [
                {
                    "name":                 r["shipper_name"] or "Unknown",
                    "reputation_score":     round(r["reputation_score"], 4),
                    "total_analyses":       r["total_analyses"],
                    "total_confirmed_fraud": r["total_confirmed_fraud"],
                    "total_cleared":        r["total_cleared"],
                    "is_trusted":           bool(r["is_trusted"]),
                }
                for r in rows
            ]

            return {"shippers": shippers, "total_profiles": total_profiles}

        except Exception as exc:
            logger.warning("get_top_risky_shippers() failed: %s", exc)
            return {"shippers": [], "total_profiles": 0}

    def get_top_risky_countries(
        self,
        organization_id: str = "__system__",
        limit: int = 10,
    ) -> dict:
        """Return origin countries ranked by confirmed-fraud count and average risk score.

        The primary sort key is confirmed fraud count (hard evidence); the
        secondary sort key is average final risk score (leading indicator for
        countries with few or no confirmed outcomes yet).

        Countries where ``origin_iso2`` is NULL (field not extracted from the
        document) are excluded.

        Parameters
        ----------
        organization_id:
            Tenant scope.
        limit:
            Maximum number of countries to return (1–50).

        Returns
        -------
        dict with keys:

            countries (list[dict]):
                Each entry: ``{iso2, country_name, total_shipments,
                confirmed_fraud_count, avg_risk_score, fraud_rate}``.
            total_countries (int):
                Distinct ``origin_iso2`` values seen for this org.
        """
        # ISO2 → display name for the most common trading partners.
        _ISO2_NAMES: dict[str, str] = {
            "CN": "China",        "VN": "Vietnam",      "MY": "Malaysia",
            "SG": "Singapore",    "KR": "South Korea",  "TW": "Taiwan",
            "IN": "India",        "TH": "Thailand",     "ID": "Indonesia",
            "BD": "Bangladesh",   "DE": "Germany",      "MX": "Mexico",
            "JP": "Japan",        "HK": "Hong Kong",    "PH": "Philippines",
            "PK": "Pakistan",     "LK": "Sri Lanka",    "TR": "Turkey",
            "GB": "United Kingdom","FR": "France",      "NL": "Netherlands",
            "IT": "Italy",        "BR": "Brazil",       "CA": "Canada",
            "AU": "Australia",    "IR": "Iran",         "KP": "North Korea",
            "RU": "Russia",       "BY": "Belarus",      "VE": "Venezuela",
            "MM": "Myanmar",      "US": "United States","KH": "Cambodia",
        }

        limit = max(1, min(limit, 50))
        try:
            rows = self._query(
                """
                SELECT sh.origin_iso2                                           AS iso2,
                       COUNT(DISTINCT sh.analysis_id)                           AS total_shipments,
                       COUNT(DISTINCT CASE WHEN po.outcome = 'CONFIRMED_FRAUD'
                                           THEN po.analysis_id END)             AS confirmed_fraud_count,
                       ROUND(AVG(sh.final_risk_score), 4)                       AS avg_risk_score
                FROM   shipment_history sh
                LEFT   JOIN pattern_outcomes po
                         ON sh.analysis_id    = po.analysis_id
                        AND po.organization_id = ?
                WHERE  sh.organization_id = ?
                  AND  sh.origin_iso2 IS NOT NULL
                GROUP  BY sh.origin_iso2
                ORDER  BY confirmed_fraud_count DESC, avg_risk_score DESC
                LIMIT  ?
                """,
                (organization_id, organization_id, limit),
            )

            total_countries: int = self._scalar(
                """
                SELECT COUNT(DISTINCT origin_iso2)
                FROM   shipment_history
                WHERE  organization_id = ?
                  AND  origin_iso2 IS NOT NULL
                """,
                (organization_id,),
                default=0,
            )

            countries = []
            for r in rows:
                iso2        = r["iso2"]
                total       = r["total_shipments"]
                fraud_count = r["confirmed_fraud_count"]
                countries.append({
                    "iso2":                 iso2,
                    "country_name":         _ISO2_NAMES.get(iso2, iso2),
                    "total_shipments":      total,
                    "confirmed_fraud_count": fraud_count,
                    "avg_risk_score":       r["avg_risk_score"] or 0.0,
                    "fraud_rate":           round(fraud_count / total, 4) if total else 0.0,
                })

            return {"countries": countries, "total_countries": total_countries}

        except Exception as exc:
            logger.warning("get_top_risky_countries() failed: %s", exc)
            return {"countries": [], "total_countries": 0}

    def get_top_flagged_hs_codes(
        self,
        organization_id: str = "__system__",
        limit: int = 10,
    ) -> dict:
        """Return HTS chapters ranked by the proportion of shipments that were flagged.

        ``hs_chapter_primary`` stores the first 2 characters of the primary
        declared HTS code (the chapter level, e.g. ``"84"`` for machinery,
        ``"85"`` for electronics).  Shipments where no HTS code was extracted
        from the document (NULL chapter) are excluded.

        A shipment is counted as "flagged" when ``final_decision`` is anything
        other than ``APPROVE``.

        Parameters
        ----------
        organization_id:
            Tenant scope.
        limit:
            Maximum number of chapters to return (1–50).

        Returns
        -------
        dict with keys:

            hs_codes (list[dict]):
                Sorted by ``flagged_count`` descending, then by
                ``flag_rate`` descending.  Each entry:
                ``{chapter, label, total_shipments, flagged_count, flag_rate,
                  avg_risk_score}``.
            total_chapters (int):
                Number of distinct HTS chapters seen for this org.
        """
        limit = max(1, min(limit, 50))
        try:
            rows = self._query(
                """
                SELECT hs_chapter_primary                                       AS chapter,
                       COUNT(*)                                                 AS total_shipments,
                       COUNT(CASE WHEN final_decision != 'APPROVE' THEN 1 END)  AS flagged_count,
                       ROUND(AVG(final_risk_score), 4)                          AS avg_risk_score
                FROM   shipment_history
                WHERE  organization_id    = ?
                  AND  hs_chapter_primary IS NOT NULL
                GROUP  BY hs_chapter_primary
                ORDER  BY flagged_count DESC, avg_risk_score DESC
                LIMIT  ?
                """,
                (organization_id, limit),
            )

            total_chapters: int = self._scalar(
                """
                SELECT COUNT(DISTINCT hs_chapter_primary)
                FROM   shipment_history
                WHERE  organization_id    = ?
                  AND  hs_chapter_primary IS NOT NULL
                """,
                (organization_id,),
                default=0,
            )

            hs_codes = []
            for r in rows:
                chapter = r["chapter"]
                total   = r["total_shipments"]
                flagged = r["flagged_count"]
                hs_codes.append({
                    "chapter":       chapter,
                    "label":         _HTS_CHAPTER_LABELS.get(chapter, f"Ch.{chapter}"),
                    "total_shipments": total,
                    "flagged_count": flagged,
                    "flag_rate":     round(flagged / total, 4) if total else 0.0,
                    "avg_risk_score": r["avg_risk_score"] or 0.0,
                })

            return {"hs_codes": hs_codes, "total_chapters": total_chapters}

        except Exception as exc:
            logger.warning("get_top_flagged_hs_codes() failed: %s", exc)
            return {"hs_codes": [], "total_chapters": 0}

    def get_recent_activity(
        self,
        organization_id: str = "__system__",
        limit: int = 20,
    ) -> dict:
        """Return the most recently analyzed shipments as an activity feed.

        Each entry includes enough data to render a feed row: timestamp,
        shipper name, origin country, decision, risk score, and any resolved
        officer verdict (outcome).  The ``outcome`` field is ``null`` when no
        feedback has been submitted for a shipment yet.

        Results are ordered by ``analyzed_at`` descending (newest first).

        Parameters
        ----------
        organization_id:
            Tenant scope.
        limit:
            Maximum number of rows to return (1–100).

        Returns
        -------
        dict with keys:

            activity (list[dict]):
                Each entry:
                ``{analysis_id, analyzed_at, shipper_name, origin_iso2,
                  final_decision, final_risk_score, pattern_cold_start,
                  outcome, officer_id}``.

                ``outcome`` is one of ``"CONFIRMED_FRAUD"``, ``"CLEARED"``,
                ``"UNRESOLVED"``, or ``null`` (no feedback yet).
                ``officer_id`` is the submitting officer or ``null``.
            total_shown (int):
                Number of entries in *activity* (≤ *limit*).
        """
        limit = max(1, min(limit, 100))
        try:
            rows = self._query(
                """
                SELECT sh.analysis_id,
                       sh.analyzed_at,
                       sh.shipper_name,
                       sh.origin_iso2,
                       sh.final_decision,
                       ROUND(sh.final_risk_score, 4)    AS final_risk_score,
                       sh.pattern_cold_start,
                       po.outcome,
                       po.officer_id
                FROM   shipment_history sh
                LEFT   JOIN pattern_outcomes po
                         ON sh.analysis_id    = po.analysis_id
                        AND po.organization_id = ?
                WHERE  sh.organization_id = ?
                ORDER  BY sh.analyzed_at DESC
                LIMIT  ?
                """,
                (organization_id, organization_id, limit),
            )

            activity = [
                {
                    "analysis_id":       r["analysis_id"],
                    "analyzed_at":       r["analyzed_at"],
                    "shipper_name":      r["shipper_name"],
                    "origin_iso2":       r["origin_iso2"],
                    "final_decision":    r["final_decision"],
                    "final_risk_score":  r["final_risk_score"],
                    "pattern_cold_start": bool(r["pattern_cold_start"]),
                    "outcome":           r["outcome"],
                    "officer_id":        r["officer_id"],
                }
                for r in rows
            ]

            return {"activity": activity, "total_shown": len(activity)}

        except Exception as exc:
            logger.warning("get_recent_activity() failed: %s", exc)
            return {"activity": [], "total_shown": 0}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Dispose the engine connection pool.

        Safe to call multiple times.  After ``close()``, all query methods
        return empty responses (``available`` is set to ``False``).
        """
        self.available = False
        if self._engine is not None:
            try:
                self._engine.dispose()
            except Exception:
                pass
            self._engine = None
        logger.debug("DashboardAnalytics engine disposed")

    def __repr__(self) -> str:
        return f"DashboardAnalytics(db_path={self._db_path!r}, available={self.available})"
