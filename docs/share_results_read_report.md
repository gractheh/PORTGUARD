# Share Results Read Report
**Date:** 2026-05-17
**Sprint:** 13 — read-only audit of the shared result flow

---

## 1. The Share Button

There are three separate share buttons in the codebase.

### 1a. Single-analysis share button (`#single-share-btn`)

**Location:** `demo.html` line 4273, inside `#results > .report-btn-row`, sitting immediately next to the "Download Compliance Report" button.

```html
<button id="single-share-btn" onclick="singleShareResult()">
  ...
  <span id="single-share-label">Share Result</span>
</button>
```

**Visibility:** Hidden by default (`style.display = 'none'`, reset at lines 7264 and 7500 when analysis starts). Only shown when `data.shipment_id` is present in the analysis response (line 8002–8009). If the analysis returns no `shipment_id` (e.g., pre-migration-004 rows), the button stays hidden.

**What it does:** `singleShareResult()` at line 8174.
1. Reads `btn.dataset.shipmentId` — set to `data.shipment_id` (the UUID from `shipment_history.analysis_id`) by `renderResults()` at line 8006.
2. Builds URL: `window.location.origin + window.location.pathname + '#result/' + shipmentId`
3. Copies URL to clipboard via `navigator.clipboard.writeText()`.
4. Shows "Copied!" label for 2 seconds; shows a toast: "Link copied! Anyone with this link can view the result."
5. Falls back to `prompt()` if clipboard API fails.

**URL format:** `https://host/demo#result/<uuid>`  — a hash fragment, NOT a query parameter.

### 1b. Bulk per-row share button (`.bulk-share-row-btn`)

**Location:** `demo.html` line 11224, rendered inside each row of the bulk results table.

**What it does:** `bulkShareRowLink(resultId, btnEl)` at line 11488.
Same URL format: `#result/<result_id>` where `result_id` is `r.result_id || r.analysis_id` from the bulk response row.

### 1c. Bulk batch share button (`#bulk-share-btn`)

**Location:** `demo.html` line 4907, in the bulk export bar.

**What it does:** `bulkShareLink()` at line 11473.
URL format: `?batch=<_bulkBatchId>` — a **query parameter**, not a hash. Different mechanism from the single-result share.

---

## 2. The URL Parameter — Is It Read on Page Load?

The share format is `#result/<id>` (hash fragment, NOT `?result=`). There is no `?result=` query parameter.

**Reading happens in `_readDeepLink()` at line 11662**, an IIFE that runs at page load before the auth overlay is shown:

```javascript
(function _readDeepLink() {
  const batchParam = new URLSearchParams(window.location.search).get('batch');
  if (batchParam) { _deepLinkBatchId = batchParam; }
  const hash = window.location.hash;
  const resultMatch = hash.match(/^#result\/(.+)/);
  if (resultMatch) { _deepLinkResultId = resultMatch[1]; }
})();
```

`_deepLinkResultId` (declared at line 10081) and `_deepLinkBatchId` are module-level `let` variables initialized to `null`.

**Acting on the deep-link happens in `hideAuthOverlay()` at line 4994**, called after successful login:

```javascript
if (_deepLinkResultId) {
  const rid = _deepLinkResultId;
  _deepLinkResultId = null;   // ← nulled immediately so re-login doesn't re-trigger
  showSection('analyze');
  _loadResultById(rid);
} else if (_deepLinkBatchId) { ... }
```

So the hash IS read on page load, stored in a variable, and acted on after login completes. The hash is also cleared from the browser URL immediately after reading, inside `_loadResultById()` at line 11580:

```javascript
history.replaceState(null, '', window.location.pathname + window.location.search);
```

This means **the URL in the address bar no longer contains `#result/<id>` after the result loads** — navigating away and pressing Back will not reload the shared result.

---

## 3. The Current Shared Result View — What Does It Show?

`_loadResultById(resultId)` (line 11578):
1. Fetches `GET /api/v1/results/<resultId>`
2. On success: calls `renderResults(data)` then `_enterSharedResultView(data)`

**`renderResults(data)` runs the complete normal results render** — this is not a stripped-down shared view. It builds every section (see Section 6 for the full list). It is **not** just the pattern learning widget.

**`_enterSharedResultView(data)` (line 11612)** then:
- Adds class `shared-result-view` to `#analyze-panel`, which via CSS hides: `.hero`, `.section-label`, `.quick-load`, `#scenario-label`, `#tabs-bar`, `#doc-pane`, `.analyze-wrap`, `#agent-pipeline`, `#rejection-screen`
- Prepends a `.shared-result-banner` element to `#results` containing:
  - Clipboard report icon (SVG)
  - "Shared Result — Viewing a shared compliance report"
  - Sub-line with the `analyzed_at` date (if present), else "This is a read-only shared compliance report."
  - "Run Your Own Analysis →" button that calls `_exitSharedResultView()`

**What the shared view actually shows (full list):**
1. Shared result banner (new — prepended by `_enterSharedResultView`)
2. Download Compliance Report button + Share Result button (shown if `data.shipment_id` present)
3. Validation warning banner (if applicable)
4. Classifier warning banner (if applicable)
5. Decision banner (color-coded, icon, label, sub-text, doc-type chip, sustainability badge, meta stats)
6. Risk gauge (animated SVG arc, numeric score, risk level label)
7. Assessment details card (confidence, risk level, origin, commodity, declared value)
8. Findings & Inconsistencies card (full `explanations` list)
9. Sustainability & Certifications card (if grade is not N/A)
10. Recommended Next Steps card
11. Pattern Intelligence section (if `data.pattern_intelligence` is present)
12. Officer Feedback section (if flagged decision and `shipment_id` present)
13. Extracted Shipment Data (collapsible grid — 14 fields)

The pattern learning **history stats panel** (the persistent `#pattern-stats-panel`) is also refreshed by `loadPatternStats()` called inside `renderResults()` — it shows the VIEWER's org's pattern stats.

---

## 4. GET /api/v1/results/{id} — Does It Exist? What Does It Return?

**Yes, it exists.** Defined at `api/app.py` line 3218:

```python
@app.get("/api/v1/results/{result_id}")
def get_result(result_id: str, current_org: dict = Depends(get_current_organization))
```

**Auth:** JWT Bearer token required (same as all other authenticated endpoints). There is no unauthenticated path.

**Logic:**
1. Calls `_pattern_db.get_report_payload(result_id, org_id)` — queries `shipment_history.report_payload WHERE analysis_id = result_id AND organization_id = org_id`
2. If found: parses JSON. Injects `shipment_id = result_id` into the payload (because `shipment_id` is null in the stored payload — serialized before the DB write assigned it). Tries to inject `analyzed_at` from `shipment_history.analyzed_at` if not already in the payload (wrapped in try/except — silent fail). Returns the dict.
3. If not found in this org: calls `get_result_owner(result_id)` (no org filter). If no row at all → HTTP 404. If row exists but belongs to a different org → HTTP 403.

**Returns:** The full serialized `AnalyzeResponse` JSON — same shape as the `POST /api/v1/analyze` response — plus `shipment_id` and `analyzed_at` injected by the endpoint.

**Key constraints:**
- Only retrieves from `shipment_history.report_payload` — the single-analysis table
- Does NOT retrieve from `bulk_shipments.result_json` — bulk results are invisible to this endpoint
- `report_payload` is stored via `store_report_payload()` called from `_record_shipment_bg()` background task — if that task fails (silently, non-fatal), `report_payload` is NULL and the endpoint returns 404
- Org-scoped: cross-org access returns 403, not 404

---

## 5. The ScreeningResult Model — What Fields Does It Have?

There is no class named `ScreeningResult` in the codebase. The models are:

### `AnalyzeResponse` (api/app.py line 1127) — THE canonical result model

This is what `GET /api/v1/results/{id}` returns and what `renderResults()` consumes.

| Field | Type | Notes |
|---|---|---|
| `status` | `str` | |
| `shipment_data` | `ShipmentData` | Sub-object with all extracted fields (see below) |
| `risk_score` | `float [0,1]` | |
| `risk_level` | `str` | LOW / MEDIUM / HIGH / CRITICAL |
| `decision` | `str` | APPROVE / REVIEW_RECOMMENDED / FLAG_FOR_INSPECTION / REQUEST_MORE_INFORMATION / REJECT |
| `confidence` | `str` | HIGH / MEDIUM / LOW |
| `explanations` | `list[str]` | The flags list — "Findings" in the UI |
| `recommended_next_steps` | `list[str]` | |
| `inconsistencies_found` | `int` | |
| `documents_analyzed` | `int` | |
| `processing_time_seconds` | `float` | |
| `shipment_id` | `Optional[str]` | UUID assigned to the `shipment_history` row |
| `pattern_score` | `Optional[float]` | Raw pattern risk score 0-1 |
| `history_available` | `bool` | True when pattern engine had enough history |
| `pattern_signals` | `list[str]` | Plain-English pattern signal explanations |
| `pattern_history_depth` | `Optional[int]` | Prior analyses count for the shipper |
| `validation_warnings` | `list[str]` | LOW-confidence document warnings |
| `document_validations` | `list[dict]` | Per-doc type/confidence/verdict metadata |
| `sustainability_rating` | `Optional[SustainabilityRating]` | Grade A-D/N/A + signals + cert lists |
| `module_findings` | `list[ModuleFinding]` | Per-certification-module findings |
| `active_modules_at_scan` | `list[str]` | Module IDs evaluated |
| `modules_triggered` | `list[str]` | Module IDs that produced findings |
| `document_type` | `Optional[str]` | Human-readable type e.g. "Bill of Lading" |
| `document_type_code` | `Optional[str]` | Short code e.g. "BOL", "CI", "PL" |
| `classification_confidence` | `Optional[str]` | HIGH / MEDIUM / LOW |
| `classification_warning` | `Optional[str]` | Warning text when LOW confidence accepted |
| `pattern_intelligence` | `Optional[dict]` | `{hard_flag, hard_flag_reason, adjustments_applied, pattern_warnings, pattern_boosts, shipper_history}` |
| `ofac_hit` | `Optional[bool]` | True if OFAC/sanctions flag raised |
| `section301_hit` | `Optional[bool]` | True if Section 301 flag raised |
| `adcvd_hit` | `Optional[bool]` | True if AD/CVD flag raised |
| `uflpa_hit` | `Optional[bool]` | True if UFLPA/forced labor flag raised |
| `isf_complete` | `Optional[bool]` | True/False/None (None = non-sea shipment) |

`ShipmentData` sub-object fields (inlined from extraction): `importer`, `exporter`, `consignee`, `notify_party`, `origin_country`, `origin_country_iso2`, `destination_country`, `port_of_loading`, `port_of_discharge`, `port_of_entry`, `vessel_or_flight`, `bill_of_lading_number`, `shipment_date`, `arrival_date`, `incoterms`, `hts_codes_declared` (list), `commodity_description`, `declared_value`, `declared_currency`, `gross_weight`, `quantity`, `marks_and_numbers`.

### `ScreeningReport` (portguard/models/report.py) — Entry Point 2 model

Used by the structured `/api/v1/screen` pipeline, NOT by the document analysis path or the share flow. Fields: `report_id`, `created_at`, `shipment_input`, `parsed_shipment`, `classification_result`, `validation_result`, `risk_assessment`, `decision`, `pipeline_errors`, `processing_time_ms`, `model_used`, `shipment_id`, `pattern_score`, `pattern_effective_score`, `history_available`, `pattern_signals`, `pattern_history_depth`.

---

## 6. What the Main Results Screen Looks Like

The `#results` div (line 4263) is initially `display:none` and has no `visible` class. `renderResults()` sets `display:block` and adds `visible` at the end, triggering CSS fade-up animations.

**Complete DOM structure of `#results` in source order:**

### Report button row (`.report-btn-row`)
- `#download-report-btn` — "Download Compliance Report" button. Calls `downloadReport()` → `POST /api/v1/report/generate`. Hidden if no `shipment_id`.
- `#single-share-btn` — "Share Result" button. Hidden if no `shipment_id`.
- `#report-status` — Inline status text ("Report downloaded" / error message).

### Warning banners
- `#validation-warning-banner` — shown when `data.validation_warnings.length > 0`. Lists LOW-confidence document warnings.
- `#clf-warning-banner` — shown when `data.classification_warning` is set. Single-line classifier confidence warning.

### Decision banner (`#decision-banner`)
- `#decision-icon` — SVG icon (checkmark / exclamation / X depending on decision)
- `#decision-name` — Decision label text (e.g. "Approved for Release") + `#clf-doc-type-chip` appended by JS if `data.document_type` is present
- `#decision-sub` — One-line sub-text (e.g. "No significant compliance violations detected.")
- `#sustainability-badge` — Grade badge (e.g. "Sustainability: A") shown inline if grade is not N/A
- Meta row: `#meta-docs` (docs analyzed), `#meta-issues` (inconsistencies), `#meta-time` (seconds)

### Cards row (`.cards-row`) — two side-by-side cards
**Risk Score card:**
- SVG arc gauge (180° arc from 0 to 1.0)
- `#gauge-fill` — animated stroke-dasharray fill, color changes with risk level
- `#gauge-score` — numeric score with count-up animation (1.1s cubic-bezier)
- `#gauge-risk` — risk level text (LOW/MEDIUM/HIGH/CRITICAL)
- Corner labels: 0 and 1.0

**Assessment Details card:**
- `#stat-confidence` — confidence badge (HIGH/MEDIUM/LOW)
- `#stat-risk-level` — risk level text
- `#stat-origin` — origin country (hidden if not extracted)
- `#stat-commodity` — commodity description, truncated at 45 chars (hidden if not extracted)
- `#stat-value` — declared value with currency (hidden if not extracted)

### Findings card (`#findings-card`)
- Title: "Findings & Inconsistencies"
- `#findings-list` — `<ul>` built from `data.explanations`. Each item has a `►` bullet. If empty: "No significant findings."

### Sustainability card (`#sustainability-card`, initially `display:none`)
- Title: "Sustainability & Certifications"
- `#sustain-content` — built by `renderSustainability(data)`:
  - Grade letter (large, color-coded A/B/C/D)
  - Grade label text
  - Risk pills: Inherent Risk / Country Risk / Product Risk
  - Signals list (`rating.signals`)
  - Certifications Detected list (cert chips, green)
  - Recommended Certifications list (cert chips, amber)
  - Screening Modules Triggered list

### Recommended Next Steps card (`#steps-card`)
- `#steps-list` — `<ul>` built from `data.recommended_next_steps`. Numbered items. If empty: "No immediate action required."

### Pattern Intelligence section (`#pattern-intel-result`, initially `display:none`)
- Built by `renderPatternIntelResult(data.pattern_intelligence)` at line 8672.
- Hidden if `data.pattern_intelligence` is null/undefined.
- When present, shows:
  - Hard flag warning (red, if `pi.hard_flag` is true)
  - Pattern warnings (amber, each as its own div)
  - Pattern boosts (green, with checkmark icon)
  - Shipper history text (e.g. "Based on 12 prior shipments")
  - "No anomalous patterns detected" neutral text if no warnings or hard_flag

### Officer Feedback section (`#feedback-section`, initially `display:none`)
- Shown only when: decision is FLAG_FOR_INSPECTION / REVIEW_RECOMMENDED / REJECT AND `data.shipment_id` is present.
- Contains: "Confirmed Fraud" button and "Cleared — Legitimate" button.
- On submit: `POST /api/v1/feedback`. Disables buttons after submission. Shows confirmation text.

### Shipment Data card (always visible after results render)
- Toggle button: "Extracted Shipment Data" with `#toggle-arrow`
- `#shipment-data-grid` — 14 fields displayed as key/value pairs: Importer, Exporter, Consignee, Origin, Port of Loading, Port of Discharge, Shipment Date, Incoterms, Vessel/Flight, B/L Number, HTS Codes, Declared Value, Gross Weight, Quantity.

**What is NOT in the results view:**
- The compliance hit booleans (`ofac_hit`, `section301_hit`, `adcvd_hit`, `uflpa_hit`, `isf_complete`) — these exist in `AnalyzeResponse` (Sprint 12) but are NOT displayed in the results UI. They are only used in CSV export.
- No dedicated "Compliance Layer" section — the compliance hits are surfaced only as text in `explanations` (the Findings list).

---

## 7. The Exact Function That Renders Results

**Function name: `renderResults(data)`**
**Location:** `demo.html` line 7817
**Called by:** The `POST /api/v1/analyze` submission handler (success path) and `_loadResultById()` (shared result path)

**What it builds, in exact order:**

1. Calls `_exitSharedResultView()` — removes any previous shared-view state
2. Resolves decision config from `DECISION_CONFIG[decision]`
3. Shows/clears `#validation-warning-banner` based on `data.validation_warnings`
4. Shows/clears `#clf-warning-banner` based on `data.classification_warning`
5. Calls `updateTabBadges(data.document_validations)` — updates tab badges with detected types
6. Sets `#decision-banner` class, `#decision-icon`, `#decision-name`, `#decision-sub`
7. Removes any stale `#clf-doc-type-chip`; appends new chip if `data.document_type` is set
8. Sets `#meta-docs`, `#meta-issues`, `#meta-time`
9. Resets gauge to 0 (no transition), then after 80ms animates fill and runs count-up to `data.risk_score`
10. Sets `#stat-confidence` (badge), `#stat-risk-level`
11. Shows `#origin-item` / `#stat-origin` if `sd.origin_country` is present
12. Shows `#commodity-item` / `#stat-commodity` if `sd.commodity_description` is present
13. Shows `#value-item` / `#stat-value` if `sd.declared_value` is present
14. Rebuilds `#findings-list` from `data.explanations`
15. Rebuilds `#steps-list` from `data.recommended_next_steps`
16. Rebuilds `#shipment-data-grid` (resets className, closes grid) from 14 `shipment_data` fields
17. Calls `renderSustainability(data)` — builds or hides `#sustainability-card`
18. Calls `renderPatternIntelResult(data.pattern_intelligence)` — builds or hides `#pattern-intel-result`
19. Calls `renderFeedbackUI(data)` — shows/hides `#feedback-section`
20. Calls `loadPatternStats()` — refreshes the persistent pattern history panel
21. Shows/hides `#download-report-btn` and `#single-share-btn` based on `data.shipment_id`
22. Sets `#results` to `display:block`, adds class `visible`, calls `scrollIntoView` after 100ms

---

## 8. What `_lastResultId` Is

**There is no `_lastResultId` variable in the codebase.**

The report prompt asked about this, but it does not exist. The relevant related variables are:

- **`_deepLinkResultId`** (line 10081) — `let` variable initialized to `null`. Set by `_readDeepLink()` from the `#result/<id>` hash on page load. Nulled immediately after being read in `hideAuthOverlay()`.
- **`_lastShipmentId`** (line 8729) — set in `renderFeedbackUI(data)` to `data.shipment_id`. Used by `submitFeedback()` to identify which shipment to send feedback for.
- **`btn.dataset.shipmentId`** (line 8006) — `data.shipment_id` stored as a DOM data attribute on the `#single-share-btn` element. Read by `singleShareResult()` to construct the share URL.
- **`_bulkBatchId`** (line 10072) — used by the batch share flow.

---

## 9. Share URL Approach — ID in URL vs. ID in Result Data

The **ID is stored in the URL (hash fragment)**.

**Share URL format:** `https://host/demo#result/<shipment_id>`

Where `shipment_id` = the `analysis_id` UUID assigned to the row in `shipment_history` (written by `record_shipment()`, returned as `AnalyzeResponse.shipment_id`).

The result data returned by `GET /api/v1/results/<id>` also contains `shipment_id` (injected by the endpoint at line 3244 since it's null in the stored payload), but the URL is the canonical source.

**Full chain:**
1. `POST /api/v1/analyze` → `AnalyzeResponse.shipment_id` = UUID
2. `renderResults()` stores UUID on `#single-share-btn.dataset.shipmentId`
3. `singleShareResult()` builds `#result/<UUID>` URL
4. Page load: `_readDeepLink()` extracts UUID from hash into `_deepLinkResultId`
5. After login: `_loadResultById(UUID)` fetches `GET /api/v1/results/<UUID>`
6. Backend looks up `shipment_history.report_payload WHERE analysis_id = <UUID> AND organization_id = <org>`
7. Returns full `AnalyzeResponse` JSON

---

## 10. Every Single Thing That Makes the Current Shared View Inadequate

### Bug 1 — Authentication required; "anyone" claim is false (CRITICAL)

`singleShareResult()` at line 8183 shows a toast: **"Link copied! Anyone with this link can view the result."** This is factually wrong.

`GET /api/v1/results/{result_id}` uses `Depends(get_current_organization)` — full JWT auth is required. There is no unauthenticated public path.

Furthermore, JWTs are stored only in JS memory (`_authToken` variable), never in `localStorage` or `sessionStorage`. Every page load shows the auth overlay — the recipient must log in before seeing anything.

So sharing with a client, customs officer, or auditor who doesn't have a PortGuard account is impossible. The link is useless to anyone outside the system.

### Bug 2 — Organization-scoped; cross-org sharing returns 403

The endpoint filters `report_payload` by `organization_id`. If a user from Org A shares a link with someone from Org B (both valid PortGuard accounts), Org B gets HTTP 403: "This result belongs to a different organization." There is no cross-org sharing mechanism.

### Bug 3 — Bulk per-row share links return 404 (CRITICAL)

The per-row share button in the bulk results table generates `#result/<result_id>` URLs. `result_id` = `bulk_shipments.analysis_id`.

`GET /api/v1/results/{id}` calls `get_report_payload(result_id, org_id)` which queries `shipment_history.report_payload WHERE analysis_id = result_id`. Bulk results are stored in `bulk_shipments.result_json`, NOT in `shipment_history`. So:
- `get_report_payload()` returns `None`
- `get_result_owner()` also queries only `shipment_history` — returns `None`
- Endpoint returns HTTP 404

**All bulk per-row share links are broken.** They generate a valid-looking URL but the backend returns 404.

(The batch-level share `?batch=<id>` works differently — it hits `/api/v1/analyze/bulk/<id>/status` which does look in `bulk_shipments` — that path is functional.)

### Bug 4 — `report_payload` can be NULL for valid analyses

`store_report_payload()` is called from the background task `_record_shipment_bg()`. If the background write fails (logged as non-fatal at line 1417: `"store_report_payload(%s) failed (non-fatal): %s"`), `report_payload` stays NULL.

In that case:
- `get_report_payload()` returns `None`
- The endpoint goes to `get_result_owner()` which finds the row in `shipment_history` (the row was still written — only the payload column is NULL)
- BUT `get_result_owner()` has no org filter... wait, actually — the endpoint checks `get_report_payload` first (with org filter), gets None, then checks `get_result_owner` (no org filter). If owner is the same org, it still returns 404 because the code path at line 3263–3273 only distinguishes "no such result" from "different org." It has no path for "result exists in your org but payload is null."

So a single-analysis share link can generate a URL that returns 404 even though the analysis ran successfully — the user gets "This result has expired or the link is invalid" which is incorrect.

### Bug 5 — Hash cleared from URL; refreshing kills the shared view

`_loadResultById()` at line 11580 calls `history.replaceState(null, '', window.location.pathname + window.location.search)` — removing `#result/<id>` from the address bar immediately after loading.

After the page loads the shared result:
- The browser URL is now `https://host/demo` with no hash
- Refreshing the page shows the auth overlay and after login lands on a blank analyze form — the result is gone
- The "Run Your Own Analysis →" button calls `_exitSharedResultView()` which hides results entirely — there is no way to get the shared result back without the original URL

### Bug 6 — Download and Share buttons active in shared view, with misleading behavior

`renderResults()` shows both buttons when `data.shipment_id` is present:
- **Download Report button** calls `POST /api/v1/report/generate` with `shipment_id` — may work for same-org viewers but not cross-org (Bug 2)
- **Share Result button** re-runs `singleShareResult()` using the `shipment_id` from `data.shipment_id` (injected by the endpoint). This re-shares the same URL — arguably correct, but the toast still says "Anyone with this link can view the result" (Bug 1)

### Bug 7 — Feedback UI active in shared view

`renderFeedbackUI(data)` is called by `renderResults()`. If the shared result has a flagged decision AND `data.shipment_id`, the "Confirmed Fraud" / "Cleared — Legitimate" buttons appear and are functional.

This means any same-org member who views the shared result can submit officer feedback for it. There is no indication this is a shared view when looking at the feedback section. This could be intentional for team review workflows but there is no guard, audit trail, or indicator.

### Bug 8 — `analyzed_at` injection can silently fail

The endpoint at lines 3246–3260 tries to inject `analyzed_at` from a SQL query:
```python
try:
    ...
    if _row:
        payload["analyzed_at"] = _row["analyzed_at"]
except Exception:
    pass
```

If this lookup fails (any exception), `analyzed_at` is not in the payload. The shared banner then shows "This is a read-only shared compliance report" instead of the date. No error is surfaced.

### Bug 9 — Pattern stats show viewer's org data, not original analyzer's

`renderResults()` calls `loadPatternStats()` — this fetches `GET /api/v1/pattern-stats` using the viewer's JWT, returning pattern statistics for the **viewer's organization**. In a shared-view context, the "Pattern Learning History" panel updates with the viewer's org data, not the original shipper's history that was relevant to the shared analysis.

### Bug 10 — Compliance hit booleans not displayed

`AnalyzeResponse` now has `ofac_hit`, `section301_hit`, `adcvd_hit`, `uflpa_hit`, `isf_complete` (Sprint 12). None of these are displayed in `renderResults()` or anywhere in the results HTML. They exist in the API response that powers the shared view but the UI shows nothing from them. A viewer of the shared result cannot see at a glance whether OFAC, 301, AD/CVD, UFLPA, or ISF was flagged — they have to read through the full `explanations` list to infer it.

### Bug 11 — No compliance layer results section

The PDF report (`portguard/report_generator.py` line 312–326) has a full section order including "Compliance Grid" (six programs: OFAC, Section 301, AD/CVD, UFLPA, ISF, PGA). The web results view has no equivalent. The shared view shows only the `explanations` text list. A recipient who receives a shared link sees less structured compliance information than the PDF report provides.

### Bug 12 — No share link regeneration after hash is cleared

Once `_loadResultById()` has cleared the hash, the only way to re-share is to click "Share Result" — which reconstructs the URL from `data.shipment_id`. This only works if `data.shipment_id` is present (it always is in a successfully-loaded shared result, because the endpoint injects it). So this is functional but not obvious — the URL in the address bar looks like `https://host/demo` and gives no hint that there is a shareable result loaded.

### Summary of gaps

| # | Gap | Severity |
|---|---|---|
| 1 | Auth required — "anyone can view" claim is false | CRITICAL |
| 2 | Org-scoped — cross-org recipients see 403 | CRITICAL |
| 3 | Bulk per-row share links return 404 | CRITICAL |
| 4 | NULL `report_payload` → 404 for valid analyses | HIGH |
| 5 | Hash cleared on load — refresh destroys shared view | HIGH |
| 6 | Download/Share buttons in shared view work misleadingly | MEDIUM |
| 7 | Feedback UI active in shared view — no guard | MEDIUM |
| 8 | `analyzed_at` injection can silently fail | LOW |
| 9 | Pattern stats show viewer's org data, not original | LOW |
| 10 | Compliance hit booleans not displayed in results UI | MEDIUM |
| 11 | No structured compliance layer / grid in results view | MEDIUM |
| 12 | Address bar gives no hint a shareable result is loaded | LOW |
