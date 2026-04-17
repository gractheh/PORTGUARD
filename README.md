# PORTGUARD

US import compliance screening system. Accepts raw shipping documents or structured shipment data and returns a risk score, compliance decision, and actionable findings. Rule-based at its core — no external services or API keys required — with an optional **Localized Pattern Learning** layer that improves screening accuracy as officers submit feedback over time.

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
- `POST /api/v1/feedback` — record an officer's verdict on a screened shipment (CONFIRMED_FRAUD / CLEARED / UNRESOLVED); drives pattern learning
- `GET /api/v1/pattern-history` — aggregate pattern statistics: total shipments analyzed, confirmed fraud count, top-5 riskiest shippers and routes
- `POST /api/v1/report/generate` — generate a PDF compliance report for a stored shipment by `shipment_id`
- `POST /api/v1/report/generate-direct` — generate a PDF compliance report directly from a full `AnalyzeResponse` JSON body (no DB lookup)
- `GET /demo` — browser UI (`demo.html`) with 3 pre-loaded test scenarios, pattern intelligence display, officer feedback buttons, and per-shipment PDF download

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
  pattern_db.py           SQLAlchemy data layer — shipment history, entity profiles,
                          route risk, HS baselines, migrations, PatternDB class
  pattern_engine.py       Read-only scoring engine — 5 typed signals, Bayesian
                          Beta scoring, Welford z-score, Poisson frequency,
                          cold start handling, plain-English explanations
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
    report.py             ScreeningReport (includes pattern learning fields)
  agents/
    base.py               BaseAgent
    parser.py             ParserAgent
    classifier.py         ClassifierAgent
    validator.py          ValidationAgent
    risk.py               RiskAgent
    decision.py           DecisionAgent
    orchestrator.py       OrchestratorAgent — Stage 4.5 pattern scoring,
                          score blending (rule 65% + pattern 35%), DB recording
  api/
    main.py               FastAPI app (structured pipeline)
    routes.py             /health /screen /parse /classify /assess-risk /reports/{id}
  tests/
    test_pattern_db.py    59 tests — PatternDB data layer
    test_pattern_engine.py  71 tests — PatternEngine scoring logic

portguard/
  auth.py                 Authentication module — bcrypt password hashing, JWT
                          creation/verification, AuthDB (PostgreSQL/SQLite), rate
                          limiting, get_current_organization FastAPI dependency
  db.py                   Engine factory — resolves DATABASE_URL to SQLAlchemy
                          engine (PostgreSQL in prod, SQLite fallback for local dev)

portguard/
  analytics.py            DashboardAnalytics — read-only SQLAlchemy connection, 7 query
                          methods for summary stats, decision breakdown, fraud trend
                          (gap-filled), top countries/shippers/HS codes, recent activity

api/
  app.py                  FastAPI app (document analysis + pattern learning overlay)
                          /analyze, /feedback, /pattern-history,
                          /dashboard/summary, /dashboard/decisions,
                          /dashboard/fraud-trend, /dashboard/top-countries,
                          /dashboard/top-shippers, /dashboard/top-hs-codes,
                          /dashboard/recent-activity
  auth_routes.py          Auth endpoints — /auth/register, /auth/login,
                          /auth/logout, /auth/me
  document_parser.py      PDF and plain-text extraction (pdfplumber)

docs/
  pattern_learning_architecture.md  Full LPL design spec
  dashboard_architecture.md         Analytics dashboard technical plan

main.py                   CLI runner: 3 test scenarios, prints results
run_demo.py               Starts API server, opens demo in browser
test_e2e_pattern_learning.py  End-to-end pipeline test (36 checks)
```

---

## Authentication and Multi-Tenancy

PORTGUARD supports multiple independent organizations on a single deployment. Each organization registers with a company name and email, and all shipment data — pattern history, entity profiles, route risk, and HS baselines — is completely isolated per organization. One organization cannot read or influence another's screening history.

### How it works

Authentication uses JWT Bearer tokens (HS256, 24-hour expiry). Tokens live in JavaScript memory only — never written to `localStorage`, `sessionStorage`, or cookies. On logout, the token's JTI is written to a server-side revocation table so it cannot be reused even before it expires.

**Auth endpoints (`/api/v1/auth/`):**

| Method | Path | Auth required | Description |
|---|---|---|---|
| POST | `/api/v1/auth/register` | No | Create a new organization account |
| POST | `/api/v1/auth/login` | No | Authenticate; returns JWT access token |
| POST | `/api/v1/auth/logout` | Yes | Revoke the current token server-side |
| GET | `/api/v1/auth/me` | Yes | Return the authenticated organization's info |

All other API endpoints (`/api/v1/analyze`, `/api/v1/feedback`, `/api/v1/pattern-history`, `/api/v1/pattern-history/reset`, `/api/v1/extract-text`, `/api/v1/analyze-files`, and all `/api/v1/dashboard/*` endpoints) require a valid Bearer token. `GET /api/v1/health`, `GET /`, and `GET /demo` are public.

**Register:**
```json
POST /api/v1/auth/register
{
  "org_name": "Acme Customs LLC",
  "email": "ops@acme.io",
  "password": "s3cur3pass!"
}
```
Returns `201 Created` with `{ "organization_id": "<uuid>", "org_name": "...", "email": "..." }`.

**Login:**
```json
POST /api/v1/auth/login
{ "email": "ops@acme.io", "password": "s3cur3pass!" }
```
Returns `{ "access_token": "<jwt>", "token_type": "bearer" }`.

**Protected request:**
```
Authorization: Bearer <access_token>
```

### Security design

| Property | Implementation |
|---|---|
| Password hashing | bcrypt, work factor 12 |
| Token format | HS256 JWT, 24h expiry, unique JTI per token |
| Logout | JTI stored in SQLite revocation table; checked on every request |
| User enumeration protection | Non-existent user logins run bcrypt against a pre-generated dummy hash to normalize timing |
| Rate limiting | 5 failed logins per IP per 60 seconds; requests beyond the threshold are rejected with HTTP 429 |
| Credential storage | `PORTGUARD_JWT_SECRET` env var for production key rotation; random fallback for development |

### Data isolation

The `organization_id` from the JWT `sub` claim is threaded through every database operation. All four profile tables (`shipper_profiles`, `consignee_profiles`, `route_risk_profiles`, `hs_code_baselines`) use composite primary keys `(organization_id, entity_key)`. Every SQL query is filtered by `WHERE organization_id = ?`. Three independent isolation layers prevent cross-organization data leakage:

1. JWT claim — `sub` field is the organization UUID
2. Application layer — org_id passed explicitly to every PatternDB and PatternEngine call
3. SQL layer — every query has an `organization_id` predicate

Pre-authentication data (if any exists from before the auth system was deployed) is attributed to the sentinel `'__system__'` organization, which is unreachable to any authenticated user.

### Auth storage

In production (when `DATABASE_URL` is set) auth and pattern data share one PostgreSQL database — tables are distinguished by name. Locally, auth data falls back to a SQLite file (`portguard_auth.db`) separate from pattern learning data (`portguard_patterns.db`).

| Env var | Default | Description |
|---|---|---|
| `PORTGUARD_JWT_SECRET` | Random 32-byte hex | JWT signing key — must be set in production |
| `DATABASE_URL` | *(not set)* | PostgreSQL connection string — when set, SQLite files are ignored |
| `PORTGUARD_AUTH_DB_PATH` | `portguard_auth.db` | SQLite auth DB path (local dev only) |

### Browser demo

The demo UI (`GET /demo`) opens with a login screen. New users click "Create account" to register. After login, the organization name appears in the top-right header and a Logout button is available. All existing demo functionality (document analysis, pattern intelligence, officer feedback, pattern history) works exactly as before — all API calls automatically include the Bearer token in the `Authorization` header. A 401 response on any call redirects immediately to the login screen.

---

## Pattern Learning System

PORTGUARD includes a **Localized Pattern Learning (LPL)** layer that accumulates institutional knowledge from officer feedback and improves risk scores over time. It runs entirely on-device using SQLite — no cloud services, no training pipelines, no data leaving the machine.

### How it works

Every shipment analyzed by `POST /api/v1/analyze` is recorded to a local SQLite database (`portguard_patterns.db`). When an officer submits feedback via `POST /api/v1/feedback`, that outcome updates entity and route risk profiles. Subsequent screenings of the same shipper, consignee, or corridor reflect that accumulated history.

The final risk score blends two views:

```
final_score = rule_score × 0.65 + pattern_score × 0.35
```

When there is insufficient history (cold start), the blend is not applied and the pure rule score is returned unchanged.

### Five pattern signals

| Signal | Weight | Algorithm |
|---|---|---|
| **Shipper reputation** | 0.30 | Bayesian Beta(α=1, β=5 prior): `(w_fraud+1)/(w_fraud+w_cleared+6)`. Blended 60/40 with flag frequency. |
| **Consignee reputation** | 0.20 | Identical algorithm applied to consignee profiles. |
| **Route fraud rate** | 0.20 | Bayesian Beta(α=β=0.5 Jeffrey's prior) per origin-ISO2→port corridor. |
| **Flag frequency** | 0.15 | Sigmoid amplifier: `1/(1+exp(-8×(rate-0.40)))` on 90-day decay-weighted flag rate. |
| **Value anomaly** | 0.15 | Welford online z-score against HS-code value baselines. Triggers only on undervaluation (z < -1). |

All event counts use **exponential temporal decay** (λ=0.023, 30-day half-life) so recent activity outweighs historical events.

### Cold start and trust

- **Cold start**: fewer than 3 prior analyses for the shipper → `effective_pattern_score = pattern_score × 0.5` and the blend is not applied. The response carries `history_available: false`.
- **Auto-trust**: a shipper with ≥20 weighted cleared outcomes and zero confirmed fraud is automatically marked trusted; its scores are clamped to zero.

### Feedback loop

```
POST /api/v1/feedback
{
  "shipment_id": "<id from analyze response>",
  "outcome": "CONFIRMED_FRAUD" | "CLEARED" | "UNRESOLVED",
  "officer_id": "optional",
  "notes": "optional"
}
```

- `CONFIRMED_FRAUD` — increments weighted fraud count for shipper, consignee, and route; increases future risk scores for all three
- `CLEARED` — increments weighted cleared count; reduces future false-positive rates and contributes toward auto-trust
- `UNRESOLVED` — stored for auditability; no scores updated until resolved
- Resolved outcomes (CONFIRMED_FRAUD or CLEARED) are immutable; a second feedback call on the same shipment returns HTTP 409

### Pattern Learning response fields

Every `POST /api/v1/analyze` response includes:

| Field | Type | Description |
|---|---|---|
| `shipment_id` | string \| null | ID to pass to `/api/v1/feedback` |
| `pattern_score` | float \| null | Raw pattern risk score (0–1). Null when history is insufficient. |
| `history_available` | bool | True when the pattern engine contributed to the final risk score. |
| `pattern_signals` | list[string] | Plain-English explanations from triggered pattern signals, sorted by severity. |

### Configuration

| Env var | Default | Description |
|---|---|---|
| `PORTGUARD_PATTERN_LEARNING_ENABLED` | `true` | Set to `false`, `0`, or `no` to disable entirely. |
| `PORTGUARD_PATTERN_DB_PATH` | `portguard_patterns.db` | SQLite DB path (local dev only — ignored when `DATABASE_URL` is set). |

The app starts and operates rule-only if the database is inaccessible — pattern learning failures are never fatal.

### Demo UI

The browser demo (`GET /demo`) surfaces pattern learning in three panels:

- **Pattern Intelligence** — shown after each analysis; displays history depth ("Based on 12 prior shipments"), animated pattern score gauge, and signal cards color-coded by severity (CRITICAL=red, HIGH=orange, MEDIUM=amber, LOW=blue).
- **Officer Feedback** — shown after flagged results; two buttons ("✓ Confirmed Fraud" / "✗ Cleared") post to `/api/v1/feedback` and display a contextual confirmation message. Buttons are disabled after submission.
- **Pattern Learning History** — always visible; loads aggregate stats from `GET /api/v1/pattern-history` on demand: total shipments, total confirmed fraud, top-5 riskiest shippers and routes.

---

## Analytics Dashboard

PORTGUARD includes a full analytics dashboard that visualizes shipment history, fraud trends, and entity intelligence accumulated by the pattern learning system. It is built into the browser demo at `GET /demo` and backed by six read-only API endpoints.

### Accessing the dashboard

After logging in at `GET /demo`, click the **Dashboard** tab in the navigation bar below the header. The dashboard loads all data on first visit and auto-refreshes every 60 seconds. The activity feed refreshes independently every 30 seconds.

### Summary KPI cards

| Card | Value | Threshold coloring |
|---|---|---|
| Total Shipments | Count of all analyzed shipments for the org | — |
| Fraud Rate | Confirmed fraud / total shipments (%) | Green < 5%, amber 5–15%, red > 15% |
| Confirmed Fraud | Count of `CONFIRMED_FRAUD` outcomes | Red when > 0 |
| Avg Risk Score | Mean `final_risk_score` × 100 (0–100) | Amber > 40, red > 65 |
| Pattern History | Shipment count in the pattern learning DB | — |

### Charts

**Fraud Rate Trend (30 days)** — Dual-axis line chart. Left axis: fraud rate percentage (red fill). Right axis: total shipment count (blue dashed). X-axis always shows all 30 days; days with no data appear as zero rather than a gap.

**Decision Breakdown** — Doughnut chart showing the split across all five decision types with an inline legend showing count and percentage per category. Decision type colors: green = APPROVE, purple = REVIEW RECOMMENDED, orange = FLAG FOR INSPECTION, amber = MORE INFO, red = REJECT.

**Top Origin Countries by Fraud Rate** — Horizontal bar chart of up to 10 countries ranked by confirmed-fraud rate. Bar color: red ≥ 50%, orange 20–50%, blue < 20%.

**Top Shippers by Flag Count** — Horizontal bar chart of up to 10 shipper profiles ranked by confirmed-fraud count. Red bars = untrusted, green bars = auto-trusted (≥20 cleared outcomes, zero confirmed fraud).

### Recent Activity feed

Table of the 20 most recent shipments, newest first. Columns: time, shipper, origin ISO-2, decision badge, risk score (0–100), officer outcome (Fraud / Cleared / — if no feedback yet), pattern signals. Auto-refreshes every 30 seconds without reloading charts.

### Empty state

When no shipments have been analyzed yet, the dashboard shows a single message — *"No shipments analyzed yet. Run your first analysis to see trends appear here."* — with a button that navigates back to the Analyze tab. No empty charts are rendered.

### Dashboard API endpoints

All six endpoints require a valid Bearer token and scope all data to the authenticated organization.

| Method | Path | Query params | Description |
|---|---|---|---|
| GET | `/api/v1/dashboard/summary` | — | KPI totals: shipment count, confirmed fraud, cleared, unresolved, fraud rate, avg risk score, avg pattern score |
| GET | `/api/v1/dashboard/decisions` | — | Count and percentage for each of the 5 decision types; always returns all 5 even if count is zero |
| GET | `/api/v1/dashboard/fraud-trend` | `days` (1–365, default 30) | Daily series with total shipments and confirmed-fraud count; always returns exactly `days` entries with gap-filling |
| GET | `/api/v1/dashboard/top-countries` | `limit` (1–50, default 10) | Countries ranked by confirmed-fraud count then avg risk score |
| GET | `/api/v1/dashboard/top-shippers` | `limit` (1–50, default 10) | Shipper profiles ranked by confirmed-fraud count then reputation score |
| GET | `/api/v1/dashboard/top-hs-codes` | `limit` (1–50, default 10) | HS chapters (2-digit) ranked by flagged-shipment count |
| GET | `/api/v1/dashboard/recent-activity` | `limit` (1–100, default 20) | Latest shipments with decision, risk score, and officer outcome if submitted |

All endpoints return zeros and empty arrays when no history exists — they never return errors for an empty database.

**Example — summary response:**
```json
{
  "total_shipments": 42,
  "total_confirmed_fraud": 7,
  "total_cleared": 12,
  "total_unresolved": 23,
  "fraud_rate": 0.1667,
  "avg_risk_score": 0.4821,
  "avg_pattern_score": 0.3104
}
```

**Example — fraud-trend entry:**
```json
{ "day": "2026-04-11", "total": 8, "fraud_count": 2, "fraud_rate": 0.25 }
```

### Implementation

The dashboard backend is `portguard/analytics.py` — a `DashboardAnalytics` class that opens its own read-only SQLite connection to `portguard_patterns.db` (separate from `PatternDB`'s write connection; WAL mode allows concurrent readers). All seven query methods return safe defaults on any error and check `self.available` before querying, so a missing or inaccessible database never crashes the API.

The frontend uses **Chart.js 4.4** loaded via CDN (no build step). Charts are destroyed and rebuilt on each refresh to prevent canvas reuse errors. Skeleton pulse placeholders are shown while data loads so the layout never flashes blank.

---

## PDF Compliance Report Export

Every analyzed shipment can be exported as a print-ready PDF compliance report suitable for submission to CBP officers, supervisors, or legal counsel. Reports are generated server-side using **fpdf2** — no external services, no cloud rendering.

### Generating a report

**From the browser demo** — after a successful analysis, a **Download Compliance Report** button appears in the top-right of the results panel. Click it to download the PDF immediately. Each row in the Recent Activity feed also has a per-shipment download icon that fetches the same report by `analysis_id`.

**Via API:**

```bash
# By shipment ID (stored analysis)
curl -X POST http://localhost:8000/api/v1/report/generate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"shipment_id": "<shipment_id from analyze response>"}' \
  --output PortGuard_Report_2026-04-12.pdf

# Direct generation (pass the full analyze response body)
curl -X POST http://localhost:8000/api/v1/report/generate-direct \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @analyze_response.json \
  --output PortGuard_Report_2026-04-12.pdf
```

`POST /api/v1/report/generate` looks up the stored analysis payload for the given `shipment_id` (scoped to the authenticated organization) and generates the PDF. If the shipment does not exist or belongs to a different organization, it returns HTTP 404 with a structured error body:

```json
{
  "code": "REPORT_NOT_AVAILABLE",
  "message": "No report payload found for shipment '...'. ..."
}
```

`POST /api/v1/report/generate-direct` accepts the full `AnalyzeResponse` JSON body and generates a PDF without a database lookup — useful for testing or for shipments analyzed before the PDF feature was deployed.

Both endpoints return `Content-Type: application/pdf` with `Content-Disposition: attachment; filename="PortGuard_Report_<shipment_id>.pdf"`.

### Report layout

The PDF is formatted for **US Letter** at 72 dpi and designed to be legible when printed in black-and-white.

| Section | Content |
|---|---|
| **Decision banner** | Full-width color block — green (APPROVE), red (REJECT), orange (FLAG FOR INSPECTION), amber (REQUEST MORE INFORMATION), purple (REVIEW RECOMMENDED). Decision label at 26pt bold with confidence badge. Dominant first element; readable in <1 second. |
| **Key metrics strip** | Four boxes: Risk Score (0–100), Risk Level, Documents Analyzed, Inconsistencies Found. |
| **Report information** | Two-column table: Shipment ID, Organization, Analysis Engine version, Analyzed At timestamp. |
| **Shipment summary** | Two-column table: Importer, Exporter, Origin Country, Commodity, Declared Value, HTS Codes, Ports. Long values wrap within their cell — no truncation. |
| **Risk assessment** | Labeled risk bar with gray track, colored fill (green/amber/orange/red), tick marks at 25/50/75%, zone labels (LOW / MEDIUM / HIGH / CRITICAL), and a percentage label at the fill endpoint. B&W-safe via tick marks and zone text. |
| **Compliance findings** | Numbered list of all triggered findings with severity badges (CRITICAL / HIGH / MEDIUM / LOW / INFO). |
| **Compliance grid** | One row per rule engine (OFAC Sanctions, Section 301, Section 232, AD/CVD, UFLPA, ISF Completeness, PGA Requirements, Document Consistency, Valuation). Each row shows Pass/Warning/Fail status and detail. |
| **Pattern intelligence** | Pattern score gauge, history depth, and all triggered pattern signals — shown only when history is available. |
| **Recommended next steps** | Numbered action list generated by the decision engine. |
| **Officer review** | Signature fields: Officer Name, Badge / Employee ID, Date of Review (standard height), Signature (20mm — sized for a real signature). Five ruled note lines at 10mm spacing. |
| **Legal disclaimer** | Begins on a new page if fewer than ~55mm remain. States that the report is generated by an automated system, that all enforcement decisions must be made by a qualified CBP officer under applicable federal law, and that the system does not constitute legal advice. |

Page numbers appear in the footer of every page as "Page N of M".

### Dependencies

`fpdf2` is required for PDF generation. It is included in `requirements.txt`. No additional fonts or system packages are needed — reports use the built-in Helvetica family which is Latin-1 safe.

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

Dependencies: `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `python-dotenv`, `httpx`, `pdfplumber`, `fpdf2`, `python-multipart`, `python-jose[cryptography]`, `bcrypt`, `sqlalchemy`, `psycopg2-binary`, `pytest`, `pytest-asyncio`, `pytest-mock`.

---

## Deploying to Render

PortGuard ships with a `render.yaml` Blueprint that defines the web service and PostgreSQL database together. All tables are created automatically on first startup — no manual migration step required.

### Option A — Blueprint deploy (recommended)

1. Push the repo to GitHub (already done).
2. In the Render dashboard → **New** → **Blueprint** → select the `PORTGUARD` repository.
3. Render reads `render.yaml` and creates:
   - `portguard-db` — a PostgreSQL database (Starter plan, ~$7/month)
   - `portguard` — a free Python web service with `DATABASE_URL` automatically wired from the database
4. After both resources are created, open the **portguard** web service → **Environment** tab.
5. Set `PORTGUARD_JWT_SECRET` to a secure 64-character hex string:
   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
   Paste the output as the value. Without this, all sessions are lost on every redeploy.
6. Click **Save Changes** — Render redeploys automatically.
7. Visit `https://your-service.onrender.com/api/v1/health` to confirm the service is live.

### Option B — Manual setup

If you prefer to create resources individually:

**Step 1 — Create the PostgreSQL database**

1. In the Render dashboard → **New** → **PostgreSQL**
2. Name: `portguard-db`, Database: `portguard`, User: `portguard`
3. Select a plan (Starter recommended) → **Create Database**
4. Once created, open the database → **Info** tab → copy the **Internal Database URL**

**Step 2 — Create the web service**

1. **New** → **Web Service** → connect the `PORTGUARD` GitHub repository
2. Runtime: **Python 3**, Build command: `pip install -r requirements.txt`
3. Start command: `python -m uvicorn api.app:app --host 0.0.0.0 --port $PORT`
4. Under **Environment Variables**, add:

   | Key | Value |
   |---|---|
   | `DATABASE_URL` | Paste the Internal Database URL from Step 1 |
   | `PORTGUARD_JWT_SECRET` | Output of `python -c "import secrets; print(secrets.token_hex(32))"` |
   | `PORTGUARD_PATTERN_LEARNING_ENABLED` | `true` |

5. Click **Create Web Service** → Render builds and deploys.

### What happens on first deploy

When the app starts against an empty PostgreSQL database it automatically runs all schema migrations:

- Auth tables: `organizations`, `auth_token_revocations`, `auth_login_attempts`
- Pattern tables: `shipment_history`, `pattern_outcomes`, `shipper_profiles`, `consignee_profiles`, `route_risk_profiles`, `hs_code_baselines`, `schema_migrations`

No manual `CREATE TABLE` or `psql` commands are needed.

### Environment variables reference

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes (production) | PostgreSQL connection string. When absent the app falls back to SQLite files (local dev only). |
| `PORTGUARD_JWT_SECRET` | Yes (production) | 64-char hex JWT signing key. Random fallback invalidates sessions on every restart. |
| `PORTGUARD_PATTERN_LEARNING_ENABLED` | No | Set to `false` to disable pattern learning entirely. Default: `true`. |
| `PORTGUARD_AUTH_DB_PATH` | No | SQLite auth DB path (local dev only, ignored when `DATABASE_URL` is set). Default: `portguard_auth.db`. |
| `PORTGUARD_PATTERN_DB_PATH` | No | SQLite pattern DB path (local dev only, ignored when `DATABASE_URL` is set). Default: `portguard_patterns.db`. |

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
# Health check (no auth required)
curl http://localhost:8000/api/v1/health

# Register an organization
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"org_name": "Acme Customs LLC", "email": "ops@acme.io", "password": "s3cur3pass!"}'

# Log in and capture the token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "ops@acme.io", "password": "s3cur3pass!"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Upload a document and extract its text
curl -X POST http://localhost:8000/api/v1/extract-text \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@bill_of_lading.pdf"

# Analyze a shipment
curl -X POST http://localhost:8000/api/v1/analyze \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @tests/sample_documents/02_suspicious_shipment.json

# Log out (revokes the token server-side)
curl -X POST http://localhost:8000/api/v1/auth/logout \
  -H "Authorization: Bearer $TOKEN"
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
  "processing_time_seconds": 0.003,
  "shipment_id": "a3f7c2d1-...",
  "pattern_score": 0.62,
  "history_available": true,
  "pattern_signals": [
    "Shipper 'Dragon Phoenix Trading Ltd': 5 prior shipment(s). 3 confirmed fraud outcome(s) (Bayesian reputation score: 0.44). Blended signal score: 0.71.",
    "Route 'CN → Port of Miami': Bayesian fraud rate 60.0%. Exceeds 30% alert threshold."
  ]
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
# All tests
python -m pytest

# Pattern learning tests only
python -m pytest portguard/tests/

# End-to-end pipeline integration test
python test_e2e_pattern_learning.py
```

163 tests across 9 files, all passing:

```
tests/test_api.py                       7 tests   FastAPI routes (httpx AsyncClient)
tests/test_parser.py                    4 tests   ParserAgent
tests/test_classifier.py               4 tests   ClassifierAgent
tests/test_validator.py                4 tests   ValidationAgent
tests/test_risk.py                      5 tests   RiskAgent (Section 301, OFAC, AD/CVD, Section 232)
tests/test_decision.py                  5 tests   DecisionAgent (CLEAR/REVIEW/HOLD/REJECT)
tests/test_orchestrator.py             4 tests   Pipeline fault tolerance
portguard/tests/test_pattern_db.py     59 tests   PatternDB data layer (normalization,
                                                   Bayesian scoring, Welford, migrations,
                                                   CRUD, auto-trust, decay weights)
portguard/tests/test_pattern_engine.py 71 tests   PatternEngine scoring (all 5 signals,
                                                   cold start, composite score, explanations)
```

The end-to-end test (`test_e2e_pattern_learning.py`) runs 36 checks across 8 scenarios against a live FastAPI TestClient with an isolated temporary SQLite database: health check, 5-shipment analysis, DB state verification, CONFIRMED_FRAUD feedback, repeat offender detection, cold start behavior, pattern history endpoint, and edge cases (duplicate feedback, unknown ID, invalid outcome).

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
- **Single-node auth storage.** When running against PostgreSQL the auth and pattern databases share one connection pool — suitable for single-instance Render deployments. For multi-instance horizontal scaling, configure a connection pooler (e.g., PgBouncer) and ensure `PORTGUARD_JWT_SECRET` is set identically on all instances.
- **Classification is not legally binding.** HTS classifications and duty rate estimates are for preliminary screening only. A licensed customs broker must file the actual entry.
