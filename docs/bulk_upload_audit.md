# Bulk Upload System Audit

**Date:** 2026-04-27  
**Auditor:** Claude (automated)  
**Scope:** End-to-end audit of the bulk shipment screening system — backend endpoint, frontend UI, manual entry, and sustainability/module integration.

---

## 1. Backend — `POST /api/v1/analyze/bulk`

### 1.1 Route Location

**File:** `api/app.py`, line 3093  
**Framework:** FastAPI, registered directly on the top-level `app` instance (not through the `portguard/api/routes.py` sub-router, which only handles the legacy `portguard` package routes).

```python
@app.post("/api/v1/analyze/bulk", status_code=202)
async def bulk_create(
    request: Request,
    background_tasks: BackgroundTasks,
    current_org: dict = Depends(get_current_organization),
):
```

### 1.2 Input Path Trace

The handler reads `content-type` to branch between file-upload and JSON paths.

#### Path A — ZIP upload (`multipart/form-data`)

Expected request:
```
POST /api/v1/analyze/bulk
Content-Type: multipart/form-data; boundary=...
Authorization: Bearer <token>

zip_file=<binary .zip>
input_method=ZIP          ← optional; auto-detected if absent and zip_file present
```

Code trace (app.py lines 3153–3197):
1. `form = await request.form()`
2. `input_method = str(form.get("input_method", "ZIP")).upper()`
3. `zip_file = form.get("zip_file")`
4. `csv_file = form.get("csv_file")`
5. Auto-detect: if `zip_file and not csv_file` → set `input_method = "ZIP"`
6. Branch: `if input_method == "ZIP":` → if `zip_file is None` → raise HTTP 400 `MISSING_FILE "No zip_file provided."`
7. Read bytes, enforce 50 MB limit, call `parse_zip_upload(raw)`
8. Parser (`portguard/bulk_parsers.py:117`) traverses the ZIP, extracts top-level subfolders as shipments, reads `.txt` and `.pdf` files.

#### Path B — CSV upload (`multipart/form-data`)

Expected request:
```
POST /api/v1/analyze/bulk
Content-Type: multipart/form-data; boundary=...
Authorization: Bearer <token>

csv_file=<binary .csv>
input_method=CSV          ← optional; auto-detected if absent and csv_file present
```

Code trace (app.py lines 3199–3231):
1. `csv_file = form.get("csv_file")`
2. Auto-detect: `elif csv_file and not zip_file` → set `input_method = "CSV"`
3. Branch: `elif input_method == "CSV":` → if `csv_file is None` → raise HTTP 400 `MISSING_FILE "No csv_file provided."`
4. Read bytes, enforce 5 MB limit, call `parse_csv_upload(raw)`
5. Parser (`portguard/bulk_parsers.py:268`) uses `csv.DictReader`, identifies reference column from `_CSV_REF_COLUMNS`, maps document columns from `_CSV_DOC_COLUMNS`.

Recognized CSV document columns:
- `bill_of_lading`, `commercial_invoice`, `packing_list`, `certificate_of_origin`, `isf_filing`, `other_doc_1`

Recognized CSV reference columns:
- `reference_id`, `shipment_ref`, `ref`, `id`, `reference`, `shipment_id`

#### Path C — Manual JSON (`application/json`)

Expected request:
```
POST /api/v1/analyze/bulk
Content-Type: application/json
Authorization: Bearer <token>

{
  "input_method": "MANUAL",
  "shipments": [
    {
      "ref": "SHP-001",
      "documents": [
        { "filename": "bill_of_lading.txt", "raw_text": "..." }
      ]
    }
  ]
}
```

Code trace (app.py lines 3233–3262):
1. `body = await request.json()`
2. `input_method = str(body.get("input_method", "MANUAL")).upper()`
3. `raw_shipments = body.get("shipments", [])`
4. Call `validate_manual_input(raw_shipments)` → validates/normalizes the list

### 1.3 The "No zip_file provided" Error on CSV Input — Root Cause

**This is a frontend bug, not a backend logic error.** The backend code is correct. The frontend sends the wrong FormData key.

In `demo.html:_bulkSubmit()` (line 8479–8482):
```javascript
} else if (_bulkMethod === 'csv' && _bulkCsvFile) {
  const fd = new FormData();
  fd.append('file', _bulkCsvFile);   // ← BUG: key is 'file', not 'csv_file'
  res = await fetch(apiUrl() + '/api/v1/analyze/bulk', authedForm(fd));
}
```

The frontend also **does not append `input_method`** to the FormData in either the ZIP or CSV paths.

Backend execution when a CSV is submitted this way:
1. `form.get("input_method", "ZIP")` → `"ZIP"` (default, no field present)
2. `zip_file = form.get("zip_file")` → `None` (no field named `zip_file`)
3. `csv_file = form.get("csv_file")` → `None` (file is under key `"file"`, not `"csv_file"`)
4. Auto-detect: `if zip_file and not csv_file` → False; `elif csv_file and not zip_file` → False
5. `input_method` stays `"ZIP"`
6. `if input_method == "ZIP": if zip_file is None: raise HTTP 400 "No zip_file provided."`

**Result:** User uploads a CSV, receives error `"No zip_file provided."` — the wrong file type's error message.

The same bug affects ZIP upload: `fd.append('file', _bulkZipFile)` instead of `fd.append('zip_file', _bulkZipFile)`.

### 1.4 What the Endpoint Returns (HTTP 202)

```json
{
  "batch_id": "<uuid>",
  "total_shipments": 12,
  "status": "PROCESSING",
  "input_method": "ZIP",
  "created_at": "2026-04-27T12:00:00+00:00",
  "status_url": "/api/v1/analyze/bulk/<uuid>/status",
  "results_url": "/api/v1/analyze/bulk/<uuid>/results"
}
```

Processing continues asynchronously via `BackgroundTasks`. The client polls `/status` every 2 seconds.

### 1.5 Supporting Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/analyze/bulk/{batch_id}/status` | Real-time progress |
| `GET`  | `/api/v1/analyze/bulk/{batch_id}/results` | Full results (supports `?sort=` and `?decision=` filters) |
| `GET`  | `/api/v1/analyze/bulk/{batch_id}/export/csv` | CSV export of results |
| `GET`  | `/api/v1/analyze/bulk/{batch_id}/export/zip` | ZIP of per-shipment PDF reports |
| `GET`  | `/api/v1/analyze/bulk/csv-template` | Download blank CSV template |

---

## 2. Frontend — `demo.html` Bulk Upload Section

### 2.1 How the UI Constructs the Request

The UI has three method cards: **ZIP**, **CSV Manifest**, **Manual Entry**. The active method is tracked in `_bulkMethod` (JS state, initialized to `null`).

#### ZIP path (demo.html:8475–8478)
```javascript
const fd = new FormData();
fd.append('file', _bulkZipFile);   // ← BUG: should be 'zip_file'
// No input_method field appended
res = await fetch(apiUrl() + '/api/v1/analyze/bulk', authedForm(fd));
```

#### CSV path (demo.html:8479–8482)
```javascript
const fd = new FormData();
fd.append('file', _bulkCsvFile);   // ← BUG: should be 'csv_file'
// No input_method field appended
res = await fetch(apiUrl() + '/api/v1/analyze/bulk', authedForm(fd));
```

#### Manual path (demo.html:8483–8495)
```javascript
const shipments = _bulkSlots
  .filter(s => s.tabs.some(t => t.text.trim()))
  .map(s => ({
    ref: s.ref || ('SHP-' + String(s.id).padStart(3,'0')),
    documents: s.tabs
      .filter(t => t.text.trim())
      .map(t => ({ filename: t.filename || 'document.txt', raw_text: t.text.trim() })),
  }));
res = await fetch(apiUrl() + '/api/v1/analyze/bulk',
  authedJson('POST', { input_method: 'MANUAL', shipments }));
```

Manual path is correct. JSON body, correct `input_method`, correct `documents` key shape.

### 2.2 FormData Key Mismatch Summary

| Input Mode | Frontend sends | Backend expects | Match? |
|-----------|----------------|-----------------|--------|
| ZIP       | `fd.append('file', zipFile)` | `form.get("zip_file")` | **NO — BROKEN** |
| CSV       | `fd.append('file', csvFile)` | `form.get("csv_file")` | **NO — BROKEN** |
| Manual    | JSON body `{shipments:[...]}` | `body.get("shipments")` | Yes ✓ |
| ZIP input_method | not sent (missing) | `form.get("input_method", "ZIP")` | Happens to default correctly |
| CSV input_method | not sent (missing) | `form.get("input_method", "ZIP")` | **Wrong: defaults to "ZIP" instead of "CSV"** |

### 2.3 `authedForm` helper

```javascript
function authedForm(formData) {
  const h = {};
  if (_authToken) h['Authorization'] = 'Bearer ' + _authToken;
  return { method: 'POST', headers: h, body: formData };
}
```

This is correctly implemented — it does NOT set `Content-Type`, letting the browser set the multipart boundary automatically. The bug is upstream in the key names.

---

## 3. Manual Entry Section

### 3.1 Capacity

- **Maximum slots:** 50 (enforced at `bulkAddSlot()` line 8178: `if (_bulkSlots.length >= 50) return;`)
- **Starts empty:** 0 slots; user clicks "Add Shipment Slot" to add each one.

### 3.2 Fields per Slot

Each slot (`_bulkSlots` array item) contains:
- `ref` — reference ID string (editable inline text input, defaults to `"SHP-001"`, `"SHP-002"`, etc.)
- `tabs` — array of document tabs; each tab contains:
  - `filename` — editable text input (default: `"bill_of_lading.txt"`)
  - `text` — large textarea for document content
- Up to **10 documents per slot** (enforced by `validate_manual_input` in `bulk_parsers.py:487`)

A slot starts with one tab (filename `bill_of_lading.txt`). Users can add additional document tabs via a "+" button. The tab close button is hidden when only one tab exists.

### 3.3 PDF Upload Capability in Manual Entry

**There is NO PDF upload capability in manual entry.** Each slot is text-only. Users paste raw document text into a textarea. There is no file picker or drag-drop zone within the manual slot UI. This is the confirmed gap.

The only PDF ingestion in the entire bulk flow exists in the **ZIP path** only — when a `.pdf` file is found inside a shipment folder within the ZIP archive, `parse_zip_upload()` calls `api.document_parser.extract_text()` to extract its text (bulk_parsers.py:221–228).

### 3.4 enabled_modules / org context in Manual Entry

The manual entry JSON payload is:
```json
{ "input_method": "MANUAL", "shipments": [...] }
```

**`enabled_modules` is NOT included in the bulk request body.** The org's enabled module configuration is looked up on the backend during per-shipment analysis... **except it is not**. See Section 4 below — this is another confirmed gap.

---

## 4. Sustainability & Modules Integration

### 4.1 What the regular `/api/v1/analyze` endpoint does

The single-shipment `POST /api/v1/analyze` endpoint (app.py lines 1689–1740) runs:

1. **Stage 3.5 — Certification module screening:**
   ```python
   enabled_modules = _module_config_db.get_enabled_modules(org_id)
   screener = CertificationScreener(enabled_modules)
   cert_result = screener.screen(sd, all_text)
   ```
2. **Stage 5.5 — Sustainability rating:**
   ```python
   rater = SustainabilityRater()
   sustainability = rater.rate(sd, cert_result, all_text)
   ```
3. The `AnalyzeResponse` includes: `sustainability_rating`, `module_findings`, `active_modules_at_scan`, `modules_triggered`.

### 4.2 What `_run_bulk_single_analysis` does

The bulk pipeline function `_run_bulk_single_analysis` (app.py lines 2799–2954) runs:

1. Core rule-based analysis ✓
2. Pattern learning overlay ✓
3. **Does NOT run `CertificationScreener`** ✗
4. **Does NOT call `_module_config_db.get_enabled_modules()`** ✗
5. **Does NOT run `SustainabilityRater`** ✗

The `AnalyzeResponse` is constructed at line 2912 without `sustainability_rating`, `module_findings`, `active_modules_at_scan`, or `modules_triggered`. All four fields default to `None` / `[]`.

**Impact:** Every shipment processed via bulk upload silently skips certification and sustainability analysis, even if the org has modules enabled. The gap is invisible to the user — no error, no warning, just absent data.

### 4.3 Does the bulk result include `sustainability_rating` per row?

**No.** The `bulk_shipments` database table (bulk_processor.py lines 158–175) has no `sustainability_rating` column:

```sql
CREATE TABLE IF NOT EXISTS bulk_shipments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        TEXT    NOT NULL,
    shipment_ref    TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'PENDING',
    decision        TEXT,
    risk_score      REAL,
    risk_level      TEXT,
    n_findings      INTEGER,
    top_finding     TEXT,
    analysis_id     TEXT,
    result_json     TEXT,           ← full payload stored here as JSON string
    error_message   TEXT,
    processed_at    TEXT,
    ...
);
```

`sustainability_rating` is absent from the schema. Even if the bulk analysis were fixed to produce it, it would need a new column (or to be extracted from `result_json`) to surface in status/export responses.

### 4.4 Does the CSV export include `sustainability_rating`?

**No.** The CSV export headers (app.py lines 3491–3494):
```python
_CSV_HEADERS = [
    "shipment_ref", "status", "decision", "risk_score", "risk_level",
    "n_findings", "top_finding", "analysis_id", "error_message", "processed_at",
]
```

`sustainability_rating` is not a column. `get_export_rows()` (bulk_processor.py:596) only queries these same columns from the DB.

### 4.5 Does the bulk results table in the UI show sustainability?

**No.** The results table (demo.html lines 3508–3515) has columns:
- Reference ID, Decision, Risk Score, Findings, Top Finding, Actions

No sustainability column. The expand row (line 8780–8786) shows "Analysis Findings" only, not sustainability or module findings.

---

## 5. Complete Bug & Gap Inventory

### BUG-1 — CRITICAL: ZIP upload sends wrong FormData key

**File:** `demo.html:8477`  
**Code:** `fd.append('file', _bulkZipFile)`  
**Should be:** `fd.append('zip_file', _bulkZipFile)`  
**Symptom:** Every ZIP batch submission fails with HTTP 400 `{"code":"MISSING_FILE","message":"No zip_file provided."}`.  
**Impact:** ZIP upload is completely non-functional from the UI.

### BUG-2 — CRITICAL: CSV upload sends wrong FormData key

**File:** `demo.html:8481`  
**Code:** `fd.append('file', _bulkCsvFile)`  
**Should be:** `fd.append('csv_file', _bulkCsvFile)`  
**Symptom:** Every CSV batch submission fails with HTTP 400 `{"code":"MISSING_FILE","message":"No zip_file provided."}` — the wrong file type's error message, because the backend defaults `input_method` to "ZIP" when it receives an unrecognized field.  
**Impact:** CSV upload is completely non-functional from the UI.

### BUG-3 — HIGH: `input_method` not appended to FormData for file uploads

**File:** `demo.html:8475–8482`  
**Problem:** Neither the ZIP nor CSV FormData submissions append `input_method` to the form. This works for ZIP (because the backend defaults to "ZIP"), but is misleading and fragile. For CSV it is a direct contributor to BUG-2.  
**Fix:** Add `fd.append('input_method', 'ZIP')` and `fd.append('input_method', 'CSV')` respectively.

### GAP-1 — HIGH: Bulk pipeline does not run certification module screening

**File:** `api/app.py:2799–2954` (`_run_bulk_single_analysis`)  
**Problem:** The CertificationScreener is never instantiated. `enabled_modules` is never fetched from `_module_config_db`. `module_findings`, `active_modules_at_scan`, and `modules_triggered` are all empty in every bulk result.  
**Impact:** Orgs with screening modules enabled get zero module findings in bulk mode. Feature is silently absent.

### GAP-2 — HIGH: Bulk pipeline does not run sustainability rating

**File:** `api/app.py:2799–2954` (`_run_bulk_single_analysis`)  
**Problem:** `SustainabilityRater` is never instantiated. `sustainability_rating` is `None` in every bulk result.  
**Impact:** Dashboard sustainability KPI (`sustainability_grade_counts`) is never populated by bulk-screened shipments. Bulk results table shows no sustainability data.

### GAP-3 — MEDIUM: `bulk_shipments` DB table has no `sustainability_rating` column

**File:** `portguard/bulk_processor.py:135–181`  
**Problem:** Even after fixing GAP-2, the `sustainability_rating` value has nowhere to be stored as a queryable field. It would need to be extracted from `result_json` or a new column added.  
**Impact:** Sustainability is not filterable, sortable, or exportable in bulk context.

### GAP-4 — MEDIUM: CSV export has no `sustainability_rating` column

**File:** `api/app.py:3491–3494`  
**Problem:** The exported CSV does not include `sustainability_rating` even though it is present in `result_json` for single-shipment analyses.  
**Impact:** Compliance officers cannot bulk-export sustainability grades for reporting.

### GAP-5 — MEDIUM: No PDF upload in manual entry

**File:** `demo.html` — manual entry slot UI  
**Problem:** Manual entry slots only accept pasted text. There is no file picker, drag-drop, or PDF extraction within a slot. Officers who have a physical PDF for a manual entry shipment must extract text themselves.  
**Impact:** Workflow friction; manual entry is text-only while ZIP path supports PDFs.

### GAP-6 — LOW: Results table has no sustainability column

**File:** `demo.html:3506–3521`  
**Problem:** The bulk results table does not show `sustainability_rating` per row.  
**Impact:** Officers cannot see sustainability grades at a glance in bulk results.

### GAP-7 — LOW: Bulk results expand row does not show module findings

**File:** `demo.html:8780–8786`  
**Problem:** The inline expand row shows "Analysis Findings" (explanations) but not `module_findings` or `active_modules_at_scan`.  
**Impact:** Per-shipment certification screening detail is inaccessible in bulk mode.

### GAP-8 — LOW: `validate_manual_input` accepts `text` key as alias for `raw_text`

**File:** `portguard/bulk_parsers.py:491`  
**Code:** `raw_text = str(d.get("raw_text", d.get("text", "")))`  
**Observation:** The frontend always sends `raw_text` (line 8491), but the parser accepts `text` as a fallback. This is intentional defensive coding and not a bug, but worth documenting: the contract is `raw_text`, not `text`.

### GAP-9 — INFO: Rate limit is 3 batches/minute per org

**File:** `api/app.py:3046–3085`  
**Value:** `_BULK_RATE_LIMIT_PER_MINUTE = 3`  
**Note:** This is not surfaced in any UI label or tooltip. Users who hit the limit see a generic error banner with no countdown.

---

## 6. Architecture Notes

- **Two separate FastAPI apps:** `portguard/api/main.py` (legacy `portguard` package routes; no bulk upload) and `api/app.py` (the main app; all bulk upload endpoints live here). The bulk system is entirely in `api/app.py`.
- **Async processing:** `process_batch` is an `async def` coroutine scheduled as a `BackgroundTask`. It runs up to 5 shipments concurrently via `asyncio.Semaphore(5)` + `ThreadPoolExecutor(max_workers=5)`.
- **Per-shipment timeout:** 30 seconds (`SHIPMENT_TIMEOUT_SECONDS`). Timeouts mark the shipment `ERROR` and continue the batch.
- **`result_json` storage:** Full `AnalyzeResponse` JSON is stored per shipment. The ZIP-of-PDFs export uses this to regenerate PDFs without re-analysis. This is the right place to store sustainability data once GAP-2 and GAP-3 are fixed.
- **Org isolation:** Every batch is scoped to `organization_id`. All query, status, export, and delete paths filter by org, preventing cross-tenant data access.
