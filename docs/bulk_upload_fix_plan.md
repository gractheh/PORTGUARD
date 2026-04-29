# Bulk Upload Fix Plan

**Date:** 2026-04-27  
**Based on:** `docs/bulk_upload_audit.md`  
**Scope:** Three work items — CSV input fix, PDF upload in manual entry, sustainability & modules in bulk results.

---

## Overview of Changes

| Work Item | Files Changed | Complexity |
|-----------|--------------|------------|
| 1 — Fix CSV/ZIP input (FormData keys) | `demo.html` only | Trivial (2 lines + 2 lines) |
| 2 — PDF upload in manual entry slots | `demo.html` (CSS + HTML + JS) | Medium-high |
| 3 — Sustainability & modules in bulk | `api/app.py`, `portguard/bulk_processor.py`, `portguard/pattern_db.py`, `demo.html` | Medium |

No breaking changes to the single-document analyze tab, existing reports, analytics, or the `/api/v1/analyze` endpoint.

---

## Work Item 1 — Fix CSV/ZIP Input (FormData Key Mismatch)

### Root Cause (recap)

`demo.html:_bulkSubmit()` appends the file under the key `'file'` for both ZIP and CSV:

```javascript
// Current (broken)
fd.append('file', _bulkZipFile);   // line 8477
fd.append('file', _bulkCsvFile);   // line 8481
```

The backend reads `form.get("zip_file")` and `form.get("csv_file")`. Neither field exists, both resolve to `None`, the route defaults to `input_method = "ZIP"`, and the ZIP guard fires: `"No zip_file provided."`.

Neither path appends `input_method`, so for CSV the backend defaults to `"ZIP"` and fails with the wrong error message.

The **backend is correct**. Two frontend line changes fix both bugs.

### Changes

#### `demo.html` — function `_bulkSubmit()` (~line 8475)

**Before:**
```javascript
if (_bulkMethod === 'zip' && _bulkZipFile) {
  const fd = new FormData();
  fd.append('file', _bulkZipFile);
  res = await fetch(apiUrl() + '/api/v1/analyze/bulk', authedForm(fd));
} else if (_bulkMethod === 'csv' && _bulkCsvFile) {
  const fd = new FormData();
  fd.append('file', _bulkCsvFile);
  res = await fetch(apiUrl() + '/api/v1/analyze/bulk', authedForm(fd));
}
```

**After:**
```javascript
if (_bulkMethod === 'zip' && _bulkZipFile) {
  const fd = new FormData();
  fd.append('zip_file', _bulkZipFile);
  fd.append('input_method', 'ZIP');
  res = await fetch(apiUrl() + '/api/v1/analyze/bulk', authedForm(fd));
} else if (_bulkMethod === 'csv' && _bulkCsvFile) {
  const fd = new FormData();
  fd.append('csv_file', _bulkCsvFile);
  fd.append('input_method', 'CSV');
  res = await fetch(apiUrl() + '/api/v1/analyze/bulk', authedForm(fd));
}
```

That is the complete fix. Four lines changed.

### Output Shape Alignment

After this fix, all three input modes (ZIP, CSV, Manual) reach the backend, get parsed by their respective parsers, and each shipment is dispatched to `_run_bulk_single_analysis(documents, org_id)`. The function signature and return shape are identical for all three. The endpoint responds with HTTP 202 and a `batch_id`. The client polls `/results` to get per-shipment results in this shape (after Work Item 3 adds sustainability fields):

```json
{
  "batch_id": "...",
  "summary": {
    "total": 12,
    "processed": 12,
    "approved": 5,
    "review_recommended": 3,
    "flagged": 2,
    "needs_info": 1,
    "rejected": 1,
    "errors": 0,
    "avg_risk_score": 0.41
  },
  "shipments": [
    {
      "ref": "SHP-001",
      "status": "COMPLETE",
      "decision": "FLAG_FOR_INSPECTION",
      "risk_score": 0.82,
      "risk_level": "HIGH",
      "n_findings": 4,
      "top_finding": "Section 301 tariff rate 25% applies...",
      "analysis_id": "uuid",
      "sustainability_grade": "C",            ← added by Work Item 3
      "sustainability_signals": ["..."],      ← added by Work Item 3
      "full_result": {
        "explanations": [...],               ← flags
        "sustainability_rating": {...}       ← full rating object
      }
    }
  ]
}
```

The frontend `_bulkAllResults` array maps this to the internal row shape (see Work Item 3).

---

## Work Item 2 — PDF Upload in Manual Entry Slots

### Current State

Each manual slot has tabs. Each tab has:
- `filename` — text input
- `text` — textarea (plain text only)
- `id` — numeric tab ID

There is no file picker or PDF processing in the slot UI.

### Target State

Each tab inside a manual slot gains:
- An "Upload PDF" button (compact, teal-outlined) next to the filename input
- When a PDF is selected: the scan beam animation plays inside a mini preview panel embedded in the slot card (same `.pdf-scan-beam` CSS already defined, same PDF.js pipeline)
- PDF text is extracted and populates the tab's textarea
- If both PDF text and manual text are present, PDF text wins with a visible warning banner
- User can still open "Edit text" toggle to review or override the extracted text

### New State Shape for Slot Tabs

The `_bulkSlots` array item currently has:
```javascript
{
  id: slotId,
  ref: 'SHP-001',
  tabs: [{ id: tabId, filename: 'bill_of_lading.txt', text: '' }],
  activeTab: tabId,
  expanded: true
}
```

Each tab object gains these fields:
```javascript
{
  id: tabId,
  filename: 'bill_of_lading.txt',
  text: '',            // existing
  pdfFile: null,       // File object when PDF is chosen
  pdfMeta: null,       // same shape as single-doc tab.pdfMeta
  pdfUploadGen: 0,     // stale-upload guard (mirrors single-doc tab._pdfUploadGen)
  pdfExtractedText: '', // text from PDF extraction
  textEditOpen: false, // whether the "Edit text" collapse is open
}
```

### New JS Functions

All new functions are prefixed `_bulkPdf` to avoid collision with the single-doc `_pdf` functions that operate on the global `tabs` array.

#### `_bulkPdfHandleUpload(slotId, tabId, file)`

Mirrors `_pdfHandleUpload(tabId, file)` but resolves the tab via `_bulkSlots` instead of `tabs`.

```
1. Find slot = _bulkSlots.find(s => s.id === slotId)
2. Find tab  = slot.tabs.find(t => t.id === tabId)
3. Increment tab.pdfUploadGen; capture uploadGen
4. Load PDF with pdfjsLib.getDocument({ data: arrayBuffer }).promise
   - On PasswordException: show inline error, don't extract
   - On other error: fall through to show error badge
5. Set tab.pdfMeta = { pageCount, fileSizeKb, thumbnails: [], extractionState: 'extracting', badgeText: null }
6. Set tab.pdfFile = file
7. Call _bulkRenderSlot(slotId) to show mini preview
8. Render up to 5 page thumbnails via _bulkPdfRenderThumb (same logic as _pdfRenderThumb)
9. Extract text via _bulkPdfExtractText(pdfDoc, uploadGen, slot, tabId)
10. Populate tab.pdfExtractedText = extractedText
11. If both tab.pdfExtractedText and tab.text exist: show override warning banner
    Else: tab.text = extractedText; update textarea value
12. Call _bulkPdfSetScanDone(slotId, tabId, 'done')
13. Call _bulkSaveSlotActiveTab(slot) to sync textarea
```

#### `_bulkPdfExtractText(pdfDoc, uploadGen, slot, tabId)`

Mirrors `_pdfExtractTextAllPages` but checks `tab.pdfUploadGen !== uploadGen` (stale guard uses slot's tab, not global `tabs`).

Returns the extracted text string.

#### `_bulkPdfSetScanDone(slotId, tabId, state, badgeText)`

Mirrors `_pdfSetScanDone(tabId, state, badgeText)` but targets elements scoped to `bulk-slt-pdf-*` IDs (see HTML changes below).

```
1. querySelectorAll(`#bulk-slt-pdf-thumbs-${slotId}-${tabId} .pdf-thumb-wrap`)
   .forEach(w => w.classList.add('scan-done'))
2. Update badge: document.getElementById(`bulk-slt-pdf-badge-${slotId}-${tabId}`)
3. If state === 'error' and no text: open textEditOpen
```

#### `bulkSlotPdfChosen(slotId, tabId, inputEl)`

Event handler for the hidden `<input type="file">` inside each tab:
```javascript
function bulkSlotPdfChosen(slotId, tabId, inputEl) {
  const file = inputEl.files && inputEl.files[0];
  if (!file) return;
  _bulkPdfHandleUpload(slotId, tabId, file);
}
```

### HTML/Template Changes

#### Inside `_bulkBuildSlotEl(slot)` — the doc pane template

In the current template the pane emits a filename row and a textarea. The updated template for each tab pane:

**Filename row** — add a PDF upload button:
```html
<div class="bulk-slot-filename-row">
  <div class="bulk-slot-fname-label">File</div>
  <input class="bulk-slot-fname-input" id="bulk-slt-fname-${slot.id}-${activeTabData.id}"
         value="${escHtml(activeTabData.filename || '')}"
         placeholder="bill_of_lading.txt">
  <input type="file" accept=".pdf" style="display:none"
         id="bulk-slt-pdf-input-${slot.id}-${activeTabData.id}"
         onchange="bulkSlotPdfChosen(${slot.id},${activeTabData.id},this)">
  <button class="bulk-slot-pdf-btn"
          onclick="document.getElementById('bulk-slt-pdf-input-${slot.id}-${activeTabData.id}').click()">
    <svg width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
      <polyline points="14 2 14 8 20 8"/>
    </svg>
    PDF
  </button>
</div>
```

**Conditional PDF preview panel** (rendered when `activeTabData.pdfMeta` is set):
```html
<!-- Conditionally rendered only when activeTabData.pdfMeta is not null -->
<div class="bulk-slt-pdf-panel" id="bulk-slt-pdf-panel-${slot.id}-${activeTabData.id}">
  <div class="bulk-slt-pdf-header">
    <!-- filename pill + page count + size -->
    <span class="bulk-slt-pdf-filename">${escHtml(activeTabData.filename)}</span>
    <span class="bulk-slt-pdf-meta">${meta.pageCount} p · ${meta.fileSizeKb} KB</span>
    <span class="pdf-extract-badge ${meta.extractionState}"
          id="bulk-slt-pdf-badge-${slot.id}-${activeTabData.id}">
      ${_pdfBadgeHtml(meta.extractionState, meta.badgeText)}
    </span>
  </div>
  <!-- thumbnail strip (up to 3 pages — narrower than single-doc) -->
  <div class="pdf-thumb-strip bulk-slt-pdf-thumbs"
       id="bulk-slt-pdf-thumbs-${slot.id}-${activeTabData.id}">
    ${thumbsHtml}  <!-- same structure: pdf-thumb-wrap + canvas/img + pdf-scan-beam -->
  </div>
</div>
```

**PDF-text override warning** (rendered when `pdfExtractedText` is set AND `text` is also set and differs):
```html
<div class="bulk-slt-pdf-override-warn">
  PDF text is active — manual text overridden
  <button onclick="bulkSlotUsePdfText(${slot.id},${activeTabData.id})">Use PDF</button>
  <button onclick="bulkSlotUseManualText(${slot.id},${activeTabData.id})">Keep manual</button>
</div>
```

**"Edit text" toggle** (always rendered when PDF is active):
```html
<button class="pdf-edit-toggle${activeTabData.textEditOpen ? ' open' : ''}"
        onclick="bulkSlotToggleEditText(${slot.id},${activeTabData.id})">
  <svg ...><polyline points="6 9 12 15 18 9"/></svg>
  <span>${activeTabData.textEditOpen ? 'Hide text ↑' : 'Edit text ↓'}</span>
</button>
<div class="pdf-text-collapse${activeTabData.textEditOpen ? ' open' : ''}">
  <textarea class="bulk-slot-doc-text" ...>${escHtml(activeTabData.text)}</textarea>
</div>
```

When no PDF is attached, the textarea is shown unconditionally as today.

### New CSS Classes

Add alongside the existing `.bulk-slot-*` styles:

```css
/* PDF upload button inside a slot tab */
.bulk-slot-pdf-btn {
  flex-shrink: 0;
  display: inline-flex; align-items: center; gap: .3rem;
  padding: .22rem .55rem;
  border: 1px solid var(--teal-500);
  border-radius: 4px;
  background: transparent;
  color: var(--teal-300);
  font-size: .72rem; font-weight: 600;
  cursor: pointer; transition: background .15s;
}
.bulk-slot-pdf-btn:hover { background: rgba(27,154,170,.1); }

/* Mini PDF panel inside a slot */
.bulk-slt-pdf-panel {
  margin: .5rem 0;
  padding: .5rem .75rem;
  background: rgba(255,255,255,.02);
  border: 1px solid var(--border);
  border-radius: 6px;
}
.bulk-slt-pdf-header {
  display: flex; align-items: center; gap: .75rem;
  margin-bottom: .5rem; flex-wrap: wrap;
}
.bulk-slt-pdf-filename {
  font-size: .78rem; font-weight: 600; color: var(--text);
  max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.bulk-slt-pdf-meta { font-size: .72rem; color: var(--faint); }
/* Thumbnail strip inside slot: smaller than single-doc strip */
.bulk-slt-pdf-thumbs { gap: .4rem; }
.bulk-slt-pdf-thumbs .pdf-thumb-wrap { width: 52px; }

/* Override warning banner */
.bulk-slt-pdf-override-warn {
  display: flex; align-items: center; gap: .5rem; flex-wrap: wrap;
  padding: .35rem .65rem;
  background: rgba(232,168,56,.08);
  border: 1px solid rgba(232,168,56,.3);
  border-radius: 5px;
  font-size: .75rem; color: #E8A838;
  margin-bottom: .4rem;
}
.bulk-slt-pdf-override-warn button {
  padding: .15rem .4rem; border-radius: 3px;
  font-size: .7rem; cursor: pointer; border: 1px solid currentColor;
  background: transparent; color: inherit;
}
```

### Text Precedence Logic

When the submit function reads `s.tabs` to build the JSON payload, the mapping is:

```javascript
documents: s.tabs
  .filter(t => (t.pdfExtractedText || t.text || '').trim())
  .map(t => ({
    filename: t.filename || 'document.txt',
    // PDF text wins if present; fall back to manual text
    raw_text: (t.pdfExtractedText || t.text || '').trim(),
  })),
```

The override warning is shown when `t.pdfExtractedText.trim()` and `t.text.trim()` are both non-empty and differ.

### `bulkSlotToggleEditText(slotId, tabId)`, `bulkSlotUsePdfText`, `bulkSlotUseManualText`

```javascript
function bulkSlotToggleEditText(slotId, tabId) {
  const slot = _bulkSlots.find(s => s.id === slotId);
  const tab  = slot && slot.tabs.find(t => t.id === tabId);
  if (!tab) return;
  tab.textEditOpen = !tab.textEditOpen;
  _bulkRenderAllSlots();
}

function bulkSlotUsePdfText(slotId, tabId) {
  const slot = _bulkSlots.find(s => s.id === slotId);
  const tab  = slot && slot.tabs.find(t => t.id === tabId);
  if (!tab) return;
  tab.text = tab.pdfExtractedText;
  tab.pdfExtractedText = '';   // conflict resolved — no more warning
  _bulkRenderAllSlots();
}

function bulkSlotUseManualText(slotId, tabId) {
  const slot = _bulkSlots.find(s => s.id === slotId);
  const tab  = slot && slot.tabs.find(t => t.id === tabId);
  if (!tab) return;
  tab.pdfFile = null;
  tab.pdfMeta = null;
  tab.pdfExtractedText = '';
  _bulkRenderAllSlots();
}
```

### Re-render strategy

`_bulkRenderAllSlots()` rebuilds the entire slot list from `_bulkSlots` state. This is already how the slot UI works. The PDF panel is conditionally rendered based on `tab.pdfMeta !== null`. The scan beam animation restarts naturally when the PDF thumbnail `<div class="pdf-thumb-wrap">` is written without `scan-done` (CSS handles it via `animation: pdf-beam-sweep 1.9s ... infinite`). `_bulkPdfSetScanDone` adds `scan-done` which stops the beam.

### No Backend Changes

PDF extraction is done entirely client-side via PDF.js (same as single-doc). The extracted text flows into `raw_text` in the JSON body. The backend sees `raw_text` strings regardless of how they were produced.

---

## Work Item 3 — Sustainability & Modules in Bulk Results

### Overview

Three layers to fix:
1. **Backend — analysis pipeline:** `_run_bulk_single_analysis` must run certification screening and sustainability rating.
2. **Backend — storage:** `bulk_shipments` DB table must store `sustainability_grade` and `sustainability_signals` for export.
3. **Frontend — display:** Results table, expand row, and CSV export must surface these fields.

The ZIP-of-PDFs export is **already fixed for free** once the analysis pipeline is fixed: `generate_report_from_dict(payload)` at `api/app.py:3588` reads `payload["sustainability_rating"]` (report_generator.py line 795) and renders a sustainability section in the PDF. No changes needed to report_generator.py.

---

### 3.1 Backend — `api/app.py:_run_bulk_single_analysis`

**File:** `api/app.py`  
**Function:** `_run_bulk_single_analysis` (~line 2799)  
**Insertion point:** After the pattern learning block (after line ~2910), before the `AnalyzeResponse(...)` constructor call (~line 2912).

#### Step A — Build `all_text`

After `docs` list is built (currently around line 2841), add:

```python
# Build concatenated document text for cert screening and sustainability
all_text_bulk = "\n\n".join(
    f"=== {d.get('filename', f'Document {i+1}')} ===\n{d.get('raw_text', d.get('text', '')).strip()}"
    for i, d in enumerate(documents_data)
    if d.get('raw_text', d.get('text', '')).strip()
)
```

#### Step B — Certification module screening (Stage 3.5)

Insert after pattern learning block, before `AnalyzeResponse` construction:

```python
# --- Stage 3.5: Certification module screening ---
cert_result_bulk: Optional[CertificationScreeningResult] = None
try:
    from portguard.agents.certification_screener import CertificationScreener
    enabled_modules_bulk: list[str] = (
        _module_config_db.get_enabled_modules(org_id)
        if _module_config_db is not None else []
    )
    screener_bulk = CertificationScreener(enabled_modules_bulk)
    cert_result_bulk = screener_bulk.screen(sd, all_text_bulk)
except Exception as _exc:
    logger.warning("CertificationScreener failed in bulk (non-fatal): %s", _exc)
```

#### Step C — Sustainability rating (Stage 5.5)

```python
# --- Stage 5.5: Sustainability rating ---
sustainability_bulk: Optional[SustainabilityRating] = None
try:
    from portguard.agents.sustainability_rater import SustainabilityRater
    rater_bulk = SustainabilityRater()
    sustainability_bulk = rater_bulk.rate(sd, cert_result_bulk, all_text_bulk)
except Exception as _exc:
    logger.warning("SustainabilityRater failed in bulk (non-fatal): %s", _exc)
```

#### Step D — Update `AnalyzeResponse(...)` constructor

Add four arguments to the existing call (~line 2912):

```python
analyze_response = AnalyzeResponse(
    # ... all existing args unchanged ...
    sustainability_rating=sustainability_bulk,
    module_findings=cert_result_bulk.findings if cert_result_bulk else [],
    active_modules_at_scan=cert_result_bulk.modules_run if cert_result_bulk else [],
    modules_triggered=cert_result_bulk.triggered_modules if cert_result_bulk else [],
)
```

These are the same four args already in the `analyze` endpoint's `AnalyzeResponse(...)` call. The `AnalyzeResponse` model already has all four fields defined (lines 1184–1201).

**No changes** to the `_record_shipment_bg()` call below — it operates on `sd` and already records to PatternDB independently.

---

### 3.2 Backend — DB Schema Migration

**File:** `portguard/pattern_db.py`

Add migration `"008_bulk_shipments_sustainability"` to the `_MIGRATIONS` list after migration `"007_bulk_modules_snapshot"` (~line 623):

```python
(
    "008_bulk_shipments_sustainability",
    [
        "ALTER TABLE bulk_shipments ADD COLUMN sustainability_grade TEXT;",
        "ALTER TABLE bulk_shipments ADD COLUMN sustainability_signals TEXT;",
    ],
),
```

`sustainability_signals` stores the rating's `signals` list as a pipe-separated string (e.g., `"HIGH country risk: Vietnam|No sustainability certifications found"`). This is consistent with how `shipment_history.sustainability_signals` is stored (migration 006, same column, same convention).

**File:** `portguard/bulk_processor.py`

Update `_BULK_SCHEMA_SQL` (line 135) to include the columns for fresh installs (the migration handles upgrades; the `CREATE TABLE IF NOT EXISTS` handles new installs):

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
    result_json     TEXT,
    sustainability_grade    TEXT,        ← NEW
    sustainability_signals  TEXT,        ← NEW
    error_message   TEXT,
    processed_at    TEXT,
    UNIQUE(batch_id, shipment_ref),
    FOREIGN KEY(batch_id) REFERENCES bulk_batches(batch_id)
);
```

---

### 3.3 Backend — `bulk_processor.py:_store_shipment_result`

**File:** `portguard/bulk_processor.py`  
**Function:** `_store_shipment_result` (line 734)

After the existing field extractions (~line 749), add:

```python
# Extract sustainability grade and signals for queryable storage
sustainability_grade: Optional[str] = None
sustainability_signals: Optional[str] = None
sr = result.get("sustainability_rating")
if sr and isinstance(sr, dict):
    sustainability_grade = sr.get("grade")
    signals_list = sr.get("signals") or []
    if signals_list:
        sustainability_signals = "|".join(str(s) for s in signals_list[:20])
```

Update the `UPDATE bulk_shipments SET ...` statement to include:

```sql
sustainability_grade   = :sustainability_grade,
sustainability_signals = :sustainability_signals,
```

And add both to the params dict:
```python
"sustainability_grade":   sustainability_grade,
"sustainability_signals": sustainability_signals,
```

---

### 3.4 Backend — `bulk_processor.py:get_export_rows`

**File:** `portguard/bulk_processor.py`  
**Function:** `get_export_rows` (line 596)

Update the SELECT query to include the new columns:

```sql
SELECT shipment_ref, status, decision, risk_score, risk_level,
       n_findings, top_finding, analysis_id, error_message, processed_at,
       sustainability_grade, sustainability_signals    ← NEW
FROM bulk_shipments
WHERE batch_id = :id
ORDER BY ...
```

`get_export_rows` returns `[dict(r) for r in rows]`, so the new columns are automatically included.

---

### 3.5 Backend — `api/app.py:bulk_export_csv`

**File:** `api/app.py`  
**Function:** `bulk_export_csv` (line 3442)

Update `_CSV_HEADERS` (line 3491):

```python
_CSV_HEADERS = [
    "shipment_ref", "status", "decision", "risk_score", "risk_level",
    "n_findings", "top_finding", "analysis_id",
    "sustainability_grade", "sustainability_signals",   ← NEW (two columns)
    "error_message", "processed_at",
]
```

`sustainability_signals` is the pipe-separated string from the DB. No additional processing needed; `extrasaction="ignore"` on the `DictWriter` means extra keys from the row dict are silently dropped, but the new columns will be populated because they exist in the row dict from `get_export_rows`.

---

### 3.6 Frontend — `demo.html`

#### A. Update `_bulkAllResults` mapping (~line 8627)

Add sustainability fields when building the results array:

```javascript
_bulkAllResults = (data.shipments || []).map(s => ({
  ref:          s.ref,
  decision:     s.decision   || 'PENDING',
  risk_score:   s.risk_score != null ? +s.risk_score : null,
  n_findings:   s.n_findings || 0,
  top_finding:  s.top_finding || '',
  status:       s.status,
  analysis_id:  s.analysis_id,
  findings:     (s.full_result && s.full_result.explanations) || [],
  // Sustainability — sourced from result DB column (fast path) or full_result
  sustainability_grade:   s.sustainability_grade
    || (s.full_result && s.full_result.sustainability_rating && s.full_result.sustainability_rating.grade)
    || null,
  sustainability_signals: s.sustainability_signals
    ? s.sustainability_signals.split('|').filter(Boolean)
    : (s.full_result && s.full_result.sustainability_rating && s.full_result.sustainability_rating.signals) || [],
  module_findings: (s.full_result && s.full_result.module_findings) || [],
}));
```

Note: `s.sustainability_grade` is available from the DB column via the `/results` endpoint once the backend stores it. The `full_result` fallback handles in-flight results where the DB column may not yet be populated.

#### B. Add "Sustainability" column to results table HTML (~line 3506)

Update the `<thead>` row to add a Sustainability header:

```html
<thead>
  <tr>
    <th onclick="bulkSort('ref')">Reference ID <span class="sort-icon" id="bsort-ref"></span></th>
    <th onclick="bulkSort('decision')">Decision <span class="sort-icon" id="bsort-decision"></span></th>
    <th onclick="bulkSort('risk_score')">Risk Score <span class="sort-icon" id="bsort-risk_score">↓</span></th>
    <th onclick="bulkSort('sustainability_grade')">Sustainability <span class="sort-icon" id="bsort-sustainability_grade"></span></th>  ← NEW
    <th onclick="bulkSort('n_findings')">Findings <span class="sort-icon" id="bsort-n_findings"></span></th>
    <th>Top Finding</th>
    <th>Actions</th>
  </tr>
</thead>
```

Update `colspan="6"` in the "no results" empty row to `colspan="7"` (line 8739).

#### C. Update `_bulkRenderTable` to render the sustainability badge (~line 8742)

Add a sustainability cell to each row:

```javascript
const sGrade = r.sustainability_grade;
const sGradeKey = sGrade === 'N/A' ? 'NA' : (sGrade || null);
const sCell = sGradeKey
  ? `<td><span class="sustain-badge sustain-badge-${sGradeKey}">${sGrade}</span></td>`
  : `<td style="color:var(--faint);font-size:.8rem">—</td>`;
```

Insert `${sCell}` after the risk score cell and before the n_findings cell in the row template.

#### D. Add sustainability sort support

In the sort handler (`bulkSort` function), add `'sustainability_grade'` to `valid_sorts`. Sort alphabetically (A < B < C < D < N/A).

#### E. Update expand row to show sustainability signals and module findings

In `_bulkRenderTable` expand row section (~line 8783), update the `expRow.innerHTML`:

```javascript
// Existing findings section
const findings = r.findings.length ? ... : ...;

// Sustainability section (new)
let sustainHtml = '';
if (r.sustainability_grade) {
  const gradeKey = r.sustainability_grade === 'N/A' ? 'NA' : r.sustainability_grade;
  sustainHtml = `
    <div style="margin-top:.75rem">
      <div style="font-size:.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--faint);margin-bottom:.35rem">
        Sustainability
      </div>
      <span class="sustain-badge sustain-badge-${gradeKey}" style="margin-bottom:.4rem;display:inline-block">
        ${r.sustainability_grade}
      </span>
      ${r.sustainability_signals.length
        ? '<ul class="sustain-signals">' + r.sustainability_signals.map(s => `<li>${escHtml(s)}</li>`).join('') + '</ul>'
        : ''}
    </div>`;
}

// Module findings section (new)
let modulesHtml = '';
if (r.module_findings && r.module_findings.length) {
  const triggered = r.module_findings.filter(f => f.triggered);
  if (triggered.length) {
    modulesHtml = `
      <div style="margin-top:.6rem">
        <div style="font-size:.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--faint);margin-bottom:.35rem">
          Module Findings
        </div>
        ${triggered.map(f => `<div class="bulk-inline-finding">${escHtml(f.module_name)}: ${escHtml(f.message)}</div>`).join('')}
      </div>`;
  }
}

expRow.innerHTML = `<td colspan="7"><div class="bulk-inline-analysis">
  <div style="font-size:.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--faint);margin-bottom:.5rem">Analysis Findings</div>
  <div class="bulk-inline-findings">${findings}</div>
  ${sustainHtml}
  ${modulesHtml}
</div></td>`;
```

#### F. Update the `bulkSort` function for sustainability

The new sort column `sustainability_grade` needs an alphabetical sort (it's a grade letter). The existing sort logic for non-`risk_score` columns uses string comparison (`s.get(sort) || ""`), which works correctly for A/B/C/D/N/A alphabetically. No special logic needed.

---

## Execution Order

The three work items are independent but should be implemented in this order to allow incremental testing:

```
1. Work Item 1  (frontend-only, 4 lines)          → test ZIP and CSV uploads end-to-end
2. Work Item 3  (backend + frontend)               → test sustainability in results
3. Work Item 2  (frontend PDF in manual entry)     → test PDF extraction in manual slots
```

Work Item 1 must land before 2 or 3 are useful to test manually.

---

## Non-Breaking Guarantees

| Component | Change | Breaking? |
|-----------|--------|-----------|
| `POST /api/v1/analyze` | None | No |
| `POST /api/v1/analyze` (file upload) | None | No |
| `GET /api/v1/analyze/bulk/{id}/results` | `sustainability_grade`/`sustainability_signals` added to shipment rows | Additive — no |
| `GET /api/v1/analyze/bulk/{id}/export/csv` | Two new columns | Additive — no |
| `GET /api/v1/analyze/bulk/{id}/export/zip` | PDF now includes sustainability section | Additive — no |
| `bulk_shipments` schema | Two new nullable TEXT columns | Additive, migration-safe — no |
| Dashboard analytics | Unchanged | No |
| Pattern learning | Unchanged | No |
| Single-doc analyze tab | Unchanged | No |

---

## File Change Summary

| File | Work Item | Changes |
|------|-----------|---------|
| `demo.html` | 1 | 4 lines in `_bulkSubmit`: rename FormData keys, add `input_method` |
| `demo.html` | 2 | New CSS (`.bulk-slot-pdf-btn`, `.bulk-slt-pdf-*`), updated slot template, 5 new JS functions (`_bulkPdfHandleUpload`, `_bulkPdfExtractText`, `_bulkPdfSetScanDone`, `bulkSlotPdfChosen`, `bulkSlotToggleEditText`, `bulkSlotUsePdfText`, `bulkSlotUseManualText`), updated `_bulkSubmit` payload builder |
| `demo.html` | 3 | Updated `_bulkAllResults` mapping, `<thead>` HTML, `_bulkRenderTable` (sustainability cell + expand row), `bulkSort` update |
| `api/app.py` | 3 | `_run_bulk_single_analysis`: add ~25 lines (all_text, cert_screener, sustainability_rater, updated AnalyzeResponse call); `bulk_export_csv`: 2 new `_CSV_HEADERS` entries |
| `portguard/bulk_processor.py` | 3 | `_BULK_SCHEMA_SQL`: 2 new columns in CREATE TABLE; `_store_shipment_result`: extract + store grade/signals; `get_export_rows`: 2 new columns in SELECT |
| `portguard/pattern_db.py` | 3 | Migration `008_bulk_shipments_sustainability`: 2 ALTER TABLE statements |
