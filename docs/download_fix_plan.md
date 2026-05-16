# Download Fix Plan
**Date:** 2026-05-15
**Depends on:** docs/download_read_report.md
**Files to change:** api/app.py, portguard/analytics.py, demo.html

---

## SECTION 1 — ROOT CAUSE

The download chain has four distinct broken links. They are independent; each can fail without the others.

### Link 1 — `GET /api/results/{result_id}/report` returns 403 instead of 404 for own results with no stored payload (app.py lines 3288–3303)

`get_report_payload()` returns `None` in two cases:
- (a) The `analysis_id` exists but `report_payload IS NULL` — result stored but payload never written (pre-migration-004 analyses, or silent `store_report_payload` failure).
- (b) The `analysis_id` doesn't match the requesting org's `organization_id` — foreign org.

The endpoint currently cannot distinguish (a) from (b). It calls `get_result_owner(result_id)` to check if the row exists at all, then raises 403 whenever `owner_org is not None` — even when `owner_org == org_id` (case a). A user who owns the result but whose `report_payload` is NULL gets HTTP 403 "This result belongs to a different organization." The correct response for case (a) is HTTP 404 with a message telling them to re-analyze.

### Link 2 — Error detail rendered as `[object Object]` in the toast (demo.html line 8176)

FastAPI returns structured error bodies: `{"detail": {"code": "...", "message": "..."}}`.
`downloadActivityReport` extracts the message with:
```
throw new Error(errData.detail || errData.error || 'Server error ' + response.status);
```
`errData.detail` is a JavaScript object — always truthy — so `new Error(errData.detail)` coerces it to `[object Object]`. Every 4xx and 5xx from this endpoint shows the user "Download failed: [object Object]" with no actionable information. This masks Link 1's wrong-403 entirely.

### Link 3 — `POST /api/v1/report/generate-direct` never returns the PDF (app.py line 3186)

The function computes `pdf_bytes = generate_report_from_dict(payload)` then falls off the end with no `return` statement. FastAPI returns HTTP 200 with a null body and `Content-Type: application/json`. This endpoint is not currently called by any download button in the frontend, so it is a latent (not active) break. It must be fixed before it can serve as a fallback path.

### Link 4 — `store_report_payload` failures are silently swallowed (app.py lines 1391–1394)

If the `UPDATE shipment_history SET report_payload = ...` fails for any reason (disk full, SQLite lock timeout, etc.), the exception is caught and logged at WARNING level. The `POST /api/analyze` response still includes a valid `shipment_id`, which enables the Download button — but the payload was never written, so any subsequent download triggers Link 1's 403 false positive. There is no retry, no indicator in the response, and no way for the user to know their report cannot be downloaded.

### Non-issues confirmed by the read

- `GET /api/v1/dashboard/recent-activity` **does** return `analysis_id` on every row. No fix required for data delivery.
- The frontend `r.id || r.analysis_id` fallback **does** resolve to the UUID correctly. Functional as-is; cosmetic cleanup recommended.
- `generate_report_from_dict()` correctly returns `bytes`. No fix required.
- `POST /api/v1/analyze` correctly saves to DB and returns `shipment_id`. No fix required.
- Auth on `GET /api/results/{result_id}/report` is correct. No fix required.

---

## SECTION 2 — DATABASE MODEL

### Does a `ScreeningResult` model with an `id` column exist?

**No table named `ScreeningResult` or `screening_results` exists.** There is no separate model to create. The `shipment_history` table in `portguard_patterns.db` (managed by `PatternDB` in `portguard/pattern_db.py`) already stores every field the download chain needs. Its primary key is `analysis_id TEXT PRIMARY KEY` (UUID v4). This is the functional equivalent of the `id` column.

**Creating a parallel `ScreeningResult` table would be wrong.** It would duplicate data already in `shipment_history`, introduce synchronisation hazards, and require migrating every existing record. The correct approach is to use `shipment_history` as-is and fix the endpoint logic that queries it.

### Column mapping — spec requirements vs. what already exists

| Spec column | Type | Existing column in `shipment_history` | Notes |
|---|---|---|---|
| `id` | UUID string, PK | `analysis_id TEXT PRIMARY KEY` | UUID v4, generated at insert |
| `organization_email` | string | `organization_id` (UUID, not email) | Email available inside `report_payload` JSON; org lookup possible via `portguard_auth.db` if needed |
| `decision` | string | `final_decision TEXT NOT NULL` | One of `APPROVE`, `REVIEW_RECOMMENDED`, `HOLD` |
| `risk_score` | float | `final_risk_score REAL NOT NULL` | 0.0–1.0 |
| `flags` | JSON string | `rules_fired TEXT NOT NULL` | JSON array of rule-firing dicts |
| `sustainability_rating` | string | `sustainability_grade TEXT` | Added migration 005; nullable |
| `shipper` | string, nullable | `shipper_name TEXT` | |
| `origin_country` | string, nullable | `origin_iso2 TEXT` | ISO-2 code |
| `destination_country` | string, nullable | `destination_iso2 TEXT` | Default `'US'` |
| `declared_value` | float, nullable | `declared_value_usd REAL` | |
| `raw_result` | JSON string, full dict | `report_payload TEXT` | Full serialised `AnalyzeResponse` JSON; nullable (migration 004); NULL for pre-004 analyses |
| `created_at` | datetime | `analyzed_at TEXT NOT NULL` | ISO-8601 UTC string |

**No new columns, tables, or migrations are required.** All data is already present. The only schema gap is that `organization_email` is not directly in `shipment_history` (only `organization_id` UUID is stored), but email is embedded in `report_payload` and is not needed by the download endpoint.

---

## SECTION 3 — BACKEND FIXES

### Fix B1 — Correct the 403 false-positive in `GET /api/results/{result_id}/report` (app.py ~line 3288)

**Current code (broken):**
```
if payload_json is None:
    owner_org = _pattern_db.get_result_owner(result_id)
    if owner_org is None:
        raise 404           # result doesn't exist at all
    raise 403               # always 403 when row exists — WRONG
```

**Required logic:**
```
if payload_json is None:
    owner_org = _pattern_db.get_result_owner(result_id)
    if owner_org is None:
        raise 404           # result doesn't exist at all
    if owner_org == org_id:
        raise 404           # result is ours but has no stored payload — tell user to re-analyze
    raise 403               # result exists but belongs to different org
```

The 404 message for the "ours but null payload" case must be distinct and actionable: `"No report payload is stored for this result. Re-analyze the shipment to generate a downloadable report."` The `REPORT_NOT_AVAILABLE` code is already correct for this case.

This is a two-line logic change. No schema change, no new dependency.

### Fix B2 — Add missing return statement to `POST /api/v1/report/generate-direct` (app.py ~line 3186)

After `pdf_bytes = generate_report_from_dict(payload)` succeeds, add:
```python
return _pdf_response(pdf_bytes, shipment_id)
```

This is a one-line addition. The `_pdf_response` helper already exists and produces the correct headers.

### Fix B3 — Surface `store_report_payload` failures in the analyze response (app.py ~lines 1388–1394)

Do not change the non-fatal swallow (the analysis must not fail because of a DB payload write). Instead:

- Log at `ERROR` level (not `WARNING`) so it pages on-call.
- Add a boolean field `report_available: bool` to `AnalyzeResponse` (or a warning string to `validation_warnings`) so the frontend can immediately disable the Download button on results where the payload write failed.

This is a medium-complexity change. It requires:
1. Adding `report_available: bool = True` to `AnalyzeResponse` (app.py ~line 1127).
2. In `_record_shipment_bg`, returning a `(analysis_id, report_stored: bool)` tuple instead of just `analysis_id`, or setting a flag the caller can read.
3. In the `POST /api/analyze` handler, setting `analyze_response.report_available = report_stored`.
4. In the frontend, checking `data.report_available !== false` before showing the Download button (in the post-analyze flow only — the activity table has no real-time knowledge of this).

**Scoping note:** Fix B3 is a good-practice hardening fix, not a prerequisite for the primary download to work. The primary download fails on B1 (wrong 403) + F1 (bad error extraction). Fix B3 only on new analyses where `store_report_payload` happens to fail, which should be rare. Prioritise B1 and B2.

### Non-changes — backend already correct

- `POST /api/v1/analyze`: saves fingerprint row, then UPDATEs `report_payload`. Returns `shipment_id`. No change needed.
- `GET /api/v1/dashboard/recent-activity`: returns `analysis_id` on every row. Functional as-is.

**Optional enhancement to `GET /api/v1/dashboard/recent-activity`:** Add `"id": r["analysis_id"]` as an alias key alongside `"analysis_id"` in `analytics.py get_recent_activity()`. This removes the frontend's `r.id || r.analysis_id` fragility and makes the API contract explicit. One-line change in the dict comprehension.

---

## SECTION 4 — FRONTEND FIXES

### Fix F1 — Correct error detail extraction in `downloadActivityReport` (demo.html ~line 8176)

**Current (broken):**
```js
throw new Error(errData.detail || errData.error || 'Server error ' + response.status);
```

**Fix — mirror the pattern already used by `downloadReport()` at line 8101:**
```js
throw new Error(
    (errData.detail && (errData.detail.message || errData.detail)) ||
    errData.error ||
    'Server error ' + response.status
);
```

This handles three cases:
- `errData.detail` is a string (older FastAPI style) → uses it directly.
- `errData.detail` is a dict `{"code":…,"message":…}` (current style) → uses `.message`.
- Neither → falls through to `'Server error N'`.

This is the single most impactful frontend fix. Without it, all error toasts show `[object Object]` regardless of which of B1/B2/B3 triggered them.

### Fix F2 — Pass button element explicitly; remove `event.currentTarget` dependency (demo.html ~lines 8153, 9984)

**Problem:** `downloadActivityReport` captures the button with `event.currentTarget`, which depends on the global `event` object being accessible as a non-null reference inside the called function — unreliable in strict mode and some browser contexts.

**Fix: update the inline handler to pass `this`:**

The button render at line 9984:
```js
// Current:
onclick="downloadActivityReport('${escHtml(itemId)}')"

// Fixed:
onclick="downloadActivityReport('${escHtml(itemId)}', this)"
```

The `bulkDownloadRowPdf` call at line 11265 that delegates to `downloadActivityReport` must pass `null` as the second argument so the function signature is consistent:
```js
// Current:
await downloadActivityReport(analysisId);

// Fixed:
await downloadActivityReport(analysisId, null);
```

**Update the function signature:**
```js
// Current:
async function downloadActivityReport(resultId) {
    var btn = (typeof event !== 'undefined' && event && event.currentTarget) ? event.currentTarget : null;

// Fixed:
async function downloadActivityReport(resultId, btnEl) {
    var btn = (btnEl instanceof Element) ? btnEl : null;
```

This makes the loading state reliable across all browsers and removes the global-event dependency.

### Fix F3 — Add `apiUrl()` prefix (demo.html ~line 8162)

**Current:**
```js
fetch('/api/results/' + resultId + '/report', { ... })
```

**Fix:**
```js
fetch(apiUrl() + '/api/results/' + resultId + '/report', { ... })
```

`apiUrl()` currently returns `''`, so this is functionally a no-op today. It is required for consistency: every other fetch in demo.html uses `apiUrl()`. If the app is ever deployed under a path prefix or a proxy is added, this will break in isolation otherwise.

### Fix F4 — Clean up `r.id || r.analysis_id` in the activity render (demo.html ~line 9982)

**Current:**
```js
const itemId = r.id || r.analysis_id || null;
```

The API returns `analysis_id`, never `id`. `r.id` is always `undefined`. The `||` fallback works, but it is fragile: if the API ever adds an unrelated `id` field (e.g., a row number), this silently uses the wrong value.

**Fix:**
```js
const itemId = r.analysis_id || null;
```

If the optional backend enhancement from Section 3 (adding `"id"` alias) is implemented, update to:
```js
const itemId = r.analysis_id || r.id || null;
```
(prefer the explicit `analysis_id` field first, use `id` as fallback for forward-compat).

### Error handling coverage — confirmed correct, no changes needed

| Condition | Current handling | Status |
|---|---|---|
| 404 — result not found | Hard-coded string: "Report not found…" | Correct |
| 401 — expired token | Hard-coded: "Session expired. Please log in again." | Correct |
| Content-type not PDF | Throws "Server did not return a PDF." | Correct |
| Blob size < 50 bytes | Throws "Received empty or corrupt PDF." | Correct |
| Generic 4xx/5xx | `errData.detail` → **broken (F1)** | Fixed by F1 above |
| Button restore on failure | `finally` block restores `originalHTML` | Correct once F2 is in |

---

## SECTION 5 — STEP BY STEP BUILD ORDER

Steps are ordered so each step can be tested in isolation before the next is taken. All steps are in `api/app.py`, `portguard/analytics.py`, or `demo.html` — no new files required.

**1. Fix `GET /api/results/{result_id}/report` — add the `owner_org == org_id` branch (api/app.py ~line 3300)**

Change the `if payload_json is None` block to add the three-way check: no row → 404, own row with null payload → 404 with "re-analyze" message, foreign row → 403.

Acceptance: calling the endpoint with a valid `analysis_id` that belongs to the requesting org but has `report_payload IS NULL` now returns HTTP 404 with `"REPORT_NOT_AVAILABLE"` and a clear message — not 403.

**2. Fix `POST /api/v1/report/generate-direct` — add missing return statement (api/app.py ~line 3186)**

After the `try` block that sets `pdf_bytes`, add `return _pdf_response(pdf_bytes, shipment_id)`.

Acceptance: `POST /api/v1/report/generate-direct` with a valid AnalyzeResponse JSON body returns HTTP 200 with `Content-Type: application/pdf` and a non-empty body.

**3. (Optional) Add `"id"` alias to `get_recent_activity` response (portguard/analytics.py ~line 878)**

In the dict comprehension, add `"id": r["analysis_id"]` alongside the existing `"analysis_id"` key. Update the endpoint docstring.

Acceptance: `GET /api/v1/dashboard/recent-activity` returns each item with both `"id"` and `"analysis_id"` set to the same UUID.

**4. Fix error detail extraction — F1 (demo.html ~line 8176)**

Replace `errData.detail` with `(errData.detail && (errData.detail.message || errData.detail))` in the throw expression inside `downloadActivityReport`.

Acceptance: with a mock 403 response `{"detail": {"code": "FORBIDDEN", "message": "This result belongs to a different organization."}}`, the toast reads "Download failed: This result belongs to a different organization." — not "[object Object]".

**5. Fix button capture — F2: update button render to pass `this` (demo.html ~line 9984)**

Update the `onclick` attribute in the activity table row renderer from `downloadActivityReport('${itemId}')` to `downloadActivityReport('${itemId}', this)`.

Acceptance: button render HTML contains `onclick="downloadActivityReport('...', this)"`.

**6. Fix button capture — F2: update `downloadActivityReport` signature and button capture (demo.html ~line 8147–8153)**

Change function signature from `downloadActivityReport(resultId)` to `downloadActivityReport(resultId, btnEl)`. Replace the `event.currentTarget` capture with `var btn = (btnEl instanceof Element) ? btnEl : null;`.

Acceptance: clicking a Download button in the activity table shows "Generating..." during the request, then re-enables regardless of success or failure. No reliance on global `event`.

**7. Fix `bulkDownloadRowPdf` to pass `null` as second arg (demo.html ~line 11265)**

Change `await downloadActivityReport(analysisId)` to `await downloadActivityReport(analysisId, null)`.

Acceptance: bulk row PDF download still works with the updated function signature.

**8. Add `apiUrl()` prefix — F3 (demo.html ~line 8162)**

Change `fetch('/api/results/' + resultId + '/report', ...)` to `fetch(apiUrl() + '/api/results/' + resultId + '/report', ...)`.

Acceptance: the URL constructed by `downloadActivityReport` is identical to what it was before (since `apiUrl()` returns `''`), but is now consistent with all other fetch calls.

**9. Clean up `r.id || r.analysis_id` — F4 (demo.html ~line 9982)**

Change `const itemId = r.id || r.analysis_id || null;` to `const itemId = r.analysis_id || null;` (or `r.analysis_id || r.id || null` if step 3 was implemented).

Acceptance: `itemId` is still the UUID; Download buttons still render for rows with an `analysis_id`.

**10. (Optional) Surface `store_report_payload` failure — Fix B3 (api/app.py)**

Add `report_available: bool = True` to `AnalyzeResponse`. In `_record_shipment_bg`, track whether `store_report_payload` succeeded. In the `POST /api/analyze` handler, set `analyze_response.report_available = <result>`. In demo.html, check `data.report_available !== false` before enabling the download button after analysis.

Acceptance: if `store_report_payload` throws during a test (e.g., by temporarily breaking the DB path), the analyze response contains `"report_available": false` and the Download button is shown in a disabled state.

---

## Fix Priority Summary

| Priority | Step | Location | Lines changed (est.) |
|---|---|---|---|
| P0 — must ship | 1 | api/app.py | ~4 |
| P0 — must ship | 4 | demo.html | 1 |
| P0 — must ship | 5 | demo.html | 1 |
| P0 — must ship | 6 | demo.html | 2 |
| P0 — must ship | 7 | demo.html | 1 |
| P1 — should ship | 2 | api/app.py | 1 |
| P1 — should ship | 8 | demo.html | 1 |
| P1 — should ship | 9 | demo.html | 1 |
| P2 — nice to have | 3 | portguard/analytics.py | 1 |
| P2 — nice to have | 10 | api/app.py + demo.html | ~15 |

Steps 1 + 4 + 5 + 6 + 7 together form the minimum viable fix — every confirmed user-facing failure is resolved by those five changes.
