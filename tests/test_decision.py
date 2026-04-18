"""Tests for DecisionAgent."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from portguard.agents.decision import DecisionAgent
from portguard.models.risk import RiskAssessment, RiskFactor, RiskSeverity, RiskType
from portguard.models.decision import ComplianceDecision, DecisionLevel


# ---------------------------------------------------------------------------
# Helpers for building test RiskAssessments
# ---------------------------------------------------------------------------

def _make_risk_assessment(
    risk_factors: list[RiskFactor] | None = None,
    overall_risk_level: RiskSeverity = RiskSeverity.LOW,
) -> RiskAssessment:
    return RiskAssessment(
        risk_factors=risk_factors or [],
        overall_risk_level=overall_risk_level,
        estimated_additional_duties_usd=None,
        risk_notes=[],
    )


def _ofac_critical_factor() -> RiskFactor:
    return RiskFactor(
        risk_type=RiskType.OFAC_SANCTIONS,
        severity=RiskSeverity.CRITICAL,
        country="IR",
        description=(
            "COMPREHENSIVE OFAC sanctions apply to Iran (IR) under ITSR (31 CFR 560). "
            "Full trade embargo — all transactions prohibited."
        ),
        regulatory_reference="OFAC ITSR; 31 CFR 560",
        recommended_action="STOP TRANSACTION. Contact OFAC.",
    )


def _section301_high_factor() -> RiskFactor:
    return RiskFactor(
        risk_type=RiskType.SECTION_301,
        severity=RiskSeverity.HIGH,
        hts_code="8457.10.0000",
        country="CN",
        description="Section 301 List 1 additional duty of 25% applies to HTS 8457.",
        additional_duty_rate="25%",
        regulatory_reference="USTR Section 301 List 1; effective 2018-07-06",
        recommended_action="Deposit 25% additional duty.",
    )


def _transshipment_medium_factor() -> RiskFactor:
    return RiskFactor(
        risk_type=RiskType.OTHER,
        severity=RiskSeverity.MEDIUM,
        country="VN",
        description="Potential transshipment risk — goods may have originated in China.",
        regulatory_reference="19 USC 1304; CBP transshipment enforcement",
        recommended_action="Obtain country of origin certification from manufacturer.",
    )


# ---------------------------------------------------------------------------
# Decision response templates
# ---------------------------------------------------------------------------

def _make_decision_response(decision: str, confidence: float = 0.9) -> dict:
    return {
        "decision": decision,
        "confidence": confidence,
        "summary": f"{decision} — test decision.",
        "key_findings": [f"Test finding for {decision} decision."],
        "required_actions": [
            {
                "priority": 1,
                "action": "Complete customs entry filing.",
                "responsible_party": "Customs Broker",
                "deadline": "10 business days",
                "regulatory_reference": "19 CFR 143.21",
            }
        ],
        "estimated_base_duties_usd": 100.0,
        "estimated_additional_duties_usd": None,
        "estimated_total_duties_usd": 100.0,
        "decision_rationale": f"Rationale for {decision} decision in test.",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_decision_clear_for_no_risk(
    sample_parsed_shipment,
    sample_classification_result,
    sample_validation_result,
):
    """CLEAR decision should be returned when no significant risk factors exist."""
    risk = _make_risk_assessment(risk_factors=[], overall_risk_level=RiskSeverity.LOW)

    agent = DecisionAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_make_decision_response("CLEAR"))):
        result = await agent.decide(
            sample_parsed_shipment,
            sample_classification_result,
            sample_validation_result,
            risk,
        )

    assert isinstance(result, ComplianceDecision)
    assert result.decision == DecisionLevel.CLEAR


@pytest.mark.asyncio
async def test_decision_reject_for_comprehensive_sanctions(
    sample_parsed_shipment,
    sample_classification_result,
    sample_validation_result,
):
    """REJECT decision should be returned when CRITICAL OFAC sanctions are present."""
    risk = _make_risk_assessment(
        risk_factors=[_ofac_critical_factor()],
        overall_risk_level=RiskSeverity.CRITICAL,
    )

    agent = DecisionAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_make_decision_response("REJECT", 0.99))):
        result = await agent.decide(
            sample_parsed_shipment,
            sample_classification_result,
            sample_validation_result,
            risk,
        )

    assert result.decision == DecisionLevel.REJECT


@pytest.mark.asyncio
async def test_decision_hold_for_high_risk(
    sample_parsed_shipment,
    sample_classification_result,
    sample_validation_result,
):
    """HOLD decision should be returned for HIGH risk factors (e.g., Section 301)."""
    risk = _make_risk_assessment(
        risk_factors=[_section301_high_factor()],
        overall_risk_level=RiskSeverity.HIGH,
    )

    agent = DecisionAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_make_decision_response("HOLD", 0.92))):
        result = await agent.decide(
            sample_parsed_shipment,
            sample_classification_result,
            sample_validation_result,
            risk,
        )

    assert result.decision == DecisionLevel.HOLD


@pytest.mark.asyncio
async def test_decision_review_for_medium_risk(
    sample_parsed_shipment,
    sample_classification_result,
    sample_validation_result,
):
    """REVIEW decision should be returned for MEDIUM risk factors."""
    risk = _make_risk_assessment(
        risk_factors=[_transshipment_medium_factor()],
        overall_risk_level=RiskSeverity.MEDIUM,
    )

    agent = DecisionAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_make_decision_response("REVIEW", 0.85))):
        result = await agent.decide(
            sample_parsed_shipment,
            sample_classification_result,
            sample_validation_result,
            risk,
        )

    assert result.decision == DecisionLevel.REVIEW


@pytest.mark.asyncio
async def test_decision_required_actions_populated(
    sample_parsed_shipment,
    sample_classification_result,
    sample_validation_result,
):
    """Required actions should be non-empty when decision is not CLEAR."""
    risk = _make_risk_assessment(
        risk_factors=[_section301_high_factor()],
        overall_risk_level=RiskSeverity.HIGH,
    )
    agent_response = _make_decision_response("HOLD", 0.92)
    # Ensure required_actions is populated
    agent_response["required_actions"] = [
        {
            "priority": 1,
            "action": "Deposit Section 301 additional duty of 25% before cargo release.",
            "responsible_party": "Importer",
            "deadline": "Before cargo release",
            "regulatory_reference": "USTR Section 301; 19 CFR 141",
        },
        {
            "priority": 2,
            "action": "File CF-7501 entry summary with CBP.",
            "responsible_party": "Customs Broker",
            "deadline": "Within 10 business days of release",
            "regulatory_reference": "19 CFR 143.21",
        },
    ]

    agent = DecisionAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=agent_response)):
        result = await agent.decide(
            sample_parsed_shipment,
            sample_classification_result,
            sample_validation_result,
            risk,
        )

    assert len(result.required_actions) > 0, (
        "required_actions should not be empty for a HOLD decision"
    )
    assert result.required_actions[0].priority == 1
    assert result.required_actions[0].responsible_party in ("Importer", "Customs Broker", "Trade Counsel", "CBP", "OFAC")
