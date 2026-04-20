"""
portguard/agents/sustainability_rater.py — Stage 5.5 sustainability rating engine.

Computes an A/B/C/D/N/A sustainability grade from four independent signal groups:
  1. Detected certifications from CertificationScreeningResult
  2. Country-of-origin sustainability risk (static table, ISO2-keyed)
  3. Product category sustainability risk (HTS chapter-keyed)
  4. Supplier declaration keywords in document text

Critical design constraint (architecture doc §6.1):
    The SustainabilityRating does NOT influence the DecisionLevel
    (CLEAR / REVIEW / HOLD / REJECT).  It is a parallel informational
    output, not a compliance gate.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from portguard.models.certification import (
    CertificationScreeningResult,
    SustainabilityRating,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Country sustainability risk table (ISO2 → risk level)
# ---------------------------------------------------------------------------

_COUNTRY_SUSTAINABILITY_RISK: dict[str, str] = {
    # HIGH deforestation / forced labor / extraction risk
    "ID": "HIGH",   # Indonesia — palm oil / deforestation
    "MY": "HIGH",   # Malaysia — palm / timber
    "PG": "HIGH",   # Papua New Guinea — timber
    "BR": "HIGH",   # Brazil — soy / cattle / deforestation
    "KH": "HIGH",   # Cambodia — timber
    "MM": "HIGH",   # Myanmar
    "PH": "HIGH",   # Philippines — fisheries
    "VN": "HIGH",   # Vietnam — seafood / timber
    "CD": "HIGH",   # DRC — minerals / conflict
    "SS": "HIGH",   # South Sudan — conflict
    # MEDIUM supply chain opacity / labor / environmental
    "CN": "MEDIUM",
    "IN": "MEDIUM",
    "BD": "MEDIUM",
    "PK": "MEDIUM",
    "MX": "MEDIUM",
    "TH": "MEDIUM",
    "GH": "MEDIUM",
    "NG": "MEDIUM",
    "UZ": "MEDIUM",
    "TM": "MEDIUM",
    "ET": "MEDIUM",
    "KE": "MEDIUM",
    "CO": "MEDIUM",
    "PE": "MEDIUM",
    "UG": "MEDIUM",
    "TZ": "MEDIUM",
    "RW": "MEDIUM",
    "TR": "MEDIUM",
    "IR": "MEDIUM",
    # LOW — strong environmental governance
    "DE": "LOW",
    "FR": "LOW",
    "SE": "LOW",
    "FI": "LOW",
    "NO": "LOW",
    "DK": "LOW",
    "AU": "LOW",
    "NZ": "LOW",
    "CA": "LOW",
    "CH": "LOW",
    "GB": "LOW",
    "NL": "LOW",
    "AT": "LOW",
    "BE": "LOW",
    "JP": "LOW",
    "KR": "LOW",
    "US": "LOW",
    "SG": "LOW",
}

# ---------------------------------------------------------------------------
# HTS chapter → product sustainability risk
# ---------------------------------------------------------------------------

_HTS_CHAPTER_RISK: dict[str, str] = {
    # HIGH — deforestation, forced labor, extractive
    "03": "HIGH",   # seafood
    "09": "HIGH",   # coffee/cocoa
    "10": "HIGH",   # grain/soy
    "12": "HIGH",   # oilseeds, palm
    "15": "HIGH",   # edible oils, palm oil
    "26": "HIGH",   # ores/minerals
    "41": "HIGH",   # hides/leather
    "44": "HIGH",   # wood/timber
    "47": "HIGH",   # pulp
    "48": "HIGH",   # paper
    "52": "HIGH",   # cotton
    "71": "HIGH",   # precious stones/minerals
    # MEDIUM — opacity / labor / regional risk
    "02": "MEDIUM",
    "06": "MEDIUM",
    "07": "MEDIUM",
    "08": "MEDIUM",
    "16": "MEDIUM",
    "17": "MEDIUM",
    "18": "MEDIUM",
    "42": "MEDIUM",
    "53": "MEDIUM",
    "54": "MEDIUM",
    "55": "MEDIUM",
    "61": "MEDIUM",
    "62": "MEDIUM",
    "63": "MEDIUM",
    "64": "MEDIUM",
    "72": "MEDIUM",
    "73": "MEDIUM",
    "74": "MEDIUM",
    "75": "MEDIUM",
    "76": "MEDIUM",
    "28": "MEDIUM",
    "29": "MEDIUM",
    # LOW — lower inherent complexity
    "30": "LOW",
    "39": "LOW",
    "84": "LOW",
    "85": "LOW",
    "87": "LOW",
    "90": "LOW",
    "94": "LOW",
    # N/A — no applicable sustainability standards
    "49": "N/A",
    "97": "N/A",
    "98": "N/A",
    "99": "N/A",
}

# ---------------------------------------------------------------------------
# Supplier declaration keyword patterns
# ---------------------------------------------------------------------------

_DECLARATION_PATTERNS: list[re.Pattern] = [
    re.compile(r"we\s+certif[yi]\s+that\s+this\s+shipment\s+complies", re.IGNORECASE),
    re.compile(r"supplier\s+is\s+certified\s+under", re.IGNORECASE),
    re.compile(r"certificate\s+of\s+compliance[:\s]", re.IGNORECASE),
    re.compile(r"we\s+hereby\s+declare\s+that\s+(?:the\s+)?(?:above\s+)?(?:goods|products|merchandise)\s+(?:are|were)\s+produced\s+in\s+compliance", re.IGNORECASE),
    re.compile(r"sustainability\s+declaration", re.IGNORECASE),
    re.compile(r"responsible\s+sourcing\s+declaration", re.IGNORECASE),
    re.compile(r"environmental\s+compliance\s+declaration", re.IGNORECASE),
]

# Certification names that indicate credible sustainability documentation
_SUSTAINABILITY_CERT_KEYWORDS = frozenset({
    "FSC", "PEFC", "SFI", "Rainforest Alliance", "RSPO", "MSC",
    "Better Cotton", "BCI", "Fairtrade", "SA8000", "ISO 14001", "ISO14001",
    "LEED", "Green Seal", "ECOLOGO", "Carbon Trust", "CarbonNeutral",
    "Cradle to Cradle", "C2C",
})


class SustainabilityRater:
    """Stage 5.5: Compute a sustainability grade from document signals.

    Runs after the DecisionAgent and does not influence the compliance decision.
    """

    def rate(
        self,
        shipment_data: dict,
        cert_result: Optional[CertificationScreeningResult],
        all_text: str,
        hts_chapters: list[str] | None = None,
        origin_iso2: str | None = None,
    ) -> SustainabilityRating:
        """Compute the sustainability grade for a screened shipment.

        Parameters
        ----------
        shipment_data:
            Extracted shipment dict from _extract_shipment_data().
        cert_result:
            Result from CertificationScreener.screen() — may be None if the
            screener was not run.
        all_text:
            Concatenated raw document text.
        hts_chapters:
            2-digit chapter strings.  Derived from shipment_data when None.
        origin_iso2:
            ISO2 country code.  Derived from shipment_data when None.
        """
        if hts_chapters is None:
            hts_codes = shipment_data.get("hts_codes_declared") or []
            hts_chapters = list({c[:2] for c in hts_codes if len(c) >= 2})

        if origin_iso2 is None:
            origin_iso2 = shipment_data.get("origin_country_iso2") or ""

        signals: list[str] = []
        computation_notes: list[str] = []

        # ------------------------------------------------------------------
        # N/A fast-path: all line items map to N/A product category
        # ------------------------------------------------------------------
        if not hts_chapters:
            # No HTS codes at all — treat as N/A (unknown goods type)
            return SustainabilityRating(
                grade="N/A",
                inherent_risk_level="N/A",
                country_risk_level="N/A",
                product_risk_level="N/A",
                certifications_detected=[],
                certifications_missing=[],
                signals=["No HTS codes declared — sustainability assessment not applicable."],
                computation_notes=["N/A fast-path: no HTS codes."],
            )

        product_risks = [_HTS_CHAPTER_RISK.get(ch, "MEDIUM") for ch in hts_chapters]
        if all(r == "N/A" for r in product_risks):
            return SustainabilityRating(
                grade="N/A",
                inherent_risk_level="N/A",
                country_risk_level="N/A",
                product_risk_level="N/A",
                certifications_detected=[],
                certifications_missing=[],
                signals=["All HTS chapters map to product categories with no applicable sustainability standards."],
                computation_notes=["N/A fast-path: all chapters are N/A product risk."],
            )

        # ------------------------------------------------------------------
        # Signal Group 2: Country risk
        # ------------------------------------------------------------------
        country_risk = _COUNTRY_SUSTAINABILITY_RISK.get(origin_iso2, "UNKNOWN") if origin_iso2 else "UNKNOWN"
        if country_risk == "HIGH":
            signals.append(f"Country of origin {origin_iso2} has HIGH inherent sustainability risk (deforestation, forced labor, or extraction concerns).")
        elif country_risk == "MEDIUM":
            signals.append(f"Country of origin {origin_iso2} has MEDIUM inherent sustainability risk.")
        elif country_risk == "LOW":
            signals.append(f"Country of origin {origin_iso2} has LOW inherent sustainability risk.")
        elif country_risk == "UNKNOWN":
            country_risk = "MEDIUM"  # treat unknown as medium for grade purposes
            signals.append("Country of origin unknown — treating as MEDIUM risk for sustainability assessment.")

        # ------------------------------------------------------------------
        # Signal Group 3: Product category risk (worst-case across chapters)
        # ------------------------------------------------------------------
        non_na_risks = [r for r in product_risks if r != "N/A"]
        _RISK_ORDER = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "N/A": 0}
        product_risk = max(non_na_risks, key=lambda r: _RISK_ORDER.get(r, 0)) if non_na_risks else "LOW"

        chapter_list = ", ".join(sorted(hts_chapters))
        if product_risk == "HIGH":
            signals.append(f"HTS chapter(s) {chapter_list} carry HIGH inherent sustainability risk (e.g., deforestation, forced labor, or extractive industry).")
        elif product_risk == "MEDIUM":
            signals.append(f"HTS chapter(s) {chapter_list} carry MEDIUM inherent sustainability risk.")
        else:
            signals.append(f"HTS chapter(s) {chapter_list} carry LOW inherent sustainability risk.")

        inherent_risk_levels = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        inherent_risk_num = max(
            inherent_risk_levels.get(country_risk, 2),
            inherent_risk_levels.get(product_risk, 2),
        )
        inherent_risk: str = {3: "HIGH", 2: "MEDIUM", 1: "LOW"}[inherent_risk_num]

        # ------------------------------------------------------------------
        # Signal Group 1: Detected certifications from cert_result
        # ------------------------------------------------------------------
        certs_detected: list[str] = []
        certs_missing: list[str] = []
        strong_cert_count = 0
        weak_cert_count = 0

        if cert_result:
            for finding in cert_result.findings:
                if finding.finding_type == "CERTIFICATION_DETECTED":
                    certs_detected.append(finding.module_name)
                    strong_cert_count += 1
                elif finding.finding_type == "DECLARATION_PRESENT":
                    certs_detected.append(f"{finding.module_name} (declaration only)")
                    weak_cert_count += 1
                elif finding.finding_type == "CERTIFICATION_MISSING":
                    certs_missing.append(finding.module_name)

        # Also do a secondary scan for sustainability cert keywords in raw text
        text_lower = all_text.lower()
        for cert_kw in _SUSTAINABILITY_CERT_KEYWORDS:
            if cert_kw.lower() in text_lower:
                label = f"{cert_kw} (mentioned in documents)"
                if label not in certs_detected:
                    weak_cert_count += 1
                    if len(certs_detected) < 10:
                        certs_detected.append(label)

        if strong_cert_count >= 2:
            cert_score = "STRONG"
            signals.append(f"{strong_cert_count} verified certification(s) detected in documents: {', '.join(certs_detected[:3])}.")
        elif strong_cert_count == 1:
            cert_score = "PRESENT"
            signals.append(f"1 verified certification detected: {certs_detected[0]}.")
        elif weak_cert_count > 0:
            cert_score = "WEAK"
            signals.append(f"Sustainability certification(s) mentioned in documents but no certificate numbers found.")
        else:
            cert_score = "ABSENT"
            signals.append("No sustainability certifications detected in submitted documents.")

        # ------------------------------------------------------------------
        # Signal Group 4: Supplier declaration text
        # ------------------------------------------------------------------
        declaration_found = any(pat.search(all_text) for pat in _DECLARATION_PATTERNS)
        if declaration_found and cert_score == "ABSENT":
            cert_score = "WEAK"
            signals.append("Supplier compliance declaration found in documents (weak signal — no certificate number).")

        # ------------------------------------------------------------------
        # Grade matrix (architecture doc §3.4)
        # ------------------------------------------------------------------
        grade: str
        if cert_score == "STRONG" and inherent_risk == "LOW":
            grade = "A"
        elif cert_score == "STRONG" and inherent_risk in ("MEDIUM", "HIGH"):
            grade = "B"
        elif cert_score == "PRESENT" and inherent_risk in ("LOW", "MEDIUM"):
            grade = "B"
        elif cert_score == "PRESENT" and inherent_risk == "HIGH":
            grade = "C"
        elif cert_score == "WEAK" and inherent_risk == "LOW":
            grade = "B"
        elif cert_score == "WEAK" and inherent_risk == "MEDIUM":
            grade = "C"
        elif cert_score == "WEAK" and inherent_risk == "HIGH":
            grade = "D"
        elif cert_score == "ABSENT" and inherent_risk == "LOW":
            grade = "B"
        elif cert_score == "ABSENT" and inherent_risk == "MEDIUM":
            grade = "C"
        elif cert_score == "ABSENT" and inherent_risk == "HIGH":
            grade = "D"
        else:
            grade = "C"  # safe default

        computation_notes.append(
            f"Grade {grade}: cert_score={cert_score}, inherent_risk={inherent_risk} "
            f"(country={country_risk}, product={product_risk})."
        )

        if certs_missing:
            signals.append(f"Expected certifications not found: {', '.join(certs_missing[:5])}.")

        return SustainabilityRating(
            grade=grade,
            inherent_risk_level=inherent_risk,
            country_risk_level=country_risk,
            product_risk_level=product_risk,
            certifications_detected=certs_detected,
            certifications_missing=certs_missing,
            signals=signals,
            computation_notes=computation_notes,
        )
