"""ValidationAgent — check ISF, PGA requirements, marking, and documentation compliance."""

from portguard.agents.base import BaseAgent
from portguard.data.pga import get_pga_requirements
from portguard.models.shipment import ParsedShipment
from portguard.models.classification import ClassificationResult
from portguard.models.validation import ValidationResult


# ISF required data elements per 19 CFR 149.2
_ISF_REQUIRED_FIELDS = [
    "seller",
    "buyer",
    "importer_of_record",
    "consignee",
    "manufacturer_supplier",
    "ship_to_party",
    "country_of_origin",
    "hts_code",
]


class ValidationAgent(BaseAgent):
    AGENT_NAME = "ValidationAgent"

    SYSTEM_PROMPT = """You are a US import compliance specialist and Customs Broker with deep expertise
in CBP regulations, 19 CFR, and multi-agency import requirements. Your role is to identify compliance
gaps, documentation deficiencies, and regulatory violations in import shipments.

Your validation areas:

## 1. Importer Security Filing (ISF) — 19 CFR 149
ISF (10+2) is mandatory for ocean cargo. The 10 importer-provided data elements are:
- Seller (name and address)
- Buyer (name and address)
- Importer of Record number
- Consignee number
- Manufacturer/Supplier (name and address)
- Ship-to party (name and address)
- Country of origin
- Commodity HTS-6 number
- Container stuffing location
- Consolidator (stuffer)
Mark isf_complete=false if importer name, exporter/seller name, country of origin, or HTS code is missing.

## 2. Partner Government Agency (PGA) Requirements
Various agencies regulate specific commodities:
- FDA: Food, drugs, medical devices, cosmetics (Prior Notice for food)
- USDA APHIS: Plants, animals, agricultural products (phytosanitary certificates)
- USDA FSIS: Meat, poultry, egg products (inspection certificates)
- FTC: Textiles (fiber content labeling under Textile Fiber Products Identification Act)
- CPSC: Consumer products, toys, children's items (safety standards)
- FCC: Electronics with radio frequency emissions (equipment authorization)
- NHTSA: Motor vehicles and equipment (FMVSS compliance)
- ATF: Firearms, ammunition, explosives (import permits)
- EPA: TSCA chemicals, vehicles (emissions), fuels
- TTB: Alcohol and tobacco (COLA, import permits)
- Commerce: Section 232 import licenses (steel, aluminum)

## 3. Country of Origin Marking — 19 USC 1304 / 19 CFR 134
All articles imported into the US must be marked with their country of origin in English,
in a conspicuous place, in a manner that is legible, indelible, and permanent.
Exemptions include goods that cannot be marked without injury, crude substances, and articles
that will be substantially transformed before sale.

## 4. Customs Valuation — 19 USC 1401a / 19 CFR 152
Transaction value is the primary method. Verify:
- Declared value appears reasonable for the commodity type
- All dutiable assists are included
- No apparent undervaluation

## 5. Documentation Requirements
Standard ocean import documentation:
- Commercial invoice (CBP Form 7501 / 19 CFR 141.86) — must include seller, buyer,
  description, quantity, unit price, total value, origin, HTS
- Packing list
- Bill of lading / air waybill
- Certificate of origin (for FTA claims)
- Special certificates as required by PGA agencies

## Finding Severity Levels
- CRITICAL: Legal prohibition (sanctions, embargo) — shipment must be rejected
- ERROR: Regulatory violation requiring correction before release
- WARNING: Potential issue requiring review or additional documentation
- INFO: Best practice recommendation or informational note

Always provide specific regulatory citations (19 CFR §, 19 USC §) and concrete remediation steps."""

    _TOOL_SCHEMA = {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "description": "List of validation findings",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "Short alphanumeric finding code (e.g. ISF-001, PGA-002, MARK-001)",
                        },
                        "severity": {
                            "type": "string",
                            "enum": ["INFO", "WARNING", "ERROR", "CRITICAL"],
                            "description": "Finding severity level",
                        },
                        "field": {
                            "type": ["string", "null"],
                            "description": "Specific field or line item the finding relates to",
                        },
                        "message": {
                            "type": "string",
                            "description": "Clear description of the compliance issue",
                        },
                        "regulatory_reference": {
                            "type": ["string", "null"],
                            "description": "Specific regulatory citation (e.g. '19 CFR 149.2(a)')",
                        },
                        "remediation": {
                            "type": "string",
                            "description": "Specific steps to resolve the finding",
                        },
                    },
                    "required": ["code", "severity", "message", "remediation"],
                },
            },
            "pga_requirements": {
                "type": "array",
                "items": {"type": "string"},
                "description": "All PGA requirements applicable to this shipment",
            },
            "isf_complete": {
                "type": "boolean",
                "description": "True if all 10 ISF data elements are present and complete",
            },
            "marking_compliant": {
                "type": ["boolean", "null"],
                "description": "True if country of origin marking requirements are met, null if unknown",
            },
            "validation_notes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "General validation notes and observations",
            },
        },
        "required": ["findings", "pga_requirements", "isf_complete", "validation_notes"],
    }

    def _gather_pga_requirements(self, classification_result: ClassificationResult) -> list[str]:
        """Collect all PGA requirements for the classified HTS codes."""
        all_requirements: list[str] = []
        seen: set[str] = set()
        for cls in classification_result.classifications:
            for req in get_pga_requirements(cls.hts_code):
                if req not in seen:
                    all_requirements.append(req)
                    seen.add(req)
        return all_requirements

    def _check_isf_completeness(
        self, parsed_shipment: ParsedShipment, classification_result: ClassificationResult
    ) -> bool:
        """Check whether the minimum ISF data elements are present."""
        if not parsed_shipment.importer_name:
            return False
        if not parsed_shipment.exporter_name:
            return False
        for item in parsed_shipment.line_items:
            if not item.country_of_origin_iso2:
                return False
        # HTS codes must be present for all classified items
        if not classification_result.classifications:
            return False
        return True

    def _build_prompt(
        self,
        parsed_shipment: ParsedShipment,
        classification_result: ClassificationResult,
        pga_requirements: list[str],
        isf_complete: bool,
    ) -> str:
        lines = [
            "Perform a comprehensive US import compliance validation on the following shipment.\n",
            "## SHIPMENT SUMMARY",
            f"Importer: {parsed_shipment.importer_name}",
            f"Exporter: {parsed_shipment.exporter_name or 'NOT PROVIDED'}",
            f"Exporter Country: {parsed_shipment.exporter_country or 'Unknown'}",
            f"Ship Date: {parsed_shipment.shipment_date or 'Not provided'}",
            f"Port of Entry: {parsed_shipment.port_of_entry or 'Not specified'}",
            f"Incoterms: {parsed_shipment.incoterms or 'Not specified'}",
            f"Total Value: ${parsed_shipment.total_value_usd:,.2f} USD\n",
            "## CLASSIFIED LINE ITEMS",
        ]

        for cls in classification_result.classifications:
            # Find corresponding parsed item
            parsed_item = next(
                (item for item in parsed_shipment.line_items if item.line_number == cls.line_number),
                None,
            )
            lines.append(f"\nLine {cls.line_number}:")
            if parsed_item:
                lines.append(f"  Description: {parsed_item.description}")
                lines.append(f"  Country of Origin: {parsed_item.country_of_origin} ({parsed_item.country_of_origin_iso2})")
                lines.append(f"  Value: ${parsed_item.total_value_usd:,.2f} USD")
            lines.append(f"  HTS Code: {cls.hts_code}")
            lines.append(f"  HTS Description: {cls.hts_description}")
            lines.append(f"  General Duty Rate: {cls.duty_rate_general}")

        lines.append("\n## PRE-COMPUTED PGA REQUIREMENTS")
        if pga_requirements:
            for req in pga_requirements:
                lines.append(f"  - {req}")
        else:
            lines.append("  None identified from HTS chapter lookup.")

        lines.append(f"\n## ISF COMPLETENESS (pre-check): {'COMPLETE' if isf_complete else 'INCOMPLETE'}")
        if not parsed_shipment.exporter_name:
            lines.append("  WARNING: Exporter name is missing — ISF seller/supplier element absent")

        lines.append(
            "\nIdentify ALL compliance issues including: documentation gaps, ISF data element "
            "deficiencies, PGA permit/certificate requirements, country of origin marking issues, "
            "valuation concerns, and any other regulatory requirements. Be specific with citations."
        )
        return "\n".join(lines)

    async def validate(
        self,
        parsed_shipment: ParsedShipment,
        classification_result: ClassificationResult,
    ) -> ValidationResult:
        """Validate a parsed and classified shipment for US import compliance.

        Combines rule-based PGA lookups and ISF checks with Claude-driven
        regulatory analysis for a comprehensive validation result.
        """
        pga_requirements = self._gather_pga_requirements(classification_result)
        isf_complete = self._check_isf_completeness(parsed_shipment, classification_result)

        prompt = self._build_prompt(
            parsed_shipment, classification_result, pga_requirements, isf_complete
        )
        result = await self._call_structured(
            user_prompt=prompt,
            tool_name="record_validation_result",
            tool_description=(
                "Record all import compliance validation findings, PGA requirements, "
                "ISF completeness status, and marking compliance determination."
            ),
            output_schema=self._TOOL_SCHEMA,
        )

        # Override the Claude-computed PGA list with our authoritative data-driven list,
        # but merge in any additional ones Claude identified
        claude_pga = result.get("pga_requirements", [])
        merged_pga = list(pga_requirements)
        for req in claude_pga:
            if req not in merged_pga:
                merged_pga.append(req)
        result["pga_requirements"] = merged_pga

        # Override ISF completeness with our rule-based check
        result["isf_complete"] = isf_complete

        return ValidationResult(**result)
