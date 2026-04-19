# PortGuard Bulk Upload Architecture

**Document type:** Implementation architecture plan  
**Feature:** Bulk Shipment Screening  
**Status:** Pre-implementation (plan only — no code written yet)  
**Scope:** `api/app.py`, `portguard/pattern_db.py`, `demo.html`

---

## 1. Overview

Bulk Upload adds a second screening mode to PortGuard. Instead of uploading one
shipment at a time, a compliance officer submits up to 50 shipments in a single
request. Each shipment passes through the **identical existing pipeline**
(`_analyze_documents()` → pattern learning overlay → `_record_shipment_bg()`) so
risk decisions are produced by the same engine, with the same rules and thresholds,
as a single-shipment analysis.

The feature introduces:

- Three new input methods (ZIP, CSV, manual batch)
- A background processing queue with per-shipment fault isolation
- Five new API endpoints
- Two new database tables
- A bulk-results UI section in `demo.html`

Nothing in the existing single-shipment pipeline is modified. Bulk is a wrapper
around the existing code, not a replacement.

---

## 2. High-Level Data Flow

```
Officer submits batch
        │
        ▼
POST /api/v1/analyze/bulk
  ├─ Authenticate JWT (same get_current_organization dep)
  ├─ Parse input (ZIP / CSV / JSON)
  ├─ Validate: ≤50 shipments, each shipment ≤10 docs
  ├─ Insert bulk_batches row (status=PENDING)
  ├─ Insert bulk_shipments rows (status=PENDING)
  └─ Schedule background task → return {batch_id, status_url}
        │
        ▼
BackgroundTask: _process_bulk_batch(batch_id, shipments, org_id)
  ├─ Mark batch status=PROCESSING
  ├─ asyncio.Semaphore(5) — max 5 concurrent shipment analyses
  ├─ For each shipment (concurrent, fault-isolated):
  │     ├─ Mark shipment status=PROCESSING
  │     ├─ _validate_documents() gate
  │     ├─ _analyze_documents()
  │     ├─ Pattern learning overlay
  │     ├─ _record_shipment_bg() → analysis_id
  │     └─ Update bulk_shipments row (status=COMPLETE or ERROR)
  └─ Mark batch status=COMPLETE, set completed_at
        │
        ▼
Frontend polls GET /api/v1/analyze/bulk/{batch_id}/status
  every 2 seconds until status=COMPLETE
        │
        ▼
GET /api/v1/analyze/bulk/{batch_id}/results
  → renders results table in demo.html
```

---

## 3. Input Methods

### 3.1 ZIP File Upload

The officer uploads a ZIP file where each top-level subfolder represents one
shipment. The subfolder name becomes the shipment reference ID. Files inside
each subfolder are individual trade documents (`.txt` or `.pdf`).

**Expected ZIP structure:**

```
batch.zip
├── SHP-001/
│   ├── bill_of_lading.txt
│   ├── commercial_invoice.txt
│   └── packing_list.txt
├── SHP-002/
│   ├── bill_of_lading.pdf
│   └── commercial_invoice.txt
└── SHP-003/
    ├── bill_of_lading.txt
    ├── commercial_invoice.txt
    ├── packing_list.txt
    └── certificate_of_origin.txt
```

**Server-side extraction logic:**

```python
import zipfile, io

def _parse_zip_input(file_bytes: bytes) -> list[dict]:
    """
    Returns list of:
      {"ref": "SHP-001", "documents": [{"filename": "bill_of_lading.txt", "text": "..."}]}
    """
    shipments = {}
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
        for entry in zf.namelist():
            parts = entry.strip("/").split("/")
            if len(parts) < 2:
                continue                          # skip root-level loose files
            folder = parts[0]
            filename = parts[-1]
            if not filename or filename.startswith("."):
                continue                          # skip directories and hidden files
            raw = zf.read(entry)
            # Reuse existing document_parser.extract_text() for PDF support
            try:
                result = extract_text(raw, filename)
                text = result.text
            except DocumentParserError:
                continue                          # skip unreadable files silently
            shipments.setdefault(folder, []).append(
                {"filename": filename, "text": text}
            )
    return [{"ref": ref, "documents": docs} for ref, docs in shipments.items()]
```

**Constraints:**
- Max ZIP file size: 50 MB (enforced before extraction)
- Max 50 subfolders (shipments) per ZIP
- Max 10 documents per subfolder (matches single-shipment limit)
- Nested subdirectories are flattened to their parent shipment folder
- Files that fail `extract_text()` are silently skipped (not counted as errors)
- A shipment folder with zero extractable files is skipped entirely

### 3.2 CSV File Upload

Each row represents one shipment. Text for each document type is provided in
dedicated columns. The officer pastes document text directly into the CSV cells.

**Required CSV columns:**

| Column | Description | Required |
|---|---|---|
| `shipment_ref` | Unique reference ID for this shipment | Yes |
| `bill_of_lading` | Full text of the B/L | No |
| `commercial_invoice` | Full text of the commercial invoice | No |
| `packing_list` | Full text of the packing list | No |
| `certificate_of_origin` | Full text of the COO | No |
| `isf_filing` | Full text of the ISF filing | No |
| `other_doc_1` | Optional additional document | No |

At least one document column must be non-empty per row for the row to be included.

**Server-side parsing logic:**

```python
import csv, io

_CSV_DOC_COLUMNS = [
    "bill_of_lading", "commercial_invoice", "packing_list",
    "certificate_of_origin", "isf_filing", "other_doc_1",
]

def _parse_csv_input(file_bytes: bytes) -> list[dict]:
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    shipments = []
    for row in reader:
        ref = row.get("shipment_ref", "").strip()
        if not ref:
            continue
        docs = []
        for col in _CSV_DOC_COLUMNS:
            val = row.get(col, "").strip()
            if val:
                docs.append({"filename": col + ".txt", "text": val})
        if docs:
            shipments.append({"ref": ref, "documents": docs})
    return shipments
```

**Constraints:**
- Max CSV file size: 5 MB
- `shipment_ref` must be unique within the file; duplicates are deduplicated
  by keeping the first occurrence
- CSV must use UTF-8 encoding (UTF-8 BOM accepted)
- Max 50 data rows processed (rows beyond 50 are silently dropped)

**Template generation:** `GET /api/v1/analyze/bulk/csv-template` returns a
minimal CSV with headers and one example row so officers can download and fill
it in without having to create the column layout from scratch.

### 3.3 Manual Batch Entry

The officer adds shipment slots directly in the UI. Each slot has the same
document tab interface as the existing single-shipment upload — textarea per
document type — plus a Shipment Reference ID field at the top of the slot.

**POST body (JSON):**

```json
{
  "input_method": "MANUAL",
  "shipments": [
    {
      "ref": "SHP-001",
      "documents": [
        {"filename": "bill_of_lading.txt", "text": "BILL OF LADING\nB/L No: COSU1234..."},
        {"filename": "commercial_invoice.txt", "text": "COMMERCIAL INVOICE\nInvoice No: INV-001..."}
      ]
    },
    {
      "ref": "SHP-002",
      "documents": [
        {"filename": "bill_of_lading.txt", "text": "..."}
      ]
    }
  ]
}
```

---

## 4. Backend — API Endpoints

### 4.1 POST /api/v1/analyze/bulk

Create a new batch and begin background processing.

**Authentication:** Bearer JWT (same `get_current_organization` dependency as
`POST /api/v1/analyze`)

**Request — ZIP or CSV upload (multipart/form-data):**

```
POST /api/v1/analyze/bulk
Content-Type: multipart/form-data
Authorization: Bearer <token>

file:         <binary — .zip or .csv>
input_method: "ZIP" | "CSV"
```

**Request — Manual batch (application/json):**

```json
{
  "input_method": "MANUAL",
  "shipments": [ ... ]
}
```

**Response 202 Accepted:**

```json
{
  "batch_id": "a1b2c3d4-...",
  "total_shipments": 12,
  "status": "PROCESSING",
  "input_method": "ZIP",
  "created_at": "2026-04-19T14:30:00+00:00",
  "status_url": "/api/v1/analyze/bulk/a1b2c3d4-.../status",
  "results_url": "/api/v1/analyze/bulk/a1b2c3d4-.../results"
}
```

**Error responses:**

| Status | Code | Trigger |
|---|---|---|
| 400 | `EMPTY_BATCH` | No valid shipments parsed from input |
| 400 | `BATCH_TOO_LARGE` | More than 50 shipments |
| 400 | `INVALID_ZIP` | ZIP file is corrupt or password-protected |
| 400 | `INVALID_CSV` | CSV is malformed or missing `shipment_ref` column |
| 413 | `FILE_TOO_LARGE` | ZIP > 50 MB or CSV > 5 MB |
| 401 | `MISSING_TOKEN` | No auth header |
| 429 | `RATE_LIMITED` | Batch submission rate exceeded (3 batches/minute/org) |

---

### 4.2 GET /api/v1/analyze/bulk/{batch_id}/status

Poll endpoint for real-time progress. Frontend calls this every 2 seconds.

**Response 200:**

```json
{
  "batch_id": "a1b2c3d4-...",
  "status": "PROCESSING",
  "input_method": "ZIP",
  "total": 12,
  "processed": 7,
  "pending": 5,
  "decisions": {
    "APPROVE": 3,
    "REVIEW_RECOMMENDED": 1,
    "FLAG_FOR_INSPECTION": 2,
    "REQUEST_MORE_INFORMATION": 1,
    "REJECT": 0,
    "ERROR": 0
  },
  "started_at": "2026-04-19T14:30:01+00:00",
  "elapsed_seconds": 14.2,
  "estimated_remaining_seconds": 10.2
}
```

When `status == "COMPLETE"`:

```json
{
  "batch_id": "a1b2c3d4-...",
  "status": "COMPLETE",
  "total": 12,
  "processed": 12,
  "pending": 0,
  "decisions": { ... },
  "started_at": "...",
  "completed_at": "...",
  "elapsed_seconds": 24.6,
  "estimated_remaining_seconds": 0
}
```

**`estimated_remaining_seconds` calculation:**

```
avg_time_per_shipment = elapsed_seconds / processed
remaining = avg_time_per_shipment × pending
```

Returns 0 when `processed == 0` (no data to estimate from yet).

---

### 4.3 GET /api/v1/analyze/bulk/{batch_id}/results

Retrieve full results for a completed (or still-processing) batch. Returns all
shipments processed so far; if the batch is still running, partial results are
returned with `status: "PROCESSING"`.

**Response 200:**

```json
{
  "batch_id": "a1b2c3d4-...",
  "status": "COMPLETE",
  "input_method": "ZIP",
  "created_at": "...",
  "completed_at": "...",
  "summary": {
    "total": 12,
    "processed": 12,
    "approved": 5,
    "review_recommended": 2,
    "flagged": 3,
    "needs_info": 1,
    "errors": 1,
    "avg_risk_score": 0.4213,
    "highest_risk": {
      "ref": "SHP-007",
      "decision": "FLAG_FOR_INSPECTION",
      "risk_score": 0.91,
      "analysis_id": "uuid-..."
    },
    "processing_time_seconds": 24.6
  },
  "shipments": [
    {
      "ref": "SHP-001",
      "status": "COMPLETE",
      "decision": "APPROVE",
      "risk_score": 0.11,
      "risk_level": "LOW",
      "n_findings": 0,
      "top_finding": null,
      "analysis_id": "uuid-...",
      "processed_at": "..."
    },
    {
      "ref": "SHP-007",
      "status": "COMPLETE",
      "decision": "FLAG_FOR_INSPECTION",
      "risk_score": 0.91,
      "risk_level": "CRITICAL",
      "n_findings": 4,
      "top_finding": "Transshipment indicator: port of loading (Singapore) does not match declared origin (China).",
      "analysis_id": "uuid-...",
      "processed_at": "..."
    },
    {
      "ref": "SHP-003",
      "status": "ERROR",
      "decision": null,
      "risk_score": null,
      "risk_level": null,
      "n_findings": null,
      "top_finding": null,
      "analysis_id": null,
      "error_message": "DocumentValidationFailed: Document 1 (bill_of_lading.txt) — no trade signals detected.",
      "processed_at": "..."
    }
  ]
}
```

Shipments are returned sorted by `risk_score` descending (highest risk first) by
default. Query parameters `sort` (`risk_score`, `ref`, `processed_at`) and
`decision` (filter by decision type) are supported.

---

### 4.4 GET /api/v1/analyze/bulk/{batch_id}/export/csv

Stream a CSV summary of all completed shipments in the batch.

**Response headers:**

```
Content-Type: text/csv; charset=utf-8
Content-Disposition: attachment; filename="PortGuard_Batch_<batch_id[:8]>_<date>.csv"
```

**CSV columns:**

```
shipment_ref, decision, risk_score, risk_level, n_findings, top_finding,
analysis_id, processed_at, status, error_message
```

Implemented using `StreamingResponse` over a generator to avoid buffering all
results in memory before sending.

---

### 4.5 GET /api/v1/analyze/bulk/{batch_id}/export/zip

Stream a ZIP archive containing one PDF compliance report per completed shipment.

**Response headers:**

```
Content-Type: application/zip
Content-Disposition: attachment; filename="PortGuard_Reports_<batch_id[:8]>_<date>.zip"
```

**ZIP contents:**

```
PortGuard_Reports_a1b2c3d4_2026-04-19.zip
├── SHP-001_APPROVE_0.11.pdf
├── SHP-002_REVIEW_RECOMMENDED_0.34.pdf
├── SHP-007_FLAG_FOR_INSPECTION_0.91.pdf
└── ...
```

Filenames: `{ref}_{decision}_{risk_score:.2f}.pdf`

Implementation: iterate `bulk_shipments` rows for the batch; for each COMPLETE
row, call `generate_report_from_dict(json.loads(report_payload))` (same function
used by the single-shipment PDF endpoint). Write each PDF into a `zipfile.ZipFile`
backed by `io.BytesIO`, then stream the result. ERROR rows are skipped (no PDF
generated for failed shipments).

**Note:** This endpoint should only be called after `status == COMPLETE` or
explicitly when the officer knows processing is done. Calling during processing
returns a partial ZIP covering only completed shipments at that moment.

---

### 4.6 GET /api/v1/analyze/bulk/csv-template

Return a downloadable CSV template with correct column headers and one example
row with placeholder text. No auth required — this is a public static resource.

---

## 5. Background Processing Architecture

### 5.1 FastAPI BackgroundTasks

Bulk processing uses FastAPI's built-in `BackgroundTasks` mechanism, consistent
with how `_record_shipment_bg` is already scheduled in the existing analyze
endpoint. No additional infrastructure (Celery, Redis, task queues) is needed for
≤50 shipments.

```python
@app.post("/api/v1/analyze/bulk", status_code=202)
def create_bulk_batch(
    background_tasks: BackgroundTasks,
    current_org: dict = Depends(get_current_organization),
    ...
):
    batch_id = _create_batch_record(shipments, org_id, input_method)
    background_tasks.add_task(_process_bulk_batch, batch_id, shipments, org_id)
    return BulkBatchCreatedResponse(batch_id=batch_id, ...)
```

`BackgroundTasks.add_task()` runs the coroutine in the same event loop after the
response is sent, so the HTTP 202 is returned immediately without waiting for
any shipment to complete.

### 5.2 Concurrency Model

Since `_analyze_documents()` in `api/app.py` is a **synchronous** CPU-bound
function, it must not be called directly from an async context (it would block
the event loop). The processing loop runs each shipment in a thread pool via
`loop.run_in_executor()`:

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

_BULK_EXECUTOR = ThreadPoolExecutor(max_workers=5, thread_name_prefix="bulk")
_BULK_SEMAPHORE_SIZE = 5   # max concurrent shipment analyses

async def _process_bulk_batch(batch_id: str, shipments: list, org_id: str):
    loop = asyncio.get_event_loop()
    semaphore = asyncio.Semaphore(_BULK_SEMAPHORE_SIZE)

    async def _process_one(shipment: dict):
        async with semaphore:
            try:
                return await loop.run_in_executor(
                    _BULK_EXECUTOR,
                    _analyze_one_shipment,      # synchronous wrapper
                    batch_id, shipment, org_id,
                )
            except Exception as exc:
                _mark_shipment_error(batch_id, shipment["ref"], str(exc))

    _mark_batch_processing(batch_id)
    await asyncio.gather(*[_process_one(s) for s in shipments])
    _mark_batch_complete(batch_id)
```

**`_BULK_SEMAPHORE_SIZE = 5`** limits concurrent analyses to 5. This prevents
memory spikes from 50 simultaneous analyses while still providing meaningful
parallelism (5× speedup over sequential processing for a 50-shipment batch).

**`ThreadPoolExecutor(max_workers=5)`** is a module-level singleton so threads
are reused across batches, not recreated per shipment.

### 5.3 Per-Shipment Fault Isolation

Each shipment is processed inside its own `try/except` block. Any exception —
document validation failure, analysis error, DB write failure — catches at the
shipment level. The batch continues. The failed shipment is recorded with
`status=ERROR` and an `error_message` string. The exception is **not** re-raised
and does **not** affect other shipments.

```python
def _analyze_one_shipment(batch_id: str, shipment: dict, org_id: str):
    ref = shipment["ref"]
    _mark_shipment_processing(batch_id, ref)
    try:
        docs = [DocumentInput(**d) for d in shipment["documents"]]

        # Document validation gate (same as single-shipment endpoint)
        val_results = _validate_documents(docs)
        rejected = [r for r in val_results if not r.is_valid]
        if rejected:
            filenames = [d["filename"] for d in shipment["documents"]]
            rej_names = [filenames[i] for i, r in enumerate(val_results) if not r.is_valid]
            err = build_rejection_error(rejected, rej_names, len(docs))
            raise ValueError(f"DocumentValidationFailed: {err['message']}")

        # Core analysis (existing function, unchanged)
        result = _analyze_documents(docs)

        # Pattern learning overlay (existing logic, unchanged)
        sd = result.get("shipment_data", {})
        rule_score = result["risk_score"]
        rule_decision = result["decision"]
        # ... (identical to analyze() endpoint logic) ...

        # Store result in bulk_shipments
        _mark_shipment_complete(batch_id, ref, result, analysis_id)

    except Exception as exc:
        _mark_shipment_error(batch_id, ref, str(exc))
```

### 5.4 Per-Shipment Timeout

A 30-second per-shipment timeout prevents a single stalled document from hanging
the entire batch. Implemented by passing `timeout=30` to `run_in_executor` via a
wrapper:

```python
async def _process_one_with_timeout(shipment):
    async with semaphore:
        try:
            await asyncio.wait_for(
                loop.run_in_executor(_BULK_EXECUTOR, _analyze_one_shipment, ...),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            _mark_shipment_error(batch_id, shipment["ref"], "Analysis timed out after 30 seconds.")
```

---

## 6. Database Schema

Two new tables added to `portguard_patterns.db` (same database as pattern
learning). This keeps bulk batch data co-located with the analysis history it
references via `analysis_id` foreign keys.

### 6.1 `bulk_batches`

```sql
CREATE TABLE IF NOT EXISTS bulk_batches (
    batch_id            TEXT    PRIMARY KEY,
    organization_id     TEXT    NOT NULL,
    status              TEXT    NOT NULL DEFAULT 'PENDING',
    -- PENDING | PROCESSING | COMPLETE | FAILED
    input_method        TEXT    NOT NULL,
    -- ZIP | CSV | MANUAL
    total_shipments     INTEGER NOT NULL,
    processed_count     INTEGER NOT NULL DEFAULT 0,
    approved_count      INTEGER NOT NULL DEFAULT 0,
    review_count        INTEGER NOT NULL DEFAULT 0,
    flagged_count       INTEGER NOT NULL DEFAULT 0,
    needs_info_count    INTEGER NOT NULL DEFAULT 0,
    rejected_count      INTEGER NOT NULL DEFAULT 0,
    error_count         INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL,
    started_at          TEXT,
    completed_at        TEXT,
    error_message       TEXT
);

CREATE INDEX IF NOT EXISTS idx_bulk_batches_org_created
    ON bulk_batches(organization_id, created_at DESC);
```

### 6.2 `bulk_shipments`

```sql
CREATE TABLE IF NOT EXISTS bulk_shipments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        TEXT    NOT NULL REFERENCES bulk_batches(batch_id),
    shipment_ref    TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'PENDING',
    -- PENDING | PROCESSING | COMPLETE | ERROR
    decision        TEXT,
    risk_score      REAL,
    risk_level      TEXT,
    n_findings      INTEGER,
    top_finding     TEXT,
    analysis_id     TEXT,
    report_payload  TEXT,   -- full AnalyzeResponse JSON for PDF regeneration
    error_message   TEXT,
    processed_at    TEXT,
    UNIQUE(batch_id, shipment_ref)
);

CREATE INDEX IF NOT EXISTS idx_bulk_shipments_batch
    ON bulk_shipments(batch_id, status);

CREATE INDEX IF NOT EXISTS idx_bulk_shipments_analysis
    ON bulk_shipments(analysis_id);
```

**`report_payload` column:** Stores the full `AnalyzeResponse` JSON so that the
ZIP export endpoint can generate PDFs purely from the database without re-running
analysis. This mirrors the existing `report_payload` column in `shipment_history`
used by the single-shipment PDF download feature.

### 6.3 Schema Migration

The two tables are created by the existing `PatternDB._init_schema()` pattern —
new `CREATE TABLE IF NOT EXISTS` statements added to the schema init list. No
separate migration file is required since SQLite's `IF NOT EXISTS` makes the
statement idempotent on databases that already exist.

---

## 7. Progress Tracking

### 7.1 Database Counter Updates

Batch-level counters (`processed_count`, `approved_count`, etc.) are updated
atomically per-shipment using `UPDATE … SET processed_count = processed_count + 1`
so concurrent threads do not race. SQLite's WAL mode (already enabled by
`portguard/db.py`) serializes write transactions automatically.

```python
def _increment_batch_counters(batch_id: str, decision: str):
    col = {
        "APPROVE": "approved_count",
        "REVIEW_RECOMMENDED": "review_count",
        "FLAG_FOR_INSPECTION": "flagged_count",
        "REQUEST_MORE_INFORMATION": "needs_info_count",
        "REJECT": "rejected_count",
    }.get(decision, "error_count")
    with engine.begin() as conn:
        conn.execute(text(f"""
            UPDATE bulk_batches
            SET processed_count = processed_count + 1,
                {col} = {col} + 1
            WHERE batch_id = :batch_id
        """), {"batch_id": batch_id})
```

### 7.2 Frontend Polling

The results section becomes visible as soon as the batch is created (showing
the progress UI). The JS polling loop:

```javascript
let _bulkPollInterval = null;

function _startBulkPolling(batchId) {
  _bulkPollInterval = setInterval(async () => {
    const status = await _fetchBulkStatus(batchId);
    _updateProgressUI(status);
    if (status.status === 'COMPLETE' || status.status === 'FAILED') {
      clearInterval(_bulkPollInterval);
      _bulkPollInterval = null;
      if (status.status === 'COMPLETE') {
        const results = await _fetchBulkResults(batchId);
        _renderBulkResults(results);
      }
    }
  }, 2000);
}
```

The interval is always cleared in two cases: batch completes successfully, or
`status == 'FAILED'` (entire batch failed at the intake level, not per-shipment
errors). Per-shipment errors with `status == ERROR` still resolve to a
`COMPLETE` batch.

### 7.3 Progress Bar Calculation

```
progress_pct = (processed / total) × 100
```

Per-shipment status badges are rendered in a grid. Each badge starts as a gray
circle (PENDING), transitions to a spinning ring (PROCESSING), then becomes a
colored decision badge (APPROVE=green, FLAG=orange, REVIEW=yellow,
REQUEST_INFO=blue, REJECT=red, ERROR=dark red) when complete.

The badge grid uses the same CSS tokens and color system as the existing decision
displays in `demo.html`.

---

## 8. Results UI

### 8.1 Navigation

A new **"Bulk Screening"** button is added to the top navigation bar in
`demo.html`, alongside the existing "Single Screening" and "Analytics" tabs.
The bulk section is a full-height view that replaces the single-screening upload
panel.

### 8.2 Input Mode Selector

Three tabs at the top of the bulk section:

```
[ ZIP Upload ] [ CSV Upload ] [ Manual Entry ]
```

The active tab shows its respective input panel. Only one method is active at a
time.

**ZIP/CSV tab:** Drag-and-drop zone with file browse fallback. Shows file name
and parsed shipment count after selection. "Preview" button shows the first 3
parsed shipment references. "Start Screening" button submits.

**Manual Entry tab:**

- "Add Shipment" button (max 50 slots)
- Each slot is a collapsible card with:
  - Shipment Reference ID text input (auto-filled as SHP-001, SHP-002 … but editable)
  - Horizontal tab bar: Bill of Lading | Commercial Invoice | Packing List | COO | Other
  - Textarea per tab (same as existing single-shipment UI)
  - "Remove" button (×) in the card header
- Slots are collapsed by default after the user moves to the next one
- Slot count badge: "3 / 50 shipments"

### 8.3 Progress View

Shown immediately after submission, replacing the input panel:

```
┌─────────────────────────────────────────────────────────┐
│  Screening Batch · a1b2c3d4                             │
│  12 shipments · ZIP Upload                              │
│                                                         │
│  ████████████░░░░░░  7 / 12 processed                  │
│  ~10 seconds remaining                                  │
│                                                         │
│  ● ● ● ● ● ● ◌ ◌ ◌ ◌ ◌ ◌   (status badges)          │
│                                                         │
│  SHP-001  APPROVE       SHP-002  FLAG_FOR_INSPECTION    │
│  SHP-003  ⟳ processing  SHP-004  ⟳ processing          │
│  ...                                                    │
└─────────────────────────────────────────────────────────┘
```

### 8.4 Summary Dashboard

Shown above the results table after `status == COMPLETE`:

```
┌───────────┬───────────┬───────────┬───────────┬───────────┐
│  12 Total │  5 Approve│  2 Review │  3 Flagged│  1 Error  │
└───────────┴───────────┴───────────┴───────────┴───────────┘

  Avg Risk Score: 0.42   Highest Risk: SHP-007 (0.91)
  Processing time: 24.6s
```

Clicking "SHP-007 (0.91)" scrolls the results table to that row and expands it.

### 8.5 Results Table

One row per shipment, sorted by `risk_score` descending by default.

| Column | Notes |
|---|---|
| Shipment Ref | Sortable |
| Decision | Color-coded badge (same palette as single-shipment result) |
| Risk Score | Numeric + risk bar (same mini-bar as existing UI) |
| Findings | Count of compliance findings |
| Top Finding | Truncated text of the most critical finding |
| Report | PDF download icon button (calls `/api/v1/report/generate-direct` with stored payload) |

**Sort controls:** Click any column header to sort. Arrow indicates direction.

**Filter bar:**
```
[All] [Approve] [Review] [Flagged] [More Info] [Error]
```
Clicking a filter hides all rows not matching that decision. Error badge
shows count when errors exist.

**Expand row:** Clicking a row (anywhere except the PDF button) toggles an
expanded accordion below the row. The accordion contains the full analysis
output in the same layout as the single-shipment results panel (risk factors,
missing fields, compliance findings, pattern learning notes). Implemented by
storing `report_payload` JSON per shipment in `bulk_shipments` and rendering
it with the same JS renderer used for single-shipment results.

**Error rows:** Shown with a red `ERROR` badge. Expanding shows the error
message. No PDF button (greyed out).

### 8.6 Export Controls

Above the results table:

```
[ ↓ Download CSV Summary ]  [ ↓ Download All PDFs (ZIP) ]
```

Both buttons are enabled once `status == COMPLETE`.

**CSV download:** Calls `GET /api/v1/analyze/bulk/{batch_id}/export/csv`. The
browser receives a streaming CSV response and triggers the native file-save
dialog.

**ZIP download:** Calls `GET /api/v1/analyze/bulk/{batch_id}/export/zip`.
For large batches with many PDFs, the button shows a spinner while the server
builds the ZIP (this is a synchronous endpoint — the PDF generation happens
on the request, not in a background task).

---

## 9. Security & Access Control

### 9.1 Tenant Isolation

Every `bulk_batches` and `bulk_shipments` row stores `organization_id`. Every
query to these tables includes `WHERE organization_id = :org_id`. An org can
never read or export another org's batch results.

### 9.2 Batch Ownership Check

All batch endpoints check that the `batch_id` belongs to the requesting org
before returning data:

```python
def _get_batch_or_404(batch_id: str, org_id: str):
    row = _query_batch(batch_id)
    if row is None or row["organization_id"] != org_id:
        raise HTTPException(404, {"code": "BATCH_NOT_FOUND", ...})
    return row
```

Returning 404 (rather than 403) on ownership mismatch avoids leaking whether
a batch ID exists.

### 9.3 Rate Limiting

A soft rate limit of **3 batch submissions per minute per org** is enforced
at the POST endpoint. This mirrors the existing per-IP login rate limit in
`api/auth_routes.py`. Implementation: count rows in `bulk_batches` for the
requesting org with `created_at >= now - 60s`. If count >= 3, return HTTP 429.

### 9.4 Input Validation

- ZIP extraction happens in memory only (`io.BytesIO`); no files are written
  to disk at any point
- Max file sizes are checked **before** attempting to read or extract content
- Shipment reference IDs are sanitized: only alphanumeric, hyphens, underscores,
  periods, and spaces are permitted. All other characters are stripped.
- `report_payload` JSON is stored verbatim from the analysis output — it is
  never eval'd or executed, only serialized/deserialized with `json.dumps` /
  `json.loads`

---

## 10. Combined PDF Report

`GET /api/v1/analyze/bulk/{batch_id}/export/combined-pdf`

Generates a single multi-section PDF covering all shipments in the batch.
Structure:

1. **Cover page** — batch ID, org name, date, summary stats (total screened,
   decision breakdown, avg risk score)
2. **Batch summary table** — one row per shipment (ref, decision, risk score)
3. **Per-shipment sections** — same layout as individual PDF reports, separated
   by section dividers, sorted highest risk first

Implementation uses `fpdf2`'s `import_page()` to concatenate individual shipment
PDFs, preceded by a programmatically generated cover page and summary table.
The existing `ReportGenerator` class in `portguard/report_generator.py` is
extended with a `generate_batch_report(batch_summary, shipment_payloads)` class
method that handles this layout.

The combined PDF endpoint is an alternative to the ZIP download, useful when
the officer needs to submit a single PDF file to a regulatory system or archive.

---

## 11. Implementation Phases

### Phase 1 — Backend Core (implement first)

1. Add `bulk_batches` and `bulk_shipments` tables to `PatternDB._init_schema()`
2. Implement `_parse_zip_input()`, `_parse_csv_input()` parser functions in `api/app.py`
3. Implement `_analyze_one_shipment()` wrapper around existing `_analyze_documents()`
4. Implement `_process_bulk_batch()` async background task with semaphore
5. Implement DB helper functions: `_create_batch_record()`, `_mark_batch_processing()`,
   `_mark_shipment_complete()`, `_mark_shipment_error()`, `_increment_batch_counters()`,
   `_mark_batch_complete()`
6. Wire up `POST /api/v1/analyze/bulk` endpoint
7. Wire up `GET /api/v1/analyze/bulk/{batch_id}/status` endpoint
8. Wire up `GET /api/v1/analyze/bulk/{batch_id}/results` endpoint
9. Test all three input methods against the existing test scenarios

### Phase 2 — Export Endpoints

10. Implement `GET /api/v1/analyze/bulk/{batch_id}/export/csv`
11. Implement `GET /api/v1/analyze/bulk/{batch_id}/export/zip`
12. Implement `GET /api/v1/analyze/bulk/csv-template`
13. Extend `ReportGenerator` with `generate_batch_report()` for combined PDF
14. Implement `GET /api/v1/analyze/bulk/{batch_id}/export/combined-pdf`

### Phase 3 — Frontend

15. Add Bulk Screening nav tab and routing to `demo.html`
16. Build input mode selector (ZIP / CSV / Manual) with three panels
17. Implement Manual Entry slot system (add/remove/collapse cards)
18. Implement drag-and-drop ZIP/CSV upload zone
19. Implement progress view with polling loop and badge grid
20. Implement summary dashboard cards
21. Implement sortable/filterable results table with expandable rows
22. Wire up CSV and ZIP export buttons
23. End-to-end test all three input methods in the browser

---

## 12. Files to Create / Modify

| File | Change |
|---|---|
| `api/app.py` | Add 5 new endpoints, `_parse_zip_input()`, `_parse_csv_input()`, `_analyze_one_shipment()`, `_process_bulk_batch()`, DB helpers |
| `portguard/pattern_db.py` | Add `bulk_batches` and `bulk_shipments` to `_init_schema()`; add `create_bulk_batch()`, `update_bulk_shipment()`, `get_bulk_status()`, `get_bulk_results()` methods |
| `portguard/report_generator.py` | Add `generate_batch_report(batch_summary, payloads)` class method |
| `demo.html` | Add Bulk Screening section, input panels, progress view, results table, export buttons |

No new files need to be created. All additions slot into existing modules using
the same patterns already established in the codebase.

---

## 13. Key Design Constraints

- **No new dependencies.** ZIP: stdlib `zipfile`. CSV: stdlib `csv`. Async:
  stdlib `asyncio` + `concurrent.futures`. All already available.
- **No new infrastructure.** No Celery, no Redis, no message queue. `BackgroundTasks`
  is sufficient for ≤50 shipments processed at 5 concurrently.
- **The existing pipeline is not modified.** `_analyze_documents()`, `_make_decision()`,
  `_assess_risk()`, and all rule logic run identically for bulk and single-shipment.
  Bulk is strictly a wrapper.
- **Pattern learning applies to bulk shipments.** Each shipment in a batch goes
  through the full pattern overlay and is recorded in `shipment_history`. This
  means bulk screening contributes to the Bayesian reputation profiles, consistent
  with manual screening.
- **Partial results are always accessible.** The status and results endpoints
  return data for completed shipments even while the batch is still running.
  An officer can start reviewing completed analyses before the full batch finishes.
- **Batch results are persistent.** `bulk_batches` and `bulk_shipments` rows
  are not deleted after export. Officers can re-download exports or revisit
  results for any previous batch as long as the database is not reset.
