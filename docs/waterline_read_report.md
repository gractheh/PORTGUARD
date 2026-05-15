# Waterline Read Report
**Date:** 2026-05-15
**Scope:** Full read of demo.html, api/app.py, portguard/, docs/ — ocean scene items only

---

## 1. Exact CSS values for the ocean scene containers

### `#ocean-scene` (lines 235–238)
```css
#ocean-scene {
  position: fixed; inset: 0; pointer-events: none; z-index: 0;
  overflow: hidden;
}
```
Additional rule (line 243): `@media (prefers-reduced-motion: reduce) { #ocean-scene { display: none; } }`

### `#horizon-layer` (lines 3352–3358)
```css
#horizon-layer {
  position: absolute;
  bottom: 0; left: 0; right: 0;
  height: 30vh;
  pointer-events: none;
  z-index: 3;
}
```
Additional rule (line 3415): `body.bg-minimal #horizon-layer { opacity: 0.5; }`

### `#dock-svg` (lines 3359–3362)
```css
#dock-svg {
  width: 100%; height: 100%;
  filter: brightness(var(--dock-brightness, 0.4));
}
```
CSS variable default (line 155): `--dock-brightness: 0.4;`

### `#ocean-water` (lines 3364–3371)
```css
#ocean-water {
  position: absolute;
  bottom: 0; left: 0; right: 0;
  height: 26vh;
  pointer-events: none;
  z-index: 4;
  overflow: hidden;
}
```
Additional rule (line 3372): `#ocean-water svg { width: 200%; height: 100%; }`

### `#ships-layer` (lines 3374–3382)
```css
#ships-layer {
  position: absolute;
  bottom: 22%;
  left: 0; right: 0;
  height: 80px;
  overflow: visible;
  pointer-events: none;
  z-index: 5;
}
```

---

## 2. Exact HTML structure of the ocean scene

All children of `<div id="ocean-scene">` (lines 3799–4089), in order:

```
<div id="ocean-scene" aria-hidden="true">

  <!-- Layer 1: Sky -->
  <div id="sky-layer">
    <div id="celestial-body"></div>
  </div>

  <!-- Layer 2: Stars canvas -->
  <canvas id="stars-canvas"></canvas>

  <!-- Layer 3: Clouds — 5 independent puffs -->
  <div id="cloud-layer">
    <div class="cloud"><div class="cloud-puff" id="cloud-1" data-speed="90"  data-dir="left"  data-y="8"  data-opacity="0.6"></div></div>
    <div class="cloud"><div class="cloud-puff" id="cloud-2" data-speed="130" data-dir="right" data-y="14" data-opacity="0.45"></div></div>
    <div class="cloud"><div class="cloud-puff" id="cloud-3" data-speed="110" data-dir="left"  data-y="5"  data-opacity="0.55"></div></div>
    <div class="cloud"><div class="cloud-puff" id="cloud-4" data-speed="150" data-dir="right" data-y="20" data-opacity="0.35"></div></div>
    <div class="cloud"><div class="cloud-puff" id="cloud-5" data-speed="80"  data-dir="left"  data-y="10" data-opacity="0.50"></div></div>
  </div>

  <!-- Layer 4: Horizon — dock port SVG -->
  <div id="horizon-layer">
    <svg id="dock-svg" viewBox="0 0 1440 320" preserveAspectRatio="xMidYMax meet"
         width="100%" height="100%">
      …(see item 8)…
    </svg>
  </div>

  <!-- Layer 5: Ocean water — SMIL-animated waves -->
  <div id="ocean-water">
    <svg id="ocean-water-svg" viewBox="0 0 1440 200" preserveAspectRatio="xMidYMax meet"
         width="100%" height="100%">
      …waves, foam sparkles…
    </svg>
  </div>

  <!-- Layer 6: Ships — dynamically spawned by JS -->
  <div id="ships-layer"></div>

</div>
```

Layer z-index stack summary:
- `#sky-layer`: no z-index set (stacks behind all)
- `#stars-canvas`: z-index 1
- `#cloud-layer`: z-index 2
- `#horizon-layer` / `#dock-svg`: z-index 3
- `#ocean-water`: z-index 4
- `#ships-layer`: z-index 5

---

## 3. Exact SVG viewBox of each ship builder function

### `buildContainerShip(scale)` — line 12514
```
viewBox="0 0 220 70"
```
Hull path: `M8,32 L0,44 L4,56 L216,56 L220,44 L212,32Z`
Hull bottom y-coordinate: **56**

### `buildTugboat(scale)` — line 12550
```
viewBox="0 0 100 56"
```
Hull path: `M6,26 L0,38 L3,46 L97,46 L100,38 L94,26Z`
Hull bottom y-coordinate: **46**

### `buildSailboat(scale)` — line 12572
```
viewBox="0 0 66 95"
```
Hull path: `M5,64 L0,74 L3,80 L63,80 L66,74 L61,64Z`
Hull bottom y-coordinate: **80**

---

## 4. Exact y position of the wake ellipse in each ship SVG

### ContainerShip (line 12545)
```svg
<ellipse cx="110" cy="58" rx="80" ry="4" fill="#4DCFDF" opacity="0.10"/>
```
Wake ellipse center: **cy=58** (2px below hull bottom at y=56)

### Tugboat (line 12567)
```svg
<ellipse cx="50" cy="48" rx="38" ry="3" fill="#4DCFDF" opacity="0.12"/>
```
Wake ellipse center: **cy=48** (2px below hull bottom at y=46)

### Sailboat (line 12583)
```svg
<ellipse cx="33" cy="82" rx="24" ry="3" fill="#4DCFDF" opacity="0.12"/>
```
Wake ellipse center: **cy=82** (2px below hull bottom at y=80)

In all three ships the wake ellipse is placed exactly 2px below the hull bottom edge.

---

## 5. Value set for `ship.style.bottom` in `spawnShip()`

Line 12619:
```js
div.style.bottom = '0px';
```

The value is hardcoded to `'0px'`. This means every spawned ship `<div>` is positioned at the bottom edge of `#ships-layer` regardless of ship type.

The `SHIP_TYPES` array (lines 12590–12595) defines `baseBottom` values per ship type (18, 22, 18, 19 vh), and the `spawnShip()` function computes a `bottom` variable with `±2 vh` jitter (line 12615), but **this `bottom` value is never applied to `div.style.bottom`** — it is computed and discarded. The hardcoded `'0px'` is used instead.

---

## 6. The `shipBob` keyframe — does it go both up AND down?

Lines 3573–3577:
```css
@keyframes shipBob {
  0%, 100% { transform: translateY(0); }
  50%       { transform: translateY(-4px); }
}
```

**No. Ships only move upward.** The animation moves from `translateY(0)` → `translateY(-4px)` → `translateY(0)`. The maximum displacement is −4px (up). There is no downward movement (no positive translateY value anywhere in the keyframe).

---

## 7. What is inside `#horizon-layer`?

`#horizon-layer` contains a single child: `<svg id="dock-svg">` (see item 8 for full contents).

The SVG uses `viewBox="0 0 1440 320"` with `preserveAspectRatio="xMidYMax meet"`, `width="100%"`, `height="100%"`. The `xMidYMax meet` value anchors the bottom of the SVG viewBox to the bottom of `#horizon-layer`, so the wharf strip at y=295 always aligns to the container bottom.

---

## 8. What is inside `#dock-svg`?

Full contents of `<svg id="dock-svg" viewBox="0 0 1440 320">` (lines 3820–3999):

### City skyline silhouette (far background buildings)
- **Apartment block** (x=40, y=120, 160×200) — 10 warm amber `#FFDE80` windows at 3 rows
- **Domed building** (x=220, y=160, 30×160) with `<ellipse cx="235" cy="160">` dome top
- **Port warehouse** (x=280, y=140, 220×180) — 5 blue `#A0C8FF` windows, double loading door
- **Control tower / office building** (x=540, y=60, 80×260) — 4×3 mixed amber+blue windows, lit amber control room at top
- **Large port building** (x=660, y=130, 200×190) — 2 rows of 3–5 mixed amber+blue windows
- **Customs/port authority building** (x=900, y=160, 300×160) — 1 row of 5 mixed amber+blue windows
- **Right skyline spire** (x=1250, y=50, 16×270)
- **Right skyline extension** (x=1300, y=140, 80×180) + tall building (x=1390, y=100, 50×220) with windows

All silhouette rects use `fill="var(--dock-silhouette,#0A0A14)"`.

### Three gantry cranes (steel blue `#4A8AB0`)
1. **Crane 1** (center ~x=350): two vertical legs, horizontal boom, vertical mast, outrigger arm reaching left to x=180; amber cab at x=466,y=65; trolley/spreader; hoist cable; red danger light `cx=350,cy=75,r=4`
2. **Crane 2** (foreground ~x=780): two vertical legs, horizontal boom, vertical mast, outrigger arms both directions (x=580 to x=949); amber cab at x=936,y=42; spreader bar; operator cab on boom; hoist cable; red danger light `cx=783,cy=52,r=5`
3. **Crane 3** (right background ~x=1177): two vertical legs, horizontal boom, vertical mast, outrigger arms; amber cab at x=1272,y=85; hoist cable; spreader; red danger light `cx=1177,cy=93,r=3.5`

### Container stacks (3 rows, lines 3946–3967)
- **Row 1** (y=262): 8 containers — red/amber/teal/red/amber/gray/red/teal at 50% opacity
- **Row 2** (y=242): 6 containers — amber/red/teal/gray/red/amber at 45% opacity
- **Row 3** (y=222): 4 containers — red/teal/amber/gray at 38% opacity

### Wharf and bollards (lines 3969–3984)
- Full-width wharf strip: `<rect x="0" y="295" width="1440" height="25">` in dock-silhouette color
- 14 bollards at y=284, spaced ~80px, each 20×12 with rx=2

### Navigation beacon (lines 3986–3988)
- Vertical pole: `<line x1="80" y1="295" x2="80" y2="240">` in `#3A5A7A`
- Orange light: `<circle cx="80" cy="236" r="6" fill="#FF8000" opacity="0.85">`

---

## 9. Why ships appear to float above the water

**Pixel-level analysis at 800px viewport height:**

| Layer | CSS | Pixel position (800px viewport) |
|---|---|---|
| `#ocean-water` | `bottom:0; height:26vh` | top edge at 800 − 208 = **592px from top** (208px from bottom) |
| `#ships-layer` | `bottom:22%` of `#ocean-scene` (= viewport) | bottom edge at 800 − 176 = **624px from top** (176px from bottom) |
| Ship div | `bottom:0px` within `#ships-layer` | bottom edge at **624px from top** |

The waterline (top of `#ocean-water`) is at 592px from top.
Ships sit with their bottom at 624px from top — **geometrically 32px below the waterline**, placing them inside the water area.

Ships visually appear above water because **`z-index: 5` on `#ships-layer` is higher than `z-index: 4` on `#ocean-water`** — the ocean SVG is painted first, then ships are painted on top of it regardless of their geometric position.

Additionally, the hull bottom of each ship SVG occupies the lower portion of its bounding box:
- ContainerShip: hull bottom at y=56 out of 70 total height = 80% down the SVG
- Tugboat: hull bottom at y=46 out of 56 = 82% down
- Sailboat: hull bottom at y=80 out of 95 = 84% down

So ships render with roughly 80–84% of their SVG height below their containing div's top edge, increasing the apparent submersion. The wake ellipse (2px below hull bottom) ends up painted on top of the ocean layer, producing a visual waterline effect.

---

## 10. Every CSS rule and JS reference mentioning `horizon-layer`, `dock-svg`, or `dock-brightness`

### CSS rules

```
Line 155:   --dock-brightness:  0.4;                (CSS variable default in :root)
Line 3352:  #horizon-layer { ... }                   (layout rule — see item 1)
Line 3359:  #dock-svg { filter: brightness(var(--dock-brightness, 0.4)); }
Line 3415:  body.bg-minimal #horizon-layer { opacity: 0.5; }
```

### HTML
```
Line 3819:  <div id="horizon-layer">
Line 3820:  <svg id="dock-svg" ...>
```

### JavaScript
```
Line 12407: root.setProperty('--dock-brightness', String(p.dockBright));   (in applyPalette)
```

No other JS references to `horizon-layer`, `dock-svg`, or `dock-brightness` exist anywhere in demo.html. The dock SVG has no JS manipulation — it is pure static SVG, with only its brightness controlled dynamically via the CSS variable.

---

## 11. Does `applyPalette` reference `dock-brightness`? At what line?

Yes. In `applyPalette(p)` (function starts at line 12387):

```js
root.setProperty('--dock-brightness', String(p.dockBright));  // line 12407
```

`p.dockBright` is a numeric value sourced from the active `PALETTES` entry. The dock SVG's `filter: brightness(...)` then reads this variable at paint time, dimming or brightening the entire port silhouette based on the current time-of-day palette.
