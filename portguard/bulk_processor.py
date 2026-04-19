"""
portguard/bulk_processor.py — Bulk shipment screening orchestrator.

BulkProcessor wraps the existing single-shipment analysis pipeline so that
up to 50 shipments can be screened in one batch.  Each shipment runs through
the identical pipeline as a normal POST /api/v1/analyze request.

Key design decisions
--------------------
- ``analyze_fn`` is injected at construction time so this module has zero
  imports from ``api/app.py``, avoiding circular imports.  The callable is
  a synchronous wrapper that the API layer provides.
- Processing uses ``asyncio.Semaphore(5)`` + ``ThreadPoolExecutor(max_workers=5)``
  so at most 5 shipments run concurrently at any point.  Each shipment call
  runs in a thread pool because ``analyze_fn`` is CPU-bound and synchronous.
- Every shipment is fault-isolated: any exception stores ERROR status and
  continues the batch.  The batch always reaches COMPLETE, never FAILED,
  due to individual-shipment errors.  FAILED is reserved for startup failures
  (e.g. no analyze_fn supplied).
- Database writes use SQLAlchemy ``engine.begin()`` (auto-commit/rollback).
  Counter increments use SQL arithmetic (``col = col + 1``) so concurrent
  threads never race on batch-level totals.
- Bulk tables live in the same SQLite file as PatternDB.  They are created by
  ``_ensure_tables()`` on first use AND by migration 005 in PatternDB when
  PatternDB initialises — whichever runs first wins.

Public API
----------
BulkProcessor(db_path, analyze_fn)
  .create_batch(org_id, shipments, input_method)  → batch_id (str)
  .process_batch(batch_id, shipments, org_id)     → None  (async coroutine)
  .get_batch_status(batch_id, org_id)             → BulkBatchStatus | None
  .get_batch_results(batch_id, org_id)            → dict | None
  .get_export_rows(batch_id, org_id)              → list[dict] | None
  .get_shipment_payloads(batch_id, org_id)        → list[dict] | None
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy import text

from portguard.db import adapt_stmt, get_engine, split_migration_sql

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_BATCH_SIZE: int = 50
SHIPMENT_TIMEOUT_SECONDS: float = 30.0
_SEMAPHORE_SIZE: int = 5

# Module-level thread pool — reused across all batches.
_BULK_EXECUTOR: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=5,
    thread_name_prefix="portguard-bulk",
)

# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------

BATCH_PENDING: str = "PENDING"
BATCH_PROCESSING: str = "PROCESSING"
BATCH_COMPLETE: str = "COMPLETE"
BATCH_FAILED: str = "FAILED"

SHIPMENT_PENDING: str = "PENDING"
SHIPMENT_PROCESSING: str = "PROCESSING"
SHIPMENT_COMPLETE: str = "COMPLETE"
SHIPMENT_ERROR: str = "ERROR"

_ALL_DECISIONS: tuple[str, ...] = (
    "APPROVE",
    "REVIEW_RECOMMENDED",
    "FLAG_FOR_INSPECTION",
    "REQUEST_MORE_INFORMATION",
    "REJECT",
)

# ---------------------------------------------------------------------------
# Return-value dataclasses
# ---------------------------------------------------------------------------


@dataclass
class BulkShipmentStatus:
    """Status of a single shipment within a batch."""
    ref: str
    status: str                      # PENDING | PROCESSING | COMPLETE | ERROR
    decision: Optional[str]
    risk_score: Optional[float]
    risk_level: Optional[str]
    n_findings: Optional[int]
    top_finding: Optional[str]
    analysis_id: Optional[str]
    error_message: Optional[str]
    processed_at: Optional[str]


@dataclass
class BulkBatchStatus:
    """Aggregate progress of a bulk batch."""
    batch_id: str
    organization_id: str
    status: str                      # PENDING | PROCESSING | COMPLETE | FAILED
    input_method: str                # ZIP | CSV | MANUAL
    total: int
    processed: int
    pending: int
    decisions: dict                  # {decision_type: count, "ERROR": count}
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    elapsed_seconds: float
    estimated_remaining_seconds: float
    shipments: list                  # list[BulkShipmentStatus]


# ---------------------------------------------------------------------------
# Bulk table DDL
# ---------------------------------------------------------------------------

_BULK_SCHEMA_SQL: str = """
CREATE TABLE IF NOT EXISTS bulk_batches (
    batch_id            TEXT    PRIMARY KEY,
    organization_id     TEXT    NOT NULL,
    status              TEXT    NOT NULL DEFAULT 'PENDING',
    input_method        TEXT    NOT NULL,
    total_shipments     INTEGER NOT NULL,
    processed_count     INTEGER NOT NULL DEFAULT 0,
    approved_count      INTEGER NOT NULL DEFAULT 0,
    review_count        INTEGER NOT NULL DEFAULT 0,
    flagged_count       INTEGER NOT NULL DEFAULT 0,
    needs_info_count    INTEGER NOT NULL DEFAULT 0,
    rejected_count      INTEGER NOT NULL DEFAULT 0,
    error_count         INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL,
    started_at          TEXT,
    completed_at        TEXT,
    error_message       TEXT
);

CREATE INDEX IF NOT EXISTS idx_bulk_batches_org
    ON bulk_batches(organization_id, created_at DESC);

CREATE TABLE IF NOT EXISTS bulk_shipments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        TEXT    NOT NULL,
    shipment_ref    TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'PENDING',
    decision        TEXT,
    risk_score      REAL,
    risk_level      TEXT,
    n_findings      INTEGER,
    top_finding     TEXT,
    analysis_id     TEXT,
    result_json     TEXT,
    error_message   TEXT,
    processed_at    TEXT,
    UNIQUE(batch_id, shipment_ref),
    FOREIGN KEY(batch_id) REFERENCES bulk_batches(batch_id)
);

CREATE INDEX IF NOT EXISTS idx_bulk_shipments_batch_status
    ON bulk_shipments(batch_id, status);

CREATE INDEX IF NOT EXISTS idx_bulk_shipments_analysis
    ON bulk_shipments(analysis_id);
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_seconds(start_iso: Optional[str], end_iso: Optional[str] = None) -> float:
    """Return elapsed seconds between two ISO timestamps.  Uses now() if end is None."""
    if not start_iso:
        return 0.0
    try:
        start_dt = datetime.fromisoformat(start_iso)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        if end_iso:
            end_dt = datetime.fromisoformat(end_iso)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
        else:
            end_dt = datetime.now(timezone.utc)
        return max(0.0, (end_dt - start_dt).total_seconds())
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# BulkProcessor
# ---------------------------------------------------------------------------


class BulkProcessor:
    """Orchestrates bulk shipment screening.

    Parameters
    ----------
    db_path:
        Path to the SQLite patterns database (same file as PatternDB).
        Bulk tables are created inside this file on first use.
    analyze_fn:
        Synchronous callable with signature::

            analyze_fn(documents: list[dict], org_id: str) -> dict

        Where *documents* is a list of ``{"filename": str, "raw_text": str}``
        dicts and the return value is a serialised ``AnalyzeResponse`` dict.
        On failure the callable must raise any ``Exception`` — BulkProcessor
        catches it and marks the shipment as ERROR.

        Pass ``None`` only in tests that exercise the DB layer without analysis.
    """

    def __init__(
        self,
        db_path: str | Path = "portguard_patterns.db",
        analyze_fn: Optional[Callable] = None,
    ) -> None:
        self._db_path = str(db_path)
        self._engine, self._dialect = get_engine(self._db_path)
        self._analyze_fn = analyze_fn
        self._ensure_tables()
        logger.info("BulkProcessor initialized (db=%s)", self._db_path)

    # ------------------------------------------------------------------
    # Schema setup
    # ------------------------------------------------------------------

    def _ensure_tables(self) -> None:
        """Create bulk tables if they do not exist.  Idempotent."""
        try:
            stmts = split_migration_sql(_BULK_SCHEMA_SQL, self._dialect)
            with self._engine.begin() as conn:
                for stmt in stmts:
                    adapted = adapt_stmt(stmt, self._dialect)
                    if adapted:
                        conn.execute(text(adapted))
        except Exception as exc:
            logger.warning(
                "BulkProcessor._ensure_tables() partial failure (non-fatal): %s", exc
            )

    # ------------------------------------------------------------------
    # Public API — create
    # ------------------------------------------------------------------

    def create_batch(
        self,
        org_id: str,
        shipments: list[dict],
        input_method: str,
    ) -> str:
        """Register a new batch in PENDING status and return its batch_id.

        This is synchronous and fast (two DB writes) so it can be called
        directly in the request handler before the response is sent.

        Parameters
        ----------
        org_id:
            Authenticated organisation UUID.
        shipments:
            List of ``{"ref": str, "documents": [...]}`` dicts.
        input_method:
            ``"ZIP"`` | ``"CSV"`` | ``"MANUAL"``

        Returns
        -------
        str
            New batch_id (UUID v4).
        """
        batch_id = str(uuid.uuid4())
        now = _utcnow()

        with self._engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO bulk_batches
                        (batch_id, organization_id, status, input_method,
                         total_shipments, created_at)
                    VALUES
                        (:batch_id, :org_id, 'PENDING', :method, :total, :now)
                """),
                {
                    "batch_id": batch_id,
                    "org_id": org_id,
                    "method": input_method,
                    "total": len(shipments),
                    "now": now,
                },
            )
            for shipment in shipments:
                conn.execute(
                    text("""
                        INSERT INTO bulk_shipments (batch_id, shipment_ref, status)
                        VALUES (:bid, :ref, 'PENDING')
                    """),
                    {"bid": batch_id, "ref": shipment["ref"]},
                )

        logger.info(
            "Bulk batch created: id=%s org=%s count=%d method=%s",
            batch_id, org_id, len(shipments), input_method,
        )
        return batch_id

    # ------------------------------------------------------------------
    # Public API — process (async)
    # ------------------------------------------------------------------

    async def process_batch(
        self,
        batch_id: str,
        shipments: list[dict],
        org_id: str,
    ) -> None:
        """Process all shipments in the batch concurrently.

        This is an ``async def`` coroutine designed to run as a FastAPI
        ``BackgroundTask``.  It returns only after every shipment has been
        processed (or timed out / errored).

        Each shipment analysis runs in a thread pool because ``analyze_fn``
        is synchronous.  At most ``_SEMAPHORE_SIZE`` analyses run simultaneously.
        Per-shipment timeouts of ``SHIPMENT_TIMEOUT_SECONDS`` prevent one slow
        document set from stalling the entire batch.

        Batch counters (``processed_count``, ``approved_count``, etc.) are
        incremented atomically via SQL arithmetic so concurrent thread writes
        never race.

        Parameters
        ----------
        batch_id:
            UUID returned by :meth:`create_batch`.
        shipments:
            Same list passed to :meth:`create_batch`.
        org_id:
            Authenticated organisation UUID.
        """
        if self._analyze_fn is None:
            msg = "No analyze_fn configured; cannot process batch."
            logger.error("BulkProcessor: %s (batch=%s)", msg, batch_id)
            self._mark_batch_failed(batch_id, msg)
            return

        self._mark_batch_processing(batch_id)
        loop = asyncio.get_running_loop()
        semaphore = asyncio.Semaphore(_SEMAPHORE_SIZE)

        async def _process_one(shipment: dict) -> None:
            ref = shipment["ref"]
            async with semaphore:
                self._mark_shipment_processing(batch_id, ref)
                try:
                    result: dict = await asyncio.wait_for(
                        loop.run_in_executor(
                            _BULK_EXECUTOR,
                            self._analyze_fn,
                            shipment["documents"],
                            org_id,
                        ),
                        timeout=SHIPMENT_TIMEOUT_SECONDS,
                    )
                    self._store_shipment_result(batch_id, ref, result)
                except asyncio.TimeoutError:
                    self._store_shipment_error(
                        batch_id,
                        ref,
                        f"Analysis timed out after {int(SHIPMENT_TIMEOUT_SECONDS)} seconds.",
                    )
                except Exception as exc:
                    self._store_shipment_error(batch_id, ref, str(exc))

        await asyncio.gather(*[_process_one(s) for s in shipments])
        self._mark_batch_complete(batch_id)
        logger.info("Bulk batch complete: id=%s", batch_id)

    # ------------------------------------------------------------------
    # Public API — query
    # ------------------------------------------------------------------

    def get_batch_status(
        self,
        batch_id: str,
        org_id: str,
    ) -> Optional[BulkBatchStatus]:
        """Return current processing status for a batch.

        Returns ``None`` if the batch does not exist or belongs to a different
        organisation.  Returning ``None`` vs raising lets the caller return
        HTTP 404 without leaking whether the batch_id exists.
        """
        with self._engine.connect() as conn:
            batch_row = conn.execute(
                text("""
                    SELECT * FROM bulk_batches
                    WHERE batch_id = :id AND organization_id = :org
                """),
                {"id": batch_id, "org": org_id},
            ).mappings().fetchone()

        if batch_row is None:
            return None

        with self._engine.connect() as conn:
            shipment_rows = conn.execute(
                text("""
                    SELECT shipment_ref, status, decision, risk_score, risk_level,
                           n_findings, top_finding, analysis_id, error_message, processed_at
                    FROM bulk_shipments
                    WHERE batch_id = :id
                    ORDER BY
                        CASE WHEN risk_score IS NULL THEN 1 ELSE 0 END ASC,
                        risk_score DESC,
                        shipment_ref ASC
                """),
                {"id": batch_id},
            ).mappings().fetchall()

        # Compute elapsed / ETA
        elapsed = _elapsed_seconds(
            batch_row["started_at"],
            batch_row["completed_at"],
        )

        total = batch_row["total_shipments"]
        processed = batch_row["processed_count"]
        pending = max(0, total - processed)
        eta = 0.0
        if processed > 0 and pending > 0 and elapsed > 0:
            eta = round((elapsed / processed) * pending, 1)

        decisions: dict[str, int] = {d: 0 for d in _ALL_DECISIONS}
        decisions["ERROR"] = batch_row["error_count"]
        decisions["APPROVE"] = batch_row["approved_count"]
        decisions["REVIEW_RECOMMENDED"] = batch_row["review_count"]
        decisions["FLAG_FOR_INSPECTION"] = batch_row["flagged_count"]
        decisions["REQUEST_MORE_INFORMATION"] = batch_row["needs_info_count"]
        decisions["REJECT"] = batch_row["rejected_count"]

        shipment_statuses = [
            BulkShipmentStatus(
                ref=r["shipment_ref"],
                status=r["status"],
                decision=r["decision"],
                risk_score=r["risk_score"],
                risk_level=r["risk_level"],
                n_findings=r["n_findings"],
                top_finding=r["top_finding"],
                analysis_id=r["analysis_id"],
                error_message=r["error_message"],
                processed_at=r["processed_at"],
            )
            for r in shipment_rows
        ]

        return BulkBatchStatus(
            batch_id=batch_id,
            organization_id=org_id,
            status=batch_row["status"],
            input_method=batch_row["input_method"],
            total=total,
            processed=processed,
            pending=pending,
            decisions=decisions,
            created_at=batch_row["created_at"],
            started_at=batch_row["started_at"],
            completed_at=batch_row["completed_at"],
            elapsed_seconds=round(elapsed, 2),
            estimated_remaining_seconds=eta,
            shipments=shipment_statuses,
        )

    def get_batch_results(
        self,
        batch_id: str,
        org_id: str,
    ) -> Optional[dict]:
        """Return full results for a batch including per-shipment payloads.

        Results are available for in-progress batches (partial) as well as
        completed ones.  Shipments are sorted by risk_score descending.

        Returns ``None`` if batch not found or wrong org.
        """
        status = self.get_batch_status(batch_id, org_id)
        if status is None:
            return None

        with self._engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT shipment_ref, status, decision, risk_score, risk_level,
                           n_findings, top_finding, analysis_id, result_json,
                           error_message, processed_at
                    FROM bulk_shipments
                    WHERE batch_id = :id
                    ORDER BY
                        CASE WHEN risk_score IS NULL THEN 1 ELSE 0 END ASC,
                        risk_score DESC,
                        shipment_ref ASC
                """),
                {"id": batch_id},
            ).mappings().fetchall()

        # Build summary stats
        total_score = 0.0
        score_count = 0
        highest_risk: Optional[dict] = None

        shipments: list[dict] = []
        for r in rows:
            rs: Optional[float] = r["risk_score"]
            if rs is not None:
                total_score += rs
                score_count += 1
                if highest_risk is None or rs > highest_risk["risk_score"]:
                    highest_risk = {
                        "ref": r["shipment_ref"],
                        "decision": r["decision"],
                        "risk_score": rs,
                        "analysis_id": r["analysis_id"],
                    }

            entry: dict[str, Any] = {
                "ref": r["shipment_ref"],
                "status": r["status"],
                "decision": r["decision"],
                "risk_score": rs,
                "risk_level": r["risk_level"],
                "n_findings": r["n_findings"],
                "top_finding": r["top_finding"],
                "analysis_id": r["analysis_id"],
                "error_message": r["error_message"],
                "processed_at": r["processed_at"],
            }
            if r["result_json"]:
                try:
                    entry["full_result"] = json.loads(r["result_json"])
                except json.JSONDecodeError:
                    pass
            shipments.append(entry)

        # Processing time
        elapsed = _elapsed_seconds(status.started_at, status.completed_at)

        return {
            "batch_id": batch_id,
            "status": status.status,
            "input_method": status.input_method,
            "created_at": status.created_at,
            "completed_at": status.completed_at,
            "summary": {
                "total": status.total,
                "processed": status.processed,
                "approved": status.decisions.get("APPROVE", 0),
                "review_recommended": status.decisions.get("REVIEW_RECOMMENDED", 0),
                "flagged": status.decisions.get("FLAG_FOR_INSPECTION", 0),
                "needs_info": status.decisions.get("REQUEST_MORE_INFORMATION", 0),
                "rejected": status.decisions.get("REJECT", 0),
                "errors": status.decisions.get("ERROR", 0),
                "avg_risk_score": (
                    round(total_score / score_count, 4) if score_count else None
                ),
                "highest_risk": highest_risk,
                "processing_time_seconds": round(elapsed, 2),
            },
            "shipments": shipments,
        }

    def get_export_rows(
        self,
        batch_id: str,
        org_id: str,
    ) -> Optional[list[dict]]:
        """Return flat row dicts for CSV export.

        Includes all shipments (COMPLETE and ERROR) sorted by risk_score desc.
        Returns ``None`` if batch not found or wrong org.
        """
        if not self._batch_belongs_to_org(batch_id, org_id):
            return None

        with self._engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT shipment_ref, status, decision, risk_score, risk_level,
                           n_findings, top_finding, analysis_id, error_message, processed_at
                    FROM bulk_shipments
                    WHERE batch_id = :id
                    ORDER BY
                        CASE WHEN risk_score IS NULL THEN 1 ELSE 0 END ASC,
                        risk_score DESC,
                        shipment_ref ASC
                """),
                {"id": batch_id},
            ).mappings().fetchall()

        return [dict(r) for r in rows]

    def get_shipment_payloads(
        self,
        batch_id: str,
        org_id: str,
    ) -> Optional[list[dict]]:
        """Return (ref, decision, risk_score, payload) for all COMPLETE shipments.

        Used by the ZIP-of-PDFs export endpoint to regenerate PDFs from stored
        result_json without re-running analysis.  Skips shipments with no
        result_json (ERROR rows or rows written before this feature existed).

        Returns ``None`` if batch not found or wrong org.
        """
        if not self._batch_belongs_to_org(batch_id, org_id):
            return None

        with self._engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT shipment_ref, decision, risk_score, result_json
                    FROM bulk_shipments
                    WHERE batch_id = :id
                      AND status = 'COMPLETE'
                      AND result_json IS NOT NULL
                    ORDER BY
                        CASE WHEN risk_score IS NULL THEN 1 ELSE 0 END ASC,
                        risk_score DESC
                """),
                {"id": batch_id},
            ).mappings().fetchall()

        result: list[dict] = []
        for r in rows:
            try:
                payload = json.loads(r["result_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            result.append({
                "ref": r["shipment_ref"],
                "decision": r["decision"],
                "risk_score": r["risk_score"],
                "payload": payload,
            })
        return result

    # ------------------------------------------------------------------
    # Private helpers — ownership check
    # ------------------------------------------------------------------

    def _batch_belongs_to_org(self, batch_id: str, org_id: str) -> bool:
        with self._engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT 1 FROM bulk_batches
                    WHERE batch_id = :id AND organization_id = :org
                """),
                {"id": batch_id, "org": org_id},
            ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Private helpers — DB writes
    # ------------------------------------------------------------------

    def _mark_batch_processing(self, batch_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE bulk_batches
                    SET status = 'PROCESSING', started_at = :now
                    WHERE batch_id = :id
                """),
                {"id": batch_id, "now": _utcnow()},
            )

    def _mark_batch_complete(self, batch_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE bulk_batches
                    SET status = 'COMPLETE', completed_at = :now
                    WHERE batch_id = :id
                """),
                {"id": batch_id, "now": _utcnow()},
            )

    def _mark_batch_failed(self, batch_id: str, error_message: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE bulk_batches
                    SET status = 'FAILED', completed_at = :now, error_message = :msg
                    WHERE batch_id = :id
                """),
                {"id": batch_id, "now": _utcnow(), "msg": error_message[:500]},
            )

    def _mark_shipment_processing(self, batch_id: str, ref: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE bulk_shipments
                    SET status = 'PROCESSING'
                    WHERE batch_id = :bid AND shipment_ref = :ref
                """),
                {"bid": batch_id, "ref": ref},
            )

    def _store_shipment_result(
        self,
        batch_id: str,
        ref: str,
        result: dict,
    ) -> None:
        """Persist a successful analysis result and update batch-level counters."""
        decision: str = result.get("decision", "APPROVE")
        risk_score: Optional[float] = result.get("risk_score")
        risk_level: Optional[str] = result.get("risk_level")
        explanations: list[str] = result.get("explanations", [])
        n_findings: int = len(explanations)
        top_finding: Optional[str] = (explanations[0][:200] + "...") \
            if explanations and len(explanations[0]) > 200 \
            else (explanations[0] if explanations else None)
        analysis_id: Optional[str] = result.get("shipment_id")
        now = _utcnow()

        try:
            result_json: Optional[str] = json.dumps(result)
        except Exception:
            result_json = None

        # Map decision to counter column name.
        decision_col: str = {
            "APPROVE":                  "approved_count",
            "REVIEW_RECOMMENDED":       "review_count",
            "FLAG_FOR_INSPECTION":      "flagged_count",
            "REQUEST_MORE_INFORMATION": "needs_info_count",
            "REJECT":                   "rejected_count",
        }.get(decision, "approved_count")

        with self._engine.begin() as conn:
            conn.execute(
                text(f"""
                    UPDATE bulk_shipments
                    SET status = 'COMPLETE',
                        decision = :decision,
                        risk_score = :risk_score,
                        risk_level = :risk_level,
                        n_findings = :n_findings,
                        top_finding = :top_finding,
                        analysis_id = :analysis_id,
                        result_json = :result_json,
                        processed_at = :now
                    WHERE batch_id = :bid AND shipment_ref = :ref
                """),
                {
                    "decision": decision,
                    "risk_score": risk_score,
                    "risk_level": risk_level,
                    "n_findings": n_findings,
                    "top_finding": top_finding,
                    "analysis_id": analysis_id,
                    "result_json": result_json,
                    "now": now,
                    "bid": batch_id,
                    "ref": ref,
                },
            )
            # Atomic counter increment — no read-modify-write race.
            conn.execute(
                text(f"""
                    UPDATE bulk_batches
                    SET processed_count = processed_count + 1,
                        {decision_col} = {decision_col} + 1
                    WHERE batch_id = :id
                """),
                {"id": batch_id},
            )

        logger.debug(
            "Bulk shipment COMPLETE: batch=%s ref=%s decision=%s score=%.3f",
            batch_id, ref, decision, risk_score or 0.0,
        )

    def _store_shipment_error(
        self,
        batch_id: str,
        ref: str,
        error_message: str,
    ) -> None:
        """Mark a shipment as errored and increment the batch error counter."""
        msg = (error_message or "Unknown error")[:500]
        now = _utcnow()

        with self._engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE bulk_shipments
                    SET status = 'ERROR', error_message = :msg, processed_at = :now
                    WHERE batch_id = :bid AND shipment_ref = :ref
                """),
                {"msg": msg, "now": now, "bid": batch_id, "ref": ref},
            )
            conn.execute(
                text("""
                    UPDATE bulk_batches
                    SET processed_count = processed_count + 1,
                        error_count     = error_count + 1
                    WHERE batch_id = :id
                """),
                {"id": batch_id},
            )

        logger.warning(
            "Bulk shipment ERROR: batch=%s ref=%s error=%s",
            batch_id, ref, msg,
        )
