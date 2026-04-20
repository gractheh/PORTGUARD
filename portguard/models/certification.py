"""
portguard/models/certification.py — Pydantic models for certification screening
and sustainability rating results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# ModuleFinding — one finding produced by a single certification module
# ---------------------------------------------------------------------------

class ModuleFinding(BaseModel):
    module_id: str
    module_name: str
    triggered: bool
    finding_type: Literal[
        "CERTIFICATION_MISSING",
        "CERTIFICATION_DETECTED",
        "HIGH_RISK_PRODUCT",
        "HIGH_RISK_COUNTRY",
        "PATTERN_MATCH",
        "DECLARATION_PRESENT",
    ]
    severity: Literal["INFO", "WARNING", "ERROR", "CRITICAL"]
    message: str
    evidence: list[str] = []           # matched text snippets / pattern hits
    regulatory_reference: str = ""
    remediation: str = ""


# ---------------------------------------------------------------------------
# CertificationScreeningResult — aggregate output of CertificationScreener
# ---------------------------------------------------------------------------

class CertificationScreeningResult(BaseModel):
    findings: list[ModuleFinding] = []
    triggered_modules: list[str] = []   # module IDs that produced ≥1 finding
    modules_run: list[str] = []         # module IDs that were evaluated


# ---------------------------------------------------------------------------
# SustainabilityRating — computed sustainability grade for a shipment
# ---------------------------------------------------------------------------

class SustainabilityRating(BaseModel):
    grade: Literal["A", "B", "C", "D", "N/A"]
    inherent_risk_level: Literal["LOW", "MEDIUM", "HIGH", "N/A"]
    country_risk_level: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN", "N/A"]
    product_risk_level: Literal["LOW", "MEDIUM", "HIGH", "N/A"]
    certifications_detected: list[str] = []    # cert names/numbers found
    certifications_missing: list[str] = []     # expected certs not found
    signals: list[str] = []                    # plain-English explanation strings
    computation_notes: list[str] = []          # how the grade was reached
