"""OrchestratorAgent — runs the full PORTGUARD compliance screening pipeline."""

import time
import uuid
from datetime import datetime, timezone

from portguard.agents.parser import ParserAgent
from portguard.agents.classifier import ClassifierAgent
from portguard.agents.validator import ValidationAgent
from portguard.agents.risk import RiskAgent
from portguard.agents.decision import DecisionAgent
from portguard.models.shipment import ShipmentInput
from portguard.models.report import ScreeningReport

_ENGINE = "portguard-rule-based"


class OrchestratorAgent:
    """Runs the full 5-stage PORTGUARD compliance screening pipeline.

    Pipeline stages (each is fault-tolerant):
    1. ParserAgent — extract and normalize shipment data
    2. ClassifierAgent — classify all line items under HTSUS
    3. ValidationAgent — check ISF, PGA, marking, documentation
    4. RiskAgent — Section 301, 232, AD/CVD, sanctions, UFLPA
    5. DecisionAgent — synthesize final compliance decision

    Failures in any stage are captured as pipeline_errors and processing continues
    where possible. If parsing fails, subsequent stages are skipped.
    """

    def __init__(self) -> None:
        self.parser = ParserAgent()
        self.classifier = ClassifierAgent()
        self.validator = ValidationAgent()
        self.risk_assessor = RiskAgent()
        self.decision_maker = DecisionAgent()

    async def screen(self, shipment_input: ShipmentInput) -> ScreeningReport:
        """Screen a shipment through the full compliance pipeline."""
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

        # Stage 5: Decision (requires all prior stages)
        if parsed and classification and validation and risk:
            try:
                decision = await self.decision_maker.decide(
                    parsed, classification, validation, risk
                )
            except Exception as e:
                errors.append(f"DecisionAgent failed: {e}")

        elapsed_ms = (time.monotonic() - start) * 1000

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
            model_used=_ENGINE,
        )
