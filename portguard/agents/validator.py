"""ValidationAgent — rule-based ISF, PGA, and documentation compliance checks."""

from __future__ import annotations

from portguard.agents.base import BaseAgent
from portguard.data.pga import get_pga_requirements
from portguard.models.shipment import ParsedShipment
from portguard.models.classification import ClassificationResult
from portguard.models.validation import ValidationResult, ValidationFinding, FindingSeverity


class ValidationAgent(BaseAgent):
    AGENT_NAME = "ValidationAgent"

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
        if not classification_result.classifications:
            return False
        return True

    def _generate_findings(
        self,
        parsed_shipment: ParsedShipment,
        classification_result: ClassificationResult,
        pga_requirements: list[str],
        isf_complete: bool,
    ) -> list[ValidationFinding]:
        """Generate validation findings from rule-based checks."""
        findings: list[ValidationFinding] = []

        # ISF completeness findings
        if not isf_complete:
            missing: list[str] = []
            if not parsed_shipment.exporter_name:
                missing.append("seller/supplier name (ISF element 1)")
            for item in parsed_shipment.line_items:
                if not item.country_of_origin_iso2:
                    missing.append(f"country of origin for line {item.line_number} (ISF element 7)")
            if not classification_result.classifications:
                missing.append("HTS-6 commodity code (ISF element 8)")
            if missing:
                findings.append(ValidationFinding(
                    code="ISF-001",
                    severity=FindingSeverity.ERROR,
                    field="isf",
                    message=(
                        f"ISF (10+2) is incomplete — missing: {', '.join(missing)}. "
                        "ISF must be filed at least 24 hours before vessel departure."
                    ),
                    regulatory_reference="19 CFR 149.2; 19 USC 1415",
                    remediation=(
                        "File complete ISF via ACE with all 10 importer-provided data elements. "
                        "Late or incomplete ISF may result in penalties up to $10,000 per violation."
                    ),
                ))

        # Marking compliance — country of origin marking required
        has_origin = any(item.country_of_origin_iso2 for item in parsed_shipment.line_items)
        if not has_origin:
            findings.append(ValidationFinding(
                code="MARK-001",
                severity=FindingSeverity.WARNING,
                field="country_of_origin",
                message=(
                    "Country of origin marking cannot be verified — no country of origin "
                    "identified for line items. All articles imported into the US must be "
                    "conspicuously marked in English with their country of origin."
                ),
                regulatory_reference="19 USC 1304; 19 CFR 134",
                remediation=(
                    "Verify all articles are permanently marked with country of origin "
                    "before importation. Failure to mark may result in additional marking duties."
                ),
            ))

        # PGA requirement findings (INFO level)
        for i, req in enumerate(pga_requirements, 1):
            findings.append(ValidationFinding(
                code=f"PGA-{i:03d}",
                severity=FindingSeverity.INFO,
                field=None,
                message=f"PGA requirement identified: {req}",
                regulatory_reference=None,
                remediation=(
                    f"Ensure all required permits, certifications, and documentation for "
                    f"'{req}' are obtained prior to importation."
                ),
            ))

        # Valuation check
        if parsed_shipment.total_value_usd > 0:
            for item in parsed_shipment.line_items:
                if item.unit_value_usd <= 0:
                    findings.append(ValidationFinding(
                        code="VAL-001",
                        severity=FindingSeverity.WARNING,
                        field=f"line_items[{item.line_number}].unit_value_usd",
                        message=(
                            f"Line {item.line_number}: declared unit value is zero or negative "
                            f"(${item.unit_value_usd:.2f}). This may indicate a valuation issue."
                        ),
                        regulatory_reference="19 USC 1401a; 19 CFR 152",
                        remediation=(
                            "Verify transaction value and ensure all dutiable assists are included. "
                            "CBP may request additional documentation to support declared value."
                        ),
                    ))

        return findings

    async def validate(
        self,
        parsed_shipment: ParsedShipment,
        classification_result: ClassificationResult,
    ) -> ValidationResult:
        """Validate a parsed and classified shipment for US import compliance.

        Uses rule-based PGA lookups, ISF checks, and marking compliance — no external API.
        """
        pga_requirements = self._gather_pga_requirements(classification_result)
        isf_complete = self._check_isf_completeness(parsed_shipment, classification_result)
        findings = self._generate_findings(
            parsed_shipment, classification_result, pga_requirements, isf_complete
        )

        # Determine marking compliance based on data availability
        marking_compliant: bool | None = None
        if any(item.country_of_origin_iso2 for item in parsed_shipment.line_items):
            marking_compliant = True  # origin present; assume marking requirement can be met

        return ValidationResult(
            findings=findings,
            pga_requirements=pga_requirements,
            isf_complete=isf_complete,
            marking_compliant=marking_compliant,
            validation_notes=[
                "Validation performed using rule-based checks: ISF completeness, "
                "PGA requirements, country of origin marking."
            ],
        )
