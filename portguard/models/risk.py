"""Risk assessment models."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel


class RiskType(str, Enum):
    SECTION_301 = "SECTION_301"
    SECTION_232 = "SECTION_232"
    SECTION_201 = "SECTION_201"
    ANTIDUMPING = "ANTIDUMPING"
    COUNTERVAILING = "COUNTERVAILING"
    OFAC_SANCTIONS = "OFAC_SANCTIONS"
    EXPORT_CONTROL = "EXPORT_CONTROL"
    DENIED_PARTY = "DENIED_PARTY"
    FORCED_LABOR = "FORCED_LABOR"
    VALUATION = "VALUATION"
    OTHER = "OTHER"


class RiskSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskFactor(BaseModel):
    risk_type: RiskType
    severity: RiskSeverity
    hts_code: str | None = None
    country: str | None = None
    entity: str | None = None
    description: str
    additional_duty_rate: str | None = None
    order_number: str | None = None
    regulatory_reference: str
    recommended_action: str


class RiskAssessment(BaseModel):
    risk_factors: list[RiskFactor]
    overall_risk_level: RiskSeverity
    estimated_additional_duties_usd: float | None = None
    risk_notes: list[str] = []
