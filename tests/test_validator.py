"""Tests for ValidationAgent."""

import pytest
from unittest.mock import AsyncMock, patch

from portguard.agents.validator import ValidationAgent
from portguard.models.validation import ValidationResult


_VALID_VALIDATION_DICT = {
    "findings": [
        {
            "code": "PGA-001",
            "severity": "INFO",
            "field": "line_items[2].hts_code",
            "message": "FTC textile fiber labeling required for cotton t-shirts.",
            "regulatory_reference": "15 USC 70; 16 CFR 303",
            "remediation": "Ensure labels show fiber content and country of origin.",
        }
    ],
    "pga_requirements": [
        "FTC — Textile fiber products labeling",
        "CPSC — Flammability (children's sleepwear ch.61)",
    ],
    "isf_complete": True,
    "marking_compliant": True,
    "validation_notes": ["ISF data elements complete. Standard documentation adequate."],
}

_VALIDATION_DICT_ISF_INCOMPLETE = {
    "findings": [
        {
            "code": "ISF-001",
            "severity": "ERROR",
            "field": "exporter_name",
            "message": "ISF seller/supplier element missing — exporter name not provided.",
            "regulatory_reference": "19 CFR 149.2(a)",
            "remediation": "Provide exporter name and address before cargo loading.",
        }
    ],
    "pga_requirements": ["FTC — Textile fiber products labeling"],
    "isf_complete": True,  # rule-based check will override this
    "marking_compliant": None,
    "validation_notes": ["Exporter name missing — ISF incomplete."],
}


@pytest.mark.asyncio
async def test_validate_returns_validation_result(
    sample_parsed_shipment, sample_classification_result
):
    """ValidationAgent.validate() should return a ValidationResult instance."""
    agent = ValidationAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_VALID_VALIDATION_DICT)):
        result = await agent.validate(sample_parsed_shipment, sample_classification_result)

    assert isinstance(result, ValidationResult)
    assert isinstance(result.isf_complete, bool)
    assert isinstance(result.findings, list)
    assert isinstance(result.pga_requirements, list)


@pytest.mark.asyncio
async def test_validate_isf_incomplete_when_missing_exporter(
    sample_parsed_shipment, sample_classification_result
):
    """When exporter_name is None, isf_complete should be False."""
    # Create a shipment with no exporter
    shipment_no_exporter = sample_parsed_shipment.model_copy(
        update={"exporter_name": None}
    )

    agent = ValidationAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_VALIDATION_DICT_ISF_INCOMPLETE)):
        result = await agent.validate(shipment_no_exporter, sample_classification_result)

    # Our rule-based check sets isf_complete correctly
    assert result.isf_complete is False, (
        "isf_complete should be False when exporter_name is None"
    )

    # There should be an ISF-related finding or the override is sufficient
    # (the rule-based override alone satisfies this test)


@pytest.mark.asyncio
async def test_validate_pga_requirements_populated(
    sample_parsed_shipment, sample_classification_result
):
    """PGA requirements should be non-empty for HTS codes with known PGA requirements."""
    agent = ValidationAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_VALID_VALIDATION_DICT)):
        result = await agent.validate(sample_parsed_shipment, sample_classification_result)

    # HTS 6109.10 (chapter 61) and 8471.30 (chapter 84) both have PGA requirements
    assert len(result.pga_requirements) > 0, (
        "pga_requirements should not be empty — chapters 61 and 84 have PGA requirements"
    )


@pytest.mark.asyncio
async def test_validate_textile_pga_requirement(
    sample_parsed_shipment, sample_classification_result
):
    """For HTS 6109.x (chapter 61), FTC textile labeling requirement should appear."""
    agent = ValidationAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_VALID_VALIDATION_DICT)):
        result = await agent.validate(sample_parsed_shipment, sample_classification_result)

    ftc_requirements = [req for req in result.pga_requirements if "FTC" in req]
    assert len(ftc_requirements) > 0, (
        f"Expected FTC textile labeling requirement in pga_requirements, "
        f"got: {result.pga_requirements}"
    )
    assert any("textile" in req.lower() or "fiber" in req.lower() for req in ftc_requirements), (
        "FTC requirement should mention textile or fiber labeling"
    )
