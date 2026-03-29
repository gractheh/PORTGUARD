"""Shared pytest fixtures for PORTGUARD tests."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport

from portguard.api.main import app
from portguard.models.shipment import (
    LineItemInput,
    ShipmentInput,
    ParsedLineItem,
    ParsedShipment,
)
from portguard.models.classification import (
    GRIAnalysis,
    HTSLineClassification,
    ClassificationResult,
)
from portguard.models.validation import (
    FindingSeverity,
    ValidationFinding,
    ValidationResult,
)
from portguard.models.risk import (
    RiskAssessment,
    RiskFactor,
    RiskSeverity,
    RiskType,
)
from portguard.models.decision import (
    ComplianceDecision,
    DecisionLevel,
    RequiredAction,
)
from portguard.models.report import ScreeningReport


# ---------------------------------------------------------------------------
# Shipment fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_shipment_input() -> ShipmentInput:
    """ShipmentInput with 2 line items: laptops from Vietnam + cotton t-shirts from Bangladesh."""
    return ShipmentInput(
        importer_name="Global Tech Imports LLC",
        importer_country="US",
        exporter_name="Asia Pacific Exports Co Ltd",
        exporter_country="Vietnam",
        shipment_date="2025-03-15",
        port_of_entry="Los Angeles, CA",
        incoterms="FOB",
        line_items=[
            LineItemInput(
                description="Laptop computers, 15-inch display, Intel Core i7, 16GB RAM, 512GB SSD",
                quantity=10,
                unit="units",
                unit_value=120.0,
                currency="USD",
                country_of_origin="Vietnam",
                manufacturer="TechVN Manufacturing Ltd",
                hts_declared="8471.30.0100",
            ),
            LineItemInput(
                description="Men's cotton t-shirts, short sleeve, 100% cotton, various colors, sizes S-XL",
                quantity=500,
                unit="units",
                unit_value=1.0,
                currency="USD",
                country_of_origin="Bangladesh",
                manufacturer="Dhaka Garments Ltd",
                hts_declared="6109.10.0012",
            ),
        ],
        documents_present=["commercial invoice", "packing list", "bill of lading"],
    )


@pytest.fixture
def sample_parsed_shipment() -> ParsedShipment:
    """Hardcoded ParsedShipment corresponding to sample_shipment_input."""
    return ParsedShipment(
        importer_name="Global Tech Imports LLC",
        importer_country="US",
        exporter_name="Asia Pacific Exports Co Ltd",
        exporter_country="Vietnam",
        exporter_country_iso2="VN",
        shipment_date="2025-03-15",
        port_of_entry="Los Angeles, CA",
        incoterms="FOB",
        total_value_usd=1700.0,
        line_items=[
            ParsedLineItem(
                line_number=1,
                description="Laptop computers, 15-inch display, Intel Core i7, 16GB RAM, 512GB SSD",
                quantity=10,
                unit="units",
                unit_value_usd=120.0,
                total_value_usd=1200.0,
                country_of_origin="Vietnam",
                country_of_origin_iso2="VN",
                manufacturer="TechVN Manufacturing Ltd",
                hts_declared="8471.30.0100",
                goods_category="electronics",
            ),
            ParsedLineItem(
                line_number=2,
                description="Men's cotton t-shirts, short sleeve, 100% cotton, various colors",
                quantity=500,
                unit="units",
                unit_value_usd=1.0,
                total_value_usd=500.0,
                country_of_origin="Bangladesh",
                country_of_origin_iso2="BD",
                manufacturer="Dhaka Garments Ltd",
                hts_declared="6109.10.0012",
                goods_category="textiles/apparel",
            ),
        ],
        parser_notes=["All values in USD. Countries resolved to ISO-2."],
        parsing_confidence=0.95,
    )


@pytest.fixture
def sample_classification_result() -> ClassificationResult:
    """Hardcoded ClassificationResult for laptops (duty-free) and cotton t-shirts (16.5%)."""
    return ClassificationResult(
        classifications=[
            HTSLineClassification(
                line_number=1,
                hts_code="8471.30.0100",
                hts_description=(
                    "Automatic data processing machines; portable, weighing not more than 10 kg, "
                    "consisting of at least a central processing unit, a keyboard and a display"
                ),
                duty_rate_general="Free",
                duty_rate_special="Free (AU, BH, CA, CL, CO, D, E, IL, JO, KR, MA, MX, OM, P, PA, PE, S, SG)",
                gri_analysis=GRIAnalysis(
                    primary_gri="GRI 1",
                    secondary_gri=None,
                    rationale=(
                        "Classified under Heading 8471 per GRI 1 — Chapter 84 Note 5 defines "
                        "ADP machines; portable laptops with CPU, keyboard, and display are "
                        "specifically described in subheading 8471.30."
                    ),
                ),
                confidence=0.98,
                classification_notes="ITA Agreement provides duty-free treatment.",
            ),
            HTSLineClassification(
                line_number=2,
                hts_code="6109.10.0012",
                hts_description=(
                    "T-shirts, singlets, tank tops and similar garments, knitted or crocheted: "
                    "of cotton: men's or boys': other"
                ),
                duty_rate_general="16.5%",
                duty_rate_special="Free (AU, BH, CA, CL, CO, D, IL, JO, KR, MA, MX, OM, P, PA, PE, S, SG)",
                gri_analysis=GRIAnalysis(
                    primary_gri="GRI 1",
                    secondary_gri=None,
                    rationale=(
                        "Classified under Heading 6109 per GRI 1 — 100% cotton t-shirts are "
                        "specifically described as knitted cotton garments. Chief weight is "
                        "cotton, so 6109.10 (cotton) applies over 6109.90 (other fibers)."
                    ),
                ),
                confidence=0.97,
                classification_notes=None,
            ),
        ],
        classifier_notes=["Vietnam-origin laptops are duty-free under ITA Agreement."],
    )


@pytest.fixture
def sample_validation_result() -> ValidationResult:
    """Hardcoded ValidationResult — ISF complete, FTC textile requirement for t-shirts."""
    return ValidationResult(
        findings=[
            ValidationFinding(
                code="PGA-001",
                severity=FindingSeverity.INFO,
                field="line_items[2].hts_code",
                message=(
                    "FTC Textile Fiber Products Identification Act labeling required for "
                    "cotton t-shirts (HTS 6109.10.0012). Labels must disclose fiber content, "
                    "country of origin, manufacturer identity, and care instructions."
                ),
                regulatory_reference="15 USC 70 (Textile Fiber Products Identification Act); 16 CFR 303",
                remediation=(
                    "Ensure all garments are permanently labeled with: fiber content (100% Cotton), "
                    "country of origin (Bangladesh), and RN/WPL number. Verify before shipping."
                ),
            ),
        ],
        pga_requirements=[
            "FTC — Textile fiber products labeling",
            "CPSC — Flammability (children's sleepwear ch.61)",
        ],
        isf_complete=True,
        marking_compliant=True,
        validation_notes=["All ISF data elements present. Standard documentation adequate."],
    )


@pytest.fixture
def sample_risk_assessment() -> RiskAssessment:
    """Hardcoded RiskAssessment — no OFAC/Section 301 factors (VN and BD are clean)."""
    return RiskAssessment(
        risk_factors=[
            RiskFactor(
                risk_type=RiskType.VALUATION,
                severity=RiskSeverity.LOW,
                description=(
                    "Shipment total value $1,700.00 USD exceeds $2,500 formal entry "
                    "threshold. A formal customs entry (CBP Form 7501) is required."
                ),
                regulatory_reference="19 CFR 143.21; 19 USC 1484",
                recommended_action=(
                    "File formal customs entry (Type 01 Consumption Entry). "
                    "Ensure surety bond is in place."
                ),
            ),
        ],
        overall_risk_level=RiskSeverity.LOW,
        estimated_additional_duties_usd=None,
        risk_notes=[
            "Vietnam and Bangladesh are not subject to Section 301, OFAC sanctions, "
            "or active AD/CVD orders for these HTS codes."
        ],
    )


@pytest.fixture
def sample_decision() -> ComplianceDecision:
    """Hardcoded ComplianceDecision — CLEAR with minimal required actions."""
    return ComplianceDecision(
        decision=DecisionLevel.CLEAR,
        confidence=0.95,
        summary="CLEAR — no material compliance issues; standard processing may proceed.",
        key_findings=[
            "Laptops (HTS 8471.30.0100) from Vietnam are duty-free under the ITA Agreement.",
            "Cotton t-shirts (HTS 6109.10.0012) from Bangladesh carry 16.5% general duty.",
            "FTC textile labeling requirements apply to the t-shirts.",
            "ISF is complete with all 10 required data elements present.",
            "No Section 301, sanctions, or AD/CVD risks identified.",
        ],
        required_actions=[
            RequiredAction(
                priority=1,
                action="File formal customs entry (Type 01) — shipment value exceeds $2,500.",
                responsible_party="Customs Broker",
                deadline="Within 10 working days of cargo release",
                regulatory_reference="19 CFR 143.21",
            ),
            RequiredAction(
                priority=2,
                action="Verify FTC textile fiber content labels on all t-shirt units prior to import.",
                responsible_party="Importer",
                deadline="Before shipment departs origin",
                regulatory_reference="15 USC 70",
            ),
        ],
        estimated_base_duties_usd=82.50,
        estimated_additional_duties_usd=None,
        estimated_total_duties_usd=82.50,
        decision_rationale=(
            "Shipment qualifies for CLEAR status. Vietnam-origin laptops are duty-free under "
            "the ITA Agreement; Bangladesh-origin cotton t-shirts carry standard 16.5% duty "
            "with no additional measures. No OFAC sanctions, Section 301 tariffs, or AD/CVD "
            "orders apply. ISF is complete. FTC labeling is an INFO-level administrative "
            "requirement, not a blocking issue."
        ),
    )


@pytest.fixture
def sample_screening_report(
    sample_shipment_input,
    sample_parsed_shipment,
    sample_classification_result,
    sample_validation_result,
    sample_risk_assessment,
    sample_decision,
) -> ScreeningReport:
    """Full ScreeningReport using all sample fixtures."""
    return ScreeningReport(
        report_id="test-report-id-12345",
        created_at="2025-03-15T12:00:00+00:00",
        shipment_input=sample_shipment_input,
        parsed_shipment=sample_parsed_shipment,
        classification_result=sample_classification_result,
        validation_result=sample_validation_result,
        risk_assessment=sample_risk_assessment,
        decision=sample_decision,
        pipeline_errors=[],
        processing_time_ms=4250.75,
        model_used="claude-opus-4-6",
    )


# ---------------------------------------------------------------------------
# Mock Claude response factory
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_claude_response():
    """Factory fixture that returns a mock AsyncAnthropic response with a tool_use block."""
    def _make_response(tool_input: dict):
        tool_use_block = MagicMock()
        tool_use_block.type = "tool_use"
        tool_use_block.input = tool_input

        response = MagicMock()
        response.content = [tool_use_block]
        return response
    return _make_response


# ---------------------------------------------------------------------------
# Async HTTP test client
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def async_client():
    """AsyncClient for the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
