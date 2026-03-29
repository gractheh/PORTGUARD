"""HTS classification result models."""

from pydantic import BaseModel


class GRIAnalysis(BaseModel):
    primary_gri: str
    secondary_gri: str | None = None
    rationale: str


class HTSLineClassification(BaseModel):
    line_number: int
    hts_code: str           # 10-digit HTSUS e.g. "8471.30.0100"
    hts_description: str
    duty_rate_general: str
    duty_rate_special: str | None = None
    gri_analysis: GRIAnalysis
    confidence: float
    classification_notes: str | None = None


class ClassificationResult(BaseModel):
    classifications: list[HTSLineClassification]
    classifier_notes: list[str] = []
