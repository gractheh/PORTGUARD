# Toggle & Emoji Fix Plan
**Date:** 2026-05-15
**Scope:** Background toggle wiring, emoji-to-SVG replacement
**Inputs:** docs/toggle_emoji_read_report.md, demo.html (full read)
**Status:** Plan only. No code changes.

---

## SECTION 1 — BRAIN EMOJI FIX

### Presence in demo.html

U+1F9E0 (🧠) is **not present** anywhere in demo.html. The read report confirmed this with a direct codepoint scan (`'\U0001F9E0' in content` → `False`). It is absent from HTML, JS, CSS, and comments.

### Where it was likely intended

The function at line 12562 is named `injectAnalyzeBtnEmoji()` — its name implies it was originally injecting an emoji (presumably the brain emoji, fitting for an AI analysis tool), which was later replaced in a prior sprint with an anchor SVG. The Pattern Intelligence section header at line 8643 also uses `_piAnchorSvg` (a maritime anchor icon) to lead the "Pattern Intelligence" title, another candidate location for a brain emoji.

There are no JS template literals, HTML character references, or CSS content values that contain U+1F9E0.

### Action

No change required for the brain emoji — it is already absent.

If U+1F9E0 is intentionally re-introduced in future (e.g., to badge the Pattern Intelligence section), the correct replacement SVG to use instead is a 12×12 inline SVG head/brain silhouette, or a network/circuit icon representing AI. Using the file's established SVG format:

```
width="12" height="12" viewBox="0 0 24 24" fill="none"
stroke="currentColor" stroke-width="2"
stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
```

The specific path data for the brain is complex; the existing anchor SVG (`_piAnchorSvg`) already serves this purpose in the Pattern Intelligence section and should remain. No new icon is needed.

### JS template literal check

Because U+1F9E0 is absent from the file entirely, it does not appear in any JS template literal. No template literal fix is needed.

---

## SECTION 2 — ALL OTHER EMOJI FIXES

Nine occurrences of four distinct symbol characters remain. All are in the Miscellaneous Symbols / Dingbats Unicode blocks (U+2600–U+27BF), not in the U+1F000+ emoji block. They may render as emoji glyphs on some systems (notably ⚠ and ♻ on iOS/macOS) and as monochrome text on others, producing inconsistent rendering. All should be replaced or removed.

### SVG design standard for all replacements

Every SVG in this file follows one pattern:

```
width="N" height="N" viewBox="0 0 24 24" fill="none"
stroke="currentColor" stroke-width="2"
stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
```

Use `width="12" height="12"` for characters embedded in flowing text, `width="13" height="13"` to match existing inline icon sizes. Apply `style="vertical-align:-1px"` when the SVG sits next to text in a `<span>`.

---

### Occurrence 1 — Line 3235 · U+2713 ✓ · CSS `<style>` block

**Current:**
```css
.cert-chip.detected::before { content: '✓ '; }
```

**Context:** A CSS pseudo-element prepending a checkmark to certification chips that are detected. The character is in a `content:` value — it renders as a text glyph using the font stack, not as a color emoji. On desktop browsers with standard fonts this is acceptable. On some mobile systems it may render in color emoji style.

**Fix approach:** CSS `content:` cannot embed an SVG element directly. Two options:

- Option A (minimal): Replace the literal Unicode character with its CSS-safe hex escape `'\2713 '` — this forces text-glyph rendering rather than emoji presentation. No visual difference in most browsers; guarantees no emoji color rendering.
- Option B (thorough): Remove the `content` rule entirely and instead inject an SVG `::before` via a `background-image: url("data:image/svg+xml,...")` approach on `.cert-chip.detected`.

**Recommended:** Option A. Change the line to:
```css
.cert-chip.detected::before { content: '\2713\00a0'; }
```
(`\00a0` is a non-breaking space, replacing the space that was after the literal character.) This is a one-character edit that eliminates emoji rendering risk without restructuring the CSS.

---

### Occurrence 2 — Line 5344 · U+2713 ✓ · JS template literal (inside `_pdfBadgeHtml`)

**Current:**
```js
return `<svg class="pdf-badge-check" viewBox="0 0 16 16" …><polyline points="2 8 6 12 14 4"/></svg>
        <span>${customText || 'Text extracted ✓'}</span>`;
```

**Context:** The 'done' branch of `_pdfBadgeHtml()`. The function already builds an SVG checkmark element (`pdf-badge-check`). The ✓ inside the fallback string `'Text extracted ✓'` is therefore redundant — the checkmark is already rendered by the SVG to the left of the `<span>`.

**Fix:** Remove ✓ from the fallback string only:
```
'Text extracted ✓'  →  'Text extracted'
```
The SVG element provides the visual check; the character in the text is noise.

---

### Occurrence 3 — Line 5347 · U+26A0 ⚠ · JS template literal (inside `_pdfBadgeHtml`)

**Current:**
```js
return `<span>⚠</span><span>${customText || 'Partial extraction'}</span>`;
```

**Context:** The 'warn' branch of `_pdfBadgeHtml()`. The `⚠` sits in a `<span>` that is injected into an element via `innerHTML`. Because it goes through `innerHTML`, an SVG can be used directly.

**Replacement SVG** (warning triangle, matches `_piFraudSvg` already in the file):
```
<svg width="12" height="12" viewBox="0 0 24 24" fill="none"
     stroke="currentColor" stroke-width="2"
     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94
           a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
  <line x1="12" y1="9" x2="12" y2="13"/>
  <line x1="12" y1="17" x2="12.01" y2="17"/>
</svg>
```

**Fix:** Replace `<span>⚠</span>` with the SVG above (no wrapping `<span>` needed).

---

### Occurrence 4 — Line 5350 · U+2715 ✕ · JS template literal (inside `_pdfBadgeHtml`)

**Current:**
```js
return `<span>✕</span><span>${customText || 'Extraction failed'}</span>`;
```

**Context:** The 'error' branch of `_pdfBadgeHtml()`. Same `innerHTML` context as occurrence 3 — SVG is directly usable.

**Replacement SVG** (X / close icon):
```
<svg width="12" height="12" viewBox="0 0 24 24" fill="none"
     stroke="currentColor" stroke-width="2"
     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <line x1="18" y1="6" x2="6" y2="18"/>
  <line x1="6" y1="6" x2="18" y2="18"/>
</svg>
```

**Fix:** Replace `<span>✕</span>` with the SVG above.

---

### Occurrence 5 — Line 8010 · U+267B ♻ · JS statement (`badge.textContent`)

**Current:**
```js
badge.textContent = `♻ Sustainability: ${grade}`;
```

**Context:** Sets the text of the sustainability badge element on the decision banner. Uses `textContent`, which cannot render HTML. The `grade` value comes from API response data (a string like `'A'`, `'B'`, etc.).

**Replacement SVG** (circular arrows / sustainability icon):
```
<svg width="12" height="12" viewBox="0 0 24 24" fill="none"
     stroke="currentColor" stroke-width="2"
     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <polyline points="1 4 1 10 7 10"/>
  <polyline points="23 20 23 14 17 14"/>
  <path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4-4.64 4.36A9 9 0 0 1 3.51 15"/>
</svg>
```

**Fix:** Switch from `textContent` to `innerHTML`. Because `grade` is server-supplied, escape it before injecting:

- Change `badge.textContent = ...` to `badge.innerHTML = SVG_STRING + ' Sustainability: ' + escHtml(grade)`

The `escHtml` function is already defined globally in the first script block (line 12018). The SVG markup is hardcoded, not user-supplied, so it is safe to inject directly.

---

### Occurrence 6 — Line 8137 · U+2713 ✓ · JS statement (`labelEl.textContent`)

**Current:**
```js
labelEl.textContent = '✓ Copied!';
```

**Context:** Momentary feedback when the user copies a share link. `labelEl` is the text label of the "Share Result" button. After 2 seconds it reverts to `'Share Result'`. Uses `textContent`.

**Fix:** Two options:

- Option A (simplest): Remove the character, keep plain text. `labelEl.textContent = 'Copied!'` — the button already adds the `'copied'` CSS class which can provide visual feedback via a color change or other style rule.
- Option B (thorough): Switch to `innerHTML` and inject the checkmark SVG. Since the string is fully hardcoded with no user data, it is safe: `labelEl.innerHTML = SVG_CHECKMARK + ' Copied!'`

**Recommended:** Option A. The `'copied'` class already exists on the button at this point. The text change alone is clear feedback; the checkmark is decorative noise.

---

### Occurrence 7 — Line 8522 · U+2713 ✓ · JS string literal (assigned to `badgeText`)

**Current:**
```js
const badgeText = state === 'warn'
  ? 'Partial extraction — review text'
  : 'Text extracted ✓';
```

**Context:** `badgeText` is passed to `_pdfSetScanDone()` which passes it to `_pdfBadgeHtml()` as `customText`. Inside `_pdfBadgeHtml`, `customText` is injected into a `<span>` inside a template literal rendered via `innerHTML`. The `_pdfBadgeHtml` function also prepends an SVG checkmark for the 'done' state — so the ✓ in `badgeText` is again redundant (same issue as occurrence 2).

**Fix:** Remove ✓ from the string:
```
'Text extracted ✓'  →  'Text extracted'
```

---

### Occurrence 8 — Line 9407 · U+2715 ✕ · JS template literal (toast dismiss button)

**Current:**
```js
`<button class="dash-toast-close" aria-label="Dismiss">✕</button>`
```

**Context:** The dismiss (×) button injected into dashboard toast notifications. Built via a JS template literal and injected via `innerHTML`. SVG is directly usable.

**Replacement SVG:** Same X/close icon as occurrence 4:
```
<svg width="10" height="10" viewBox="0 0 24 24" fill="none"
     stroke="currentColor" stroke-width="2.5"
     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <line x1="18" y1="6" x2="6" y2="18"/>
  <line x1="6" y1="6" x2="18" y2="18"/>
</svg>
```

Use `width="10" height="10"` and `stroke-width="2.5"` here — the toast close button is smaller than the badge icons and slightly bolder looks better at that size.

**Fix:** Replace the ✕ character with the SVG above inside the template literal.

---

### Occurrence 9 — Line 11718 · U+2713 ✓ · JS template literal (module save indicator)

**Current:**
```js
`<span class="save-indicator" id="save-ind-${mod.module_id}">Saved ✓</span>`
```

**Context:** The momentary "Saved ✓" indicator that appears after a module toggle is saved. Built via JS template literal and injected via `innerHTML`. SVG is directly usable.

**Replacement SVG** (same checkmark used in `_piCheckSvg` at line 8639):
```
<svg width="11" height="11" viewBox="0 0 24 24" fill="none"
     stroke="currentColor" stroke-width="2.5"
     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
     style="vertical-align:-1px;margin-right:.2em">
  <polyline points="20 6 9 17 4 12"/>
</svg>
```

**Fix:** Replace `Saved ✓` with `SVG_CHECKMARK + 'Saved'` in the template literal.

---

### Summary table

| # | Line | Char | Context | Fix type |
|---|------|------|---------|----------|
| 1 | 3235 | ✓  | CSS `content:` | CSS hex escape `\2713` instead of literal char |
| 2 | 5344 | ✓  | JS template literal, already has SVG | Remove char from string |
| 3 | 5347 | ⚠  | JS template literal → `innerHTML` | Replace `<span>⚠</span>` with warning SVG |
| 4 | 5350 | ✕  | JS template literal → `innerHTML` | Replace `<span>✕</span>` with X SVG |
| 5 | 8010 | ♻  | JS `textContent` assignment | Switch to `innerHTML`, replace with recycling SVG |
| 6 | 8137 | ✓  | JS `textContent` assignment | Remove char, plain `'Copied!'` |
| 7 | 8522 | ✓  | JS string → `badgeText` → `_pdfBadgeHtml` | Remove char from string |
| 8 | 9407 | ✕  | JS template literal → `innerHTML` | Replace ✕ with X SVG |
| 9 | 11718| ✓  | JS template literal → `innerHTML` | Replace char with checkmark SVG |

---

## SECTION 3 — ROOT CAUSE OF TOGGLE NOT WORKING

The four candidate root causes from the brief:

### A — initOceanScene() runs after setBgMode(), overriding the saved preference

**NOT the problem.**

`initOceanScene()` has a `_oceanInit` guard at its very first line:
```js
if (_oceanInit) return;
_oceanInit = true;
```
It runs exactly once — synchronously during the IIFE's execution at the bottom of `<body>`. By the time any user clicks a toggle button, `initOceanScene` has already returned and `_oceanInit` is `true`. A subsequent click cannot trigger re-initialization. No ordering conflict exists. This option does not apply.

### B — The ship spawner interval is a local variable and cannot be stopped

**NOT the problem.**

`startShipSpawner()` assigns the `setInterval` handle to `window._shipSpawnerInterval` — a property on the global `window` object, not a local variable. `applyBgMode()` reads and clears it via the same `window._shipSpawnerInterval` reference. All three sites (`startShipSpawner`, the 'minimal' branch of `applyBgMode`, the 'plain' branch of `applyBgMode`) are consistent. Stopping and restarting the spawner works correctly in the logic. This option does not apply.

### C — applyBgMode references wrong element IDs

**YES. This is one of the two real bugs.**

The CSS rule at line 3408:
```css
body.bg-plain #ocean-bg { display: none; }
```
targets `#ocean-bg`. That ID does not exist anywhere in demo.html. The actual ocean scene element at line 3789 is:
```html
<div id="ocean-scene" aria-hidden="true">
```
The selector `#ocean-bg` matches nothing. `#ocean-scene` is never hidden. This bug is secondary — currently masked by bug D — but it is a real, independent defect. If D were fixed and C were not, the toggle would call the function correctly, the `bg-plain` class would be added to `body`, SMIL animations would pause, ships would stop, but the ocean background would remain fully visible.

### D — The button onclick calls the function before it is defined

**PARTIALLY CORRECT — the real issue is scope, not timing.**

`setBgMode` is defined inside `(function () { 'use strict'; ... })()` — the IIFE wrapping the entire Ocean Scene JS engine (second `<script>` block, lines 12032–12684). It is never assigned to `window.setBgMode`. It is never exported from the IIFE in any way.

HTML `onclick` attributes execute in global scope. When the browser evaluates `onclick="setBgMode('plain')"`, it looks for `setBgMode` on: the element itself → the document → `window`. The function is in the IIFE's closed-over scope, invisible to all three. The result is `ReferenceError: setBgMode is not defined`.

The phrasing "before it is defined" is slightly imprecise — by click time, the IIFE has already executed and `setBgMode` is defined within the closure. The problem is not timing but **scope**: the function exists but is unreachable from the onclick's global execution context.

**This is the primary, fatal bug.** Every button click — Full, Minimal, Plain — throws a `ReferenceError` and does nothing.

---

## SECTION 4 — THE FIX FOR EACH ROOT CAUSE

### Fix for C — Wrong element ID in CSS

**Location:** Line 3408, inside the `<style>` block.

**Current:**
```css
body.bg-plain #ocean-bg { display: none; }
```

**Fixed:**
```css
body.bg-plain #ocean-scene { display: none; }
```

One word changes: `ocean-bg` → `ocean-scene`. No other changes to this rule. The adjacent rule at line 3409 (`body.bg-plain .app-layout { background: var(--bg); }`) is correct and does not need to change.

No changes are needed to the `body.bg-minimal` rules — `body.bg-minimal #ships-layer { display: none; }` targets `#ships-layer`, which exists, and the 'minimal' mode is designed to keep the sky/water visible (only ships disappear), so there is no equivalent problem there.

---

### Fix for D — setBgMode not accessible from global scope

**Location:** Inside the second `<script>` block's IIFE. Add one line at the end of the IIFE body, immediately before the closing `})();`.

**What to add:**
```js
window.setBgMode = setBgMode;
```

This exposes `setBgMode` on the global `window` object. When the onclick fires and looks for `setBgMode`, it finds `window.setBgMode`, which is the same function, still executing in the closure's scope with access to all the ocean-scene internals (`spawnShip`, `applyBgMode`, etc.).

**Why only `setBgMode` and not `applyBgMode`:**
`applyBgMode` is an internal implementation detail. Only `setBgMode` is called from HTML attributes. `applyBgMode` stays private in the IIFE — it is called internally by `setBgMode` and by `initOceanScene`. Exposing only `setBgMode` maintains encapsulation.

**Alternative rejected:** Moving `setBgMode` and `applyBgMode` out of the IIFE into the first script block (global scope) would also work but would require moving their dependency chain (all the ocean-scene functions they call), fragmenting the Ocean Scene engine across two script blocks. The single-line `window.setBgMode = setBgMode` is the minimal correct fix.

---

## SECTION 5 — THE CORRECT PAGE LOAD ORDER

The desired order is:

```
1. All functions are defined
2. DOMContentLoaded fires
3. Auth and normal init run
4. initOceanScene() runs — starts the scene
5. Saved bg mode is read from localStorage
6. applyBgMode(savedMode) runs — overrides scene if needed
7. Button states are synced
```

**Current actual order** (from the read report):

1. First `<script>` block (line 4899) executes — all global app functions defined (`authHeaders`, auth state, UI panel functions, etc.)
2. HTML body continues parsing through all UI elements, including the toggle buttons
3. Second `<script>` block (line 12031) executes — IIFE runs, defines all ocean-scene functions
4. IIFE guard at lines 12677–12682: `document.readyState` is `'interactive'` (DOM fully parsed, script is at bottom of `<body>`) → `initOceanScene()` is called immediately and synchronously
5. Inside `initOceanScene()`: palette is applied, clouds positioned, then `localStorage.getItem('portguard_bg_mode')` is read
6. If a saved mode is found: `applyBgMode(savedMode)` is called
7. Inside `applyBgMode`: toggle button active states are updated
8. `startShipSpawner()` is called only if body does not already have `bg-minimal` or `bg-plain` class

**Assessment:** Steps 5, 6, and 7 of the desired order already happen correctly inside `initOceanScene()`, in the right sequence, with the ship spawner guarded by the bg-mode check. The page load order **does not need to change**.

The only structural note: there is no explicit "auth init" step on page load. The first script block defines auth functions but does not call any on load. Auth is triggered by user interaction (login form submission). This is intentional and correct — the ocean scene can and should start independently of auth state.

**After the two fixes (C and D) are applied, the page load order remains unchanged.** The toggle buttons become functional because `setBgMode` is now on `window`. The `applyBgMode('plain')` call now hides `#ocean-scene` correctly because the CSS rule targets the right ID.

The existing `_oceanInit` guard and the localStorage-restore sequence inside `initOceanScene` correctly handle the scenario where the user had previously saved 'plain' mode: on the next page load, `initOceanScene` reads the saved mode, calls `applyBgMode('plain')`, and then the subsequent `startShipSpawner()` call is skipped because `bg-plain` is already on the body. The page initializes directly into plain mode. No reordering is required.

---

## SECTION 6 — STEP BY STEP BUILD ORDER

**Phase A — Toggle fix (two edits, zero risk)**

1. In the CSS `<style>` block at line 3408: change `#ocean-bg` to `#ocean-scene` in the `body.bg-plain` rule.
2. At the end of the IIFE body in the second `<script>` block, immediately before `})();` (currently line 12684): add `window.setBgMode = setBgMode;`
3. Reload the page in a browser. Open the settings drawer. Click "Plain" — confirm the ocean scene disappears and the button highlights. Click "Full" — confirm the ocean scene returns and ships resume. Click "Minimal" — confirm ships disappear but the sky/water remain. Click "Full" again — confirm ships resume.
4. Reload the page while "Plain" is active — confirm the page initializes in plain mode (localStorage restore works).
5. Verify no `ReferenceError: setBgMode is not defined` appears in the browser console on any button click.

**Phase B — Emoji cleanup (nine edits, each isolated)**

6. Line 3235, CSS: Change the ✓ literal to its CSS hex escape `'\2713\00a0'`.
7. Line 5344, JS: Change `'Text extracted ✓'` to `'Text extracted'` in the `_pdfBadgeHtml` 'done' branch fallback string.
8. Line 5347, JS: Replace `<span>⚠</span>` with the 12×12 warning-triangle SVG (see Section 2, Occurrence 3).
9. Line 5350, JS: Replace `<span>✕</span>` with the 12×12 X/close SVG (see Section 2, Occurrence 4).
10. Line 8010, JS: Change `badge.textContent = \`♻ Sustainability: ${grade}\`` to `badge.innerHTML = SVG_RECYCLING + ' Sustainability: ' + escHtml(grade)` where `SVG_RECYCLING` is the 12×12 recycling SVG (see Section 2, Occurrence 5). The SVG markup is a local `const` defined on the line before the assignment.
11. Line 8137, JS: Change `labelEl.textContent = '✓ Copied!'` to `labelEl.textContent = 'Copied!'` — remove character only.
12. Line 8522, JS: Change `'Text extracted ✓'` to `'Text extracted'` in the `badgeText` assignment.
13. Line 9407, JS: Replace `✕` inside the toast dismiss button template literal with the 10×10 X/close SVG (see Section 2, Occurrence 8).
14. Line 11718, JS: Replace `Saved ✓` inside the module save-indicator template literal with the 11×11 checkmark SVG + `'Saved'` (see Section 2, Occurrence 9).

**Phase C — Verification**

15. Run the Python codepoint scan to confirm zero occurrences of U+2713, U+2715, U+26A0, U+267B remain:
    ```
    python3 -c "
    TARGETS = {0x2713, 0x2715, 0x26A0, 0x267B, 0x1F9E0}
    with open('demo.html', encoding='utf-8') as f:
        lines = f.readlines()
    found = [(i+1, c) for i, line in enumerate(lines) for c in line if ord(c) in TARGETS]
    print('Remaining:', found if found else 'none')
    "
    ```
16. In the browser: verify PDF badge states (done/warn/error) render correctly with SVG icons — upload a PDF and confirm the badge shows the checkmark SVG, not a ✓ character.
17. In the browser: submit a module toggle — confirm "Saved" indicator appears with the checkmark SVG.
18. In the browser: trigger a toast notification — confirm the dismiss button shows the X SVG.
19. In the browser: run a shipment analysis with sustainability data — confirm the sustainability badge reads correctly with the recycling SVG.
20. In the browser: confirm cert chips still show checkmarks (the CSS hex escape approach at line 3235).

**Phase D — Commit**

21. Commit with message: `fix(ui): wire bg toggle to global scope, fix ocean-scene id, replace emoji with SVG`
22. Push to master.
