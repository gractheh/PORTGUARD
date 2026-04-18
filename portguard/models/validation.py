"""Validation result models."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel


class FindingSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ValidationFinding(BaseModel):
    code: str
    severity: FindingSeverity
    field: str | None = None
    message: str
    regulatory_reference: str | None = None
    remediation: str


class ValidationResult(BaseModel):
    findings: list[ValidationFinding]
    pga_requirements: list[str]
    isf_complete: bool
    marking_compliant: bool | None = None
    validation_notes: list[str] = []
