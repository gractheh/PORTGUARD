# PDF Upload Experience — Technical Architecture

**Document:** `docs/pdf_experience_architecture.md`  
**Status:** Planning  
**Scope:** `demo.html` only — Analyze tab, single-shipment document pane  
**Zero impact on:** Dashboard, Bulk Upload, auth, API, backend Python code

---

## 1. Problem Statement

The current PDF upload flow is utilitarian and breaks immersion. When a user uploads a PDF:

1. A spinner appears briefly in the `.extract-status` bar
2. The raw extracted text is dumped directly into the monospace `textarea`
3. The user must scroll through hundreds of lines of extracted text to confirm it worked
4. There is no visual confirmation the document was understood
5. While analysis runs, the button just says "Analyzing…" with three cycling text labels

This feels like a developer tool, not an enterprise compliance system. The new experience must feel like magic — the PDF becomes a recognized, real-feeling document, and the analysis feels like a live multi-agent pipeline.

---

## 2. Feature Overview

Six changes, implemented as a single cohesive upgrade:

| # | Feature | Trigger | Reverts |
|---|---------|---------|---------|
| 1 | PDF Preview Panel | PDF file selected/dropped | Tab switch or new file |
| 2 | Scanning Beam Animation | Immediately on file selection | When extraction completes |
| 3 | Client-side text extraction via PDF.js | PDF file selected | N/A (runs once) |
| 4 | Edit Text Toggle | Shows after extraction | Collapsed by default |
| 5 | Agent Pipeline Visualizer | Analyze button clicked | Resets when next analyze starts |
| 6 | TXT file / manual text | No change to existing flow | N/A |

---

## 3. Architecture Overview

### 3.1 New State Per Tab

Each tab object in the `tabs` array gains two new optional fields:

```js
{
  id: Number,
  filename: String,
  text: String,            // existing — always populated
  // NEW:
  pdfFile: File | null,    // the raw File object if a PDF was uploaded
  pdfMeta: {               // populated after PDF.js processes the file
    pageCount: Number,
    fileSizeKb: Number,
    thumbnailDataUrls: String[],  // one per page, max 3
    extractedText: String,        // client-side extracted text (may differ from server)
  } | null,
  hasPdfPreview: Boolean,   // true = show preview panel, false = show textarea
  textEditOpen: Boolean,    // true = textarea expanded below preview
}
```

### 3.2 Rendering Contract

`renderPane()` is the single function that rebuilds the active tab's `.doc-pane` HTML. It currently always renders a textarea. After this change:

```
renderPane(tab)
  if tab.hasPdfPreview:
    render PdfPreviewPanel (thumbnail + metadata card + status badge)
    render EditTextToggle
    render Textarea (hidden — display:none, but in DOM)
  else:
    render normal textarea (existing behavior, untouched)
```

The textarea is **always rendered and always contains the extracted text**. Only its `display` is toggled. The `analyze()` function reads text from `tab.text` (the JS object), not from the DOM textarea, so this is safe.

### 3.3 Extraction Pipeline

Current flow (server-side only):
```
File selected → FormData → POST /api/v1/extract-text → text into textarea
```

New flow (client-side first, server validates):
```
File selected
  ├── PDF.js loads file ArrayBuffer                    (client, immediate)
  │   ├── Render thumbnail DataURLs for pages 1-3      (client, ~200ms)
  │   └── Extract text from all pages                  (client, ~100ms)
  ├── Show PdfPreviewPanel with scanning beam          (immediate)
  ├── Place PDF.js extracted text into tab.text        (on PDF.js complete)
  ├── POST /api/v1/extract-text (server extraction)    (parallel, for accuracy)
  └── On server response: overwrite tab.text with
      server text (server pdfplumber is more accurate)
      → Update status badge "Text extracted ✓"
      → Stop scanning beam
```

**Why two extractions?** PDF.js extraction in the browser is good enough to show the preview experience immediately, but pdfplumber on the server handles complex PDFs (mixed encodings, rotated text, CJK characters) better. The server result always wins. If the server is unreachable, the PDF.js text is used as fallback.

---

## 4. PDF.js Integration

### 4.1 CDN Script Tag

Add to the `<head>` alongside the existing Chart.js CDN tag:

```html
<script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
<script>
  // Must be set before any PDF.js call
  if (typeof pdfjsLib !== 'undefined') {
    pdfjsLib.GlobalWorkerOptions.workerSrc =
      'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
  }
</script>
```

Version pinned to **3.11.174** (stable, widely cached on CDN). The worker must be set before any `getDocument()` call.

### 4.2 Thumbnail Generation

Render pages 1–3 only. Higher-resolution pages would be slow and are not needed for thumbnails.

```js
async function _pdfRenderThumbnail(pdfDoc, pageNum, canvasEl) {
  const page     = await pdfDoc.getPage(pageNum);
  const viewport = page.getViewport({ scale: 0.35 });  // ~52% of A4 → ~170px wide
  const ctx      = canvasEl.getContext('2d');
  canvasEl.width  = viewport.width;
  canvasEl.height = viewport.height;
  await page.render({ canvasContext: ctx, viewport }).promise;
  return canvasEl.toDataURL('image/jpeg', 0.7);  // JPEG at 70% quality
}
```

Scale `0.35` on a standard 612-wide PDF yields ~214px wide thumbnails, which fit the preview card without exceeding ~60 KB each.

### 4.3 Text Extraction

```js
async function _pdfExtractTextClientSide(pdfDoc) {
  let fullText = '';
  for (let i = 1; i <= pdfDoc.numPages; i++) {
    const page    = await pdfDoc.getPage(i);
    const content = await page.getTextContent();
    fullText += content.items.map(item => item.str).join(' ') + '\n';
  }
  return fullText.trim();
}
```

This runs on all pages. For large PDFs (>20 pages) this still completes in under 1s in the browser since we're only reading text, not rendering.

### 4.4 Fallback — PDF.js Unavailable

If the CDN fails to load (offline demo, content blocker):
- `typeof pdfjsLib === 'undefined'` check before any PDF.js code
- Fall back gracefully to the current server-only flow
- No preview panel is shown; textarea fills normally
- No user-visible error; the existing `extract-status` bar works as before

---

## 5. PDF Preview Panel — HTML Structure

The panel replaces (visually) the textarea when a PDF is loaded. The textarea stays in the DOM with `display:none`.

```html
<div class="pdf-preview-panel" id="pdf-preview-${tabId}">

  <!-- LEFT: Thumbnail strip (up to 3 pages) -->
  <div class="pdf-thumb-strip" id="pdf-thumbs-${tabId}">
    <!-- Each thumb wrapper: -->
    <div class="pdf-thumb-wrap" id="pdf-thumb-wrap-${tabId}-${pageNum}">
      <canvas class="pdf-thumb-canvas"></canvas>
      <!-- Scanning beam overlay — sits over the canvas, absolute positioned -->
      <div class="pdf-scan-beam" id="pdf-scan-beam-${tabId}-${pageNum}"></div>
      <div class="pdf-thumb-page-num">p.${pageNum}</div>
    </div>
  </div>

  <!-- RIGHT: Metadata card -->
  <div class="pdf-meta-card">
    <div class="pdf-meta-icon">
      <!-- PDF icon SVG -->
    </div>
    <div class="pdf-meta-info">
      <div class="pdf-meta-filename" id="pdf-meta-fn-${tabId}">
        <!-- e.g. bill_of_lading.pdf -->
      </div>
      <div class="pdf-meta-details">
        <span class="pdf-meta-pill" id="pdf-meta-pages-${tabId}">
          <!-- e.g. "4 pages" -->
        </span>
        <span class="pdf-meta-pill" id="pdf-meta-size-${tabId}">
          <!-- e.g. "142 KB" -->
        </span>
      </div>
      <!-- Extraction status badge -->
      <div class="pdf-extract-status" id="pdf-extract-status-${tabId}">
        <div class="pdf-extract-spinner"></div>
        <span>Extracting text…</span>
      </div>
    </div>
  </div>

</div>

<!-- Edit text toggle -->
<button class="pdf-edit-toggle" id="pdf-edit-toggle-${tabId}"
        onclick="pdfToggleEditText(${tabId})">
  <svg><!-- chevron down icon --></svg>
  Edit text ↓
</button>

<!-- The textarea — always present, display toggled -->
<textarea class="doc-text pdf-hidden-textarea"
          id="text-${tabId}"
          style="display:none">
  <!-- extracted text lives here -->
</textarea>
```

### 5.1 Layout Rules

```
.pdf-preview-panel {
  display: flex;
  gap: 1rem;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1rem;
  min-height: 160px;
}

.pdf-thumb-strip {
  display: flex;
  gap: .5rem;
  flex-shrink: 0;
}

.pdf-thumb-wrap {
  position: relative;        /* anchors the absolute scanning beam */
  border-radius: 4px;
  overflow: hidden;
  border: 1px solid var(--border);
  flex-shrink: 0;
}

.pdf-thumb-canvas {
  display: block;
  max-width: 110px;          /* clamp thumbnail width */
  height: auto;
}

.pdf-meta-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: .6rem;
  justify-content: center;
}
```

For PDFs with more than 3 pages, only pages 1, 2, 3 are thumbnailed. A `+N more pages` pill is shown in the metadata card.

---

## 6. Scanning Beam Animation

### 6.1 CSS

```css
/* The beam: a full-width thin gradient line that sweeps top-to-bottom */
.pdf-scan-beam {
  position: absolute;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(27,154,170,.0) 10%,
    rgba(27,154,170,.9) 50%,
    rgba(27,154,170,.0) 90%,
    transparent 100%
  );
  box-shadow: 0 0 8px rgba(27,154,170,.6), 0 0 2px rgba(77,207,223,.8);
  top: 0;
  animation: pdf-scan-sweep 1.8s ease-in-out infinite;
  pointer-events: none;
  z-index: 2;
}

/* A subtle green tint that follows behind the beam */
.pdf-scan-beam::before {
  content: '';
  position: absolute;
  left: 0; right: 0;
  top: -40px;
  height: 40px;
  background: linear-gradient(
    to bottom,
    transparent 0%,
    rgba(27,154,170,.04) 100%
  );
}

@keyframes pdf-scan-sweep {
  0%   { top: -3px; opacity: 0; }
  5%   { opacity: 1; }
  90%  { opacity: 1; }
  100% { top: 100%; opacity: 0; }
}
```

### 6.2 State Machine

The beam is controlled by CSS class `scanning` on the `.pdf-thumb-wrap`:

```
.pdf-thumb-wrap.scanning  .pdf-scan-beam  → animation plays (running)
.pdf-thumb-wrap            .pdf-scan-beam  → animation-play-state: paused; opacity: 0
.pdf-thumb-wrap.done       .pdf-scan-beam  → display: none
```

JavaScript:
```js
function _pdfSetScanning(tabId, scanning) {
  document.querySelectorAll(`#pdf-thumbs-${tabId} .pdf-thumb-wrap`).forEach(w => {
    w.classList.toggle('scanning', scanning);
    w.classList.toggle('done', !scanning);
  });
}
```

### 6.3 Timing

| Event | Beam state |
|-------|-----------|
| File selected | Start scanning immediately |
| PDF.js text extraction complete (client) | Continue scanning (waiting for server) |
| Server `/api/v1/extract-text` responds (success) | **Stop scanning** → show "Text extracted ✓" |
| Server responds (error, but PDF.js text available) | Stop scanning → show warning badge |
| Server responds (error, no text) | Stop scanning → show error badge |

The beam should not run for more than 15 seconds. A `setTimeout` of 15s will force-stop it even if the server hangs, so the UI never feels stuck.

---

## 7. Extraction Status Badge

The `.pdf-extract-status` element cycles through three states:

### State 1 — Extracting (default when panel appears)
```html
<div class="pdf-extract-status extracting">
  <div class="pdf-extract-spinner"></div>
  <span>Extracting text…</span>
</div>
```
Style: teal background tint, spinner visible

### State 2 — Complete
```html
<div class="pdf-extract-status done">
  <svg><!-- checkmark icon --></svg>
  <span>Text extracted ✓</span>
</div>
```
Style: green tint, checkmark visible, brief fade-in animation

### State 3 — Warning (partial extraction)
```html
<div class="pdf-extract-status warn">
  <svg><!-- warning icon --></svg>
  <span>Partial extraction — review text</span>
</div>
```
Style: amber tint

### State 4 — Error (scanned PDF, no text)
```html
<div class="pdf-extract-status error">
  <svg><!-- x icon --></svg>
  <span>Scanned PDF — no text found</span>
</div>
```
Style: red tint. In error state the preview panel still shows thumbnails (user can see the document), but the Analyze button will be disabled or the user is asked to manually paste text.

---

## 8. Edit Text Toggle

### 8.1 Visual Design

The toggle button sits immediately below the PDF preview panel, left-aligned:

```
[ ↓ Edit text ]
```

Collapsed state (default):
- Button shows "↓ Edit text" with chevron-down icon
- Textarea `display: none` (height 0, no DOM space taken)

Expanded state:
- Button shows "↑ Hide text" with chevron-up icon
- Textarea `display: block`, animates in via `max-height` transition
- Textarea uses existing `.doc-text` styles (monospace, 220px height, resizable)
- A subtle label above the textarea reads "Extracted text — edit if needed"

### 8.2 Smooth Expand Animation

Use `max-height` transition (the reliable CSS-only approach):

```css
.pdf-edit-textarea-wrap {
  max-height: 0;
  overflow: hidden;
  transition: max-height 0.35s var(--ease-out);
}
.pdf-edit-textarea-wrap.open {
  max-height: 360px;   /* enough for 220px textarea + padding */
}
```

The textarea inside the wrapper always exists in the DOM. The wrapper's `max-height` controls visibility.

### 8.3 State Persistence

`tab.textEditOpen` tracks whether the edit panel is open. This survives `renderPane()` re-calls (tab switches, etc.) because `renderPane()` reads `tab.textEditOpen` before generating HTML.

---

## 9. Agent Pipeline Visualizer

### 9.1 When It Appears

The pipeline visualizer replaces the existing `setLoading(true)` UX **only when a PDF tab is active**. For manual text entry, the existing behavior (button spinner + text steps) is unchanged.

Detection: `tabs.find(t => t.id === activeTab).hasPdfPreview === true`

### 9.2 DOM Placement

The pipeline visualizer is inserted between the `#doc-pane` and `#analyze-btn`, **not** inside the results section. It is a sibling of the existing `#error-banner`.

```html
<!-- Inserted once into the Analyze panel; shown/hidden via JS -->
<div id="agent-pipeline" class="agent-pipeline" style="display:none">
  <div class="pipeline-track">
    <!-- 6 nodes + 5 connectors, injected by buildAgentPipeline() -->
  </div>
  <div class="pipeline-status" id="pipeline-status">
    <!-- Current step message -->
  </div>
</div>
```

### 9.3 Node Structure

Each node is a flex column:

```html
<div class="pipeline-node" id="pnode-classifier" data-agent="classifier">
  <div class="pnode-circle">
    <!-- icon SVG inside -->
    <div class="pnode-pulse-ring"></div>   <!-- animated ring, visible when active -->
  </div>
  <div class="pnode-name">Classifier</div>
</div>
```

Connectors between nodes:

```html
<div class="pipeline-connector" id="pconn-0"></div>
```

### 9.4 The Six Agents

| Index | Agent Key | Display Name | Icon | Status Message |
|-------|-----------|--------------|------|----------------|
| 0 | `classifier` | Document Classifier | document-search | "Identifying document types…" |
| 1 | `parser` | Parser | code-brackets | "Parsing fields and tables…" |
| 2 | `validator` | Validator | shield-check | "Checking document completeness…" |
| 3 | `risk` | Risk Engine | alert-triangle | "Running compliance rules…" |
| 4 | `decision` | Decision Agent | scales | "Generating compliance decision…" |
| 5 | `orchestrator` | Orchestrator | network | "Compiling final report…" |

### 9.5 Node States and CSS Classes

```
.pipeline-node                   → idle (gray)
.pipeline-node.active            → currently running (teal pulse)
.pipeline-node.complete          → done (solid teal + checkmark replaces icon)
.pipeline-connector              → gray line (default)
.pipeline-connector.active       → teal line, animated fill left-to-right
```

CSS for states:
```css
/* Idle */
.pnode-circle {
  width: 48px; height: 48px; border-radius: 50%;
  border: 2px solid var(--border);
  background: var(--bg-card);
  display: flex; align-items: center; justify-content: center;
  position: relative;
  transition: border-color .3s, background .3s;
  color: var(--faint);
}

/* Active state */
.pipeline-node.active .pnode-circle {
  border-color: var(--teal-500);
  background: rgba(27,154,170,.08);
  color: var(--teal-300);
  box-shadow: 0 0 0 4px rgba(27,154,170,.08);
}

/* Pulse ring — spins outward from active node */
.pnode-pulse-ring {
  position: absolute; inset: -6px;
  border-radius: 50%;
  border: 2px solid transparent;
  animation: none;
}
.pipeline-node.active .pnode-pulse-ring {
  border-color: rgba(27,154,170,.3);
  animation: pnode-pulse 1.2s ease-out infinite;
}
@keyframes pnode-pulse {
  0%   { transform: scale(1); opacity: .8; }
  100% { transform: scale(1.5); opacity: 0; }
}

/* Complete state */
.pipeline-node.complete .pnode-circle {
  border-color: var(--teal-500);
  background: rgba(27,154,170,.12);
  color: var(--teal-400);
}

/* Connector (thin horizontal line) */
.pipeline-connector {
  flex: 1; height: 2px;
  background: var(--border);
  position: relative; overflow: hidden;
  margin-top: -16px;  /* vertically center with node circles */
  transition: background .3s;
}
.pipeline-connector.active {
  background: var(--teal-700);
}
.pipeline-connector.active::after {
  content: '';
  position: absolute; top: 0; left: 0;
  height: 100%; width: 100%;
  background: linear-gradient(90deg, var(--teal-500), var(--teal-300));
  animation: conn-fill .4s var(--ease-out) forwards;
}
@keyframes conn-fill {
  from { transform: scaleX(0); transform-origin: left; }
  to   { transform: scaleX(1); transform-origin: left; }
}
```

### 9.6 Animation Sequence

The actual `POST /api/v1/analyze` is a single HTTP call that takes ~50–200ms. There is no real per-agent progress stream. The pipeline animation is **simulated**: it shows the illusion of a multi-agent pipeline by advancing on a timer that matches the known latency.

**Timing sequence (total ~6–8 steps from click to results):**

```
T+0ms    → Show pipeline, set classifier.active
T+0ms    → Send actual fetch() to /api/v1/analyze
T+600ms  → Move: classifier.complete, parser.active
T+1400ms → Move: parser.complete, validator.active
T+2200ms → Move: validator.complete, risk.active
T+3000ms → Move: risk.complete, decision.active
T+3800ms → Move: decision.complete, orchestrator.active
T+? ms   → fetch() resolves: immediately set orchestrator.complete → render results
```

If the fetch resolves **before** the timer reaches that step, the timer is cancelled and the pipeline fast-forwards to completion. If the fetch resolves after step 3 (e.g., slow server), the timer pauses at whichever step it is on until the response arrives, then the remaining steps complete quickly.

Implementation:
```js
const PIPELINE_STEP_MS = [0, 600, 1400, 2200, 3000, 3800];

async function analyzeWithPipeline() {
  const agents  = ['classifier','parser','validator','risk','decision','orchestrator'];
  let stepIdx   = 0;
  let resolved  = false;
  let fetchData  = null;

  buildAgentPipeline();
  showAgentPipeline();

  // Start fetch in parallel
  const fetchPromise = fetch(...).then(r => r.json()).then(d => { fetchData = d; resolved = true; });

  // Advance through agent steps on timer
  function advanceStep() {
    if (stepIdx > 0) setAgentComplete(agents[stepIdx - 1]);
    if (stepIdx < agents.length) {
      setAgentActive(agents[stepIdx]);
      stepIdx++;
      const nextDelay = PIPELINE_STEP_MS[stepIdx] - PIPELINE_STEP_MS[stepIdx - 1];
      // If fetch already done by the time we'd advance, fast-forward
      if (resolved && stepIdx < agents.length) {
        setTimeout(advanceStep, 120);  // fast-forward remaining steps
      } else if (stepIdx < agents.length) {
        setTimeout(advanceStep, nextDelay);
      }
      // On last agent (orchestrator), wait for fetch to finish before completing
      else {
        fetchPromise.then(() => {
          setAgentComplete('orchestrator');
          setTimeout(() => hideAgentPipeline(), 600);
          renderResults(fetchData);
        });
      }
    }
  }

  advanceStep();
  await fetchPromise;
}
```

**Key invariant:** The `renderResults()` call happens exactly once, after `fetchPromise` resolves. The timer only controls the visual pipeline; it never calls `renderResults`.

### 9.7 Error Handling

If the fetch fails:
- Stop all timers
- Mark the currently-active agent node with `.error` class (red circle)
- Show existing `#error-banner` below the pipeline
- Pipeline stays visible so user can see where it failed

```css
.pipeline-node.error .pnode-circle {
  border-color: var(--red);
  background: rgba(224,80,80,.08);
  color: var(--red);
}
```

### 9.8 Non-PDF Path

For tabs with manual text (no PDF preview), `analyze()` uses the existing `setLoading(true)` path unchanged. The agent pipeline is never shown. **No regression risk.**

---

## 10. Complete Modified Function Map

| Function | Change Type | Description |
|----------|-------------|-------------|
| `renderPane(tab)` | **Modified** | Branch on `tab.hasPdfPreview`: PDF path renders preview panel + hidden textarea; normal path unchanged |
| `handleFileUpload(tabId, file)` | **Modified** | For PDF: trigger PDF.js + show preview panel before POSTing to server; for non-PDF: unchanged |
| `analyze()` | **Modified** | Add `if (activeTabHasPdf) analyzeWithPipeline(); else existingFlow()` |
| `setLoading(on)` | **Unchanged** | Used on non-PDF path only |
| `buildAgentPipeline()` | **New** | Build the 6-node pipeline HTML into `#agent-pipeline` |
| `showAgentPipeline()` | **New** | Show `#agent-pipeline`, hide analyze button |
| `hideAgentPipeline()` | **New** | Hide `#agent-pipeline`, restore analyze button |
| `setAgentActive(key)` | **New** | Add `.active` to node, update status text, activate connector |
| `setAgentComplete(key)` | **New** | Remove `.active`, add `.complete` to node and preceding connector |
| `pdfToggleEditText(tabId)` | **New** | Toggle `tab.textEditOpen` + animate wrapper open/close |
| `_pdfLoadAndPreview(tabId, file)` | **New** | Orchestrate PDF.js thumbnail + extraction + panel render |
| `_pdfRenderThumbnail(pdfDoc, pageNum, canvas)` | **New** | Render one page to canvas, return dataURL |
| `_pdfExtractTextClientSide(pdfDoc)` | **New** | Extract all text via PDF.js getTextContent |
| `_pdfSetScanning(tabId, bool)` | **New** | Start/stop beam animation on all thumbs for a tab |
| `_pdfSetExtractionStatus(tabId, state)` | **New** | Update status badge: 'extracting' | 'done' | 'warn' | 'error' |
| `switchTab(id)` | **Minor** | Save `tab.textEditOpen` state before switching |
| `removeTab(id)` | **Minor** | Clear `tab.pdfFile` and `tab.pdfMeta` on removal |

---

## 11. New CSS Classes (complete list)

```
.pdf-preview-panel         — outer container (flex row)
.pdf-thumb-strip           — flex row of up to 3 thumb wrappers
.pdf-thumb-wrap            — position:relative wrapper; class: scanning | done
.pdf-thumb-canvas          — the rendered canvas thumbnail
.pdf-scan-beam             — absolute, animated scan line
.pdf-thumb-page-num        — small page number label bottom-right
.pdf-more-pages            — "+N more pages" chip when >3 pages
.pdf-meta-card             — right column with filename + details
.pdf-meta-icon             — document SVG icon with teal tint
.pdf-meta-filename         — filename in monospace
.pdf-meta-details          — flex row of pills
.pdf-meta-pill             — small rounded badge (page count, file size)
.pdf-extract-status        — status row: class: extracting | done | warn | error
.pdf-extract-spinner       — teal spinner, same as .extract-spinner
.pdf-edit-toggle           — "Edit text ↓ / ↑" button below panel
.pdf-edit-textarea-wrap    — max-height transition wrapper
.pdf-edit-label            — small label above textarea when open
.pdf-hidden-textarea       — textarea in PDF mode (added as modifier, no style override)
.agent-pipeline            — outer pipeline container
.pipeline-track            — flex row of nodes + connectors
.pipeline-node             — flex col (circle + label); class: active | complete | error
.pnode-circle              — the 48px circle
.pnode-pulse-ring          — absolute ring for pulse animation
.pnode-icon                — SVG inside circle
.pnode-name                — small text below circle
.pipeline-connector        — flex:1 horizontal line; class: active
.pipeline-status           — current step message text
```

---

## 12. Files to Modify

| File | Change |
|------|--------|
| `demo.html` | **Only file modified.** Three insertion points: |
| | 1. `<head>`: Add PDF.js CDN `<script>` tags (2 lines) |
| | 2. `<style>`: Add new CSS classes (in the Analyze panel section, before the Bulk Upload section) |
| | 3. `<script>`: Replace `handleFileUpload()`, modify `renderPane()`, modify `analyze()`, add all new functions |

No Python files, no API changes, no other HTML sections.

---

## 13. Implementation Phases

### Phase 1 — PDF.js CDN + thumbnail rendering (no behavioral changes)
1. Add PDF.js CDN tags to `<head>`
2. Add `_pdfLoadAndPreview()` function (loads PDF.js doc, renders thumbnails, extracts text)
3. Wire into `handleFileUpload()`: for PDFs, call `_pdfLoadAndPreview()` instead of immediately posting to server; after PDF.js work, continue with the existing server POST

### Phase 2 — PDF Preview Panel + Scanning Beam
4. Add all new CSS classes
5. Modify `renderPane()` to branch on `tab.hasPdfPreview`
6. Build `buildPdfPreviewPanel(tab)` HTML builder
7. Add `_pdfSetScanning()` + `_pdfSetExtractionStatus()` helpers
8. Test: upload a PDF, confirm thumbnails render, beam sweeps, badge updates

### Phase 3 — Edit Text Toggle
9. Add `pdfToggleEditText()` + `pdf-edit-textarea-wrap` expand/collapse animation
10. Confirm textarea always has correct extracted text when opened
11. Confirm `analyze()` still reads from `tab.text` (not DOM) — verify no regression

### Phase 4 — Agent Pipeline Visualizer
12. Add `buildAgentPipeline()`, `showAgentPipeline()`, `hideAgentPipeline()`
13. Add `setAgentActive()`, `setAgentComplete()`, timer sequencing
14. Modify `analyze()` to call `analyzeWithPipeline()` when active tab has PDF preview
15. Test: confirm pipeline advances correctly, confirm results render after fetch resolves
16. Test: trigger a fetch error, confirm error state appears on correct node

### Phase 5 — Polish & Regression Check
17. Test tab switching with mixed PDF and text tabs
18. Test rapid re-uploads (cancel in-flight PDF.js work if new file selected)
19. Test slow server (>4s response): confirm pipeline pauses at last agent, doesn't double-render
20. Test PDF.js CDN failure: confirm graceful fallback to textarea
21. Confirm Dashboard, Bulk Upload, Auth completely unaffected

---

## 14. Regression Boundaries

The following are explicit non-touch zones:

| Zone | Why safe |
|------|---------|
| `#dashboard-panel` | Different section, separate JS namespace |
| `#bulk-panel` | Different section, no shared state |
| `#auth-overlay` | Not in Analyze panel |
| `analyze()` results rendering | `renderResults()` called identically regardless of pipeline |
| `tab.text` population | Always populated; analyze() reads `tab.text` not DOM |
| Non-PDF uploads (`.txt`) | Explicit `if (isPdf)` guard on all new code paths |
| Quick-load scenarios | Load text into textarea directly; `tab.hasPdfPreview` stays false |
| Existing `handleFileUpload` error handling | All existing error paths preserved; new code added around them |

---

## 15. Open Decisions

These need confirmation before implementation:

1. **Thumbnail rendering for very large PDFs** — For a 100-page PDF, we only render pages 1–3 as thumbnails. Client-side text extraction still runs on all pages (~500ms for 100 pages in PDF.js). This is acceptable but worth measuring.

2. **Fallback text when PDF.js extraction is empty but server has text** — If the PDF has no machine-readable text (scanned), PDF.js returns empty string. The server will return `SCANNED_PDF` error. In this case: show preview thumbnails (the user sees the document), show error badge, disable Analyze. Same outcome as current behavior, but with visual thumbnails as consolation.

3. **Agent pipeline for non-PDF tabs** — Spec says "when the user clicks Analyze (after PDF upload)". Interpretation: pipeline shows only when the active tab has `hasPdfPreview = true`. If the user has 2 tabs (one PDF, one manual text) and switches to the text tab before clicking Analyze, no pipeline is shown. This is the correct behavior — the pipeline is a PDF affordance, not a generic loading state.

4. **Persistence of PDF preview across tab switches** — When switching back to a PDF tab, the preview panel re-renders from `tab.pdfMeta.thumbnailDataUrls` (DataURLs already stored in the tab state object). No re-fetching from PDF.js. This is fast but uses ~180 KB of string memory per PDF tab (3 JPEG thumbnails at 60 KB each). Acceptable for up to 10 tabs.
