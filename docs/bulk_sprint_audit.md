# PortGuard Bulk Upload Sprint Audit
**Date:** 2026-04-28  
**Author:** Claude (audit only — no code written or modified)  
**Scope:** Every file in the project was read before producing this document.  
**Purpose:** Authoritative current-state summary prior to sprint planning.

---

## 1. BULK UPLOAD — Current State

### 1.1 Endpoint location and entry points

**Backend endpoint:** `POST /api/v1/analyze/bulk` — defined at `api/app.py:3093`.

The handler `bulk_create(request, background_tasks, current_org)` dispatches via two code paths:

- **Multipart/form-data** (`Content-Type: multipart/form-data`): reads either the `zip_file` field (ZIP) or `csv_file` field (CSV), plus a required `input_method` field (`"ZIP"` or `"CSV"`). Parsing done by `portguard/bulk_parsers.py`.
- **JSON body** (`Content-Type: application/json`): reads `{input_method: "MANUAL", shipments: [...]}`. Validated by `validate_manual_input()` in `portguard/bulk_parsers.py`.

All paths return HTTP **202** immediately with:
```json
{
  "batch_id": "<uuid>",
  "status": "QUEUED",
  "total_shipments": <n>,
  "status_url": "/api/v1/analyze/bulk/<batch_id>/status",
  "results_url": "/api/v1/analyze/bulk/<batch_id>/results"
}
```

The actual analysis is dispatched as a FastAPI `BackgroundTask` → `BulkProcessor.process_batch(batch_id, org_id)`.

---

### 1.2 Execution path (full trace)

```
POST /api/v1/analyze/bulk
  └─ bulk_create()                                     api/app.py:3093
       ├─ parse ZIP/CSV/manual via bulk_parsers.py
       ├─ INSERT INTO bulk_batches (status=QUEUED)
       └─ background_tasks.add_task(processor.process_batch, batch_id, org_id)

BulkProcessor.process_batch(batch_id, org_id)           portguard/bulk_processor.py
  ├─ UPDATE bulk_batches SET status=PROCESSING
  ├─ asyncio.Semaphore(5)  — max 5 concurrent shipments
  ├─ ThreadPoolExecutor(max_workers=5)
  └─ per shipment:
       ├─ asyncio.wait_for(run_in_executor(fn, docs, org_id), timeout=30.0)
       ├─ fn = _run_bulk_single_analysis(documents_data, org_id)    api/app.py:2970
       │    ├─ _validate_documents(docs)
       │    ├─ _analyze_documents(docs)            — 5-stage rule pipeline
       │    ├─ PatternEngine.score()               — blended scoring if history available
       │    ├─ CertificationScreener.screen()      — stage 3.5 (lines 3092–3100)
       │    ├─ SustainabilityRater.rate()          — stage 5.5 (lines 3105–3109)
       │    ├─ AnalyzeResponse construction        — line 3111
       │    ├─ _record_shipment_bg()               — writes to shipment_history
       │    └─ returns result dict
       └─ _store_shipment_result()                 — writes to bulk_shipments
            ├─ UPDATE bulk_shipments SET status=COMPLETE, decision, risk_score, ...
            ├─ sustainability_grade (extracted from result_json)
            ├─ sustainability_signals (pipe-separated, from result_json)
            └─ active_modules_snapshot (pipe-separated, from result_json)
```

---

### 1.3 FormData keys (ZIP and CSV)

**Current state: CORRECT.** The prior audit (`docs/bulk_upload_audit.md`) documented BUG-1 and BUG-2 (wrong FormData key names). Those bugs have been **fixed** in the current `demo.html`. The correct code at `demo.html:9382-9391`:

```javascript
// ZIP
fd.append('zip_file', _bulkZipFile);
fd.append('input_method', 'ZIP');

// CSV
fd.append('csv_file', _bulkCsvFile);
fd.append('input_method', 'CSV');
```

Backend reads: `request.form.get('input_method')`, then `await request.form()` for the file under `zip_file` or `csv_file`. Keys match. **No action needed.**

---

### 1.4 Concurrency and parallelism

- `asyncio.Semaphore(5)` limits concurrent shipments per batch to 5.
- `ThreadPoolExecutor(max_workers=5)` handles CPU-bound analysis in worker threads.
- 30-second per-shipment timeout enforced via `asyncio.wait_for`.
- No cross-batch concurrency limit: multiple simultaneous batches from different orgs each get their own semaphore/executor, which could saturate the 5-worker pool.
- The executor is created once in `BulkProcessor.__init__` and shared for the lifetime of the process.

---

### 1.5 50-row maximum behavior

`MAX_BATCH_SIZE = 50` in `portguard/bulk_parsers.py`.

| Input method | Enforcement point | Error raised |
|---|---|---|
| ZIP | After full parse, before returning | `BatchTooLargeError` → HTTP 400 |
| CSV | Break at row 50, remaining ignored | Truncation, no error |
| Manual | At `validate_manual_input()` entry | `BatchTooLargeError` → HTTP 400 |

ZIP and manual raise hard errors; CSV silently truncates at 50. This inconsistency is documented under INCON-1 in Section 4.

---

### 1.6 Why shipment rows stay PENDING

Background processing is asynchronous. Shipments are inserted into `bulk_shipments` with `status=PENDING` when the batch is created. The async worker then processes them one-by-one (up to 5 at a time), transitioning each through `PROCESSING` → `COMPLETE` or `ERROR`.

Rows remain `PENDING` until a worker slot is free. For a 50-shipment batch with 5 concurrent workers, slots 6-50 queue behind the first 5, each waiting for a preceding shipment to complete or timeout (max 30s per slot).

---

### 1.7 Polling behavior (frontend)

`_bulkStartPolling(total)` is called immediately after the HTTP 202 response (`demo.html:9426`).

```
_bulkStartPolling(total)
  └─ setInterval(_bulkPoll, 2000)        — polls every 2 seconds

_bulkPoll()
  ├─ GET /api/v1/analyze/bulk/{batch_id}/status
  ├─ updates live feed (new COMPLETE shipments appended to list)
  ├─ updates progress bar: processed / total
  ├─ if status === 'COMPLETE':
  │    clearInterval, setTimeout(_bulkLoadResults, 400)
  └─ if status === 'FAILED':
       clearInterval, _bulkShowProgressError(...)
```

The 400ms delay before calling `_bulkLoadResults()` gives the browser time to finish rendering the last live-feed entry.

---

### 1.8 Share link generation

`bulkShareLink()` at `demo.html:9817`:

```javascript
const url = window.location.href.split('?')[0] + '?batch=' + (_bulkBatchId || '');
navigator.clipboard.writeText(url);
```

The URL is generated and copied to clipboard. **However, no page-load handler reads the `?batch=` parameter.** There is no `DOMContentLoaded` listener, no `URLSearchParams` check, and no call to restore a batch from URL state. Sharing this link opens the page in its default blank state — the batch is not restored. This is BUG-2 in Section 4.

---

### 1.9 Results stats — critical data shape bug

`_bulkLoadResults()` (`demo.html:9529`) fetches `/results` and reads:

```javascript
const decisions = data.decisions || {};          // BUG: undefined
data.total_shipments || _bulkAllResults.length;  // BUG: undefined
data.average_risk_score                          // BUG: undefined  
data.highest_risk                               // BUG: undefined
```

The results endpoint (`GET /api/v1/analyze/bulk/{batch_id}/results`) returns from `get_batch_results()` in `portguard/bulk_processor.py:580`:

```json
{
  "batch_id": "...",
  "status": "COMPLETE",
  "summary": {
    "total": 10,
    "processed": 10,
    "approved": 7,
    "review_recommended": 1,
    "flagged": 1,
    "needs_info": 0,
    "rejected": 0,
    "errors": 1,
    "avg_risk_score": 0.32,
    "highest_risk": { "ref": "SHP-003", "risk_score": 0.87 },
    "processing_time_seconds": 42.5
  },
  "shipments": [...]
}
```

`data.decisions` does not exist in this response — it only exists in the **status** endpoint response.

**Effect on every results stat badge:**

| Badge | Frontend reads | Actual key | Result |
|---|---|---|---|
| Total | `data.total_shipments` | `data.summary.total` | Uses fallback `_bulkAllResults.length` (correct by accident) |
| Approved | `decisions.APPROVE` | `data.summary.approved` | Always 0 |
| Flagged | `decisions.FLAG_FOR_INSPECTION + REJECT` | `data.summary.flagged + rejected` | Always 0 |
| Review | `decisions.REVIEW_RECOMMENDED` | `data.summary.review_recommended` | Always 0 |
| Info | `decisions.REQUEST_MORE_INFORMATION` | `data.summary.needs_info` | Always 0 |
| Errors | `decisions.ERROR` | `data.summary.errors` | Always 0 |
| Avg risk | `data.average_risk_score` | `data.summary.avg_risk_score` | Always shows `—` |
| Highest risk bar | `data.highest_risk` | `data.summary.highest_risk` | Never shown |

This is **BUG-1** in Section 4 — the most impactful UI bug in the current codebase.

---

## 2. PDF PREVIEW IN MANUAL ENTRY ROWS — Current State

### 2.1 HTML structure

Each manual entry slot renders a PDF upload trigger via `bulkSlotPdfChosen()`. The full template is at `demo.html:9006-9095`. Structure when a PDF is loaded:

```
<div class="bulk-slot has-pdf">
  <input type="file" accept="application/pdf" onchange="bulkSlotPdfChosen(sid,tid,this)">  <!-- hidden -->
  <button class="bulk-slot-pdf-btn">Upload PDF</button>

  <div class="bulk-slt-pdf-panel" id="bulk-slt-pdf-panel-{sid}-{tid}">
    <div class="bulk-slt-pdf-header">
      <span class="bulk-slt-pdf-filename">{filename}</span>
      <span class="bulk-slt-pdf-meta">{pageCount} p · {sizeKb} KB</span>
      <span class="pdf-extract-badge {state}">...</span>
    </div>

    <div class="pdf-thumb-strip bulk-slt-pdf-thumbs" id="bulk-slt-pdf-thumbs-{sid}-{tid}">
      <!-- Up to 3 thumb wraps -->
      <div class="pdf-thumb-wrap" id="bulk-pdf-thumb-wrap-{sid}-{tid}-{p}">
        <canvas class="pdf-thumb-canvas" id="bulk-pdf-canvas-{sid}-{tid}-{p}"></canvas>
        <div class="pdf-scan-beam"></div>  <!-- animated during extraction -->
      </div>
    </div>

    <button id="bulk-slt-pdf-toggle-{sid}-{tid}" onclick="bulkSlotToggleEditText(...)">
    <div class="pdf-text-collapse" id="bulk-slt-pdf-collapse-{sid}-{tid}">
      <textarea id="bulk-slt-text-{sid}-{tid}">{extracted text}</textarea>
    </div>
  </div>

  <!-- Override warning — shown only on conflict: -->
  <div class="bulk-slt-pdf-override-warn">
    PDF text will be used on submit. Edit below to override.
    <button onclick="bulkSlotUsePdfText(...)">Use PDF text</button>
  </div>
</div>
```

---

### 2.2 JavaScript functions (all fully implemented)

All PDF-related JS functions are present and complete in `demo.html`.

| Function | Location | Purpose |
|---|---|---|
| `bulkSlotPdfChosen(slotId, tabId, inputEl)` | 9102 | Entry point; resets input, calls `_bulkPdfHandleUpload` |
| `_bulkPdfHandleUpload(slotId, tabId, file)` | 9109 | Async; loads PDF.js doc, renders thumbs, extracts text, handles conflict |
| `_bulkPdfRenderThumb(pdfDoc, pageNum, slotId, tabId)` | 9213 | Renders one page to canvas at scale 0.22; returns dataUrl |
| `_bulkPdfExtractText(pdfDoc, uploadGen, tab)` | 9230 | Extracts all pages' text via `page.getTextContent()` |
| `_bulkPdfSetScanDone(slotId, tabId, state, badgeText)` | 9242 | Adds `scan-done` class to all thumbs; updates badge state |
| `bulkSlotToggleEditText(slotId, tabId)` | 9254 | Toggles the collapsed text editor panel |

**Stale-upload guard:** `tab._pdfUploadGen` increments on every upload; all async steps bail if the generation counter changes. Prevents race conditions when the user rapidly re-uploads.

**Conflict resolution path:**
- If PDF text ≠ existing `tab.text` (non-empty): `tab.pdfExtractedText = pdf_text`; `tab.text` preserved; override warning shown.
- If no existing text: `tab.text = pdf_text`; `tab.pdfExtractedText = ''`; textarea updated in-place.
- On submit (`demo.html:9402`): `raw_text: (t.pdfExtractedText || t.text || '').trim()` — PDF text takes priority.
- **Gap:** When a conflict is present, the textarea still displays `tab.text` (the manual text), but the submission uses `tab.pdfExtractedText` (the PDF text). Edits made to the textarea after the conflict warning are silently discarded on submit — only the PDF text is sent. Documented as GAP-1 in Section 4.

---

### 2.3 Container CSS (bulk slot thumbnails)

At `demo.html:2101-2153`:

```css
/* The upload trigger button */
.bulk-slot-pdf-btn {
  font-size: .7rem;
  padding: .25rem .55rem;
  border: 1px solid var(--teal);
  border-radius: 4px;
  color: var(--teal);
  background: transparent;
  cursor: pointer;
}
.bulk-slot-pdf-btn:hover { background: rgba(27,154,170,.1); }
.bulk-slot.has-pdf { border-color: rgba(27,154,170,.45); }

/* The panel that appears when a PDF is loaded */
.bulk-slt-pdf-panel {
  margin: .4rem 0 .35rem;
  padding: .45rem .6rem;
  background: rgba(255,255,255,.02);
  border: 1px solid var(--border);
  border-radius: 6px;
}

.bulk-slt-pdf-header {
  display: flex;
  align-items: center;
  gap: .4rem;
  margin-bottom: .35rem;
}

.bulk-slt-pdf-filename { font-size: .72rem; font-weight: 500; }
.bulk-slt-pdf-meta { font-size: .7rem; color: var(--faint); }

/* Thumbnail strip — overrides shared .pdf-thumb-strip gap */
.bulk-slt-pdf-thumbs { gap: .35rem; }

/* Critical: forces 48px width on each thumb wrap in bulk slots */
.bulk-slt-pdf-thumbs .pdf-thumb-wrap { width: 48px; }

/* Override warning banner */
.bulk-slt-pdf-override-warn {
  font-size: .68rem;
  color: var(--amber);
  background: rgba(255,180,0,.06);
  border: 1px solid rgba(255,180,0,.25);
  border-radius: 4px;
  padding: .25rem .45rem;
  margin: .2rem 0;
  display: flex;
  align-items: center;
  gap: .5rem;
}
```

---

### 2.4 Shared `.pdf-thumb-wrap` CSS (applies to both single-doc and bulk)

At `demo.html:1570-1577`:

```css
.pdf-thumb-wrap {
  position: relative;      /* scan beam uses absolute positioning relative to this */
  border-radius: 5px;
  overflow: hidden;         /* clips the scan beam and canvas */
  border: 1px solid var(--border);
  flex-shrink: 0;
  background: var(--bg-deep);
}
```

The `overflow: hidden` is load-bearing for the scan beam animation — it clips the beam as it travels downward.

---

### 2.5 Scan beam CSS (shared between single-doc and bulk)

At `demo.html:1594-1653`:

```css
.pdf-scan-beam {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: linear-gradient(90deg,
    transparent 0%,
    rgba(27,154,170,.6) 30%,
    rgba(27,154,170,1) 50%,
    rgba(27,154,170,.6) 70%,
    transparent 100%
  );
  animation: pdf-beam-sweep 1.9s cubic-bezier(.4,0,.6,1) infinite;
  pointer-events: none;
  z-index: 2;
}

@keyframes pdf-beam-sweep {
  from { transform: translateY(0); }
  to   { transform: translateY(calc(var(--thumb-h, 200px) + 6px)); }
}

/* Beam hidden once scan is done */
.pdf-thumb-wrap.scan-done .pdf-scan-beam { display: none; }
```

**`--thumb-h` variable analysis:**

- Default: `200px`
- Set by: nothing for bulk slot thumbs — the variable is never explicitly set for `.bulk-slt-pdf-thumbs`
- Typical rendered canvas height: at `scale: 0.22` on a standard letter page (612×792pt PDF units), the canvas renders to approximately 135×174px. With `.pdf-thumb-wrap { width: 48px }` constraining display width via CSS, the visible height is roughly proportional — but the canvas pixel height is still ~174px.
- The 200px default overshoots by ~26px beyond the visible canvas area.
- `overflow: hidden` on `.pdf-thumb-wrap` clips the overshoot, so the visual animation works correctly.
- The beam animation cycles through 206px travel when only ~174px are visible — the beam disappears and reappears from the top cleanly due to clipping.

---

### 2.6 Comparison with single-document PDF preview

| Aspect | Single-doc (`/analyze` tab) | Bulk manual entry |
|---|---|---|
| Panel class | `.pdf-preview-panel` | `.bulk-slt-pdf-panel` |
| Thumbnail strip class | `.pdf-thumb-strip` | `.pdf-thumb-strip bulk-slt-pdf-thumbs` |
| Thumb wrap width | None set (natural canvas width, max 120px canvas) | `48px` via `.bulk-slt-pdf-thumbs .pdf-thumb-wrap` |
| Render scale | `0.25` (single-doc) | `0.22` (bulk) |
| Max thumbnails | 3 | 3 |
| `--thumb-h` variable | Not set (200px default) | Not set (200px default) |
| Scan beam CSS | `.pdf-scan-beam` (shared) | `.pdf-scan-beam` (shared) |
| Text extraction | `page.getTextContent()` | `page.getTextContent()` (same) |
| Conflict handling | N/A (single tab, no pre-existing text) | Override warning + `pdfExtractedText` field |
| PDF.js guard | `typeof pdfjsLib === 'undefined'` | Same guard |

---

## 3. CERTIFICATION MODULES IN BULK — Current State

### 3.1 `enabled_modules` loading in `_run_bulk_single_analysis`

`_run_bulk_single_analysis()` at `api/app.py:2970` loads enabled modules on every shipment:

```python
# api/app.py:3092-3100
enabled_modules_bulk: list[str] = (
    _module_config_db.get_enabled_modules(org_id)
    if _module_config_db is not None else []
)
screener_bulk = CertificationScreener(enabled_modules_bulk)
cert_result_bulk = screener_bulk.screen(sd, all_text_bulk)
```

This is correct: every shipment in the batch is screened against the org's active modules. Modules are loaded fresh per shipment from `organization_modules` table in `portguard_auth.db`.

**Performance note:** `CertificationScreener(enabled_modules_bulk)` pre-compiles regex patterns in `__init__`. For a 50-shipment batch, this instantiates the screener 50 times. The enabled modules are identical for all shipments in the same org batch. This is wasteful — see GAP-3 in Section 4.

---

### 3.2 Sustainability rating in bulk results

Fully implemented:

```python
# api/app.py:3103-3109
rater_bulk = SustainabilityRater()
sustainability_bulk = rater_bulk.rate(sd, cert_result_bulk, all_text_bulk)
```

Result is passed into `AnalyzeResponse(sustainability_rating=sustainability_bulk, ...)`.

`_store_shipment_result()` in `portguard/bulk_processor.py` extracts from the result dict:
- `sustainability_grade` → dedicated `bulk_shipments.sustainability_grade TEXT` column
- `sustainability_signals` → `bulk_shipments.sustainability_signals TEXT` (pipe-separated)
- `active_modules_snapshot` → `bulk_shipments.active_modules_snapshot TEXT` (pipe-separated)

These columns were added in migration 008. The full result JSON is also stored in `bulk_shipments.result_json`.

Frontend reads sustainability from the fast path (dedicated DB columns) with a fallback to the full result JSON:

```javascript
// demo.html:9549-9561
sustainability_grade:
  s.sustainability_grade
  || (s.full_result?.sustainability_rating?.grade)
  || null,
sustainability_signals:
  s.sustainability_signals
    ? s.sustainability_signals.split('|').filter(Boolean)
    : ((s.full_result?.sustainability_rating?.signals) || []),
```

---

### 3.3 Module findings in results

`_run_bulk_single_analysis()` populates the full AnalyzeResponse with:

```python
module_findings=cert_result_bulk.findings if cert_result_bulk else [],
active_modules_at_scan=cert_result_bulk.modules_run if cert_result_bulk else [],
modules_triggered=cert_result_bulk.triggered_modules if cert_result_bulk else [],
```

These fields are stored in `bulk_shipments.result_json`. The frontend reads them via:

```javascript
module_findings: (s.full_result && s.full_result.module_findings) || [],
active_modules:
  s.active_modules_snapshot
    ? s.active_modules_snapshot.split('|').filter(Boolean)
    : ((s.full_result && s.full_result.active_modules_at_scan) || []),
```

The expand row in `_bulkRenderTable()` renders `modulesHtml` with each module finding's name, status, and evidence — fully implemented.

CSV export (`bulk_export_csv`) includes `sustainability_grade`, `sustainability_signals`, and `active_modules_snapshot` columns in `_CSV_HEADERS` at `api/app.py:3786-3789`.

---

### 3.4 Module toggles — per-row availability

There are **no per-row module toggles** in the bulk results UI. Modules are an org-level setting. The bulk results table shows each shipment's `active_modules` from the snapshot captured at scan time — if a user changes their module config after submitting a batch, existing batch results retain their original module snapshot. New batches will use the updated module set. This behavior is correct and consistent with the single-shipment analyzer.

---

### 3.5 Previously documented gaps — resolution status

| Gap | Prior Audit Document | Current State |
|---|---|---|
| GAP-1 (cert screening not in bulk) | Listed in `bulk_upload_audit.md` as open | **RESOLVED** — CertificationScreener runs at `api/app.py:3092` |
| GAP-2 (sustainability not in bulk) | Listed as open | **RESOLVED** — SustainabilityRater runs at `api/app.py:3105` |
| GAP-3 (no sustainability DB columns) | Listed as open | **RESOLVED** — migration 008 added columns; `_store_shipment_result()` writes them |
| GAP-4 (no CSV export columns) | Listed as open | **RESOLVED** — `_CSV_HEADERS` includes all three sustainability columns |
| BUG-1 (wrong FormData key `file` instead of `zip_file`) | Listed as open | **RESOLVED** — demo.html sends `zip_file` |
| BUG-2 (wrong FormData key for CSV) | Listed as open | **RESOLVED** — demo.html sends `csv_file` |

**The existing `docs/bulk_upload_audit.md` is now substantially stale** and should be considered superseded by this document.

---

## 4. Exhaustive Bug, Gap, and Inconsistency List

---

### BUGS (incorrect behavior in shipped code)

---

**BUG-1: Stats panel shows all zeros — results data shape mismatch**  
File: `demo.html:9565-9589`  
Severity: **High** — every summary stat badge is broken

`_bulkLoadResults()` reads from the wrong keys when populating the 8 stat badges. The results endpoint (`/results`) and the status endpoint (`/status`) have different response shapes, and the frontend uses status-shaped keys on the results response.

Reads today:
```javascript
const decisions = data.decisions || {};              // undefined — only exists in /status
data.total_shipments                                 // undefined — only exists in create response
data.average_risk_score                              // undefined — only in /status (as data.decisions)
data.highest_risk                                    // undefined — only at data.summary.highest_risk
```

Correct keys for the results response:
```javascript
const s = data.summary || {};
s.total           // → bstat-total
s.approved        // → bstat-approved  (was decisions.APPROVE)
s.flagged + s.rejected  // → bstat-flagged
s.review_recommended  // → bstat-review
s.needs_info      // → bstat-info
s.errors          // → bstat-errors
s.avg_risk_score  // → bstat-avg-risk  (was data.average_risk_score)
s.highest_risk    // → bulk-highest-risk-bar (was data.highest_risk)
```

---

**BUG-2: Share link generates a dead URL**  
File: `demo.html:9817-9830`  
Severity: **Medium** — feature is completely non-functional

`bulkShareLink()` appends `?batch=<id>` to the URL and copies it to clipboard. No code on page load reads this parameter. Opening a share link loads the page in blank initial state — the batch is not restored, not even after login.

Fix requires: a `DOMContentLoaded` handler that checks `URLSearchParams` for `?batch=`, authenticates, then calls `_bulkLoadResults()` with the batch ID.

---

**BUG-3: PDF conflict text silently discards textarea edits**  
File: `demo.html:9194-9207`, `9400-9403`  
Severity: **Medium** — data loss risk

When a PDF is uploaded into a slot that already has manual text:
- `tab.pdfExtractedText` = PDF text
- `tab.text` = original manual text (preserved and displayed in textarea)
- Override warning shown

On submit: `raw_text: (t.pdfExtractedText || t.text || '').trim()` — PDF text is submitted.

If the user sees the warning and then edits the textarea (editing `tab.text`), their edits are silently discarded — the PDF text still takes priority because `tab.pdfExtractedText` is non-empty. The UI gives no indication that textarea edits won't be submitted.

---

### GAPS (missing functionality or incomplete implementations)

---

**GAP-1: No rate limiting on bulk create endpoint**  
File: `api/app.py:3093`  
The single-analyze endpoint and auth endpoints have rate limiting. The bulk endpoint has none. A single org could submit unlimited 50-shipment batches in rapid succession, saturating the thread pool.

---

**GAP-2: `CertificationScreener` and `SustainabilityRater` re-instantiated per shipment**  
File: `api/app.py:3092-3109`  
For a 50-shipment batch, `CertificationScreener(enabled_modules_bulk)` is called 50 times. Each call pre-compiles all module regex patterns. All 50 shipments in a single-org batch have identical `enabled_modules_bulk`. The screener and rater should be instantiated once per batch and passed into the per-shipment function.

---

**GAP-3: No cross-batch concurrency guard**  
File: `portguard/bulk_processor.py`  
The `asyncio.Semaphore(5)` is per-batch. If multiple batches are submitted concurrently (either same org or multiple orgs), each gets its own semaphore but shares the same `ThreadPoolExecutor(max_workers=5)`. Concurrent batches can oversubscribe the pool, causing all of them to run slower than the documented 5-concurrent limit suggests.

---

**GAP-4: CSV over-50 behavior is silent truncation, not an error**  
File: `portguard/bulk_parsers.py:425-430`  
CSV inputs silently drop rows beyond row 50 with a log warning. ZIP and manual raise `BatchTooLargeError`. The user gets no feedback that some of their shipments were dropped. Should either raise an error consistently or surface a truncation warning in the 202 response.

---

**GAP-5: No "New batch" / "Start over" button after results are shown**  
File: `demo.html` (bulk results screen)  
Once results are displayed, there is no clearly labeled "New batch" or "Back to upload" action. Users can navigate away by clicking the tab bar, but the bulk upload section state (`_bulkSlots`, file state) may or may not be reset. The upload section is hidden (not destroyed) when progress/results screens are shown.

---

**GAP-6: `data.total_shipments` in polling init is from create response, not status response**  
File: `demo.html:9426`  
`_bulkStartPolling(data.total_shipments || 0)` reads from the HTTP 202 create response, which does have `total_shipments`. This is correct. However the total is passed to `_bulkSetProgress` to show the denominator. If any shipment is rejected at the parsing stage (which would cause a 400 not 202), this is fine. But if the backend ever changes the create response shape, this will silently show `0/0` progress. Low risk, worth noting.

---

**GAP-7: `result_json` schema drift — no migration path**  
File: `portguard/bulk_processor.py` (`bulk_shipments.result_json TEXT`)  
Old batch rows (pre-certification, pre-sustainability) lack `sustainability_rating`, `module_findings`, etc. in their stored JSON. The frontend handles this with null-fallbacks, which is correct. But there is no documentation of the schema version, no upgrade path, and no way to backfill old results. Schema drift could cause silent data gaps over time.

---

**GAP-8: `api/Real_Claude_PortGuard` — stray empty file in repo**  
File: `api/Real_Claude_PortGuard`  
This file exists at the project root (`api/Real_Claude_PortGuard`) and contains a single blank line. It is tracked in git (`?? api/Real_Claude_PortGuard`), has no extension, no purpose, and no module imports it. It is likely an accidental `git add` of a scratch/test file and should be deleted.

---

**GAP-9: `docs/bulk_upload_audit.md` is stale and misleading**  
File: `docs/bulk_upload_audit.md`  
All BUG and GAP items listed in that document as open have been fixed in the current codebase. The document still reads as if they are unresolved. Any developer reading it will have an incorrect picture of the code state. It should be archived, deleted, or updated with resolution dates.

---

### INCONSISTENCIES

---

**INCON-1: Status vs Results endpoint response shapes are incompatible**  
`GET /status` → `{ decisions: { APPROVE: n, ... }, total: n, processed: n, ... }`  
`GET /results` → `{ summary: { approved: n, total: n, ... }, shipments: [...] }`

The key names differ (`decisions.APPROVE` vs `summary.approved`), the nesting differs, and the top-level field names differ (`total` vs `summary.total`). This caused BUG-1. The two endpoints should use consistent naming — ideally both nest counts under `summary` with snake_case keys.

---

**INCON-2: Duplicate `DEFAULT_ENABLED_MODULES` constant**  
`portguard/auth.py:AuthDB._DEFAULT_ENABLED_MODULES`  
`portguard/module_config_db.py:ModuleConfigDB.DEFAULT_ENABLED_MODULES`

Both define the same 7 default module IDs independently. Any change to the default set requires updating two files. They are currently in sync (FSC_COC, RAINFOREST_ALLIANCE, RSPO, WRAP, CONFLICT_MINERALS, ISO_9001, CE_MARKING) but could drift silently.

---

**INCON-3: CSV batch truncates silently; ZIP and manual raise hard errors**  
(Also listed as GAP-4 above)  
Behavior is inconsistent across input methods. All three should behave the same way when the 50-shipment limit is exceeded.

---

**INCON-4: ZIP PDF extraction uses server-side `pdfplumber`; manual/CSV use client-side PDF.js**

| Path | PDF extraction | Where |
|---|---|---|
| ZIP | `api/document_parser.py` via `pdfplumber` (server-side) | `bulk_parsers.py:parse_zip_upload()` |
| CSV | Client pre-extracts via PDF.js; sends text | N/A — PDF.js not part of CSV flow |
| Manual | Client-side PDF.js; extracted text sent as `raw_text` | `demo.html:_bulkPdfHandleUpload()` |

This is an architectural asymmetry: ZIP supports true binary PDF upload (the server extracts text), while manual and CSV entries can only submit pre-extracted text. If a user wants to upload a PDF without extractable text layers (scanned image PDF), the manual path silently sends an empty string while the ZIP path would fail at `extract_text()` with an explicit error.

---

**INCON-5: `_bulkStartPolling` receives `total` from create response but ignores it for status bar accuracy**  
The `total` passed to `_bulkStartPolling` initializes the progress denominator. But subsequent poll results from `/status` return `status.total` independently. If these ever diverge (e.g., due to a race at batch creation), the progress bar denominator would be wrong.

---

**INCON-6: Module instantiation per-shipment vs per-request in single analyze**  
In the single analyze endpoint (`POST /api/v1/analyze`), `CertificationScreener` is instantiated once per HTTP request. In bulk, it is instantiated once per shipment within a background task. The logical unit should be consistent — both should instantiate once per "analysis unit," which in bulk would be once per batch, not per shipment.

---

### SUMMARY TABLE

| ID | Type | Severity | File | Status |
|---|---|---|---|---|
| BUG-1 | Bug | High | `demo.html:9565-9589` | Open — all stat badges show 0 |
| BUG-2 | Bug | Medium | `demo.html:9817` | Open — share links are dead |
| BUG-3 | Bug | Medium | `demo.html:9194-9403` | Open — PDF conflict discards textarea edits |
| GAP-1 | Gap | Medium | `api/app.py:3093` | Open — no rate limit on bulk endpoint |
| GAP-2 | Gap | Low | `api/app.py:3092-3109` | Open — screener/rater re-instantiated per shipment |
| GAP-3 | Gap | Low | `portguard/bulk_processor.py` | Open — no cross-batch concurrency guard |
| GAP-4 | Gap | Medium | `portguard/bulk_parsers.py:425` | Open — CSV truncation is silent |
| GAP-5 | Gap | Low | `demo.html` | Open — no "new batch" button in results screen |
| GAP-6 | Gap | Low | `demo.html:9426` | Open — create response field dependency |
| GAP-7 | Gap | Low | `portguard/bulk_processor.py` | Open — no result_json schema versioning |
| GAP-8 | Gap | Low | `api/Real_Claude_PortGuard` | Open — stray empty file in repo |
| GAP-9 | Gap | Low | `docs/bulk_upload_audit.md` | Open — stale audit doc |
| INCON-1 | Inconsistency | High | `api/app.py:3633-3661, 3580-3601` | Open — status vs results key name mismatch |
| INCON-2 | Inconsistency | Low | `portguard/auth.py`, `portguard/module_config_db.py` | Open — duplicate default module constants |
| INCON-3 | Inconsistency | Medium | `portguard/bulk_parsers.py` | Open — inconsistent over-limit behavior |
| INCON-4 | Inconsistency | Medium | `bulk_parsers.py`, `demo.html` | Open — ZIP vs manual PDF extraction asymmetry |
| INCON-5 | Inconsistency | Low | `demo.html:9426` | Open — polling total vs status total |
| INCON-6 | Inconsistency | Low | `api/app.py:2970-3134` | Open — module instantiation granularity |

---

*End of audit. No code was written or modified in producing this document.*
