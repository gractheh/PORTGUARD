# PORTGUARD Sprint Log

---

## Sprint: Ocean Theme — Phases 3–6 + Audit/Fix
**Date:** 2026-05-11  
**Branch:** master  
**Status:** Closed

---

### What was built

A full-screen living ocean scene layered behind the PortGuard UI, implemented across four phases (3–6) plus a final audit/fix pass.

#### Phase 3 — JavaScript Engine
- **Time-of-day palettes** — 7 named periods (night / dawn / morning / midday / afternoon / sunset / dusk), each with 18 CSS custom properties (sky, ocean, celestial body, clouds, stars). Palette applied automatically on load via `getPalette(hour)` + `applyPalette(p)`.
- **Star canvas** — HTML5 Canvas drawn via Mulberry32 seeded PRNG; 180 stars with radius variation and glow halos. Opacity driven by palette; skipped below 0.05.
- **Ship spawner** — 3 ship types (container ship, tugboat, sailboat) with weighted frequency, random scale, speed, and vertical position. Max 4 concurrent ships; 3 staggered on load, then every 12s. Ships self-remove via `animationend` + safety `setTimeout`.
- **Button ripples** — Click-positioned `span.ripple-wave` sized to `max(width, height) * 1.5`. `MutationObserver` catches dynamically added buttons. Neutral `rgba(255,255,255,.12)` ripple.

#### Phase 4 + 6 — Button & Navigation / Auth + Dashboard Polish
- Analyze button `btn-float-idle` 2px bob animation (pauses on hover/focus)
- Quick-load emoji bounce on hover via `quick-bounce` keyframe
- Nav tab maritime icons injected by JS (`⚓`, `📊`, `📦`)
- Stat pills staggered `stat-bob` with 0–2.5s spread across 6 pills
- Auth overlay frosted glass (`backdrop-filter: blur(12px)`)
- Auth card frosted glass with `!important` to override legacy rules
- Decision banners frosted glass overlay
- Hero H1 gradient text clip
- Dashboard empty state ship harbor SVG scene (inline style prevents 72px circle clip)

#### Phase 5 — Bulk Upload Maritime Polish
- Drop zones: ship/anchor SVGs, hover lift + glow, drag-over state, `backdrop-filter: blur(6px)`
- Progress bar: frosted glass wrap, height 12px, `overflow: visible`, `::after` 🚢 emoji riding the fill edge via `shipBob` animation
- Cargo empty state illustration

#### Audit + Fix Pass
8 bugs fixed — see `docs/ocean_theme_test_results.md` for full details:
1. `ship-cross-left` keyframe — ships were visible for ~2% of duration; rewritten with `calc(100vw + 400px)` math
2. `ship-cross-right` keyframe — `scaleX(-1) translateX` direction was inverted; fixed with correct transform order
3. `spawnShip()` JS — conflicting `right`/`left` offset + stale inline `scaleX(-1)` removed; unified to `left:0`
4. `#cloud-5` — started at `left:-50px` drifting further left; fixed to `left:100vw`
5. Stat-bob delays tightened from 0–1.25s to 0–2.5s spread
6. `prefers-reduced-motion` missing 3 suppressions (hero badge, progress ship, drop zone transform)
7. `btn-float` amplitude reduced from 3px to 2px
8. `--ripple-color` changed from teal to neutral white (`rgba(255,255,255,.12)`)

Final verification added 5 camelCase keyframe aliases (`floatIdle`, `anchorSpin`, `statBob`, `btnBounce`, `rippleAnim`) and normalized palette `h:` format for build-plan spec compliance.

---

### Files changed

- **`demo.html`** — all ocean theme CSS and JS (single-file app); ~500 lines added across Phases 3–6 + fixes
- **`docs/ocean_theme_build_plan.md`** — spec reference (pre-existing, not modified)
- **`docs/ocean_theme_test_results.md`** — created; 8 bugs documented, 30-item checklist all green

---

### Validation results

41/41 automated checks green:
- All HTML scene layer IDs present (`ocean-scene`, `sky-layer`, `ships-layer`, `cloud-layer`, `stars-canvas`, `horizon-layer`, `ocean-water`, `celestial-body`)
- All JS functions present (`getPalette`, `applyPalette`, `drawStars`, `buildContainerShip`, `buildTugboat`, `buildSailboat`, `spawnShip`, `initOceanScene`, `attachAllRipples`)
- 7 PALETTES defined with correct `h:[start,end]` format
- All keyframes present (including camelCase aliases)
- `prefers-reduced-motion` block present
- `-webkit-backdrop-filter` vendor prefix present
- Script tag balance correct (5 open, 5 close)
- Single DOCTYPE

---

## Sprint: Bulk Upload — Full Implementation + QA Pass
**Date:** 2026-04-29  
**Branch:** master  
**Status:** Closed

---

### What was changed

#### Backend (`api/app.py`, `portguard/pattern_db.py`)

- **`POST /api/v1/analyze/bulk`** — rewired to synchronous execution via `asyncio.gather`. All rows processed before HTTP response; no polling loop required. Returns `data.results[]` with per-row `result_id`, `reference_id`, `decision`, `risk_score`, `flags`, `sustainability_rating`, `sustainability_signals`, `active_modules_snapshot`.
- **`GET /api/v1/results/{result_id}`** — new JWT-protected endpoint. Returns stored `AnalyzeResponse` JSON (200), 404 if unknown, 403 if result belongs to a different org. Used by the single-result share-link flow.
- **`PatternDB.get_result_owner(analysis_id)`** — new method that returns the owning `organization_id` without leaking row data, enabling the 404 vs 403 distinction in the results endpoint.
- **`import json` hoisted** — was only imported locally inside functions; moved to module-level to fix `RESULT_CORRUPT` 500 on the new endpoint.
- **Dead code removed** — unreachable `return _pdf_response(pdf_bytes, shipment_id)` line at end of `bulk_csv_template()` (refactoring artifact).

#### Frontend (`demo.html`)

- **Bulk submit** — synchronous response model. Single POST awaited; results rendered from `_bulkRenderFromResponse(data)` immediately. No polling. AbortController wired to Cancel button. Indeterminate progress bar animation while request is in-flight.
- **`_bulkRenderFromResponse(data)`** — new function maps POST response shape (`data.results[]`) to `_bulkAllResults`. Handles `reference_id`, `result_id`, `flags`, `sustainability_rating`, `sustainability_signals`, `active_modules_snapshot`.
- **Share links** — per-row chain-link button copies `/#result/{result_id}`. Single-doc share button writes `/#result/{shipmentId}`. Deep-link routing: `#result/` hash → `_pendingResultId`; `?batch=` query → `_pendingBatchId`. Both restored after login via `hideAuthOverlay()`.
- **`_loadResultById(resultId)`** — fetches `GET /api/v1/results/{id}`; routes 403/404 to `showError()`; success calls `renderResults(data)`.
- **PDF preview** — `.bulk-slt-pdf-panel` capped at `max-height: 600px; overflow-y: auto`. Bulk thumb wraps use `width: auto`. `--thumb-h` CSS variable set per-wrap in `_bulkPdfRenderThumb()` for independent beam calibration.
- **Per-row beam animation** — `enabled_modules` list from `_moduleToggles` passed in manual batch POST body.
- **CSV export** — built client-side from `_bulkAllResults`; includes `sustainability_grade`, `sustainability_signals`, `active_modules` columns.
- **Polish pass** — `.bulk-badge.TIMEOUT` CSS added; sustainability badge colors normalized to spec (A `#22c55e`, B `#14b8a6`, C `#f59e0b`, D `#ef4444`, N/A `#6b7280`); `.bulk-table-wrap` changed from `overflow: hidden` to `overflow-x: auto`; `_bulkShowProgressError` no longer disables Cancel button (user can always start over); `bulkSort()` now clears `.sorted` from all headers before setting the active one; `expandId` and ref cell null-guarded for rows with missing `ref`.
- **Debug cleanup** — removed 3 `console.log` statements from login flow (logged email and full auth response — a privacy/security concern in production).

---

### Known limitations

- **ZIP upload** — ZIP parsing is server-side; very large ZIPs (> 50 entries) are accepted but each entry's text is truncated at the API's per-document limit. No per-entry progress is shown.
- **Batch result persistence** — individual `result_id` values are persisted per-row for share links. The batch-level `batch_id` is still returned but the polling endpoints are effectively unused; `?batch=` deep-links route to the legacy GET-results path which requires the batch to exist in the DB.
- **No sticky columns** — Reference ID and Decision columns scroll off on very narrow screens. `overflow-x: auto` is enabled but `position: sticky` on those columns was not added (border-collapse interaction on some browsers requires extra handling).
- **PDF beam animation in bulk** — beam height calibrated via `--thumb-h` CSS var set after render. If the PDF.js render is very fast the beam may not be visible before it completes. Not a bug — intentional UX tradeoff.
- **JWT secret not persisted** — `PORTGUARD_JWT_SECRET` env var not set on local dev; server restart invalidates all existing tokens. Set the env var before deploying to Render to avoid this.
