"""Tests for OrchestratorAgent."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from portguard.agents.orchestrator import OrchestratorAgent
from portguard.models.report import ScreeningReport


@pytest.mark.asyncio
async def test_screen_returns_screening_report(
    sample_shipment_input,
    sample_parsed_shipment,
    sample_classification_result,
    sample_validation_result,
    sample_risk_assessment,
    sample_decision,
):
    """OrchestratorAgent.screen() should return a ScreeningReport with report_id and created_at."""
    orchestrator = OrchestratorAgent()

    orchestrator.parser.parse = AsyncMock(return_value=sample_parsed_shipment)
    orchestrator.classifier.classify = AsyncMock(return_value=sample_classification_result)
    orchestrator.validator.validate = AsyncMock(return_value=sample_validation_result)
    orchestrator.risk_assessor.assess_risk = AsyncMock(return_value=sample_risk_assessment)
    orchestrator.decision_maker.decide = AsyncMock(return_value=sample_decision)

    result = await orchestrator.screen(sample_shipment_input)

    assert isinstance(result, ScreeningReport)
    assert result.report_id is not None
    assert len(result.report_id) > 0
    assert result.created_at is not None
    assert len(result.created_at) > 0
    assert result.parsed_shipment == sample_parsed_shipment
    assert result.classification_result == sample_classification_result
    assert result.validation_result == sample_validation_result
    assert result.risk_assessment == sample_risk_assessment
    assert result.decision == sample_decision
    assert result.pipeline_errors == []


@pytest.mark.asyncio
async def test_screen_pipeline_errors_on_parser_failure(sample_shipment_input):
    """If ParserAgent fails, pipeline_errors should contain the error and decision should be None."""
    orchestrator = OrchestratorAgent()
    orchestrator.parser.parse = AsyncMock(side_effect=RuntimeError("Claude API timeout"))

    result = await orchestrator.screen(sample_shipment_input)

    assert isinstance(result, ScreeningReport)
    assert len(result.pipeline_errors) > 0
    assert any("ParserAgent" in err for err in result.pipeline_errors)
    assert "Claude API timeout" in result.pipeline_errors[0]
    assert result.parsed_shipment is None
    assert result.decision is None


@pytest.mark.asyncio
async def test_screen_partial_results_on_classifier_failure(
    sample_shipment_input,
    sample_parsed_shipment,
):
    """If ClassifierAgent fails after parsing succeeds, parsed_shipment should be set but classification_result None."""
    orchestrator = OrchestratorAgent()
    orchestrator.parser.parse = AsyncMock(return_value=sample_parsed_shipment)
    orchestrator.classifier.classify = AsyncMock(side_effect=ValueError("Invalid HTS structure"))

    result = await orchestrator.screen(sample_shipment_input)

    assert isinstance(result, ScreeningReport)
    # Parser succeeded
    assert result.parsed_shipment == sample_parsed_shipment
    # Classifier failed
    assert result.classification_result is None
    # Downstream stages skipped
    assert result.validation_result is None
    assert result.risk_assessment is None
    assert result.decision is None
    # Error recorded
    assert any("ClassifierAgent" in err for err in result.pipeline_errors)


@pytest.mark.asyncio
async def test_screen_processing_time_populated(
    sample_shipment_input,
    sample_parsed_shipment,
    sample_classification_result,
    sample_validation_result,
    sample_risk_assessment,
    sample_decision,
):
    """processing_time_ms should be greater than 0 after a successful screen."""
    orchestrator = OrchestratorAgent()
    orchestrator.parser.parse = AsyncMock(return_value=sample_parsed_shipment)
    orchestrator.classifier.classify = AsyncMock(return_value=sample_classification_result)
    orchestrator.validator.validate = AsyncMock(return_value=sample_validation_result)
    orchestrator.risk_assessor.assess_risk = AsyncMock(return_value=sample_risk_assessment)
    orchestrator.decision_maker.decide = AsyncMock(return_value=sample_decision)

    result = await orchestrator.screen(sample_shipment_input)

    assert result.processing_time_ms > 0, (
        f"processing_time_ms should be positive, got: {result.processing_time_ms}"
    )
    assert result.model_used == "claude-opus-4-6"
