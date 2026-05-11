# PortGuard Ocean Theme Audit
**Date:** 2026-04-29  
**Files read:** demo.html (all 10,515 lines), api/app.py, api/auth_routes.py, portguard/agents/\*, portguard/bulk_processor.py, portguard/pattern_engine.py, portguard/report_generator.py, portguard/analytics.py, portguard/auth.py, portguard/module_config_db.py, docs/ui_design_system.md, docs/vision.md, docs/SPRINT_LOG.md, docs/bulk_sprint_plan.md, all other docs/\*  
**Scope:** Ocean/maritime visual theme implementation status

---

## 1. Current State — What CSS / JS Exists

### CSS (demo.html)

**`:root` variables (lines 13–72)** — 30 custom properties defined. Full list:

| Variable | Value | Notes |
|---|---|---|
| `--bg-deep` | `#0A1628` | Page canvas — deepest navy |
| `--bg-surface` | `#0F2040` | Card backgrounds |
| `--bg-card` | `#162845` | Inner surfaces, inputs |
| `--bg-card-hi` | `#1A3259` | Hover state elevations |
| `--bg` | `var(--bg-deep)` | Legacy alias |
| `--surface` | `var(--bg-surface)` | Legacy alias |
| `--card` | `var(--bg-card)` | Legacy alias |
| `--card-hi` | `var(--bg-card-hi)` | Legacy alias |
| `--border` | `#1A3259` | Default border |
| `--border-hi` | `#254778` | Elevated border |
| `--teal-900` | `#072B30` | Deep teal |
| `--teal-700` | `#0D6B7A` | Mid teal |
| `--teal-500` | `#1B9AAA` | Primary teal — ocean reference color |
| `--teal-400` | `#22B5C8` | Active/hover teal |
| `--teal-300` | `#4DCFDF` | Focus rings, glow halos |
| `--blue` | `#1B9AAA` | Legacy alias for `--teal-500` |
| `--blue-dim` | `#0D6B7A` | Legacy alias for `--teal-700` |
| `--coral` | `#C0392B` | Container red — secondary color |
| `--amber-500` | `#E8A838` | Sunset gold accent |
| `--text` | `#EDF2F8` | Primary text |
| `--muted` | `#8BAABF` | Supporting text |
| `--faint` | `#4A6880` | Tertiary text, labels |
| `--green` | `#1DB87A` | Approve / cleared |
| `--amber` | `#E8A838` | Warning |
| `--orange` | `#D4773A` | Flag |
| `--red` | `#E05050` | Reject / danger |
| `--purple` | `#9B6CD6` | Review recommended |
| `--glow-teal-sm` | `0 0 10px rgba(27,154,170,.25)` | Small teal glow |
| `--glow-teal-md` | `0 0 20px rgba(27,154,170,.35), 0 0 6px rgba(27,154,170,.2)` | Medium glow |
| `--shadow-card` | `0 2px 16px rgba(0,0,0,.35), 0 1px 3px rgba(0,0,0,.2)` | Card shadow |
| `--shadow-modal` | `0 20px 60px rgba(0,0,0,.6), 0 6px 20px rgba(0,0,0,.3)` | Modal shadow |
| `--radius` | `10px` | Default radius |
| `--radius-sm` | `6px` | Small radius |
| `--radius-lg` | `14px` | Large radius |
| `--radius-xl` | `20px` | XL radius |
| `--ease-out` | `cubic-bezier(0,0,0.2,1)` | Exit/reveal easing |
| `--ease-spring` | `cubic-bezier(0.34,1.56,0.64,1)` | Spring easing |

**Gap vs. design spec (`docs/ui_design_system.md`):** The spec defines ~60 tokens including `--teal-800`, `--teal-100`, `--coral-700/600/400/300/100`, `--amber-700/400/300`, `--success`, `--success-dim`, `--success-glow`, `--warning-dim`, `--danger`, `--danger-dim`, `--danger-glow`, `--flag`, `--flag-dim`, `--purple-dim`, `--border-teal`, `--border-teal-hi`, `--glow-teal-lg`, `--glow-danger-sm/md`, `--glow-success-sm/md`, `--shadow-card-hover`, `--font-ui`, `--font-mono`, and all spacing tokens (`--space-1` through `--space-16`). None of these are implemented. The current `:root` has roughly half the spec'd tokens, many using legacy alias names.

### #wave-bg element (lines 137–148 CSS, lines 2876–2889 HTML)

**What it is:** A `position: fixed; inset: 0; pointer-events: none; z-index: 0` container with one inline SVG. The SVG contains three `<path>` elements — sinusoidal wave curves drawn with `stroke="rgba(27,154,170,.025/.018/.015)"` at opacities 0.025, 0.018, and 0.015. The SVG is `width: 200%` and plays `wave-drift-left` on its wrapper.

**What it is not:** It is not a full ocean scene. It is not canvas-based. It is not multi-layered. It contains no ship, no dock, no cloud layer, no grid overlay.

**Closeness to spec:** The spec (§7) calls for a 5-layer background:
1. Base color (`--bg-deep`) — ✅ present via `body` background
2. Radial gradient glow (`body::before`) — ❌ absent
3. Wave layer (animated SVG, three waves at different speeds/directions) — ⚠️ partially present (one SVG, three paths, but all sharing one animation direction vs. the spec's mixed left/right per wave)
4. Grid overlay (60px nav chart grid, `.015` opacity) — ❌ absent
5. Content (z-index above all) — ✅ present

---

## 2. What Is Working vs. Broken vs. Incomplete

### Working ✅
- **Wave background** — `#wave-bg` renders and animates. The three wave paths drift slowly left via `wave-drift-left 28s linear infinite`. Visually subtle at the spec'd opacity levels.
- **Color palette** — All 30 `:root` variables are correctly applied throughout the CSS. Every component uses the tokens consistently.
- **Logo SVG** — The header and auth card both contain inline ship logomarks with teal hull gradients, coral/amber containers, teal waterline. The design intent is faithfully implemented even though the SVG spec differs slightly from `ui_design_system.md`'s exact geometry.
- **Teal glow on interactive elements** — `--glow-teal-sm` and `--glow-teal-md` are applied to the analyze button, active cards, and focused inputs.
- **Dark navy base** — `--bg-deep: #0A1628` matches the spec's "midnight harbor water" intent.
- **Maritime color references** — Coral (`--coral: #C0392B`) appears on cargo containers in both SVG logos. Amber (`--amber-500: #E8A838`) appears on containers. Teal is the primary interactive color throughout.

### Broken / Not Present ❌
- **`body::before` radial glow** — Not implemented. The spec calls for `radial-gradient(ellipse 80% 50% at 50% -10%, rgba(27,154,170,.08) 0%, transparent 70%)` as a fixed pseudo-element. Nothing exists.
- **Grid overlay** — Not implemented. The spec's 60px navigation-chart SVG pattern at `.015` opacity does not exist anywhere.
- **`ocean-scene` element** — Does not exist. Never referenced. Zero matches in the codebase.
- **Ship spawner** — Does not exist. No JS function for spawning animated ships. No `shipSpawn`, `spawnShip`, or similar identifier anywhere.
- **`getPalette` / `applyPalette`** — Does not exist. No palette switching, no theme selector, no color mode toggle. Zero matches.
- **Cloud layer** — Does not exist. No cloud SVG, no cloud CSS, no cloud animation.
- **Dock SVG** — Does not exist. No dock, pier, or port-infrastructure SVG.
- **Progress bar ship emoji** — Does not exist. The bulk progress bar (`#bulk-progress-fill`) is a plain teal fill with `bulk-progress-indeterminate` keyframe animation. No ship emoji or maritime icon in any progress indicator.
- **`docs/ocean_theme_sprint_plan.md`** — Does not exist. Confirmed with `ls docs/` — file is absent from the repository.

### Incomplete / Partial ⚠️
- **Wave layer** — Present but simplified. The spec calls for three independent wave paths at different Y positions (30%, 55%, 75% from top) with different speeds (28s, 22s, 18s) and **mixed directions** (left, right, left). The current `#wave-bg` has three paths inside one SVG element with one shared `wave-drift-left` animation — meaning all three paths move together at the same speed and direction. The spec's per-wave independence is not implemented.
- **CSS token vocabulary** — Current tokens are a subset of the spec. The `--teal-800`, `--teal-100`, `--coral-*`, `--amber-700/400/300`, `--success*`, `--warning-dim`, `--danger*`, `--flag*`, `--purple-dim`, `--border-teal*`, spacing, and font tokens all exist only in the design spec, not in `:root`.
- **`will-change: transform` on wave** — The spec requires this GPU hint on wave elements. Not present on `#wave-bg svg`.
- **Reduced motion media query** — The spec requires `@media (prefers-reduced-motion: reduce) { .wave-line { animation: none; } }`. Not implemented. Wave animates regardless of user preference.

---

## 3. Every Animation Keyframe Currently Defined

All 21 `@keyframes` definitions, with line numbers:

| Name | Line | Description |
|---|---|---|
| `fade-up` | 98 | opacity 0→1 + translateY(16px→0), used for page-load stagger |
| `banner-alert` | 102 | box-shadow pulse (2 beats) for FLAG/REJECT decision banners |
| `banner-alert-amber` | 106 | same pattern in amber, for REQUEST_MORE_INFO decisions |
| `error-shake` | 110 | horizontal shake (±6px, ±4px) for API errors |
| `success-pulse` | 117 | radial box-shadow pulse for feedback/reset success states |
| `skel-shimmer` | 122 | skeleton shimmer — background-position 200%→-200% |
| `teal-pulse` | 126 | teal box-shadow pulse, used on active pipeline nodes |
| `wave-drift-left` | 142 | `from { transform: translateX(0); } to { transform: translateX(-50%); }` |
| `wave-drift-right` | 143 | `from { transform: translateX(-50%); } to { transform: translateX(0); }` |
| `spin` | 522 | `to { transform: rotate(360deg); }` — **DUPLICATE: also defined at line 1143** |
| `modal-in` | 1049 | scale(.96) translateY(8px) → scale(1) translateY(0), opacity 0→1 |
| `pulse-dot` | 1356 | opacity 1→.35→1, used for pipeline status pulsing dot |
| `toast-in` | 1476 | opacity 0→1 + translateX(20px→0) |
| `toast-out` | 1477 | opacity 1→0 + translateX(0→20px) |
| `pdf-beam-sweep` | 1646 | translateY(-100%→110%) + opacity 0→.8→.8→0, the PDF scan beam |
| `pdf-done-flash` | 1660 | opacity 1→0→.6, the flash when PDF scanning completes |
| `pnode-fill-pulse` | 1772 | pipeline node background fill pulse (opacity .15→.4→.15) |
| `pnode-ring-pulse` | 1787 | pipeline node ring pulse (scale 1→1.4 + opacity .7→0) |
| `conn-fill` | 1846 | pipeline connector fill animation (width 0→100%) |
| `bulk-progress-indeterminate` | 2264 | translateX(-100%→200%) for the indeterminate progress bar |
| `bounce-up` | 2521 | translateY(0→-4px→0), used for bulk slot add button hover |

**Note on `spin` duplicate:** Defined identically at lines 522 and 1143. Both are `to { transform: rotate(360deg); }` with no `from` clause. The second definition silently overrides the first. No runtime error, but it is a CSS hygiene issue.

**Keyframes called for in the spec but not yet implemented:**
- `wave-drift-right` for the mid-wave (defined but not applied to any current element — exists only in the keyframe block)
- Any ship animation (translation across viewport)
- Any cloud drift
- Any dock/port ambient animation

---

## 4. Every CSS Variable Currently Defined in `:root`

See full table in Section 1. Count: **30 variables** (plus 4 legacy aliases). The design spec defines approximately **60 unique tokens**. The gap is ~30 tokens not yet implemented.

---

## 5. Exact Line Numbers — Key Ocean-Theme Elements

| Element | CSS Lines | HTML/JS Lines | Notes |
|---|---|---|---|
| `#wave-bg` CSS block | 137–148 | — | Fixed positioning, overflow: hidden, `@media (max-width:640px) { display:none }` at line 146 |
| `#wave-bg` HTML element | — | 2876–2889 | Contains one `<svg>` with three `<path>` waves |
| `wave-drift-left` keyframe | 142 | — | Shared by the wave SVG; speed: 28s linear infinite |
| `wave-drift-right` keyframe | 143 | — | Defined but not applied to any element in the current HTML |
| Ship logo (header) | — | 2981–3028 | Inline SVG, `id="hdr-hull"` / `id="hdr-bridge"` gradients |
| Ship logo (auth overlay) | — | 2896–2919 | Smaller 32px version, `id="auth-hull"` gradient |
| **`ocean-scene`** | — | — | **DOES NOT EXIST** |
| **Ship spawner** | — | — | **DOES NOT EXIST** |
| **`getPalette`** | — | — | **DOES NOT EXIST** |
| **`applyPalette`** | — | — | **DOES NOT EXIST** |
| **Cloud layer** | — | — | **DOES NOT EXIST** |
| **Dock SVG** | — | — | **DOES NOT EXIST** |
| **Progress bar ship emoji** | — | — | **DOES NOT EXIST** |
| **`docs/ocean_theme_sprint_plan.md`** | — | — | **FILE DOES NOT EXIST** |

---

## 6. CSS Conflicts and Duplicates

### `@keyframes spin` — Duplicate (lines 522 and 1143)
Identical definition appears twice. The second overrides the first silently. No visual bug — both are `to { transform: rotate(360deg); }` with no `from`. Should be deduplicated; keep only the first occurrence at line 522.

### Legacy alias variables
Four legacy tokens remain in `:root` alongside their replacements:
- `--bg` aliases `--bg-deep`
- `--surface` aliases `--bg-surface`
- `--card` aliases `--bg-card`
- `--card-hi` aliases `--bg-card-hi`
- `--blue` aliases `--teal-500`
- `--blue-dim` aliases `--teal-700`

These are used inconsistently throughout the file — some components use `--bg`, others use `--bg-deep`. Not a visual conflict (they resolve to the same value) but they should be unified when the full CSS token migration from §9 of the design spec is executed.

### `wave-drift-right` keyframe defined but unused
Line 143 defines `wave-drift-right` but nothing in the current HTML uses it. The mid-wave that should animate rightward (per the spec) instead shares `wave-drift-left`. The keyframe is available; it just needs to be applied when the wave layer is properly split into three independent elements.

### Reduced motion not handled
Wave animation has no `prefers-reduced-motion` guard. The spec requires:
```css
@media (prefers-reduced-motion: reduce) {
  .wave-line { animation: none; }
  body::before { opacity: .5; }
}
```
Neither clause exists. Users with vestibular disorders get the wave animation unconditionally.

### `body::before` — radial glow missing
The spec defines a `body::before` pseudo-element for the top-edge teal glow. Currently `body::before` is not defined at all. No conflict, but the entire atmospheric layer is absent.

---

## 7. Button States Audit — Ripple Coverage

The design spec calls for the spring-easing hover lift pattern on all interactive cards and buttons. Current implementation:

| Button / Interactive Element | Has Ripple / Hover | Has Spring | Notes |
|---|---|---|---|
| Analyze button (`#analyze-btn`) | ✅ Yes (translateY -1px, shadow grows) | ✅ `--ease-spring` | Fully spec-compliant |
| Auth submit buttons (`#login-btn`, `#register-btn`) | ⚠️ Partial (opacity only) | ❌ No | No translateY lift |
| Quick load buttons (`.quick-btn`) | ✅ Yes (background tint) | ❌ No spring | No lift, just color |
| Tab + button (`#tab-add-btn`) | ⚠️ Partial (color only) | ❌ No | No lift |
| Settings gear button | ✅ Yes (background tint) | ❌ No | No lift |
| Logout button | ✅ Yes (background tint) | ❌ No | No lift |
| Module preset buttons (`.preset-btn`) | ✅ Yes (color + border) | ❌ No | No lift |
| Module toggle switches | ✅ Yes (thumb + track color) | ✅ CSS transition | Not spring but correct |
| Dashboard refresh button | ✅ Yes (background + `spin` on icon) | ❌ No spring | No lift |
| Feedback buttons | ✅ Yes (opacity + border change) | ❌ No spring | No lift |
| Report download button | ✅ Yes (brightness + translateY) | ✅ Spring | Spec-compliant |
| Bulk submit button | ✅ Yes (translateY -1px) | ✅ Spring | Spec-compliant |
| Bulk cancel button | ✅ Yes (color change) | ❌ No | No lift |
| Bulk filter buttons | ✅ Yes (background tint) | ❌ No | No lift |
| Bulk export buttons | ✅ Yes (color + border) | ❌ No | No lift |
| Card hover states | ✅ Yes (border teal + shadow) | ⚠️ `ease-out` not spring | Close but not full spec |

**Summary:** Primary CTA buttons (Analyze, Bulk Submit, Download Report) are fully spec-compliant with spring easing and shadow growth. Secondary and utility buttons have hover states but mostly use `ease-out` instead of `--ease-spring` and lack the `translateY(-1px)` lift. No ripple click-wave effects exist anywhere — the spec describes button active/hover but the word "ripple" in the original audit request refers to the lift effect, which is partially implemented.

---

## 8. JS Errors and Issues (From Code)

Issues identified from reading the source:

### 1. `@keyframes spin` duplicate (line 1143)
Cosmetic issue. Not a runtime JS error, but confirms a CSS-lint failure.

### 2. `wave-drift-right` defined, never applied (line 143)
No JS error, but the rightward wave that the spec requires for the mid-wave is silently missing. The keyframe exists; no element references it.

### 3. No `prefers-reduced-motion` handler
`#wave-bg svg` always animates. For users who have enabled `prefers-reduced-motion: reduce`, the CSS media query for pausing the animation is entirely absent.

### 4. `#wave-bg` hidden on mobile (line 146)
`@media (max-width: 640px) { #wave-bg { display: none; } }` — this is intentional (spec §7 performance note) but means the wave background is completely absent on mobile screens. The `body::before` radial glow (when implemented) should still show on mobile.

### 5. Legacy alias variables (`--bg`, `--surface`, `--card`, `--card-hi`, `--blue`, `--blue-dim`) scattered through ~300+ CSS rules
When the CSS token migration from `ui_design_system.md §9` is eventually executed, every occurrence of these aliases must be replaced. The aliases are currently the only thing preventing breakage — removing them without a find-replace pass would silently un-style large sections of the UI.

### 6. No `will-change: transform` on `#wave-bg svg`
The spec says: "`will-change: transform` on wave lines to hint the GPU." Not present. Wave animation currently promotes to a new compositing layer only if the browser heuristically decides to do so, not explicitly. Minor performance concern on lower-end devices.

---

## 9. Full TODO List — What Needs to Be Done

Ordered by prerequisite dependency:

### Phase 1 — CSS Foundation (no JS required)

**P1-A: Expand `:root` token vocabulary**
Add all missing tokens from `ui_design_system.md §1`:
- `--teal-800: #0A4A54`, `--teal-100: #B8EEF4`
- `--coral-700/600/400/300/100`
- `--amber-700/400/300`
- `--success: #1DB87A`, `--success-dim: #0F6B47`, `--success-glow`
- `--warning-dim: #5C3D08`
- `--danger: #E05050`, `--danger-dim: #5A1515`, `--danger-glow`
- `--flag: #D4773A`, `--flag-dim: #5C2D0A`
- `--purple-dim: #3A1F6E`
- `--border-teal`, `--border-teal-hi`
- `--glow-teal-lg`, `--glow-danger-sm/md`, `--glow-success-sm/md`
- `--shadow-card-hover`
- `--font-ui`, `--font-mono`
- `--space-1` through `--space-16`

**P1-B: Migrate legacy aliases**
Replace all `--bg`, `--surface`, `--card`, `--card-hi`, `--blue`, `--blue-dim` references with their canonical names. Do this as a single atomic pass with `replace_all`.

**P1-C: Add `body::before` radial glow**
```css
body::before {
  content: '';
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  background: radial-gradient(
    ellipse 80% 50% at 50% -10%,
    rgba(27,154,170,.08) 0%,
    transparent 70%
  );
}
```

**P1-D: Add navigation grid overlay**
Add a second fixed layer (z-index 0) using an SVG `<pattern>` at 60px × 60px, teal lines at `.015` opacity.

**P1-E: Split wave layer into three independent elements**
Replace the current single `#wave-bg` SVG with three separate elements:
- `#wave-deep` (Y ~30%, 28s, `wave-drift-left`, opacity .025)
- `#wave-mid` (Y ~55%, 22s, `wave-drift-right`, opacity .035) ← uses the already-defined `wave-drift-right` keyframe
- `#wave-surface` (Y ~75%, 18s, `wave-drift-left`, opacity .02)

Add `will-change: transform` to each.

**P1-F: Add `prefers-reduced-motion` guard**
```css
@media (prefers-reduced-motion: reduce) {
  #wave-deep, #wave-mid, #wave-surface { animation: none; }
  body::before { opacity: .5; }
}
```

**P1-G: Remove `@keyframes spin` duplicate**
Delete the duplicate at line 1143, keep line 522.

---

### Phase 2 — Button / Component Polish

**P2-A: Apply `--ease-spring` and `translateY(-1px)` lift to secondary buttons**
Auth submit buttons, tab buttons, quick-load buttons, dashboard refresh, feedback buttons, bulk cancel, bulk filter buttons, bulk export buttons. All should get `transition: transform 150ms var(--ease-spring), box-shadow 150ms ease-out` and `&:hover { transform: translateY(-1px); }`.

**P2-B: Apply `--shadow-card-hover` + `--glow-teal-sm` to card hover states**
Currently cards use `border-color: var(--border-hi)` on hover. Should add `box-shadow: var(--shadow-card-hover), var(--glow-teal-sm)` and `transform: translateY(-2px)` per spec §6.

---

### Phase 3 — Optional Visual Enhancement (true "ocean scene")

These are the features referenced in the original audit request that **do not exist yet** and require new code:

**P3-A: Ship emoji / icon in progress bar**
Replace the indeterminate teal fill in `#bulk-progress-fill` with a ship 🚢 or `⛴` emoji that traverses the bar left-to-right during processing. Requires JS to animate the emoji position based on progress percentage.

**P3-B: Ambient ship SVG spawner**
A JS function that periodically spawns a small ship SVG crossing the `#wave-bg` layer from right to left at a random vertical position. Low z-index, pointer-events: none, auto-removes after crossing. Would animate using CSS `@keyframes ship-cross { from { transform: translateX(110vw); } to { transform: translateX(-110px); } }`. **Not in the spec — this is a creative addition if desired.**

**P3-C: Dock / port SVG in empty states**
Replace the generic cargo-box SVG in `.bulk-empty-art` (line 3686) and the dashboard empty state (line 3568) with a maritime dock/pier SVG using the brand palette.

**P3-D: `getPalette` / `applyPalette` functions**
If a day/dusk/night palette switcher is desired (harbor at dawn, harbor at dusk, harbor at night), these functions would swap `:root` token values. Currently there is no spec or plan for this feature. Define scope before implementing.

**P3-E: Cloud layer**
Slow-drifting cloud shapes at the top of the viewport. Very low opacity. Not in the current spec — would sit between the radial glow and wave layers.

---

### Summary Table

| Item | Category | Priority | Effort | Status |
|---|---|---|---|---|
| P1-A: Expand `:root` tokens | CSS Foundation | High | Small | ❌ Not started |
| P1-B: Migrate legacy aliases | CSS Foundation | High | Small | ❌ Not started |
| P1-C: `body::before` radial glow | CSS Foundation | High | Tiny | ❌ Not started |
| P1-D: Navigation grid overlay | CSS Foundation | Medium | Small | ❌ Not started |
| P1-E: Split wave layer (3 independent) | CSS Foundation | Medium | Small | ❌ Not started |
| P1-F: Reduced motion guard | CSS Foundation | High | Tiny | ❌ Not started |
| P1-G: Remove `spin` duplicate | CSS Cleanup | Low | Tiny | ❌ Not started |
| P2-A: Spring easing on all buttons | Component polish | Medium | Medium | ⚠️ Partial (CTAs only) |
| P2-B: Card hover states (shadow + lift) | Component polish | Medium | Small | ⚠️ Partial (border only) |
| P3-A: Ship emoji in progress bar | Ocean scene | Low | Small | ❌ Not started |
| P3-B: Ambient ship spawner | Ocean scene | Low | Medium | ❌ Not started |
| P3-C: Dock SVG in empty states | Ocean scene | Low | Small | ❌ Not started |
| P3-D: `getPalette`/`applyPalette` | Ocean scene | Very Low | Large | ❌ Not started |
| P3-E: Cloud layer | Ocean scene | Very Low | Medium | ❌ Not started |

---

## Backend Files — Ocean Theme Impact

All backend files (`api/app.py`, `api/auth_routes.py`, `portguard/agents/*`, `portguard/bulk_processor.py`, `portguard/pattern_engine.py`, `portguard/report_generator.py`, `portguard/analytics.py`, `portguard/auth.py`, `portguard/module_config_db.py`) contain **zero ocean theme content** and require **zero changes** for any ocean theme work. The ocean theme is purely a frontend CSS/HTML/JS concern.

---

*End of audit. Code changes begin in the next sprint.*
