# PORTGUARD

US import compliance screening system. Accepts raw shipping documents or structured shipment data and returns a risk score, compliance decision, and actionable findings — powered by Claude claude-opus-4-6.

---

## What it does

PORTGUARD screens import shipments for:

- **OFAC sanctions** — comprehensive embargoes (Iran, Cuba, North Korea, Syria) and sectoral programs (Russia, Belarus, Venezuela, Myanmar, and others)
- **Section 301 tariffs** — 333 HTS prefixes across all four China lists (Lists 1/2/3 at 25%, List 4A at 7.5%)
- **Section 232 steel and aluminum** — 25% steel (Chapters 72–73), 10% aluminum (Chapter 76)
- **Antidumping and countervailing duty orders** — 14 active orders across CN, DE, KR, MX, VN
- **ISF completeness** — all 10 importer-provided data elements per 19 CFR 149
- **PGA requirements** — 72 HTS chapters mapped to FDA, USDA, FTC, CPSC, FCC, NHTSA, ATF, EPA, TTB, Commerce
- **UFLPA** — Uyghur Forced Labor Prevention Act rebuttable presumption for China-origin goods in high-risk categories
- **Document inconsistencies** — cross-document mismatches in shipper names, origin country, declared value, HTS codes

---

## Architecture

There are two independent entry points that share the same Claude API key and `docs/prompts.md` system prompt.

### Entry point 1 — Document analysis API (`api/app.py`)

Stateless. Accepts raw document text, returns a single structured screening result.

```
POST /api/v1/analyze
     |
     +-- Combines all document text into one prompt
     +-- Calls Claude claude-opus-4-6 via tool_use (structured JSON output)
     +-- Returns decision, risk score, explanations, next steps
```

### Entry point 2 — Structured screening pipeline (`portguard/`)

Stateful per-request pipeline. Accepts structured shipment data (importer, line items, values), runs five sequential agents, returns a full screening report.

```
POST /api/v1/screen
     |
     +-- ParserAgent      extract + normalize to ParsedShipment
     +-- ClassifierAgent  GRI 1-6 -> 10-digit HTSUS codes + duty rates
     +-- ValidationAgent  ISF check + PGA lookup + Claude regulatory review
     +-- RiskAgent        rule-based checks (301/232/ADCVD/sanctions/UFLPA)
                          + Claude expert analysis for transshipment, export controls
     +-- DecisionAgent    rule-based decision floor + Claude narrative synthesis
     +-- ScreeningReport  stored in memory, retrievable by report_id
```

```
portguard/
  config.py               pydantic-settings: ANTHROPIC_API_KEY, model, max_tokens
  data/
    section301.py         333 HTS prefixes, Lists 1-4 (get_section_301)
    sanctions.py          10 OFAC programs (get_sanctions_programs)
    adcvd.py              14 AD/CVD orders (get_adcvd_orders)
    pga.py                72 HTS chapters -> PGA requirements
  models/
    shipment.py           ShipmentInput, ParsedShipment, LineItem*
    classification.py     ClassificationResult, HTSLineClassification, GRIAnalysis
    validation.py         ValidationResult, ValidationFinding, FindingSeverity
    risk.py               RiskAssessment, RiskFactor, RiskType, RiskSeverity
    decision.py           ComplianceDecision, DecisionLevel, RequiredAction
    report.py             ScreeningReport
  agents/
    base.py               BaseAgent: AsyncAnthropic + _call_structured (tool_use)
    parser.py             ParserAgent
    classifier.py         ClassifierAgent
    validator.py          ValidationAgent
    risk.py               RiskAgent
    decision.py           DecisionAgent
    orchestrator.py       OrchestratorAgent (runs the pipeline)
  api/
    main.py               FastAPI app (portguard pipeline)
    routes.py             /health /screen /parse /classify /assess-risk /reports/{id}

api/
  app.py                  FastAPI app (document analysis, stateless)

main.py                   CLI runner: runs all 3 test scenarios and prints results
```

---

## AD/CVD orders covered

| Case | Type | Country | Product |
|---|---|---|---|
| A-570-029 | AD | CN | Cold-Rolled Steel Flat Products (265.79%) |
| A-570-028 | AD | CN | Hot-Rolled Steel Flat Products (199.43%) |
| A-570-967 | AD | CN | Aluminum Extrusions (374.15%) |
| A-570-979 | AD | CN | Crystalline Silicon Photovoltaic Cells (238.95%) |
| A-570-106 | AD | CN | Wooden Cabinets and Vanities (262.18%) |
| A-570-116 | AD | CN | Hardwood Plywood (183.36%) |
| C-570-116 | CVD | CN | Hardwood Plywood (22.98%) |
| A-570-099 | AD | CN | Carbon and Alloy Steel Wire Rod (110.25%) |
| A-570-918 | AD | CN | Prestressed Concrete Steel Wire Strand |
| A-570-601 | AD | CN | Wooden Bedroom Furniture (216.01%) |
| A-428-830 | AD | DE | Hot-Rolled Steel Flat Products (3.44%) |
| A-580-883 | AD | KR | Cold-Rolled Steel Flat Products (6.32%) |
| A-201-848 | AD | MX | Cold-Rolled Steel Flat Products (7.68%) |
| A-552-818 | AD | VN | Steel Nails (323.99%) |

---

## Setup

**Requirements:** Python 3.11+, an Anthropic API key with credits.

```bash
# 1. Clone and enter the directory
git clone https://github.com/gractheh/PORTGUARD.git
cd PORTGUARD

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

**.env.example**
```
ANTHROPIC_API_KEY=your_anthropic_api_key_here
PORTGUARD_MODEL=claude-opus-4-6
PORTGUARD_MAX_TOKENS=4096
```

---

## Running

### Demo — run all 3 test scenarios from the terminal

```bash
python main.py
```

Runs the three sample scenarios in `tests/sample_documents/` against the document analysis API and prints results:

```
======================================================================
  PORTGUARD - Trade Compliance Screening
  Running 3 scenarios against POST /api/v1/analyze
======================================================================

  Running scenario 1: Clean Shipment...

======================================================================
Scenario 1: Clean Shipment  (Vietnamese laptop manufacturer -- all documents consistent)
----------------------------------------------------------------------
  Decision   : APPROVE
  Risk level : LOW  (score 0.04)
  Confidence : HIGH
  Docs read  : 4   Inconsistencies: 0   Time: 4.21s

  Importer  : Horizon Technology Group, Inc.
  Origin    : Vietnam
  Commodity : Laptop computers, 15.6" FHD, Intel Core i5
  Value     : USD 208,000.00

  Findings:
    - No sanctions exposure: Vietnam is not subject to OFAC restrictions
    - HTS 8471.30.0100 correctly declared; duty-free under ITA
    - All four documents (B/L, invoice, packing list, CO) are consistent
    - Declared value $52/unit is within normal range for mid-range laptops

  Recommended next steps:
    1. File ISF 24 hours prior to vessel loading
    2. Confirm FCC equipment authorization for US market
    3. Standard consumption entry (CBP Form 7501)

  [PASS] matches expected

======================================================================
Scenario 2: Suspicious Shipment  (Semiconductor ICs -- shipper mismatch, transshipment...)
----------------------------------------------------------------------
  Decision   : FLAG_FOR_INSPECTION
  Risk level : HIGH  (score 0.83)
  Confidence : HIGH
  Docs read  : 3   Inconsistencies: 5   Time: 5.14s

  Importer  : NexGen Components Inc.
  Origin    : Malaysia (declared) / China (port of loading)
  Commodity : Monolithic Integrated Circuit Boards, memory type
  Value     : USD 6,800.00

  Findings:
    - Shipper on B/L (Guangzhou Apex Trading Ltd, China) does not match
      invoice seller (Sunrise Global Exports Pte Ltd, Singapore)
    - Invoice declares country of origin as Malaysia; port of loading is
      Yantian, Shenzhen, China -- classic transshipment pattern
    - Declared value $0.85/unit for semiconductor ICs is 80-90% below
      market rate ($4-12/unit); probable undervaluation
    - HTS 8542.31.0000 from China subject to Section 301 List 3 at 25%
    - No manufacturer identified on any document

  Recommended next steps:
    1. Request CF-28 (Request for Information) for proof of origin
    2. Refer to CBP for suspected transshipment investigation
    3. Do not release pending origin verification
    4. Obtain producer/exporter-specific AD/CVD rate if CN origin confirmed

  [PASS] matches expected

======================================================================
Scenario 3: Incomplete Shipment  (Frozen shrimp -- missing FDA Prior Notice...)
----------------------------------------------------------------------
  Decision   : REQUEST_MORE_INFORMATION
  Risk level : MEDIUM  (score 0.61)
  Confidence : HIGH
  Docs read  : 2   Inconsistencies: 8   Time: 4.87s

  Importer  : American Seafood Distributors
  Origin    : Bangladesh (inferred from shipper address)
  Commodity : Frozen Shrimp, shell-on, headless, IQF
  Value     : USD 32,000.00

  Findings:
    - FDA Prior Notice not filed (mandatory under 21 CFR 1.279 for all
      imported food; CBP will detain shipment automatically)
    - Country of origin not stated on commercial invoice
    - HTS provided at 6-digit level only (0306.17); 10-digit required
    - Manufacturer and processing plant not identified (ISF element 5)
    - Importer of Record number absent (ISF element 3)
    - No USDA NMFS inspection certificate or health certificate
    - B/L is water-damaged and partially illegible
    - Currency not specified on invoice ($ symbol only)

  Recommended next steps:
    1. File FDA Prior Notice immediately via FDA's Prior Notice System
       Interface (PNSI) before vessel arrival
    2. Obtain complete commercial invoice with country of origin and
       10-digit HTS subheading
    3. Identify processing facility and obtain HACCP documentation
    4. File complete ISF with all 10 data elements

  [PASS] matches expected

======================================================================
  Results: 3/3 matched expected decisions
======================================================================
```

*(Actual output depends on Claude's analysis; decisions are driven by document content.)*

### Document analysis API

```bash
uvicorn api.app:app --reload --port 8000
```

```bash
# Health check
curl http://localhost:8000/api/v1/health

# Screen a shipment
curl -X POST http://localhost:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d @tests/sample_documents/02_suspicious_shipment.json
```

**Request format:**
```json
{
  "documents": [
    {
      "raw_text": "BILL OF LADING\nShipper: ...",
      "filename": "bill_of_lading.txt"
    },
    {
      "raw_text": "COMMERCIAL INVOICE\n...",
      "filename": "commercial_invoice.txt"
    }
  ]
}
```

**Response format:**
```json
{
  "status": "completed",
  "shipment_data": {
    "importer": "NexGen Components Inc.",
    "exporter": "Sunrise Global Exports Pte. Ltd.",
    "origin_country": "Malaysia",
    "origin_country_iso2": "MY",
    "commodity_description": "Monolithic Integrated Circuit Boards",
    "hts_codes_declared": ["8542.31.0000"],
    "declared_value": "6800.00",
    "declared_currency": "USD"
  },
  "risk_score": 0.83,
  "risk_level": "HIGH",
  "decision": "FLAG_FOR_INSPECTION",
  "confidence": "HIGH",
  "explanations": [
    "Shipper on B/L differs from invoice seller",
    "Port of loading (Yantian, China) inconsistent with declared origin (Malaysia)",
    "Declared value $0.85/unit is 80-90% below market rate"
  ],
  "recommended_next_steps": [
    "Request CF-28 for proof of origin",
    "Refer to CBP for transshipment investigation"
  ],
  "inconsistencies_found": 5,
  "documents_analyzed": 3,
  "processing_time_seconds": 5.14
}
```

**Decision values:**

| Decision | Meaning |
|---|---|
| `APPROVE` | No material issues — standard processing |
| `REVIEW_RECOMMENDED` | Minor issues — proceed with review |
| `FLAG_FOR_INSPECTION` | Significant red flags — physical or documentary inspection required |
| `REQUEST_MORE_INFORMATION` | Critical documentation gaps — cannot process until resolved |
| `REJECT` | Prohibited transaction — OFAC sanctions or embargo |

### Structured pipeline API

```bash
uvicorn portguard.api.main:app --reload --port 8001
```

**Endpoints:**

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/health` | Liveness check |
| POST | `/api/v1/screen` | Full 5-agent pipeline; stores report |
| POST | `/api/v1/parse` | ParserAgent only |
| POST | `/api/v1/classify` | ClassifierAgent only (accepts ParsedShipment) |
| POST | `/api/v1/assess-risk` | RiskAgent only (accepts parsed + classification) |
| GET | `/api/v1/reports/{id}` | Retrieve stored report by ID |

Interactive docs at `http://localhost:8001/docs`.

### Tests

```bash
pytest
```

33 tests across 7 files. All tests mock `BaseAgent._call_structured` — no real API calls are made during testing.

```
tests/test_api.py           7 tests   FastAPI routes (httpx AsyncClient)
tests/test_parser.py        4 tests   ParserAgent
tests/test_classifier.py    4 tests   ClassifierAgent
tests/test_validator.py     4 tests   ValidationAgent
tests/test_risk.py          5 tests   RiskAgent (Section 301, OFAC, AD/CVD, Section 232)
tests/test_decision.py      5 tests   DecisionAgent (CLEAR/REVIEW/HOLD/REJECT logic)
tests/test_orchestrator.py  4 tests   Pipeline fault tolerance
```

---

## Test scenarios

Three sample request payloads in `tests/sample_documents/`:

| File | Scenario | Documents | Key issues |
|---|---|---|---|
| `01_clean_shipment.json` | Vietnamese laptops | B/L, invoice, packing list, certificate of origin | None — all consistent |
| `02_suspicious_shipment.json` | Chinese semiconductor ICs via Singapore | B/L, invoice, packing list | Shipper mismatch, transshipment, undervaluation, Section 301 |
| `03_incomplete_shipment.json` | Frozen shrimp from Bangladesh | Partial B/L, incomplete invoice | Missing FDA Prior Notice, no origin, absent ISF elements |

---

## Limitations

- **No live regulatory data.** Section 301, AD/CVD rates, and OFAC SDN list are embedded as static reference data. Rates and orders may have changed since last update.
- **Reports are in-memory only.** The structured pipeline stores reports in a Python dict. Reports are lost on process restart.
- **No authentication.** Neither API has request authentication.
- **Single-process only.** The in-memory report store is not shared across workers.
- **Claude's classification is not legally binding.** HTS classifications and duty rates produced by ClassifierAgent are for preliminary screening. A licensed customs broker must file the actual entry.
