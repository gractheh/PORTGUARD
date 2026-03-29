"""DecisionAgent — synthesize all pipeline results into a final compliance decision."""

import re

from portguard.agents.base import BaseAgent
from portguard.models.shipment import ParsedShipment
from portguard.models.classification import ClassificationResult
from portguard.models.validation import ValidationResult, FindingSeverity
from portguard.models.risk import RiskAssessment, RiskSeverity, RiskType
from portguard.models.decision import ComplianceDecision, DecisionLevel, RequiredAction


class DecisionAgent(BaseAgent):
    AGENT_NAME = "DecisionAgent"

    SYSTEM_PROMPT = """You are the Chief Compliance Officer of a leading customs brokerage, making
final import compliance release decisions. You synthesize parsed shipment data, HTS classifications,
validation findings, and risk assessments into a clear, actionable compliance decision.

Your decision framework:

## Decision Levels
- REJECT: The shipment involves a PROHIBITED transaction that cannot proceed:
  * Comprehensive OFAC sanctions (Iran, Cuba, North Korea, Syria) — absolute prohibition
  * Confirmed OFAC SDN (Specially Designated Nationals) party involvement
  * CBP Withhold and Release Order goods from confirmed forced labor origin
  * Other absolute legal prohibitions

- HOLD: The shipment can potentially proceed but requires significant action before release:
  * HIGH-severity risk factors requiring additional duty deposits (Section 301, AD/CVD, Section 232)
  * CRITICAL validation errors that must be corrected
  * Insufficient documentation for PGA clearance
  * Suspected misclassification with duty implications
  * UFLPA rebuttal evidence required
  * Sectoral sanctions requiring OFAC licensing

- REVIEW: The shipment requires additional review but no immediate blocking action:
  * WARNING-level validation findings
  * MEDIUM risk factors (sectoral sanctions concern, potential transshipment)
  * Classification confidence below 0.70
  * Missing optional documentation
  * Low-probability risk factors requiring monitoring

- CLEAR: The shipment may proceed with standard processing:
  * No risk factors, or only LOW severity factors
  * All required documentation present
  * Compliant classification and valuation
  * No PGA holds pending

## Duty Estimation
When estimating duties:
- Base duty = general rate × declared customs value
- Additional duties stack on top of the base duty
- Section 301 applies to the full dutiable value (not just China-origin markup)
- AD/CVD applies to the entered value
- Section 232 applies at the port of entry

## Required Actions
List specific, prioritized actions. Priority 1 = most urgent. Assign responsible party
(Importer, Customs Broker, Trade Counsel, CBP, OFAC). Include deadlines where regulatory
timeframes apply.

## Summary Requirements
- Be direct and clear: "CLEAR — no material compliance issues identified" vs
  "HOLD — Section 301 duties at 25% not yet deposited; AD/CVD bond required"
- Key findings should be the 3-7 most important facts driving the decision
- Decision rationale should explain the legal/regulatory basis for the decision level
- Confidence score reflects certainty in the decision (0.0-1.0)"""

    _TOOL_SCHEMA = {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["CLEAR", "REVIEW", "HOLD", "REJECT"],
                "description": "Final compliance decision",
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Confidence in the decision",
            },
            "summary": {
                "type": "string",
                "description": "One-sentence decision summary",
            },
            "key_findings": {
                "type": "array",
                "items": {"type": "string"},
                "description": "3-7 key findings driving the decision",
            },
            "required_actions": {
                "type": "array",
                "description": "Prioritized list of required actions",
                "items": {
                    "type": "object",
                    "properties": {
                        "priority": {"type": "integer", "minimum": 1},
                        "action": {"type": "string"},
                        "responsible_party": {"type": "string"},
                        "deadline": {"type": ["string", "null"]},
                        "regulatory_reference": {"type": ["string", "null"]},
                    },
                    "required": ["priority", "action", "responsible_party"],
                },
            },
            "estimated_base_duties_usd": {
                "type": ["number", "null"],
                "description": "Estimated base customs duties in USD",
            },
            "estimated_additional_duties_usd": {
                "type": ["number", "null"],
                "description": "Estimated additional duties (Section 301, AD/CVD, 232) in USD",
            },
            "estimated_total_duties_usd": {
                "type": ["number", "null"],
                "description": "Estimated total duties in USD",
            },
            "decision_rationale": {
                "type": "string",
                "description": "Full explanation of decision basis with regulatory citations",
            },
        },
        "required": [
            "decision", "confidence", "summary", "key_findings",
            "required_actions", "decision_rationale",
        ],
    }

    def _determine_decision_level(
        self,
        risk_assessment: RiskAssessment,
        validation_result: ValidationResult,
        classification_result: ClassificationResult,
    ) -> DecisionLevel:
        """Apply decision rules to determine the appropriate decision level."""
        # REJECT conditions
        for factor in risk_assessment.risk_factors:
            if factor.severity == RiskSeverity.CRITICAL:
                return DecisionLevel.REJECT
            if factor.risk_type == RiskType.OFAC_SANCTIONS and factor.severity in (
                RiskSeverity.CRITICAL, RiskSeverity.HIGH
            ):
                # Comprehensive sanctions → REJECT; sectoral → HOLD
                if "COMPREHENSIVE" in factor.description.upper():
                    return DecisionLevel.REJECT

        # HOLD conditions
        has_high_risk = any(
            f.severity == RiskSeverity.HIGH for f in risk_assessment.risk_factors
        )
        has_critical_finding = any(
            f.severity == FindingSeverity.CRITICAL for f in validation_result.findings
        )
        if has_high_risk or has_critical_finding:
            return DecisionLevel.HOLD

        # REVIEW conditions
        has_medium_risk = any(
            f.severity == RiskSeverity.MEDIUM for f in risk_assessment.risk_factors
        )
        has_warning_finding = any(
            f.severity in (FindingSeverity.WARNING, FindingSeverity.ERROR)
            for f in validation_result.findings
        )
        low_confidence_classification = any(
            cls.confidence < 0.7 for cls in classification_result.classifications
        )
        if has_medium_risk or has_warning_finding or low_confidence_classification:
            return DecisionLevel.REVIEW

        return DecisionLevel.CLEAR

    def _estimate_duties(
        self,
        parsed_shipment: ParsedShipment,
        classification_result: ClassificationResult,
        risk_assessment: RiskAssessment,
    ) -> tuple[float | None, float | None, float | None]:
        """Estimate base, additional, and total duties in USD."""
        base_duties = 0.0
        additional_duties = 0.0

        for cls in classification_result.classifications:
            item = next(
                (i for i in parsed_shipment.line_items if i.line_number == cls.line_number),
                None,
            )
            if not item:
                continue
            value = item.total_value_usd

            # Parse base duty rate
            rate_str = cls.duty_rate_general.lower().strip()
            if rate_str == "free" or rate_str == "0%" or rate_str == "0.0%":
                base_rate = 0.0
            else:
                # Extract numeric portion
                try:
                    base_rate = float(rate_str.rstrip("%")) / 100.0
                except ValueError:
                    base_rate = 0.0
            base_duties += value * base_rate

        # Additional duties from risk factors
        for factor in risk_assessment.risk_factors:
            if not factor.additional_duty_rate or not factor.hts_code:
                continue
            # Find the classification whose HTS code matches the risk factor
            matching_cls = next(
                (c for c in classification_result.classifications if c.hts_code == factor.hts_code),
                None,
            )
            if matching_cls is None:
                continue
            item = next(
                (i for i in parsed_shipment.line_items if i.line_number == matching_cls.line_number),
                None,
            )
            if item:
                rate_str = factor.additional_duty_rate.lower()
                # Take the first numeric rate found (handles "265.79% (all others)")
                rates = re.findall(r"[\d.]+%", rate_str)
                if rates:
                    try:
                        rate = float(rates[0].rstrip("%")) / 100.0
                        additional_duties += item.total_value_usd * rate
                    except ValueError:
                        pass

        if base_duties == 0 and additional_duties == 0:
            return None, None, None

        total = base_duties + additional_duties
        return (
            round(base_duties, 2) if base_duties > 0 else None,
            round(additional_duties, 2) if additional_duties > 0 else None,
            round(total, 2),
        )

    def _build_prompt(
        self,
        parsed_shipment: ParsedShipment,
        classification_result: ClassificationResult,
        validation_result: ValidationResult,
        risk_assessment: RiskAssessment,
        preliminary_decision: DecisionLevel,
        base_duties: float | None,
        additional_duties: float | None,
        total_duties: float | None,
    ) -> str:
        lines = [
            "Synthesize the following compliance pipeline results into a final compliance decision "
            f"with detailed findings and required actions.\n",
            f"## PRELIMINARY DECISION (rule-based): {preliminary_decision.value}",
            f"## SHIPMENT OVERVIEW",
            f"Importer: {parsed_shipment.importer_name}",
            f"Total Value: ${parsed_shipment.total_value_usd:,.2f} USD",
            f"Countries of Origin: {', '.join(set(i.country_of_origin_iso2 for i in parsed_shipment.line_items))}",
            f"Line Items: {len(parsed_shipment.line_items)}",
        ]

        if base_duties is not None:
            lines.append(f"Estimated Base Duties: ${base_duties:,.2f} USD")
        if additional_duties is not None:
            lines.append(f"Estimated Additional Duties: ${additional_duties:,.2f} USD")
        if total_duties is not None:
            lines.append(f"Estimated Total Duties: ${total_duties:,.2f} USD")

        lines.append("\n## CLASSIFICATIONS")
        for cls in classification_result.classifications:
            item = next(
                (i for i in parsed_shipment.line_items if i.line_number == cls.line_number),
                None,
            )
            desc = item.description if item else "Unknown"
            lines.append(
                f"  Line {cls.line_number}: {desc} → {cls.hts_code} "
                f"({cls.duty_rate_general}) confidence={cls.confidence:.2f}"
            )

        if validation_result.findings:
            lines.append(f"\n## VALIDATION FINDINGS ({len(validation_result.findings)} total)")
            for finding in validation_result.findings:
                lines.append(f"  [{finding.severity.value}] {finding.code}: {finding.message}")
        else:
            lines.append("\n## VALIDATION FINDINGS: None")

        lines.append(f"\n## ISF COMPLETE: {validation_result.isf_complete}")
        lines.append(f"## MARKING COMPLIANT: {validation_result.marking_compliant}")

        if validation_result.pga_requirements:
            lines.append(f"\n## PGA REQUIREMENTS ({len(validation_result.pga_requirements)} agencies)")
            for req in validation_result.pga_requirements:
                lines.append(f"  - {req}")

        if risk_assessment.risk_factors:
            lines.append(f"\n## RISK FACTORS ({len(risk_assessment.risk_factors)} identified)")
            for factor in risk_assessment.risk_factors:
                lines.append(
                    f"  [{factor.severity.value}] {factor.risk_type.value}: "
                    f"{factor.description[:150]}"
                )
        else:
            lines.append("\n## RISK FACTORS: None identified")

        lines.append(f"\n## OVERALL RISK LEVEL: {risk_assessment.overall_risk_level.value}")

        lines.append(
            "\nBased on the above, provide the final compliance decision with: "
            "one-sentence summary, 3-7 key findings, all required actions with priorities, "
            "duty estimates, and full decision rationale with regulatory citations. "
            f"The rule-based system has determined: {preliminary_decision.value}. "
            "Validate this or escalate if your expert analysis indicates a more severe level."
        )
        return "\n".join(lines)

    async def decide(
        self,
        parsed_shipment: ParsedShipment,
        classification_result: ClassificationResult,
        validation_result: ValidationResult,
        risk_assessment: RiskAssessment,
    ) -> ComplianceDecision:
        """Make a final compliance decision synthesizing all pipeline results.

        Rule-based decision logic determines the minimum decision level; Claude
        synthesizes the narrative, key findings, required actions, and may escalate.
        """
        preliminary_decision = self._determine_decision_level(
            risk_assessment, validation_result, classification_result
        )
        base_duties, additional_duties, total_duties = self._estimate_duties(
            parsed_shipment, classification_result, risk_assessment
        )

        prompt = self._build_prompt(
            parsed_shipment,
            classification_result,
            validation_result,
            risk_assessment,
            preliminary_decision,
            base_duties,
            additional_duties,
            total_duties,
        )
        result = await self._call_structured(
            user_prompt=prompt,
            tool_name="record_compliance_decision",
            tool_description=(
                "Record the final import compliance decision including decision level, "
                "key findings, required actions, duty estimates, and full rationale."
            ),
            output_schema=self._TOOL_SCHEMA,
        )

        # Ensure Claude's decision is at least as severe as our rule-based determination
        _level_order = {
            DecisionLevel.CLEAR: 0,
            DecisionLevel.REVIEW: 1,
            DecisionLevel.HOLD: 2,
            DecisionLevel.REJECT: 3,
        }
        claude_level = DecisionLevel(result.get("decision", preliminary_decision.value))
        final_level = (
            claude_level
            if _level_order[claude_level] >= _level_order[preliminary_decision]
            else preliminary_decision
        )
        result["decision"] = final_level.value

        # Use our computed duty estimates if Claude didn't provide them
        if result.get("estimated_base_duties_usd") is None:
            result["estimated_base_duties_usd"] = base_duties
        if result.get("estimated_additional_duties_usd") is None:
            result["estimated_additional_duties_usd"] = additional_duties
        if result.get("estimated_total_duties_usd") is None:
            result["estimated_total_duties_usd"] = total_duties

        return ComplianceDecision(**result)
