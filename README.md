# PORTGUARD

US import compliance screening system. Accepts raw shipping documents or structured shipment data and returns a risk score, compliance decision, and actionable findings — 100% rule-based, no external services required.

---

## What it does

PORTGUARD screens import shipments for:

- **OFAC sanctions** — 4 comprehensive embargoes (Cuba, Iran, North Korea, Syria) and 6 sectoral programs (Russia, Belarus, Venezuela, Myanmar, Central African Republic, Zimbabwe)
- **Section 301 tariffs** — 333 HTS prefixes across all four China lists (Lists 1/2/3 at 25%, List 4A at 7.5%), longest-prefix matching with overlap resolution
- **Section 232 steel and aluminum** — 25% steel (chapters 72–73), 10% aluminum (chapter 76)
- **Antidumping and countervailing duty orders** — 14 active orders across CN, DE, KR, MX, VN
- **UFLPA** — Uyghur Forced Labor Prevention Act rebuttable presumption for China-origin goods in 10 high-risk categories (cotton, polysilicon, steel, textiles, apparel, aluminum, solar, batteries, chemicals, tomatoes)
- **ISF completeness** — 4 verifiable data elements per 19 CFR 149 (importer, exporter, country of origin, HTS code)
- **PGA requirements** — 72 HTS chapters mapped to 14 agencies: FDA, USDA (FSIS/APHIS/AMS/NMFS), FTC, CPSC, FCC, NHTSA, ATF, EPA, TTB, Commerce, NRC, FAA, FRA, US Fish & Wildlife
- **Document inconsistencies** — cross-document mismatches in shipper/seller names (token similarity), origin country, declared value, and port of loading vs. origin country
- **Undervaluation** — declared unit value compared against benchmarks for 5 commodity types (ICs, laptops, smartphones, solar panels, flat-panel displays)

---

## Architecture

Two independent entry points share the same underlying compliance data modules.

### Entry point 1 — Document analysis API (`api/app.py`)

Stateless. Accepts one or more raw document texts with filenames, extracts fields via regex, runs rule-based risk scoring, and returns a single structured screening result.

```
POST /api/v1/analyze
     |
     +-- _extract_shipment_data()
     |     importer, exporter, origin, port of loading, port of discharge,
     |     vessel, B/L number, date, incoterms, HTS codes, value, quantity
     |
     +-- _find_inconsistencies()
     |     shipper vs. seller token similarity (< 0.35 = mismatch)
     |     origin country consistency across documents
     |     port-of-loading city -> ISO2 vs. declared origin (transshipment)
     |     value discrepancy across documents (> 2.5x variance)
     |
     +-- _check_missing_fields()
     |     origin absent or "NOT STATED"; HTS at 6-digit only
     |     importer/consignee not identified; manufacturer absent
     |     currency not specified; illegible/water-damaged documents
     |     FDA Prior Notice absent for food; USDA certificate absent for seafood
     |     ISF importer of record number absent
     |
     +-- _assess_risk()
     |     OFAC sanctions                   -- CRITICAL / HIGH
     |     transshipment + Section 301      -- HIGH
     |     Section 301 (direct CN origin)   -- HIGH
     |     AD/CVD orders                    -- HIGH
     |     undervaluation vs. benchmarks    -- HIGH / MEDIUM
     |     vague B/L cargo description      -- MEDIUM
     |     negotiable B/L (TO ORDER)        -- LOW
     |     Russia/Belarus sectoral sanctions -- HIGH
     |
     +-- _compute_score()
     |     weighted sum capped at 1.0
     |     (sanctions 0.90 | transshipment 0.28 | sectoral 0.30
     |      Section 301 0.22 | AD/CVD 0.20 | undervaluation 0.18/0.10
     |      vague description 0.08 | negotiable B/L 0.04
     |      + 0.06 per missing field + 0.06 per inconsistency)
     |
     +-- _make_decision()
           REJECT               -- comprehensive OFAC sanctions
           REQUEST_MORE_INFO    -- 3+ critical documentation gaps
           FLAG_FOR_INSPECTION  -- transshipment + mismatch, or score > 0.50
           REVIEW_RECOMMENDED   -- score > 0.20 or any MEDIUM risk factor
           APPROVE              -- no material issues
```

Additional endpoints:
- `GET /api/v1/health` — liveness check
- `POST /api/v1/extract-text` — upload a `.pdf` or `.txt` file; returns extracted text, page count, and any warnings
- `GET /demo` — browser UI (`demo.html`) with 3 pre-loaded test scenarios and file upload support

### Entry point 2 — Structured screening pipeline (`portguard/`)

Stateful per-request pipeline. Accepts structured shipment data (importer, line items with descriptions, quantities, values, countries of origin, declared HTS codes), runs five sequential agents, stores the full report, and returns it.

```
POST /api/v1/screen
     |
     +-- Stage 1: ParserAgent
     |     normalize line items to ParsedLineItem (ISO2, USD, goods category)
     |     parsing_confidence: 0.90 (structured) / 0.75 / 0.55 (raw text)
     |
     +-- Stage 2: ClassifierAgent
     |     classify each line item to 10-digit HTSUS code
     |     priority: (1) declared HTS, (2) keyword table (25 entries), (3) goods_category fallback
     |     GRI 1 for declared/keyword, GRI 4 for fallback
     |     confidence: 0.80 / 0.65 / 0.30
     |
     +-- Stage 3: ValidationAgent
     |     ISF completeness (importer, exporter, origin ISO2, HTS code)
     |     PGA requirements per HTS chapter
     |     country of origin marking (19 USC 1304)
     |     zero-value line item flag (19 USC 1401a)
     |
     +-- Stage 4: RiskAgent
     |     Section 301 (CN-origin)     -- HIGH
     |     Section 232 (ch. 72/73/76)  -- HIGH
     |     AD/CVD orders               -- HIGH
     |     OFAC sanctions              -- CRITICAL / HIGH
     |     UFLPA                       -- HIGH
     |     valuation thresholds        -- LOW
     |
     +-- Stage 5: DecisionAgent
     |     REJECT -- any CRITICAL risk factor
     |     HOLD   -- any HIGH risk factor or CRITICAL validation finding
     |     REVIEW -- MEDIUM risk, ERROR/WARNING findings, or confidence < 0.70
     |     CLEAR  -- no significant issues
     |     duty estimation: base duties + additional duties from risk factors
     |
     +-- ScreeningReport
           UUID, timestamp, all stage outputs, pipeline errors, processing time
```

Fault tolerance: each stage runs independently. Errors are recorded in `pipeline_errors`; downstream stages that require missing output are skipped rather than crashing.

Additional pipeline endpoints:
- `POST /api/v1/parse` — ParserAgent only
- `POST /api/v1/classify` — ClassifierAgent only
- `POST /api/v1/assess-risk` — RiskAgent only
- `GET /api/v1/reports/{id}` — retrieve stored report by UUID

### File structure

```
portguard/
  config.py               pydantic-settings configuration (no API keys)
  data/
    sanctions.py          10 OFAC programs -- get_sanctions_programs(iso2)
    section301.py         333 HTS prefixes, Lists 1-4A -- get_section_301(hts, iso2)
    adcvd.py              14 AD/CVD orders -- get_adcvd_orders(hts, iso2)
    pga.py                72 HTS chapters -> PGA requirements -- get_pga_requirements(hts)
  models/
    shipment.py           ShipmentInput, ParsedShipment, ParsedLineItem
    classification.py     ClassificationResult, HTSLineClassification, GRIAnalysis
    validation.py         ValidationResult, ValidationFinding, FindingSeverity
    risk.py               RiskAssessment, RiskFactor, RiskType, RiskSeverity
    decision.py           ComplianceDecision, DecisionLevel, RequiredAction
    report.py             ScreeningReport
  agents/
    base.py               BaseAgent
    parser.py             ParserAgent
    classifier.py         ClassifierAgent
    validator.py          ValidationAgent
    risk.py               RiskAgent
    decision.py           DecisionAgent
    orchestrator.py       OrchestratorAgent (pipeline runner)
  api/
    main.py               FastAPI app (structured pipeline)
    routes.py             /health /screen /parse /classify /assess-risk /reports/{id}

api/
  app.py                  FastAPI app (document analysis, stateless)
  document_parser.py      PDF and plain-text extraction (pdfplumber)

main.py                   CLI runner: 3 test scenarios, prints results
run_demo.py               Starts API server, opens demo in browser
```

---

## Setup

**Requirements:** Python 3.11+. No API keys or external services needed.

```bash
# 1. Clone and enter the directory
git clone https://github.com/gractheh/PORTGUARD.git
cd PORTGUARD

# 2. Install dependencies
pip install -r requirements.txt
```

Dependencies: `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `python-dotenv`, `httpx`, `pdfplumber`, `python-multipart`, `pytest`, `pytest-asyncio`, `pytest-mock`.

---

## Running

### CLI test runner

```bash
python main.py
```

Runs the three sample scenarios in `tests/sample_documents/` against the document analysis API in-process and prints color-coded results. Actual output:

```
======================================================================
  PORTGUARD - Trade Compliance Screening
  Running 3 scenarios against POST /api/v1/analyze
======================================================================

======================================================================
Scenario 1: Clean Shipment  (Vietnamese laptop manufacturer - all documents consistent)
----------------------------------------------------------------------
  Decision   : APPROVE
  Risk level : LOW  (score 0.00)
  Confidence : HIGH
  Docs read  : 4   Inconsistencies: 0   Time: 0.01s

  Importer  : Horizon Technology Group, Inc
  Origin    : Vietnam
  Commodity : Laptop Computers (Notebook PCs)
  Value     : 208000.00

  Findings:
    - No sanctions, Section 301, or AD/CVD exposure identified.
    - All documents appear consistent.
    - No critical missing fields detected.

  Recommended next steps:
    1. File ISF at least 24 hours prior to vessel loading if not already filed.
    2. File standard consumption entry (CBP Form 7501) within 10 working days of arrival.

  [PASS] matches expected

======================================================================
Scenario 2: Suspicious Shipment  (Semiconductor ICs - shipper mismatch, transshipment indicators, undervaluation)
----------------------------------------------------------------------
  Decision   : FLAG_FOR_INSPECTION
  Risk level : CRITICAL  (score 0.84)
  Confidence : HIGH
  Docs read  : 3   Inconsistencies: 2   Time: 0.00s

  Importer  : NexGen Components Inc
  Origin    : Malaysia
  Commodity : General Merchandise

  Findings:
    - Transshipment indicator: port of loading (Yantian International Container
      Terminal, Shenzhen, China) is in CN, but declared origin is MY
    - HTS 8542.31.0000 subject to Section 301 List 3 at 25% if actual origin is
      China (inferred from port of loading) - tariff evasion is a federal offense
    - B/L contains vague cargo description ('General Merchandise' or similar)
    - Consignee listed as 'TO ORDER' - negotiable B/L
    - Shipper on B/L ('Guangzhou Apex Trading Ltd') does not match seller on
      invoice ('Sunrise Global Exports Pte. Ltd') - possible transshipment
    - One or more documents are water-damaged or partially illegible
    - ISF incomplete - missing: importer of record number (ISF element 3)

  Recommended next steps:
    1. Request CF-28 for certified proof of origin - do not release cargo pending verification.
    2. Refer to CBP for suspected transshipment investigation under 19 USC 1592.
    3. Deposit Section 301 additional duties with CBP at time of entry.
    4. Do not release cargo until all flagged issues are resolved.

  [PASS] matches expected

======================================================================
Scenario 3: Incomplete Shipment  (Frozen shrimp - missing FDA Prior Notice, no origin, absent ISF elements)
----------------------------------------------------------------------
  Decision   : REQUEST_MORE_INFORMATION
  Risk level : LOW  (score 0.24)
  Confidence : MEDIUM
  Docs read  : 2   Inconsistencies: 0   Time: 0.00s

  Importer  : American Seafood Distributors
  Commodity : Frozen Shrimp (processed)

  Findings:
    - Country of origin is explicitly 'NOT STATED' on commercial invoice
    - HTS code 0306.17 is 6-digit only - 10-digit HTSUS subheading required
    - One or more documents are water-damaged or partially illegible
    - ISF incomplete - missing: importer of record number (ISF element 3)

  Recommended next steps:
    1. Obtain corrected commercial invoice with country of origin.
    2. Obtain complete 10-digit HTSUS subheading.
    3. Request legible replacement copies of all damaged documents.
    4. File complete ISF (10+2) with all required data elements.

  [PASS] matches expected

======================================================================
  Results: 3/3 matched expected decisions
======================================================================
```

### Browser demo

```bash
python run_demo.py
```

Starts the document analysis API server, polls `/api/v1/health` every 250ms until ready, then opens `http://localhost:8000/demo` in the browser. The demo page has pre-loaded document text for all three test scenarios; results display inline. Press Ctrl+C to stop.

### Document analysis API

```bash
uvicorn api.app:app --reload --port 8000
```

```bash
# Health check
curl http://localhost:8000/api/v1/health

# Upload a document and extract its text
curl -X POST http://localhost:8000/api/v1/extract-text \
  -F "file=@bill_of_lading.pdf"

# Analyze a shipment
curl -X POST http://localhost:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d @tests/sample_documents/02_suspicious_shipment.json
```

**`POST /api/v1/extract-text` — file upload**

Accepts a `.pdf` or `.txt` file via multipart form upload. Returns the extracted text ready to pass to `/api/v1/analyze`.

```json
{
  "text": "--- Page 1 ---\nCOMMERCIAL INVOICE\n...",
  "filename": "commercial_invoice.pdf",
  "page_count": 1,
  "warnings": []
}
```

Error responses use structured `detail` bodies with a machine-readable `code`:

| HTTP | `code` | Cause |
|---|---|---|
| 413 | `FILE_TOO_LARGE` | File exceeds 10 MB |
| 422 | `SCANNED_PDF` | PDF has no machine-readable text layer (scanned image) |
| 422 | `PASSWORD_PROTECTED` | PDF is encrypted |
| 422 | `CORRUPT_PDF` | PDF bytes are malformed |
| 422 | `TOO_MANY_PAGES` | PDF exceeds 50 pages |
| 422 | `UNSUPPORTED_FORMAT` | File extension is not `.pdf` or `.txt` |

**Request:**
```json
{
  "documents": [
    { "raw_text": "BILL OF LADING\nShipper: ...", "filename": "bill_of_lading.txt" },
    { "raw_text": "COMMERCIAL INVOICE\n...",      "filename": "commercial_invoice.txt" }
  ]
}
```

**Response:**
```json
{
  "status": "completed",
  "shipment_data": {
    "importer": "NexGen Components Inc",
    "exporter": "Sunrise Global Exports Pte. Ltd",
    "origin_country": "Malaysia",
    "origin_country_iso2": "MY",
    "commodity_description": "General Merchandise",
    "hts_codes_declared": ["8542.31.0000"],
    "declared_value": "8000",
    "declared_currency": "USD"
  },
  "risk_score": 0.84,
  "risk_level": "CRITICAL",
  "decision": "FLAG_FOR_INSPECTION",
  "confidence": "HIGH",
  "explanations": [
    "Transshipment indicator: port of loading (Yantian, Shenzhen, China) is CN but declared origin is MY",
    "HTS 8542.31.0000 subject to Section 301 List 3 at 25% if actual origin is China",
    "Shipper on B/L does not match seller on invoice"
  ],
  "recommended_next_steps": [
    "Request CF-28 for certified proof of origin — do not release cargo pending verification.",
    "Refer to CBP for suspected transshipment investigation under 19 USC 1592."
  ],
  "inconsistencies_found": 2,
  "documents_analyzed": 3,
  "processing_time_seconds": 0.003
}
```

**Decision values (Entry Point 1):**

| Decision | Trigger |
|---|---|
| `APPROVE` | No material issues — standard processing |
| `REVIEW_RECOMMENDED` | Risk score > 0.20 or any MEDIUM risk factor |
| `FLAG_FOR_INSPECTION` | Transshipment + mismatch, or risk score > 0.50 |
| `REQUEST_MORE_INFORMATION` | 3+ critical documentation gaps |
| `REJECT` | Comprehensive OFAC sanctions on country of origin |

### Structured pipeline API

```bash
uvicorn portguard.api.main:app --reload --port 8001
```

Interactive docs at `http://localhost:8001/docs`.

**Endpoints:**

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/health` | Liveness check |
| POST | `/api/v1/screen` | Full 5-stage pipeline; stores report |
| POST | `/api/v1/parse` | ParserAgent only |
| POST | `/api/v1/classify` | ClassifierAgent only |
| POST | `/api/v1/assess-risk` | RiskAgent only |
| GET | `/api/v1/reports/{id}` | Retrieve stored report by UUID |

**Decision values (Entry Point 2):**

| Decision | Trigger |
|---|---|
| `CLEAR` | No significant issues |
| `REVIEW` | MEDIUM risk, WARNING/ERROR findings, or classification confidence < 0.70 |
| `HOLD` | Any HIGH risk factor or CRITICAL validation finding |
| `REJECT` | Any CRITICAL risk factor (comprehensive OFAC sanctions) |

### Tests

```bash
python -m pytest
```

33 tests across 7 files, all passing:

```
tests/test_api.py           7 tests   FastAPI routes (httpx AsyncClient)
tests/test_parser.py        4 tests   ParserAgent
tests/test_classifier.py    4 tests   ClassifierAgent
tests/test_validator.py     4 tests   ValidationAgent
tests/test_risk.py          5 tests   RiskAgent (Section 301, OFAC, AD/CVD, Section 232)
tests/test_decision.py      5 tests   DecisionAgent (CLEAR/REVIEW/HOLD/REJECT)
tests/test_orchestrator.py  4 tests   Pipeline fault tolerance
```

---

## Compliance data

### AD/CVD orders covered

| Case | Type | Country | Product | Rate |
|---|---|---|---|---|
| A-570-029 | AD | CN | Cold-Rolled Steel Flat Products (7209, 7211) | 265.79% |
| A-570-028 | AD | CN | Hot-Rolled Steel Flat Products (7208, 7211) | 199.43% |
| A-570-967 | AD | CN | Aluminum Extrusions (7604, 7608, 7610) | 374.15% |
| A-570-979 | AD | CN | Crystalline Silicon Photovoltaic Cells (8541.40) | 238.95% |
| A-570-106 | AD | CN | Wooden Cabinets and Vanities (9403.40, 9403.90) | 262.18% |
| A-570-116 | AD | CN | Hardwood Plywood (4412) | 183.36% |
| C-570-116 | CVD | CN | Hardwood Plywood (4412) | 22.98% |
| A-570-099 | AD | CN | Carbon and Alloy Steel Wire Rod (7213, 7227) | 110.25% |
| A-570-918 | AD | CN | Prestressed Concrete Steel Wire Strand (7312.10) | 43.80–194.90% |
| A-570-601 | AD | CN | Wooden Bedroom Furniture (9403.50, 9403.60) | 216.01% |
| A-428-830 | AD | DE | Hot-Rolled Steel Flat Products (7208, 7211) | 3.44% |
| A-580-883 | AD | KR | Cold-Rolled Steel Flat Products (7209, 7211) | 6.32% |
| A-201-848 | AD | MX | Cold-Rolled Steel Flat Products (7209, 7211) | 7.68% |
| A-552-818 | AD | VN | Steel Nails (7317) | 323.99% |

### Section 301 — China lists

| List | Rate | Effective | Coverage |
|---|---|---|---|
| List 1 | 25% | 2018-07-06 | Industrial machinery, pumps, compressors — 41 HTS headings |
| List 2 | 25% | 2018-08-23 | Engines, generators, motors, vehicles — 23 HTS headings |
| List 3 | 25% | 2018-09-24 | Broad: food, steel, plastics, electronics, furniture, medical — ~120 headings |
| List 4A | 7.5% | 2019-09-01 | ADP machines (8471), apparel (61xx/62xx), furniture (9401/9403), smartphones (8517.12) |

List 3 takes precedence over List 4A when the same HTS prefix appears in both.

### HTSUS keyword classifier

The structured pipeline classifies line items using a 25-entry keyword table. Covered products include: laptops (8471.30.0100), desktops (8471.41.0150), servers (8471.49.0000), semiconductors/ICs (8542.31.0000), smartphones (8517.12.0050), routers/switches (8517.62.0090), solar panels (8541.40.6020), LCD/LED monitors (8528.52.0000), cold-rolled steel (7209.16.0030), hot-rolled steel (7208.36.0030), steel wire rod (7213.91.3011), aluminum extrusions (7604.29.1010), wooden cabinets (9403.40.9060), bedroom furniture (9403.50.9042), hardwood plywood (4412.33.0571), steel nails (7317.00.5500), frozen shrimp (0306.17.0020), frozen fish (0303.89.0000), cotton T-shirts (6109.10.0012), cotton woven shirts (6205.20.2016), footwear (6403.99.9060), toys (9503.00.0073), centrifugal pumps (8413.70.2004), AC motors (8501.52.4000), chemicals/polymers/resins (3901.20.5000).

---

## Test scenarios

Three sample request payloads in `tests/sample_documents/`:

| File | Scenario | Documents | Expected decision |
|---|---|---|---|
| `01_clean_shipment.json` | Vietnamese laptops — 4 consistent documents | B/L, invoice, packing list, cert. of origin | `APPROVE` |
| `02_suspicious_shipment.json` | Chinese ICs declared as Malaysian origin — transshipment, shipper mismatch | B/L, invoice, packing list | `FLAG_FOR_INSPECTION` |
| `03_incomplete_shipment.json` | Frozen shrimp — missing FDA Prior Notice, no origin, damaged B/L | Partial B/L, incomplete invoice | `REQUEST_MORE_INFORMATION` |

---

## Limitations

- **Static regulatory data.** Section 301 prefix tables, AD/CVD orders, and OFAC program lists are embedded at build time. They do not update automatically when regulations change.
- **Document parsing is regex-based.** Field extraction works on well-structured text. Heavily formatted layouts or unusual document structures may not extract correctly.
- **PDF support is text-layer only.** `POST /api/v1/extract-text` uses pdfplumber to extract machine-readable text from PDFs (up to 50 pages, 10 MB). Scanned PDFs with no text layer, password-protected PDFs, and corrupt PDFs are rejected with descriptive error codes. For scanned documents, run OCR before uploading.
- **ISF checks are partial.** Only 4 of the 10 ISF importer-provided data elements can be verified from document text. Elements 2 (buyer), 6 (ship-to party), 9 (consolidator), and 10 (container stuffing location) are not checked.
- **In-memory report storage.** The structured pipeline stores reports in a Python dict. Reports are lost on process restart. Not suitable for multi-worker deployments.
- **No authentication.** Neither API endpoint has access control.
- **Classification is not legally binding.** HTS classifications and duty rate estimates are for preliminary screening only. A licensed customs broker must file the actual entry.
