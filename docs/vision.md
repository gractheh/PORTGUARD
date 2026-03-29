# PORTGUARD — Master Plan

---

## Problem

Every US import shipment must clear a patchwork of overlapping regulatory requirements before cargo can be released. A broker or compliance officer working a single entry must simultaneously check:

- Whether the country of origin is under OFAC sanctions (four comprehensive embargoes, six sectoral programs)
- Whether the HTS code is subject to Section 301 additional duties — 333 HTS prefixes across four China tariff lists ranging from 7.5% to 25%
- Whether active antidumping or countervailing duty orders apply — each with its own case number, HTS coverage, and deposit rate
- Whether the goods trigger Section 232 steel (25%) or aluminum (10%) tariffs
- Whether the shipment involves goods that may be tainted by forced labor under UFLPA
- Whether ISF has been filed with all required data elements, at least 24 hours before vessel departure
- Whether Partner Government Agency (PGA) requirements apply — FDA Prior Notice, USDA phytosanitary certificates, FCC equipment authorizations, CPSC safety certifications, and dozens of others
- Whether the documents are internally consistent — shipper names, origin countries, declared values, HTS codes matching across the B/L, commercial invoice, packing list, and certificate of origin

Each of these checks requires regulatory data (HTS tables, sanctions lists, AD/CVD orders), document analysis (field extraction from unstructured text), and heuristic judgment (does this unit value make sense for this commodity?). Done manually, one entry takes hours. Done poorly, it means underpaid duties, CBP penalties, or cargo held at port indefinitely.

---

## Solution

**PORTGUARD** is a fully local, rule-based US import compliance screening system. It ingests raw shipping documents or structured shipment data and returns, in under one second, a risk score, a compliance decision, a list of specific findings, and prioritized remediation steps — with no external API calls, no paid services, and no network dependency.

Every compliance check is implemented directly in code:

- OFAC sanctions are checked against an embedded dataset of 10 programs (4 comprehensive, 6 sectoral), covering Cuba, Iran, North Korea, Syria, Russia, Belarus, Venezuela, Myanmar, Central African Republic, and Zimbabwe
- Section 301 is checked via 333 embedded HTS prefixes across Lists 1, 2, 3, and 4A, with longest-prefix matching and overlap resolution
- AD/CVD is checked against 14 embedded active orders covering cold-rolled steel, hot-rolled steel, aluminum extrusions, solar cells, wooden cabinets, hardwood plywood, wire rod, prestressed concrete strand, bedroom furniture from China, plus steel products from Germany, South Korea, Mexico, and steel nails from Vietnam
- Section 232 is triggered by HTS chapters 72 and 73 (steel, 25%) and chapter 76 (aluminum, 10%)
- UFLPA is flagged for China-origin goods in 10 high-risk categories: cotton, polysilicon, tomatoes, steel, textiles, apparel, aluminum, solar, batteries, and chemicals
- ISF completeness is evaluated against importer name, exporter name, country of origin, and HTS classification as the four verifiable elements
- PGA requirements are mapped across 72 HTS chapters to FDA, USDA (FSIS/APHIS/AMS/NMFS), FTC, CPSC, FCC, NHTSA, ATF, EPA, TTB, Commerce, NRC, FAA, FRA, and US Fish & Wildlife
- Transshipment is detected by comparing port-of-loading city to declared country of origin, using a mapping of 35+ port cities to ISO2 country codes
- Undervaluation is flagged when declared unit value falls below 40% of a commodity benchmark for five product types: semiconductor ICs, laptops, smartphones, solar panels, and flat-panel displays

---

## Architecture

PORTGUARD has two independent entry points that share the same underlying compliance data modules.

### Entry Point 1 — Document Analysis API (`api/app.py`)

Stateless. Accepts one or more raw document texts with filenames, returns a single structured screening result. This is the primary entry point used by the browser demo and the CLI test runner.

```
POST /api/v1/analyze
     |
     +-- _extract_shipment_data()
     |     regex extraction: importer, exporter, origin, port of loading,
     |     port of discharge, vessel, B/L number, shipment date, incoterms,
     |     HTS codes, total value, currency, quantity, weight, commodity
     |
     +-- _find_inconsistencies()
     |     cross-document shipper vs. seller name comparison (token similarity)
     |     origin country consistency across documents
     |     port-of-loading country vs. declared origin (transshipment indicator)
     |     value discrepancy across documents (>2.5x variance)
     |
     +-- _check_missing_fields()
     |     country of origin absent or "NOT STATED"
     |     HTS code absent or 6-digit only
     |     importer/consignee not identified
     |     manufacturer not referenced (ISF element 5)
     |     currency not specified
     |     illegible/water-damaged documents
     |     FDA Prior Notice absent for food imports (by HTS chapter or commodity keywords)
     |     USDA/NMFS health certificate absent for seafood
     |     ISF importer of record number absent
     |
     +-- _assess_risk()
     |     OFAC sanctions (get_sanctions_programs) — CRITICAL/HIGH
     |     transshipment + inferred CN origin + Section 301 check
     |     Section 301 direct CN origin (get_section_301) — HIGH
     |     AD/CVD orders (get_adcvd_orders) — HIGH
     |     undervaluation vs. commodity benchmarks — HIGH/MEDIUM
     |     vague B/L cargo description — MEDIUM
     |     negotiable B/L (TO ORDER consignee) — LOW
     |     Russia/Belarus sectoral sanctions — HIGH
     |
     +-- _compute_score()
     |     weighted sum: sum(factor.score) + n_inconsistencies * 0.06
     |     + n_missing_fields * 0.06, capped at 1.0
     |
     +-- _make_decision()
     |     REJECT               — comprehensive OFAC sanctions
     |     REQUEST_MORE_INFO    — >=3 critical documentation gaps
     |     FLAG_FOR_INSPECTION  — transshipment + mismatch, or score > 0.50
     |     REVIEW_RECOMMENDED   — score > 0.20 or any MEDIUM risk factor
     |     APPROVE              — everything else
     |
     +-- _generate_next_steps()
           decision-specific, finding-specific actionable instructions
```

**Response fields:**
- `shipment_data` — all extracted fields (importer, exporter, origin, HTS codes, value, etc.)
- `risk_score` — float [0.0, 1.0]
- `risk_level` — LOW / MEDIUM / HIGH / CRITICAL
- `decision` — one of five values (see above)
- `confidence` — HIGH / MEDIUM / LOW based on data completeness
- `explanations` — specific findings with regulatory references
- `recommended_next_steps` — prioritized remediation instructions
- `inconsistencies_found` — count of cross-document conflicts
- `documents_analyzed` — number of documents processed
- `processing_time_seconds` — elapsed time

Additional endpoints:
- `GET /api/v1/health` — liveness check
- `GET /demo` — serves `demo.html` (browser UI with 3 pre-loaded test scenarios)

### Entry Point 2 — Structured Screening Pipeline (`portguard/`)

Stateful per-request pipeline. Accepts structured shipment data (importer, line items with descriptions, quantities, values, countries of origin, declared HTS codes), runs five sequential agents, stores the full report, and returns it.

```
POST /api/v1/screen
     |
     +-- Stage 1: ParserAgent
     |     normalize line items to ParsedLineItem (ISO2, USD conversion, goods category)
     |     fallback: regex extraction from raw_text if no structured line items
     |     assign parsing_confidence: 0.90 (structured) / 0.75 / 0.55 (raw text)
     |
     +-- Stage 2: ClassifierAgent
     |     classify each line item to 10-digit HTSUS code
     |     priority: (1) declared HTS code, (2) keyword table (25 entries),
     |     (3) goods_category fallback
     |     GRI analysis: GRI 1 for declared/keyword, GRI 4 for fallback
     |     confidence: 0.80 / 0.65 / 0.30
     |
     +-- Stage 3: ValidationAgent
     |     ISF completeness check (importer, exporter, origin ISO2, HTS codes)
     |     PGA requirements per HTS chapter (get_pga_requirements)
     |     country of origin marking check (19 USC 1304)
     |     zero-value line item flag (19 USC 1401a)
     |     findings: ERROR / WARNING / INFO severity levels
     |
     +-- Stage 4: RiskAgent
     |     Section 301 (get_section_301) — CN-origin line items — HIGH
     |     Section 232 — chapters 72/73 (steel 25%), 76 (aluminum 10%) — HIGH
     |     AD/CVD (get_adcvd_orders) — per line item — HIGH
     |     OFAC sanctions (get_sanctions_programs) — CRITICAL/HIGH
     |     UFLPA — CN-origin + high-risk category or Xinjiang keywords — HIGH
     |     valuation thresholds — de minimis ($800) and formal entry ($2500) — LOW
     |
     +-- Stage 5: DecisionAgent
     |     REJECT — any CRITICAL risk factor
     |     HOLD   — any HIGH risk factor or CRITICAL validation finding
     |     REVIEW — MEDIUM risk, ERROR/WARNING findings, or classification confidence < 0.7
     |     CLEAR  — no significant issues
     |     duty estimation: base duties + additional duties from risk factors
     |
     +-- ScreeningReport
           UUID, timestamp, all stage outputs, pipeline errors, processing time
```

Additional pipeline endpoints:
- `POST /api/v1/parse` — ParserAgent only
- `POST /api/v1/classify` — ClassifierAgent only
- `POST /api/v1/assess-risk` — RiskAgent only
- `GET /api/v1/reports/{id}` — retrieve stored report by UUID

**Fault tolerance:** Each stage runs independently. If a stage fails, the error is recorded in `pipeline_errors` and subsequent stages that require its output are skipped. Parsing failure stops the pipeline; classifier or validation failures are recorded but the pipeline continues for remaining stages.

### Compliance Data Modules (`portguard/data/`)

All regulatory data is embedded as Python objects — no database, no external service.

| Module | Contents | Lookup function |
|---|---|---|
| `sanctions.py` | 10 OFAC programs (4 comprehensive, 6 sectoral) | `get_sanctions_programs(iso2)` |
| `section301.py` | 333 HTS prefixes across Lists 1–4A, China only | `get_section_301(hts_code, iso2)` |
| `adcvd.py` | 14 active AD/CVD orders, 5 countries | `get_adcvd_orders(hts_code, iso2)` |
| `pga.py` | 72 HTS chapters mapped to PGA requirements | `get_pga_requirements(hts_code)` |

---

## Features

### OFAC Sanctions Screening

Checks every country of origin against 10 embedded OFAC programs:

**Comprehensive embargoes (CRITICAL severity):**
- Cuba — CACR (31 CFR 515)
- Iran — ITSR (31 CFR 560)
- North Korea — NKSR (31 CFR 510)
- Syria — SySR (31 CFR 542)

**Sectoral sanctions (HIGH severity):**
- Russia — EO 14024 (31 CFR 587) — finance, energy, defense, aviation, technology
- Belarus — EO 14038 (31 CFR 548) — finance, defense, technology
- Venezuela — EO 13884 (31 CFR 591) — government, gold, oil
- Myanmar/Burma — EO 14014 (31 CFR 582) — defense, government
- Central African Republic — EO 13667 (31 CFR 553) — defense, minerals
- Zimbabwe — EO 13391 (31 CFR 541) — government, minerals

Comprehensive sanctions trigger REJECT (Entry Point 1) or REJECT decision level (Entry Point 2).

### Section 301 Tariff Screening

Checks every declared HTS code against 333 embedded prefixes for China-origin goods:
- **List 1** (25%, effective 2018-07-06): industrial machinery, pumps, compressors — 41 HTS headings
- **List 2** (25%, effective 2018-08-23): engines, generators, motors, vehicles — 23 HTS headings
- **List 3** (25%, effective 2018-09-24): broad coverage — food, steel, plastics, electronics, furniture, medical — ~120 HTS headings
- **List 4A** (7.5%, effective 2019-09-01): ADP machines (8471), apparel (61xx/62xx), furniture (9401/9403), smartphones (8517.12) — 18 entries; List 3 headings take precedence

Matching uses longest-prefix wins. In the document analysis entry point, transshipment is detected when the port of loading is Chinese and the HTS code hits Section 301 — flagged as tariff evasion (federal offense under 19 USC 1592).

### Section 232 Steel and Aluminum

Flags all imports in HTS chapters 72–73 (steel, 25%) and chapter 76 (aluminum, 10%). Triggered regardless of country of origin — exemptions for specific countries with quota arrangements or alternative agreements are noted in the recommended action. Requires Commerce steel/aluminum import license filing.

### Antidumping and Countervailing Duty Orders

14 active orders checked by HTS prefix match and country of origin:

| Case | Type | Country | Product | Rate |
|---|---|---|---|---|
| A-570-029 | AD | China | Cold-Rolled Steel Flat Products (7209, 7211) | 265.79% |
| A-570-028 | AD | China | Hot-Rolled Steel Flat Products (7208, 7211) | 199.43% |
| A-570-967 | AD | China | Aluminum Extrusions (7604, 7608, 7610) | 374.15% |
| A-570-979 | AD | China | Crystalline Silicon Photovoltaic Cells (8541.40) | 238.95% |
| A-570-106 | AD | China | Wooden Cabinets and Vanities (9403.40, 9403.90) | 262.18% |
| A-570-116 | AD | China | Hardwood Plywood (4412) | 183.36% |
| C-570-116 | CVD | China | Hardwood Plywood (4412) | 22.98% |
| A-570-099 | AD | China | Carbon and Alloy Steel Wire Rod (7213, 7227) | 110.25% |
| A-570-918 | AD | China | Prestressed Concrete Steel Wire Strand (7312.10) | 43.80–194.90% |
| A-570-601 | AD | China | Wooden Bedroom Furniture (9403.50, 9403.60) | 216.01% |
| A-428-830 | AD | Germany | Hot-Rolled Steel Flat Products (7208, 7211) | 3.44% |
| A-580-883 | AD | South Korea | Cold-Rolled Steel Flat Products (7209, 7211) | 6.32% |
| A-201-848 | AD | Mexico | Cold-Rolled Steel Flat Products (7209, 7211) | 7.68% |
| A-552-818 | AD | Vietnam | Steel Nails (7317) | 323.99% |

### UFLPA Forced Labor Screening

Flags China-origin goods that fall into high-risk categories subject to the Uyghur Forced Labor Prevention Act rebuttable presumption: cotton, polysilicon, tomatoes, steel, textiles, apparel, aluminum, solar, batteries, and chemicals. Also triggered by Xinjiang-related keywords in any document text (xinjiang, xuar, uyghur, uygur, east turkestan). Flagged as HIGH severity with guidance on supply chain due diligence and CBP rebuttal evidence requirements.

### ISF Completeness Checking

Verifies the four ISF data elements that can be evaluated from document data:
- Importer name (ISF elements 3–4)
- Exporter/seller name (ISF element 1)
- Country of origin for every line item (ISF element 7)
- HTS-level commodity code (ISF element 8)

Missing elements generate ERROR-severity findings citing 19 CFR 149.2 and 19 USC 1415. ISF must be filed at least 24 hours before vessel departure; late or incomplete ISF carries penalties up to $10,000 per violation.

### PGA Requirements

72 HTS chapters are mapped to Partner Government Agency requirements. Examples:
- Chapters 01–24 (food/ag): FDA Prior Notice (mandatory under 21 CFR 1.279), USDA FSIS/APHIS certificates, NMFS seafood inspection, TTB COLA for alcohol
- Chapters 28–29 (chemicals): EPA TSCA certification
- Chapter 30 (pharmaceuticals): FDA drug establishment registration, 510(k)/PMA for medical devices
- Chapters 50–63 (textiles/apparel): FTC textile fiber labeling, CPSC flammability standards
- Chapters 72–73 (steel): Commerce Section 232 import license
- Chapter 76 (aluminum): Commerce Section 232 import license
- Chapters 84–85 (machinery/electronics): FCC equipment authorization, CPSC safety standards
- Chapter 87 (vehicles): NHTSA FMVSS, EPA emissions certification
- Chapter 93 (firearms): ATF import permit
- Chapter 95 (toys): CPSC ASTM F963, flammability standards

PGA requirements are returned as INFO-severity findings in the structured pipeline, and as a separate `pga_requirements` field in the ValidationResult.

### Document Inconsistency Detection

In the document analysis entry point, cross-document consistency is verified across all uploaded files:

- **Shipper vs. seller mismatch** — B/L shipper name compared to commercial invoice seller name using token overlap scoring (score < 0.35 triggers flag). Legal entity suffixes (Ltd, Co, LLC, Inc, Pte, GmbH, etc.) are stripped before comparison.
- **Origin country mismatch** — country of origin extracted per document; disagreements across documents are flagged.
- **Port of loading vs. declared origin** — port city mapped to ISO2 country using 35+ port/city entries; disagreement with declared origin triggers transshipment flag.
- **Value discrepancy** — total value extracted per document; ratio > 2.5× between highest and lowest triggers flag.

### Undervaluation Detection

Declared unit value is compared against commodity-specific benchmarks. Values below 40% of the minimum benchmark trigger HIGH severity; below 70% trigger MEDIUM:

| Commodity | Benchmark minimum | Notes |
|---|---|---|
| Semiconductor ICs, monolithic circuits | $2.50/unit | Typical range $3–12 |
| Laptop / notebook computers | $100/unit | Typical range $200–1,200 |
| Smartphones / mobile phones | $60/unit | Typical range $100–800 |
| Solar panels / PV modules | $20/unit | Typical range $30–200 |
| Flat-panel displays / LCD monitors | $30/unit | Typical range $50–500 |

### HTSUS Classification

The structured pipeline classifies every line item to a 10-digit HTSUS code. Twenty-five keyword-table entries cover:

- ADP machines: laptops (8471.30.0100), desktops (8471.41.0150), servers (8471.49.0000)
- Semiconductors / integrated circuits (8542.31.0000)
- Smartphones (8517.12.0050), routers and network switches (8517.62.0090)
- Solar panels and photovoltaic modules (8541.40.6020)
- LCD/LED monitors (8528.52.0000)
- Cold-rolled steel flat products (7209.16.0030)
- Hot-rolled steel flat products (7208.36.0030)
- Steel wire rod (7213.91.3011)
- Aluminum extrusions and profiles (7604.29.1010)
- Kitchen cabinets / wooden furniture (9403.40.9060)
- Bedroom furniture (9403.50.9042)
- Hardwood plywood (4412.33.0571)
- Steel nails (7317.00.5500)
- Frozen shrimp and prawns (0306.17.0020)
- Frozen fish (0303.89.0000)
- Cotton T-shirts (6109.10.0012), cotton woven shirts (6205.20.2016)
- Footwear (6403.99.9060)
- Toys (9503.00.0073)
- Centrifugal pumps (8413.70.2004)
- AC motors (8501.52.4000)
- Chemicals / polymers / resins (3901.20.5000)

GRI 1 is applied for declared codes and keyword matches. GRI 4 is applied for goods-category fallbacks. Duty rates are embedded per entry; base and additional duty estimates are computed in the DecisionAgent.

### Risk Scoring and Decision Logic

**Document Analysis API (5 decision levels):**

| Decision | Trigger |
|---|---|
| REJECT | Comprehensive OFAC sanctions on origin country |
| REQUEST_MORE_INFORMATION | 3+ critical documentation gaps (origin, HTS, FDA, illegible docs, manufacturer, ISF) |
| FLAG_FOR_INSPECTION | Transshipment + shipper mismatch or undervaluation; or risk score > 0.50 with high-severity factors |
| REVIEW_RECOMMENDED | Risk score > 0.20 or any MEDIUM-severity risk factor |
| APPROVE | No material issues |

Risk score is a weighted sum of individual factor scores (sanctions 0.90, transshipment 0.28, sectoral sanctions 0.30, Section 301 0.22, AD/CVD 0.20, undervaluation 0.18/0.10, vague description 0.08, negotiable B/L 0.04) plus documentation gap penalties (0.06 per missing field, capped at 0.30) and inconsistency penalties (0.06 per inconsistency, capped at 0.24), capped at 1.0.

**Structured Pipeline (4 decision levels):**

| Decision | Trigger |
|---|---|
| REJECT | Any CRITICAL risk factor (comprehensive OFAC sanctions) |
| HOLD | Any HIGH risk factor or CRITICAL validation finding |
| REVIEW | Any MEDIUM risk factor, ERROR/WARNING validation finding, or classification confidence < 0.70 |
| CLEAR | No significant issues |

### Browser Demo and CLI Test Runner

- `python run_demo.py` — starts the API server, polls `/api/v1/health` every 250ms, opens `http://localhost:8000/demo` in the browser automatically
- `python main.py` — runs the three sample scenarios against the API in-process using FastAPI's test client, prints color-coded results with PASS/FAIL status
- `demo.html` — browser UI with pre-loaded document text for all three test scenarios; displays risk score, decision, findings, and next steps

Three sample scenarios are provided in `tests/sample_documents/`:

| File | Scenario | Expected decision |
|---|---|---|
| `01_clean_shipment.json` | Vietnamese laptops, 4 consistent documents | APPROVE |
| `02_suspicious_shipment.json` | Chinese ICs via Singapore — transshipment, mismatch, undervaluation | FLAG_FOR_INSPECTION |
| `03_incomplete_shipment.json` | Frozen shrimp — missing FDA Prior Notice, no origin, illegible B/L | REQUEST_MORE_INFORMATION |

---

## Limitations

- **Static regulatory data.** Section 301 prefix tables, AD/CVD orders, and OFAC program lists are embedded at build time. They do not update automatically when regulations change.
- **Document parsing is regex-based.** Field extraction works on well-structured document text. Heavily formatted, scanned, or unusual document layouts may not extract correctly.
- **No authentication.** Neither API endpoint has access control.
- **In-memory report storage.** The structured pipeline stores reports in a Python dict. Reports are lost on process restart. Not suitable for multi-worker deployments.
- **Classification is preliminary.** HTS classifications and duty rate estimates are for screening purposes only. A licensed customs broker must file the actual entry. These outputs are not legally binding.
- **ISF checks are partial.** Only four of the ten ISF importer-provided data elements can be verified from document text. Elements 2 (buyer), 6 (ship-to party), 9 (consolidator), and 10 (container stuffing location) are not checked.
