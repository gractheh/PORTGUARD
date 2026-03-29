"""Tests for RiskAgent — rule-based checks."""

import pytest
from unittest.mock import AsyncMock, patch

from portguard.agents.risk import RiskAgent
from portguard.models.shipment import ParsedShipment, ParsedLineItem
from portguard.models.classification import ClassificationResult, HTSLineClassification, GRIAnalysis
from portguard.models.risk import RiskAssessment, RiskType, RiskSeverity


# ---------------------------------------------------------------------------
# Helpers to build test fixtures
# ---------------------------------------------------------------------------

def _make_parsed_shipment(
    country_iso2: str,
    hts_declared: str = "8471.30.0100",
    total_value: float = 5000.0,
    description: str = "Industrial goods",
    manufacturer: str | None = None,
) -> ParsedShipment:
    return ParsedShipment(
        importer_name="Test Importer LLC",
        importer_country="US",
        exporter_name="Test Exporter Co",
        exporter_country="Test Country",
        exporter_country_iso2=country_iso2,
        shipment_date="2025-03-15",
        port_of_entry="New York",
        incoterms="FOB",
        total_value_usd=total_value,
        line_items=[
            ParsedLineItem(
                line_number=1,
                description=description,
                quantity=10.0,
                unit="units",
                unit_value_usd=total_value / 10,
                total_value_usd=total_value,
                country_of_origin="Test Country",
                country_of_origin_iso2=country_iso2,
                manufacturer=manufacturer,
                hts_declared=hts_declared,
                goods_category="machinery",
            )
        ],
        parser_notes=[],
        parsing_confidence=0.95,
    )


def _make_classification_result(line_number: int = 1, hts_code: str = "8471.30.0100") -> ClassificationResult:
    return ClassificationResult(
        classifications=[
            HTSLineClassification(
                line_number=line_number,
                hts_code=hts_code,
                hts_description=f"Test product — {hts_code}",
                duty_rate_general="Free",
                duty_rate_special=None,
                gri_analysis=GRIAnalysis(
                    primary_gri="GRI 1",
                    rationale="Test classification.",
                ),
                confidence=0.90,
            )
        ],
        classifier_notes=[],
    )


# Minimal stub response for risk tests (no additional risks)
_EMPTY_RISK_STUB = {
    "additional_risk_factors": [],
    "overall_risk_level": "LOW",
    "estimated_additional_duties_usd": None,
    "risk_notes": ["No additional risks identified by expert analysis."],
}

_CRITICAL_RISK_STUB = {
    "additional_risk_factors": [],
    "overall_risk_level": "CRITICAL",
    "estimated_additional_duties_usd": None,
    "risk_notes": ["Comprehensive sanctions apply."],
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_risk_no_factors_for_clean_shipment():
    """Vietnam-origin, non-sanctioned goods should have no OFAC or Section 301 factors."""
    shipment = _make_parsed_shipment(country_iso2="VN")
    classification = _make_classification_result(hts_code="8471.30.0100")

    agent = RiskAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_EMPTY_RISK_STUB)):
        result = await agent.assess_risk(shipment, classification)

    assert isinstance(result, RiskAssessment)

    ofac_factors = [f for f in result.risk_factors if f.risk_type == RiskType.OFAC_SANCTIONS]
    assert len(ofac_factors) == 0, "Vietnam should have no OFAC sanctions"

    section_301_factors = [f for f in result.risk_factors if f.risk_type == RiskType.SECTION_301]
    assert len(section_301_factors) == 0, "Vietnam-origin goods are not subject to Section 301"


@pytest.mark.asyncio
async def test_risk_section301_flagged_for_china():
    """China-origin goods with HTS 8471 (List 3/4A) should trigger SECTION_301 risk factor."""
    # HTS 8471 is in both List 3 (25%) and List 4A (7.5%)
    # List 3 takes precedence over List 4A → 25%
    shipment = _make_parsed_shipment(country_iso2="CN", hts_declared="8471.30.0100")
    classification = _make_classification_result(hts_code="8471.30.0100")

    agent = RiskAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_EMPTY_RISK_STUB)):
        result = await agent.assess_risk(shipment, classification)

    section_301_factors = [f for f in result.risk_factors if f.risk_type == RiskType.SECTION_301]
    assert len(section_301_factors) > 0, (
        "HTS 8471 from China should trigger Section 301 risk factor"
    )

    factor = section_301_factors[0]
    assert factor.severity == RiskSeverity.HIGH
    assert factor.additional_duty_rate is not None
    assert "%" in factor.additional_duty_rate
    # List 3 (25%) takes precedence
    assert "25%" in factor.additional_duty_rate or "7.5%" in factor.additional_duty_rate


@pytest.mark.asyncio
async def test_risk_ofac_comprehensive_sanctions():
    """Iran-origin (IR) goods should trigger CRITICAL OFAC_SANCTIONS risk factor."""
    shipment = _make_parsed_shipment(country_iso2="IR")
    classification = _make_classification_result(hts_code="5702.39.2000")

    agent = RiskAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_CRITICAL_RISK_STUB)):
        result = await agent.assess_risk(shipment, classification)

    ofac_factors = [f for f in result.risk_factors if f.risk_type == RiskType.OFAC_SANCTIONS]
    assert len(ofac_factors) > 0, "Iran-origin goods should trigger OFAC sanctions factor"

    critical_factors = [f for f in ofac_factors if f.severity == RiskSeverity.CRITICAL]
    assert len(critical_factors) > 0, (
        "Iran comprehensive sanctions should produce CRITICAL severity factor"
    )

    factor = critical_factors[0]
    assert "ITSR" in factor.description or "Iran" in factor.description or "31 CFR 560" in factor.regulatory_reference


@pytest.mark.asyncio
async def test_risk_adcvd_flagged():
    """China-origin HTS 7209 (cold-rolled steel) should match AD order A-570-029."""
    shipment = _make_parsed_shipment(
        country_iso2="CN",
        hts_declared="7209.16.0030",
        description="Cold-rolled steel flat products in coils",
        total_value=50000.0,
    )
    classification = _make_classification_result(hts_code="7209.16.0030")

    # Override goods category for this test
    shipment.line_items[0].goods_category = "steel/metals"

    agent = RiskAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_EMPTY_RISK_STUB)):
        result = await agent.assess_risk(shipment, classification)

    ad_factors = [f for f in result.risk_factors if f.risk_type == RiskType.ANTIDUMPING]
    assert len(ad_factors) > 0, (
        "China-origin HTS 7209 should match AD order A-570-029"
    )

    # Should reference case A-570-029
    case_numbers = [f.order_number for f in ad_factors if f.order_number]
    assert "A-570-029" in case_numbers, (
        f"Expected order A-570-029 in AD factors, got: {case_numbers}"
    )


@pytest.mark.asyncio
async def test_risk_section232_steel():
    """China-origin HTS 7208 (hot-rolled steel, chapter 72) should trigger Section 232."""
    shipment = _make_parsed_shipment(
        country_iso2="CN",
        hts_declared="7208.36.0030",
        description="Hot-rolled steel flat products",
        total_value=80000.0,
    )
    classification = _make_classification_result(hts_code="7208.36.0030")

    agent = RiskAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_EMPTY_RISK_STUB)):
        result = await agent.assess_risk(shipment, classification)

    section_232_factors = [f for f in result.risk_factors if f.risk_type == RiskType.SECTION_232]
    assert len(section_232_factors) > 0, (
        "HTS 7208 (chapter 72 steel) should trigger Section 232 risk factor"
    )

    factor = section_232_factors[0]
    assert factor.severity == RiskSeverity.HIGH
    assert factor.additional_duty_rate == "25%"
    assert "232" in factor.regulatory_reference or "232" in factor.description
