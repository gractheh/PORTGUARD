# Pattern Learning History — Fix Plan
**Date:** 2026-05-15  
**Author:** Session 3 audit  
**Input:** `docs/pattern_history_read_report.md`, `demo.html`, `api/app.py`, `portguard/pattern_engine.py`

---

## SECTION 1 — ROOT CAUSE

### Primary cause (lines 8974–8978, `demo.html`)

`fetchAndRenderPatternStats()` silently swallows every failure:

```javascript
if (!res.ok) return;         // line 8975 — exits silently on 503, 500, 404, etc.
  const stats = await res.json();
  renderPatternStats(stats);
} catch (_) {}               // line 8978 — swallows network errors, JSON parse errors
```

When the server returns `503 PATTERN_LEARNING_DISABLED` or any non-2xx status, the function returns with no side effects. `pattern-panel-body` was previously set to `<div class="ph-empty">Loading…</div>` and that text is never replaced. From the user's perspective the panel is frozen at "Loading…" indefinitely.

### Secondary cause (lines 8966–8968, `demo.html`)

```javascript
if (patternPanel && !patternPanel.innerHTML.trim()) {
  patternPanel.innerHTML = '<div class="ph-empty">Loading…</div>';
}
```

The loading indicator is only injected when the panel is **empty**. On every call after the first (e.g. auto-refresh after an analysis), the guard is false, so no loading state is shown at all. The panel flickers from stale data to new data with no intermediate "fetching" state.

### Secondary cause — `refreshPatternStats` (line 9009, `demo.html`)

```javascript
} catch (err) {
  console.error('Pattern stats refresh failed:', err);  // console only — no UI feedback
}
```

The refresh button's error path only logs to the console. Users see the spin animation complete normally even when the fetch failed.

### What is NOT broken

- The `/api/v1/pattern-stats` endpoint (`api/app.py:2488`) is correct and returns a valid response.
- `get_pattern_stats()` (`portguard/pattern_engine.py:455`) queries `pattern_store` correctly and returns all fields the frontend expects.
- The `renderPatternStats(stats)` function (`demo.html:8868`) correctly handles both empty and data states.
- `record_signals()` correctly populates `pattern_store` after each analysis.
- The refresh button SVG, CSS, and disabled/re-enable logic are correct.

---

## SECTION 2 — BACKEND FIXES NEEDED

### 2a. Endpoint — no changes required

`GET /api/v1/pattern-stats` at `api/app.py:2488` is correct. It:
- Requires valid JWT (via `Depends(get_current_organization)`)
- Returns `503` with `{"code":"PATTERN_LEARNING_DISABLED"}` when `_pattern_db is None`
- Returns `500` with `{"code":"STATS_ERROR","message":"..."}` on exception
- Delegates to `get_pattern_stats(_pattern_db, current_org["email"])` on success

### 2b. `get_pattern_stats()` — no changes required

The function at `portguard/pattern_engine.py:455` already returns the full shape the frontend needs:

```python
{
  "has_history": bool,
  "total_shipments_screened": int,
  "unique_shippers_tracked": int,
  "unique_routes_tracked": int,
  "confirmed_fraud_count": int,
  "high_risk_shippers": [
    {
      "signal_key": str,
      "flag_count": int,
      "occurrence_count": int,
      "fraud_confirmed_count": int,
      "avg_risk_score": float,          # rounded to 1 decimal
      "flag_rate": float,               # flag_count / occurrence_count * 100
      "last_seen": str,                 # ISO 8601
      "last_decision": str | None
    },
    ...  # top 5
  ],
  "high_risk_routes": [
    {
      "signal_key": str,
      "flag_count": int,
      "occurrence_count": int,
      "avg_risk_score": float,
      "flag_rate": float,
      "last_seen": str,
      "last_decision": str | None
    },
    ...  # top 5
  ],
  "value_anomalies": [
    {
      "signal_key": str,
      "flag_count": int,
      "occurrence_count": int,
      "flag_rate": float
    },
    ...  # top 3
  ],
  "cleared_shippers": [
    {
      "signal_key": str,
      "cleared_count": int
    },
    ...  # top 5
  ],
  "avg_org_risk_score": float,
  "total_flags_issued": int,
  "approval_rate": float                # percentage
}
```

### 2c. `record_signals()` — no changes required

Called after every analysis. Upserts `SHIPPER_REP`, `ROUTE_RISK`, and `VALUE_ANOMALY` rows into `pattern_store`. `CONFIRMED_FRAUD` and `CLEARED` rows are written by other code paths. No changes needed.

### 2d. 503 response — handle gracefully in frontend (not backend)

The `503 PATTERN_LEARNING_DISABLED` response is intentional and correct. The fix belongs in the frontend: detect this code and show a distinct "Pattern learning is not enabled" message instead of the generic error toast.

---

## SECTION 3 — FRONTEND FIXES NEEDED

All changes are in `demo.html`. No other file requires modification.

### 3a. Fix `fetchAndRenderPatternStats()` — error handling (lines 8964–8979)

**Current (broken):**
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
    if (!res.ok) return;
    const stats = await res.json();
    renderPatternStats(stats);
  } catch (_) {}
}
```

**Required (fixed):**
```javascript
async function fetchAndRenderPatternStats() {
  if (!_authToken) return;
  const patternPanel = document.getElementById('pattern-panel-body');
  if (patternPanel) {
    patternPanel.innerHTML = '<div class="ph-empty">Loading…</div>';
  }
  try {
    const res = await fetch(apiUrl() + '/api/v1/pattern-stats', {
      headers: { 'Authorization': 'Bearer ' + _authToken },
    });
    if (res.status === 401) { handle401(); return; }
    if (!res.ok) {
      let msg = 'Pattern data unavailable';
      try {
        const errBody = await res.json();
        if (errBody?.code === 'PATTERN_LEARNING_DISABLED') {
          msg = 'Pattern learning is not enabled for this deployment.';
        } else if (errBody?.message) {
          msg = 'Pattern data unavailable: ' + errBody.message;
        }
      } catch (_) {}
      if (patternPanel) patternPanel.innerHTML = '<div class="ph-empty">' + msg + '</div>';
      return;
    }
    const stats = await res.json();
    renderPatternStats(stats);
  } catch (err) {
    if (patternPanel) patternPanel.innerHTML = '<div class="ph-empty">Failed to load pattern data. Check your connection.</div>';
  }
}
```

Key changes:
1. Remove the `!patternPanel.innerHTML.trim()` guard — always set loading state before fetch
2. Replace `if (!res.ok) return;` with error body extraction + inline panel message
3. Replace `catch (_) {}` with inline panel message

### 3b. Fix `refreshPatternStats(btn)` — error feedback (lines 8981–9016)

**Current (broken):**
```javascript
} catch (err) {
  console.error('Pattern stats refresh failed:', err);
}
```

**Required (fixed):**
```javascript
} catch (err) {
  const panel = document.getElementById('pattern-panel-body');
  if (panel) panel.innerHTML = '<div class="ph-empty">Refresh failed. Check your connection.</div>';
}
```

The `console.error` line must be replaced (not supplemented) so that the user sees feedback when refresh fails. The panel already shows the loading state at this point, so writing to `pattern-panel-body` gives visible feedback without requiring a toast.

### 3c. Existing CSS — no changes required

All CSS for `.ph-panel`, `.ph-header`, `.ph-load-btn`, `.pattern-refresh-btn`, `.ph-empty`, `.pattern-stats-grid`, `.pattern-row`, `.pattern-key`, `.pattern-badge`, `.pattern-health-row`, and all animation/hover states was added in commit `afc61ee` and is correct. No CSS changes needed for this fix.

### 3d. `renderPatternStats(stats)` — no changes required

The function at line 8868 already handles both the empty state (`stats.has_history === false` or `stats.total_shipments_screened === 0`) and the full data state. It renders the 4-stat grid, health bars, high-risk shippers with FRAUD badge, high-risk routes, value anomalies, and cleared shippers. No changes needed.

---

## SECTION 4 — THE REFRESH BUTTON

### Current state (correct, no changes needed)

The refresh button at `demo.html:4537` is:
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

CSS (lines 944–967) includes:
- Teal border (`#00B4A0`) with teal text, 8px border-radius, 0.2 alpha teal background on hover
- `.pattern-refresh-btn:disabled` with reduced opacity and `cursor: not-allowed`
- `@keyframes spin-icon` with 360° rotation

The `refreshPatternStats(btn)` function:
- **Spin**: SVG rotates 360° over 0.6s (`icon.style.transform = 'rotate(360deg)'`), resets to 0° at 650ms
- **Disabled state**: `btn.disabled = true` before fetch; `btn.disabled = false` in `finally`
- **Success flash**: `ph-panel` opacity drops to 0.4 then returns to 1.0 over 150ms
- **Failure**: currently `console.error` only — **must be changed to inline panel message** (see Section 3b)

### Only change needed

Replace `console.error('Pattern stats refresh failed:', err)` at line 9009 with inline panel content (see Section 3b).

---

## SECTION 5 — EMPTY STATE VS DATA STATE

### Empty state

Shown when:
- `stats.has_history === false`, OR
- `stats.total_shipments_screened === 0`

Rendered by `renderPatternStats()` as:
```html
<div class="ph-empty">
  No pattern history yet — run an analysis to start building intelligence.
</div>
```

Also shown during loading (before fetch resolves):
```html
<div class="ph-empty">Loading…</div>
```

And on fetch error (after this fix):
```html
<div class="ph-empty">Pattern data unavailable: {message}</div>
<!-- or for 503: -->
<div class="ph-empty">Pattern learning is not enabled for this deployment.</div>
<!-- or for network error: -->
<div class="ph-empty">Failed to load pattern data. Check your connection.</div>
<!-- or for refresh failure: -->
<div class="ph-empty">Refresh failed. Check your connection.</div>
```

### Full data state

Shown when `stats.has_history === true` AND `stats.total_shipments_screened > 0`.

Rendered by `renderPatternStats()` as a sequence of sections inside `#pattern-panel-body`:

1. **4-stat grid** (`.pattern-stats-grid`): Total Screened, Unique Shippers, Unique Routes, Confirmed Fraud — one `.pattern-stat` card each with number + label.

2. **Org health bars** (`.pattern-health-row`): Approval Rate bar (green, `approval_rate`%) and Avg Risk Score bar (red, `avg_org_risk_score / 100`%) with percentage labels.

3. **High-Risk Shippers** section: Each shipper as a `.pattern-row` with `.pattern-key` (name, truncated with `min-width:0; flex:1 1 0`) and a `.pattern-badge-stack` containing:
   - If `fraud_confirmed_count > 0`: red `.pattern-badge.fraud` showing "FRAUD ×N"
   - Always: teal `.pattern-badge` showing "N flags (R%)" where R = `flag_rate` rounded to 1 decimal

4. **High-Risk Routes** section: Each route as `.pattern-row` with route name and `.pattern-badge` showing "N flags (R%)".

5. **Value Anomalies** section: Each anomaly as `.pattern-row` with commodity type and `.pattern-badge` showing "N flags (R%)".

6. **Cleared Shippers** section: Each shipper as `.pattern-row` with name and green `.pattern-badge` showing "✓ cleared N×".

Sections with empty arrays are omitted from the render.

---

## SECTION 6 — STEP BY STEP BUILD ORDER

### Step 1 — Read current state of `demo.html` (lines 8964–9016)

Before editing, re-read the exact current text of `fetchAndRenderPatternStats()` (lines 8964–8979) and `refreshPatternStats(btn)` (lines 8981–9016) to confirm line numbers match. The file is 12,698 lines and was last changed in commit `afc61ee`. Verify the exact strings before using Edit tool.

### Step 2 — Fix `fetchAndRenderPatternStats()` (demo.html ~line 8964)

Using the Edit tool, replace the entire function body with the fixed version from Section 3a:
- Remove the `!patternPanel.innerHTML.trim()` guard
- Always write `Loading…` before the fetch
- Replace `if (!res.ok) return;` with error body extraction + inline message
- Replace `catch (_) {}` with inline error message

### Step 3 — Fix `refreshPatternStats(btn)` error path (demo.html ~line 9009)

Using the Edit tool, replace the single line:
```javascript
  console.error('Pattern stats refresh failed:', err);
```
with:
```javascript
  const panel = document.getElementById('pattern-panel-body');
  if (panel) panel.innerHTML = '<div class="ph-empty">Refresh failed. Check your connection.</div>';
```

### Step 4 — Visual verification

Open `demo.html` in browser (or confirm dev server is running). Log in. Confirm:
- Panel shows "Loading…" briefly then renders data (if `pattern_store` has rows)
- Panel shows "No pattern history yet…" (if `pattern_store` is empty)
- Refresh button spins, disables, re-enables, and shows updated data
- If backend is down, panel shows "Failed to load pattern data. Check your connection." instead of staying at "Loading…"
- No regressions in Dashboard, Analyze, Bulk screens

### Step 5 — Commit

Stage only `demo.html`:
```
git add demo.html
git commit -m "fix(pattern-panel): show error message instead of silent 'Loading…' on fetch failure

- fetchAndRenderPatternStats: always show loading state before fetch
- fetchAndRenderPatternStats: replace silent !res.ok return with inline error message
- fetchAndRenderPatternStats: replace catch (_) {} with inline error message
- refreshPatternStats: replace console.error with inline panel error message"
```

### Step 6 — Push

```
git push origin master
```

Render will auto-deploy from the push (direct GitHub integration, no Blueprint/render.yaml needed).

---

## Summary table

| Location | Line | Change | Type |
|---|---|---|---|
| `demo.html` | ~8964 | Remove `!innerHTML.trim()` guard | Bug fix |
| `demo.html` | ~8975 | Replace `if (!res.ok) return;` with error extraction + message | Bug fix |
| `demo.html` | ~8978 | Replace `catch (_) {}` with inline message | Bug fix |
| `demo.html` | ~9009 | Replace `console.error(...)` with inline panel message | Bug fix |
| `api/app.py` | — | No changes | — |
| `portguard/pattern_engine.py` | — | No changes | — |
