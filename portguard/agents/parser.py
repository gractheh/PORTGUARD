"""ParserAgent — extract and normalize shipment data from raw or structured input."""

import json
from portguard.agents.base import BaseAgent
from portguard.models.shipment import ShipmentInput, ParsedShipment


class ParserAgent(BaseAgent):
    AGENT_NAME = "ParserAgent"

    SYSTEM_PROMPT = """You are an expert US Customs and Border Protection (CBP) trade document parser
and data normalization specialist with 20+ years of experience processing commercial invoices,
bills of lading, packing lists, and entry documentation for US import transactions.

Your responsibilities:
1. EXTRACT all relevant shipment data from both structured fields and free-form raw text.
2. NORMALIZE monetary values to USD — apply reasonable exchange rates if non-USD currency
   is specified (use approximate current rates: EUR≈1.09, GBP≈1.27, CNY≈0.14, VND≈0.000040,
   JPY≈0.0067, KRW≈0.00074, INR≈0.012, BDT≈0.0091, MXN≈0.051, CAD≈0.74, AUD≈0.65).
3. RESOLVE country names and abbreviations to ISO 3166-1 alpha-2 codes. Examples:
   - "China", "PRC", "Mainland China" → "CN"
   - "Vietnam", "Viet Nam" → "VN"
   - "South Korea", "Korea" → "KR"
   - "Bangladesh" → "BD"
   - "Germany", "Deutschland" → "DE"
   - "Iran", "Islamic Republic of Iran" → "IR"
   - "Taiwan", "Chinese Taipei" → "TW"
   - "United States", "USA", "US" → "US"
   - "Mexico" → "MX"
   - "Cambodia" → "KH"
   - "Thailand" → "TH"
   - "Indonesia" → "ID"
   - "India" → "IN"
4. INFER goods_category from the product description. Use standard categories such as:
   "electronics", "machinery", "textiles/apparel", "steel/metals", "chemicals",
   "food/agriculture", "furniture/wood products", "pharmaceuticals", "automotive",
   "plastics", "rubber", "optical/medical instruments", "toys/games", "footwear",
   "paper/printing", "glass/ceramics", "other manufactured goods".
5. COMPUTE total_value_usd as the sum of all line item total_value_usd amounts.
6. ASSIGN parsing_confidence between 0.0 and 1.0 based on data completeness and clarity.
   Score 0.9+ if all critical fields are present and unambiguous.
   Score 0.7-0.89 if minor inferences were required.
   Score below 0.7 if significant assumptions were made.
7. ADD parser_notes explaining any assumptions, currency conversions, or inferences made.
8. Preserve declared HTS codes exactly as provided — do not alter or classify.

Be precise with country of origin — this is critical for duty and sanctions determination."""

    _TOOL_SCHEMA = {
        "type": "object",
        "properties": {
            "importer_name": {"type": "string", "description": "Full legal name of the US importer of record"},
            "importer_country": {"type": "string", "description": "ISO 3166-1 alpha-2 country code of importer (usually US)"},
            "exporter_name": {"type": ["string", "null"], "description": "Full legal name of the foreign exporter/shipper"},
            "exporter_country": {"type": ["string", "null"], "description": "Full country name of the exporter"},
            "exporter_country_iso2": {"type": ["string", "null"], "description": "ISO 3166-1 alpha-2 code of exporter country"},
            "shipment_date": {"type": ["string", "null"], "description": "Date of shipment in YYYY-MM-DD format"},
            "port_of_entry": {"type": ["string", "null"], "description": "US port of entry name or code"},
            "incoterms": {"type": ["string", "null"], "description": "Incoterms rule (e.g. FOB, CIF, EXW, DDP)"},
            "total_value_usd": {"type": "number", "description": "Total declared customs value in USD"},
            "line_items": {
                "type": "array",
                "description": "Parsed and normalized line items",
                "items": {
                    "type": "object",
                    "properties": {
                        "line_number": {"type": "integer", "description": "Sequential line item number starting at 1"},
                        "description": {"type": "string", "description": "Full product description"},
                        "quantity": {"type": "number", "description": "Quantity of goods"},
                        "unit": {"type": "string", "description": "Unit of measure (e.g. units, kg, meters)"},
                        "unit_value_usd": {"type": "number", "description": "Unit value converted to USD"},
                        "total_value_usd": {"type": "number", "description": "Total line value in USD (quantity × unit_value_usd)"},
                        "country_of_origin": {"type": "string", "description": "Full country name of origin"},
                        "country_of_origin_iso2": {"type": "string", "description": "ISO 3166-1 alpha-2 country of origin code"},
                        "manufacturer": {"type": ["string", "null"], "description": "Manufacturer name if known"},
                        "hts_declared": {"type": ["string", "null"], "description": "Declared HTSUS code if provided"},
                        "goods_category": {"type": "string", "description": "Inferred goods category"},
                    },
                    "required": [
                        "line_number", "description", "quantity", "unit",
                        "unit_value_usd", "total_value_usd", "country_of_origin",
                        "country_of_origin_iso2", "goods_category",
                    ],
                },
            },
            "parser_notes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Notes on parsing assumptions, currency conversions, or data quality issues",
            },
            "parsing_confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Confidence score for the overall parse quality",
            },
        },
        "required": [
            "importer_name", "importer_country", "exporter_name",
            "exporter_country", "exporter_country_iso2", "shipment_date",
            "port_of_entry", "incoterms", "total_value_usd", "line_items",
            "parser_notes", "parsing_confidence",
        ],
    }

    def _build_prompt(self, shipment_input: ShipmentInput) -> str:
        sections = [
            "Parse and normalize the following shipment information into structured data.\n",
        ]

        if shipment_input.raw_text:
            sections.append(f"## RAW DOCUMENT TEXT\n{shipment_input.raw_text}\n")

        sections.append("## STRUCTURED INPUT FIELDS")
        sections.append(f"Importer Name: {shipment_input.importer_name}")
        sections.append(f"Importer Country: {shipment_input.importer_country}")

        if shipment_input.exporter_name:
            sections.append(f"Exporter Name: {shipment_input.exporter_name}")
        if shipment_input.exporter_country:
            sections.append(f"Exporter Country: {shipment_input.exporter_country}")
        if shipment_input.shipment_date:
            sections.append(f"Shipment Date: {shipment_input.shipment_date}")
        if shipment_input.port_of_entry:
            sections.append(f"Port of Entry: {shipment_input.port_of_entry}")
        if shipment_input.incoterms:
            sections.append(f"Incoterms: {shipment_input.incoterms}")
        if shipment_input.documents_present:
            sections.append(f"Documents Present: {', '.join(shipment_input.documents_present)}")
        if shipment_input.additional_context:
            sections.append(f"Additional Context: {shipment_input.additional_context}")

        if shipment_input.line_items:
            sections.append("\n## LINE ITEMS")
            for i, item in enumerate(shipment_input.line_items, 1):
                sections.append(f"\nLine {i}:")
                sections.append(f"  Description: {item.description}")
                sections.append(f"  Quantity: {item.quantity} {item.unit}")
                sections.append(f"  Unit Value: {item.unit_value} {item.currency}")
                sections.append(f"  Country of Origin: {item.country_of_origin}")
                if item.manufacturer:
                    sections.append(f"  Manufacturer: {item.manufacturer}")
                if item.hts_declared:
                    sections.append(f"  Declared HTS: {item.hts_declared}")

        return "\n".join(sections)

    async def parse(self, shipment_input: ShipmentInput) -> ParsedShipment:
        """Parse and normalize a ShipmentInput into a ParsedShipment.

        Calls Claude to extract, normalize, and enrich all shipment data.
        """
        prompt = self._build_prompt(shipment_input)
        result = await self._call_structured(
            user_prompt=prompt,
            tool_name="record_parsed_shipment",
            tool_description=(
                "Record the fully parsed and normalized shipment data extracted "
                "from the provided commercial document or structured input."
            ),
            output_schema=self._TOOL_SCHEMA,
        )
        return ParsedShipment(**result)
