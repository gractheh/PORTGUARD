# Toggle & Emoji Read Report
**Date:** 2026-05-15
**Scope:** demo.html full read — background toggle buttons, setBgMode/applyBgMode, ocean scene init, emoji audit
**Status:** Read-only. No changes made.

---

## 1. Brain Emoji U+1F9E0 — Location in demo.html

**Not present.**

A Python scan (`'\U0001F9E0' in content`) returned `False`. U+1F9E0 (🧠) does not appear anywhere in demo.html — not in HTML, not in a JS template literal, not in a CSS `content` value, not in a comment. Also checked U+1F9E1 through U+1F9E5: all absent.

---

## 2. Every Emoji and Symbol Remaining in UI Chrome

The file contains no multi-codepoint emoji (no skin tone modifiers, no ZWJ sequences, no flag sequences). All symbol characters are in the Miscellaneous Symbols or Dingbats Unicode blocks (U+2600–U+27BF). Every occurrence is inside the `<script>` block (JS), except one which is in the `<style>` block (CSS).

| Line  | Codepoint | Char | Location | Full context |
|-------|-----------|------|----------|--------------|
| 3235  | U+2713    | ✓    | CSS (`<style>` block) | `.cert-chip.detected::before { content: '✓ '; }` — the green checkmark prepended to detected certification chips |
| 5344  | U+2713    | ✓    | JS template literal | `` `…<span>${customText || 'Text extracted ✓'}</span>` `` — PDF badge "done" state label |
| 5347  | U+26A0    | ⚠    | JS template literal | `` `<span>⚠</span><span>${customText || 'Partial extraction'}</span>` `` — PDF badge "warn" state |
| 5350  | U+2715    | ✕    | JS template literal | `` `<span>✕</span><span>${customText || 'Extraction failed'}</span>` `` — PDF badge "error" state |
| 8010  | U+267B    | ♻    | JS statement | `` badge.textContent = `♻ Sustainability: ${grade}`; `` — sustainability badge label on the decision banner |
| 8137  | U+2713    | ✓    | JS statement | `labelEl.textContent = '✓ Copied!';` — share link clipboard feedback |
| 8522  | U+2713    | ✓    | JS string literal | `'Text extracted ✓'` — `badgeText` string for extraction-complete state |
| 9407  | U+2715    | ✕    | JS template literal | `` `<button class="dash-toast-close" aria-label="Dismiss">✕</button>` `` — toast dismiss button |
| 11718 | U+2713    | ✓    | JS template literal | `` `<span class="save-indicator" id="save-ind-${mod.module_id}">Saved ✓</span>` `` — module toggle save indicator |

**Unique characters:** ✓ (×6), ✕ (×2), ⚠ (×1), ♻ (×1). Zero occurrences of any character in the U+1F000–U+1FFFF emoji block.

---

## 3. Background Toggle Buttons — Exact HTML

Lines 4079–4086:

```html
<!-- Background mode toggle -->
<div class="bg-mode-toggle">
  <div class="bg-mode-label">Background</div>
  <div class="bg-mode-options">
    <button class="bg-mode-btn active" id="bg-mode-full"    onclick="setBgMode('full')">Full</button>
    <button class="bg-mode-btn"        id="bg-mode-minimal" onclick="setBgMode('minimal')">Minimal</button>
    <button class="bg-mode-btn"        id="bg-mode-plain"   onclick="setBgMode('plain')">Plain</button>
  </div>
</div>
```

**IDs:** `bg-mode-full`, `bg-mode-minimal`, `bg-mode-plain`

**onclick handlers:** `setBgMode('full')`, `setBgMode('minimal')`, `setBgMode('plain')`

**data-mode attributes:** None. No button has a `data-mode` attribute of any kind.

**Other attributes:** No `type="button"`, no `aria-pressed`, no `title`. Only `class`, `id`, and `onclick`.

**Initial active state:** `bg-mode-full` has the `active` class hardcoded in HTML. The other two do not.

These buttons are inside the settings drawer (`.drawer-body`, `id="drawer-body"`).

---

## 4. setBgMode — Does It Exist? What Does It Do?

**It exists.** Lines 12556–12559, inside the second `<script>` block's IIFE:

```js
function setBgMode(mode) {
  applyBgMode(mode);
  try { localStorage.setItem('portguard_bg_mode', mode); } catch (_) {}
}
```

**What it does:**
1. Calls `applyBgMode(mode)` — delegates all DOM and animation work there
2. Saves the chosen mode string to `localStorage` under the key `'portguard_bg_mode'` so the preference survives page reload
3. The `try/catch` silently swallows any `SecurityError` (e.g., when localStorage is blocked in private browsing)

**Scope:** `setBgMode` is declared as a `function` declaration inside `(function () { 'use strict'; ... })()` — an IIFE. It is **not assigned to `window.setBgMode`** anywhere. It is not accessible from global scope. This is critical — see item 10.

---

## 5. applyBgMode — Does It Exist? What Does It Do for mode='plain'?

**It exists.** Lines 12520–12554, inside the same IIFE.

Full function:

```js
function applyBgMode(mode) {
  document.body.classList.remove('bg-minimal', 'bg-plain');
  if (mode === 'minimal') {
    document.body.classList.add('bg-minimal');
    const oceanSvg = document.getElementById('ocean-water-svg');
    if (oceanSvg && oceanSvg.pauseAnimations) oceanSvg.pauseAnimations();
    if (window._shipSpawnerInterval) {
      clearInterval(window._shipSpawnerInterval);
      window._shipSpawnerInterval = null;
    }
  } else if (mode === 'plain') {
    document.body.classList.add('bg-plain');
    const oceanSvg = document.getElementById('ocean-water-svg');
    if (oceanSvg && oceanSvg.pauseAnimations) oceanSvg.pauseAnimations();
    if (window._shipSpawnerInterval) {
      clearInterval(window._shipSpawnerInterval);
      window._shipSpawnerInterval = null;
    }
  } else {
    // 'full' — resume everything
    const oceanSvg = document.getElementById('ocean-water-svg');
    if (oceanSvg && oceanSvg.unpauseAnimations) oceanSvg.unpauseAnimations();
    if (!window._shipSpawnerInterval) {
      window._shipSpawnerInterval = setInterval(spawnShip, 12000);
      spawnShip();
    }
  }
  ['full', 'minimal', 'plain'].forEach(m => {
    const btn = document.getElementById('bg-mode-' + m);
    if (btn) btn.classList.toggle('active', m === mode);
  });
}
```

**What `applyBgMode('plain')` does, step by step:**
1. `document.body.classList.remove('bg-minimal', 'bg-plain')` — strips both classes from body first
2. `document.body.classList.add('bg-plain')` — adds `bg-plain` class to body
3. Gets `#ocean-water-svg` element, calls `pauseAnimations()` — pauses the SMIL wave animations
4. Clears `window._shipSpawnerInterval` via `clearInterval`, sets it to `null` — stops new ships spawning
5. Iterates `['full', 'minimal', 'plain']` and toggles `.active` class on each button element

**What it does NOT do:**
- It does **not** directly call `.style.display = 'none'` on any element
- It does not directly hide `#ocean-scene`
- It relies entirely on a CSS rule to hide the ocean background: `body.bg-plain #ocean-bg { display: none; }` (line 3408)

**The CSS rule targets `#ocean-bg`. The actual element in the DOM is `<div id="ocean-scene">` (line 3789). There is no element with `id="ocean-bg"` anywhere in the file.** The rule never matches. The ocean background is never hidden.

---

## 6. initOceanScene — When Is It Called?

Lines 12607–12675 define the function. Lines 12677–12682 call it:

```js
// ── 15. DOMContentLoaded guard ────────────────────────────────────────────
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initOceanScene);
} else {
  initOceanScene();
}
```

**Timing:** The `<script>` block containing this code is at the bottom of `<body>` (the script tag is at line 12031; `</body>` is at line 12686). By the time the browser reaches a `<script>` at the bottom of `<body>`, the DOM has been fully parsed. `document.readyState` is `'interactive'` at that point — not `'loading'`. Therefore the `else` branch fires and `initOceanScene()` is called **synchronously and immediately** as the IIFE executes. It does not wait for the `load` event (images, stylesheets). It runs before `load` but after DOMContentLoaded.

**Guard:** The function sets `_oceanInit = true` on its first line (after the guard check `if (_oceanInit) return;`). Any subsequent call — including one that might theoretically be triggered by the DOMContentLoaded listener if `readyState` were still `'loading'` — would be a no-op.

---

## 7. startShipSpawner — Interval Variable

```js
// ── 11. startShipSpawner() ────────────────────────────────────────────────
function startShipSpawner() {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  // 3 staggered initial ships so harbor feels alive on load
  spawnShip();
  setTimeout(spawnShip, 3000);
  setTimeout(spawnShip, 6000);
  // Then maintain population at MAX_SHIPS, checking every 12s
  window._shipSpawnerInterval = setInterval(spawnShip, 12000);
}
```

**Variable:** `window._shipSpawnerInterval`

**It is a property on `window`.** It is not a local variable. Both `applyBgMode` (which clears it) and `startShipSpawner` (which sets it) reference `window._shipSpawnerInterval`. The `full` branch in `applyBgMode` also reads it before deciding to restart the spawner:

```js
if (!window._shipSpawnerInterval) {
  window._shipSpawnerInterval = setInterval(spawnShip, 12000);
  spawnShip();
}
```

This is consistent and correct — the window property is the shared handle for the interval.

---

## 8. Page Load — Exact Sequence of Function Calls

1. **Browser parses `<body>` (line 3786)** — no `onload` attribute
2. **First `<script>` block (line 4899) executes** — `'use strict';` at top level, NOT an IIFE. All `function` declarations at this level are globally accessible on `window`. This block defines: `authHeaders`, `authedJson`, `authedForm`, and every application-layer function (auth, panels, dashboard, bulk, pattern history, module settings, PDF handling, etc.) — hundreds of functions. No ocean-scene code here.
3. **HTML elements continue parsing** (the drawer, nav, panels, ocean-scene HTML, etc.)
4. **Second `<script>` block (line 12031) executes** — this is the IIFE `(function () { 'use strict'; ... })()`. It executes immediately as the parser reaches it. Defines all ocean-scene functions inside the closure (NOT on window).
5. **IIFE reaches lines 12677–12682** — `document.readyState` is `'interactive'` (DOM parsed, not `'loading'`), so the `else` branch fires and `initOceanScene()` is called synchronously.
6. **`initOceanScene()` calls, in order:**
   - `_oceanInit = true` (set guard)
   - `shipsLayer.style.overflow = 'visible'`
   - `oceanSvg.setAttribute('overflow', 'visible')` on `#ocean-water svg`
   - `getPalette(new Date().getHours())` → returns palette object
   - `applyPalette(palette)` → sets all CSS custom properties on `:root`
   - `positionClouds()` → randomizes cloud starting x positions
   - `localStorage.getItem('portguard_bg_mode')` → if a saved mode exists: `applyBgMode(savedMode)`
   - `startShipSpawner()` → (only if body does not have `.bg-minimal` or `.bg-plain`)
     - `spawnShip()` (immediate)
     - `setTimeout(spawnShip, 3000)`
     - `setTimeout(spawnShip, 6000)`
     - `window._shipSpawnerInterval = setInterval(spawnShip, 12000)`
   - `attachAllRipples()` → `MutationObserver` + initial ripple wiring
   - `injectNavTabIcons()` → ensures nav SVGs are visible
   - `injectQuickLoadEmojis()` → injects SVG icons into quick-load buttons, then calls `injectAnalyzeBtnEmoji()`
   - `setInterval(palettePoller, 60_000)` → 60-second palette update loop
   - `window.addEventListener('resize', ...)` → resize handler registration
   - `document.addEventListener('visibilitychange', ...)` → tab visibility handler

---

## 9. Clicking 'Plain' — Exact Trace

**What actually happens in the browser:**

1. User clicks the "Plain" button
2. Browser fires the `onclick` handler: evaluates `setBgMode('plain')`
3. The onclick handler executes in **global scope** — it looks for `setBgMode` on the scope chain: element → document → `window`
4. `setBgMode` is defined **inside the IIFE** in the second `<script>` block. It is NOT on `window`. It is not reachable from global scope.
5. **`ReferenceError: setBgMode is not defined` is thrown.**
6. Execution stops. Nothing else happens.

**Result:**
- `applyBgMode` is never called
- `document.body` does not gain the `bg-plain` class
- `#ocean-scene` is not hidden (and would not be even if the error were fixed — see item 10)
- Ocean animations continue
- Ships continue spawning
- Button active states do not update
- Nothing is saved to localStorage

**Hypothetical trace if `setBgMode` were globally accessible:**

1. `setBgMode('plain')` → `applyBgMode('plain')`
2. Body gets `bg-plain` class
3. CSS engine evaluates `body.bg-plain #ocean-bg { display: none; }` (line 3408)
4. No element with `id="ocean-bg"` exists in the DOM — selector does not match anything
5. `#ocean-scene` (`<div id="ocean-scene">`, line 3789) remains visible
6. `body.bg-plain .app-layout { background: var(--bg); }` (line 3409) does fire — sets `.app-layout` background color
7. `pauseAnimations()` fires on `#ocean-water-svg` — SMIL waves freeze
8. `window._shipSpawnerInterval` is cleared — no new ships will spawn; existing ships finish their crossing animations
9. Toggle button active classes update — "Plain" becomes visually active
10. Mode saved to localStorage
11. **Visual result:** Ocean is fully visible and rendered on screen. Waves frozen. No new ships. App content area background changes. The toggle appears to do something (button highlights, wave motion stops) but the ocean itself does not disappear.

**Does `#ocean-scene` get `display:none`?** NO — under any code path.
**Does body background change?** Partially — `.app-layout` background changes if bug 1 were fixed, but the ocean overlay (which sits behind everything at `z-index: 0`) does not.

---

## 10. Why the Toggle Is Not Working — Root Cause Analysis

### Is `applyBgMode` called when the button is clicked?

**NO.**

The onclick attribute `onclick="setBgMode('plain')"` executes in global scope and cannot find `setBgMode`. `ReferenceError` is thrown before `setBgMode` body runs. `applyBgMode` is therefore never called.

**Root cause:** `setBgMode` and `applyBgMode` are both declared inside `(function () { 'use strict'; ... })()` — the IIFE that wraps the entire Ocean Scene JS engine. Functions declared inside an IIFE are in the IIFE's closure scope, not on `window`. Neither function is assigned to a `window` property. The onclick attribute is a dead wire.

**The fix:** Expose the function to global scope, e.g. by adding `window.setBgMode = setBgMode;` at the end of the IIFE body, just before the closing `})();`.

---

### Does `#ocean-scene` actually get hidden?

**NO** — and there are two independent reasons.

**Reason 1 (masked):** `setBgMode` is unreachable from onclick, so `applyBgMode` never runs, so `body.bg-plain` is never added, so no CSS rule fires at all.

**Reason 2 (the underlying CSS bug):** Even if `setBgMode` were globally accessible and `body.bg-plain` were added successfully, the CSS rule at line 3408 is:

```css
body.bg-plain #ocean-bg { display: none; }
```

The ocean scene element in the HTML at line 3789 is:

```html
<div id="ocean-scene" aria-hidden="true">
```

The id is `ocean-scene`. There is no element with `id="ocean-bg"` anywhere in demo.html. The CSS selector `#ocean-bg` matches nothing. `#ocean-scene` never receives `display: none`.

**The fix (after fixing reason 1):** Change the CSS rule to target `#ocean-scene`:
```css
body.bg-plain #ocean-scene { display: none; }
```

---

### Does `initOceanScene` run AFTER `applyBgMode` and override it?

**NO.**

`initOceanScene` is guarded by a module-level boolean:

```js
let _oceanInit = false;

function initOceanScene() {
  if (_oceanInit) return;   // ← early exit on every call after the first
  _oceanInit = true;
  // ... all initialization ...
}
```

`initOceanScene` runs exactly once — synchronously when the IIFE executes on page load. Any user action (like clicking a toggle button) happens after `initOceanScene` has already returned and `_oceanInit` is `true`. Even if `initOceanScene` were somehow called again, it would be a no-op. There is no re-initialization issue. This is not a contributing factor.

---

### Is the ship spawner interval on `window` or a local variable?

**On `window`.** The setInterval handle is stored as `window._shipSpawnerInterval` (line 12455 in `startShipSpawner`). `applyBgMode` reads and clears it via `window._shipSpawnerInterval` (lines 12528–12531 and 12536–12539). The 'full' branch of `applyBgMode` also reads it before restarting (line 12544). All three references are consistent. This part is correctly implemented and is not a contributing factor.

---

### Are the button onclick attributes correct?

**NO** — they reference a function that is not in global scope.

The attributes themselves are syntactically valid: `onclick="setBgMode('full')"`, `onclick="setBgMode('minimal')"`, `onclick="setBgMode('plain')"`. The function name matches exactly what is defined in the IIFE. The string arguments are correct mode identifiers.

The problem is not the syntax — it is scope. `setBgMode` is inside the IIFE. Inline onclick attributes execute in global scope. The function is not exported from the IIFE. Every button click throws `ReferenceError: setBgMode is not defined`.

---

## Summary: Two Bugs, Stacked

| # | Bug | Severity | Effect |
|---|-----|----------|--------|
| 1 | `setBgMode` is inside an IIFE and not exported to `window`. `onclick` attributes execute in global scope and cannot find it. | **Fatal** | Every button click is a no-op (ReferenceError). Nothing works. |
| 2 | CSS rule `body.bg-plain #ocean-bg { display: none; }` targets `#ocean-bg` but the element is `id="ocean-scene"`. | **Secondary** | Even after fixing bug 1, the 'plain' mode would not hide the ocean scene. Ships would stop, waves would pause, but the ocean background would remain fully visible. |

Bug 2 is currently masked by bug 1. Both must be fixed for the 'plain' toggle to work correctly.
