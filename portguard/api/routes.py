"""PORTGUARD API route handlers."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from portguard.agents.orchestrator import OrchestratorAgent
from portguard.agents.parser import ParserAgent
from portguard.agents.classifier import ClassifierAgent
from portguard.agents.risk import RiskAgent
from portguard.models.shipment import ShipmentInput, ParsedShipment
from portguard.models.classification import ClassificationResult
from portguard.models.risk import RiskAssessment
from portguard.models.report import ScreeningReport

router = APIRouter()

# ---------------------------------------------------------------------------
# Singleton agent instances (initialized once at module load)
# ---------------------------------------------------------------------------
_orchestrator = OrchestratorAgent()
_parser = ParserAgent()
_classifier = ClassifierAgent()
_risk_agent = RiskAgent()

# ---------------------------------------------------------------------------
# In-memory report store
# ---------------------------------------------------------------------------
reports_store: dict[str, ScreeningReport] = {}


# ---------------------------------------------------------------------------
# Request/response helpers
# ---------------------------------------------------------------------------

class AssessRiskRequest(BaseModel):
    """Request body for the /assess-risk endpoint."""
    parsed: ParsedShipment
    classification: ClassificationResult


class HealthResponse(BaseModel):
    status: str
    model: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check() -> HealthResponse:
    """Return API health status and configured model."""
    return HealthResponse(status="ok", model="portguard-rule-based")


@router.post("/screen", response_model=ScreeningReport, tags=["Screening"])
async def screen_shipment(shipment_input: ShipmentInput) -> ScreeningReport:
    """Run the full 5-stage compliance screening pipeline on a shipment.

    Stores the resulting report and returns it. Use GET /reports/{report_id}
    to retrieve a stored report later.
    """
    try:
        report = await _orchestrator.screen(shipment_input)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Screening pipeline error: {e}")

    reports_store[report.report_id] = report
    return report


@router.post("/parse", response_model=ParsedShipment, tags=["Agents"])
async def parse_shipment(shipment_input: ShipmentInput) -> ParsedShipment:
    """Run ParserAgent only — extract and normalize shipment data."""
    try:
        return await _parser.parse(shipment_input)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parser error: {e}")


@router.post("/classify", response_model=ClassificationResult, tags=["Agents"])
async def classify_shipment(parsed_shipment: ParsedShipment) -> ClassificationResult:
    """Run ClassifierAgent only — classify a ParsedShipment under HTSUS."""
    try:
        return await _classifier.classify(parsed_shipment)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Classifier error: {e}")


@router.post("/assess-risk", response_model=RiskAssessment, tags=["Agents"])
async def assess_risk(request: AssessRiskRequest) -> RiskAssessment:
    """Run RiskAgent only — assess trade compliance risk for a classified shipment."""
    try:
        return await _risk_agent.assess_risk(request.parsed, request.classification)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Risk assessment error: {e}")


@router.get("/reports/{report_id}", response_model=ScreeningReport, tags=["Reports"])
async def get_report(report_id: str) -> ScreeningReport:
    """Retrieve a previously generated screening report by ID."""
    report = reports_store.get(report_id)
    if not report:
        raise HTTPException(
            status_code=404,
            detail=f"Report '{report_id}' not found. Reports are stored in memory and lost on restart.",
        )
    return report
