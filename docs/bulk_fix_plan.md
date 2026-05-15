# Bulk Upload Fix Plan
**Date:** 2026-05-15
**Scope:** End-to-end fix for all known bulk upload bugs
**Rule:** Plan only — no code changes in this document

---

## SECTION 1 — ROOT CAUSE OF "DOCUMENT VALIDATION FAILED"

### Exact Root Cause

`_run_bulk_single_analysis()` in `api/app.py` (lines 3311–3319) applies the hardened document classifier — `_classify_document()` — to every document in a bulk shipment before any analysis runs. The hardened classifier (`portguard/agents/document_classifier_hardened.py`) was designed for single-document uploads where documents are full-length trade files. It enforces two rejection conditions that are too strict for bulk input:

1. **Vocabulary minimum:** `total_pro < 2.0 AND doc_type is None` → rejects documents with fewer than two positive shipping vocabulary signals and no hard fingerprint match.
2. **Confidence floor:** `confidence < 0.35 AND doc_type is None` → rejects documents that score below 35% confidence and have no fingerprint.

Legitimate bulk shipment entries — especially rows derived from CSV files — are short by nature. A CSV row for a single shipment may contain only 10–30 words (shipper name, destination, HS code, declared value, commodity description). That sparse text cannot accumulate `total_pro >= 2.0` and cannot reach `confidence >= 0.35`. The hardened classifier therefore rejects it as "not a trade document", raises `ValueError("Document validation failed: ...")`, and the entire batch entry is marked `ERROR` or aborted.

The hardened classifier's purpose is to reject clearly non-shipping content (resumes, shopping lists, personal correspondence). That is the right behavior for single-document uploads. It is the wrong behavior for bulk pipelines where brevity is expected and legitimate.

### Required Fix

The bulk pipeline needs its own classification path — `classify_document_bulk()` — which skips the vocabulary minimum and confidence floor, and only hard-rejects content that is unmistakably not a trade document (anti-pattern score above a high threshold with no counterevidence). Short documents with any shipping vocabulary pass.

---

## SECTION 2 — ROOT CAUSE OF "NO ZIP FILE PROVIDED" ON CSV UPLOAD

### Exact Root Cause

The root cause is **multipart form parsing failure caused by `authedForm()` setting an explicit `Content-Type` header**, which destroys the multipart boundary that the browser generates.

Step-by-step trace of the failure:

1. The frontend constructs a `FormData` object (`fd`) and appends `csv_file`, `input_method: 'CSV'`, and other fields to it.
2. `authedForm(fd)` is called to build the fetch request headers. If `authedForm()` sets `Content-Type: multipart/form-data` (without the `boundary=...` parameter), or sets any explicit Content-Type at all, it overwrites the browser-generated `Content-Type: multipart/form-data; boundary=<uuid>` header.
3. FastAPI/Starlette receives the request with a Content-Type header that has no boundary parameter (or the wrong one). It cannot split the request body into parts. All `form.get(...)` calls return `None` or empty.
4. In `bulk_create()` (`api/app.py:3736`):
   - `form.get("zip_file")` → `None`
   - `form.get("csv_file")` → `None`
   - The fallback scan (lines 3843–3865) also finds no files because the body is unparsed.
   - `form.get("input_method", "")` → `""` (empty string)
5. The `input_method` derivation logic (lines 3867–3876):
   - `explicit_method = ""` → not in `("ZIP", "CSV")`
   - `zip_file` is `None`, `csv_file` is `None`
   - Falls to `else: input_method = "ZIP"` (the ambiguous default)
6. The ZIP guard fires: `if input_method == "ZIP" and zip_file is None` → `HTTPException(400, "No ZIP file found in the request.")`

**This is not a field name mismatch.** The frontend correctly names the field `csv_file`. The backend correctly reads `form.get("csv_file")`. The names match. The failure occurs before field lookup: the entire form body is unreadable because the Content-Type header is wrong.

**Secondary contributing bug:** The fallback file-type scan (lines 3843–3865) contains an operator precedence error in the ZIP detection condition. The expression `_fn.endswith(".zip") or "zip" in _ct or _ct in (...) and _fn.endswith(".zip")` evaluates as `(_fn.endswith(".zip")) OR ("zip" in _ct) OR ((_ct in (...)) AND (_fn.endswith(".zip")))` because `and` binds tighter than `or`. This means any file whose content-type contains the substring `"zip"` (including some archive MIME types) would be treated as a ZIP even if the filename ends in `.csv`. This secondary bug would not fire if the form parsing failure is the primary issue, but it must be fixed regardless.

---

## SECTION 3 — BACKEND FIXES NEEDED

### Fix 3.1 — Permissive Bulk Classifier

**File:** `portguard/agents/document_classifier.py`
**Function:** Module level (new function `classify_document_bulk()`)
**Currently does:** Only re-exports `classify_document()` from the hardened classifier. No bulk-specific path exists.
**Needs to do instead:** Export an additional function `classify_document_bulk(text: str) -> dict` that:
- Calls the hardened classifier's internal scoring (anti-pattern scoring, hard fingerprint detection)
- Only rejects if `anti_score >= 12.0` AND the hard fingerprint score is `< 8.0` (i.e., no strong shipping fingerprint counteracts the anti-patterns)
- Never rejects solely based on `total_pro < 2.0` — short text is not evidence of non-shipping content
- Never rejects solely based on `confidence < 0.35` — low confidence on short text is expected
- Returns the same dict shape as `classify_document()`: `{ accepted, confidence, confidence_label, detected_doc_type, detected_doc_type_code, rejection_reason, rejection_category, warning, anti_pattern_matches, pro_signals, hard_signals, raw_scores }`
- For accepted bulk documents: `accepted = True`, `rejection_reason = None`

### Fix 3.2 — Bulk Pipeline Uses Permissive Classifier

**File:** `api/app.py`
**Function:** `_run_bulk_single_analysis()` (line 3255)
**Currently does:** Lines 3311–3319 call `_classify_document(doc.raw_text or "")` — the hardened single-document classifier — for every document in the batch. Rejects if any document is not accepted.
**Needs to do instead:** Call `classify_document_bulk(doc.raw_text or "")` instead of `_classify_document()` for the bulk gate. Import `classify_document_bulk` from `portguard.agents.document_classifier` at the top of `app.py`. All other pipeline logic after the gate (document analysis, pattern engine, certifications, sustainability) remains unchanged.

### Fix 3.3 — `bulk_create()` Content-Type Handling and Input Method Derivation

**File:** `api/app.py`
**Function:** `bulk_create()` (line 3736)
**Currently does:**
- Reads `form.get("zip_file")` and `form.get("csv_file")` as primary keys; runs a fallback scan
- Derives `input_method` from explicit form field first, then inferred from which file is present, then defaults to `"ZIP"` when ambiguous
- The ZIP guard fires when `input_method == "ZIP"` and `zip_file is None`, returning HTTP 400 "No ZIP file found in the request"
- The fallback ZIP detection expression has an operator precedence bug: `_fn.endswith(".zip") or "zip" in _ct or _ct in (...) and _fn.endswith(".zip")` evaluates incorrectly
**Needs to do instead:**
- The default fallback must NOT be `"ZIP"`. When the `input_method` field is absent and neither file is found, raise HTTP 400 with message "Could not determine upload type. Set input_method to ZIP or CSV, or ensure the file field is named zip_file or csv_file."
- The fallback ZIP detection expression must be parenthesized: `(_fn.endswith(".zip")) or ("zip" in _ct) or (_ct in (...) and _fn.endswith(".zip"))` — the third clause requires both the MIME match AND the .zip extension.
- These two changes together eliminate the false "No ZIP file provided" error on CSV uploads.

### Fix 3.4 — CSV Parser Raises on Oversized Batch

**File:** `portguard/bulk_parsers.py`
**Function:** `parse_csv_upload()`
**Currently does:** Iterates rows; when the row counter reaches 50, logs a warning and silently stops parsing. Returns only the first 50 rows without notifying the caller that data was dropped.
**Needs to do instead:** When the row count exceeds 50 (i.e., when a 51st row is encountered), raise `BatchTooLargeError(f"CSV contains more than {MAX_BATCH_SIZE} rows. Split the file and upload in batches.")`. This matches the behavior of `parse_zip_upload()` and `validate_manual_input()`, which both raise `BatchTooLargeError` at the same threshold. No data is silently dropped.

### Fix 3.5 — Analytics Status Case Normalization

**File:** `portguard/analytics.py`
**Function:** `get_module_summary()` (line ~950) and `get_top_missing_certifications()` (line ~1042)
**Currently does:** Both functions query `WHERE status = 'complete'` (lowercase). `BulkProcessor._store_shipment_result()` writes `status = 'COMPLETE'` (uppercase). SQLite string comparison is case-sensitive. All bulk shipments are invisible to the analytics dashboard.
**Needs to do instead:** Change both SQL string literals to `'COMPLETE'` (uppercase) to match what `BulkProcessor` writes. Verify all other status comparisons in `analytics.py` for the same mismatch and fix any found.

### Fix 3.6 — Confirm ZIP Parser Already Raises Correctly

**File:** `portguard/bulk_parsers.py`
**Function:** `parse_zip_upload()`
**Currently does:** Raises `BatchTooLargeError` when entry count exceeds `MAX_BATCH_SIZE`. This is already correct behavior.
**Needs to do instead:** No change required. Document this as verified-correct in the sprint log after the fix pass.

### Fix 3.7 — Confirm `_build_bulk_response()` Shape Is Frontend-Compatible

**File:** `api/app.py`
**Function:** `_build_bulk_response()` (line 3654)
**Currently does:** Returns `{ batch_id, total, processed, summary, results: [...] }` where each result has `reference_id`, `result_id`, `decision`, `risk_score`, `risk_level`, `flags`, `summary`, `sustainability_rating`, `sustainability_signals`, `active_modules_snapshot`, `status`, `error_message`, `processed_at`.
**Needs to do instead:** No structural change required. Confirm (by reading the actual function during implementation) that `error_message` is present in error rows so the frontend can display the rejection reason. If `error_message` is absent for error rows, add it.

---

## SECTION 4 — FRONTEND FIXES NEEDED

### Fix 4.1 — `authedForm()` Must Not Set Content-Type

**File:** `demo.html`
**Function:** `authedForm(formData)` (the helper that adds auth headers to fetch options)
**Currently does:** Adds the `Authorization: Bearer <token>` header. Risk: if `authedForm()` also sets or merges a `Content-Type` header into the headers object, it overwrites the browser-generated multipart boundary.
**Needs to do instead:** `authedForm()` must only set `Authorization`. It must explicitly NOT set `Content-Type` when the body is a `FormData` object. If there is a generic header-merge that sets Content-Type unconditionally, add a guard: `if (body instanceof FormData) delete headers['Content-Type']`. The browser automatically sets `Content-Type: multipart/form-data; boundary=<uuid>` when the fetch body is FormData — any manual override destroys parsing.

### Fix 4.2 — No "Pending" Pre-Render State

**File:** `demo.html`
**Function:** `_bulkSubmit()` and any result-rendering helpers
**Currently does:** Any logic that adds placeholder "pending" rows to the results table before the response arrives, or partially renders rows during the request, conflicts with the synchronous model (the server returns all results at once after full processing).
**Needs to do instead:** Remove all pre-render placeholder logic. The results table must remain empty (or hidden) until the full response arrives. No partial/pending rows. The processing overlay (Fix 4.3) is the only in-flight indicator.

### Fix 4.3 — Processing Overlay While Request Is In-Flight

**File:** `demo.html`
**Function:** `_bulkSubmit()`
**Currently does:** Shows an indeterminate progress bar animation. Overlay behavior may be incomplete — user can navigate away, close modal, or interact with the table area while processing.
**Needs to do instead:**
- When the POST is dispatched: show the bulk processing overlay (or progress section) with an indeterminate animation and a "Processing batch…" message that includes the shipment count.
- Disable the submit button and the upload inputs so the user cannot trigger a second submission.
- Show the Cancel button; wire it to the AbortController (see Fix 4.4).
- When the response arrives (success or error): hide the overlay, re-enable controls, render results.

### Fix 4.4 — AbortController Timeout

**File:** `demo.html`
**Function:** `_bulkSubmit()`
**Currently does:** An `AbortController` is created and wired to the Cancel button. There is no automatic timeout.
**Needs to do instead:** Add a hard timeout on the `AbortController` signal. 120 seconds (`120_000 ms`) is the correct value — this matches the existing server-side `SHIPMENT_TIMEOUT_SECONDS = 30.0` × up to 4 serial passes with headroom. Call `setTimeout(() => controller.abort(), 120_000)` immediately after the fetch is dispatched. When the timeout fires, show the error message "Request timed out. Try a smaller batch or try again." Clear the timeout if the response arrives before it fires.

### Fix 4.5 — Results Rendered Only After Complete Response

**File:** `demo.html`
**Function:** `_bulkRenderFromResponse(data)` called from `_bulkSubmit()`
**Currently does:** Called with the parsed JSON after `await response.json()`. Maps `data.results[]` to `_bulkAllResults` and renders the table.
**Needs to do instead:** No structural change to timing — results are already rendered after the full response. Verify that the call to `_bulkRenderFromResponse(data)` is inside the `await` chain (not in a `.then()` branch that could execute before JSON parsing completes), and that no partial rendering happens before this call.

### Fix 4.6 — Result Row Display Fields

**File:** `demo.html`
**Function:** `_bulkRenderFromResponse()` and the table-row rendering helper
**Currently does:** Renders columns from `_bulkAllResults`. Current rendering may omit error rows or fail to show `error_message` for rejected entries.
**Needs to do instead:** Each result row must display:
- `reference_id` — the user-supplied shipment reference (or row index if absent)
- `decision` — APPROVE / FLAG_FOR_INSPECTION / REJECT / ERROR / TIMEOUT
- `risk_score` — numeric 0–10 (empty/dash for ERROR/TIMEOUT rows)
- `risk_level` — LOW / MEDIUM / HIGH / CRITICAL (empty for error rows)
- `flags[]` — list of flag codes, each rendered as a badge
- `sustainability_rating` — grade A/B/C/D/N/A (omit for error rows)
- `error_message` — for ERROR/TIMEOUT rows, display the rejection reason in the row so the user knows which entries failed and why
- Share-link button (chain icon) that copies `/#result/{result_id}` to clipboard — omit for error rows where `result_id` is absent

---

## SECTION 5 — STEP BY STEP BUILD ORDER

**1.** Read `portguard/agents/document_classifier_hardened.py` in full to understand the internal scoring structure: where `total_pro` is accumulated, where `anti_score` is computed, and where rejection decisions are made. This is required to write `classify_document_bulk()` correctly without breaking the hardened path.

**2.** Read `portguard/agents/document_classifier.py` in full to confirm the current exports and the dict shape it returns. The new function must return an identical shape.

**3.** In `portguard/agents/document_classifier.py`, add `classify_document_bulk(text: str) -> dict`. The function must invoke the hardened classifier's scoring layer, override only the two permissive-threshold conditions (`total_pro < 2.0` and `confidence < 0.35`), and return the same dict shape. Export it at module level.

**4.** Read `api/app.py` lines 1–50 to find the import block where `_classify_document` is imported from `portguard.agents.document_classifier`. Add `classify_document_bulk` to that import line.

**5.** Read `api/app.py` lines 3300–3325 to locate the exact classifier gate in `_run_bulk_single_analysis()`. Replace the two calls to `_classify_document` with `classify_document_bulk`. Do not change any other logic in this function.

**6.** Read `portguard/bulk_parsers.py` in full. Locate `parse_csv_upload()` and find the truncation-at-50 code. Change the silent truncation to `raise BatchTooLargeError(...)`. Confirm `BatchTooLargeError` is already imported/defined in this file.

**7.** Read `portguard/analytics.py` lines 940–1060. Locate all occurrences of `status = 'complete'` (lowercase). Change each to `status = 'COMPLETE'`. Grep the entire file for `'complete'` and `'error'` to catch any additional case mismatches for status-column comparisons.

**8.** Read `api/app.py` lines 3840–3880 (the fallback scan and `input_method` derivation in `bulk_create()`). Fix the operator precedence bug in the ZIP detection expression by adding parentheses. Change the default `input_method = "ZIP"` fallback to raise `HTTPException(400, "Could not determine upload type...")`.

**9.** Read `demo.html` and search for the `authedForm` function definition. Inspect every line of `authedForm()`. If it sets `Content-Type` anywhere — either directly or via a header-merge loop — add a guard that removes or skips `Content-Type` when the body argument is `FormData`. Verify the function is called the same way in `_bulkSubmit()` for both the ZIP and CSV paths.

**10.** Read `demo.html` lines in `_bulkSubmit()` covering the full submit flow. Confirm the AbortController timeout is 120 000 ms. If it is absent or set to a different value, add/correct it. Confirm the timeout handle is cleared on successful response.

**11.** Read `demo.html` to locate any pre-render / pending-row logic added before the `await response.json()` call. Remove it. Confirm `_bulkRenderFromResponse(data)` is called only after the full JSON is parsed.

**12.** Read `demo.html`'s table-row rendering helper (the function that converts a single result object into a `<tr>`). Confirm all six required fields are present: `reference_id`, `decision`, `risk_score`, `risk_level`, `flags[]` as badges, `sustainability_rating`. Confirm error rows display `error_message` and that the share-link button is conditionally omitted when `result_id` is absent.

**13.** Read `api/app.py` lines 3654–3735 (`_build_bulk_response()`). Confirm `error_message` is included in the result dict for error rows. If it is absent, add it — read from the BulkProcessor's per-shipment result object (`shipment.error_message` or equivalent field name confirmed by reading `BulkProcessor._store_shipment_error()`).

**14.** Start the backend server locally. Upload a CSV with a single short-text row. Confirm the response is HTTP 200 with a valid result (not "Document validation failed" and not "No ZIP file provided").

**15.** Upload a CSV with exactly 50 rows. Confirm it succeeds. Upload a CSV with 51 rows. Confirm HTTP 400 with `BatchTooLargeError` message (not silent truncation).

**16.** Upload a ZIP. Confirm it still works end-to-end.

**17.** Submit a MANUAL batch. Confirm it still works end-to-end.

**18.** Trigger the analytics dashboard. Confirm bulk shipments now appear in module summary and top missing certifications (the `status = 'COMPLETE'` fix).

**19.** Run the existing validation suite (if any) and confirm no regressions on single-document analysis, auth endpoints, or pattern stats endpoints.

**20.** Commit all changed files together in a single commit. Message: "fix(bulk): permissive bulk classifier, CSV field fix, analytics status case, CSV truncation raises". Push to master.

**21.** Update `docs/SPRINT_LOG.md` with a new sprint entry covering all five root causes and all changed files.

---

## Appendix — Files Changed Summary

| File | Section | Change |
|---|---|---|
| `portguard/agents/document_classifier.py` | 3.1 | Add `classify_document_bulk()` |
| `api/app.py` | 3.2 | `_run_bulk_single_analysis()` uses `classify_document_bulk` |
| `api/app.py` | 3.3 | `bulk_create()` operator precedence fix + no default-to-ZIP |
| `api/app.py` | 3.7 | Confirm / add `error_message` in `_build_bulk_response()` |
| `portguard/bulk_parsers.py` | 3.4 | `parse_csv_upload()` raises on > 50 rows |
| `portguard/analytics.py` | 3.5 | Uppercase `'COMPLETE'` in both query literals |
| `demo.html` | 4.1 | `authedForm()` strips Content-Type for FormData |
| `demo.html` | 4.2 | Remove pending pre-render rows |
| `demo.html` | 4.3 | Processing overlay while in-flight |
| `demo.html` | 4.4 | AbortController 120s timeout |
| `demo.html` | 4.6 | Error rows show `error_message`; share button omitted when no `result_id` |
