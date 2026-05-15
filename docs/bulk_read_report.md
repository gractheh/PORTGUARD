# PortGuard Bulk Upload — Full Codebase Read Report

**Date:** 2026-05-15
**Scope:** Every file in the project read before writing this document.
**Purpose:** Authoritative current-state report on the bulk upload system: field names, error conditions, parsing logic, execution trace, and every known defect.

---

## 1. Bulk Upload Endpoint — URL and Function Name

**URL:** `POST /api/v1/analyze/bulk`
**Function:** `bulk_create(request, current_org)` in `api/app.py`

The function signature uses a raw `Request` object instead of FastAPI's typed `File()` / `Form()` parameters. This is intentional — it allows the endpoint to handle both multipart/form-data (ZIP/CSV) and JSON (manual batch) in a single handler by inspecting `Content-Type`.

---

## 2. Field Name the Endpoint Expects

For ZIP uploads, the backend reads: `form.get("zip_file")`
For CSV uploads, the backend reads: `form.get("csv_file")`
Both paths also read: `form.get("input_method")` to determine routing.

**Fallback scan** (`api/app.py` ~lines 3840–3872): If neither named key is found, the endpoint scans all `UploadFile` entries in the form and selects the first one whose filename ends in `.zip` or `.csv` (or whose MIME type is `application/zip` / `text/csv`). This is a resilience fallback — the named keys are the primary path.

For manual batch (JSON body): the endpoint reads `input_method: "MANUAL"` and `shipments: [...]` from the parsed JSON.

---

## 3. Field Name the Frontend Sends

`demo.html` (~lines 10872–10887):

```javascript
// ZIP path:
fd.append('zip_file', _bulkZipFile);
fd.append('input_method', 'ZIP');

// CSV path:
fd.append('csv_file', _bulkCsvFile);
fd.append('input_method', 'CSV');
```

---

## 4. Do the Field Names Match?

**YES — the field names match.**

Both the frontend and backend use `zip_file` for ZIP and `csv_file` for CSV. This was a historical bug (the original frontend sent `'file'` for both, the backend expected named keys), but it was fixed in the Bulk Upload sprint (commit `3ba7eb9`). The current code is correct on both sides.

---

## 5. "Document Validation Failed" — Where Thrown, What Line, What Condition

**Location:** `api/app.py`, inside `_run_bulk_single_analysis()`, approximately line 3312–3319.

**Code:**
```python
_bulk_clf = [_classify_document(doc.raw_text or "") for doc in docs]
_bulk_rejected = [(i, r) for i, r in enumerate(_bulk_clf) if not r["accepted"]]
if _bulk_rejected:
    rej_reasons = "; ".join(
        r["rejection_reason"] or "Not a trade document"
        for _, r in _bulk_rejected
    )
    raise ValueError(f"Document validation failed: {rej_reasons}")
```

**Condition:** One or more documents in the shipment returned `accepted: False` from the document classifier. `_classify_document()` is called on every document's `raw_text`. If any document fails, the entire shipment is rejected with a `ValueError`. This exception is caught by `BulkProcessor.process_batch()` and stored as the shipment's `error_message` with `status = 'ERROR'`.

Note the exact casing: the error text begins with an uppercase "D" — `"Document validation failed: ..."` (not lowercase "document validation failed"). Case matters if anything is string-matching on this error.

---

## 6. How the Bulk Pipeline Calls the Document Classifier

The bulk pipeline calls the **strict hardened version** of the document classifier via a thin facade.

`_classify_document` is imported at module level in `api/app.py` from `portguard.agents.document_classifier`. That module (`portguard/agents/document_classifier.py`) is a one-file facade:

```python
from portguard.agents.document_classifier_hardened import classify_document, DocumentClassifier, ...
```

Every call to `_classify_document(text)` in the bulk pipeline goes through `DocumentClassifier.classify()` in `document_classifier_hardened.py`, which applies all 5 classification layers with the full set of thresholds. There is no "lite" or "non-strict" classifier path — the same hardened engine is used for both bulk and single-document analysis.

---

## 7. Minimum Score Threshold for Rejection

The hardened classifier has **four independent rejection conditions** (any one triggers rejection):

| Condition | Threshold | Message |
|---|---|---|
| Anti-score hard reject | `anti_score >= 12.0` (unless `doc_type` detected AND `total_pro >= 30`) | "Document contains too much non-trade content" |
| Anti-ratio reject | `anti_ratio >= 0.60` AND `anti_score >= 6.0` (unless strong fingerprint bonus `>= 8.0`) | "Document is predominantly non-trade content" |
| Generic content reject | `total_pro < 2.0` AND `doc_type is None` | "This document does not appear to contain shipping or customs content" |
| Below minimum threshold | `confidence < 0.35` AND `doc_type is None` | Generic rejection reason |

`LOW_THRESHOLD = 0.35`, `MEDIUM_THRESHOLD = 0.50`, `HIGH_THRESHOLD = 0.75`

The **lowest effective bar** is condition 3: any text with fewer than 2.0 points of trade vocabulary AND no recognizable document type pattern is rejected outright. This is the condition that fires for generic text.

---

## 8. Would a Short CSV Row or Simple Text Snippet Pass or Fail?

**FAIL.** A short CSV row (e.g., `"SHP-001, Acme Corp, 100 units"`) or a plain text snippet without trade-document vocabulary will be **rejected**.

Why: The hardened classifier scores text against shipping and customs vocabulary (bill of lading terms, HTS codes, ISF elements, shipper/consignee fields, etc.) to build `total_pro`. A brief, non-trade string produces `total_pro < 2.0` and fails to match any document-type fingerprint (`doc_type = None`). Condition 3 fires: `"This document does not appear to contain shipping or customs content."` The shipment fails with `"Document validation failed: This document does not appear to contain shipping or customs content"`.

This is by design — the classifier is supposed to block non-document content. The practical implication: a CSV row that only contains metadata columns (ref, importer name) but no actual document text will fail. Every shipment in a bulk CSV must have at least one document column with real trade document text, not just metadata.

---

## 9. ZIP File Parsing — Library and Code

**Library:** Python standard library `zipfile` module.

**Function:** `parse_zip_upload(zip_bytes: bytes)` in `portguard/bulk_parsers.py`.

**Logic:**
1. Opens `zipfile.ZipFile(io.BytesIO(zip_bytes))`.
2. Iterates `zf.namelist()`. Each entry is split by `/`. Entries with fewer than 2 path parts (root-level loose files) are skipped.
3. The first path part becomes the shipment reference (`folder`). The last part is the filename.
4. Hidden files (starting with `.`) are skipped.
5. `.txt` files: read as bytes, decoded UTF-8 with `errors='replace'`.
6. `.pdf` files: extracted via `extract_text_from_pdf(raw_bytes)` which uses `pdfplumber`. If text extraction fails, the document is silently skipped.
7. All other file types: silently skipped.
8. Max 50 shipments (folders); raises `BatchTooLargeError` if exceeded.
9. Max 10 documents per shipment; documents beyond 10 are silently truncated.
10. A folder with zero successfully extracted documents is excluded from the result.

**Key asymmetry:** ZIP PDF extraction is server-side (pdfplumber); manual-entry PDF extraction is client-side (PDF.js). A scanned image PDF (no text layer) will silently produce an empty string on the ZIP path, potentially passing the batch intake but failing at the classifier gate.

---

## 10. CSV File Parsing — Library and Code

**Library:** Python standard library `csv` module with `csv.DictReader` and `csv.Sniffer`.

**Function:** `parse_csv_upload(csv_bytes: bytes)` in `portguard/bulk_parsers.py`.

**Logic:**
1. Decodes bytes as UTF-8-sig (BOM-tolerant).
2. Uses `csv.Sniffer().sniff()` with fallback to comma delimiter if sniffing fails.
3. Reads via `csv.DictReader`. Normalizes column names (strip, lowercase, replace spaces/hyphens with underscores).
4. Looks for the reference column: any of `shipment_ref`, `reference`, `ref`, `id`, `shipment_id`. First match wins.
5. Canonical document columns: `bill_of_lading`, `commercial_invoice`, `packing_list`, `certificate_of_origin`, `isf_filing`, `other_doc_1`.
6. For each row: skips rows with no reference value. For each non-empty document column, creates a `RawDocument(filename=col+'.txt', text=value)`. If none of the canonical columns are found, concatenates all non-reference columns as a fallback `raw_text`.
7. Silently truncates at row 50 (logs a warning, returns first 50 rows). **No error is raised for CSV over-limit** — this is an inconsistency with ZIP and manual which raise `BatchTooLargeError`.

---

## 11. Does the Frontend Show Results Before the Backend Finishes?

**NO.** As of the Bulk Upload sprint (`commit 3ba7eb9`, 2026-04-29), the architecture was changed from asynchronous background processing with polling to **synchronous execution**.

The frontend sends a single POST to `/api/v1/analyze/bulk` and awaits the complete response. The backend runs all shipments synchronously via `asyncio.gather` (still concurrent, but waits for all to complete before responding). The response contains `data.results[]` with all per-shipment outcomes.

```javascript
// demo.html ~lines 10912-10917:
const data = await res.json();
_bulkBatchId = data.batch_id;
// Backend returned all results synchronously — render immediately
fillEl.classList.remove('indeterminate');
_bulkRenderFromResponse(data);
```

`_bulkRenderFromResponse(data)` maps `data.results[]` to `_bulkAllResults` and renders the table. There is no pending state, no "PROCESSING" badges, no polling loop. Results only appear after all shipments have been processed.

---

## 12. Exact Sequence of Events After a Bulk File Is Uploaded

This is the full execution trace for a ZIP upload:

```
1. User selects ZIP file → _bulkZipFile set in demo.html state
2. User clicks "Start Screening"
   └─ _bulkSubmit() called in demo.html

3. Frontend:
   └─ FormData built: { zip_file: <File>, input_method: 'ZIP' }
   └─ AbortController created, wired to Cancel button
   └─ Progress bar set to indeterminate animation
   └─ POST /api/v1/analyze/bulk with Authorization: Bearer <jwt>
   └─ fetch(..., { signal: abortController.signal })
   └─ *** awaits response — UI shows loading progress bar ***

4. Backend — bulk_create() (api/app.py):
   └─ get_current_organization() validates JWT → org dict
   └─ reads Content-Type: multipart/form-data
   └─ await request.form()
   └─ form.get("zip_file") → UploadFile object
   └─ zip_bytes = await zip_file.read()
   └─ parse_zip_upload(zip_bytes) → list of shipment dicts
   └─ if 0 shipments → HTTP 400 EMPTY_BATCH
   └─ if > 50 shipments → HTTP 400 BATCH_TOO_LARGE
   └─ BulkProcessor.create_batch(shipments, org_email) → batch_id
      └─ INSERT INTO bulk_batches (status='QUEUED')
      └─ INSERT INTO bulk_shipments rows (status='PENDING')
   └─ asyncio.gather(*[_run_one(s) for s in shipments])
      *** All shipments processed HERE before HTTP response ***
      └─ asyncio.Semaphore(5) — max 5 concurrent
      └─ ThreadPoolExecutor(max_workers=5)
      └─ For each shipment (concurrent):
         └─ loop.run_in_executor(executor, _run_bulk_single_analysis, docs, org)
            └─ _validate_documents(docs)  ← basic document gate
            └─ _classify_document(doc.raw_text) for each doc  ← HARDENED CLASSIFIER
            └─ if any rejected → raise ValueError("Document validation failed: ...")
            └─ _analyze_documents(docs)  ← 5-stage rule pipeline:
               └─ ParserAgent.parse()
               └─ ClassifierAgent.classify()
               └─ ValidationAgent.validate()
               └─ RiskAgent.assess_risk()
               └─ DecisionAgent.decide()
            └─ PatternEngine.score()  ← pattern blend if history available
            └─ CertificationScreener.screen()  ← stage 3.5
            └─ SustainabilityRater.rate()  ← stage 5.5
            └─ AnalyzeResponse constructed
            └─ _record_shipment_bg()  ← writes to shipment_history
         └─ BulkProcessor._store_shipment_result()
            └─ UPDATE bulk_shipments SET status='COMPLETE', decision, risk_score, ...
      └─ All asyncio.gather tasks complete
   └─ BulkProcessor._mark_batch_complete()
      └─ UPDATE bulk_batches SET status='COMPLETE', completed_at
   └─ Build response dict with data.results[] from bulk_shipments rows

5. HTTP 200 response returned with:
   {
     batch_id: "...",
     results: [{ result_id, reference_id, decision, risk_score, flags,
                 sustainability_rating, sustainability_signals,
                 active_modules_snapshot, ... }, ...]
   }

6. Frontend — response received:
   └─ fillEl.classList.remove('indeterminate')
   └─ _bulkRenderFromResponse(data)
      └─ maps data.results[] → _bulkAllResults
      └─ _bulkRenderTable() → renders results table
      └─ _bulkShowSummaryStats() → renders stat badges
   └─ Progress bar fills to 100%
   └─ Results table appears
```

For **ERROR shipments** (classifier rejection, analysis failure, timeout): the shipment's `error_message` is stored in `bulk_shipments` and returned in `data.results[]` with `decision: null`. The results table shows an ERROR badge for that row.

For the **30-second per-shipment timeout**: `asyncio.wait_for(run_in_executor(...), timeout=30.0)` wraps each shipment. On `TimeoutError`, the shipment is marked ERROR with "Analysis timed out after 30 seconds."

---

## 13. Everything That Is Currently Broken

### CONFIRMED BUGS

---

**BUG-1: `analytics.py` status case mismatch — bulk analyses invisible to dashboard**
Files: `portguard/analytics.py:950` and `portguard/analytics.py:1042`
Severity: **High** — silent data loss in dashboard metrics

`get_module_summary()` and `get_top_missing_certifications()` query `bulk_shipments` with:
```python
WHERE status = 'complete'   ← lowercase
```

But `BulkProcessor._store_shipment_result()` writes:
```python
status = 'COMPLETE'   ← uppercase (portguard/bulk_processor.py:_BULK_SCHEMA_SQL)
```

SQLite string comparison is case-sensitive by default. **No bulk analyses are ever counted in the module stats or missing certification reports on the Analytics dashboard.** The dashboard only reflects single-document analyses for these two metrics. This is a silent bug — no error is raised, the query just returns zero rows.

---

**BUG-2: PDF conflict in manual entry silently discards textarea edits**
File: `demo.html` (~lines 9194–9207, 9400–9403, per sprint audit)
Severity: **Medium** — data loss risk

When a PDF is uploaded into a manual entry slot that already contains text:
- `tab.pdfExtractedText` = extracted PDF text
- `tab.text` = original manual text (preserved and shown in textarea)
- Override warning is shown

On submit: `raw_text: (t.pdfExtractedText || t.text || '').trim()` — PDF text is always submitted.

If the user edits the textarea after seeing the override warning, those edits are silently discarded because `t.pdfExtractedText` is non-empty and takes priority. The UI does not communicate that textarea edits in this conflict state will not be submitted.

---

**BUG-3: Stray empty file in repo**
File: `api/Real_Claude_PortGuard`
Severity: **Low** — cosmetic

A tracked file at `api/Real_Claude_PortGuard` contains a single blank line. No module imports it. It is an accidental commit artifact. It should be deleted.

---

### CONFIRMED GAPS (missing features or incomplete implementations)

---

**GAP-1: No rate limiting on `POST /api/v1/analyze/bulk`**
File: `api/app.py:bulk_create()`
The single-analyze endpoint and auth endpoints have per-IP rate limiting. The bulk endpoint has none. A single org can submit unlimited 50-shipment batches in rapid succession, saturating the 5-worker thread pool for all orgs on the server.

---

**GAP-2: CSV over-50 behavior is silent truncation, not an error**
File: `portguard/bulk_parsers.py` (~line 425)
ZIP and manual inputs raise `BatchTooLargeError` when the 50-shipment limit is exceeded. CSV silently drops rows beyond row 50 with only a server-side log warning. The user receives no indication that their data was truncated — their 202 response looks identical to a 50-row batch that processed everything. Rows 51–N are silently discarded.

---

**GAP-3: No "new batch" / "start over" button after results are displayed**
File: `demo.html` (bulk results section)
After results are shown there is no prominent "New batch" button. Users can navigate via the tab bar but the bulk section's JS state (`_bulkSlots`, file references) is not explicitly reset. The upload panel is hidden, not destroyed.

---

**GAP-4: No cross-batch concurrency guard**
File: `portguard/bulk_processor.py`
The `asyncio.Semaphore(5)` is per-batch. Multiple concurrent batches from the same org or different orgs each create their own semaphore but share the same `ThreadPoolExecutor(max_workers=5)`. Two simultaneous 50-shipment batches can together saturate the 5-worker pool, causing both to run at half speed rather than the documented 5× speedup.

---

**GAP-5: `result_json` schema drift — no versioning or backfill**
File: `portguard/bulk_processor.py` (`bulk_shipments.result_json TEXT`)
Old batch rows (pre-certification, pre-sustainability) lack `sustainability_rating`, `module_findings`, etc. in their stored JSON. The frontend handles this with null-fallbacks (correct behavior), but there is no schema version stamp, no documented upgrade path, and no backfill mechanism. Schema drift accumulates silently.

---

### INCONSISTENCIES

---

**INCON-1: Duplicate `DEFAULT_ENABLED_MODULES` constant**
Files: `portguard/auth.py:AuthDB._DEFAULT_ENABLED_MODULES` and `portguard/module_config_db.py:ModuleConfigDB.DEFAULT_ENABLED_MODULES`

The same 7 default module IDs are defined independently in both files (FSC_COC, RAINFOREST_ALLIANCE, RSPO, WRAP, CONFLICT_MINERALS, ISO_9001, CE_MARKING). Any change to the default set requires updating both files. They are currently in sync but could silently diverge.

---

**INCON-2: ZIP uses server-side PDF extraction; manual entry uses client-side PDF.js**

| Input method | PDF extraction | Location |
|---|---|---|
| ZIP upload | pdfplumber (server-side) | `bulk_parsers.py:parse_zip_upload()` |
| Manual entry | PDF.js (client-side) | `demo.html:_bulkPdfHandleUpload()` |
| CSV | Not applicable — text in cells | — |

A scanned image PDF (no text layer) behaves differently on each path: ZIP path produces an empty string and silently skips the document; manual path also produces an empty string but the user sees the extraction badge UI. Neither path warns the user that the PDF had no extractable text. The resulting empty `raw_text` will fail the classifier gate with "This document does not appear to contain shipping or customs content."

---

**INCON-3: Status endpoint (`/status`) and results endpoint (`/results`) have incompatible response shapes**

Even though the frontend no longer polls these endpoints (sync model), they are still live API endpoints. Their shapes are:

`GET /status` response: `{ decisions: { APPROVE: n, FLAG_FOR_INSPECTION: n, ... }, total: n, processed: n }`
`GET /results` response: `{ summary: { approved: n, flagged: n, total: n, ... }, shipments: [...] }`

Key names differ (`decisions.APPROVE` vs `summary.approved`), nesting differs. Any external tool or integration that reads both endpoints will need to handle two incompatible shapes. The inconsistency is documented but not fixed.

---

### SUMMARY TABLE

| ID | Type | Severity | File | Description |
|---|---|---|---|---|
| BUG-1 | Bug | **High** | `portguard/analytics.py:950,1042` | Bulk shipments invisible to dashboard — `status='complete'` vs `'COMPLETE'` |
| BUG-2 | Bug | **Medium** | `demo.html:~9194–9403` | PDF conflict in manual entry silently discards textarea edits |
| BUG-3 | Bug | **Low** | `api/Real_Claude_PortGuard` | Stray empty file in repo |
| GAP-1 | Gap | **Medium** | `api/app.py:bulk_create` | No rate limiting on bulk endpoint |
| GAP-2 | Gap | **Medium** | `portguard/bulk_parsers.py:~425` | CSV silently truncates at 50 rows, no error |
| GAP-3 | Gap | **Low** | `demo.html` | No "new batch" button after results |
| GAP-4 | Gap | **Low** | `portguard/bulk_processor.py` | No cross-batch concurrency guard |
| GAP-5 | Gap | **Low** | `portguard/bulk_processor.py` | No result_json schema versioning |
| INCON-1 | Inconsistency | **Low** | `portguard/auth.py`, `portguard/module_config_db.py` | Duplicate DEFAULT_ENABLED_MODULES constant |
| INCON-2 | Inconsistency | **Medium** | `bulk_parsers.py`, `demo.html` | ZIP vs manual PDF extraction asymmetry |
| INCON-3 | Inconsistency | **Low** | `api/app.py` | Status vs results endpoint response shape mismatch |

---

## Appendix: Key File Locations

| Component | File | Key function/class |
|---|---|---|
| Bulk endpoint | `api/app.py:bulk_create()` | ~line 3736 |
| Per-shipment analysis | `api/app.py:_run_bulk_single_analysis()` | ~line 3255 |
| Batch orchestration | `portguard/bulk_processor.py:BulkProcessor` | Full file |
| ZIP parsing | `portguard/bulk_parsers.py:parse_zip_upload()` | — |
| CSV parsing | `portguard/bulk_parsers.py:parse_csv_upload()` | — |
| Document classifier facade | `portguard/agents/document_classifier.py` | Thin facade → hardened |
| Hardened classifier | `portguard/agents/document_classifier_hardened.py:DocumentClassifier` | 1034 lines |
| Analytics (broken query) | `portguard/analytics.py:get_module_summary()` | Line 950 |
| Analytics (broken query) | `portguard/analytics.py:get_top_missing_certifications()` | Line 1042 |
| Frontend bulk submit | `demo.html:_bulkSubmit()` | ~line 10860 |
| Frontend render | `demo.html:_bulkRenderFromResponse()` | ~line 10912 |
