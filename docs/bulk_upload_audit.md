# Bulk Upload End-to-End Audit (2026-05-14)
**Supersedes:** prior audit dated 2026-04-27  
**Files:** `demo.html`, `api/app.py`, `portguard/bulk_processor.py`, `portguard/bulk_parsers.py`, `portguard/module_config_db.py`

---

## 1. ZIP Upload — Field Names Match, Working

### Frontend (demo.html ~10653)
```javascript
fd.append('zip_file', _bulkZipFile);
fd.append('input_method', 'ZIP');
```
`authedForm(fd)` correctly omits Content-Type header — browser sets multipart boundary.

### Backend (app.py:3823)
```python
_zf_raw = form.get("zip_file")
if _zf_raw and hasattr(_zf_raw, "filename"):
    zip_file = _zf_raw
```
Field name `zip_file` MATCHES frontend. Fallback scanner also present for any UploadFile with `.zip` extension.

### Parser (bulk_parsers.py `parse_zip_upload`)
- Max: 50 MB / 50 shipments
- One top-level subfolder = one shipment; folder name = ref ID
- Reads `.txt` and `.pdf` files (PDF text extracted via `api.document_parser.extract_text`)
- Skips `__MACOSX`, hidden files, non-txt/pdf

**Status: ✅ Working**

---

## 2. CSV Upload — Field Names Match, Working

### Frontend (demo.html ~10659)
```javascript
fd.append('csv_file', _bulkCsvFile);
fd.append('input_method', 'CSV');
```

### Backend (app.py:3824)
```python
_cf_raw = form.get("csv_file")
if _cf_raw and hasattr(_cf_raw, "filename"):
    csv_file = _cf_raw
```
Field name `csv_file` MATCHES frontend.

### Parser (bulk_parsers.py `parse_csv_upload`)
- Max: 5 MB / 50 rows
- Detects reference column (case-insensitive): `reference_id | shipment_ref | ref | id | reference | shipment_id`
- Detects doc columns: `bill_of_lading | commercial_invoice | packing_list | certificate_of_origin | isf_filing | other_doc_1`
- Generic fallback: `document_text | text | content | shipment | description`
- Raw-row fallback: concatenates all non-reference columns if no doc columns recognized

**Status: ✅ Working**

---

## 3. Manual Entry

- `_bulkMethod = 'manual'`, `_bulkSlots` array
- Each slot: reference ID + tabs with `text`/`pdfExtractedText`
- On submit: JSON body `{ input_method: 'MANUAL', shipments: [...] }`
- Backend: `validate_manual_input(raw_shipments)` → auto-generates refs, deduplicates

**Status: ✅ Working**

---

## 4. PDFs in Manual Entry — Already Implemented

`_bulkPdfHandleUpload(slotId, tabId, file)` at demo.html:10364:
- PDF.js (`pdfjsLib`) renders thumbnails (up to 3 pages)
- Extracts text from all pages
- Stores as `tab.pdfExtractedText`, shows conflict warning vs manual text
- PDF button only shown if `typeof pdfjsLib !== 'undefined'`
- Stale-upload guard via `tab._pdfUploadGen`

**Status: ✅ Working**

---

## 5. Results Rendering — All-at-Once (Working)

`_bulkSubmit` awaits the synchronous POST response, then calls `_bulkRenderFromResponse(data)` which maps all results at once. No partial/pending rows shown during processing.

Polling code (`_bulkStartPolling`, `_bulkPoll`) is preserved for the share-link restore path only.

**Status: ✅ Working**

---

## 6. Module Toggles During Bulk — Working

app.py:4011-4018:
```python
enabled_modules = _module_config_db.get_enabled_modules(org_id)
await processor.process_batch(..., enabled_modules=enabled_modules, ...)
```
Snapshot bound via `functools.partial` — same modules used for all rows without per-row DB queries.

`_run_bulk_single_analysis` correctly uses `enabled_modules` for `CertificationScreener` (app.py:3411-3420).

**Status: ✅ Working**

---

## 7. Sustainability in Bulk — Working

`_run_bulk_single_analysis` runs `SustainabilityRater` at app.py:3424-3431. Result stored in `AnalyzeResponse.sustainability_rating` and then in `result_json` column of `bulk_shipments`. `_bulkRenderFromResponse` extracts `sus.grade` and `sus.signals` per row.

**Status: ✅ Working**

---

## 8. PDF Preview Code in Single-Upload Screen

Single-upload PDF preview at demo.html (search `_pdfHandleUpload`):
- `_pdfHandleUpload(file)` — main upload handler
- `_pdfRenderThumb(pdfDoc, pageNum)` — canvas thumbnail render
- `_pdfExtractText(pdfDoc)` — extracts text from all pages
- `_pdfSetScanDone(state, badgeText)` — updates badge state
- CSS: `.pdf-thumb-wrap`, `.pdf-scan-beam`, `.pdf-extract-badge`, `.pdf-thumb-canvas`

---

## 9. Confirmed Bugs (Fixed in This Session)

### BUG-1 (FIXED): No timeout on bulk fetch

**File:** `demo.html` in `_bulkSubmit`  
**Previous behavior:** `_bulkAbortController = new AbortController()` with no timeout. If backend takes >120s, browser hangs indefinitely. No error shown.  
**Fix applied:** Added 120-second `setTimeout` that sets `_bulkTimedOut = true` and calls `.abort()`. Catch block now distinguishes timeout from user-cancel and shows: "Bulk screening timed out after 2 minutes. Try a smaller batch or check your connection."

### BUG-2 (FIXED): AbortError silently swallowed on timeout

**Previous behavior:** `catch (e) { if (e.name === 'AbortError') return; }` — timeout fired but no error was shown.  
**Fix applied:** `if (_bulkTimedOut) _bulkShowProgressError(...)` shown before the return.

### BUG-3 (FIXED): Confirm modal always shown for ZIP/CSV

**Previous behavior:** `count = '?'` for ZIP/CSV → `typeof count === 'number'` false → modal always shown with "? shipments" — unhelpful friction.  
**Fix applied:** `bulkSubmitClick()` now skips the modal immediately for ZIP/CSV and calls `bulkModalConfirm()` directly.

---

## 10. Architecture Notes

- **Concurrent processing:** `asyncio.Semaphore(5)` + `ThreadPoolExecutor(5)` — max 5 shipments at once
- **Per-shipment timeout:** 30s (`SHIPMENT_TIMEOUT_SECONDS`) — timeouts stored as `TIMEOUT` decision
- **Batch timeout:** 120s client-side (added in this session)
- **Rate limit:** 3 batches/minute per org
- **Org isolation:** All batch queries filter by `organization_id`
- **result_json:** Full `AnalyzeResponse` JSON stored per shipment for PDF regeneration
