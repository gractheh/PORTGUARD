# Pattern Learning History — Full Codebase Read Report

**Date:** 2026-05-15  
**Scope:** All files in `/Users/andresnewman/Desktop/PORTGUARD/`  
**Purpose:** Complete audit of the Pattern Learning History panel — what every piece does, where it lives, and what is broken.

---

## 1. The HTML Element

**Primary container:**
```html
<!-- demo.html line 4532 -->
<div class="ph-panel" id="ph-panel">
```

**DOM position:**
```
<div id="analyze-panel">          ← top-level tab panel (line 4286)
  <div class="container">         ← main content wrapper
    <div class="results" id="results-section">
      <!-- ...analysis results... -->
    </div><!-- /results -->
    
    <!-- Pattern History -->
    <div class="ph-panel" id="ph-panel">   ← LINE 4532
      <div class="ph-header">
        <div class="section-label">Pattern Learning History</div>
        <div style="display:flex;gap:.5rem;align-items:center">
          <button class="ph-reset-btn" id="ph-reset-btn" ...>Reset History</button>
          <button class="ph-load-btn pattern-refresh-btn" onclick="refreshPatternStats(this)">
            <svg .../>
          </button>
        </div>
      </div>
      <div class="ph-success" id="ph-success">   ← success flash, hidden by default
        ✓ History cleared — PortGuard is starting fresh.
      </div>
      <div id="pattern-panel-body"></div>   ← LINE 4543 — the content target
    </div>
    
    <!-- Reset confirmation modal -->
    <div class="modal-overlay" id="reset-modal" ...>...</div>
    
  </div><!-- /container -->
</div><!-- /analyze-panel -->
```

**Key IDs:**
- `id="ph-panel"` — the full panel wrapper (used for opacity flash by refresh button)
- `id="pattern-panel-body"` — the div whose innerHTML is replaced with actual stats content
- `id="ph-success"` — the post-reset success banner (shown via `.visible` class)
- `id="ph-reset-btn"` — the Reset History button
- No id on the refresh button (targeted by `querySelector('[onclick*="refreshPatternStats"]')` as fallback)

**CSS classes:**
- `.ph-panel` (line 934): `margin-top: 2.5rem; border-top: 1px solid rgba(27,154,170,.15); padding-top: 2rem`
- `.ph-header` (line 939): flex row, space-between
- `.ph-load-btn` (line 943): base button style — var(--card) background, var(--border) border, var(--muted) text
- `.pattern-refresh-btn` (line 949, added in commit afc61ee): teal bg, teal border, `#4DCFDF` icon color, inline-flex, hover scale
- `.ph-success` (line 1257): `display:none` by default; `.ph-success.visible { display: flex }` (line 1265)
- `.ph-empty` (line 1004): faint italic text for loading/no-data states
- `.pattern-stats-grid` (line 1007 + 1083): 4-column stat grid

**The panel is NOT hidden at page load.** There is no `display:none` on `#ph-panel` itself. It is always visible when the `analyze` tab is active. The `analyze-panel` has `#analyze-panel.hidden { display: none; }` (line 1355) which hides it when switching to the Dashboard or Bulk tabs — but this affects the whole `analyze-panel`, not `ph-panel` specifically.

---

## 2. The JavaScript Functions

### Primary rendering function: `renderPatternStats(stats)` — line 8868

**What it does:**
- Receives a stats dict from the API
- Checks `stats.has_history` and `stats.total_shipments_screened === 0`
  - If no history: renders the "Pattern Intelligence Active" empty state with a brain emoji
  - If has history: renders a 4-stat grid + two health bar rows + up to 4 sections

**Stats grid values rendered (lines 8887–8901):**
- `stats.total_shipments_screened` — "Shipments Screened"
- `stats.unique_shippers_tracked` — "Shippers Tracked"
- `stats.unique_routes_tracked` — "Routes Tracked"
- `stats.confirmed_fraud_count` — "Confirmed Fraud" (red if > 0)

**Health bar rows:**
- Approval Rate: computed as `Math.round(stats.approval_rate)%`
- Avg Risk Score: `(stats.avg_org_risk_score || 0)/10 × 100`

**Sections rendered if non-empty:**
- ⚠️ High-Risk Shippers: `stats.high_risk_shippers[]` — each has `signal_key`, `fraud_confirmed_count`, `flag_rate`
- 🗺️ High-Risk Routes: `stats.high_risk_routes[]` — each has `signal_key`, `flag_rate`
- 💰 Value Anomalies: `stats.value_anomalies[]` — each has `signal_key`, `flag_rate`
- ✅ Known-Good Shippers: `stats.cleared_shippers[]` — each has `signal_key`, `cleared_count`

### Data fetch function: `fetchAndRenderPatternStats()` — line 8964

```javascript
async function fetchAndRenderPatternStats() {
  if (!_authToken) return;
  const patternPanel = document.getElementById('pattern-panel-body');
  if (patternPanel && !patternPanel.innerHTML.trim()) {
    patternPanel.innerHTML = '<div class="ph-empty">Loading…</div>';
  }
  try {
    const res = await fetch(apiUrl() + '/api/v1/pattern-stats', {
      headers: { 'Authorization': 'Bearer ' + _authToken },
    });
    if (res.status === 401) { handle401(); return; }
    if (!res.ok) return;          // ← SILENT FAILURE — see §8
    const stats = await res.json();
    renderPatternStats(stats);
  } catch (_) {}                  // ← SILENT EXCEPTION SWALLOW — see §8
}
```

**When this function is called:**
- Line 5101: inside `hideAuthOverlay()` — immediately after the user logs in
- Line 8093: inside result rendering — after every single-document analysis
- Line 8856: after submitting officer feedback (confirmed fraud / cleared)
- Line 9098: after `confirmReset()` succeeds

### Refresh button function: `refreshPatternStats(btn)` — line 8981

Added in commit `afc61ee`. Wraps the same fetch with:
- SVG spin animation (360° over 0.6s, then reset)
- `btn.disabled = true` during fetch
- `console.error` on failure (no user-visible error)
- `ph-panel` opacity flash (0.4 → 1.0) on success to signal the update

### Load alias: `loadPatternHistory()` — line 9018

```javascript
async function loadPatternHistory() {
  await fetchAndRenderPatternStats();
}
```
Pure pass-through. Not called from anywhere in the current code — dead function retained from an earlier implementation.

### Reset modal function: `openResetModal()` — line 9041

Calls `/api/v1/pattern-stats` (same endpoint) to pre-populate the `#modal-count` element with `data.total_shipments_screened` before showing the confirmation modal.

### Reset confirm function: `confirmReset()` — line 9078

Calls `DELETE /api/v1/pattern-history/reset` with `{ confirm: true }`. On success: closes modal, calls `fetchAndRenderPatternStats()`, shows `ph-success` banner for 6 seconds.

---

## 3. The API Endpoint

**Frontend call** (lines 8971, 8997, 9048):
```javascript
fetch(apiUrl() + '/api/v1/pattern-stats', {
  headers: { 'Authorization': 'Bearer ' + _authToken }
})
```

**`apiUrl()` function** (line 5047):
```javascript
const apiUrl = () => '';
```
Returns an empty string — meaning all API calls go to the same origin the page is served from. Full URL in production: `https://<render-domain>/api/v1/pattern-stats`.

**Backend route definition** (`api/app.py` line 2488):
```python
@app.get("/api/v1/pattern-stats")
def pattern_stats_endpoint(current_org: dict = Depends(get_current_organization)):
```

**What it returns** (the `get_pattern_stats()` return dict, typed fields):
```json
{
  "total_shipments_screened": 0,
  "unique_shippers_tracked": 0,
  "unique_routes_tracked": 0,
  "confirmed_fraud_count": 0,
  "avg_org_risk_score": 0.0,
  "total_flags_issued": 0,
  "approval_rate": 100.0,
  "high_risk_shippers": [],
  "high_risk_routes": [],
  "value_anomalies": [],
  "cleared_shippers": [],
  "has_history": false
}
```

**Error cases:**
- 503 with `PATTERN_LEARNING_DISABLED` if `_pattern_db is None` at startup
- 500 with `STATS_ERROR` if the DB query throws

**Also uses this same endpoint:**
- `openResetModal()` (line 9048) — reads `total_shipments_screened` for modal count display

---

## 4. The Backend Function

**Function:** `get_pattern_stats(db, org_email: str) -> dict`  
**File:** `portguard/pattern_engine.py` lines 455–644

**Called from:** `api/app.py` lines 2507–2513:
```python
from portguard.pattern_engine import get_pattern_stats as _get_patt_stats
return _get_patt_stats(_pattern_db, current_org["email"])
```

Note: uses `current_org["email"]` (e.g., `user@company.com`), NOT the UUID `organization_id`. The `pattern_store` table is keyed on `organization_email` accordingly.

**What the function queries:**
1. Aggregate totals from `pattern_store` WHERE `signal_type = 'SHIPPER_REP'`:
   - `SUM(occurrence_count)` → `total_shipments_screened`
   - `COUNT(SHIPPER_REP rows)` → `unique_shippers_tracked`
   - `COUNT(ROUTE_RISK rows)` → `unique_routes_tracked`
   - `SUM(fraud_confirmed_count)` → `confirmed_fraud_count`
   - `SUM(flag_count)` → `total_flags_issued`
   - `AVG(avg_risk_score)` → `avg_org_risk_score`
2. High-risk shippers (flag_rate > 0.3, top 10 by flag_rate DESC)
3. High-risk routes (flag_rate > 0.3, occurrence ≥ 3, top 10)
4. Value anomalies (top 3 by flag_count DESC)
5. Cleared shippers (cleared_count > 0, top 10)

**Non-fatal design:** Every exception is caught, logged as `WARNING`, and the safe default dict (all zeros/empty arrays) is returned. The function never raises.

---

## 5. The Database Table: `pattern_store`

**File:** `portguard/pattern_db.py` — migration `010_pattern_store_table` at line 693  
**Database:** `portguard_patterns.db` (SQLite local, or PostgreSQL via `DATABASE_URL` on Render)

**Schema:**
```sql
CREATE TABLE IF NOT EXISTS pattern_store (
    organization_email      TEXT    NOT NULL DEFAULT '__system__',
    signal_type             TEXT    NOT NULL
                                CHECK(signal_type IN (
                                    'SHIPPER_REP',
                                    'ROUTE_RISK',
                                    'VALUE_ANOMALY',
                                    'CONFIRMED_FRAUD',
                                    'CLEARED'
                                )),
    signal_key              TEXT    NOT NULL DEFAULT '',
    occurrence_count        INTEGER NOT NULL DEFAULT 1,
    flag_count              INTEGER NOT NULL DEFAULT 0,
    fraud_confirmed_count   INTEGER NOT NULL DEFAULT 0,
    cleared_count           INTEGER NOT NULL DEFAULT 0,
    last_seen               TEXT    NOT NULL DEFAULT '1970-01-01T00:00:00Z',
    first_seen              TEXT    NOT NULL DEFAULT '1970-01-01T00:00:00Z',
    avg_risk_score          REAL    NOT NULL DEFAULT 0.0,
    last_decision           TEXT,
    notes                   TEXT,
    PRIMARY KEY (organization_email, signal_type, signal_key)
)
```

**Three indexes:**
- `idx_pattern_store_org_type ON pattern_store(organization_email, signal_type)` — most common query path
- `idx_pattern_store_org_key ON pattern_store(organization_email, signal_key)` — per-entity cross-type lookup
- `idx_pattern_store_last_seen ON pattern_store(organization_email, last_seen DESC)` — recency ordering

**Signal types and what they represent:**
- `SHIPPER_REP` — one row per unique exporter name. `signal_key` = normalized lowercase exporter name.
- `ROUTE_RISK` — one row per origin→destination pair. `signal_key` = `"CN→US"` format.
- `VALUE_ANOMALY` — one row per value bucket × origin combination. `signal_key` = `"HIGH:CN"` format.
- `CONFIRMED_FRAUD` / `CLEARED` — legacy signal types defined in CHECK constraint but not used by the `get_pattern_stats()` query path (which filters on the three above).

**How `pattern_store` gets populated:**  
`record_signals(db, org_email, analysis_result)` in `pattern_engine.py` line 214. Called from `api/app.py` at:
- Line 1878: after every `POST /api/v1/analyze` (JSON path)
- Line 2219: after every `POST /api/v1/analyze-files` (file upload path)
- Line 3485: inside `_run_bulk_single_analysis()` (bulk path)

Each call upserts three rows: one `SHIPPER_REP`, one `ROUTE_RISK`, one `VALUE_ANOMALY` (only if all required fields are present in the analysis result). Uses SQL `ON CONFLICT DO UPDATE` for idempotent increment behavior.

**This is a separate table from the older `shipment_history` table.** The dashboard analytics (`DashboardAnalytics`, `analytics.py`) reads from `shipment_history`. The Pattern Learning History panel reads from `pattern_store`. These are two parallel stores with different schemas and different keying strategies (`organization_id` UUID vs `organization_email`).

---

## 6. The Refresh Button — Current State

**HTML** (demo.html line 4537):
```html
<button class="ph-load-btn pattern-refresh-btn" onclick="refreshPatternStats(this)">
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
       stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
    <polyline points="23 4 23 10 17 10"/>
    <polyline points="1 20 1 14 7 14"/>
    <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
  </svg>
</button>
```

**What it does when clicked (post-afc61ee fix):**
1. Finds the SVG inside the button via `btn.querySelector('svg, .refresh-icon')`
2. Sets `icon.style.transform = 'rotate(360deg)'` with a 0.6s ease transition — spins the icon
3. After 650ms: resets transform to `rotate(0deg)` with `transition: none`
4. Sets `btn.disabled = true` to prevent double-clicks
5. Calls `fetch(apiUrl() + '/api/v1/pattern-stats', { headers: { Authorization: Bearer ... } })`
6. On 401: calls `handle401()` (session expiry handler)
7. On non-ok: throws Error, caught by the `catch` block, logs to `console.error`
8. On success: calls `renderPatternStats(stats)`, then fades `ph-panel` to 40% opacity and back to 100%
9. `finally` block: re-enables the button

**What it did BEFORE the fix (commit afc61ee):**
- Button had `onclick="fetchAndRenderPatternStats()"` — no `this` reference
- Button content was `&#8635; Refresh` (Unicode circular arrow + text) — no SVG to animate
- No animation, no disabled state, no panel flash
- Function ran silently; user saw zero visual feedback whether it succeeded or failed

**CSS for the button:**
- `.ph-load-btn` (line 943): var(--card) bg, var(--border) border, muted text — the base style
- `.pattern-refresh-btn` (line 949, added afc61ee): overrides with teal tint background (`rgba(27,154,170,0.1)`), teal border, `#4DCFDF` icon color; `display:inline-flex; align-items:center; justify-content:center`
- `.pattern-refresh-btn:hover` (line 962): `background: rgba(27,154,170,0.22); transform: scale(1.08)`
- `.pattern-refresh-btn:active` (line 966): `transform: scale(0.95)`
- `.pattern-refresh-btn:disabled` (line 967): `opacity: 0.5; cursor: not-allowed; transform: none`

The button is also included in the ripple system (line 12561: `'.ph-load-btn'`), so it gets the click-ripple animation from `attachAllRipples()`.

---

## 7. Every Occurrence of Pattern Learning History / pattern-stats in demo.html

| Line | Type | Content |
|------|------|---------|
| 934 | CSS | `.ph-panel {` — panel wrapper styles |
| 939 | CSS | `.ph-header {` — header flex row |
| 943 | CSS | `.ph-load-btn {` — refresh button base style |
| 948 | CSS | `.ph-load-btn:hover` |
| 949 | CSS | `.pattern-refresh-btn {` — new teal override (added afc61ee) |
| 962 | CSS | `.pattern-refresh-btn:hover` |
| 966 | CSS | `.pattern-refresh-btn:active` |
| 967 | CSS | `.pattern-refresh-btn:disabled` |
| 1004 | CSS | `.ph-empty {` — italic faint text for loading/empty states |
| 1007 | CSS | `.pattern-stats-grid {` — 4-column stat grid, small screens |
| 1010 | CSS | `@media(max-width:640px) { .pattern-stats-grid { 2 cols } }` |
| 1083 | CSS | `.pattern-stats-grid { grid-template-columns: 1fr 1fr }` — large screen override |
| 1101 | CSS | `.ph-header .section-label { color: rgba(255,255,255,0.85) }` — visible header text |
| 1242 | CSS | `.ph-reset-btn {` — Reset History button style |
| 1250 | CSS | `.ph-reset-btn:hover:not(:disabled)` |
| 1255 | CSS | `.ph-reset-btn:disabled` |
| 1257 | CSS | `.ph-success {` — post-reset success banner, default hidden |
| 1265 | CSS | `.ph-success.visible { display: flex }` |
| 4532 | HTML | `<div class="ph-panel" id="ph-panel">` — panel container |
| 4533 | HTML | `<div class="ph-header">` |
| 4534 | HTML | `<div class="section-label">Pattern Learning History</div>` — the heading text |
| 4536 | HTML | `<button class="ph-reset-btn" id="ph-reset-btn" onclick="openResetModal()">` |
| 4537 | HTML | `<button class="ph-load-btn pattern-refresh-btn" onclick="refreshPatternStats(this)">` with SVG |
| 4540 | HTML | `<div class="ph-success" id="ph-success">` |
| 4543 | HTML | `<div id="pattern-panel-body"></div>` — content target |
| 4550 | HTML | Reset modal title: "Reset Pattern Learning History" |
| 4559 | HTML | `id="modal-confirm-btn"` — confirm reset button |
| 5101 | JS | `fetchAndRenderPatternStats();` — called on login |
| 8093 | JS | `fetchAndRenderPatternStats();` — called after analysis result render |
| 8856 | JS | `fetchAndRenderPatternStats();` — called after feedback submission |
| 8868 | JS | `function renderPatternStats(stats) {` — definition |
| 8869 | JS | `const patternPanel = document.getElementById('pattern-panel-body');` |
| 8887 | JS | `<div class="pattern-stats-grid">` inside innerHTML template |
| 8964 | JS | `async function fetchAndRenderPatternStats() {` — definition |
| 8966 | JS | `document.getElementById('pattern-panel-body')` |
| 8968 | JS | `patternPanel.innerHTML = '<div class="ph-empty">Loading…</div>';` |
| 8971 | JS | `fetch(apiUrl() + '/api/v1/pattern-stats', ...)` |
| 8977 | JS | `renderPatternStats(stats);` — success path |
| 8981 | JS | `async function refreshPatternStats(btn) {` — definition |
| 8982 | JS | `document.querySelector('[onclick*="refreshPatternStats"]')` |
| 8997 | JS | `fetch(apiUrl() + '/api/v1/pattern-stats', ...)` — in refreshPatternStats |
| 9003 | JS | `renderPatternStats(stats);` — success path in refreshPatternStats |
| 9005 | JS | `document.getElementById('ph-panel')` — for opacity flash |
| 9018 | JS | `async function loadPatternHistory() {` — dead alias |
| 9023 | JS | `.ph-empty` in buildPhTable() — no-data row placeholder |
| 9041 | JS | `async function openResetModal() {` |
| 9048 | JS | `fetch(apiUrl() + '/api/v1/pattern-stats', ...)` — for modal count |
| 9078 | JS | `async function confirmReset() {` |
| 9080 | JS | `document.getElementById('ph-reset-btn')` |
| 9085 | JS | `fetch(apiUrl() + '/api/v1/pattern-history/reset', ...)` |
| 9098 | JS | `fetchAndRenderPatternStats();` — after reset |
| 9101 | JS | `document.getElementById('ph-success')` |
| 12561 | JS | `'#ph-reset-btn', '.ph-load-btn'` in `attachAllRipples()` selector list |

---

## 8. What Is Currently Broken

### Issue A — Silent failure when API is unavailable (HIGH)

**In `fetchAndRenderPatternStats()` at line 8975:**
```javascript
if (!res.ok) return;   // ← if 503 or any error, just bail silently
```
**In both `fetchAndRenderPatternStats()` and `refreshPatternStats()`:**
```javascript
} catch (_) {}   // ← all network errors swallowed silently
```

**Failure scenario:** If `_pattern_db` is `None` at startup (environment variable `PORTGUARD_PATTERN_LEARNING_ENABLED=false`, or DB file missing/corrupt), `GET /api/v1/pattern-stats` returns HTTP 503. The frontend:
1. Shows "Loading…" when first called
2. Gets 503 back
3. `if (!res.ok) return;` — exits silently
4. Panel body stays at `<div class="ph-empty">Loading…</div>` forever
5. The user sees "Loading…" and never knows why

**No user-visible error message is ever shown.** The `refreshPatternStats()` has `console.error` (line 9012) but that only appears in DevTools, not in the UI.

### Issue B — Stale "Loading…" state on subsequent failed refreshes (MEDIUM)

After the first failed fetch, `patternPanel.innerHTML` is no longer empty — it contains "Loading…". On the next call to `fetchAndRenderPatternStats()`, the guard at line 8967:
```javascript
if (patternPanel && !patternPanel.innerHTML.trim()) {
  patternPanel.innerHTML = '<div class="ph-empty">Loading…</div>';
}
```
...does NOT run because the panel is not empty. So the user doesn't even see a loading indicator on retry — they see the same "Loading…" from before, which looks identical to a permanent no-content state.

### Issue C — `pattern_store` is empty until analyses are run (informational, not a bug)

The panel correctly shows the empty state ("Pattern Intelligence Active — Screen your first shipment…") when `has_history: false`. This is correct behavior. But it can look broken to a user who expects to see data from the older `shipment_history` table. The two are separate stores:

- `pattern_store` — written by `record_signals()` on EVERY `/api/v1/analyze` call going forward
- `shipment_history` — the older store used by the Dashboard analytics

A fresh install, or an install that had analyses run before migration 010 was applied, will have empty `pattern_store` and show the empty state.

### Issue D — `loadPatternHistory()` is dead code (LOW)

Function defined at line 9018, not called anywhere. Exists as a leftover from an earlier refactor. Harmless.

### Issue E — The refresh button had no animation before commit afc61ee (FIXED)

Prior to commit `afc61ee` (the most recent commit), the button:
- Used `onclick="fetchAndRenderPatternStats()"` — no reference to `this`, no button access
- Used `&#8635; Refresh` text — no SVG to animate
- Had zero visual feedback on click

This is what the previous task sprint was about and is now fixed.

### Trace of the exact pre-fix failure path

1. User is on the Analyze tab, logged in
2. `fetchAndRenderPatternStats()` runs on login (line 5101)
3. **If the API call succeeds and `has_history: true`:** panel renders correctly — no bug visible here
4. **If the API call succeeds and `has_history: false`:** panel shows empty state — correct behavior
5. **If the API call fails (503, network error, etc.):** panel stuck at "Loading…"
6. User clicks the refresh button to force a reload
7. **Pre-fix:** old `fetchAndRenderPatternStats()` called with no animation — button appears dead, no indication anything happened, silent failure persists
8. **Post-fix (afc61ee):** button spins (good), fetch runs, but on failure still only logs to console — panel still stuck at "Loading…" with no user message

**The element is NOT hidden.** The JS function IS called. The element IDs match. The failure is at the API layer — either a 503 (pattern learning disabled) or a silent catch swallowing a network error — and the frontend provides no user-visible feedback for this failure case.

---

## 9. Last Working State

Based on git history and sprint documents:

**Sprint C (commits 13aec20 → 32c74e7 → d86012f → 89f0594):**
- `13aec20` — `feat(pattern): rewrite pattern_engine.py with 5-function pattern_store API` — the `get_pattern_stats()` function was created here
- `32c74e7` — `feat(pattern): wire pattern_store engine into all three analysis paths` — `record_signals()` wired into `/api/v1/analyze`, `/api/v1/analyze-files`, and bulk analysis
- `d86012f` — `feat(pattern-ui): fix 5 display bugs and deepen intelligence content` — `renderPatternStats()` was rewritten to show the 4-stat grid, health bars, and sectioned shippers/routes/anomalies
- `89f0594` — `fix(pattern-ui): delete duplicate old pattern popup, fix fraud warning clipping, fix shipper row font and badge overflow`

**At commit `89f0594` (the pre-ocean-theme state), the panel was fully functional when:**
- At least one analysis had been run for the authenticated org
- The `/api/v1/pattern-stats` endpoint returned HTTP 200 with `has_history: true`
- The response had the correct field names that `renderPatternStats()` reads

**The panel showed:**
- 4 stat boxes (Shipments Screened, Shippers Tracked, Routes Tracked, Confirmed Fraud)
- Approval Rate bar and Avg Risk Score bar
- High-Risk Shippers section (if any shippers with flag_rate > 30%)
- High-Risk Routes section (if any routes with flag_rate > 30% and ≥ 3 occurrences)
- Value Anomalies section (top 3 VALUE_ANOMALY rows)
- Known-Good Shippers section (shippers with cleared_count > 0)

**The refresh button at that point** had the old `onclick="fetchAndRenderPatternStats()"` with the `&#8635;` character — functionally it would re-fetch and re-render, but with no animation and no visual feedback on failure.

---

## Summary

| Question | Answer |
|----------|--------|
| Panel element | `<div id="ph-panel">` at line 4532, inside `analyze-panel`, always visible on Analyze tab |
| Content target | `<div id="pattern-panel-body">` at line 4543 — innerHTML replaced on every fetch |
| Primary renderer | `renderPatternStats(stats)` at line 8868 |
| Data fetcher | `fetchAndRenderPatternStats()` at line 8964 |
| API endpoint | `GET /api/v1/pattern-stats` — same origin, Bearer auth |
| Backend function | `get_pattern_stats(db, org_email)` in `portguard/pattern_engine.py` line 455 |
| Database table | `pattern_store` in `portguard_patterns.db`, migration 010 |
| Primary key | `(organization_email, signal_type, signal_key)` |
| Refresh button (current) | `onclick="refreshPatternStats(this)"` with SVG spin, disabled state, panel flash |
| Core remaining bug | Silent API failure leaves panel stuck at "Loading…" with no user message |
| Element hidden? | No — never `display:none` |
| IDs match? | Yes — `pattern-panel-body` in HTML matches `getElementById('pattern-panel-body')` in JS |
| API returns error? | Only if pattern learning is disabled (503) or DB is down — frontend swallows silently |
| Data empty? | Panel correctly shows empty state when no analyses have been run yet |
