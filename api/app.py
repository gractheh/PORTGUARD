"""
PORTGUARD Analyze API
POST /api/v1/analyze  — stateless document screening via Claude
GET  /api/v1/health   — liveness check
"""

import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

load_dotenv()

# ---------------------------------------------------------------------------
# System prompt — loaded once at startup from docs/prompts.md
# ---------------------------------------------------------------------------

_PROMPTS_PATH = Path(__file__).parent.parent / "docs" / "prompts.md"

def _load_system_prompt() -> str:
    text = _PROMPTS_PATH.read_text(encoding="utf-8")
    parts = text.split("## System Prompt\n\n", maxsplit=1)
    if len(parts) < 2:
        raise RuntimeError("docs/prompts.md is missing the '## System Prompt' section")
    return parts[1].strip()

SYSTEM_PROMPT = _load_system_prompt()

# Append analysis-mode instructions on top of the base persona
SYSTEM_PROMPT += """

---

## Document Analysis Mode

When called via the API you receive one or more raw shipping documents (bills of lading,
commercial invoices, packing lists, certificates of origin, etc.) and must return a
structured compliance screening result.

For each analysis you must:

1. **Extract shipment data** — importer, exporter, consignee, origin country, destination,
   port of entry, commodity descriptions, HS/HTS codes declared, quantities, declared values,
   incoterms, vessel/flight details, dates. Consolidate across all provided documents.

2. **Identify inconsistencies** — flag any of the following:
   - Declared value appears artificially low for the commodity type and quantity
   - Shipper, consignee, or notify party names differ across documents
   - Country of origin on invoice differs from bill of lading
   - HTS code inconsistent with the product description
   - Missing required fields (ISF elements, marks and numbers, country of origin marking)
   - Date anomalies (ship date after arrival date, etc.)
   - Weight/quantity discrepancies between documents
   - Vague or generic product descriptions that obscure classification

3. **Assess trade compliance risk** based on:
   - Origin country (OFAC sanctions: Iran/CU/KP/SY = CRITICAL; Russia/Belarus = HIGH)
   - Section 301 / AD/CVD exposure (Chinese-origin goods in industrial/consumer categories)
   - Potential undervaluation (declared value < typical market value for commodity)
   - Misclassification risk (declared HTS doesn't match description)
   - Missing documentation (no certificate of origin for FTA claim, no FDA prior notice, etc.)
   - Transshipment indicators (origin country inconsistent with routing)
   - Denied party / entity screening flags (known state-owned entities, flagged names)

4. **Score the risk** from 0.0 (no risk) to 1.0 (prohibited transaction):
   - 0.00–0.25: LOW   — proceed, standard processing
   - 0.26–0.50: MEDIUM — review recommended
   - 0.51–0.75: HIGH  — flag for inspection
   - 0.76–1.00: CRITICAL — hold/reject

5. **Make a decision**:
   - APPROVE                  — no material issues, shipment may proceed
   - REVIEW_RECOMMENDED       — minor issues, proceed with review
   - FLAG_FOR_INSPECTION      — significant compliance issues, physical or documentary inspection required
   - REQUEST_MORE_INFORMATION — critical documentation gaps; shipment cannot be processed until resolved
   - REJECT                   — prohibited transaction (sanctions, embargo), do not import

   Use REQUEST_MORE_INFORMATION specifically when the shipment cannot be evaluated because required
   documents are absent or incomplete (e.g. missing FDA Prior Notice, no country of origin, absent
   ISF elements, illegible/missing bill of lading). Use FLAG_FOR_INSPECTION when documents are
   present but contain red flags, mismatches, or suspected fraud.

6. **Provide explanations** — plain-English list of specific findings driving the risk score.

7. **Recommend next steps** — concrete, actionable items for the broker/importer.
"""

# ---------------------------------------------------------------------------
# Anthropic client — singleton
# ---------------------------------------------------------------------------

_client = anthropic.Anthropic()

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class Document(BaseModel):
    raw_text: str = Field(..., description="Full raw text of the shipping document")
    filename: str | None = Field(None, description="Original filename, used for document type hints")


class AnalyzeRequest(BaseModel):
    documents: list[Document] = Field(..., min_length=1, description="One or more shipping documents to analyze")


class ShipmentData(BaseModel):
    importer: str | None = None
    exporter: str | None = None
    consignee: str | None = None
    notify_party: str | None = None
    origin_country: str | None = None
    origin_country_iso2: str | None = None
    destination_country: str | None = None
    port_of_loading: str | None = None
    port_of_discharge: str | None = None
    port_of_entry: str | None = None
    vessel_or_flight: str | None = None
    bill_of_lading_number: str | None = None
    shipment_date: str | None = None
    arrival_date: str | None = None
    incoterms: str | None = None
    commodity_description: str | None = None
    hts_codes_declared: list[str] = []
    quantity: str | None = None
    gross_weight: str | None = None
    declared_value: str | None = None
    declared_currency: str | None = None
    marks_and_numbers: str | None = None


class AnalyzeResponse(BaseModel):
    status: str
    shipment_data: ShipmentData
    risk_score: float = Field(..., ge=0.0, le=1.0)
    risk_level: str
    decision: str
    confidence: str
    explanations: list[str]
    recommended_next_steps: list[str]
    inconsistencies_found: int
    documents_analyzed: int
    processing_time_seconds: float


# ---------------------------------------------------------------------------
# Tool schema — forces Claude to return structured JSON
# ---------------------------------------------------------------------------

_ANALYZE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "shipment_data": {
            "type": "object",
            "description": "Structured data extracted and consolidated from all documents",
            "properties": {
                "importer":             {"type": ["string", "null"]},
                "exporter":             {"type": ["string", "null"]},
                "consignee":            {"type": ["string", "null"]},
                "notify_party":         {"type": ["string", "null"]},
                "origin_country":       {"type": ["string", "null"]},
                "origin_country_iso2":  {"type": ["string", "null"], "description": "ISO 3166-1 alpha-2"},
                "destination_country":  {"type": ["string", "null"]},
                "port_of_loading":      {"type": ["string", "null"]},
                "port_of_discharge":    {"type": ["string", "null"]},
                "port_of_entry":        {"type": ["string", "null"]},
                "vessel_or_flight":     {"type": ["string", "null"]},
                "bill_of_lading_number":{"type": ["string", "null"]},
                "shipment_date":        {"type": ["string", "null"]},
                "arrival_date":         {"type": ["string", "null"]},
                "incoterms":            {"type": ["string", "null"]},
                "commodity_description":{"type": ["string", "null"]},
                "hts_codes_declared":   {"type": "array", "items": {"type": "string"}},
                "quantity":             {"type": ["string", "null"]},
                "gross_weight":         {"type": ["string", "null"]},
                "declared_value":       {"type": ["string", "null"]},
                "declared_currency":    {"type": ["string", "null"]},
                "marks_and_numbers":    {"type": ["string", "null"]},
            },
        },
        "risk_score": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Composite risk score: 0.0 = no risk, 1.0 = prohibited transaction",
        },
        "risk_level": {
            "type": "string",
            "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
            "description": "Risk band derived from risk_score",
        },
        "decision": {
            "type": "string",
            "enum": ["APPROVE", "REVIEW_RECOMMENDED", "FLAG_FOR_INSPECTION", "REQUEST_MORE_INFORMATION", "REJECT"],
            "description": "Recommended compliance action",
        },
        "confidence": {
            "type": "string",
            "enum": ["LOW", "MEDIUM", "HIGH"],
            "description": "Confidence in the decision given available documentation",
        },
        "explanations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Plain-English list of specific findings driving the risk score",
            "minItems": 1,
        },
        "recommended_next_steps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concrete, actionable next steps for the broker or importer",
            "minItems": 1,
        },
        "inconsistencies_found": {
            "type": "integer",
            "minimum": 0,
            "description": "Total number of distinct inconsistencies or red flags identified",
        },
    },
    "required": [
        "shipment_data",
        "risk_score",
        "risk_level",
        "decision",
        "confidence",
        "explanations",
        "recommended_next_steps",
        "inconsistencies_found",
    ],
}


# ---------------------------------------------------------------------------
# Core analysis function
# ---------------------------------------------------------------------------

def _build_user_prompt(documents: list[Document]) -> str:
    parts = [
        f"Analyze the following {len(documents)} shipping document(s) for US import compliance.\n"
    ]
    for i, doc in enumerate(documents, 1):
        label = doc.filename or f"Document {i}"
        parts.append(f"---\n## Document {i}: {label}\n\n{doc.raw_text.strip()}\n")
    parts.append(
        "---\nExtract all shipment data, identify inconsistencies, assess compliance risk, "
        "and return a structured screening result."
    )
    return "\n".join(parts)


def _call_claude(documents: list[Document]) -> dict:
    prompt = _build_user_prompt(documents)
    response = _client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=[
            {
                "name": "record_screening_result",
                "description": (
                    "Record the structured compliance screening result for the provided "
                    "shipping documents, including extracted shipment data, risk assessment, "
                    "decision, and actionable findings."
                ),
                "input_schema": _ANALYZE_TOOL_SCHEMA,
            }
        ],
        tool_choice={"type": "tool", "name": "record_screening_result"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Claude returned no tool_use block")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="PORTGUARD Document Analysis API",
    description="Stateless trade compliance screening for shipping documents",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/demo", include_in_schema=False)
def serve_demo():
    demo_path = Path(__file__).parent.parent / "demo.html"
    return FileResponse(demo_path, media_type="text/html")


@app.get("/api/v1/health")
def health():
    return {
        "status": "ok",
        "model": "claude-opus-4-6",
        "service": "portguard-analyze",
    }


@app.post("/api/v1/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest):
    start = time.monotonic()

    try:
        result = _call_claude(request.documents)
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=401, detail="Invalid Anthropic API key")
    except anthropic.RateLimitError:
        raise HTTPException(status_code=429, detail="Anthropic rate limit exceeded — retry after a moment")
    except anthropic.APIStatusError as e:
        raise HTTPException(status_code=502, detail=f"Anthropic API error: {e.message}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    elapsed = round(time.monotonic() - start, 3)

    shipment_raw = result.get("shipment_data", {})

    return AnalyzeResponse(
        status="completed",
        shipment_data=ShipmentData(**shipment_raw),
        risk_score=result["risk_score"],
        risk_level=result["risk_level"],
        decision=result["decision"],
        confidence=result["confidence"],
        explanations=result["explanations"],
        recommended_next_steps=result["recommended_next_steps"],
        inconsistencies_found=result["inconsistencies_found"],
        documents_analyzed=len(request.documents),
        processing_time_seconds=elapsed,
    )
