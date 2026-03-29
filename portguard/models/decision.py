"""Compliance decision models."""

from enum import Enum
from pydantic import BaseModel


class DecisionLevel(str, Enum):
    CLEAR = "CLEAR"
    REVIEW = "REVIEW"
    HOLD = "HOLD"
    REJECT = "REJECT"


class RequiredAction(BaseModel):
    priority: int
    action: str
    responsible_party: str
    deadline: str | None = None
    regulatory_reference: str | None = None


class ComplianceDecision(BaseModel):
    decision: DecisionLevel
    confidence: float
    summary: str
    key_findings: list[str]
    required_actions: list[RequiredAction]
    estimated_base_duties_usd: float | None = None
    estimated_additional_duties_usd: float | None = None
    estimated_total_duties_usd: float | None = None
    decision_rationale: str
