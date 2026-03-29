"""Tests for ClassifierAgent."""

import re
import pytest
from unittest.mock import AsyncMock, patch

from portguard.agents.classifier import ClassifierAgent
from portguard.models.classification import ClassificationResult


_VALID_CLASSIFICATION_DICT = {
    "classifications": [
        {
            "line_number": 1,
            "hts_code": "8471.30.0100",
            "hts_description": (
                "Portable automatic data processing machines, weighing not more than 10 kg, "
                "consisting of at least a central processing unit, a keyboard and a display"
            ),
            "duty_rate_general": "Free",
            "duty_rate_special": "Free (AU, BH, CA, CL, CO, IL, JO, KR, MA, MX, OM, P, PA, PE, S, SG)",
            "gri_analysis": {
                "primary_gri": "GRI 1",
                "secondary_gri": None,
                "rationale": (
                    "Classified under 8471.30 per GRI 1. Chapter 84 Note 5 defines ADP machines. "
                    "Portable laptops with integrated display fall squarely in 8471.30."
                ),
            },
            "confidence": 0.98,
            "classification_notes": "ITA Agreement — duty-free.",
        },
        {
            "line_number": 2,
            "hts_code": "6109.10.0012",
            "hts_description": (
                "T-shirts, singlets, tank tops and similar garments, knitted or crocheted: "
                "of cotton: men's or boys': other"
            ),
            "duty_rate_general": "16.5%",
            "duty_rate_special": "Free (AU, BH, CA, CL, IL, JO, KR, MA, MX, OM, PA, PE, S, SG)",
            "gri_analysis": {
                "primary_gri": "GRI 1",
                "secondary_gri": None,
                "rationale": (
                    "Classified under 6109.10 per GRI 1. Cotton knit t-shirts are specifically "
                    "provided for under 6109.10 (cotton). GRI 3(b) chief weight analysis confirms "
                    "cotton at 100%."
                ),
            },
            "confidence": 0.97,
            "classification_notes": None,
        },
    ],
    "classifier_notes": ["Vietnam-origin laptops are duty-free under ITA Agreement."],
}


@pytest.mark.asyncio
async def test_classify_returns_classification_result(sample_parsed_shipment):
    """ClassifierAgent.classify() should return a ClassificationResult instance."""
    agent = ClassifierAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_VALID_CLASSIFICATION_DICT)):
        result = await agent.classify(sample_parsed_shipment)

    assert isinstance(result, ClassificationResult)
    assert len(result.classifications) == 2
    assert result.classifications[0].hts_code == "8471.30.0100"
    assert result.classifications[1].hts_code == "6109.10.0012"


@pytest.mark.asyncio
async def test_classify_hts_code_format(sample_parsed_shipment):
    """Each hts_code should match the XXXX.XX.XXXX format."""
    agent = ClassifierAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_VALID_CLASSIFICATION_DICT)):
        result = await agent.classify(sample_parsed_shipment)

    hts_pattern = re.compile(r"^\d{4}\.\d{2}\.\d{4}$")
    for cls in result.classifications:
        assert hts_pattern.match(cls.hts_code), (
            f"HTS code '{cls.hts_code}' for line {cls.line_number} "
            f"does not match expected XXXX.XX.XXXX format"
        )


@pytest.mark.asyncio
async def test_classify_confidence_range(sample_parsed_shipment):
    """Each classification confidence score should be between 0.0 and 1.0."""
    agent = ClassifierAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_VALID_CLASSIFICATION_DICT)):
        result = await agent.classify(sample_parsed_shipment)

    for cls in result.classifications:
        assert 0.0 <= cls.confidence <= 1.0, (
            f"Confidence {cls.confidence} for line {cls.line_number} "
            f"is outside [0.0, 1.0] range"
        )


@pytest.mark.asyncio
async def test_classify_all_line_items_classified(sample_parsed_shipment):
    """Number of classifications should match number of line items in the shipment."""
    agent = ClassifierAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_VALID_CLASSIFICATION_DICT)):
        result = await agent.classify(sample_parsed_shipment)

    assert len(result.classifications) == len(sample_parsed_shipment.line_items), (
        f"Expected {len(sample_parsed_shipment.line_items)} classifications, "
        f"got {len(result.classifications)}"
    )

    # Verify each line number is represented
    classified_lines = {cls.line_number for cls in result.classifications}
    expected_lines = {item.line_number for item in sample_parsed_shipment.line_items}
    assert classified_lines == expected_lines, (
        f"Missing classifications for lines: {expected_lines - classified_lines}"
    )
