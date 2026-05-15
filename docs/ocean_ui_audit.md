# Ocean Theme UI Audit
**Date:** 2026-05-14  
**Files:** `demo.html`  
**Issues:** 4 confirmed bugs — bulk empty state, ocean/ship alignment, missing bg toggle, emoji usage

---

## 1. Z-Index Stack (current)

| Layer | Element | CSS z-index | Height |
|-------|---------|-------------|--------|
| Stars | `#stars-layer` | 1 | full bg |
| Clouds | `.cloud-puff` | 2 | top 30% |
| Horizon/dock | `#horizon-layer` | 3 | 30vh from bottom |
| Ocean water | `#ocean-water` | 4 | 22vh from bottom |
| Ships | `#ships-layer` | 5 | 120px, bottom:16% |

Ships at `bottom:16%` float ABOVE the ocean water layer. Ocean water is `22vh` tall. If viewport is 800px, ocean = 176px (22%), ships layer starts at 128px (16%) — ships are 4% ABOVE the waterline. Bug confirmed.

---

## 2. Bulk Empty State (Bug 1)

### HTML (lines 4713–4730)
```html
<div class="bulk-empty-art">
  <div style="position:relative;width:80px;height:60px;margin:0 auto">
    <svg width="80" height="60" ...>   <!-- waves at y=45,50 -->
    <div style="position:absolute;bottom:10px;left:50%;transform:translateX(-50%);animation:shipBob 3s ...">
      <svg width="54" height="22" ...>  <!-- ship hull -->
```

**Root cause:** `position:absolute` inside a flex container removes the child from normal flow. Flexbox centers the 80×60 wrapper, but the ship+waves occupy only the bottom half of that wrapper — so the ship appears below-center of the 88px circle.

### CSS (lines 2793–2798)
```css
.bulk-empty-art {
  width: 88px; height: 88px; border-radius: 50%;
  background: rgba(27,154,170,.08); border: 1px solid rgba(27,154,170,.15);
  display: flex; align-items: center; justify-content: center;
}
```

---

## 3. Ocean Scene Bugs (Bug 2)

### #ocean-water (line 3297)
- `height: 22vh` — too short; ships need to sit AT the waterline
- `overflow: hidden` — correct for water but may clip waves

### #ships-layer (line 3307)
- `bottom: 16%` — wrong; ships float above water (see z-index table)
- `height: 120px` — tall container
- `overflow: hidden` — prevents ships from entering from off-screen

### spawnShip() (line 12287)
```javascript
div.style.bottom = bottom + 'vh';  // baseBottom 18-22vh
```
Ship element bottom is set in vh from the ships-layer bottom, but ships-layer itself is at bottom:16%. This double-offsets ships.

### startShipSpawner() (line 12323)
```javascript
setInterval(spawnShip, 12000);  // no reference stored
```
Cannot be stopped for bg-mode toggle.

### Dock SVG (lines 3732–3851)
Buildings, cranes, bollards, wharf all use `var(--dock-silhouette,#0A0A14)` — monochrome black silhouette. Container stacks at lines 3803–3822 already use colored rgba fills.

### Ocean SVG (lines 3856–3905)
Uses CSS `style="animation: wave-drift-left 22s linear infinite"` — CSS-dependent. Would benefit from SMIL `<animate>` for portability.

---

## 4. Background Toggle (Bug 3)

### Settings drawer (lines 4067–4107)
`<div class="drawer-body" id="drawer-body">` at line 4089 — this is where bg-mode-toggle HTML will be injected/added.

No bg-mode toggle exists anywhere in current code. No `portguard_bg_mode` localStorage key. No `setBgMode()`/`applyBgMode()` function.

`window._shipSpawnerInterval` does not exist — the setInterval return value is not stored.

---

## 5. Emoji Inventory (Bug 4)

### CSS `content:` emoji
| Line | Rule | Emoji |
|------|------|-------|
| 2539 | `.bulk-progress-fill::after` | `'🚢'` |
| 3167 | `.cert-chip.detected::before` | `'✓ '` ← text, ok |
| 3168 | `.cert-chip.missing::before`  | `'⚠ '` |

### Static HTML emoji
| Line | Context |
|------|---------|
| 4074 | Drawer title: `⚓ Screening Modules` |
| 4095 | Preset btn: `📦 Packaging & Paper` |
| 4096 | Preset btn: `👕 Apparel & Footwear` |
| 4097 | Preset btn: `⚡ Electronics & Tech` |
| 4098 | Preset btn: `🌿 Food & Agriculture` |
| 4099 | Preset btn: `⛏ Minerals & Metals` |
| 4642 | Drop zone sub: `Drag aboard! ⚓` |
| 4671 | Drop zone sub: `Set sail! 🌊` |
| 4732 | Empty state title: `⚓ Screen up to 50 shipments at once` |
| 4742 | Progress title: `🚢 Processing Batch…` |

### JS emoji (dynamic HTML)
| Line | Context |
|------|---------|
| 8622 | Pattern intel title: `🧠 Pattern Intelligence` |
| 8625 | Fraud flag: `🚨 ${...}` |
| 8636 | Boost item: `✅ ${...}` |
| 8641 | Shipper history: `📋 ${...}` |
| 10884 | Bulk error: `'⚠ ' + msg` |
| 11362 | Shared banner icon: `📋` |
| 12393 | Analyze btn: `span.textContent = '⚓'` |
| 12401 | Quick load map: `{ clean: '🚢', suspicious: '🚨', incomplete: '📋' }` |
| 12418 | Nav tab map: `⚓` (analyze) |
| 12419 | Nav tab map: `🧭` (dashboard) |
| 12420 | Nav tab map: `📦` (bulk) |

---

## 6. Fix Plan Summary

### Bug 1
- Add `.bulk-empty-ship-circle` CSS: `animation: shipBob 2.5s ease-in-out infinite`
- Replace inner HTML with single centered SVG (68×46), ship+waves in one viewBox
- Remove `position:absolute` positioning from inner elements

### Bug 2
- `#ocean-water`: height `22vh → 26vh`
- `#ships-layer`: `bottom: 16% → 22%`, `height: 120px → 80px`, `overflow: hidden → visible`
- `spawnShip()`: `div.style.bottom = bottom + 'vh'` → `div.style.bottom = '0px'`
- `startShipSpawner()`: store interval → `window._shipSpawnerInterval = setInterval(...)`
- Dock SVG: add color to cranes (steel blue), buildings (keep dark), add lit windows
- Ocean SVG: convert CSS animations to SMIL `<animateTransform>` / `<animate>`

### Bug 3
- Add CSS: `.bg-mode-toggle`, `.bg-mode-label`, `.bg-mode-options`, `.bg-mode-btn`, `.bg-mode-btn.active`
- Add HTML before `#drawer-layers` in `#drawer-body`
- Add JS: `setBgMode(mode)`, `applyBgMode(mode)` — controls body class, stops/starts ship spawner
- Read `portguard_bg_mode` from localStorage on `initOceanScene()`

### Bug 4
- CSS: `.bulk-progress-fill::after` → switch to SVG overlay approach or remove emoji
- CSS: `.cert-chip.missing::before` → use `'! '` text or `'△ '` symbol
- HTML: Replace emoji strings with inline SVG or plain text labels
- JS: Replace `injectNavTabIcons`, `injectAnalyzeBtnEmoji`, `injectQuickLoadEmojis` with SVG icons
- JS pattern intel: replace emoji strings with inline SVG tags
- JS bulk error/banner: replace emoji strings with plain labels
