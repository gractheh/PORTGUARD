# PORTGUARD Sprint Log

---

## Sprint: Bulk Upload — Full Implementation + QA Pass
**Date:** 2026-04-29  
**Branch:** master  
**Status:** Closed

---

### What was changed

#### Backend (`api/app.py`, `portguard/pattern_db.py`)

- **`POST /api/v1/analyze/bulk`** — rewired to synchronous execution via `asyncio.gather`. All rows processed before HTTP response; no polling loop required. Returns `data.results[]` with per-row `result_id`, `reference_id`, `decision`, `risk_score`, `flags`, `sustainability_rating`, `sustainability_signals`, `active_modules_snapshot`.
- **`GET /api/v1/results/{result_id}`** — new JWT-protected endpoint. Returns stored `AnalyzeResponse` JSON (200), 404 if unknown, 403 if result belongs to a different org. Used by the single-result share-link flow.
- **`PatternDB.get_result_owner(analysis_id)`** — new method that returns the owning `organization_id` without leaking row data, enabling the 404 vs 403 distinction in the results endpoint.
- **`import json` hoisted** — was only imported locally inside functions; moved to module-level to fix `RESULT_CORRUPT` 500 on the new endpoint.
- **Dead code removed** — unreachable `return _pdf_response(pdf_bytes, shipment_id)` line at end of `bulk_csv_template()` (refactoring artifact).

#### Frontend (`demo.html`)

- **Bulk submit** — synchronous response model. Single POST awaited; results rendered from `_bulkRenderFromResponse(data)` immediately. No polling. AbortController wired to Cancel button. Indeterminate progress bar animation while request is in-flight.
- **`_bulkRenderFromResponse(data)`** — new function maps POST response shape (`data.results[]`) to `_bulkAllResults`. Handles `reference_id`, `result_id`, `flags`, `sustainability_rating`, `sustainability_signals`, `active_modules_snapshot`.
- **Share links** — per-row chain-link button copies `/#result/{result_id}`. Single-doc share button writes `/#result/{shipmentId}`. Deep-link routing: `#result/` hash → `_pendingResultId`; `?batch=` query → `_pendingBatchId`. Both restored after login via `hideAuthOverlay()`.
- **`_loadResultById(resultId)`** — fetches `GET /api/v1/results/{id}`; routes 403/404 to `showError()`; success calls `renderResults(data)`.
- **PDF preview** — `.bulk-slt-pdf-panel` capped at `max-height: 600px; overflow-y: auto`. Bulk thumb wraps use `width: auto`. `--thumb-h` CSS variable set per-wrap in `_bulkPdfRenderThumb()` for independent beam calibration.
- **Per-row beam animation** — `enabled_modules` list from `_moduleToggles` passed in manual batch POST body.
- **CSV export** — built client-side from `_bulkAllResults`; includes `sustainability_grade`, `sustainability_signals`, `active_modules` columns.
- **Polish pass** — `.bulk-badge.TIMEOUT` CSS added; sustainability badge colors normalized to spec (A `#22c55e`, B `#14b8a6`, C `#f59e0b`, D `#ef4444`, N/A `#6b7280`); `.bulk-table-wrap` changed from `overflow: hidden` to `overflow-x: auto`; `_bulkShowProgressError` no longer disables Cancel button (user can always start over); `bulkSort()` now clears `.sorted` from all headers before setting the active one; `expandId` and ref cell null-guarded for rows with missing `ref`.
- **Debug cleanup** — removed 3 `console.log` statements from login flow (logged email and full auth response — a privacy/security concern in production).

---

### Known limitations

- **ZIP upload** — ZIP parsing is server-side; very large ZIPs (> 50 entries) are accepted but each entry's text is truncated at the API's per-document limit. No per-entry progress is shown.
- **Batch result persistence** — individual `result_id` values are persisted per-row for share links. The batch-level `batch_id` is still returned but the polling endpoints are effectively unused; `?batch=` deep-links route to the legacy GET-results path which requires the batch to exist in the DB.
- **No sticky columns** — Reference ID and Decision columns scroll off on very narrow screens. `overflow-x: auto` is enabled but `position: sticky` on those columns was not added (border-collapse interaction on some browsers requires extra handling).
- **PDF beam animation in bulk** — beam height calibrated via `--thumb-h` CSS var set after render. If the PDF.js render is very fast the beam may not be visible before it completes. Not a bug — intentional UX tradeoff.
- **JWT secret not persisted** — `PORTGUARD_JWT_SECRET` env var not set on local dev; server restart invalidates all existing tokens. Set the env var before deploying to Render to avoid this.
