# PortGuard Bulk Upload Sprint Plan
**Date:** 2026-04-28  
**Based on:** `docs/bulk_sprint_audit.md` + full source re-read  
**Status:** Pre-implementation. No code has been written.

---

## Work Item 1: Bulk Upload Processes All 50, No Pending Rows

### 1.1 Root Cause

The backend is correct and already guarantees zero pending rows in the final output. `BulkProcessor.process_batch()` (`portguard/bulk_processor.py:338`) uses `asyncio.gather(*[_process_one(s) for s in shipments])` — the gather awaits every coroutine before calling `_mark_batch_complete()`. Inside each `_process_one()`, every code path (success, timeout, exception) routes to either `_store_shipment_result()` or `_store_shipment_error()`. Both functions write the shipment row with status `COMPLETE` or `ERROR` and atomically increment `processed_count`. The batch status becomes `COMPLETE` only after all coroutines return — which guarantees that by the time `status === 'COMPLETE'` is observable via the `/status` endpoint, every shipment has a non-PENDING status in the database.

The "pending rows" symptom is entirely a **frontend stats reading bug**. `_bulkLoadResults()` (`demo.html:9565–9589`) reads from the status endpoint's response shape (`data.decisions.APPROVE`, `data.total_shipments`, `data.average_risk_score`, `data.highest_risk`) against the results endpoint's response, which puts these values at `data.summary.approved`, `data.summary.total`, `data.summary.avg_risk_score`, and `data.summary.highest_risk`. The result: all six decision badges show `0` and the highest-risk bar never appears, making the entire results panel look broken even though the underlying data is correct.

---

### 1.2 Architecture choice: Polling (keep current mechanism)

**Polling is the right choice.** Justification:

- **SSE** would require a new `StreamingResponse` endpoint, long-lived HTTP connections, reconnect logic on the client, and FastAPI's BackgroundTask model doesn't integrate naturally with SSE (the SSE generator and the background analysis would need to share state via a queue or database). The polling mechanism already delivers real-time progress via `d.processed / d.total` from the status endpoint — there is no perceptible difference in UX.
- **Single blocking response** is not viable. A 50-shipment batch with 5 concurrent workers takes at minimum `ceil(50/5) × avg_analysis_time` seconds. At even 8s per shipment that is `10 × 8 = 80s` — well beyond the default browser/proxy/Render HTTP timeout.
- **Polling** is already implemented, already returns accurate `processed / total` counts, correctly transitions to COMPLETE only after all shipments finish, and the fix is a 12-line change to `_bulkLoadResults()`.

---

### 1.3 Files to change

| File | Lines | Change |
|---|---|---|
| `demo.html` | 9565–9589 | Fix `_bulkLoadResults()` to read from `data.summary` |

No backend changes are needed. The backend is already correct.

---

### 1.4 Step-by-step implementation

**Step 1: Fix `_bulkLoadResults()` stats block (`demo.html:9564–9590`).**

Replace the current block:
```javascript
// Summary stats
const decisions = data.decisions || {};
document.getElementById('bstat-total').textContent    = data.total_shipments || _bulkAllResults.length;
document.getElementById('bstat-approved').textContent = decisions.APPROVE || 0;
document.getElementById('bstat-flagged').textContent  = (decisions.FLAG_FOR_INSPECTION || 0) + (decisions.REJECT || 0);
document.getElementById('bstat-review').textContent   = decisions.REVIEW_RECOMMENDED || 0;
document.getElementById('bstat-info').textContent     = decisions.REQUEST_MORE_INFORMATION || 0;
document.getElementById('bstat-errors').textContent   = decisions.ERROR || 0;

const avgRisk = data.average_risk_score;
const avgEl   = document.getElementById('bstat-avg-risk');
if (avgRisk != null) {
  avgEl.textContent = Math.round(avgRisk * 100);
  avgEl.style.color = _bulkRiskColor(avgRisk);
} else {
  avgEl.textContent = '—';
  avgEl.style.color = '';
}

// Highest risk
if (data.highest_risk) {
  const hr = data.highest_risk;
```

With:
```javascript
// Summary stats — results endpoint uses data.summary (not data.decisions)
const s = data.summary || {};
document.getElementById('bstat-total').textContent    = s.total || _bulkAllResults.length;
document.getElementById('bstat-approved').textContent = s.approved || 0;
document.getElementById('bstat-flagged').textContent  = (s.flagged || 0) + (s.rejected || 0);
document.getElementById('bstat-review').textContent   = s.review_recommended || 0;
document.getElementById('bstat-info').textContent     = s.needs_info || 0;
document.getElementById('bstat-errors').textContent   = s.errors || 0;

const avgRisk = s.avg_risk_score;
const avgEl   = document.getElementById('bstat-avg-risk');
if (avgRisk != null) {
  avgEl.textContent = Math.round(avgRisk * 100);
  avgEl.style.color = _bulkRiskColor(avgRisk);
} else {
  avgEl.textContent = '—';
  avgEl.style.color = '';
}

// Highest risk
if (s.highest_risk) {
  const hr = s.highest_risk;
```

**Step 2: Fix the `data.highest_risk` reference two lines later (`demo.html:9584–9589`).**

The current code has:
```javascript
if (data.highest_risk) {
  const hr = data.highest_risk;
```

This needs to read `s.highest_risk` (already changed in Step 1 since `s` is defined from `data.summary`). Verify the `hr.ref` and `hr.risk_score` property names match what `get_batch_results()` returns (`portguard/bulk_processor.py:546–549`): `{ "ref": ..., "risk_score": ... }`. They match — no change needed to the inner property reads.

---

### 1.5 Exact response shape guarantee

The results endpoint returns (from `portguard/bulk_processor.py:580–602`):
```json
{
  "batch_id": "uuid",
  "status": "COMPLETE",
  "input_method": "MANUAL",
  "created_at": "...",
  "completed_at": "...",
  "summary": {
    "total": 50,
    "processed": 50,
    "approved": 32,
    "review_recommended": 8,
    "flagged": 6,
    "needs_info": 2,
    "rejected": 1,
    "errors": 1,
    "avg_risk_score": 0.3412,
    "highest_risk": { "ref": "SHP-041", "risk_score": 0.92 },
    "processing_time_seconds": 78.4
  },
  "shipments": [
    {
      "ref": "SHP-041",
      "status": "COMPLETE",
      "decision": "FLAG_FOR_INSPECTION",
      "risk_score": 0.92,
      "risk_level": "CRITICAL",
      "n_findings": 7,
      "top_finding": "Declared value inconsistent with weight...",
      "analysis_id": "uuid",
      "error_message": null,
      "processed_at": "...",
      "sustainability_grade": "C",
      "sustainability_signals": "FSC_COC_CERTIFIED|...",
      "active_modules_snapshot": "FSC_COC|WRAP|...",
      "full_result": { ... }
    },
    ...
  ]
}
```

Every shipment has `status` of either `"COMPLETE"` or `"ERROR"`. `"PENDING"` and `"PROCESSING"` never appear in the results endpoint response because the backend only marks the batch `COMPLETE` after all shipments have been processed.

---

### 1.6 How per-row errors are isolated

Error isolation is already implemented in `portguard/bulk_processor.py:393–400`:

```python
except asyncio.TimeoutError:
    self._store_shipment_error(batch_id, ref,
        f"Analysis timed out after {int(SHIPMENT_TIMEOUT_SECONDS)} seconds.")
except Exception as exc:
    self._store_shipment_error(batch_id, ref, str(exc))
```

Each `_process_one()` coroutine has its own try/except. A failure in any shipment only writes that shipment to `ERROR` and increments `error_count` — it does not cancel other coroutines in the gather.

---

### 1.7 Progress indicator (already correct)

During polling, `_bulkPoll()` (`demo.html:9453–9457`) reads:
```javascript
const processed = d.processed || 0;
const tot       = d.total || total || 1;
const pct       = Math.round((processed / tot) * 100);
_bulkSetProgress(processed, tot, pct);
```

`_bulkSetProgress()` renders `"${processed} of ${total} shipments complete"`. The `d.processed` value comes from `batch_row["processed_count"]` which is atomically incremented on every `_store_shipment_result()` and `_store_shipment_error()` call. This is genuinely real-time — "Analyzing 23 of 50…" reflects actual DB state. No change needed.

---

### 1.8 Timeout handling

Per-shipment timeout is 30 seconds (`SHIPMENT_TIMEOUT_SECONDS` in `bulk_processor.py`). When `asyncio.wait_for()` raises `TimeoutError`, `_store_shipment_error()` is called with the message `"Analysis timed out after 30 seconds."`. The shipment's `status` becomes `ERROR`, `decision` stays null, `error_message` is set, and `error_count` is incremented. The results endpoint includes timed-out rows in the `errors` count. The frontend shows them in the Errors badge and the table (decision badge reads `PENDING` → `r.decision || 'PENDING'` — but since `decision` is null for ERRORs, a timed-out row shows "PENDING" badge in the results table).

**This is a secondary bug:** timed-out rows have `status: 'ERROR'` but `decision: null`, so they render with a "PENDING" badge in `_bulkRenderTable()` at `demo.html:9669`: `const dec = r.decision || 'PENDING'`. Fix: change to `const dec = (r.decision || (r.status === 'ERROR' ? 'ERROR' : 'PENDING'))`.

Add this to Step 1 as Step 1b:

**Step 1b: Fix timed-out row badge in `_bulkRenderTable()` (`demo.html:9669`).**

Change:
```javascript
const dec   = r.decision || 'PENDING';
```
To:
```javascript
const dec   = r.decision || (r.status === 'ERROR' ? 'ERROR' : 'PENDING');
```

---

### 1.9 What must NOT be touched

- `BulkProcessor.process_batch()` — backend is correct, do not change
- `_bulkPoll()` — polling mechanism is correct, do not change
- `_bulkStartPolling()` — correct, do not change
- `_bulkAddLiveFeedItem()` — correct, do not change
- `_bulkAllResults` mapping block (`demo.html:9539–9562`) — reads from `data.shipments`, correct, do not change
- All status endpoint code in `api/app.py` — do not change
- All results endpoint code in `api/app.py` — do not change

---

### 1.10 Test cases

1. Submit a 3-shipment manual batch. After COMPLETE: all stat badges show non-dash values; Approved + Review + Flagged + Info + Rejected + Errors = Total.
2. Submit a batch where one shipment has no text (will error). After COMPLETE: Errors badge shows 1; that row's badge in table shows "Error" (not "PENDING").
3. Submit a 10-shipment batch. During processing, progress text reads "X of 10 shipments complete" with X incrementing. After COMPLETE: avg risk shows a number, not "—".
4. If highest_risk exists: `bulk-highest-risk-bar` is visible after load, shows the correct ref and score.
5. Refresh the page and navigate back — no regression on existing single-analyze functionality.

---

## Work Item 2: Share Link Works and Navigates to Result

### 2.1 Root Cause

`bulkShareLink()` at `demo.html:9817–9820` generates a URL with `?batch=<batch_id>` and copies it to the clipboard, but the page has no `DOMContentLoaded` listener and no `URLSearchParams` check. The page always starts by calling `showAuthOverlay()` at `demo.html:9873`. There is no code that reads `?batch=` from the URL at any point in the page's lifecycle. A user who opens the share link is shown the login screen, and after logging in, is dropped at the default analyze tab with no batch loaded.

---

### 2.2 Data the share link encodes

The link encodes only the `batch_id` (a UUID generated server-side). The `batch_id` is org-scoped: `get_batch_results()` and `get_batch_status()` both verify `organization_id` matches. An org-B user who receives a link for an org-A batch will get a 404 from the results endpoint, which the frontend should display as a "Batch not found or access denied" error.

No additional data needs to be encoded. The batch_id is sufficient to fetch all results, including status, summary, and per-shipment details.

---

### 2.3 URL structure

Keep the existing format: `?batch=<batch_id>` appended to the page's base URL. This is already what `bulkShareLink()` generates. No path routing changes needed (the app is a single-page app with no server-side routing).

---

### 2.4 Backend changes required

None. The existing endpoints are sufficient:
- `GET /api/v1/analyze/bulk/{batch_id}/status` — verify the batch is COMPLETE
- `GET /api/v1/analyze/bulk/{batch_id}/results` — load the full results

The backend already enforces org-scoping on both endpoints.

---

### 2.5 Files to change

| File | Lines | Change |
|---|---|---|
| `demo.html` | ~9868–9874 (init block) | Add URLSearchParams check before `showAuthOverlay()` |
| `demo.html` | ~3852–3865 (`hideAuthOverlay`) | Add post-login batch restore check |
| `demo.html` | ~9817–9830 (`bulkShareLink`) | Add success toast confirmation |
| `demo.html` | ~8682–8690 (state variables block) | Add `_pendingBatchId` variable |

---

### 2.6 Step-by-step implementation

**Step 1: Declare `_pendingBatchId` in the state variables block (`demo.html` near `_bulkBatchId` declaration, around line 8682).**

Add alongside the existing bulk state variables:
```javascript
let _pendingBatchId = null;   // set from ?batch= URL param on page load
```

**Step 2: Read `?batch=` from URL before calling `showAuthOverlay()` (`demo.html:9873`).**

Replace:
```javascript
showAuthOverlay();
```
With:
```javascript
const _urlBatch = new URLSearchParams(window.location.search).get('batch');
if (_urlBatch) _pendingBatchId = _urlBatch;
showAuthOverlay();
```

**Step 3: After successful login, check `_pendingBatchId` and restore the batch (`demo.html:9934–9935`, inside `hideAuthOverlay()` or immediately after it in the login success handler).**

In `hideAuthOverlay()` at `demo.html:3852`, after the final line that sets the org name, add:
```javascript
// Restore share-link batch if present
if (_pendingBatchId) {
  const batchToLoad = _pendingBatchId;
  _pendingBatchId = null;
  showSection('bulk');
  _bulkLoadSharedBatch(batchToLoad);
}
```

**Step 4: Implement `_bulkLoadSharedBatch(batchId)` as a new function near `_bulkLoadResults()` (after `demo.html:9606`).**

```javascript
async function _bulkLoadSharedBatch(batchId) {
  // Show the bulk section in a loading state
  document.getElementById('bulk-upload-section').style.display = 'none';
  document.getElementById('bulk-results-screen').classList.remove('visible');
  document.getElementById('bulk-progress-screen').classList.add('visible');
  document.getElementById('bulk-progress-text').textContent = 'Loading shared batch…';

  try {
    // Verify batch exists and is complete
    const statusRes = await fetch(
      apiUrl() + '/api/v1/analyze/bulk/' + batchId + '/status',
      { headers: _authToken ? { Authorization: 'Bearer ' + _authToken } : {} }
    );
    if (statusRes.status === 401) { handle401(); return; }
    if (statusRes.status === 404) {
      _bulkShowProgressError('Batch not found or access denied.');
      return;
    }
    if (!statusRes.ok) {
      _bulkShowProgressError('Could not load batch — server error.');
      return;
    }
    const statusData = await statusRes.json();

    if (statusData.status === 'COMPLETE' || statusData.status === 'FAILED') {
      // Load results directly
      _bulkBatchId = batchId;
      await _bulkLoadResults();
    } else {
      // Batch still processing — start polling
      _bulkBatchId = batchId;
      _bulkSetProgress(statusData.processed || 0, statusData.total || 0, 0);
      _bulkStartPolling(statusData.total || 0);
    }
  } catch (e) {
    _bulkShowProgressError('Network error: ' + e.message);
  }
}
```

**Step 5: Update `bulkShareLink()` to show a toast confirmation (`demo.html:9817–9830`).**

The current `bulkShareLink()` generates the URL and copies it but has no visible confirmation after copying. Add a toast (using the existing `_showToast` mechanism if available, otherwise a simple inline message). Examine the existing toast pattern in the codebase and replicate it. The link URL itself remains `window.location.href.split('?')[0] + '?batch=' + _bulkBatchId`.

---

### 2.7 Auth flow

The recipient needs to be logged in. The flow:

1. Recipient opens `https://app.example.com/?batch=abc-123`
2. `_pendingBatchId = 'abc-123'` stored in memory
3. `showAuthOverlay()` shows the login screen
4. Recipient logs in with their credentials
5. `hideAuthOverlay()` is called → detects `_pendingBatchId` → calls `showSection('bulk')` → calls `_bulkLoadSharedBatch('abc-123')`
6. Backend validates org ownership: if the batch belongs to a different org, `/status` returns 404 → error shown
7. If valid: results load and the full batch results screen is shown

No `sessionStorage` or `localStorage` is needed — the batch ID lives in `_pendingBatchId` in memory for the lifetime of the page load. If the user refreshes (e.g., hard reload after login), the `?batch=` URL param is re-read from the URL and the flow repeats from step 1.

---

### 2.8 What must NOT be touched

- `_bulkLoadResults()` — no changes (called from `_bulkLoadSharedBatch`)
- `_bulkPoll()` / `_bulkStartPolling()` — no changes (reused for in-progress batches)
- `bulkShareLink()` URL generation — keep the same `?batch=<id>` format; only add the toast
- All backend endpoints — no changes

---

### 2.9 Test cases

1. While logged in: submit a batch, wait for results, click "Share". Verify clipboard URL contains `?batch=<uuid>`.
2. Open the share link in a new browser tab (not logged in). Verify login form appears. Log in. Verify results for the correct batch load automatically, bulk tab is active.
3. Open the share link for a batch belonging to a different org. Log in as org-B. Verify error message "Batch not found or access denied" is shown.
4. Open the share link for an in-progress batch. Verify polling starts and the live progress screen is shown, then transitions to results when COMPLETE.
5. Open the share link with an invalid batch ID (e.g., `?batch=notreal`). Verify the 404 error is shown, not a JS crash.

---

## Work Item 3: PDF Preview Not Clipped in Manual Entry Rows

### 3.1 Root Cause

The PDF preview canvas/image in bulk manual entry rows is visually clipped to a narrow vertical strip. The cause is a mismatch between the rendered image width and the container's forced width:

1. `_bulkPdfRenderThumb()` at `demo.html:9213` renders each page with `page.getViewport({ scale: 0.22 })`. For a standard US letter page (612pt wide), this produces a canvas 135px wide and 174px tall.
2. After rendering, the canvas element is replaced with an `<img class="pdf-thumb-canvas">` (`demo.html:9163–9167`). The `pdf-thumb-canvas` CSS rule (`demo.html:1579–1582`) sets `max-width: 120px; height: auto`. The img therefore renders at its natural constrained width — up to 120px — with proportional height (~157px for a letter page).
3. The img is inside a `.pdf-thumb-wrap` element. The shared `.pdf-thumb-wrap` CSS (`demo.html:1570–1577`) includes `overflow: hidden`. A bulk-specific override at `demo.html:2136` forces `.bulk-slt-pdf-thumbs .pdf-thumb-wrap { width: 48px }`.
4. Result: the img is ~120px wide but the container is 48px wide with `overflow: hidden`. **Only the leftmost 48px (40%) of the page image is visible.** The thumbnail appears as a narrow vertical slice of the page's left margin.

The scan beam overshoot is a secondary issue: the beam animation keyframe uses `translateY(calc(var(--thumb-h, 200px) + 6px))` (line 1652). The `--thumb-h` variable is never set for bulk slot wraps, so it defaults to 200px. The actual canvas height is ~157–174px. The beam travels 206px when only ~165px are visible. Since `overflow: hidden` clips this, the animation looks correct but the timing is slightly off (the beam "disappears" early and re-enters from the top before a full sweep should complete).

---

### 3.2 Files to change

| File | Lines | Change |
|---|---|---|
| `demo.html` | 2136 | Remove the `width: 48px` override OR change to `width: auto` |
| `demo.html` | 2118–2124 (`.bulk-slt-pdf-panel`) | Add `max-height: 600px; overflow-y: auto` |
| `demo.html` | 9213–9228 (`_bulkPdfRenderThumb`) | Set `--thumb-h` on the wrap element after render |

---

### 3.3 Step-by-step implementation

**Step 1: Remove the 48px width constraint (`demo.html:2136`).**

Change:
```css
.bulk-slt-pdf-thumbs .pdf-thumb-wrap { width: 48px; }
```
To:
```css
.bulk-slt-pdf-thumbs .pdf-thumb-wrap { width: auto; }
```

This removes the forced clipping. The wrap will size to its content — the img with `max-width: 120px` — resulting in a properly proportioned thumbnail (up to 120×157px).

**Step 2: Add height cap to the PDF panel (`demo.html:2118–2124`).**

Change the `.bulk-slt-pdf-panel` rule from:
```css
.bulk-slt-pdf-panel {
  margin: .4rem 0 .35rem;
  padding: .45rem .6rem;
  background: rgba(255,255,255,.02);
  border: 1px solid var(--border);
  border-radius: 6px;
}
```
To:
```css
.bulk-slt-pdf-panel {
  margin: .4rem 0 .35rem;
  padding: .45rem .6rem;
  background: rgba(255,255,255,.02);
  border: 1px solid var(--border);
  border-radius: 6px;
  max-height: 600px;
  overflow-y: auto;
}
```

This caps the entire panel (header + thumbnails + toggle + textarea) at 600px with internal scrolling if it ever exceeds that height. For typical PDFs at scale 0.22 the panel will be ~250–350px tall — well under the cap.

**Step 3: Set `--thumb-h` per wrap after rendering (`demo.html:9213–9228`, inside `_bulkPdfRenderThumb()`).**

After setting `canvas.width` and `canvas.height` and before returning, set the CSS variable on the wrap element:

```javascript
async function _bulkPdfRenderThumb(pdfDoc, pageNum, slotId, tabId) {
  const page     = await pdfDoc.getPage(pageNum);
  const viewport = page.getViewport({ scale: 0.22 });
  const canvas   = document.getElementById(`bulk-pdf-canvas-${slotId}-${tabId}-${pageNum}`);
  if (!canvas) {
    const offscreen = document.createElement('canvas');
    offscreen.width  = viewport.width;
    offscreen.height = viewport.height;
    await page.render({ canvasContext: offscreen.getContext('2d'), viewport }).promise;
    return offscreen.toDataURL('image/jpeg', 0.75);
  }
  canvas.width  = viewport.width;
  canvas.height = viewport.height;
  // Set --thumb-h so the scan beam travels exactly the canvas height
  const wrapEl = document.getElementById(`bulk-pdf-thumb-wrap-${slotId}-${tabId}-${pageNum}`);
  if (wrapEl) wrapEl.style.setProperty('--thumb-h', Math.round(viewport.height) + 'px');
  await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise;
  return canvas.toDataURL('image/jpeg', 0.75);
}
```

Note: `--thumb-h` is set on the `pdf-thumb-wrap` element. The `@keyframes pdf-beam-sweep` rule reads `var(--thumb-h, 200px)`. When set on the containing element, the CSS variable cascades to the `.pdf-scan-beam` pseudoelements inside it. This is standard CSS custom property inheritance.

**Step 4: Verify the row card expands.**

The `.bulk-slot` container has no `max-height` or `overflow: hidden` at the row level (it uses `flex-direction: column`). Adding the PDF panel (with its natural height) inside the slot will simply expand the slot card vertically. No additional changes are needed to the `.bulk-slot` CSS.

---

### 3.4 Element-by-element change list

| Selector | Property | Before | After |
|---|---|---|---|
| `.bulk-slt-pdf-thumbs .pdf-thumb-wrap` | `width` | `48px` | `auto` |
| `.bulk-slt-pdf-panel` | `max-height` | (not set) | `600px` |
| `.bulk-slt-pdf-panel` | `overflow-y` | (not set) | `auto` |
| `.pdf-thumb-wrap` (via JS `style.setProperty`) | `--thumb-h` | (unset, defaults to 200px) | `${viewport.height}px` per wrap |

No other elements need CSS changes.

---

### 3.5 What must NOT be touched

- `.pdf-thumb-wrap` base CSS rule (`demo.html:1570–1577`) — shared with single-doc, do not modify
- `.pdf-thumb-canvas` base CSS rule (`demo.html:1579–1582`) — shared with single-doc, do not modify
- `.pdf-thumb-strip` base CSS rule (`demo.html:1561–1566`) — single-doc strip, do not modify
- `.pdf-scan-beam` CSS and keyframes (`demo.html:1593–1653`) — shared, do not modify
- `_pdfRenderThumb()` at `demo.html:7151` — single-doc render function, do not touch
- `_pdfServerExtract()` — single-doc server extraction, do not touch
- Single-doc PDF panel CSS (`.pdf-preview-panel`, `.pdf-meta-*`) — do not touch

---

### 3.6 Test cases

1. Upload a portrait PDF to a bulk manual entry row. Verify the thumbnail shows the full page (not a narrow left strip). The thumbnail should be approximately 120px wide and ~157px tall.
2. Upload a landscape PDF. Verify the thumbnail shows the full landscape page, wider than tall.
3. Verify the scan beam sweeps the full height of the thumbnail (no visible gap between where the beam disappears and the bottom edge of the thumbnail).
4. Upload a PDF to 3 different bulk slots simultaneously. Verify each slot shows its own full thumbnail with its own correctly-calibrated beam.
5. Open the single-document analyze tab and upload a PDF. Verify the single-doc preview is completely unchanged — same appearance as before.
6. Upload a PDF to a bulk slot where the textarea already has manual text. Verify the override warning appears AND the thumbnail renders at full size (not clipped).

---

## Work Item 4: PDF Animation + Module Toggles in Manual Rows

### 4.1 Root Cause

This work item has no single failure — it bundles four independent concerns that need to be designed correctly. Two are already correctly implemented; two require the fixes from Work Item 3.

---

### 4.2 Scan beam scoped per row independently

**Current state:** The scan beam uses `var(--thumb-h, 200px)` for its animation endpoint. The variable is inherited from the element tree. Currently, `--thumb-h` is never set anywhere for bulk slot wraps, so all beams use the 200px default. All beams in all rows animate identically.

**After Work Item 3 Step 3:** `--thumb-h` is set via `wrapEl.style.setProperty('--thumb-h', ...)` on the individual `.pdf-thumb-wrap` element for each page of each upload. Each wrap has its own CSS variable value. The beam animation reads the variable via CSS `var()` inheritance — since the variable is set on the `.pdf-thumb-wrap` element itself (the direct parent of `.pdf-scan-beam`), it correctly scopes to that wrap. Different rows that render at different canvas heights (due to PDF variation) will have independently calibrated beams. **No additional work needed beyond Work Item 3.**

---

### 4.3 PDF.js worker initialized once, reused per row

**Current state and required state:** PDF.js is loaded from CDN at `demo.html:2829`:
```html
<script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
```

The worker source is set once immediately after (`demo.html:2831–2834`):
```javascript
if (typeof pdfjsLib !== 'undefined') {
  pdfjsLib.GlobalWorkerOptions.workerSrc =
    'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
}
```

`pdfjsLib.getDocument({ data: arrayBuffer })` in each `_bulkPdfHandleUpload()` call reuses the shared worker process that PDF.js manages internally. PDF.js 3.x creates one worker per window and multiplexes all document loads through it. No per-row worker initialization is needed or should be added.

The guard `if (typeof pdfjsLib === 'undefined') return;` at `demo.html:9110` protects `_bulkPdfHandleUpload()` in case the CDN script fails to load. **No changes needed.**

---

### 4.4 Enabled modules read at submit time — current behavior and guarantee

**How it works:**

The frontend does not pass module state in the bulk request body. Module configuration is an org-level server-side setting stored in the `organization_modules` table in `portguard_auth.db`. When the user toggles a module in the settings drawer (`POST /api/v1/modules/{module_id}` fires on each toggle, updating the DB immediately).

At analysis time, `_run_bulk_single_analysis()` in `api/app.py:3093–3096` reads:
```python
enabled_modules_bulk: list[str] = (
    _module_config_db.get_enabled_modules(org_id)
    if _module_config_db is not None else []
)
```

This is a fresh DB read per shipment. Because the BackgroundTask runs after the HTTP 202 response is sent, any module toggle made in the drawer BEFORE clicking "Analyze Batch" will be reflected in the analysis for every shipment. Any toggle made AFTER clicking Analyze (while the batch is processing) may or may not apply to individual shipments depending on timing.

**The guarantee:** Modules toggled before batch submission are guaranteed to apply. This is the correct and expected behavior. **No changes needed.**

**What to document in the UI (optional enhancement — not a code requirement):** A tooltip on the module settings drawer saying "Module changes take effect on your next scan" is sufficient to communicate this to users.

---

### 4.5 Sustainability rating displayed in results table

**Current state (fully implemented):** `_bulkRenderTable()` at `demo.html:9676–9731` already:

1. Reads `r.sustainability_grade` from `_bulkAllResults` (which is read from `s.sustainability_grade` — the dedicated DB column — or falls back to `s.full_result.sustainability_rating.grade`).
2. Renders a colored `<span class="sustain-badge sustain-badge-${sGradeKey}">` in column 4 of the results table.
3. In the expand row, shows a full sustainability section with grade badge, signals list, and active modules.

Color coding is handled via the `sustain-badge-{A|B|C|D|NA}` CSS classes. These classes must already exist (since the single-doc results tab renders them). **No changes needed.**

The `_bulkAllResults` mapping at `demo.html:9548–9561` reads sustainability from the fast path (DB column) or falls back to `full_result`. This is already correct.

---

### 4.6 Files to change

| File | Lines | Change |
|---|---|---|
| `demo.html` | 9213–9228 | Set `--thumb-h` per wrap (same as Work Item 3 Step 3) |

All other concerns are already correctly implemented. Work Items 3 and 4 share the same JS change (the `--thumb-h` fix in `_bulkPdfRenderThumb()`). That one change satisfies both:
- WI3: fixes the clipping (via the CSS `width: auto` change)
- WI4: scopes the beam per row (via the per-wrap `--thumb-h` value)

---

### 4.7 What must NOT be touched

- `pdfjsLib.GlobalWorkerOptions.workerSrc` initialization — shared, do not add per-row initialization
- `POST /api/v1/modules/{module_id}` endpoint — module toggle mechanism is correct
- `_run_bulk_single_analysis()` module loading — correct, do not add frontend module passing
- Sustainability badge CSS classes (`sustain-badge-*`) — already defined, do not redefine
- `_bulkRenderTable()` — already renders sustainability correctly, do not change

---

### 4.8 Test cases

1. Toggle a module OFF in the settings drawer. Submit a bulk batch. Expand a result row — verify that module is NOT listed in `active_modules` for any shipment.
2. Toggle a module back ON. Submit another batch. Expand a result row — verify that module IS listed in `active_modules`.
3. Upload PDFs to 3 bulk slots with different PDF files. Verify each slot's scan beam sweeps its own canvas height independently (beams are not synchronized between rows).
4. Rapidly upload different PDFs to the same bulk slot (3 times in quick succession). Verify only the last upload's thumbnail is shown (stale-upload guard prevents ghost beams from earlier uploads).
5. Submit a batch with a shipment that has sustainability data. Verify the results table shows the grade badge (A/B/C/D) in the Sustainability column. Click the expand chevron — verify the sustainability section shows grade, signals, and active modules list.
6. Submit a batch with a shipment that has `sustainability_grade: null` (no HTS codes or N/A product type). Verify the Sustainability column shows "—" and no crash.

---

## Implementation Order

Execute in this order — each item is a prerequisite for the one above being fully testable:

1. **WI3 (PDF clipping)** — CSS-only change, fastest to verify visually
2. **WI4 (beam scope / modules)** — shares the JS change with WI3
3. **WI1 (stats bug)** — fixes the broken stats panel; can be verified immediately after
4. **WI2 (share link)** — requires WI1 to be working first so the loaded batch shows correct stats

---

## What is explicitly out of scope for this sprint

The following items from the audit are noted but are NOT part of this sprint:

- GAP-1: Rate limiting on bulk endpoint — security hardening, separate sprint
- GAP-2: Screener re-instantiation per shipment — performance optimization, separate sprint  
- GAP-3: Cross-batch concurrency guard — infrastructure concern
- GAP-4: CSV silent truncation — error handling improvement
- INCON-1: Status vs Results endpoint shape — the WI1 fix works around this; unifying the shapes requires a breaking API change
- INCON-2: Duplicate DEFAULT_ENABLED_MODULES — refactor, not a bug
- BUG-3 (PDF conflict text UX) — the current behavior is arguably intentional; PDF wins over manual text. If the user wants to change this policy, it is a separate UX decision
- GAP-8: `api/Real_Claude_PortGuard` stray file — trivial cleanup, one `git rm`
