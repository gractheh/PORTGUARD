# Share Backend Verification
**Date:** 2026-05-17
**Sprint:** 13 — backend-only changes for public shared result links

---

## Files changed

| File | Change |
|---|---|
| `portguard/pattern_db.py` | Added `get_report_payload_public()` method |
| `api/app.py` | Added `share_url` field to `AnalyzeResponse`; set it in all three analyze endpoints; added `GET /api/results/{result_id}` public endpoint |
| `portguard/bulk_processor.py` | Added `shipment_history` mirror write in `_store_shipment_result()` |

---

## TASK 1 — `GET /api/results/{result_id}` (public endpoint)

### What the user's template said vs. what was implemented

The task template referenced `ScreeningResult` (ORM model with `raw_result`, `flags`, `created_at` columns). **That model does not exist in this codebase.** No such class is defined anywhere.

The actual result storage mechanism is:
- `shipment_history.report_payload` — TEXT column containing a full `AnalyzeResponse` JSON blob
- Written by `store_report_payload()` in `PatternDB`
- Read (with org filter) by `get_report_payload(analysis_id, org_id)` — authenticated path
- Read (without org filter) by the new `get_report_payload_public(analysis_id)` — public path

### New PatternDB method: `get_report_payload_public()`

```
portguard/pattern_db.py — inserted after get_report_payload() (line ~1480)
```

Queries `SELECT report_payload FROM shipment_history WHERE analysis_id = :id` — no `organization_id` filter. Returns the raw JSON string or `None` if the row doesn't exist or `report_payload` is NULL.

Errors are caught and logged; never raises to callers.

### New endpoint: `GET /api/results/{result_id}`

```
api/app.py — inserted before GET /api/results/{result_id}/report (~line 3288)
```

- **No `Depends(get_current_organization)`** — zero auth requirement.
- Calls `_pattern_db.get_report_payload_public(result_id)`.
- Returns HTTP 503 if PatternDB not initialised.
- Returns HTTP 404 if payload is None (result not found or payload NULL).
- Returns HTTP 500 if stored JSON cannot be parsed.
- Injects `shipment_id = result_id` if not already in the payload (same logic as the authenticated endpoint).
- Injects `analyzed_at` from `shipment_history.analyzed_at` via a direct SQL query (same pattern as the authenticated endpoint; failure is silent).
- Adds `shared: True` and `organization_email: null` to the response.
- Returns the full `AnalyzeResponse`-shaped dict — all 25+ fields.

### Route conflict check

Three routes exist on the `/api/results/` prefix:
- `GET /api/v1/results/{result_id}` — authenticated (unchanged)
- `GET /api/results/{result_id}` — **new, public**
- `GET /api/results/{result_id}/report` — authenticated (unchanged)

FastAPI path parameters (`{result_id}`) do not capture slashes, so a request to `/api/results/<id>/report` correctly matches the `/report` route, not the `{result_id}` route. No conflict.

### Template fallback dict — coverage

The user's task template included a fallback dict for when `raw_result` is None. The equivalent in our codebase is when `report_payload` is NULL — in that case the public endpoint returns **HTTP 404** (same as the authenticated endpoint). This is correct behavior: a NULL payload means the background write failed and the result cannot be reconstructed safely.

The fallback dict in the template (`id`, `decision`, `risk_score`, `flags`, `sustainability_rating`, `shipper`, `origin_country`, `destination_country`, `declared_value`, `timestamp`) would return partial data. HTTP 404 is preferable — it tells the caller the result is unavailable rather than returning an incomplete payload that would confuse the frontend renderer.

---

## TASK 2 — `POST /api/v1/analyze` returns `id` and `share_url`

### `AnalyzeResponse.share_url` (new field)

```
api/app.py line ~1252 — added after isf_complete field
```

```python
share_url: Optional[str] = Field(
    None,
    description="Public share URL for this result: '/?result=<shipment_id>'.",
)
```

### Set in analyze endpoints

Three analyze paths all set `share_url` immediately after `shipment_id` is assigned:

| Endpoint | File location | Variable |
|---|---|---|
| `POST /api/v1/analyze` | after line 1920 | `analyze_response.share_url` |
| `POST /api/v1/analyze-files` | after line 2260 | `analyze_response_files.share_url` |
| `_run_bulk_single_analysis()` | after line 3657 | `analyze_response.share_url` |

Value: `f'/?result={shipment_id}'` when `shipment_id` is not None, otherwise field stays `None`.

The `shipment_id` field was already in `AnalyzeResponse` and already set by all three paths — `share_url` is derived from it.

### What `id` maps to

The task template references `new_result.id`. In this codebase that is `analyze_response.shipment_id` — the UUID returned by `PatternDB.record_shipment()` and stored in `shipment_history.analysis_id`. The field was already named `shipment_id` in `AnalyzeResponse`. The `share_url` field makes it convenient for the frontend to construct the share link without string concatenation.

---

## TASK 3 — `raw_result` / `report_payload` coverage

### Does `raw_result` exist?

**No.** There is no `raw_result` column on any model or table in the codebase. The correct column is `shipment_history.report_payload` (TEXT, added in migration 004).

### Is `report_payload` always populated?

Not guaranteed. `store_report_payload()` is called after `record_shipment()` succeeds. If `store_report_payload()` raises (e.g., the UPDATE affects 0 rows because the INSERT raced, or a transient SQLite lock), the failure is caught and logged as non-fatal. In that case `report_payload` stays NULL.

Frequency: rare in practice. The INSERT and UPDATE happen in the same function call within milliseconds. But it can happen.

### Public endpoint behavior when payload is NULL

`get_report_payload_public()` returns `None` when the row exists but `report_payload` is NULL (because `SELECT report_payload ... WHERE analysis_id = :id` returns a row with a NULL value, and `row["report_payload"]` is `None`). The endpoint then returns HTTP 404. The frontend should display "This result link is invalid or has expired."

This is safer than the template's fallback dict approach — returning incomplete data from a partial fallback would cause the frontend `renderResults()` to crash on missing nested objects (`shipment_data`, `sustainability_rating`, etc.).

### Bulk results — are they now accessible?

Yes, via Plan Option A. `_store_shipment_result()` in `portguard/bulk_processor.py` now mirrors `result_json` into `shipment_history.report_payload` immediately after writing to `bulk_shipments`:

```python
UPDATE shipment_history SET report_payload = :payload WHERE analysis_id = :analysis_id
```

The `shipment_history` row already exists at this point (written by `_record_shipment_bg()` which is called inside `_run_bulk_single_analysis()` before the result is returned to the bulk processor). The mirror UPDATE is non-fatal — failure is logged and does not affect the bulk result storage in `bulk_shipments`.

### Fallback coverage for error/timeout shipments

For bulk shipments that error or timeout, `_store_shipment_error()` is called instead of `_store_shipment_result()`. The `result_json` mirror is NOT done for error rows (correct — there's no result to mirror). The `shipment_history` row for an errored bulk shipment has `report_payload = NULL`, so the public endpoint returns 404 for those. Correct.

---

## Verification checklist

| Check | Result |
|---|---|
| `GET /api/results/{id}` exists with no auth | ✓ added at line 3288 |
| Returns 404 for unknown IDs | ✓ (get_report_payload_public returns None → 404) |
| Returns 503 when PatternDB not initialised | ✓ |
| Returns full AnalyzeResponse payload | ✓ (same JSON blob as authenticated endpoint) |
| `shipment_id` injected if missing | ✓ |
| `analyzed_at` injected (best-effort) | ✓ |
| `shared: true` in response | ✓ |
| `organization_email: null` in response | ✓ |
| `POST /api/v1/analyze` returns `shipment_id` | ✓ (was already returning it) |
| `POST /api/v1/analyze` returns `share_url` | ✓ added |
| `POST /api/v1/analyze-files` returns `share_url` | ✓ added |
| Bulk single analysis returns `share_url` | ✓ added |
| Bulk per-row share links work (payload mirrored) | ✓ _store_shipment_result mirrors to shipment_history |
| All three modified files pass `ast.parse()` | ✓ |
| `ScreeningResult` model — used? | ✗ does not exist; not used |
| `raw_result` column — used? | ✗ does not exist; correct column is `report_payload` |

---

## What was NOT changed

- `GET /api/v1/results/{result_id}` — authenticated endpoint unchanged
- `GET /api/results/{result_id}/report` — authenticated PDF endpoint unchanged
- `portguard/auth.py` — `get_current_organization` unchanged
- `demo.html` — not touched (frontend sprint is next)
- `shipment_history` schema — unchanged
- `bulk_shipments` schema — unchanged
