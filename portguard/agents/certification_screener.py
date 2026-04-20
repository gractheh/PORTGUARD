"""
portguard/agents/certification_screener.py — Stage 3.5 certification module screener.

Runs all enabled certification modules against the shipment's extracted data and
document text.  Layer 1 modules are always included regardless of enabled_module_ids.

Design constraints (from architecture doc §6.1):
- CertificationScreener findings feed into DecisionAgent as INFO/WARNING only.
- A missing certificate NEVER produces a HOLD or REJECT outcome.
- Only Layer 1 modules (already wired into RiskAgent) can produce HOLD/REJECT.
- All regex patterns are compiled from static module catalog at instantiation.
  No user input is ever compiled into a regex.
"""

from __future__ import annotations

import logging
import re

from portguard.data.certification_modules import (
    ALL_MODULES,
    MODULE_BY_ID,
    CertificationModule,
)
from portguard.models.certification import (
    CertificationScreeningResult,
    ModuleFinding,
)

logger = logging.getLogger(__name__)

# Evidence snippets are capped per pattern to avoid response bloat.
_MAX_EVIDENCE_SNIPPETS = 5
_EVIDENCE_CONTEXT_CHARS = 40


class CertificationScreener:
    """Stage 3.5: Run all enabled certification modules against a shipment.

    Parameters
    ----------
    enabled_module_ids:
        List of module IDs enabled for the current organization.  Layer 1
        modules (always_on=True) are always included regardless of this list.
    """

    def __init__(self, enabled_module_ids: list[str]) -> None:
        enabled_set = set(enabled_module_ids)
        self._modules: list[CertificationModule] = [
            m for m in ALL_MODULES
            if not m.toggleable or m.module_id in enabled_set
        ]
        # Pre-compile all regex patterns at construction time (not per-scan).
        self._compiled: dict[str, list[re.Pattern]] = {}
        for module in self._modules:
            patterns: list[re.Pattern] = []
            for pat in module.cert_number_patterns:
                try:
                    patterns.append(re.compile(pat, re.IGNORECASE))
                except re.error as exc:
                    logger.warning(
                        "Invalid regex pattern in module %s ('%s'): %s",
                        module.module_id, pat, exc
                    )
            self._compiled[module.module_id] = patterns

    def screen(
        self,
        shipment_data: dict,
        all_text: str,
        hts_chapters: list[str] | None = None,
        origin_iso2: str | None = None,
    ) -> CertificationScreeningResult:
        """Screen a shipment against all applicable enabled modules.

        Parameters
        ----------
        shipment_data:
            Dict from _extract_shipment_data() in app.py.
        all_text:
            Concatenated raw text from all submitted documents.
        hts_chapters:
            2-digit HTS chapter strings extracted from the shipment.
            When None, derived from shipment_data['hts_codes_declared'].
        origin_iso2:
            ISO2 country of origin.  When None, taken from shipment_data.
        """
        if hts_chapters is None:
            hts_codes = shipment_data.get("hts_codes_declared") or []
            hts_chapters = list({c[:2] for c in hts_codes if len(c) >= 2})

        if origin_iso2 is None:
            origin_iso2 = shipment_data.get("origin_country_iso2") or ""

        findings: list[ModuleFinding] = []
        triggered_modules: list[str] = []
        modules_run: list[str] = []

        for module in self._modules:
            if not self._is_applicable(module, hts_chapters):
                continue
            modules_run.append(module.module_id)
            module_findings = self._run_module(module, all_text, hts_chapters, origin_iso2)
            findings.extend(module_findings)
            if any(f.triggered for f in module_findings):
                triggered_modules.append(module.module_id)

        return CertificationScreeningResult(
            findings=findings,
            triggered_modules=triggered_modules,
            modules_run=modules_run,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_applicable(
        self, module: CertificationModule, hts_chapters: list[str]
    ) -> bool:
        """A module is applicable when it has no chapter restriction (universal)
        or when at least one shipment chapter appears in its chapter list."""
        if not module.applicable_hts_chapters:
            return True
        return any(ch in module.applicable_hts_chapters for ch in hts_chapters)

    def _run_module(
        self,
        module: CertificationModule,
        all_text: str,
        hts_chapters: list[str],
        origin_iso2: str,
    ) -> list[ModuleFinding]:
        """Evaluate one module and return its findings list."""
        findings: list[ModuleFinding] = []

        # 1. Scan for certification number patterns (STRONG signal)
        strong_hits = self._scan_cert_numbers(module, all_text)

        # 2. Scan for certification keyword mentions (WEAK signal)
        keyword_hits = self._scan_keywords(module, all_text)

        # 3. Country-of-origin risk check
        country_risk = origin_iso2 in module.risk_countries if origin_iso2 else False

        # Determine cert evidence level
        if strong_hits:
            cert_level = "STRONG"
            cert_evidence = strong_hits[:_MAX_EVIDENCE_SNIPPETS]
        elif keyword_hits:
            cert_level = "WEAK"
            cert_evidence = keyword_hits[:_MAX_EVIDENCE_SNIPPETS]
        else:
            cert_level = "ABSENT"
            cert_evidence = []

        # Skip Layer 1 modules from generating findings here — they're
        # already handled by the existing risk pipeline in app.py.
        if not module.toggleable:
            return findings

        # Build findings for toggleable modules
        if cert_level == "STRONG":
            findings.append(ModuleFinding(
                module_id=module.module_id,
                module_name=module.name,
                triggered=True,
                finding_type="CERTIFICATION_DETECTED",
                severity="INFO",
                message=(
                    f"{module.name} certification detected in shipment documents."
                ),
                evidence=cert_evidence,
                regulatory_reference="",
                remediation="",
            ))
        elif cert_level == "WEAK":
            findings.append(ModuleFinding(
                module_id=module.module_id,
                module_name=module.name,
                triggered=True,
                finding_type="DECLARATION_PRESENT",
                severity="INFO",
                message=(
                    f"{module.name} mentioned in documents but no certificate number "
                    "found — supplier declaration only, not verified certification."
                ),
                evidence=cert_evidence,
                regulatory_reference="",
                remediation=(
                    f"Request official {module.name} certificate with certificate number "
                    "from supplier to confirm compliance."
                ),
            ))
        elif cert_level == "ABSENT" and self._is_applicable(module, hts_chapters):
            # Certificate expected but not found
            severity = "WARNING" if country_risk else "INFO"
            chapter_str = ", ".join(sorted(set(hts_chapters) & set(module.applicable_hts_chapters))) if module.applicable_hts_chapters else "applicable"
            findings.append(ModuleFinding(
                module_id=module.module_id,
                module_name=module.name,
                triggered=True,
                finding_type="CERTIFICATION_MISSING",
                severity=severity,
                message=(
                    f"{module.name} not found for HTS chapter(s) {chapter_str or 'applicable'} — "
                    f"{module.description}"
                ),
                evidence=[],
                regulatory_reference="",
                remediation=(
                    f"Obtain {module.name} certification documentation from the supplier. "
                    f"{module.why_it_matters}"
                ),
            ))

        # Country-specific risk finding (additive)
        if country_risk and cert_level in ("ABSENT", "WEAK"):
            findings.append(ModuleFinding(
                module_id=module.module_id,
                module_name=f"{module.name} — Country Risk",
                triggered=True,
                finding_type="HIGH_RISK_COUNTRY",
                severity="WARNING",
                message=(
                    f"{origin_iso2} is a flagged country for {module.name} compliance risk. "
                    f"Certification documentation is especially important for this origin."
                ),
                evidence=[],
                regulatory_reference="",
                remediation=(
                    f"Prioritize obtaining verified {module.name} certification from "
                    f"suppliers in {origin_iso2}."
                ),
            ))

        return findings

    def _scan_cert_numbers(
        self, module: CertificationModule, text: str
    ) -> list[str]:
        """Return evidence snippets for strong (cert-number) pattern hits."""
        compiled = self._compiled.get(module.module_id, [])
        hits: list[str] = []
        for pattern in compiled:
            for m in pattern.finditer(text):
                start = max(0, m.start() - _EVIDENCE_CONTEXT_CHARS)
                end = min(len(text), m.end() + _EVIDENCE_CONTEXT_CHARS)
                snippet = text[start:end].replace("\n", " ").strip()
                hits.append(snippet)
                if len(hits) >= _MAX_EVIDENCE_SNIPPETS:
                    return hits
        return hits

    def _scan_keywords(
        self, module: CertificationModule, text: str
    ) -> list[str]:
        """Return evidence snippets for keyword mentions (weak signal)."""
        hits: list[str] = []
        text_lower = text.lower()
        for keyword in module.applicable_keywords:
            kw_lower = keyword.lower()
            idx = text_lower.find(kw_lower)
            if idx != -1:
                start = max(0, idx - _EVIDENCE_CONTEXT_CHARS)
                end = min(len(text), idx + len(keyword) + _EVIDENCE_CONTEXT_CHARS)
                snippet = text[start:end].replace("\n", " ").strip()
                hits.append(snippet)
                if len(hits) >= _MAX_EVIDENCE_SNIPPETS:
                    break
        return hits
