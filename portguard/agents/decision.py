"""DecisionAgent — synthesize pipeline results into a final compliance decision (rule-based)."""

from __future__ import annotations

import re

from portguard.agents.base import BaseAgent
from portguard.models.shipment import ParsedShipment
from portguard.models.classification import ClassificationResult
from portguard.models.validation import ValidationResult, FindingSeverity
from portguard.models.risk import RiskAssessment, RiskSeverity, RiskType
from portguard.models.decision import ComplianceDecision, DecisionLevel, RequiredAction


class DecisionAgent(BaseAgent):
    AGENT_NAME = "DecisionAgent"

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

            rate_str = cls.duty_rate_general.lower().strip()
            if rate_str in ("free", "0%", "0.0%"):
                base_rate = 0.0
            else:
                try:
                    base_rate = float(rate_str.rstrip("%")) / 100.0
                except ValueError:
                    base_rate = 0.0
            base_duties += value * base_rate

        for factor in risk_assessment.risk_factors:
            if not factor.additional_duty_rate or not factor.hts_code:
                continue
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

    def _build_key_findings(
        self,
        parsed_shipment: ParsedShipment,
        classification_result: ClassificationResult,
        validation_result: ValidationResult,
        risk_assessment: RiskAssessment,
    ) -> list[str]:
        """Generate key findings from pipeline results."""
        findings: list[str] = []

        # Risk factors (most important)
        for factor in risk_assessment.risk_factors:
            if factor.severity in (RiskSeverity.CRITICAL, RiskSeverity.HIGH):
                findings.append(factor.description[:200])

        # Validation findings (errors and warnings)
        for vf in validation_result.findings:
            if vf.severity in (FindingSeverity.CRITICAL, FindingSeverity.ERROR, FindingSeverity.WARNING):
                findings.append(vf.message[:200])

        # Classification summary
        for cls in classification_result.classifications:
            item = next(
                (i for i in parsed_shipment.line_items if i.line_number == cls.line_number),
                None,
            )
            if item:
                findings.append(
                    f"{item.description[:60]} classified as HTS {cls.hts_code} "
                    f"({cls.duty_rate_general}) from {item.country_of_origin}"
                )

        # ISF status
        if validation_result.isf_complete:
            findings.append("ISF data elements are complete.")
        else:
            findings.append("ISF is incomplete — one or more required data elements are missing.")

        # PGA requirements
        if validation_result.pga_requirements:
            findings.append(
                f"PGA requirements apply: {'; '.join(validation_result.pga_requirements[:3])}"
            )

        # LOW-severity medium risk factors
        for factor in risk_assessment.risk_factors:
            if factor.severity == RiskSeverity.MEDIUM:
                findings.append(factor.description[:200])

        return findings[:7] or ["No material compliance issues identified."]

    def _build_required_actions(
        self,
        decision_level: DecisionLevel,
        risk_assessment: RiskAssessment,
        validation_result: ValidationResult,
    ) -> list[RequiredAction]:
        """Build prioritized required actions from risk factors and validation findings."""
        actions: list[RequiredAction] = []
        priority = 1

        # Actions from risk factors (high/critical first)
        for factor in sorted(
            risk_assessment.risk_factors,
            key=lambda f: {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}[f.severity.value],
        ):
            if factor.recommended_action:
                actions.append(RequiredAction(
                    priority=priority,
                    action=factor.recommended_action,
                    responsible_party="Importer",
                    regulatory_reference=factor.regulatory_reference,
                ))
                priority += 1

        # Actions from validation findings
        for vf in validation_result.findings:
            if vf.severity in (FindingSeverity.CRITICAL, FindingSeverity.ERROR):
                actions.append(RequiredAction(
                    priority=priority,
                    action=vf.remediation,
                    responsible_party="Customs Broker",
                    regulatory_reference=vf.regulatory_reference,
                ))
                priority += 1

        # Default action for CLEAR decisions
        if not actions:
            actions.append(RequiredAction(
                priority=1,
                action="File formal customs entry (CBP Form 7501) and pay applicable duties.",
                responsible_party="Customs Broker",
                regulatory_reference="19 CFR 143.21",
            ))

        return actions

    async def decide(
        self,
        parsed_shipment: ParsedShipment,
        classification_result: ClassificationResult,
        validation_result: ValidationResult,
        risk_assessment: RiskAssessment,
    ) -> ComplianceDecision:
        """Make a final compliance decision using rule-based logic only."""
        decision_level = self._determine_decision_level(
            risk_assessment, validation_result, classification_result
        )
        base_duties, additional_duties, total_duties = self._estimate_duties(
            parsed_shipment, classification_result, risk_assessment
        )

        key_findings = self._build_key_findings(
            parsed_shipment, classification_result, validation_result, risk_assessment
        )
        required_actions = self._build_required_actions(
            decision_level, risk_assessment, validation_result
        )

        # Build summary
        level_summaries = {
            DecisionLevel.CLEAR: "CLEAR — no material compliance issues identified; standard processing may proceed.",
            DecisionLevel.REVIEW: "REVIEW — minor compliance issues detected; proceed with compliance review.",
            DecisionLevel.HOLD: "HOLD — significant compliance issues require resolution before cargo release.",
            DecisionLevel.REJECT: "REJECT — prohibited transaction; OFAC sanctions or embargo applies.",
        }
        summary = level_summaries[decision_level]

        # Build rationale
        risk_summary = (
            f"{len(risk_assessment.risk_factors)} risk factor(s) identified "
            f"(overall: {risk_assessment.overall_risk_level.value})."
            if risk_assessment.risk_factors
            else "No risk factors identified."
        )
        rationale = (
            f"Rule-based decision: {decision_level.value}. "
            f"{risk_summary} "
            f"ISF complete: {validation_result.isf_complete}. "
            f"Validation findings: {len(validation_result.findings)}."
        )

        return ComplianceDecision(
            decision=decision_level,
            confidence=0.85,
            summary=summary,
            key_findings=key_findings,
            required_actions=required_actions,
            estimated_base_duties_usd=base_duties,
            estimated_additional_duties_usd=additional_duties,
            estimated_total_duties_usd=total_duties,
            decision_rationale=rationale,
        )
