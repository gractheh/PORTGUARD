"""Full screening report model."""

from pydantic import BaseModel
from portguard.models.shipment import ShipmentInput, ParsedShipment
from portguard.models.classification import ClassificationResult
from portguard.models.validation import ValidationResult
from portguard.models.risk import RiskAssessment
from portguard.models.decision import ComplianceDecision


class ScreeningReport(BaseModel):
    report_id: str
    created_at: str
    shipment_input: ShipmentInput
    parsed_shipment: ParsedShipment | None = None
    classification_result: ClassificationResult | None = None
    validation_result: ValidationResult | None = None
    risk_assessment: RiskAssessment | None = None
    decision: ComplianceDecision | None = None
    pipeline_errors: list[str] = []
    processing_time_ms: float
    model_used: str
