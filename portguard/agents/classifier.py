"""ClassifierAgent — HTSUS tariff classification for each line item."""

from portguard.agents.base import BaseAgent
from portguard.models.shipment import ParsedShipment
from portguard.models.classification import ClassificationResult


class ClassifierAgent(BaseAgent):
    AGENT_NAME = "ClassifierAgent"

    SYSTEM_PROMPT = """You are a licensed US Customs Broker and Certified Classification Specialist
with 25+ years of experience classifying merchandise under the Harmonized Tariff Schedule of the
United States (HTSUS). You apply the General Rules of Interpretation (GRI) rigorously and are
familiar with CBP binding ruling practice, the Explanatory Notes to the HS, and CROSS rulings.

Your classification methodology:
1. GRI 1: Classify by terms of the heading and any relative Section or Chapter Notes.
   This is the primary rule — most goods are classified under GRI 1.
2. GRI 2(a): Incomplete/unfinished articles — classify as complete if they have the essential
   character of the finished article.
3. GRI 2(b): Mixtures and combinations — if an article consists of more than one material,
   classify under GRI 3.
4. GRI 3(a): When two headings each refer to part of the materials/substances, the more specific
   description takes precedence.
5. GRI 3(b): Mixtures, sets, composite goods — classify by the component that gives essential
   character (usually predominant by weight, value, or bulk).
6. GRI 3(c): When GRI 3(a) and 3(b) cannot determine classification, use the heading that occurs
   last in numerical order.
7. GRI 4: Goods not classifiable under GRI 1-3 are classified under the heading most akin.
8. GRI 5: Cases, containers, and packing materials follow the article they contain.
9. GRI 6: Classification at subheading level follows same rules applied at heading level.

For each line item, provide:
- The complete 10-digit HTSUS subheading (format: XXXX.XX.XXXX)
- The official HTSUS subheading description
- The general (Column 1 General) duty rate
- The special duty rate (Column 1 Special) if applicable — note the relevant FTA (USMCA, AU, etc.)
- A full GRI analysis explaining which GRI rule applied and why
- A confidence score (0.0–1.0) reflecting certainty of classification
- Any classification notes or caveats

Important rules:
- Always use the current HTSUS schedule structure
- 10-digit codes must be valid HTSUS subheadings
- Duty rates must reflect current statutory rates (e.g., duty-free for electronics under ITA)
- For apparel: apply GRI 3(b) when chief weight determines classification (cotton vs. man-made fiber)
- For machinery: apply Chapter 84/85 Notes, particularly Note 5 to Chapter 84 for ADP machines
- For steel: be specific about form (flat-rolled, bars, wire rod, etc.) and alloy content"""

    _TOOL_SCHEMA = {
        "type": "object",
        "properties": {
            "classifications": {
                "type": "array",
                "description": "Classification for each line item in the shipment",
                "items": {
                    "type": "object",
                    "properties": {
                        "line_number": {
                            "type": "integer",
                            "description": "Line item number corresponding to ParsedShipment line items",
                        },
                        "hts_code": {
                            "type": "string",
                            "description": "10-digit HTSUS subheading in format XXXX.XX.XXXX",
                            "pattern": r"^\d{4}\.\d{2}\.\d{4}$",
                        },
                        "hts_description": {
                            "type": "string",
                            "description": "Official HTSUS subheading description",
                        },
                        "duty_rate_general": {
                            "type": "string",
                            "description": "Column 1 General duty rate (e.g. 'Free', '5.3%', '16.5%')",
                        },
                        "duty_rate_special": {
                            "type": ["string", "null"],
                            "description": "Column 1 Special duty rate with applicable FTA codes",
                        },
                        "gri_analysis": {
                            "type": "object",
                            "properties": {
                                "primary_gri": {
                                    "type": "string",
                                    "description": "Primary GRI rule applied (e.g. 'GRI 1')",
                                },
                                "secondary_gri": {
                                    "type": ["string", "null"],
                                    "description": "Secondary GRI rule if applicable",
                                },
                                "rationale": {
                                    "type": "string",
                                    "description": "Explanation of classification logic and applicable heading/notes",
                                },
                            },
                            "required": ["primary_gri", "rationale"],
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "description": "Classification confidence score",
                        },
                        "classification_notes": {
                            "type": ["string", "null"],
                            "description": "Additional notes, caveats, or alternative classifications to consider",
                        },
                    },
                    "required": [
                        "line_number", "hts_code", "hts_description",
                        "duty_rate_general", "gri_analysis", "confidence",
                    ],
                },
            },
            "classifier_notes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "General notes about the overall classification exercise",
            },
        },
        "required": ["classifications", "classifier_notes"],
    }

    def _build_prompt(self, parsed_shipment: ParsedShipment) -> str:
        lines = [
            "Classify the following imported goods under the HTSUS (Harmonized Tariff Schedule "
            "of the United States). Apply GRI 1-6 as appropriate.\n",
            f"Importer: {parsed_shipment.importer_name}",
            f"Exporter Country: {parsed_shipment.exporter_country or 'Unknown'}",
            f"Total Shipment Value: ${parsed_shipment.total_value_usd:,.2f} USD\n",
            "## LINE ITEMS TO CLASSIFY",
        ]

        for item in parsed_shipment.line_items:
            lines.append(f"\n### Line {item.line_number}")
            lines.append(f"Description: {item.description}")
            lines.append(f"Goods Category: {item.goods_category}")
            lines.append(f"Quantity: {item.quantity} {item.unit}")
            lines.append(f"Unit Value: ${item.unit_value_usd:,.2f} USD")
            lines.append(f"Country of Origin: {item.country_of_origin} ({item.country_of_origin_iso2})")
            if item.manufacturer:
                lines.append(f"Manufacturer: {item.manufacturer}")
            if item.hts_declared:
                lines.append(f"Declared HTS (verify/correct if needed): {item.hts_declared}")

        lines.append(
            "\nFor each line item, provide the correct 10-digit HTSUS classification, "
            "applicable duty rates, and complete GRI analysis."
        )
        return "\n".join(lines)

    async def classify(self, parsed_shipment: ParsedShipment) -> ClassificationResult:
        """Classify all line items in a ParsedShipment under the HTSUS.

        Returns a ClassificationResult with HTSLineClassification for each item.
        """
        prompt = self._build_prompt(parsed_shipment)
        result = await self._call_structured(
            user_prompt=prompt,
            tool_name="record_hts_classification",
            tool_description=(
                "Record the HTSUS classification results for all line items in the shipment, "
                "including 10-digit HTS codes, duty rates, and GRI analysis."
            ),
            output_schema=self._TOOL_SCHEMA,
        )
        return ClassificationResult(**result)
