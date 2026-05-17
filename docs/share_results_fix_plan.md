# Share Results Fix Plan
**Date:** 2026-05-17
**Sprint:** 13 — shared result view (public, no-login)

---

## SECTION 1 — WHAT THE SHARED VIEW MUST LOOK LIKE

A shared result page is a full results render for a single shipment, loaded without requiring the viewer to log in. The page must contain every component that an authenticated user sees after running an analysis, with two exceptions: feedback controls are suppressed and a read-only banner is pinned at the top.

### Required components (in render order)

1. **Shared banner** — pinned at top of results panel, above all content. See Section 4 for exact copy and structure.

2. **Full decision header** — risk score, risk level badge (CLEAR / REVIEW / HOLD / REJECT), overall compliance decision, document type, shipment name/reference, analyzed-at timestamp.

3. **Shipment metadata** — shipper, origin country, destination country, declared value + currency, HTS codes, mode of transport.

4. **Compliance flags** — the complete `explanations` list rendered as flag cards. Count badge. Flags detail string.

5. **Compliance hit indicators** — the five boolean fields: OFAC hit, Section 301 hit, AD/CVD hit, UFLPA hit, ISF complete. Each displayed as a labeled pass/fail pill. These are currently computed by the backend but NOT rendered anywhere in the web UI — they must be added to the shared view and, going forward, to the authenticated view as well.

6. **Compliance program layer results** — the six regulatory program cards: OFAC Sanctions, Section 301 Tariffs, AD/CVD Orders, UFLPA (Forced Labor), ISF Filing, PGA Requirements. Each card shows: program name, triggered flag, finding type, severity, message, evidence list, regulatory reference, remediation guidance. These come from `AnalyzeResponse.module_findings` (list of `ModuleFinding` objects). This section mirrors what the PDF report calls the "compliance grid."

7. **Pattern intelligence** — pattern warning strings, hard-flag indicator, confidence score if available. Rendered via the existing `renderPatternIntelResult()` function.

8. **Sustainability rating** — grade, inherent/country/product risk levels, certifications detected, certifications missing, signals list.

9. **PDF download button** — calls `GET /api/v1/results/{id}/report` (this endpoint is also currently auth-required; see Section 2 note on PDF). Label: "Download PDF Report."

10. **"Run Your Own Analysis" CTA** — below all result content. See Section 4.

### What is suppressed in shared/read-only mode

- "Submit Feedback" button (currently at bottom of results panel)
- "Save / Flag for Review" controls if any exist
- Any action that writes back to the backend (re-analyze, dispute, etc.)
- The normal nav header showing org name and logout — replaced by a minimal top bar

### 404 / not-found handling

If `GET /api/v1/shared/results/{id}` returns 404 (result does not exist or was deleted): display a simple centered message: "This result link is invalid or has expired." No auth overlay, no redirect. A "Go to PortGuard" link navigates to `/?` (root, no fragment).

---

## SECTION 2 — BACKEND REQUIREMENTS

### 2a. New public endpoint: `GET /api/v1/shared/results/{result_id}`

- **No `Depends(get_current_organization)`** — this endpoint has zero auth requirement.
- Queries `shipment_history.report_payload` by `analysis_id` alone, with no org filter.
- Returns HTTP 404 if the row does not exist or `report_payload` is NULL.
- Returns the full `AnalyzeResponse`-shaped JSON object (same structure as the authenticated `/api/v1/results/{id}` but without org gating), plus `shipment_id` and `analyzed_at` injected at the top level.
- Response body must include all 25+ fields: decision, risk_score, risk_score_scaled, risk_level, shipment data, flags/explanations, compliance hit booleans, module_findings list, pattern_intelligence, sustainability_rating.

### 2b. New PatternDB method: `get_report_payload_public(analysis_id: str) -> Optional[str]`

Required in `portguard/pattern_db.py`. Identical to `get_report_payload()` except it omits the `AND organization_id = ?` clause. Returns the raw JSON string from `report_payload`, or `None` if no row / NULL payload.

This method is the only new database access path required.

### 2c. Bulk per-row share links — scope decision

Bulk results are stored exclusively in `bulk_shipments.result_json` — they are never written to `shipment_history.report_payload`. Two options:

**Option A (recommended for this sprint):** When a bulk shipment completes, write its `result_json` content into `shipment_history.report_payload` under the same `analysis_id`. This makes bulk results queryable by the public endpoint with zero additional logic. Requires a one-line addition to `_store_shipment_result()` in `bulk_processor.py`.

**Option B (defer):** Add a second lookup path in the public endpoint that checks `bulk_shipments.result_json` if `shipment_history` returns nothing. Adds branching logic and a second PatternDB method. Defer unless Option A is rejected.

The plan adopts Option A. The step-by-step build order in Section 5 includes it.

### 2d. PDF endpoint — auth status

`GET /api/v1/results/{result_id}/report` (line 3276 of `api/app.py`) is also auth-gated. For the PDF download button in shared view to work without login, a parallel public variant is needed: `GET /api/v1/shared/results/{result_id}/report`. This endpoint follows the same pattern as 2a — no auth, uses `get_report_payload_public()` to verify existence, then calls `ReportGenerator` to build and stream the PDF.

If PDF download in shared mode is out of scope for this sprint, the button can be shown but disabled with tooltip "Sign in to download PDF." Mark this as a decision point in Section 5.

### 2e. What does NOT change on the backend

- `GET /api/v1/results/{result_id}` (authenticated endpoint) — unchanged.
- `get_current_organization` dependency — unchanged.
- `shipment_history` schema — unchanged (report_payload column already exists from migration 004).
- `bulk_shipments` schema — unchanged (Option A writes to `shipment_history` as a side effect, not a schema change).

---

## SECTION 3 — FRONTEND REQUIREMENTS

### 3a. URL format change — hash → query param

**Current:** `singleShareResult()` builds `#result/<id>`. Hash fragments are destroyed by `history.replaceState` in `_loadResultById()` after first load. A page refresh loses the result ID entirely.

**New format:** `?result=<id>` — query parameter, survives page refresh, survives `history.replaceState` on the hash only, and can be read before the auth overlay appears.

All functions that build share URLs must be updated:
- `singleShareResult()` — change `#result/${id}` to `?result=${id}`
- `bulkShareRowLink()` — same change
- The batch-level share (`#bulk-share-btn` → `?batch=<id>`) is out of scope for this sprint (batch sharing is a separate flow).

### 3b. New module-level variable: `_pendingSharedResultId`

Declared at module scope alongside `_deepLinkResultId` and `_deepLinkBatchId`. Set by the new `_readSharedParam()` function. Checked in the startup flow before the auth overlay is shown.

```
let _pendingSharedResultId = null;
```

### 3c. New function: `_readSharedParam()`

Called once at startup, before `showAuthOverlay()`. Reads `window.location.search` for the `?result=` query parameter. If found, sets `_pendingSharedResultId` to the extracted ID. Does NOT remove the param from the URL (removal happens after the result loads successfully).

### 3d. Startup flow change — suppress auth overlay for shared results

The current startup sequence always shows the auth overlay if no token is present. The new sequence:

1. Call `_readSharedParam()`.
2. If `_pendingSharedResultId` is set, call `loadSharedResult(_pendingSharedResultId)` immediately — do NOT show the auth overlay.
3. If `_pendingSharedResultId` is null, proceed with the normal auth overlay flow unchanged.

### 3e. New function: `loadSharedResult(resultId)`

Fetches `GET /api/v1/shared/results/{resultId}` — no Authorization header, no token required. On success:
- Calls `renderSharedResult(data)`.
- Removes `?result=` from the URL via `history.replaceState` (clean URL after load).

On 404 or network error:
- Renders the not-found message (see Section 1 — 404 handling) into the main content area.
- Does not show the auth overlay.

### 3f. New function: `renderSharedResult(data)`

Wrapper around the existing `renderResults(data)`. Steps:
1. Call `renderResults(data)` — reuse the full 22-step renderer unchanged.
2. Call `_enterSharedResultView(data)` — reuse the existing function that adds `shared-result-view` CSS class and prepends the banner.
3. Suppress feedback controls: add `shared-result-view` class on `#results` which CSS rules will use to hide the feedback button. (The CSS rule already exists from the existing `_enterSharedResultView` implementation — verify this covers the feedback button selector specifically.)
4. Add the compliance hit indicators block (new render step — see Section 1, item 5).
5. Add the compliance program layer results block (new render step — see Section 1, item 6). Source: `data.module_findings` array.
6. Show the "Run Your Own Analysis" CTA (see Section 4).

### 3g. Compliance hit indicators render — new function: `renderComplianceHitBadges(data)`

Reads the five boolean fields: `data.ofac_hit`, `data.section301_hit`, `data.adcvd_hit`, `data.uflpa_hit`, `data.isf_complete`. Renders five labeled pills. Each pill is green (pass) or red (fail/hit) or grey (null / N/A). Inserted into the results panel after the decision header block.

This function is called from both `renderSharedResult()` and eventually from the authenticated `renderResults()` flow so that logged-in users also see compliance hit badges.

### 3h. Compliance program layer render — new function: `renderModuleFindings(findings)`

Reads `data.module_findings` (array of ModuleFinding objects). For each finding renders a card with: program name, triggered boolean, finding type, severity badge, message, evidence list (bulleted), regulatory reference, remediation text. Inserted after the compliance flags section.

### 3i. `_enterSharedResultView(data)` — existing function, minor update

Currently the banner says "Shared Result — Read Only." The required copy per Section 4 is "Shared Compliance Screening Result — Read Only." Update the banner title string. Add the "Run Your Own Analysis" button to the banner. No other changes to this function.

### 3j. What does NOT change on the frontend

- `renderResults(data)` — not modified directly. Called as-is from `renderSharedResult()`.
- `_loadResultById(resultId)` — unchanged (still used for authenticated deep-link flow).
- `_readDeepLink()` — unchanged (still reads `#result/<id>` hash for backward compat during transition; set `_deepLinkResultId` as before; the authenticated flow uses `_deepLinkResultId`, the public flow uses `_pendingSharedResultId`).
- `hideAuthOverlay()` — unchanged (still acts on `_deepLinkResultId` for the authenticated post-login deep-link flow).
- `GET /api/v1/results/{id}` fetch in `_loadResultById()` — unchanged, still auth-required.

---

## SECTION 4 — THE SHARED BANNER

### Banner placement

Prepended inside `#results` panel, above all result content. Fixed position relative to the results panel (not viewport-fixed). Scrolls with content.

### Banner content

```
┌──────────────────────────────────────────────────────────────────────┐
│  Shared Compliance Screening Result — Read Only                      │
│  Analyzed: {analyzed_at formatted as "May 17, 2026 at 14:32 UTC"}   │
│                                                              [button] │
│                                        Run Your Own Analysis →       │
└──────────────────────────────────────────────────────────────────────┘
```

- **Title:** "Shared Compliance Screening Result — Read Only"
- **Subtitle:** "Analyzed: {date}" — formatted from `data.analyzed_at`
- **CTA button:** "Run Your Own Analysis →" — navigates to `/?` (root, clears any result param), triggers the auth overlay. Button style: secondary/outline, right-aligned within the banner.
- **Banner style:** light blue-grey background (`#f0f4f8` or similar), left border accent in PortGuard brand color, 1rem padding, rounded corners consistent with existing card styles.

### "Run Your Own Analysis" CTA (below results)

A second CTA block appears below all result content (after sustainability section), above the page footer:

```
┌──────────────────────────────────────────────────────────────────────┐
│  Want to screen your own shipments?                                  │
│  PortGuard automates compliance checking against OFAC, Section 301, │
│  AD/CVD, UFLPA, and ISF requirements.                               │
│                                   [Run Your Own Analysis →]         │
└──────────────────────────────────────────────────────────────────────┘
```

Same button target: `/?`.

---

## SECTION 5 — STEP BY STEP BUILD ORDER

Steps are ordered to keep each increment shippable. Backend changes first (independently testable via curl), then frontend. Within each group, simpler changes precede dependent ones.

### Step 1 — Backend: `get_report_payload_public()` in PatternDB

Add `get_report_payload_public(analysis_id: str) -> Optional[str]` to `portguard/pattern_db.py`. Query: `SELECT report_payload FROM shipment_history WHERE analysis_id = ?`. Return the string or None. No org filter.

**Test:** curl `GET /api/v1/results/{known_id}` with a valid token, confirm payload matches. Then (after step 2) curl the public endpoint without a token.

### Step 2 — Backend: new public endpoint `GET /api/v1/shared/results/{result_id}`

Add route to `api/app.py` (no `Depends(get_current_organization)`). Call `get_report_payload_public()`. Parse JSON. Inject `shipment_id` and `analyzed_at`. Return 404 if None or NULL payload. Return the full payload object.

**Test:** curl without Authorization header → 200 with payload. Curl with unknown ID → 404.

### Step 3 — Backend: bulk results written to `shipment_history`

In `portguard/bulk_processor.py`, `_store_shipment_result()`: after the existing `UPDATE bulk_shipments ...` write, also call `store_report_payload(analysis_id, organization_id, result_json)` so bulk results appear in `shipment_history`. Confirm the PatternDB `store_report_payload()` signature accepts these args (it already does per the read sprint).

**Test:** run a bulk analysis, then curl `GET /api/v1/shared/results/{bulk_analysis_id}` → 200.

### Step 4 — Backend: public PDF endpoint `GET /api/v1/shared/results/{result_id}/report` (decision point)

**If in scope:** Add route mirroring `GET /api/v1/results/{id}/report` but using `get_report_payload_public()` for the existence check. No auth.

**If deferred:** Skip. In Step 8 the PDF button is shown as disabled with tooltip "Sign in to download PDF."

### Step 5 — Frontend: URL format change in share functions

In `demo.html`, change `singleShareResult()` to build `?result=${id}` instead of `#result/${id}`. Change `bulkShareRowLink()` to build `?result=${id}` instead of `#result/${id}`. Update the toast message: "Link copied! Anyone with this link can view the result." is now factually correct (public endpoint, no auth).

**Test:** Click share on a result, paste URL in incognito tab, confirm it works after Steps 6–9 are done. (This step alone is harmless — old `#result/` hash flow still works via `_readDeepLink`.)

### Step 6 — Frontend: `_readSharedParam()` and `_pendingSharedResultId`

Add `let _pendingSharedResultId = null;` near `_deepLinkResultId`. Add `_readSharedParam()` function that reads `URLSearchParams` for `result` key and sets `_pendingSharedResultId`.

### Step 7 — Frontend: startup flow — suppress auth overlay for shared results

In the startup/init sequence (wherever `showAuthOverlay()` is currently called conditionally), add the branch: if `_pendingSharedResultId` is set after `_readSharedParam()`, call `loadSharedResult()` instead of `showAuthOverlay()`.

### Step 8 — Frontend: `loadSharedResult(resultId)`

Implement the fetch against `/api/v1/shared/results/{id}`. On success → `renderSharedResult(data)` + clean URL. On 404 → render not-found message. On network error → render generic error message.

### Step 9 — Frontend: `renderComplianceHitBadges(data)` and `renderModuleFindings(findings)`

Implement both new render functions. Insert calls into the results panel at the correct positions (after decision header, after flags section respectively). These functions are safe to add without touching `renderResults()` itself — they inject into specific DOM element IDs.

### Step 10 — Frontend: `renderSharedResult(data)`

Implement the wrapper: calls `renderResults(data)`, then `_enterSharedResultView(data)`, then `renderComplianceHitBadges(data)`, then `renderModuleFindings(data.module_findings)`, then appends the bottom CTA block.

### Step 11 — Frontend: banner copy and CTA update in `_enterSharedResultView()`

Change banner title from current string to "Shared Compliance Screening Result — Read Only." Add "Run Your Own Analysis →" button to banner. Apply banner styles.

### Step 12 — Verification pass

- Unauthenticated: open `?result={single_shipment_id}` in incognito → full result visible, banner present, compliance badges rendered, module findings rendered, no auth overlay.
- Unauthenticated: open `?result={bulk_shipment_id}` in incognito → same result (confirms Step 3).
- Unauthenticated: open `?result=nonexistent` → 404 message, no auth overlay.
- Authenticated: normal flow unchanged — login works, analyze works, share button produces new URL format.
- Authenticated deep-link: `#result/{id}` hash still works via existing `_loadResultById` flow (backward compat).
- Page refresh on shared result URL → result still loads (query param survives refresh).
- PDF button: either works (if Step 4 implemented) or is disabled with tooltip.

### Step 13 — Docs + commit

Update `docs/SPRINT_LOG.md` with Sprint 13 entry. Commit all changes.

---

## Appendix — Key file locations for implementer

| Item | File | Approx line |
|---|---|---|
| `get_report_payload()` | `portguard/pattern_db.py` | 1457 |
| `get_result_owner()` | `portguard/pattern_db.py` | 1481 |
| `shipment_history` DDL | `portguard/pattern_db.py` | 325 |
| `_store_shipment_result()` | `portguard/bulk_processor.py` | 790 |
| `store_report_payload()` | `portguard/pattern_db.py` | 1432 |
| `GET /api/v1/results/{id}` | `api/app.py` | 3218 |
| `GET /api/v1/results/{id}/report` | `api/app.py` | 3276 |
| `AnalyzeResponse` model | `api/app.py` | 1127 |
| `get_current_organization` | `portguard/auth.py` | 482 |
| `_bearer = HTTPBearer(...)` | `portguard/auth.py` | 57 |
| `singleShareResult()` | `demo.html` | 8174 |
| `bulkShareRowLink()` | `demo.html` | 11224 |
| `_readDeepLink()` IIFE | `demo.html` | 11662 |
| `_deepLinkResultId` variable | `demo.html` | 10081 |
| `hideAuthOverlay()` | `demo.html` | 4994 |
| `_loadResultById()` | `demo.html` | 11578 |
| `_enterSharedResultView()` | `demo.html` | 11612 |
| `renderResults()` | `demo.html` | 7817 |
| `renderPatternIntelResult()` | `demo.html` | 8672 |
| `ModuleFinding` model | `portguard/models/certification.py` | — |
| `SustainabilityRating` model | `portguard/models/certification.py` | — |
