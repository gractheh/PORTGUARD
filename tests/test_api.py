"""Integration tests for the PORTGUARD FastAPI routes."""

import json
import pytest
from unittest.mock import AsyncMock, patch

from portguard.api import routes as routes_module


# ---------------------------------------------------------------------------
# Helpers — JSON-serializable request bodies
# ---------------------------------------------------------------------------

_VALID_SHIPMENT_BODY = {
    "importer_name": "Pacific Rim Electronics Inc",
    "importer_country": "US",
    "exporter_name": "Ho Chi Minh Tech Co Ltd",
    "exporter_country": "Vietnam",
    "shipment_date": "2025-03-15",
    "port_of_entry": "Los Angeles",
    "incoterms": "FOB",
    "line_items": [
        {
            "description": "Laptop computers, 15-inch, Intel i7",
            "quantity": 5,
            "unit": "units",
            "unit_value": 320.0,
            "currency": "USD",
            "country_of_origin": "Vietnam",
            "hts_declared": "8471.30.0100",
        }
    ],
    "documents_present": ["commercial invoice", "bill of lading"],
}

_VALID_PARSED_BODY = {
    "importer_name": "Pacific Rim Electronics Inc",
    "importer_country": "US",
    "exporter_name": "Ho Chi Minh Tech Co Ltd",
    "exporter_country": "Vietnam",
    "exporter_country_iso2": "VN",
    "shipment_date": "2025-03-15",
    "port_of_entry": "Los Angeles",
    "incoterms": "FOB",
    "total_value_usd": 1600.0,
    "line_items": [
        {
            "line_number": 1,
            "description": "Laptop computers, 15-inch, Intel i7",
            "quantity": 5.0,
            "unit": "units",
            "unit_value_usd": 320.0,
            "total_value_usd": 1600.0,
            "country_of_origin": "Vietnam",
            "country_of_origin_iso2": "VN",
            "manufacturer": None,
            "hts_declared": "8471.30.0100",
            "goods_category": "electronics",
        }
    ],
    "parser_notes": [],
    "parsing_confidence": 0.95,
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_endpoint(async_client):
    """GET /api/v1/health should return 200 with status=ok."""
    response = await async_client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "model" in data
    assert data["model"] == "portguard-rule-based"


@pytest.mark.asyncio
async def test_screen_endpoint(async_client, sample_screening_report):
    """POST /api/v1/screen should return 200 with a report_id."""
    # Patch orchestrator at the module level where it's instantiated
    with patch.object(
        routes_module._orchestrator, "screen",
        new=AsyncMock(return_value=sample_screening_report)
    ):
        response = await async_client.post(
            "/api/v1/screen",
            json=_VALID_SHIPMENT_BODY,
        )

    assert response.status_code == 200
    data = response.json()
    assert "report_id" in data
    assert data["report_id"] == "test-report-id-12345"


@pytest.mark.asyncio
async def test_screen_missing_required_field(async_client):
    """POST /api/v1/screen with missing importer_name should return 422."""
    invalid_body = {k: v for k, v in _VALID_SHIPMENT_BODY.items() if k != "importer_name"}
    response = await async_client.post("/api/v1/screen", json=invalid_body)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_report_not_found(async_client):
    """GET /api/v1/reports/{id} with unknown id should return 404."""
    response = await async_client.get("/api/v1/reports/nonexistent-report-id-xyz")
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data


@pytest.mark.asyncio
async def test_get_report_found(async_client, sample_screening_report):
    """After a screen call stores a report, GET /api/v1/reports/{id} should return it."""
    # Directly inject into the reports store
    routes_module.reports_store["test-report-id-12345"] = sample_screening_report

    response = await async_client.get("/api/v1/reports/test-report-id-12345")
    assert response.status_code == 200
    data = response.json()
    assert data["report_id"] == "test-report-id-12345"
    assert data["shipment_input"]["importer_name"] == "Global Tech Imports LLC"

    # Cleanup
    del routes_module.reports_store["test-report-id-12345"]


@pytest.mark.asyncio
async def test_parse_endpoint(async_client, sample_parsed_shipment):
    """POST /api/v1/parse should return a ParsedShipment."""
    with patch.object(
        routes_module._parser, "parse",
        new=AsyncMock(return_value=sample_parsed_shipment)
    ):
        response = await async_client.post(
            "/api/v1/parse",
            json=_VALID_SHIPMENT_BODY,
        )

    assert response.status_code == 200
    data = response.json()
    assert "importer_name" in data
    assert "line_items" in data
    assert data["importer_name"] == "Global Tech Imports LLC"
    assert len(data["line_items"]) == 2


@pytest.mark.asyncio
async def test_classify_endpoint(async_client, sample_classification_result):
    """POST /api/v1/classify should accept a ParsedShipment and return ClassificationResult."""
    with patch.object(
        routes_module._classifier, "classify",
        new=AsyncMock(return_value=sample_classification_result)
    ):
        response = await async_client.post(
            "/api/v1/classify",
            json=_VALID_PARSED_BODY,
        )

    assert response.status_code == 200
    data = response.json()
    assert "classifications" in data
    assert len(data["classifications"]) >= 1
    assert "hts_code" in data["classifications"][0]
