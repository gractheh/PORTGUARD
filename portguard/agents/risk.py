"""RiskAgent — rule-based + Claude-driven trade compliance risk assessment."""

import json
from portguard.agents.base import BaseAgent
from portguard.data.section301 import get_section_301, SECTION_301_COUNTRIES
from portguard.data.sanctions import get_sanctions_programs, is_comprehensively_sanctioned
from portguard.data.adcvd import get_adcvd_orders
from portguard.models.shipment import ParsedShipment
from portguard.models.classification import ClassificationResult
from portguard.models.risk import RiskAssessment, RiskFactor, RiskType, RiskSeverity

# HTS chapters subject to Section 232 measures
_SECTION_232_STEEL_CHAPTERS = {"72", "73"}
_SECTION_232_ALUMINUM_CHAPTERS = {"76"}

# UFLPA high-risk goods categories for Xinjiang
_UFLPA_HIGH_RISK_CATEGORIES = {
    "cotton", "polysilicon", "tomatoes", "steel", "textiles", "apparel",
    "aluminum", "solar", "batteries", "chemicals",
}

# Keywords that may indicate Xinjiang origin
_XINJIANG_KEYWORDS = {
    "xinjiang", "xuar", "uyghur", "uygur", "xinjiang uyghur",
    "xinjiang uygur", "east turkestan",
}


class RiskAgent(BaseAgent):
    AGENT_NAME = "RiskAgent"

    SYSTEM_PROMPT = """You are a senior trade compliance risk analyst specializing in US import
enforcement with expertise in OFAC sanctions, antidumping/countervailing duty orders, Section 301
tariffs, Section 232 national security tariffs, forced labor provisions, and export control
regulations. You have 20+ years of experience at a major customs brokerage and trade law firm.

Your role is to supplement automated rule-based risk checks with expert judgment, identifying
risks that static data cannot capture. The rule-based system has already flagged known risks —
your task is to:

1. REVIEW the pre-identified rule-based risk factors for accuracy and completeness.

2. IDENTIFY additional risks that require expert judgment, including:
   - Transshipment risk: Goods routed through third countries to evade tariffs or sanctions
     (common patterns: CN goods via VN, TW, MX, IN; RU goods via BY, KZ, GE, AM)
   - ITAR/EAR export control concerns: Dual-use electronics, encryption, military/defense items
     (relevant HTS: 8517, 8471, 8473, 8479, 8542, 9013, 9014, 9015)
   - Denied party screening: Flag if manufacturer/exporter names suggest sanctioned entities
     (use judgment — you don't have the full SDN list but note if names sound concerning)
   - UFLPA (Uyghur Forced Labor Prevention Act): China-origin goods in high-risk categories
     (cotton, polysilicon/solar, tomatoes, steel, aluminum, batteries) face rebuttable
     presumption of forced labor — require supply chain documentation
   - Section 201 solar safeguards: Chinese/global solar panels (HTS 8541.40)
   - AD/CVD circumvention: Vietnamese steel, solar products may be subject to anti-circumvention
   - CBP ADD/CVD Withhold Release Orders (WRO): Cotton from Xinjiang, certain seafood, etc.
   - Commercial counterfeiting risk: Consumer goods from China in categories with high counterfeit
     rates (electronics accessories, luxury goods, pharmaceuticals, apparel)

3. ESTIMATE additional duty exposure: Provide estimated additional_duty_rate where applicable.

4. ASSESS overall risk level:
   - CRITICAL: Comprehensive OFAC sanctions, confirmed prohibited transaction
   - HIGH: Section 301 (25%), AD/CVD orders, UFLPA-flagged goods, Section 232
   - MEDIUM: Sectoral sanctions, Section 301 (7.5%), transshipment risk, export control concern
   - LOW: Minor documentation risk, low-value de minimis considerations

5. For each risk factor provide:
   - The specific regulatory reference (e.g., "USTR Section 301 List 3, 83 FR 47974")
   - A concrete recommended_action
   - The additional duty rate if quantifiable

Always err on the side of identifying potential risks — it is better to flag for review
than to miss a compliance issue that could result in penalties, seizure, or enforcement action."""

    _TOOL_SCHEMA = {
        "type": "object",
        "properties": {
            "additional_risk_factors": {
                "type": "array",
                "description": "Additional risk factors identified by expert analysis (beyond rule-based checks)",
                "items": {
                    "type": "object",
                    "properties": {
                        "risk_type": {
                            "type": "string",
                            "enum": [
                                "SECTION_301", "SECTION_232", "SECTION_201",
                                "ANTIDUMPING", "COUNTERVAILING", "OFAC_SANCTIONS",
                                "EXPORT_CONTROL", "DENIED_PARTY", "FORCED_LABOR",
                                "VALUATION", "OTHER",
                            ],
                        },
                        "severity": {
                            "type": "string",
                            "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                        },
                        "hts_code": {"type": ["string", "null"]},
                        "country": {"type": ["string", "null"]},
                        "entity": {"type": ["string", "null"]},
                        "description": {"type": "string"},
                        "additional_duty_rate": {"type": ["string", "null"]},
                        "order_number": {"type": ["string", "null"]},
                        "regulatory_reference": {"type": "string"},
                        "recommended_action": {"type": "string"},
                    },
                    "required": [
                        "risk_type", "severity", "description",
                        "regulatory_reference", "recommended_action",
                    ],
                },
            },
            "overall_risk_level": {
                "type": "string",
                "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                "description": "Overall risk assessment considering all factors",
            },
            "estimated_additional_duties_usd": {
                "type": ["number", "null"],
                "description": "Estimated total additional duties in USD from all risk factors",
            },
            "risk_notes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Additional analysis notes and context",
            },
        },
        "required": [
            "additional_risk_factors", "overall_risk_level", "risk_notes",
        ],
    }

    def _check_section_301(
        self,
        parsed_shipment: ParsedShipment,
        classification_result: ClassificationResult,
    ) -> list[RiskFactor]:
        """Run rule-based Section 301 checks for all CN-origin line items."""
        factors: list[RiskFactor] = []
        for cls in classification_result.classifications:
            item = next(
                (i for i in parsed_shipment.line_items if i.line_number == cls.line_number),
                None,
            )
            if not item:
                continue
            if item.country_of_origin_iso2.upper() != "CN":
                continue
            entry = get_section_301(cls.hts_code, "CN")
            if entry:
                factors.append(
                    RiskFactor(
                        risk_type=RiskType.SECTION_301,
                        severity=RiskSeverity.HIGH,
                        hts_code=cls.hts_code,
                        country="CN",
                        description=(
                            f"HTS {cls.hts_code} ({cls.hts_description}) is subject to "
                            f"Section 301 {entry.list_name} additional duty of {entry.rate} "
                            f"on imports from China. Effective: {entry.effective_date}."
                        ),
                        additional_duty_rate=entry.rate,
                        regulatory_reference=(
                            f"USTR Section 301 {entry.list_name}; "
                            f"effective {entry.effective_date}"
                        ),
                        recommended_action=(
                            f"Deposit Section 301 additional duty of {entry.rate} on "
                            f"the dutiable value of line {cls.line_number}. Evaluate "
                            "exclusion request eligibility or first-sale valuation."
                        ),
                    )
                )
        return factors

    def _check_section_232(
        self,
        parsed_shipment: ParsedShipment,
        classification_result: ClassificationResult,
    ) -> list[RiskFactor]:
        """Flag Section 232 steel and aluminum duties."""
        factors: list[RiskFactor] = []
        for cls in classification_result.classifications:
            chapter = cls.hts_code[:2]
            item = next(
                (i for i in parsed_shipment.line_items if i.line_number == cls.line_number),
                None,
            )
            country = item.country_of_origin_iso2 if item else "Unknown"

            if chapter in _SECTION_232_STEEL_CHAPTERS:
                factors.append(
                    RiskFactor(
                        risk_type=RiskType.SECTION_232,
                        severity=RiskSeverity.HIGH,
                        hts_code=cls.hts_code,
                        country=country,
                        description=(
                            f"HTS {cls.hts_code} (Chapter {chapter}) is subject to Section 232 "
                            f"steel tariff of 25% from most countries. Country: {country}. "
                            "Quota/agreement exemptions may apply for certain countries."
                        ),
                        additional_duty_rate="25%",
                        regulatory_reference=(
                            "Section 232 of the Trade Expansion Act of 1962; "
                            "Proclamation 9705 (2018-03-23); 83 FR 11619"
                        ),
                        recommended_action=(
                            "Deposit Section 232 steel duty of 25%. Verify whether "
                            f"{country} has an approved quota arrangement, exclusion, "
                            "or alternative agreement exempting this product. "
                            "File steel import license with US Commerce prior to entry."
                        ),
                    )
                )
            elif chapter in _SECTION_232_ALUMINUM_CHAPTERS:
                factors.append(
                    RiskFactor(
                        risk_type=RiskType.SECTION_232,
                        severity=RiskSeverity.HIGH,
                        hts_code=cls.hts_code,
                        country=country,
                        description=(
                            f"HTS {cls.hts_code} (Chapter 76) is subject to Section 232 "
                            f"aluminum tariff of 10% from most countries. Country: {country}."
                        ),
                        additional_duty_rate="10%",
                        regulatory_reference=(
                            "Section 232 of the Trade Expansion Act of 1962; "
                            "Proclamation 9704 (2018-03-23); 83 FR 11619"
                        ),
                        recommended_action=(
                            "Deposit Section 232 aluminum duty of 10%. Verify whether "
                            f"{country} has an approved quota/exemption arrangement. "
                            "File aluminum import license with US Commerce."
                        ),
                    )
                )
        return factors

    def _check_adcvd(
        self,
        parsed_shipment: ParsedShipment,
        classification_result: ClassificationResult,
    ) -> list[RiskFactor]:
        """Match line items against active AD/CVD orders."""
        factors: list[RiskFactor] = []
        for cls in classification_result.classifications:
            item = next(
                (i for i in parsed_shipment.line_items if i.line_number == cls.line_number),
                None,
            )
            if not item:
                continue
            orders = get_adcvd_orders(cls.hts_code, item.country_of_origin_iso2)
            for order in orders:
                risk_type = (
                    RiskType.ANTIDUMPING if order.order_type == "AD"
                    else RiskType.COUNTERVAILING
                )
                factors.append(
                    RiskFactor(
                        risk_type=risk_type,
                        severity=RiskSeverity.HIGH,
                        hts_code=cls.hts_code,
                        country=item.country_of_origin_iso2,
                        description=(
                            f"Active {order.order_type} order {order.case_number}: "
                            f"{order.product_description}. Rate: {order.duty_rate}. "
                            f"FR: {order.federal_register}. {order.notes}"
                        ),
                        additional_duty_rate=order.duty_rate,
                        order_number=order.case_number,
                        regulatory_reference=(
                            f"ITA {order.order_type} Order {order.case_number}; "
                            f"{order.federal_register}; effective {order.effective_date}"
                        ),
                        recommended_action=(
                            f"Deposit {order.order_type} duty at rate {order.duty_rate} "
                            f"(case {order.case_number}). Obtain producer/exporter-specific "
                            "rate from ITA if available; file with CBP on CF-7501. "
                            "Consider AD/CVD bond requirements."
                        ),
                    )
                )
        return factors

    def _check_sanctions(self, parsed_shipment: ParsedShipment) -> list[RiskFactor]:
        """Check all countries of origin against OFAC sanctions programs."""
        factors: list[RiskFactor] = []
        checked_countries: set[str] = set()

        for item in parsed_shipment.line_items:
            iso2 = item.country_of_origin_iso2.upper()
            if iso2 in checked_countries:
                continue
            checked_countries.add(iso2)
            programs = get_sanctions_programs(iso2)
            for program in programs:
                severity = (
                    RiskSeverity.CRITICAL
                    if program.program_type == "COMPREHENSIVE"
                    else RiskSeverity.HIGH
                )
                sector_note = (
                    ""
                    if not program.sectors
                    else f" Affected sectors: {', '.join(program.sectors)}."
                )
                factors.append(
                    RiskFactor(
                        risk_type=RiskType.OFAC_SANCTIONS,
                        severity=severity,
                        country=iso2,
                        description=(
                            f"{program.program_type} OFAC sanctions apply to "
                            f"{program.country_name} ({iso2}) under {program.program_name} "
                            f"({program.cfr_citation}).{sector_note} {program.notes}"
                        ),
                        regulatory_reference=(
                            f"OFAC {program.program_name}; {program.cfr_citation}"
                        ),
                        recommended_action=(
                            "STOP TRANSACTION. Do not proceed with import. "
                            f"{'Comprehensive embargo — all transactions prohibited without OFAC license. ' if program.program_type == 'COMPREHENSIVE' else f'Sectoral restrictions on: {chr(44).join(program.sectors)}. '}"
                            "Contact OFAC at ofac@treasury.gov or consult trade counsel "
                            "before taking any further action."
                        ),
                    )
                )
        return factors

    def _check_uflpa(
        self,
        parsed_shipment: ParsedShipment,
        classification_result: ClassificationResult,
    ) -> list[RiskFactor]:
        """Flag potential UFLPA (Uyghur Forced Labor Prevention Act) risks."""
        factors: list[RiskFactor] = []

        # Check for explicit Xinjiang references
        full_text = " ".join([
            parsed_shipment.additional_context or "",
            " ".join(item.description.lower() for item in parsed_shipment.line_items),
            parsed_shipment.exporter_name or "",
        ]).lower()

        has_xinjiang_reference = any(kw in full_text for kw in _XINJIANG_KEYWORDS)

        for cls in classification_result.classifications:
            item = next(
                (i for i in parsed_shipment.line_items if i.line_number == cls.line_number),
                None,
            )
            if not item or item.country_of_origin_iso2.upper() != "CN":
                continue

            category_lower = item.goods_category.lower()
            description_lower = item.description.lower()

            is_high_risk_category = any(
                kw in category_lower or kw in description_lower
                for kw in _UFLPA_HIGH_RISK_CATEGORIES
            )

            if has_xinjiang_reference or is_high_risk_category:
                factors.append(
                    RiskFactor(
                        risk_type=RiskType.FORCED_LABOR,
                        severity=RiskSeverity.HIGH,
                        hts_code=cls.hts_code,
                        country="CN",
                        description=(
                            f"Line {cls.line_number} ({item.description}) from China "
                            f"in category '{item.goods_category}' may be subject to the "
                            "Uyghur Forced Labor Prevention Act (UFLPA) rebuttable "
                            "presumption. Goods produced wholly or in part in Xinjiang, "
                            "or by entities on the UFLPA Entity List, are presumed to be "
                            "made with forced labor and are prohibited from import unless "
                            "the importer can rebut the presumption."
                        ),
                        regulatory_reference=(
                            "Uyghur Forced Labor Prevention Act (UFLPA), Pub. L. 117-78; "
                            "19 USC 1307; CBP UFLPA Strategy (June 2022)"
                        ),
                        recommended_action=(
                            "Conduct supply chain due diligence. Obtain evidence that goods "
                            "were not produced in Xinjiang or with forced labor "
                            "(e.g., production records, facility audits, chain-of-custody docs). "
                            "Check manufacturer against UFLPA Entity List. "
                            "Be prepared for CBP detention and submission of rebuttal evidence."
                        ),
                    )
                )
        return factors

    def _check_valuation(self, parsed_shipment: ParsedShipment) -> list[RiskFactor]:
        """Flag valuation concerns based on shipment value thresholds."""
        factors: list[RiskFactor] = []
        total = parsed_shipment.total_value_usd

        if total <= 800:
            factors.append(
                RiskFactor(
                    risk_type=RiskType.VALUATION,
                    severity=RiskSeverity.LOW,
                    description=(
                        f"Shipment total value ${total:,.2f} USD is at or below the $800 "
                        "de minimis threshold (19 USC 1321). No formal entry required; "
                        "however, Section 301 tariffs and AD/CVD duties still apply to "
                        "de minimis shipments from China as of 2024 legislative changes."
                    ),
                    regulatory_reference="19 USC 1321; HTSUS Chapter 99 Note",
                    recommended_action=(
                        "Verify current de minimis rules for country of origin. "
                        "Note that de minimis exemption was eliminated for CN-origin goods "
                        "subject to Section 301. Confirm entry type required."
                    ),
                )
            )
        elif total > 2500:
            # Formal entry threshold — just informational
            factors.append(
                RiskFactor(
                    risk_type=RiskType.VALUATION,
                    severity=RiskSeverity.LOW,
                    description=(
                        f"Shipment value ${total:,.2f} USD exceeds $2,500 formal entry "
                        "threshold. A formal customs entry (CBP Form 7501) is required, "
                        "along with a surety bond."
                    ),
                    regulatory_reference="19 CFR 143.21; 19 USC 1484",
                    recommended_action=(
                        "File formal customs entry (Type 01 Consumption Entry). "
                        "Ensure surety bond is in place. "
                        "Entry summary due within 10 working days of release."
                    ),
                )
            )
        return factors

    def _compute_overall_risk(self, risk_factors: list[RiskFactor]) -> RiskSeverity:
        """Compute overall risk level as the maximum severity across all factors."""
        if not risk_factors:
            return RiskSeverity.LOW
        severity_order = {
            RiskSeverity.LOW: 0,
            RiskSeverity.MEDIUM: 1,
            RiskSeverity.HIGH: 2,
            RiskSeverity.CRITICAL: 3,
        }
        return max(risk_factors, key=lambda f: severity_order[f.severity]).severity

    def _build_prompt(
        self,
        parsed_shipment: ParsedShipment,
        classification_result: ClassificationResult,
        rule_based_factors: list[RiskFactor],
    ) -> str:
        lines = [
            "Perform expert trade compliance risk analysis on the following shipment. "
            "Rule-based checks have already been run — review them and identify any "
            "ADDITIONAL risks not captured by static data.\n",
            "## SHIPMENT SUMMARY",
            f"Importer: {parsed_shipment.importer_name}",
            f"Exporter: {parsed_shipment.exporter_name or 'Unknown'}",
            f"Exporter Country: {parsed_shipment.exporter_country or 'Unknown'}",
            f"Total Value: ${parsed_shipment.total_value_usd:,.2f} USD\n",
            "## LINE ITEMS",
        ]

        for cls in classification_result.classifications:
            item = next(
                (i for i in parsed_shipment.line_items if i.line_number == cls.line_number),
                None,
            )
            if item:
                lines.append(
                    f"  Line {cls.line_number}: {item.description} | "
                    f"HTS {cls.hts_code} | Origin: {item.country_of_origin} ({item.country_of_origin_iso2}) | "
                    f"Value: ${item.total_value_usd:,.2f} | Manufacturer: {item.manufacturer or 'Unknown'}"
                )

        if rule_based_factors:
            lines.append("\n## RULE-BASED RISK FACTORS ALREADY IDENTIFIED")
            for i, factor in enumerate(rule_based_factors, 1):
                lines.append(
                    f"  {i}. [{factor.severity.value}] {factor.risk_type.value}: "
                    f"{factor.description[:120]}..."
                    if len(factor.description) > 120
                    else f"  {i}. [{factor.severity.value}] {factor.risk_type.value}: {factor.description}"
                )
        else:
            lines.append("\n## RULE-BASED RISK FACTORS: None identified")

        lines.append(
            "\nAnalyze the above for any additional risks: transshipment, export controls, "
            "denied parties, UFLPA (if not already flagged), AD/CVD circumvention, "
            "counterfeiting, or other compliance concerns. Also estimate total additional "
            "duties if quantifiable."
        )
        return "\n".join(lines)

    async def assess_risk(
        self,
        parsed_shipment: ParsedShipment,
        classification_result: ClassificationResult,
    ) -> RiskAssessment:
        """Perform a complete risk assessment combining rule-based and Claude analysis.

        Rule-based checks run first (deterministic), then Claude adds expert analysis
        for risks that require judgment.
        """
        # Rule-based checks
        rule_factors: list[RiskFactor] = []
        rule_factors.extend(self._check_section_301(parsed_shipment, classification_result))
        rule_factors.extend(self._check_section_232(parsed_shipment, classification_result))
        rule_factors.extend(self._check_adcvd(parsed_shipment, classification_result))
        rule_factors.extend(self._check_sanctions(parsed_shipment))
        rule_factors.extend(self._check_uflpa(parsed_shipment, classification_result))
        rule_factors.extend(self._check_valuation(parsed_shipment))

        # Claude expert analysis for additional risks
        prompt = self._build_prompt(parsed_shipment, classification_result, rule_factors)
        claude_result = await self._call_structured(
            user_prompt=prompt,
            tool_name="record_risk_assessment",
            tool_description=(
                "Record additional trade compliance risk factors identified through "
                "expert analysis, beyond the rule-based checks already performed."
            ),
            output_schema=self._TOOL_SCHEMA,
        )

        # Merge rule-based and Claude risk factors
        all_factors = list(rule_factors)
        for factor_dict in claude_result.get("additional_risk_factors", []):
            try:
                all_factors.append(RiskFactor(**factor_dict))
            except Exception:
                pass  # Skip malformed factors from Claude

        overall_risk = self._compute_overall_risk(all_factors)

        # Use Claude's estimate if provided, otherwise None
        estimated_additional = claude_result.get("estimated_additional_duties_usd")

        return RiskAssessment(
            risk_factors=all_factors,
            overall_risk_level=overall_risk,
            estimated_additional_duties_usd=estimated_additional,
            risk_notes=claude_result.get("risk_notes", []),
        )
