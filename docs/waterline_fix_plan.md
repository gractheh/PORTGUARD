# Waterline Fix Plan
**Date:** 2026-05-15
**Scope:** Remove dock/horizon layer; align ships flush to waterline; fix ship SVGs

---

## SECTION 1 — WHAT TO DELETE

### CSS variables in `:root` (lines 154–155)
Both of these are exclusively consumed by the dock SVG and its JS palette controller. Nothing else in the file reads them.

| Line | Rule | Reason for deletion |
|------|------|---------------------|
| 154 | `--dock-silhouette: #020A14;` | Only used as `fill="var(--dock-silhouette,#0A0A14)"` inside `#dock-svg` — deleted with the SVG |
| 155 | `--dock-brightness: 0.4;` | Only consumed by `#dock-svg { filter: brightness(...) }` and `applyPalette` — both deleted |

### CSS rules (four deletions)

| Lines | Selector | Action |
|-------|----------|--------|
| 3352–3358 | `#horizon-layer { position: absolute; bottom: 0; left: 0; right: 0; height: 30vh; pointer-events: none; z-index: 3; }` | Delete entire block |
| 3359–3362 | `#dock-svg { width: 100%; height: 100%; filter: brightness(var(--dock-brightness, 0.4)); }` | Delete entire block |
| 3415 | `body.bg-minimal #horizon-layer { opacity: 0.5; }` | Delete one line — the comment on 3413 (`body.bg-minimal: keep sky/water, stop ships, dim dock`) must also be updated to remove "dim dock" |

### HTML block (lines 3818–4000)
Delete the entire Layer 4 block — comment and all:
```
<!-- Layer 4: Horizon — dock port SVG with colored structures -->
<div id="horizon-layer">
  <svg id="dock-svg" …>
    …(all city skyline, cranes, container stacks, wharf, bollards, beacon)…
  </svg>
</div>
```
This is 182 lines of HTML. The deletion leaves Layer 5 (`#ocean-water`) as the immediate successor of Layer 3 (`#cloud-layer`) inside `#ocean-scene`.

### JS — `dockBright` key in all seven PALETTES entries

| Line | Palette | Value |
|------|---------|-------|
| 12247 | `night` | `dockBright: 0.25,` |
| 12267 | `dawn` | `dockBright: 0.30,` |
| 12287 | `morning` | `dockBright: 0.42,` |
| 12307 | `midday` | `dockBright: 0.60,` |
| 12327 | `afternoon` | `dockBright: 0.48,` |
| 12347 | `sunset` | `dockBright: 0.30,` |
| 12367 | `dusk` | `dockBright: 0.20,` |

Delete the `dockBright` line from each entry. Also update the PALETTES comment on line 12230: remove `, dockBright` from the list of documented palette keys.

### JS — `applyPalette` (line 12407)
Delete this single line:
```js
root.setProperty('--dock-brightness', String(p.dockBright));
```
This is the only `setProperty` call for `--dock-brightness`. After deletion, `applyPalette` makes no reference to the dock at all.

### Summary count
- 2 CSS variables deleted from `:root`
- 3 CSS rule blocks deleted
- 182 lines of HTML deleted (entire `#horizon-layer` subtree)
- 7 `dockBright` keys deleted from PALETTES
- 1 `setProperty` call deleted from `applyPalette`
- 1 PALETTES comment line updated

---

## SECTION 2 — THE ALIGNMENT MATH

### Coordinate system

`#ocean-scene` has `position: fixed; inset: 0` — it fills 100% of the viewport in both dimensions. Its height equals the viewport height (call it `VH`). Percentage values for `bottom` on children positioned absolutely within it are relative to `VH`.

Therefore `1vh ≡ 1%` when referring to `bottom` on direct or absolute-positioned children of `#ocean-scene`.

### The invariant

```
#ships-layer.bottom  must equal  #ocean-water.height
```

Proof:
- `#ocean-water` has `bottom: 0; height: 26vh` → its top edge is at `26vh` from the bottom of the viewport.
- `26vh` from the bottom is the **waterline**.
- `#ships-layer` has `bottom: X%` → its bottom edge is `X * VH / 100` from the bottom.
- Ship divs inside have `bottom: 0px` → ship div bottom edge = `#ships-layer` bottom edge.
- For a ship's bottom edge to sit ON the waterline, `#ships-layer` bottom edge must be at `26vh` from the bottom.
- Therefore `X%` must equal `26%`.

### Current values vs required values

| CSS property | Current value | Required value | Effect of change |
|---|---|---|---|
| `#ocean-water height` | `26vh` | `26vh` (no change) | Waterline stays at 26vh from bottom |
| `#ships-layer bottom` | `22%` | `26%` | Ship divs move from 22vh to 26vh from bottom — flush with waterline |
| `ship.style.bottom` (JS) | `'0px'` | `'0px'` (no change) | Ship div bottom = ships-layer bottom = waterline |

### The gap that currently exists

At a 1000px viewport:
- `#ocean-water` top = 1000 × 0.26 = **260px from bottom** — this is the waterline
- `#ships-layer` bottom = 1000 × 0.22 = **220px from bottom**
- Ship div bottom = 220px from bottom
- **Gap: 260 − 220 = 40px** — ships currently sit 40px geometrically BELOW the waterline

Ships only appear above water because `z-index: 5` on `#ships-layer` > `z-index: 4` on `#ocean-water`, causing the ocean SVG to be painted beneath ships regardless of geometry.

After the fix (`#ships-layer bottom: 26%`), geometry and z-index agree: ship div bottom = waterline, and ocean water is painted beneath.

### The hull-flush condition

For the hull bottom to be flush with the waterline, hull bottom must coincide with the ship div's bottom edge. The ship div's bottom edge is the SVG's bottom edge (SVGs fill their containing div). Therefore:

```
hull bottom y-coordinate = viewBox height
```

If hull bottom Y < viewBox height Z, there is a `(Z − Y)` px gap of empty space below the hull inside the SVG. This empty band sits below the waterline, visually producing a submerged-hull appearance. Shifting all SVG element y-coordinates down by `(Z − Y)` moves the hull bottom to `y = Z`, eliminating the gap.

### Per-ship gap calculation

| Ship | viewBox height Z | Hull bottom Y | Gap (Z − Y) | Shift required |
|---|---|---|---|---|
| ContainerShip | 70 | 56 | 14 px | +14 to all y-values |
| Tugboat | 56 | 46 | 10 px | +10 to all y-values |
| Sailboat | 95 | 80 | 15 px | +15 to all y-values |

---

## SECTION 3 — SHIP SVG FIXES

### General rule

For each ship: add the gap value to **every y-coordinate** in every SVG element (path `M`/`L`/`C`/`Z` y values, `rect y` attributes, `line y1`/`y2` attributes, `ellipse cy` attributes, `circle cy` attributes). The `x` coordinates, `rx`/`ry` radii, and `width`/`height` attributes of rects do not change. The `viewBox` does not change. The `width` and `height` HTML attributes (computed from scale) do not change.

After the shift, the wake ellipse `cy` value in each ship will be at or beyond the viewBox bottom edge. Remove the inline wake ellipse from each ship SVG — the separately appended `.ship-wake` div (lines 12631–12638) already renders a wake trail at the correct position, and the ocean wave animation provides the surrounding water surface.

---

### `buildContainerShip` — gap: +14

**Current viewBox:** `0 0 220 70`
**Hull path current:** `M8,32 L0,44 L4,56 L216,56 L220,44 L212,32Z`
**Hull bottom currently at:** y=56 (14px above viewBox bottom)

**What changes:**
Every y-value in every element increases by 14.

| Element | y-values before | y-values after |
|---|---|---|
| Hull path | 32, 44, 56 | 46, 58, 70 |
| Shadow path | 52, 56 (the `L2,52 L218,52 L216,56 L4,56Z` strip) | 66, 70 |
| Waterline stroke | y1=44, y2=44 | y1=58, y2=58 |
| Container row 2 (lower) | y=20 | y=34 |
| Container row 1 (upper) | y=8 | y=22 |
| Superstructure rect | y=16 | y=30 |
| Superstructure top strip | y=16 | y=30 |
| Porthole rects | y=21 | y=35 |
| Funnel | y=7 (h=11) | y=21 |
| Funnel stripe | y=13 (h=3) | y=27 |
| Wake ellipse | cy=58 | cy=72 — **remove this element** (beyond viewBox; `.ship-wake` div handles wake) |

**Hull bottom after:** y=70 = viewBox height ✓

---

### `buildTugboat` — gap: +10

**Current viewBox:** `0 0 100 56`
**Hull path current:** `M6,26 L0,38 L3,46 L97,46 L100,38 L94,26Z`
**Hull bottom currently at:** y=46 (10px above viewBox bottom)

**What changes:**
Every y-value increases by 10.

| Element | y-values before | y-values after |
|---|---|---|
| Hull path | 26, 38, 46 | 36, 48, 56 |
| Shadow strip | 44, 46 | 54, 56 |
| Waterline stripe | y=38, h=3 | y=48, h=3 |
| Waterline stroke | y1=38 | y1=48 |
| Wheelhouse rect | y=12 | y=22 |
| Wheelhouse top strip | y=12 | y=22 |
| Porthole rects | y=17 | y=27 |
| Funnel | y=4, h=12 | y=14, h=12 |
| Funnel stripe | y=10, h=3 | y=20, h=3 |
| Bollard post | y=22, h=14 | y=32, h=14 |
| Porthole circles | cy=39, cy=39 | cy=49, cy=49 |
| Wake ellipse | cy=48 | cy=58 — **remove this element** (at/beyond viewBox bottom; `.ship-wake` handles wake) |

**Hull bottom after:** y=56 = viewBox height ✓

---

### `buildSailboat` — gap: +15

**Current viewBox:** `0 0 66 95`
**Hull path current:** `M5,64 L0,74 L3,80 L63,80 L66,74 L61,64Z`
**Hull bottom currently at:** y=80 (15px above viewBox bottom)

**What changes:**
Every y-value increases by 15.

| Element | y-values before | y-values after |
|---|---|---|
| Hull path | 64, 74, 80 | 79, 89, 95 |
| Shadow strip | 78, 80 | 93, 95 |
| Waterline stroke | y1=74 | y1=89 |
| Mast (vertical line) | y1=8, y2=64 | y1=23, y2=79 |
| Main sail path | M33,10 L33,60 L8,60 | M33,25 L33,75 L8,75 |
| Topsail path | M33,16 L33,58 L58,60 | M33,31 L33,73 L58,75 |
| Boom stroke | y1=60, y2=60 | y1=75, y2=75 |
| Wake ellipse | cy=82 | cy=97 — **remove this element** (beyond viewBox; `.ship-wake` handles wake) |

**Hull bottom after:** y=95 = viewBox height ✓

---

## SECTION 4 — SHIPBOB FIX

**Current keyframe (lines 3573–3577):**
```css
@keyframes shipBob {
  0%, 100% { transform: translateY(0); }
  50%       { transform: translateY(-4px); }
}
```

**Analysis:**
- `translateY(0)` = ship at waterline (neutral / resting position)
- `translateY(-4px)` = ship 4px above waterline (moved up in screen space)
- No frame has a positive translateY value

**Verdict: no change required.** The keyframe already moves only upward. After the ship SVG fixes, `translateY(0)` will mean hull flush at waterline, and `translateY(-4px)` will mean hull slightly cresting above it — correct physical behavior. A positive translateY would push the hull below the waterline surface; there is none.

The plan confirms this section as a verification step only, not an edit.

---

## SECTION 5 — FINAL OCEAN SCENE STRUCTURE

After all changes, `<div id="ocean-scene">` contains exactly these six children in this order:

```
<div id="ocean-scene" aria-hidden="true">

  <!-- Layer 1: Sky — gradient + celestial body -->
  <div id="sky-layer">
    <div id="celestial-body"></div>
  </div>

  <!-- Layer 2: Stars canvas -->
  <canvas id="stars-canvas"></canvas>

  <!-- Layer 3: Clouds — 5 independent puffs -->
  <div id="cloud-layer">
    <div class="cloud"><div class="cloud-puff" id="cloud-1" …></div></div>
    <div class="cloud"><div class="cloud-puff" id="cloud-2" …></div></div>
    <div class="cloud"><div class="cloud-puff" id="cloud-3" …></div></div>
    <div class="cloud"><div class="cloud-puff" id="cloud-4" …></div></div>
    <div class="cloud"><div class="cloud-puff" id="cloud-5" …></div></div>
  </div>

  <!-- Layer 4: Ocean water — SMIL-animated waves + foam -->
  <div id="ocean-water">
    <svg id="ocean-water-svg" viewBox="0 0 1440 200" …>
      …(waves, shimmer, foam sparkles — unchanged)…
    </svg>
  </div>

  <!-- Layer 5: Ships — dynamically spawned by JS -->
  <div id="ships-layer"></div>

</div>
```

**z-index stack after changes:**
| Layer | z-index |
|---|---|
| `#sky-layer` | (none — base) |
| `#stars-canvas` | 1 |
| `#cloud-layer` | 2 |
| `#ocean-water` | 4 |
| `#ships-layer` | 5 |

z-index 3 is freed (was `#horizon-layer`). The gap is harmless — no element uses z-index 3 after deletion.

---

## SECTION 6 — STEP BY STEP BUILD ORDER

**Phase A: CSS**

1. Delete `--dock-silhouette: #020A14;` from `:root` (line 154).
2. Delete `--dock-brightness: 0.4;` from `:root` (line 155).
3. Delete the `#horizon-layer { … }` CSS block (lines 3352–3358).
4. Delete the `#dock-svg { … }` CSS block (lines 3359–3362).
5. Change `#ships-layer` CSS: `bottom: 22%` → `bottom: 26%` (line 3376).
6. Delete `body.bg-minimal #horizon-layer { opacity: 0.5; }` (line 3415). Update the adjacent comment on line 3413 to remove "dim dock" from the description.

**Phase B: HTML**

7. Delete the entire `<!-- Layer 4 … -->` comment and `<div id="horizon-layer">` block (lines 3818–4000 inclusive, 182 lines). The `<!-- Layer 5 -->` comment for `#ocean-water` becomes the first comment after `#cloud-layer`.

**Phase C: Ship SVG fixes**

8. In `buildContainerShip`: apply +14 shift to all y-coordinates throughout the SVG template string. Remove the wake `<ellipse cx="110" cy="58" …/>` element.
9. In `buildTugboat`: apply +10 shift to all y-coordinates throughout the SVG template string. Remove the wake `<ellipse cx="50" cy="48" …/>` element.
10. In `buildSailboat`: apply +15 shift to all y-coordinates throughout the SVG template string. Remove the wake `<ellipse cx="33" cy="82" …/>` element.

**Phase D: JS cleanup**

11. Delete `dockBright: 0.25,` from the `night` palette entry (line 12247).
12. Delete `dockBright: 0.30,` from the `dawn` palette entry (line 12267).
13. Delete `dockBright: 0.42,` from the `morning` palette entry (line 12287).
14. Delete `dockBright: 0.60,` from the `midday` palette entry (line 12307).
15. Delete `dockBright: 0.48,` from the `afternoon` palette entry (line 12327).
16. Delete `dockBright: 0.30,` from the `sunset` palette entry (line 12347).
17. Delete `dockBright: 0.20,` from the `dusk` palette entry (line 12367).
18. Update the PALETTES comment (line 12230): remove `, dockBright` from the documented key list.
19. Delete `root.setProperty('--dock-brightness', String(p.dockBright));` from `applyPalette` (line 12407).

**Phase E: Verification (no edits)**

20. Confirm `shipBob` keyframe (lines 3573–3577) has no positive translateY — it does not, no edit needed.
21. Confirm `ship.style.bottom = '0px'` in `spawnShip()` (line 12619) — correct as-is, no edit needed.
22. Confirm `.ship-wake` div is still appended in `spawnShip()` (lines 12631–12638) and its CSS (`bottom: -2px`) positions it correctly at the waterline — correct as-is, no edit needed.
23. Confirm `body.bg-minimal #ships-layer { display: none; }` (line 3414) — still valid, no edit needed.
24. Confirm `body.bg-plain #ocean-bg { display: none; }` (line 3418) — still valid, no edit needed.

**Dead code note (optional cleanup, not required):**
The `bottom` variable computed in `spawnShip()` (line 12615, `type.baseBottom + (Math.random() * 4 - 2)`) is calculated but never applied — `div.style.bottom` is hardcoded to `'0px'` two lines later. The `baseBottom` field in each `SHIP_TYPES` entry is therefore also dead. These may be removed in a follow-up pass but are not blocking for the waterline fix.
