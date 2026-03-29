"""Shipment input and parsed shipment models."""

from pydantic import BaseModel, Field


class LineItemInput(BaseModel):
    description: str
    quantity: float = Field(gt=0)
    unit: str = "units"
    unit_value: float = Field(gt=0)
    currency: str = "USD"
    country_of_origin: str
    manufacturer: str | None = None
    hts_declared: str | None = None


class ShipmentInput(BaseModel):
    raw_text: str | None = None
    importer_name: str
    importer_country: str = "US"
    exporter_name: str | None = None
    exporter_country: str | None = None
    shipment_date: str | None = None
    port_of_entry: str | None = None
    incoterms: str | None = None
    line_items: list[LineItemInput] = []
    documents_present: list[str] = []
    additional_context: str | None = None


class ParsedLineItem(BaseModel):
    line_number: int
    description: str
    quantity: float
    unit: str
    unit_value_usd: float
    total_value_usd: float
    country_of_origin: str
    country_of_origin_iso2: str
    manufacturer: str | None = None
    hts_declared: str | None = None
    goods_category: str


class ParsedShipment(BaseModel):
    importer_name: str
    importer_country: str
    exporter_name: str | None
    exporter_country: str | None
    exporter_country_iso2: str | None
    shipment_date: str | None
    port_of_entry: str | None
    incoterms: str | None
    total_value_usd: float
    line_items: list[ParsedLineItem]
    parser_notes: list[str] = []
    parsing_confidence: float
    additional_context: str | None = None
