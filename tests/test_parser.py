"""Tests for ParserAgent."""

import pytest
from unittest.mock import AsyncMock, patch

from portguard.agents.parser import ParserAgent
from portguard.models.shipment import ShipmentInput, ParsedShipment


# Reusable valid ParsedShipment dict for rule-based parser tests
_VALID_PARSED_DICT = {
    "importer_name": "Global Tech Imports LLC",
    "importer_country": "US",
    "exporter_name": "Asia Pacific Exports Co Ltd",
    "exporter_country": "Vietnam",
    "exporter_country_iso2": "VN",
    "shipment_date": "2025-03-15",
    "port_of_entry": "Los Angeles, CA",
    "incoterms": "FOB",
    "total_value_usd": 1700.0,
    "line_items": [
        {
            "line_number": 1,
            "description": "Laptop computers, 15-inch display, Intel Core i7",
            "quantity": 10.0,
            "unit": "units",
            "unit_value_usd": 120.0,
            "total_value_usd": 1200.0,
            "country_of_origin": "Vietnam",
            "country_of_origin_iso2": "VN",
            "manufacturer": "TechVN Manufacturing Ltd",
            "hts_declared": "8471.30.0100",
            "goods_category": "electronics",
        },
        {
            "line_number": 2,
            "description": "Men's cotton t-shirts, 100% cotton",
            "quantity": 500.0,
            "unit": "units",
            "unit_value_usd": 1.0,
            "total_value_usd": 500.0,
            "country_of_origin": "Bangladesh",
            "country_of_origin_iso2": "BD",
            "manufacturer": "Dhaka Garments Ltd",
            "hts_declared": "6109.10.0012",
            "goods_category": "textiles/apparel",
        },
    ],
    "parser_notes": ["All values in USD."],
    "parsing_confidence": 0.95,
}


@pytest.mark.asyncio
async def test_parse_returns_parsed_shipment(sample_shipment_input):
    """ParserAgent.parse() should return a ParsedShipment instance with correct fields."""
    agent = ParserAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_VALID_PARSED_DICT)):
        result = await agent.parse(sample_shipment_input)

    assert isinstance(result, ParsedShipment)
    assert result.importer_name == "Global Tech Imports LLC"
    assert result.exporter_name == "Asia Pacific Exports Co Ltd"
    assert result.total_value_usd == 1700.0
    assert len(result.line_items) == 2
    assert result.parsing_confidence == 0.90


@pytest.mark.asyncio
async def test_parse_iso2_resolution(sample_shipment_input):
    """Each line item should have country_of_origin_iso2 set."""
    agent = ParserAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_VALID_PARSED_DICT)):
        result = await agent.parse(sample_shipment_input)

    for item in result.line_items:
        assert item.country_of_origin_iso2, (
            f"Line {item.line_number} missing country_of_origin_iso2"
        )
        assert len(item.country_of_origin_iso2) == 2, (
            f"country_of_origin_iso2 should be 2-letter code, got: {item.country_of_origin_iso2}"
        )

    assert result.line_items[0].country_of_origin_iso2 == "VN"
    assert result.line_items[1].country_of_origin_iso2 == "BD"


@pytest.mark.asyncio
async def test_parse_total_value_computed(sample_shipment_input):
    """total_value_usd should equal the sum of all line item total_value_usd values."""
    agent = ParserAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=_VALID_PARSED_DICT)):
        result = await agent.parse(sample_shipment_input)

    line_total = sum(item.total_value_usd for item in result.line_items)
    assert abs(result.total_value_usd - line_total) < 0.01, (
        f"total_value_usd {result.total_value_usd} does not match sum of line items {line_total}"
    )


@pytest.mark.asyncio
async def test_parse_handles_raw_text():
    """ParserAgent should handle input with raw_text and no structured line_items."""
    raw_input = ShipmentInput(
        importer_name="Raw Text Importer Inc",
        raw_text=(
            "COMMERCIAL INVOICE\n"
            "Seller: Shanghai Goods Co, China\n"
            "Buyer: Raw Text Importer Inc, USA\n"
            "Item: Industrial pumps, qty 5, unit price USD 800\n"
            "Country of origin: China\n"
            "HTS: 8413.50\n"
            "Total: USD 4,000\n"
        ),
    )

    raw_text_parsed_dict = {
        "importer_name": "Raw Text Importer Inc",
        "importer_country": "US",
        "exporter_name": "Shanghai Goods Co",
        "exporter_country": "China",
        "exporter_country_iso2": "CN",
        "shipment_date": None,
        "port_of_entry": None,
        "incoterms": None,
        "total_value_usd": 4000.0,
        "line_items": [
            {
                "line_number": 1,
                "description": "Industrial pumps",
                "quantity": 5.0,
                "unit": "units",
                "unit_value_usd": 800.0,
                "total_value_usd": 4000.0,
                "country_of_origin": "China",
                "country_of_origin_iso2": "CN",
                "manufacturer": "Shanghai Goods Co",
                "hts_declared": "8413.50",
                "goods_category": "machinery",
            }
        ],
        "parser_notes": ["Extracted from raw commercial invoice text."],
        "parsing_confidence": 0.82,
    }

    agent = ParserAgent()
    with patch.object(agent, "_call_structured", new=AsyncMock(return_value=raw_text_parsed_dict)):
        result = await agent.parse(raw_input)

    assert isinstance(result, ParsedShipment)
    assert result.importer_name == "Raw Text Importer Inc"
    assert len(result.line_items) == 1
    assert result.line_items[0].country_of_origin_iso2 == "CN"
    assert result.line_items[0].total_value_usd == 4000.0
