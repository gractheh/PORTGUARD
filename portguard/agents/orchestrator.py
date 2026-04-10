"""OrchestratorAgent — runs the full PORTGUARD compliance screening pipeline."""

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from portguard.agents.parser import ParserAgent
from portguard.agents.classifier import ClassifierAgent
from portguard.agents.validator import ValidationAgent
from portguard.agents.risk import RiskAgent
from portguard.agents.decision import DecisionAgent
from portguard.models.shipment import ShipmentInput
from portguard.models.report import ScreeningReport

logger = logging.getLogger(__name__)

_ENGINE = "portguard-rule-based"
_ENGINE_WITH_PATTERNS = "portguard-rule-based+pattern-learning"

# Blend weights: rule engine gets 65%, pattern learning gets 35%.
# When history is insufficient (cold start or no PatternDB), rule score is
# used unchanged.
_RULE_WEIGHT = 0.65
_PATTERN_WEIGHT = 0.35


class OrchestratorAgent:
    """Runs the full PORTGUARD compliance screening pipeline.

    Pipeline stages (each is fault-tolerant):
    1. ParserAgent       — extract and normalize shipment data
    2. ClassifierAgent   — classify all line items under HTSUS
    3. ValidationAgent   — check ISF, PGA, marking, documentation
    4. RiskAgent         — Section 301, 232, AD/CVD, sanctions, UFLPA
    4.5 PatternEngine    — query pattern history, emit scored signals
                           (skipped when PatternDB is not provided or has
                           insufficient history for this entity)
    5. DecisionAgent     — synthesize final compliance decision

    Failures in any stage are captured as pipeline_errors and processing
    continues where possible.  If parsing fails, subsequent stages are
    skipped.  Pattern learning failures never abort the pipeline — the
    system falls back to rule-only scoring transparently.

    Parameters
    ----------
    db:
        Optional :class:`~portguard.pattern_db.PatternDB` instance.  When
        provided, the pipeline activates Stage 4.5 (pattern scoring) and
        records every analysis for future learning.  When None, the pipeline
        behaves identically to the original rule-only implementation.
    """

    def __init__(self, db=None) -> None:
        self.parser = ParserAgent()
        self.classifier = ClassifierAgent()
        self.validator = ValidationAgent()
        self.risk_assessor = RiskAgent()
        self.decision_maker = DecisionAgent()
        self._db = db
        self._pattern_engine = None
        if db is not None:
            try:
                from portguard.pattern_engine import PatternEngine
                self._pattern_engine = PatternEngine(db)
                logger.info("OrchestratorAgent: pattern learning enabled")
            except Exception as exc:
                logger.warning(
                    "OrchestratorAgent: could not initialise PatternEngine (%s) — "
                    "running rule-only", exc
                )

    async def screen(self, shipment_input: ShipmentInput) -> ScreeningReport:
        """Screen a shipment through the full compliance pipeline.

        When a PatternDB is configured, the effective risk score is:
            final = rule_score × 0.65 + pattern_score × 0.35

        When history is insufficient (cold start, or PatternDB not provided):
            final = rule_score × 1.0  (identical to pre-pattern behaviour)

        The analysis is always recorded to the PatternDB after the pipeline
        completes so that future screenings for the same entities benefit from
        accumulated history.  This write is best-effort — a DB failure does not
        affect the returned ScreeningReport.
        """
        start = time.monotonic()
        report_id = str(uuid.uuid4())
        errors: list[str] = []
        parsed = None
        classification = None
        validation = None
        risk = None
        decision = None

        # Stage 1: Parse
        try:
            parsed = await self.parser.parse(shipment_input)
        except Exception as e:
            errors.append(f"ParserAgent failed: {e}")

        # Stage 2: Classify (requires parsed)
        if parsed:
            try:
                classification = await self.classifier.classify(parsed)
            except Exception as e:
                errors.append(f"ClassifierAgent failed: {e}")

        # Stage 3: Validate (requires parsed + classification)
        if parsed and classification:
            try:
                validation = await self.validator.validate(parsed, classification)
            except Exception as e:
                errors.append(f"ValidationAgent failed: {e}")

        # Stage 4: Risk assessment (requires parsed + classification)
        if parsed and classification:
            try:
                risk = await self.risk_assessor.assess_risk(parsed, classification)
            except Exception as e:
                errors.append(f"RiskAgent failed: {e}")

        # Stage 4.5: Pattern scoring (best-effort; never aborts pipeline)
        pattern_result = None
        pattern_signals: list[str] = []
        pattern_score: Optional[float] = None
        pattern_history_depth: int = 0
        history_available: bool = False

        if self._pattern_engine is not None and parsed is not None:
            try:
                from portguard.pattern_engine import ScoringRequest
                # Build a ScoringRequest from the parsed shipment.
                # Use first line item's country_of_origin_iso2 as origin.
                origin_iso2: Optional[str] = None
                hs_codes: list[str] = []
                declared_value: Optional[float] = None
                quantity: Optional[float] = None

                if parsed.line_items:
                    first = parsed.line_items[0]
                    origin_iso2 = getattr(first, "country_of_origin_iso2", None)
                    for item in parsed.line_items:
                        hts = getattr(item, "hts_code", None)
                        if hts:
                            hs_codes.append(hts)
                    # Aggregate declared value and quantity across line items
                    total_val = sum(
                        getattr(i, "total_value_usd", 0.0) or 0.0
                        for i in parsed.line_items
                    )
                    total_qty = sum(
                        getattr(i, "quantity", 0.0) or 0.0
                        for i in parsed.line_items
                    )
                    declared_value = total_val if total_val > 0 else None
                    quantity = total_qty if total_qty > 0 else None

                req = ScoringRequest(
                    shipper_name=getattr(parsed, "exporter_name", None),
                    consignee_name=getattr(parsed, "importer_name", None),
                    origin_iso2=origin_iso2,
                    port_of_entry=getattr(parsed, "port_of_discharge", None),
                    hs_codes=hs_codes,
                    declared_value_usd=declared_value,
                    quantity=quantity,
                )

                pattern_result = self._pattern_engine.score(req)
                pattern_score = pattern_result.pattern_score
                pattern_history_depth = pattern_result.history_depth
                history_available = not pattern_result.is_cold_start
                pattern_signals = pattern_result.explanations

            except Exception as exc:
                errors.append(f"PatternEngine failed (non-fatal): {exc}")
                logger.warning("PatternEngine.score() failed: %s", exc, exc_info=True)

        # Stage 5: Decision (requires all prior stages)
        if parsed and classification and validation and risk:
            try:
                decision = await self.decision_maker.decide(
                    parsed, classification, validation, risk
                )
            except Exception as e:
                errors.append(f"DecisionAgent failed: {e}")

        # Score blending: apply pattern weight only when history is available.
        # The decision's confidence field is updated to reflect the blended view.
        if decision is not None and pattern_score is not None and history_available:
            blended = _RULE_WEIGHT * decision.confidence + _PATTERN_WEIGHT * pattern_score
            # Pydantic model is immutable; re-create with updated confidence
            decision = decision.model_copy(
                update={"confidence": round(min(1.0, blended), 4)}
            )

        elapsed_ms = (time.monotonic() - start) * 1000

        # Record the analysis to PatternDB (best-effort, synchronous but fast)
        shipment_id = report_id   # reuse report_id as shipment_id for traceability
        if self._db is not None and risk is not None:
            try:
                from portguard.pattern_db import ShipmentFingerprint
                rule_score = risk.overall_risk_score if hasattr(risk, "overall_risk_score") else 0.0
                rule_decision = decision.decision.value if decision else "APPROVE"
                rule_confidence = "HIGH" if (decision and decision.confidence >= 0.7) else "MEDIUM"
                rules_fired = [
                    {"type": f.risk_type.value, "severity": f.severity.value,
                     "score": getattr(f, "score", 0.0)}
                    for f in (risk.risk_factors if risk else [])
                ]
                fp = ShipmentFingerprint(
                    shipper_name=getattr(parsed, "exporter_name", None) if parsed else None,
                    consignee_name=getattr(parsed, "importer_name", None) if parsed else None,
                    origin_iso2=origin_iso2 if parsed else None,
                    port_of_entry=getattr(parsed, "port_of_discharge", None) if parsed else None,
                    hs_codes=hs_codes if parsed else [],
                    declared_value_usd=declared_value if parsed else None,
                    quantity=quantity if parsed else None,
                    rule_risk_score=rule_score,
                    rule_decision=rule_decision,
                    rule_confidence=rule_confidence,
                    pattern_score=pattern_score,
                    pattern_history_depth=pattern_history_depth,
                    pattern_cold_start=not history_available,
                    final_risk_score=decision.confidence if decision else rule_score,
                    final_decision=rule_decision,
                    final_confidence=rule_confidence,
                )
                # record_shipment is synchronous / fast; call directly
                recorded_id = self._db.record_shipment(
                    fp, rule_decision, rules_fired, rule_confidence
                )
                shipment_id = recorded_id
            except Exception as exc:
                logger.warning("PatternDB.record_shipment() failed (non-fatal): %s", exc)

        model_name = _ENGINE_WITH_PATTERNS if self._pattern_engine else _ENGINE

        return ScreeningReport(
            report_id=report_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            shipment_input=shipment_input,
            parsed_shipment=parsed,
            classification_result=classification,
            validation_result=validation,
            risk_assessment=risk,
            decision=decision,
            pipeline_errors=errors,
            processing_time_ms=round(elapsed_ms, 2),
            model_used=model_name,
            # Pattern learning fields
            shipment_id=shipment_id,
            pattern_score=pattern_score,
            pattern_effective_score=(
                pattern_result.effective_pattern_score if pattern_result else None
            ),
            history_available=history_available,
            pattern_signals=pattern_signals,
            pattern_history_depth=pattern_history_depth,
        )
