# PortGuard Ocean Theme — Complete Build Plan
**Date:** 2026-04-30  
**Based on:** `docs/ocean_theme_audit.md` (2026-04-29)  
**Status:** Pre-implementation. No code has been written for ocean theme features.  
**Scope:** All changes confined to `demo.html`. Zero backend changes required.

---

## Executive Summary

The ocean theme is 15% implemented. The color palette and a minimal wave background exist. Everything else — the ocean scene, time-of-day palettes, ship spawner, cloud layer, dock silhouette, frosted glass system, button ripples, maritime icons, and bulk upload visual polish — must be built from scratch. This document is the complete specification. Nothing should be implemented that is not in this plan, and nothing in this plan should be skipped.

The final result: a living harbor scene visible behind the UI at all times, cycling through 7 time-of-day states based on the user's local clock, with ambient ships crossing the horizon, clouds drifting overhead, stars at night, and every button in the app responding to clicks with a physics-accurate ripple.

---

## Insertion Points in demo.html

Before writing any code, the following landmark lines must be confirmed at implementation time:

| Landmark | Current approx. line | What goes near it |
|---|---|---|
| End of `</style>` tag | ~2863 | All new CSS blocks appended before this |
| `<body>` open tag | ~2873 | Ocean scene HTML inserted immediately after |
| `#wave-bg` div | ~2876 | Replace entirely with new ocean scene |
| Start of `<script>` block | ~3845 | All new JS inserted near top of script block |
| `const apiUrl = () => '';` | ~3851 | New JS goes after this line |
| `document.addEventListener('DOMContentLoaded'` or `window.addEventListener('load'` | End of JS | `initOceanScene()` call goes here |

---

## PHASE 1 — CSS Architecture

### 1.1 New CSS Variables — Complete `:root` Expansion

The current `:root` block (lines 13–72) must be extended. All new variables are **additive** — do not remove existing ones. Insert after the last existing variable (`--ease-spring`).

#### Extended Teal Scale
```css
--teal-800:  #0A4A54;   /* dark teal section backgrounds */
--teal-100:  #B8EEF4;   /* light teal text on dark, rare use */
```

#### Full Coral / Container Red Scale
```css
--coral-700: #8B1A1A;
--coral-600: #B02020;
--coral-500: #C0392B;   /* already exists as --coral; keep both */
--coral-400: #D94F42;
--coral-300: #E87D74;
--coral-100: #F9D4D0;
```

#### Full Amber / Sunset Gold Scale
```css
--amber-700: #7A4F0A;
--amber-400: #F0BE5A;
--amber-300: #F5D07A;
/* --amber-500: #E8A838 already exists */
```

#### Semantic Status Colors
```css
--success:      #1DB87A;
--success-dim:  #0F6B47;
--success-glow: rgba(29,184,122,.25);
--warning-dim:  #5C3D08;
--danger:       #E05050;
--danger-dim:   #5A1515;
--danger-glow:  rgba(224,80,80,.25);
--flag:         #D4773A;
--flag-dim:     #5C2D0A;
--purple-dim:   #3A1F6E;
```

#### Teal Border Variants
```css
--border-teal:    rgba(27,154,170,.3);
--border-teal-hi: rgba(27,154,170,.6);
```

#### Extended Glow System
```css
--glow-teal-lg:    0 0 32px rgba(27,154,170,.45), 0 0 12px rgba(27,154,170,.25);
--glow-danger-sm:  0 0 10px rgba(224,80,80,.2);
--glow-danger-md:  0 0 20px rgba(224,80,80,.3);
--glow-success-sm: 0 0 10px rgba(29,184,122,.2);
--glow-success-md: 0 0 22px rgba(29,184,122,.3);
```

#### Elevation Shadows
```css
--shadow-card-hover: 0 8px 36px rgba(0,0,0,.45), 0 2px 8px rgba(0,0,0,.25);
```

#### Typography Tokens
```css
--font-ui:   'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
--font-mono: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
```

#### Spacing Scale (8px grid)
```css
--space-1:  0.25rem;   /* 4px */
--space-2:  0.5rem;    /* 8px */
--space-3:  0.75rem;   /* 12px */
--space-4:  1rem;      /* 16px */
--space-5:  1.25rem;   /* 20px */
--space-6:  1.5rem;    /* 24px */
--space-8:  2rem;      /* 32px */
--space-10: 2.5rem;    /* 40px */
--space-12: 3rem;      /* 48px */
--space-16: 4rem;      /* 64px */
```

#### Ocean Scene Palette Tokens (mutable — changed by applyPalette())
These start as night defaults; JS overwrites them on page load and every 60 seconds.
```css
--sky-top:        #010814;
--sky-horizon:    #0A1628;
--sky-mid:        #061020;
--ocean-surface:  #061830;
--ocean-deep:     #020C1A;
--ocean-foam:     rgba(77,207,223,.12);
--ambient-tint:   rgba(27,154,170,.04);
--celestial-color:#FFFFFF;
--celestial-glow: rgba(255,255,255,.15);
--star-opacity:   0.85;
--cloud-opacity:  0.06;
--horizon-haze:   rgba(27,154,170,.08);
```

#### Frosted Glass System Tokens
```css
--glass-bg:          rgba(10,22,40,.72);
--glass-border:      rgba(77,207,223,.12);
--glass-blur:        blur(18px);
--glass-shadow:      0 8px 32px rgba(0,0,0,.45), 0 1px 0 rgba(77,207,223,.08) inset;
--glass-bg-light:    rgba(15,32,64,.65);
--glass-border-light:rgba(77,207,223,.18);
```

#### Ripple Token
```css
--ripple-color: rgba(77,207,223,.35);
```

---

### 1.2 New Keyframe Animations

Add all blocks below to the existing keyframes section (after the last existing `@keyframes` at ~line 2521, before the closing `</style>`).

#### Remove: Delete duplicate `@keyframes spin` at line 1143 (keep line 522)

#### New keyframes:

```css
/* Ship crossing the horizon — rightward ships mirror this with scaleX(-1) */
@keyframes ship-cross-left {
  from { transform: translateX(110vw); }
  to   { transform: translateX(-320px); }
}

/* Cloud drift — two directions for variety */
@keyframes cloud-drift-left {
  from { transform: translateX(0); }
  to   { transform: translateX(-110vw); }
}
@keyframes cloud-drift-right {
  from { transform: translateX(0); }
  to   { transform: translateX(110vw); }
}

/* Stars twinkling — canvas handles this, but CSS fallback for static star dots */
@keyframes star-twinkle {
  0%, 100% { opacity: var(--star-opacity, .85); }
  50%       { opacity: calc(var(--star-opacity, .85) * .3); }
}

/* Ripple click wave */
@keyframes ripple-expand {
  from { transform: scale(0); opacity: 1; }
  to   { transform: scale(4); opacity: 0; }
}

/* Button float idle — for the Analyze button */
@keyframes btn-float {
  0%, 100% { transform: translateY(0); }
  50%       { transform: translateY(-3px); }
}

/* Stat pill bob — staggered for each pill */
@keyframes stat-bob {
  0%, 100% { transform: translateY(0); }
  50%       { transform: translateY(-2px); }
}

/* Progress ship rider bounce */
@keyframes ship-rider-bounce {
  0%, 100% { transform: translateY(0) translateX(-50%); }
  50%       { transform: translateY(-3px) translateX(-50%); }
}

/* Module card slide-right on hover — handled via CSS transform, not keyframe */

/* Celestial body (sun/moon) rise/set — used only for dramatic palette transitions */
@keyframes celestial-pulse {
  0%, 100% { filter: blur(6px); }
  50%       { filter: blur(8px); }
}

/* Foam sparkle in ocean waves */
@keyframes foam-flash {
  0%, 100% { opacity: 0; }
  50%       { opacity: .6; }
}

/* Quick-load button bounce on hover */
@keyframes quick-bounce {
  0%, 100% { transform: translateY(0); }
  40%       { transform: translateY(-5px); }
  70%       { transform: translateY(-2px); }
}

/* Settings gear spin on hover */
@keyframes gear-spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(30deg); }
}

/* Palette transition — smooth cross-fade of background colors */
@keyframes palette-fade {
  from { opacity: 0; }
  to   { opacity: 1; }
}
```

---

### 1.3 Ocean Scene Layer Structure — z-index Stack

```
z-index stack (bottom to top):

  -2   #ocean-scene         position:fixed, inset:0, pointer-events:none
         │
         ├── -2  #sky-layer              The sky gradient + celestial body
         ├── -2  #stars-canvas           HTML5 canvas, pointer-events:none
         ├── -2  #cloud-layer            Five .cloud-puff elements
         ├── -1  #horizon-layer          Dock silhouette SVG
         ├── -1  #ocean-water-layer      Ocean SVG + foam sparkles
         └──  0  #ships-layer            Spawned ship divs

  -1   body::before          radial glow overlay (teal from top)
   0   #wave-bg               REMOVE — replaced by #ocean-scene
   0   nav-grid overlay       SVG pattern, 60px grid

   1   All content panels (analyze-panel, dashboard-panel, bulk-panel)
 100   header
 200   .modal-overlay, .bulk-modal-overlay
 300   #settings-backdrop, #settings-drawer
9000   #auth-overlay
```

**CSS for #ocean-scene wrapper:**
```css
#ocean-scene {
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: -2;
  overflow: hidden;
}
```

**Remove `#wave-bg` entirely** — its HTML, its CSS block (lines 137–148), and the wave-bg SVG in the HTML (lines 2876–2889). The ocean scene replaces it at z-index -2.

---

### 1.4 Frosted Glass System — Card and Panel Styles

Add these CSS classes. They are applied to: auth card, settings drawer, modals, and (optionally) decision banners.

```css
.glass {
  background: var(--glass-bg);
  border: 1px solid var(--glass-border);
  backdrop-filter: var(--glass-blur);
  -webkit-backdrop-filter: var(--glass-blur);
  box-shadow: var(--glass-shadow);
}

.glass-light {
  background: var(--glass-bg-light);
  border: 1px solid var(--glass-border-light);
  backdrop-filter: var(--glass-blur);
  -webkit-backdrop-filter: var(--glass-blur);
  box-shadow: var(--glass-shadow);
}

/* Auth card override */
.auth-card {
  /* Replace existing background/border with: */
  background: var(--glass-bg) !important;
  border: 1px solid var(--glass-border) !important;
  backdrop-filter: var(--glass-blur) !important;
  -webkit-backdrop-filter: var(--glass-blur) !important;
  box-shadow: var(--glass-shadow) !important;
}

/* Ship watermark inside auth card — positioned absolutely, very low opacity */
.auth-card::before {
  content: '';
  position: absolute;
  bottom: -20px;
  right: -20px;
  width: 160px;
  height: 100px;
  background-image: url("data:image/svg+xml,..."); /* see Phase 6 for SVG data URI */
  background-size: contain;
  background-repeat: no-repeat;
  opacity: 0.04;
  pointer-events: none;
}
```

**Reduced motion override for frosted glass:**
```css
@media (prefers-reduced-motion: reduce) {
  .glass, .glass-light { backdrop-filter: none; background: var(--bg-surface); }
}
```

---

### 1.5 Time-of-Day Color Palette Specification — All 7 Palettes

Each palette is a JS object. `getPalette(hour)` returns one of these. All hex values are final.

#### Palette 0 — Night (22:00–03:59)
```
label:          "Night"
skyTop:         "#010814"    /* near-black, barely distinguishable from void */
skyMid:         "#061020"    /* deep midnight */
skyHorizon:     "#0A1628"    /* matches --bg-deep, seamless page blend */
oceanSurface:   "#061830"    /* dark harbor water, moonlit */
oceanDeep:      "#020C1A"    /* abyss */
oceanFoam:      "rgba(77,207,223,.08)"
ambientTint:    "rgba(27,154,170,.03)"
celestialColor: "#E8EFF8"    /* pale moon white */
celestialGlow:  "rgba(232,239,248,.18)"
celestialSize:  "36px"       /* moon radius */
isSun:          false
starOpacity:    0.90
cloudOpacity:   0.04
horizonHaze:    "rgba(27,154,170,.06)"
dockSilhouette: "#020A14"    /* almost invisible */
waterHighlight: "rgba(232,239,248,.04)"  /* moonlit water glint */
```

#### Palette 1 — Dawn (04:00–05:59)
```
label:          "Dawn"
skyTop:         "#0D0A1F"    /* deep violet pre-dawn */
skyMid:         "#2D1B3D"    /* purple-magenta transition */
skyHorizon:     "#7A3A2A"    /* rust/brick horizon glow */
oceanSurface:   "#122035"    /* still-dark water warming */
oceanDeep:      "#060E1A"
oceanFoam:      "rgba(180,120,80,.12)"  /* warm dawn foam */
ambientTint:    "rgba(120,60,40,.06)"
celestialColor: "#FF9060"    /* rising sun — orange-red */
celestialGlow:  "rgba(255,144,96,.35)"
celestialSize:  "48px"       /* sun near horizon appears larger */
isSun:          true
starOpacity:    0.25          /* stars fading out */
cloudOpacity:   0.10
horizonHaze:    "rgba(200,100,60,.20)"  /* warm horizon bloom */
dockSilhouette: "#0A0812"
waterHighlight: "rgba(255,144,96,.06)"
```

#### Palette 2 — Sunrise (06:00–07:59)
```
label:          "Sunrise"
skyTop:         "#1A1035"    /* indigo high */
skyMid:         "#8B3A2A"    /* deep coral mid */
skyHorizon:     "#E87840"    /* bright orange horizon */
oceanSurface:   "#1A3A50"    /* water catching first light */
oceanDeep:      "#0A1828"
oceanFoam:      "rgba(232,168,64,.18)"
ambientTint:    "rgba(232,120,64,.08)"
celestialColor: "#FFB040"    /* warm gold sun cresting */
celestialGlow:  "rgba(255,176,64,.50)"
celestialSize:  "52px"       /* still large near horizon */
isSun:          true
starOpacity:    0.05
cloudOpacity:   0.20          /* pink-tinted clouds visible */
horizonHaze:    "rgba(232,120,64,.30)"
dockSilhouette: "#0E1A28"
waterHighlight: "rgba(255,176,64,.10)"
```

#### Palette 3 — Morning (08:00–11:59)
```
label:          "Morning"
skyTop:         "#1A3A6E"    /* clear blue sky */
skyMid:         "#2A5A9A"    /* bright blue mid */
skyHorizon:     "#4A8ABE"    /* light blue horizon */
oceanSurface:   "#0F4060"    /* bright harbor blue-green */
oceanDeep:      "#082030"
oceanFoam:      "rgba(255,255,255,.18)"
ambientTint:    "rgba(74,138,190,.06)"
celestialColor: "#FFF5C0"    /* bright white-gold sun */
celestialGlow:  "rgba(255,245,192,.55)"
celestialSize:  "44px"
isSun:          true
starOpacity:    0.00
cloudOpacity:   0.55          /* white daytime clouds */
horizonHaze:    "rgba(74,138,190,.15)"
dockSilhouette: "#0A1A2E"
waterHighlight: "rgba(255,255,255,.12)"
```

#### Palette 4 — Day (12:00–15:59)
```
label:          "Day"
skyTop:         "#1044A0"    /* vivid noon blue */
skyMid:         "#2860C0"    /* saturated sky */
skyHorizon:     "#5A90D0"    /* bright horizon */
oceanSurface:   "#0E4870"    /* bright turquoise-blue harbor */
oceanDeep:      "#07243A"
oceanFoam:      "rgba(255,255,255,.22)"
ambientTint:    "rgba(90,144,208,.05)"
celestialColor: "#FFFDE0"    /* near-white noon sun */
celestialGlow:  "rgba(255,253,224,.60)"
celestialSize:  "40px"       /* sun appears smaller overhead */
isSun:          true
starOpacity:    0.00
cloudOpacity:   0.65
horizonHaze:    "rgba(90,144,208,.12)"
dockSilhouette: "#081522"
waterHighlight: "rgba(255,255,255,.15)"
```

#### Palette 5 — Afternoon (16:00–17:59)
```
label:          "Afternoon"
skyTop:         "#0C2A6A"    /* deepening blue */
skyMid:         "#2A5A80"    /* blue-teal afternoon */
skyHorizon:     "#D07840"    /* warm amber horizon starting */
oceanSurface:   "#0C3858"    /* afternoon harbor, slightly deeper */
oceanDeep:      "#061A2E"
oceanFoam:      "rgba(255,200,140,.14)"
ambientTint:    "rgba(208,120,64,.05)"
celestialColor: "#FFD060"    /* warm afternoon gold */
celestialGlow:  "rgba(255,208,96,.45)"
celestialSize:  "46px"       /* sun lowering */
isSun:          true
starOpacity:    0.00
cloudOpacity:   0.50
horizonHaze:    "rgba(208,120,64,.22)"
dockSilhouette: "#0A1828"
waterHighlight: "rgba(255,208,96,.08)"
```

#### Palette 6 — Sunset / Dusk (18:00–21:59)
```
label:          "Dusk"
skyTop:         "#0C1430"    /* deep violet-navy */
skyMid:         "#6A2A18"    /* deep coral-red mid */
skyHorizon:     "#F05820"    /* burning orange-red horizon */
oceanSurface:   "#16283A"    /* dark rippling water, red reflections */
oceanDeep:      "#080E1C"
oceanFoam:      "rgba(240,88,32,.15)"   /* sunset-tinted foam */
ambientTint:    "rgba(240,88,32,.08)"
celestialColor: "#FF6020"    /* deep red setting sun */
celestialGlow:  "rgba(255,96,32,.55)"
celestialSize:  "54px"       /* large near horizon */
isSun:          true
starOpacity:    0.30          /* early stars appearing */
cloudOpacity:   0.35          /* backlit silhouette clouds */
horizonHaze:    "rgba(240,88,32,.38)"
dockSilhouette: "#060A12"    /* deep silhouette */
waterHighlight: "rgba(255,96,32,.12)"
```

---

### 1.6 Button Animation Specifications

#### Ripple Click Effect (all buttons)
```css
/* Required on every button that gets ripple: position: relative; overflow: hidden */
.btn-ripple {
  position: relative;
  overflow: hidden;
}

.ripple-wave {
  position: absolute;
  border-radius: 50%;
  width: 40px;
  height: 40px;
  background: var(--ripple-color);
  pointer-events: none;
  /* centered on click point via JS: left = x - 20; top = y - 20 */
  animation: ripple-expand 600ms var(--ease-out) forwards;
}
```

#### Analyze Button — Float Idle
```css
#analyze-btn:not(:disabled) {
  animation: btn-float 3s ease-in-out infinite;
}
#analyze-btn:not(:disabled):hover {
  animation: none;   /* stop float on hover; spring lift takes over */
  transform: translateY(-2px);
}
#analyze-btn:not(:disabled):active {
  animation: none;
  transform: translateY(0);
}
```

#### Spring Lift (secondary buttons)
Apply to: `.auth-btn`, `.quick-btn`, `.dash-refresh-btn`, `.feedback-btn`, `.bulk-cancel-btn`, `.bulk-filter-btn`, `.bulk-export-btn`, `.bulk-new-batch-btn`, `.bulk-template-btn`, `.ph-load-btn`, `.ph-reset-btn`, `.rej-try-again`, `.tab-add`, `#download-report-btn`, `#single-share-btn`:
```css
.spring-lift {
  transition: transform 150ms var(--ease-spring),
              box-shadow 150ms ease-out;
}
.spring-lift:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: var(--glow-teal-sm);
}
.spring-lift:active:not(:disabled) {
  transform: translateY(0);
  box-shadow: none;
}
```

#### Quick-Load Button Emoji Bounce
```css
.quick-btn:hover .quick-emoji {
  animation: quick-bounce 400ms var(--ease-spring);
}
```

#### Settings Gear Spin
```css
.settings-gear-btn:hover svg {
  animation: gear-spin 300ms var(--ease-spring) forwards;
}
```

#### Stat Pill Bob (staggered)
```css
.stat-pill:nth-child(1) { animation: stat-bob 3.0s ease-in-out infinite; animation-delay: 0s; }
.stat-pill:nth-child(2) { animation: stat-bob 3.0s ease-in-out infinite; animation-delay: 0.25s; }
.stat-pill:nth-child(3) { animation: stat-bob 3.0s ease-in-out infinite; animation-delay: 0.50s; }
.stat-pill:nth-child(4) { animation: stat-bob 3.0s ease-in-out infinite; animation-delay: 0.75s; }
.stat-pill:nth-child(5) { animation: stat-bob 3.0s ease-in-out infinite; animation-delay: 1.00s; }
.stat-pill:nth-child(6) { animation: stat-bob 3.0s ease-in-out infinite; animation-delay: 1.25s; }
```

#### Module Card Slide-Right
```css
.module-card {
  transition: transform 200ms var(--ease-out),
              border-color 180ms ease,
              box-shadow 180ms ease;
}
.module-card:hover:not(.locked) {
  transform: translateX(4px);
  border-color: var(--border-teal);
  box-shadow: var(--glow-teal-sm);
}
```

#### Preset Button Lift + Bounce
```css
.preset-btn {
  transition: transform 200ms var(--ease-spring),
              background 180ms ease,
              border-color 180ms ease;
}
.preset-btn:hover {
  transform: translateY(-2px) scale(1.03);
}
.preset-btn:active {
  transform: translateY(0) scale(0.98);
}
```

---

### 1.7 Reduced Motion Fallbacks

Add at the very end of `<style>`, after all other CSS:
```css
@media (prefers-reduced-motion: reduce) {
  /* Kill all ambient animations */
  #ocean-scene,
  #sky-layer,
  #cloud-layer,
  #ships-layer { display: none !important; }

  /* Kill stat-pill bob */
  .stat-pill { animation: none !important; }

  /* Kill analyze float */
  #analyze-btn { animation: none !important; }

  /* Kill ship rider bounce */
  .bulk-progress-ship { animation: none !important; }

  /* Kill star twinkle */
  .star-static { animation: none !important; }

  /* Kill ripple — JS checks this and skips attachment */
  .ripple-wave { display: none !important; }

  /* Kill all spring lifts — revert to instant color changes */
  .spring-lift:hover { transform: none !important; }
  .quick-btn:hover { transform: none !important; }
  .preset-btn:hover { transform: none !important; }
  .module-card:hover { transform: none !important; }
  .settings-gear-btn:hover svg { animation: none !important; }

  /* Keep body::before glow but reduce */
  body::before { opacity: .3 !important; }
}
```

---

## PHASE 2 — Ocean Scene HTML Structure

### 2.1 Full DOM Structure

Replace the existing `#wave-bg` div (lines 2876–2889) with this complete block:

```html
<!-- ===================== OCEAN SCENE ===================== -->
<div id="ocean-scene" aria-hidden="true">

  <!-- Layer 1: Sky — gradient background + celestial body -->
  <div id="sky-layer">
    <div id="celestial-body"></div>
  </div>

  <!-- Layer 2: Stars — HTML5 canvas, drawn by drawStars() -->
  <canvas id="stars-canvas"></canvas>

  <!-- Layer 3: Clouds — five independent puffs -->
  <div id="cloud-layer">
    <div class="cloud-puff" id="cloud-1" data-speed="90" data-dir="left"  data-y="8"  data-opacity="0.6"></div>
    <div class="cloud-puff" id="cloud-2" data-speed="130" data-dir="right" data-y="14" data-opacity="0.45"></div>
    <div class="cloud-puff" id="cloud-3" data-speed="110" data-dir="left"  data-y="5"  data-opacity="0.55"></div>
    <div class="cloud-puff" id="cloud-4" data-speed="150" data-dir="right" data-y="20" data-opacity="0.35"></div>
    <div class="cloud-puff" id="cloud-5" data-speed="80"  data-dir="left"  data-y="10" data-opacity="0.50"></div>
  </div>

  <!-- Layer 4: Horizon — dock silhouette -->
  <div id="horizon-layer">
    <!-- Dock SVG injected by initOceanScene() — see §2.4 -->
  </div>

  <!-- Layer 5: Ocean water — animated SVG waves + foam -->
  <div id="ocean-water-layer">
    <!-- Ocean SVG injected by initOceanScene() — see §2.5 -->
  </div>

  <!-- Layer 6: Ships — dynamic ship divs spawned by spawnShip() -->
  <div id="ships-layer"></div>

</div>
```

### 2.2 CSS for Ocean Scene Layers

```css
#sky-layer {
  position: absolute;
  inset: 0;
  /* background set by applyPalette() via JS inline style */
  transition: background 8s ease;
}

#celestial-body {
  position: absolute;
  border-radius: 50%;
  pointer-events: none;
  /* left, top, width, height, background, box-shadow set by applyPalette() */
  animation: celestial-pulse 4s ease-in-out infinite;
  transition: background 8s ease, box-shadow 8s ease, top 8s ease, left 8s ease;
}

#stars-canvas {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 1;
  /* opacity controlled by applyPalette() */
  transition: opacity 8s ease;
}

#cloud-layer {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 2;
}

.cloud-puff {
  position: absolute;
  pointer-events: none;
  /* width, height, top set from data-attributes by initOceanScene() */
  /* background is radial-gradient SVG — see §2.3 */
  will-change: transform;
}

#horizon-layer {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  /* height covers bottom 30% of viewport */
  height: 30vh;
  pointer-events: none;
  z-index: 3;
}

#ocean-water-layer {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 22vh;
  pointer-events: none;
  z-index: 4;
  overflow: hidden;
}

#ships-layer {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 5;
}

.ocean-ship {
  position: absolute;
  pointer-events: none;
  will-change: transform;
  /* bottom position set by spawnShip() — between 10vh and 18vh from bottom */
}
```

---

### 2.3 Cloud Puff Specifications

Each cloud puff is built from layered radial gradients. The `data-*` attributes control animation parameters; `initOceanScene()` reads them and sets inline styles.

| Cloud | ID | Width | Height | Y position | Base opacity | Speed (seconds) | Direction |
|---|---|---|---|---|---|---|---|
| 1 — large far-left | `#cloud-1` | 320px | 80px | 8% from top | 0.60 | 90s | left |
| 2 — medium right | `#cloud-2` | 220px | 60px | 14% from top | 0.45 | 130s | right |
| 3 — small high | `#cloud-3` | 160px | 45px | 5% from top | 0.55 | 110s | left |
| 4 — wispy low | `#cloud-4` | 280px | 50px | 20% from top | 0.35 | 150s | right |
| 5 — medium mid | `#cloud-5` | 200px | 65px | 10% from top | 0.50 | 80s | left |

**Cloud CSS (set by JS on each element):**
```css
/* Applied via JS: element.style.background = buildCloudGradient() */
/* buildCloudGradient() returns a string like: */
"radial-gradient(ellipse 50% 40% at 50% 50%, rgba(255,255,255,0.9) 0%, rgba(255,255,255,0.5) 40%, transparent 70%),
 radial-gradient(ellipse 30% 50% at 35% 60%, rgba(255,255,255,0.7) 0%, transparent 60%),
 radial-gradient(ellipse 25% 45% at 65% 55%, rgba(255,255,255,0.6) 0%, transparent 55%)"
```

The actual white/grey tint is modulated by the current palette's `cloudOpacity`. The cloud element `opacity` is set to `palette.cloudOpacity * data-opacity`.

**Cloud starting X positions (staggered so they don't all start at the same edge):**
```
cloud-1: starts at 10% of viewport width
cloud-2: starts at 60% of viewport width
cloud-3: starts at 80% of viewport width
cloud-4: starts at 30% of viewport width
cloud-5: starts at -50px (just off-screen left)
```

**Cloud animations — set by JS on init:**
```js
// For leftward clouds:
element.style.animation = `cloud-drift-left ${speed}s linear infinite`;
// For rightward clouds:
element.style.animation = `cloud-drift-right ${speed}s linear infinite`;
```

---

### 2.4 Dock Silhouette SVG Specification

Injected as innerHTML of `#horizon-layer`. The SVG is `width="100%" height="100%" viewBox="0 0 1440 320" preserveAspectRatio="xMidYMax meet"`.

All shapes use `fill="var(--dock-silhouette, #0A0A14)"` which is set from the palette.

#### Structure (left to right across the 1440px viewBox):

**Far background buildings (behind the cranes):**
- Warehouse block A: `rect x=40 y=120 w=160 h=200 rx=2` — squat warehouse
- Water tower: `rect x=220 y=80 w=30 h=160` + `ellipse cx=235 cy=80 rx=25 ry=15` — tank on post
- Warehouse block B: `rect x=280 y=140 w=220 h=180`
- Office tower: `rect x=540 y=60 w=80 h=260 rx=1` — tall glass building
  - Windows: `rect x=548 y=70 w=10 h=12 rx=1 opacity=0.3` repeated in 4×8 grid
- Warehouse block C: `rect x=660 y=130 w=200 h=190`
- Low container storage building: `rect x=900 y=160 w=300 h=160`
- Distant crane tower (background): `rect x=1250 y=50 w=16 h=270`

**Gantry Cranes — the iconic container port element (3 cranes):**

Crane 1 (x center = 350):
```
/* Legs */
rect x=316 y=200 w=12 h=120   /* left leg */
rect x=372 y=200 w=12 h=120   /* right leg */
/* Portal beam connecting legs */
rect x=310 y=198 w=80 h=10    /* crossbeam */
/* Tower */
rect x=344 y=80 w=12 h=125    /* vertical tower */
/* Boom arm extending left over water */
rect x=180 y=77 w=170 h=8     /* boom left */
/* Boom arm extending right over dock */
rect x=355 y=77 w=120 h=8     /* boom right */
/* Boom tip counterweight */
rect x=466 y=65 w=20 h=22 rx=2
/* Trolley + hoist rope (centered, lowered position) */
rect x=348 y=85 w=8 h=80 stroke=rgba(255,255,255,0.06) stroke-width=1 fill=none
/* Spreader bar at bottom of hoist */
rect x=338 y=162 w=28 h=6
/* Navigation light on top of tower */
circle cx=350 cy=75 r=4 fill="#FF4040" opacity=0.7
```

Crane 2 (x center = 780, slightly larger — foreground crane):
```
/* Legs — wider stance */
rect x=742 y=190 w=14 h=130
rect x=810 y=190 w=14 h=130
rect x=734 y=187 w=92 h=12
/* Tower */
rect x=776 y=60 w=14 h=135
/* Boom — longer, dominates skyline */
rect x=580 y=56 w=200 h=9
rect x=789 y=56 w=160 h=9
/* Counterweight */
rect x=936 y=42 w=24 h=26 rx=2
/* Hoist and spreader */
rect x=780 y=65 w=8 h=95
rect x=768 y=157 w=32 h=7
/* Cab/operator box at tower top */
rect x=769 y=100 w=22 h=18 rx=1
/* Nav light */
circle cx=783 cy=52 r=5 fill="#FF4040" opacity=0.8
```

Crane 3 (x center = 1180, smaller — background right):
```
rect x=1154 y=210 w=10 h=110
rect x=1202 y=210 w=10 h=110
rect x=1148 y=208 w=70 h=9
rect x=1172 y=100 w=10 h=115
rect x=1020 y=97 w=155 h=7
rect x=1181 y=97 w=100 h=7
rect x=1272 y=85 w=18 h=20 rx=2
rect x=1174 y=105 w=6 h=70
rect x=1167 y=172 w=22 h=5
circle cx=1177 cy=93 r=3.5 fill="#FF4040" opacity=0.65
```

**Shipping Containers — stacked on the dock (center area):**
```
/* Row 1 — bottom, 8 containers, varied colors (all at dock silhouette opacity but slightly lighter) */
/* Containers are small rects 32px wide × 18px tall, separated by 2px gaps */
/* Y position = 280 (near dock surface) */
/* Colors (at 60% opacity mixed with silhouette): */
/* Container A: coral red */    rect x=460 y=262 w=32 h=18 rx=1 fill="rgba(192,57,43,.4)"
/* Container B: amber */        rect x=494 y=262 w=32 h=18 rx=1 fill="rgba(232,168,56,.4)"
/* Container C: teal */         rect x=528 y=262 w=32 h=18 rx=1 fill="rgba(27,154,170,.4)"
/* Container D: coral */        rect x=562 y=262 w=32 h=18 rx=1 fill="rgba(192,57,43,.4)"
/* Container E: amber */        rect x=596 y=262 w=32 h=18 rx=1 fill="rgba(232,168,56,.4)"
/* Container F: gray */         rect x=630 y=262 w=32 h=18 rx=1 fill="rgba(80,100,130,.4)"
/* Container G: coral */        rect x=664 y=262 w=32 h=18 rx=1 fill="rgba(192,57,43,.4)"
/* Container H: teal */         rect x=698 y=262 w=32 h=18 rx=1 fill="rgba(27,154,170,.4)"

/* Row 2 — stacked on top, 6 containers, starting at y=242 */
rect x=468 y=242 w=32 h=18 rx=1 fill="rgba(232,168,56,.35)"
rect x=502 y=242 w=32 h=18 rx=1 fill="rgba(192,57,43,.35)"
rect x=536 y=242 w=32 h=18 rx=1 fill="rgba(27,154,170,.35)"
rect x=570 y=242 w=32 h=18 rx=1 fill="rgba(80,100,130,.35)"
rect x=604 y=242 w=32 h=18 rx=1 fill="rgba(192,57,43,.35)"
rect x=638 y=242 w=32 h=18 rx=1 fill="rgba(232,168,56,.35)"

/* Row 3 — top, 4 containers, y=222 */
rect x=476 y=222 w=32 h=18 rx=1 fill="rgba(192,57,43,.30)"
rect x=510 y=222 w=32 h=18 rx=1 fill="rgba(27,154,170,.30)"
rect x=544 y=222 w=32 h=18 rx=1 fill="rgba(232,168,56,.30)"
rect x=578 y=222 w=32 h=18 rx=1 fill="rgba(80,100,130,.30)"
```

**Dock / Wharf structure:**
```
/* Main wharf edge — a thick horizontal bar at the waterline */
rect x=0 y=295 w=1440 h=25 fill="--dock-silhouette"
/* Pier bollards — vertical stubs along the edge */
/* 20px wide × 12px tall, every 80px from x=40 */
/* 16 bollards total */
```

**Navigation beacon on left pier:**
```
line x1=80 y1=295 x2=80 y2=240 stroke="--dock-silhouette" stroke-width=3
circle cx=80 cy=236 r=6 fill="#FF8000" opacity=0.7
```

---

### 2.5 Ocean Water SVG with Foam Sparkles

Injected as innerHTML of `#ocean-water-layer`. `viewBox="0 0 1440 200" preserveAspectRatio="xMidYMax meet" width="100%" height="100%"`.

**Wave fills (two overlapping fills for depth):**

```xml
<!-- Deep ocean fill — background color gradient -->
<defs>
  <linearGradient id="ocean-depth-grad" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="var(--ocean-surface)" stop-opacity="0.95"/>
    <stop offset="100%" stop-color="var(--ocean-deep)" stop-opacity="1"/>
  </linearGradient>
  <!-- Foam sparkle gradient — small ellipses at wave crests -->
  <radialGradient id="foam-grad">
    <stop offset="0%" stop-color="white" stop-opacity="0.8"/>
    <stop offset="100%" stop-color="white" stop-opacity="0"/>
  </radialGradient>
</defs>

<!-- Solid background fill -->
<rect x="0" y="0" width="1440" height="200" fill="url(#ocean-depth-grad)"/>

<!-- Wave surface path 1 — primary wave, animated translateX left, 22s -->
<path id="wave-primary"
  d="M0,80 C180,60 360,100 540,80 C720,60 900,100 1080,80 C1260,60 1440,100 1620,80
     C1800,60 1980,100 2160,80 C2340,60 2520,100 2700,80 L2700,200 L0,200 Z"
  fill="var(--ocean-surface)" opacity="0.7"
  style="animation: wave-drift-left 22s linear infinite; will-change: transform;"/>

<!-- Wave surface path 2 — secondary wave, animated translateX right, 28s, offset -->
<path id="wave-secondary"
  d="M0,95 C240,75 480,115 720,95 C960,75 1200,115 1440,95
     C1680,75 1920,115 2160,95 C2400,75 2640,115 2880,95 L2880,200 L0,200 Z"
  fill="var(--ocean-surface)" opacity="0.45"
  style="animation: wave-drift-right 28s linear infinite; will-change: transform;"/>

<!-- Horizon shimmer line -->
<line x1="0" y1="4" x2="1440" y2="4"
  stroke="var(--water-highlight, rgba(255,255,255,.08))" stroke-width="1.5" opacity="0.6"
  style="animation: wave-drift-left 18s linear infinite;"/>

<!-- Foam sparkle clusters — 12 total, positioned at likely wave crests -->
<!-- Each is a small ellipse with foam-flash animation, staggered delays -->
<ellipse class="foam-spark" cx="120"  cy="78"  rx="18" ry="5" fill="url(#foam-grad)" style="animation: foam-flash 3.2s ease-in-out infinite; animation-delay: 0.0s;"/>
<ellipse class="foam-spark" cx="310"  cy="92"  rx="12" ry="4" fill="url(#foam-grad)" style="animation: foam-flash 2.8s ease-in-out infinite; animation-delay: 0.5s;"/>
<ellipse class="foam-spark" cx="520"  cy="75"  rx="22" ry="6" fill="url(#foam-grad)" style="animation: foam-flash 3.5s ease-in-out infinite; animation-delay: 1.1s;"/>
<ellipse class="foam-spark" cx="700"  cy="88"  rx="14" ry="4" fill="url(#foam-grad)" style="animation: foam-flash 2.6s ease-in-out infinite; animation-delay: 0.3s;"/>
<ellipse class="foam-spark" cx="860"  cy="80"  rx="20" ry="5" fill="url(#foam-grad)" style="animation: foam-flash 3.0s ease-in-out infinite; animation-delay: 1.8s;"/>
<ellipse class="foam-spark" cx="1050" cy="93"  rx="16" ry="5" fill="url(#foam-grad)" style="animation: foam-flash 3.3s ease-in-out infinite; animation-delay: 0.8s;"/>
<ellipse class="foam-spark" cx="1200" cy="78"  rx="10" ry="3" fill="url(#foam-grad)" style="animation: foam-flash 2.9s ease-in-out infinite; animation-delay: 2.1s;"/>
<ellipse class="foam-spark" cx="1360" cy="88"  rx="18" ry="5" fill="url(#foam-grad)" style="animation: foam-flash 3.1s ease-in-out infinite; animation-delay: 0.6s;"/>
<ellipse class="foam-spark" cx="220"  cy="85"  rx="8"  ry="3" fill="url(#foam-grad)" style="animation: foam-flash 2.7s ease-in-out infinite; animation-delay: 1.4s;"/>
<ellipse class="foam-spark" cx="640"  cy="82"  rx="14" ry="4" fill="url(#foam-grad)" style="animation: foam-flash 3.4s ease-in-out infinite; animation-delay: 2.5s;"/>
<ellipse class="foam-spark" cx="980"  cy="76"  rx="11" ry="3" fill="url(#foam-grad)" style="animation: foam-flash 2.5s ease-in-out infinite; animation-delay: 0.9s;"/>
<ellipse class="foam-spark" cx="1290" cy="91"  rx="16" ry="5" fill="url(#foam-grad)" style="animation: foam-flash 3.6s ease-in-out infinite; animation-delay: 1.7s;"/>
```

---

## PHASE 3 — JavaScript Engine

### 3.1 getPalette(hour)

```js
/**
 * Returns the palette object for the given hour (0–23).
 * All palette objects contain the keys defined in Phase 1.5.
 */
function getPalette(hour) {
  if (hour >= 22 || hour < 4)  return PALETTES.night;
  if (hour < 6)                return PALETTES.dawn;
  if (hour < 8)                return PALETTES.sunrise;
  if (hour < 12)               return PALETTES.morning;
  if (hour < 16)               return PALETTES.day;
  if (hour < 18)               return PALETTES.afternoon;
  return PALETTES.dusk;        /* 18–21 */
}
```

The `PALETTES` constant is a plain object keyed by the 7 names, each value being the palette spec from Phase 1.5 (skyTop, skyMid, skyHorizon, etc.).

---

### 3.2 applyPalette(p)

```js
/**
 * Applies palette object p to:
 *   1. All --ocean-* and --sky-* and --ambient-* CSS variables on :root
 *   2. #sky-layer background gradient (inline style)
 *   3. #celestial-body position, color, glow, size
 *   4. #stars-canvas opacity
 *   5. #cloud-layer child opacities (multiplied by each cloud's base opacity)
 *
 * All transitions are handled by CSS transition declarations on the elements.
 * This function only sets values — CSS does the animation.
 */
function applyPalette(p) {
  const root = document.documentElement.style;

  // CSS variable updates — these drive all downstream color changes
  root.setProperty('--sky-top',        p.skyTop);
  root.setProperty('--sky-horizon',    p.skyHorizon);
  root.setProperty('--sky-mid',        p.skyMid);
  root.setProperty('--ocean-surface',  p.oceanSurface);
  root.setProperty('--ocean-deep',     p.oceanDeep);
  root.setProperty('--ocean-foam',     p.oceanFoam);
  root.setProperty('--ambient-tint',   p.ambientTint);
  root.setProperty('--celestial-color',p.celestialColor);
  root.setProperty('--celestial-glow', p.celestialGlow);
  root.setProperty('--star-opacity',   p.starOpacity);
  root.setProperty('--cloud-opacity',  p.cloudOpacity);
  root.setProperty('--horizon-haze',   p.horizonHaze);
  root.setProperty('--dock-silhouette',p.dockSilhouette || '#0A0A14');
  root.setProperty('--water-highlight',p.waterHighlight);

  // Sky layer gradient
  const sky = document.getElementById('sky-layer');
  if (sky) {
    sky.style.background =
      `linear-gradient(to bottom, ${p.skyTop} 0%, ${p.skyMid} 45%, ${p.skyHorizon} 100%)`;
  }

  // Celestial body (sun or moon)
  const cel = document.getElementById('celestial-body');
  if (cel) {
    const size = parseInt(p.celestialSize) || 40;
    // Position: sun arcs from left (sunrise) to right (sunset) at varying heights
    // Moon is centered, higher in sky
    let leftPct, topPct;
    const hour = new Date().getHours();
    if (p.isSun) {
      // Map hour 6→18 to left 10%→90%, height: low at edges, high at noon
      const t = Math.max(0, Math.min(1, (hour - 6) / 12));
      leftPct = 10 + t * 80;
      topPct  = 55 - Math.sin(t * Math.PI) * 45; // 55% at horizon → 10% at noon
    } else {
      leftPct = 55;
      topPct  = 18;
    }
    cel.style.width      = size + 'px';
    cel.style.height     = size + 'px';
    cel.style.left       = leftPct + '%';
    cel.style.top        = topPct + '%';
    cel.style.marginLeft = (-size / 2) + 'px';
    cel.style.marginTop  = (-size / 2) + 'px';
    cel.style.background = p.celestialColor;
    cel.style.boxShadow  = `0 0 ${size * 1.5}px ${p.celestialGlow}, 0 0 ${size * 3}px ${p.celestialGlow.replace('.18', '.06')}`;
  }

  // Stars canvas opacity
  const canvas = document.getElementById('stars-canvas');
  if (canvas) canvas.style.opacity = p.starOpacity;

  // Cloud layer: each cloud's opacity = palette.cloudOpacity * cloud's base data-opacity
  document.querySelectorAll('.cloud-puff').forEach(c => {
    const base = parseFloat(c.dataset.opacity || '0.5');
    c.style.opacity = Math.min(1, p.cloudOpacity * base / 0.5).toFixed(3);
  });

  // Redraw stars for the new opacity
  drawStars(p.starOpacity);
}
```

---

### 3.3 drawStars(opacity) — Canvas Function

```js
/**
 * Draws a static field of stars on #stars-canvas.
 * Uses a seeded random so the star field doesn't change between calls.
 * Stars vary in: radius (0.4–2.2px), opacity (20–100%), slight blue-white tint.
 * Twinkle is handled by CSS @keyframes star-twinkle on .star-static dots —
 * but the canvas version just draws static dots; no per-star JS animation.
 *
 * Call on: initOceanScene(), and whenever palette changes.
 */
function drawStars(opacity) {
  const canvas = document.getElementById('stars-canvas');
  if (!canvas) return;

  canvas.width  = window.innerWidth;
  canvas.height = window.innerHeight * 0.65; /* only upper 65% of viewport */

  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  if (opacity < 0.01) return; /* no stars during day */

  /* Seeded pseudo-random — Mulberry32 PRNG with seed 0xDEADBEEF */
  let seed = 0xDEADBEEF;
  function rnd() {
    seed ^= seed << 13;
    seed ^= seed >> 17;
    seed ^= seed << 5;
    return ((seed >>> 0) / 0xFFFFFFFF);
  }

  const STAR_COUNT = 280;

  for (let i = 0; i < STAR_COUNT; i++) {
    const x       = rnd() * canvas.width;
    const y       = rnd() * canvas.height;
    const r       = 0.4 + rnd() * 1.8;
    const a       = (0.20 + rnd() * 0.80) * opacity;
    /* slight color variation: pure white, cool blue-white, warm yellow-white */
    const hue     = rnd() < 0.3 ? 200 : rnd() < 0.5 ? 50 : 0;
    const sat     = rnd() < 0.4 ? Math.round(rnd() * 30) : 0;

    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = `hsla(${hue}, ${sat}%, 98%, ${a})`;
    ctx.fill();

    /* Add a subtle glow halo on larger stars */
    if (r > 1.4) {
      const grad = ctx.createRadialGradient(x, y, r, x, y, r * 3.5);
      grad.addColorStop(0, `hsla(${hue}, ${sat}%, 98%, ${a * 0.4})`);
      grad.addColorStop(1, 'transparent');
      ctx.beginPath();
      ctx.arc(x, y, r * 3.5, 0, Math.PI * 2);
      ctx.fillStyle = grad;
      ctx.fill();
    }
  }
}
```

---

### 3.4 Ship SVG Builders

#### buildContainerShip(scale)

```js
/**
 * Returns an SVG string for a medium container ship.
 * scale: 1.0 = standard (width ~200px). Use 0.6 for distant, 1.2 for close.
 * Direction controlled by CSS transform: scaleX(-1) for leftward travel.
 */
function buildContainerShip(scale) {
  const w = Math.round(200 * scale);
  const h = Math.round(60  * scale);
  const s = scale;
  return `<svg xmlns="http://www.w3.org/2000/svg"
               width="${w}" height="${h}"
               viewBox="0 0 200 60" fill="none">
    <defs>
      <linearGradient id="ship-hull-${Date.now()}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#1B9AAA"/>
        <stop offset="60%" stop-color="#0D6B7A"/>
        <stop offset="100%" stop-color="#072B30"/>
      </linearGradient>
    </defs>
    <!-- Hull -->
    <path d="M 8,30 L 0,42 L 4,52 L 196,52 L 200,42 L 192,30 Z"
          fill="url(#ship-hull-${Date.now()})"/>
    <!-- Waterline stripe -->
    <path d="M 2,48 L 198,48 L 196,52 L 4,52 Z" fill="#041520" opacity="0.55"/>
    <line x1="0" y1="42" x2="200" y2="42" stroke="#4DCFDF" stroke-width="1" opacity="0.65"/>
    <!-- Container stacks — 4 blocks in two rows -->
    <rect x="20" y="18" width="28" height="12" rx="1" fill="#C0392B"/>
    <rect x="50" y="18" width="28" height="12" rx="1" fill="#E8A838"/>
    <rect x="80" y="18" width="28" height="12" rx="1" fill="#1B9AAA"/>
    <rect x="110" y="18" width="28" height="12" rx="1" fill="#C0392B"/>
    <rect x="26" y="8" width="22" height="10" rx="1" fill="#E8A838"/>
    <rect x="52" y="8" width="22" height="10" rx="1" fill="#C0392B"/>
    <rect x="80" y="8" width="22" height="10" rx="1" fill="#1B9AAA"/>
    <!-- Bridge superstructure -->
    <rect x="145" y="14" width="38" height="16" rx="2" fill="#162845"/>
    <rect x="145" y="14" width="38" height="3"  rx="1" fill="#22B5C8" opacity="0.4"/>
    <rect x="148" y="19" width="6" height="5" rx="0.5" fill="#4DCFDF" opacity="0.55"/>
    <rect x="157" y="19" width="6" height="5" rx="0.5" fill="#4DCFDF" opacity="0.55"/>
    <!-- Funnel -->
    <rect x="163" y="6"  width="8"  height="10" rx="1.5" fill="#C0392B"/>
    <rect x="163" y="12" width="8"  height="3"  fill="#E8A838" opacity="0.9"/>
    <!-- Wake lines -->
    <path d="M 0,53 Q 30,50 60,53 Q 90,56 120,53"
          stroke="#4DCFDF" stroke-width="0.75" fill="none" opacity="0.25"/>
  </svg>`;
}
```

#### buildTugboat(scale)

```js
/**
 * Returns an SVG string for a small tugboat.
 * scale: 1.0 = width ~90px.
 */
function buildTugboat(scale) {
  const w = Math.round(90  * scale);
  const h = Math.round(50  * scale);
  return `<svg xmlns="http://www.w3.org/2000/svg"
               width="${w}" height="${h}"
               viewBox="0 0 90 50" fill="none">
    <!-- Hull — wider and rounder than container ship -->
    <path d="M 6,26 L 0,36 L 3,44 L 87,44 L 90,36 L 84,26 Z" fill="#0D6B7A"/>
    <line x1="0" y1="36" x2="90" y2="36" stroke="#4DCFDF" stroke-width="1" opacity="0.6"/>
    <!-- Superstructure — single block, tall -->
    <rect x="30" y="12" width="32" height="14" rx="2" fill="#162845"/>
    <rect x="30" y="12" width="32" height="3"  rx="1" fill="#22B5C8" opacity="0.45"/>
    <rect x="34" y="17" width="5"  height="5"  rx="0.5" fill="#4DCFDF" opacity="0.6"/>
    <rect x="44" y="17" width="5"  height="5"  rx="0.5" fill="#4DCFDF" opacity="0.6"/>
    <!-- Smokestack — prominent -->
    <rect x="48" y="4"  width="10" height="12" rx="2" fill="#C0392B"/>
    <rect x="48" y="10" width="10" height="3"  fill="#E8A838" opacity="0.85"/>
    <!-- Tow post at stern -->
    <rect x="72" y="22" width="6" height="12" rx="1" fill="#0A4A54"/>
    <!-- Bumpers / fenders (circles on hull side) -->
    <circle cx="15" cy="37" r="4" fill="#0A1628" stroke="#1B9AAA" stroke-width="1" opacity="0.5"/>
    <circle cx="75" cy="37" r="4" fill="#0A1628" stroke="#1B9AAA" stroke-width="1" opacity="0.5"/>
    <!-- Wake -->
    <path d="M 0,45 Q 20,42 40,45" stroke="#4DCFDF" stroke-width="0.7" fill="none" opacity="0.25"/>
  </svg>`;
}
```

#### buildSailboat(scale)

```js
/**
 * Returns an SVG string for a small sailboat.
 * scale: 1.0 = width ~60px, height ~90px (tall due to mast).
 */
function buildSailboat(scale) {
  const w = Math.round(60  * scale);
  const h = Math.round(90  * scale);
  return `<svg xmlns="http://www.w3.org/2000/svg"
               width="${w}" height="${h}"
               viewBox="0 0 60 90" fill="none">
    <!-- Hull -->
    <path d="M 5,62 L 0,72 L 3,78 L 57,78 L 60,72 L 55,62 Z" fill="#0D6B7A"/>
    <line x1="0" y1="72" x2="60" y2="72" stroke="#4DCFDF" stroke-width="1" opacity="0.6"/>
    <!-- Mast — tall, centered -->
    <line x1="30" y1="8" x2="30" y2="62" stroke="#1A3259" stroke-width="2.5"/>
    <!-- Main sail — triangular, large -->
    <path d="M 30,10 L 30,58 L 8,58 Z"
          fill="white" opacity="0.80"/>
    <!-- Jib / foresail — smaller triangle -->
    <path d="M 30,16 L 30,55 L 52,58 Z"
          fill="white" opacity="0.50"/>
    <!-- Boom -->
    <line x1="8" y1="58" x2="52" y2="58" stroke="#1A3259" stroke-width="1.5"/>
    <!-- Wake -->
    <path d="M 0,79 Q 15,76 30,79" stroke="#4DCFDF" stroke-width="0.7" fill="none" opacity="0.25"/>
  </svg>`;
}
```

---

### 3.5 spawnShip()

```js
/**
 * Spawns one ship into #ships-layer and removes it after it crosses the viewport.
 *
 * Ship selection probabilities:
 *   Container ship: 60%
 *   Tugboat:        30%
 *   Sailboat:       10%
 *
 * Ships always travel left (right→left). Very occasionally (10%) they travel
 * right (left→right) — implemented with CSS transform: scaleX(-1) on the ship div.
 *
 * Bottom positioning: ships appear at the horizon line, so their bottom edge
 * sits at 22vh–26vh from the viewport bottom (above the ocean water SVG top).
 * Slight randomization (±2vh) for parallax feel.
 *
 * Scale randomization: 0.5–1.1 for container ships (distant to close),
 *                      0.6–0.9 for tugboats, 0.5–0.8 for sailboats.
 *
 * Speed: 90–180 seconds to cross the viewport. Slower = larger apparent scale.
 *        Speed is inversely correlated with scale (distant ships move slower in px/s).
 */
function spawnShip() {
  const layer = document.getElementById('ships-layer');
  if (!layer) return;

  const rnd = Math.random;

  /* Ship type selection */
  let svgHtml, scale, typeLabel;
  const t = rnd();
  if (t < 0.60) {
    scale    = 0.5 + rnd() * 0.6;
    svgHtml  = buildContainerShip(scale);
    typeLabel = 'container';
  } else if (t < 0.90) {
    scale    = 0.6 + rnd() * 0.3;
    svgHtml  = buildTugboat(scale);
    typeLabel = 'tug';
  } else {
    scale    = 0.5 + rnd() * 0.3;
    svgHtml  = buildSailboat(scale);
    typeLabel = 'sail';
  }

  /* Direction */
  const goRight = rnd() < 0.10;

  /* Vertical position — near horizon, slight randomization */
  const bottomVh = 20 + rnd() * 4; /* 20–24vh from bottom */

  /* Animation duration — correlated with scale: large = faster apparent speed */
  const baseDuration = 90 + (1 - scale) * 90; /* 90s (large) to 180s (tiny) */
  const duration = Math.round(baseDuration + rnd() * 30);

  /* Create element */
  const div = document.createElement('div');
  div.className = 'ocean-ship';
  div.setAttribute('data-ship-type', typeLabel);
  div.style.bottom = bottomVh + 'vh';
  div.style.right  = goRight ? 'auto' : '-320px';
  div.style.left   = goRight ? '-320px' : 'auto';
  if (goRight) div.style.transform = 'scaleX(-1)';
  div.innerHTML = svgHtml;

  /* CSS animation */
  const kf = goRight ? 'ship-cross-left' : 'ship-cross-left';
  div.style.animation = `${kf} ${duration}s linear forwards`;
  if (goRight) {
    /* Override: rightward ships use a mirrored translateX sequence */
    div.style.animation = `ship-cross-right ${duration}s linear forwards`;
  }

  layer.appendChild(div);

  /* Cleanup after animation completes — prevents DOM accumulation */
  div.addEventListener('animationend', () => div.remove(), { once: true });
  /* Safety cleanup in case animationend never fires (tab hidden, etc.) */
  setTimeout(() => { if (div.parentNode) div.remove(); }, (duration + 5) * 1000);
}
```

---

### 3.6 startShipSpawner()

```js
/**
 * Starts the ambient ship spawner.
 *
 * Interval strategy:
 *   - Spawn immediately on init (after 3s delay so page load settles)
 *   - Then every 25–55 seconds (random within range per spawn)
 *   - Maximum 3 ships visible at any time (count active .ocean-ship elements)
 *   - Ships are suppressed when tab is hidden (document.visibilityState)
 *   - Ships are suppressed when prefers-reduced-motion: reduce is set
 *
 * Returns the timeout ID for cleanup.
 */
function startShipSpawner() {
  /* Suppress entirely if reduced motion is set */
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return null;

  let spawnerTimer = null;

  function scheduleNext() {
    const delay = (25 + Math.random() * 30) * 1000; /* 25–55 seconds */
    spawnerTimer = setTimeout(() => {
      if (document.visibilityState === 'visible') {
        const active = document.querySelectorAll('.ocean-ship').length;
        if (active < 3) spawnShip();
      }
      scheduleNext();
    }, delay);
    return spawnerTimer;
  }

  /* Initial spawn after 3 seconds */
  setTimeout(spawnShip, 3000);

  scheduleNext();
  return spawnerTimer; /* caller stores this if they need to stop spawner */
}

/* Ship cross right keyframe — add to CSS: */
/* @keyframes ship-cross-right { from { transform: scaleX(-1) translateX(110vw); }
                                 to   { transform: scaleX(-1) translateX(-320px); } } */
```

---

### 3.7 attachRipple(btn) + attachAllRipples()

```js
/**
 * Attaches a click ripple effect to a single button element.
 * The button must have position:relative and overflow:hidden —
 * attachRipple() adds these styles automatically via classList.
 *
 * On click:
 *   1. Calculate click position relative to button bounds
 *   2. Create .ripple-wave div centered on click point
 *   3. Append to button
 *   4. Remove after animation completes (600ms)
 *
 * Silently skips if prefers-reduced-motion: reduce is set.
 */
function attachRipple(btn) {
  if (!btn || btn._rippleAttached) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  btn.classList.add('btn-ripple');
  btn._rippleAttached = true;

  btn.addEventListener('click', function(e) {
    const rect   = btn.getBoundingClientRect();
    const x      = e.clientX - rect.left - 20;
    const y      = e.clientY - rect.top  - 20;

    const wave   = document.createElement('span');
    wave.className = 'ripple-wave';
    wave.style.left = x + 'px';
    wave.style.top  = y + 'px';

    btn.appendChild(wave);
    wave.addEventListener('animationend', () => wave.remove(), { once: true });
    /* Safety cleanup */
    setTimeout(() => { if (wave.parentNode) wave.remove(); }, 700);
  });
}

/**
 * Attaches ripple to every button and [role="button"] in the document.
 * Call once on DOMContentLoaded.
 * Also wires the MutationObserver for dynamic buttons (see §3.8).
 */
function attachAllRipples() {
  document.querySelectorAll(
    'button, [role="button"], .bulk-filter-btn, .preset-btn, .module-card'
  ).forEach(attachRipple);
}
```

---

### 3.8 MutationObserver for Dynamic Buttons

```js
/**
 * Watches for dynamically injected buttons (bulk result rows, module cards,
 * pipeline nodes) and attaches ripple to them as they are added to the DOM.
 *
 * Observes the entire document body for subtree changes.
 * Filters to only nodes that are buttons or contain buttons.
 */
function initRippleMutationObserver() {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      for (const node of mutation.addedNodes) {
        if (node.nodeType !== 1) continue; /* element nodes only */
        if (node.matches && node.matches('button, [role="button"]')) {
          attachRipple(node);
        }
        /* Also check descendants of the added node */
        if (node.querySelectorAll) {
          node.querySelectorAll('button, [role="button"]').forEach(attachRipple);
        }
      }
    }
  });

  observer.observe(document.body, { childList: true, subtree: true });
  /* Store on window for potential cleanup: window._rippleObserver = observer */
  window._rippleObserver = observer;
}
```

---

### 3.9 initOceanScene() — Complete Initialization Sequence

```js
/**
 * Full initialization sequence for the ocean scene.
 * Call once, after DOM is ready (DOMContentLoaded or load event).
 *
 * Sequence:
 *   1. Build and inject the dock SVG into #horizon-layer
 *   2. Build and inject the ocean water SVG into #ocean-water-layer
 *   3. Size the #stars-canvas to match viewport
 *   4. Get current hour → getPalette(hour) → applyPalette(p) (first paint)
 *   5. startShipSpawner()
 *   6. attachAllRipples()
 *   7. initRippleMutationObserver()
 *   8. Wire resize listener (debounced 200ms) → drawStars(currentPalette.starOpacity)
 *   9. Wire visibilitychange listener → pause/resume spawner when tab hidden
 *  10. Wire the 60-second palette update interval (see §3.10)
 *  11. Apply stat-pill bob CSS via class injection
 *  12. Wire analyze button float animation
 *  13. Wire quick-load emoji injection
 *  14. Wire settings gear spin
 *  15. Wire nav tab icon injection
 */
function initOceanScene() {
  /* Guard: don't double-init */
  if (window._oceanSceneInit) return;
  window._oceanSceneInit = true;

  /* 1. Inject dock SVG */
  const horizonLayer = document.getElementById('horizon-layer');
  if (horizonLayer) horizonLayer.innerHTML = buildDockSVG();

  /* 2. Inject ocean water SVG */
  const waterLayer = document.getElementById('ocean-water-layer');
  if (waterLayer) waterLayer.innerHTML = buildOceanWaterSVG();

  /* 3. Size stars canvas */
  const canvas = document.getElementById('stars-canvas');
  if (canvas) {
    canvas.width  = window.innerWidth;
    canvas.height = window.innerHeight * 0.65;
  }

  /* 4. First palette paint */
  const hour = new Date().getHours();
  const p    = getPalette(hour);
  window._currentPalette = p;
  applyPalette(p);

  /* 5. Start ship spawner */
  window._shipSpawnerTimer = startShipSpawner();

  /* 6 + 7. Ripple system */
  attachAllRipples();
  initRippleMutationObserver();

  /* 8. Resize → redraw stars */
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (canvas) {
        canvas.width  = window.innerWidth;
        canvas.height = window.innerHeight * 0.65;
      }
      drawStars(window._currentPalette.starOpacity);
    }, 200);
  });

  /* 9. Visibility change — pause ships when tab hidden */
  document.addEventListener('visibilitychange', () => {
    const ships = document.querySelectorAll('.ocean-ship');
    ships.forEach(s => {
      s.style.animationPlayState = document.hidden ? 'paused' : 'running';
    });
  });

  /* 10. Palette update interval — see §3.10 */
  startPaletteInterval();

  /* 11. Stat-pill bob — class already defined in CSS; pills have it by default */
  /* No JS needed — CSS handles it. */

  /* 12. Analyze button float — class added here so it doesn't bob before init */
  const analyzeBtn = document.getElementById('analyze-btn');
  if (analyzeBtn) analyzeBtn.classList.add('btn-float-idle');

  /* 13. Quick-load emoji injection — see Phase 4 §4.2 */
  injectQuickLoadEmojis();

  /* 14. Nav tab icon injection — see Phase 4 §4.3 */
  injectNavTabIcons();

  /* 15. Progress ship rider init */
  initProgressShipRider();
}
```

---

### 3.10 60-Second Palette Update Interval

```js
/**
 * Runs every 60 seconds. Re-reads the local hour and updates the palette
 * only if the hour has changed (palette transitions happen at most once per hour).
 *
 * The 60-second interval ensures that if the user loads the page at 17:59,
 * the transition to the dusk palette happens within 1 minute.
 *
 * Palette transition duration: 8 seconds (set on sky-layer and celestial-body
 * via CSS transition: background 8s ease).
 */
function startPaletteInterval() {
  let lastHour = new Date().getHours();

  setInterval(() => {
    const hour = new Date().getHours();
    if (hour !== lastHour) {
      lastHour = hour;
      const p = getPalette(hour);
      window._currentPalette = p;
      applyPalette(p);
    }
  }, 60 * 1000);
}
```

---

## PHASE 4 — Button & Navigation Enhancements

### 4.1 Every Button That Needs Ripple

**Primary CTAs (already spring-animated; add ripple):**
- `#analyze-btn`
- `#bulk-submit-btn`
- `#download-report-btn`
- `#single-share-btn`

**Auth buttons:**
- `#login-btn`
- `#register-btn`

**Navigation:**
- `.section-nav-btn` (3 buttons: Analyze, Dashboard, Bulk Upload)

**Analyze panel:**
- `.quick-btn` (3 buttons: Clean / Suspicious / Incomplete)
- `#tab-add-btn`
- `.tab-close` (dynamic — picked up by MutationObserver)
- `#feedback-fraud-btn`
- `#feedback-clear-btn`
- `.ph-reset-btn`
- `.ph-load-btn`
- `.rej-try-again`
- `#modal-confirm-btn`
- `.modal-cancel`
- `#settings-gear-btn`
- `#logout-btn`

**Settings drawer:**
- `.preset-btn` (5 buttons)
- `.drawer-close-btn`
- `.drawer-layer-header` (collapsible layers — treat as button)

**Dashboard:**
- `.dash-refresh-btn`
- `.dash-empty-cta`
- `.dash-module-banner-link`

**Bulk panel:**
- `#bulk-add-slot-btn`
- `#bulk-cancel-btn`
- `.bulk-new-batch-btn`
- `.bulk-filter-btn` (7 buttons)
- `.bulk-export-btn` (3 buttons)
- `#bulk-share-btn`
- `.bulk-modal-cancel`
- `.bulk-modal-confirm`
- `.bulk-template-btn`
- `.bulk-slt-remove` (dynamic — MutationObserver)
- `.bulk-slt-upload-btn` (dynamic — MutationObserver)
- `.bulk-action-btn` (dynamic result row buttons — MutationObserver)

**Chain-link share buttons in bulk results:**
- `.bulk-chain-btn` (dynamic — MutationObserver)

---

### 4.2 Analyze Button — Float + Anchor Emoji + Spring

```js
/**
 * The analyze button gets three enhancements:
 *
 * 1. Float idle animation (CSS btn-float, 3s cycle) — stops on hover
 * 2. A small anchor emoji prepended to the button label, animated with the float
 * 3. On click: spring bounce — CSS handles via :active state
 *
 * The anchor emoji is injected by injectQuickLoadEmojis() (called from initOceanScene).
 * It is wrapped in a <span class="btn-anchor-emoji"> so it can be
 * independently styled (slightly larger, color-tinted).
 */
function injectAnalyzeBtnEmoji() {
  const btn = document.getElementById('analyze-btn');
  if (!btn || btn.querySelector('.btn-anchor-emoji')) return;
  const emojiSpan = document.createElement('span');
  emojiSpan.className = 'btn-anchor-emoji';
  emojiSpan.textContent = '⚓';
  emojiSpan.style.cssText = 'font-size:.9em; margin-right:.4em; display:inline-block;';
  /* Insert before the existing SVG icon */
  btn.insertBefore(emojiSpan, btn.firstChild);
}
```

**CSS for float class (in Phase 1 keyframes section):**
```css
.btn-float-idle:not(:disabled) {
  animation: btn-float 3s ease-in-out infinite;
}
.btn-float-idle:not(:disabled):hover,
.btn-float-idle:not(:disabled):focus {
  animation: none;
}
```

---

### 4.3 Quick-Load Buttons — Emoji Icons + Bounce

```js
/**
 * Injects maritime/cargo emoji into the quick-load scenario buttons.
 * Wraps the emoji in .quick-emoji for CSS bounce targeting.
 *
 * Clean Shipment  →  🚢  (container ship)
 * Suspicious      →  🚨  (alert light — implies inspection)
 * Incomplete      →  📋  (clipboard — implies missing docs)
 */
function injectQuickLoadEmojis() {
  const map = {
    'clean':      '🚢',
    'suspicious': '🚨',
    'incomplete': '📋',
  };
  document.querySelectorAll('.quick-btn').forEach(btn => {
    const scenario = btn.getAttribute('onclick')?.match(/loadScenario\('(\w+)'\)/)?.[1];
    if (!scenario || !map[scenario]) return;
    if (btn.querySelector('.quick-emoji')) return;
    const emojiSpan = document.createElement('span');
    emojiSpan.className = 'quick-emoji';
    emojiSpan.textContent = map[scenario] + ' ';
    emojiSpan.style.cssText = 'font-style:normal; display:inline-block;';
    btn.insertBefore(emojiSpan, btn.firstChild);
  });
  injectAnalyzeBtnEmoji();
}
```

---

### 4.4 Nav Tabs — Maritime Icons

```js
/**
 * Replaces the existing SVG icons in the section nav buttons with
 * maritime-themed alternatives:
 *
 * Analyze  →  ⚓  anchor (customs = anchoring trade)
 * Dashboard → 🧭  compass (navigation = analytics)
 * Bulk Upload → 📦  container-stack (bulk = cargo)
 *
 * The emoji replacements are injected as aria-hidden spans alongside
 * the existing SVG (which is hidden via CSS), not replacing it entirely,
 * so the semantic SVG stays in the DOM for screen readers.
 */
function injectNavTabIcons() {
  const navMap = [
    { id: 'nav-analyze',   emoji: '⚓' },
    { id: 'nav-dashboard', emoji: '🧭' },
    { id: 'nav-bulk',      emoji: '📦' },
  ];
  navMap.forEach(({ id, emoji }) => {
    const btn = document.getElementById(id);
    if (!btn || btn.querySelector('.nav-emoji')) return;
    /* Hide existing SVG */
    const svg = btn.querySelector('svg');
    if (svg) svg.style.display = 'none';
    const emojiSpan = document.createElement('span');
    emojiSpan.className = 'nav-emoji';
    emojiSpan.setAttribute('aria-hidden', 'true');
    emojiSpan.textContent = emoji;
    emojiSpan.style.cssText = 'font-size:1em; margin-right:.35em;';
    btn.insertBefore(emojiSpan, btn.firstChild);
  });
}
```

---

### 4.5 Settings Gear — Spin on Hover

Handled entirely by CSS in Phase 1.6. No JS needed. The existing SVG inside `.settings-gear-btn` gets the `gear-spin` animation on parent hover.

---

### 4.6 Stat Pills — Staggered Bob

Handled entirely by CSS in Phase 1.6. No JS needed.

---

### 4.7 Preset Buttons — Lift + Bounce

Handled entirely by CSS in Phase 1.6. No JS needed.

---

### 4.8 Module Cards — Slide-Right Hover

Handled entirely by CSS in Phase 1.6. No JS needed.

---

## PHASE 5 — Bulk Upload Maritime Polish

### 5.1 Drop Zone Hover Effects

Add to the `.bulk-drop-zone` hover and drag-over states:
```css
.bulk-drop-zone:hover,
.bulk-drop-zone.drag-over {
  border-color: var(--teal-400);
  background: rgba(27,154,170,.07);
  box-shadow: 0 0 0 3px rgba(27,154,170,.12), var(--glow-teal-sm);
  transform: scale(1.015);
  transition: transform 200ms var(--ease-spring),
              border-color 150ms ease,
              box-shadow 150ms ease,
              background 150ms ease;
}
```

---

### 5.2 Cargo Ship SVG for ZIP Drop Zone

Replace the generic archive SVG inside `#bulk-card-zip .bulk-drop-icon` with:

```html
<!-- Cargo ship silhouette — 40×28px, teal palette -->
<svg width="40" height="28" viewBox="0 0 40 28" fill="none">
  <path d="M 2,14 L 0,18 L 1,24 L 39,24 L 40,18 L 38,14 Z" fill="#1B9AAA" opacity="0.7"/>
  <line x1="0" y1="18" x2="40" y2="18" stroke="#4DCFDF" stroke-width="0.8" opacity="0.6"/>
  <rect x="5"  y="8"  width="8" height="6" rx="0.5" fill="#C0392B" opacity="0.65"/>
  <rect x="15" y="8"  width="8" height="6" rx="0.5" fill="#E8A838" opacity="0.65"/>
  <rect x="25" y="8"  width="8" height="6" rx="0.5" fill="#1B9AAA" opacity="0.65"/>
  <rect x="7"  y="4"  width="6" height="4" rx="0.5" fill="#E8A838" opacity="0.55"/>
  <rect x="17" y="4"  width="6" height="4" rx="0.5" fill="#C0392B" opacity="0.55"/>
  <rect x="28" y="6"  width="7" height="8" rx="1"   fill="#162845" opacity="0.8"/>
  <rect x="30" y="4"  width="3" height="4" rx="0.5" fill="#C0392B" opacity="0.7"/>
</svg>
```

The drop zone label changes to: **"Drop shipment bundle here"** → stays the same. Sub-label: **"Each subfolder = one shipment"** → stays the same.

---

### 5.3 Anchor/Manifest SVG for CSV Drop Zone

Replace the generic CSV file SVG inside `#bulk-card-csv .bulk-drop-icon` with:

```html
<!-- Anchor + manifest — 36×36px, teal palette -->
<svg width="36" height="36" viewBox="0 0 36 36" fill="none">
  <!-- Anchor -->
  <circle cx="18" cy="9" r="4" stroke="#1B9AAA" stroke-width="1.5" fill="none" opacity="0.7"/>
  <line x1="18" y1="13" x2="18" y2="30" stroke="#1B9AAA" stroke-width="1.5" opacity="0.7"/>
  <path d="M 8,20 Q 6,28 18,30 Q 30,28 28,20" stroke="#1B9AAA" stroke-width="1.5" fill="none" opacity="0.7"/>
  <line x1="12" y1="20" x2="8" y2="20" stroke="#1B9AAA" stroke-width="1.5" opacity="0.5"/>
  <line x1="24" y1="20" x2="28" y2="20" stroke="#1B9AAA" stroke-width="1.5" opacity="0.5"/>
  <!-- Small document lines overlay (CSV manifest reference) -->
  <line x1="24" y1="6"  x2="30" y2="6"  stroke="#4DCFDF" stroke-width="1" opacity="0.45"/>
  <line x1="24" y1="9"  x2="32" y2="9"  stroke="#4DCFDF" stroke-width="1" opacity="0.35"/>
  <line x1="24" y1="12" x2="28" y2="12" stroke="#4DCFDF" stroke-width="1" opacity="0.25"/>
</svg>
```

---

### 5.4 Bulk Empty State — Animated Ship Illustration

Replace the generic cargo-box SVG inside `.bulk-empty-art` with:

```html
<!-- Animated harbor scene for bulk empty state -->
<div class="bulk-empty-harbor" style="position:relative; width:80px; height:60px; margin:0 auto 1rem;">
  <!-- Water surface lines -->
  <svg width="80" height="60" viewBox="0 0 80 60" fill="none" style="position:absolute;inset:0">
    <path d="M 0,45 Q 20,42 40,45 Q 60,48 80,45" stroke="rgba(27,154,170,.3)" stroke-width="1.5" fill="none"/>
    <path d="M 0,50 Q 25,47 50,50 Q 65,53 80,50" stroke="rgba(27,154,170,.2)" stroke-width="1" fill="none"/>
  </svg>
  <!-- Mini container ship — bobs gently -->
  <div style="position:absolute; bottom:10px; left:50%; transform:translateX(-50%);
              animation:stat-bob 3s ease-in-out infinite;">
    <svg width="54" height="22" viewBox="0 0 54 22" fill="none">
      <path d="M 2,10 L 0,16 L 2,20 L 52,20 L 54,16 L 52,10 Z" fill="#1B9AAA" opacity="0.6"/>
      <line x1="0" y1="16" x2="54" y2="16" stroke="#4DCFDF" stroke-width="0.8" opacity="0.55"/>
      <rect x="5"  y="5"  width="9" height="5" rx="0.5" fill="#C0392B" opacity="0.55"/>
      <rect x="16" y="5"  width="9" height="5" rx="0.5" fill="#E8A838" opacity="0.55"/>
      <rect x="27" y="5"  width="9" height="5" rx="0.5" fill="#1B9AAA" opacity="0.55"/>
      <rect x="37" y="7"  width="10" height="8" rx="1" fill="#162845" opacity="0.7"/>
      <rect x="39" y="4"  width="3"  height="5" rx="0.5" fill="#C0392B" opacity="0.6"/>
    </svg>
  </div>
</div>
```

---

### 5.5 Progress Bar Ship Emoji Rider

The progress ship rider sits on top of `#bulk-progress-fill` and moves with the progress value.

**CSS:**
```css
.bulk-progress-bar {
  position: relative; /* ensure children position against this */
}

.bulk-progress-ship {
  position: absolute;
  top: -14px;
  /* left is set by JS: (progressPct%) - 2px */
  font-size: 1.1rem;
  line-height: 1;
  pointer-events: none;
  transform: translateX(-50%);
  animation: ship-rider-bounce 1.5s ease-in-out infinite;
  transition: left 400ms ease-out;
  z-index: 2;
  user-select: none;
}
```

**HTML injection (call from `bulkSetProgress()` or equivalent):**
```js
/**
 * initProgressShipRider()
 * Injects a ship emoji above the progress bar fill.
 * Called from initOceanScene(); updates are triggered by bulkUpdateProgress().
 */
function initProgressShipRider() {
  const bar = document.querySelector('.bulk-progress-bar');
  if (!bar || bar.querySelector('.bulk-progress-ship')) return;
  const ship = document.createElement('span');
  ship.className = 'bulk-progress-ship';
  ship.setAttribute('aria-hidden', 'true');
  ship.textContent = '🚢';
  bar.appendChild(ship);
}

/**
 * updateProgressShipRider(pct)
 * Called with a 0–100 value whenever bulk progress updates.
 * Clamps to 2–98 so the ship doesn't fall off either edge.
 */
function updateProgressShipRider(pct) {
  const ship = document.querySelector('.bulk-progress-ship');
  if (!ship) return;
  const clamped = Math.max(2, Math.min(98, pct));
  ship.style.left = clamped + '%';
}
```

**Integration point:** Find all calls to `bulkUpdateProgress()` or wherever `#bulk-progress-fill` width is set and add `updateProgressShipRider(pct)` there.

---

### 5.6 Bulk Progress Screen Title

Change the bulk progress title from the static text `"Processing Batch…"` to a rotating set of maritime phrases, cycling every 5 seconds:

```js
const _BULK_PROGRESS_TITLES = [
  'Processing Batch…',
  'Loading Cargo… 🚢',
  'Inspecting Manifests…',
  'Clearing Customs… ⚓',
  'Running Compliance Checks…',
  'Almost at Port…',
];

function startBulkProgressTitleCycle() {
  let idx = 0;
  const el = document.querySelector('.bulk-progress-title');
  if (!el) return;
  /* Don't cycle if reduced motion */
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  const timer = setInterval(() => {
    idx = (idx + 1) % _BULK_PROGRESS_TITLES.length;
    el.style.opacity = '0';
    setTimeout(() => {
      el.textContent = _BULK_PROGRESS_TITLES[idx];
      el.style.opacity = '1';
    }, 250);
  }, 5000);

  /* Store for cleanup when bulk results appear */
  window._bulkTitleTimer = timer;
}

function stopBulkProgressTitleCycle() {
  clearInterval(window._bulkTitleTimer);
  const el = document.querySelector('.bulk-progress-title');
  if (el) { el.textContent = 'Processing Batch…'; el.style.opacity = '1'; }
}
```

**Integration:** Call `startBulkProgressTitleCycle()` when the progress screen appears. Call `stopBulkProgressTitleCycle()` when results appear or cancel is clicked. Add `transition: opacity 250ms ease` to `.bulk-progress-title` in CSS.

---

## PHASE 6 — Auth & Dashboard Polish

### 6.1 Auth Overlay — Transparent Background

The current `#auth-overlay` has an opaque navy background that hides the ocean scene entirely. Replace it with a semi-transparent frosted glass background so the ocean scene is visible behind the login card.

**CSS change** (replaces current `#auth-overlay { background: ... }`):**
```css
#auth-overlay {
  /* replace opaque background with translucent blur */
  background: rgba(5, 12, 26, 0.78);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
}
```

The `#auth-overlay` must remain `z-index: 9000` and `position: fixed; inset: 0`.

---

### 6.2 Auth Card — Frosted Glass + Ship Watermark

**CSS changes to `.auth-card`:**
```css
.auth-card {
  background: rgba(10, 22, 40, 0.82) !important;
  border: 1px solid rgba(77, 207, 223, 0.14) !important;
  backdrop-filter: blur(20px) !important;
  -webkit-backdrop-filter: blur(20px) !important;
  box-shadow:
    0 24px 80px rgba(0,0,0,.6),
    0 8px 24px rgba(0,0,0,.3),
    0 1px 0 rgba(77,207,223,.08) inset !important;
  position: relative;
  overflow: hidden;
}
```

**Ship watermark pseudo-element:**
```css
.auth-card::after {
  content: '';
  position: absolute;
  bottom: -30px;
  right: -30px;
  width: 180px;
  height: 110px;
  opacity: 0.035;
  pointer-events: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 70' fill='none'%3E%3Cpath d='M8,30 L0,42 L4,60 L196,60 L200,42 L192,30Z' fill='%234DCFDF'/%3E%3Crect x='20' y='14' width='28' height='16' rx='1' fill='%234DCFDF'/%3E%3Crect x='54' y='14' width='28' height='16' rx='1' fill='%234DCFDF'/%3E%3Crect x='88' y='14' width='28' height='16' rx='1' fill='%234DCFDF'/%3E%3Crect x='140' y='10' width='44' height='20' rx='2' fill='%234DCFDF'/%3E%3C/svg%3E");
  background-size: contain;
  background-repeat: no-repeat;
  background-position: bottom right;
}
```

---

### 6.3 Dashboard Empty State — Animated Ship Illustration

Replace the `&#128202;` emoji icon in `.dash-empty-icon` with a harbor illustration matching the bulk empty state pattern (Phase 5.4). Use the same `buildContainerShip(0.8)` SVG wrapped in a bobbing container.

Additionally, change the empty state text:
- Title: **"No shipments analyzed yet"** → **"Harbor is quiet — no shipments yet"** 
- Sub: Keep existing text, prepend: **"Screen your first shipment to watch the analytics fill in."**

---

### 6.4 Decision Banners — Frosted Glass + Enhanced Glow

The existing decision banners (`.decision-banner`) already have colored backgrounds and borders. Apply frosted glass with the palette-appropriate glow:

```css
.decision-banner {
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  /* existing border and background remain; backdrop-filter adds depth */
}

/* Enhanced glow — the banner-alert keyframe already fires; add persistent steady glow */
.decision-banner.REJECT {
  box-shadow:
    var(--shadow-card),
    0 0 28px rgba(224,80,80,.18);
}
.decision-banner.FLAG_FOR_INSPECTION {
  box-shadow:
    var(--shadow-card),
    0 0 24px rgba(212,119,58,.15);
}
.decision-banner.REVIEW_RECOMMENDED {
  box-shadow:
    var(--shadow-card),
    0 0 22px rgba(155,108,214,.15);
}
.decision-banner.APPROVE,
.decision-banner.CLEAR {
  box-shadow:
    var(--shadow-card),
    0 0 20px rgba(29,184,122,.12);
}
```

**Note:** The banner CSS class must be set dynamically. The JS `renderResults()` function already sets `id="decision-banner"` with class based on decision value. Confirm the decision string matches the CSS class (e.g., `APPROVE`, `FLAG_FOR_INSPECTION`) — they do per the current code.

---

## PHASE 7 — Testing Checklist

### 7.1 Visual Regression Tests

For each of the 7 time-of-day palettes, visually verify:

- [ ] **Night (23:00 simulated):** Sky is near-black (`#010814`). Moon visible at ~55% top, 36px circle. Stars visible at 90% opacity. Ocean is very dark. Dock is deep silhouette. Ships appear as dark shapes. Auth overlay is frosted with ocean scene visible behind it.

- [ ] **Dawn (05:00 simulated):** Purple-violet sky. Rust horizon glow. Stars at 25% (fading). Moon replaced by red-orange sun near left horizon. Warm foam tint. Cloud opacity 10%.

- [ ] **Sunrise (07:00 simulated):** Indigo top, coral mid, orange horizon gradient. Large gold sun low-left. Stars gone. Pink-tinted clouds visible. Water has orange reflection.

- [ ] **Morning (09:00 simulated):** Clear blue sky. Bright white-gold sun at ~35% height. White daytime clouds. Bright harbor. No stars.

- [ ] **Day (13:00 simulated):** Vivid noon blue. Small near-white sun overhead (near top). Full cloud coverage at 65% opacity. Brightest harbor.

- [ ] **Afternoon (17:00 simulated):** Deepening blue, warm amber horizon. Gold sun lowering right. Warming water color.

- [ ] **Dusk (19:00 simulated):** Deep violet top, coral-red mid, burning orange horizon. Large red sun right-low. Early stars (30%). Backlit silhouette clouds. Darkest harbor.

---

### 7.2 Animation Performance — No Jank

- [ ] Open Chrome DevTools Performance tab. Record 5 seconds while ocean scene is active. Verify: **no frames below 55fps** on a 2020-era laptop. All ship and cloud animations must show only `composite` operations in the frame timeline (no layout or paint recalculations).
- [ ] Verify `will-change: transform` is set on: all wave SVGs, all `.cloud-puff` elements, all `.ocean-ship` elements, `#celestial-body`.
- [ ] Verify `#ships-layer` has no more than 3 `.ocean-ship` children at any time by watching the DOM while ships spawn.
- [ ] Open DevTools Memory tab. Take heap snapshot before and after 5 minutes of ship spawning. Verify heap does not grow unboundedly (ships are removed from DOM after crossing).

---

### 7.3 Time-of-Day Palette Verification

- [ ] Manually set `new Date().getHours()` mock or change system clock to each of the 7 hour ranges. Confirm correct palette is returned by `getPalette()` for boundary hours: 3, 4, 5, 6, 7, 8, 11, 12, 15, 16, 17, 18, 21, 22.
- [ ] Load page at 11:59 PM (hour=23) → verify night palette.
- [ ] Load page at 12:00 AM (hour=0) → verify night palette.
- [ ] Simulate hour change during session: call `applyPalette(getPalette(18))` from console while page is at day palette. Verify 8-second gradient transition plays on `#sky-layer` and `#celestial-body`.

---

### 7.4 Ship Spawner Memory Leak Check

- [ ] Let the page idle for 10 minutes with DevTools open.
- [ ] Count `.ocean-ship` elements in DOM at intervals. Maximum should be 3. Should regularly drop to 0–1.
- [ ] Confirm `animationend` fires for each ship (log in handler temporarily): every spawned ship must fire its cleanup.
- [ ] Confirm the `setTimeout` safety cleanup at `(duration + 5) * 1000` fires correctly for ships spawned while tab is hidden (animation may not run while tab is backgrounded).
- [ ] Heap snapshot: compare before and after 20 ships have crossed. No reference count growth for ship-related objects.

---

### 7.5 Ripple Cleanup Check

- [ ] Rapidly click any ripple-enabled button 20 times in quick succession. Open DevTools Elements panel. Verify no `.ripple-wave` elements remain in the DOM after all animations complete (max 700ms after last click).
- [ ] Verify `btn._rippleAttached = true` prevents duplicate event listeners on buttons that are rendered multiple times (e.g., module cards that are re-injected when drawer opens).
- [ ] Click a `.bulk-action-btn` in a results row (injected dynamically). Verify ripple fires — confirms MutationObserver picked it up.

---

### 7.6 Reduced Motion Compliance

Test with `@media (prefers-reduced-motion: reduce)` active (enable in OS accessibility settings or override in DevTools):

- [ ] `#ocean-scene` is `display: none` — ocean scene completely hidden.
- [ ] Stat pills have no animation.
- [ ] Analyze button has no float animation.
- [ ] Ripple clicks produce no `.ripple-wave` elements (JS guard fires).
- [ ] Quick-load buttons have no bounce on hover.
- [ ] Ship rider on progress bar has no bounce.
- [ ] Spring lift transforms are suppressed on all secondary buttons.
- [ ] Wave SVGs in `#wave-bg` (if any remain) have no animation.
- [ ] Bulk progress title does not cycle.
- [ ] All existing functionality works normally — decisions still render, forms still submit, results still display.

---

### 7.7 Mobile Viewport Check

Test at viewport widths: 375px (iPhone SE), 390px (iPhone 14), 768px (iPad portrait).

- [ ] `#ocean-scene` is hidden at ≤640px via `@media (max-width: 640px) { #ocean-scene { display: none; } }`. Verify page layout is correct without it.
- [ ] `body::before` radial glow is visible on mobile (it should not be hidden).
- [ ] Auth card frosted glass still renders correctly without backdrop-filter if unsupported (fallback: `background: var(--bg-surface)`).
- [ ] Ripple effects work on touch events (iOS Safari). Verify `click` event fires on tap — it does on iOS, but confirm ripple wave appears at tap point.
- [ ] Bulk progress ship rider does not overflow the progress bar container on narrow screens.
- [ ] `.section-nav-btn` with emoji icons doesn't wrap or truncate unexpectedly at 375px.
- [ ] Settings drawer slides in correctly — the gear button is still accessible.

---

### 7.8 All Existing Functionality Preserved

Full regression verification that ocean theme changes have not broken any working feature:

- [ ] **Login / Register:** Auth forms submit. Success redirects to Analyze tab. Error codes display correctly.
- [ ] **Single document analysis:** Paste text → Analyze → results render (decision banner, gauge, findings, next steps).
- [ ] **PDF upload:** File chosen → PDF preview renders → beam animation plays → analysis submits.
- [ ] **Multi-document tabs:** Add tab → remove tab → switch tabs → all working.
- [ ] **Quick load scenarios:** All 3 buttons populate docs and trigger analysis correctly.
- [ ] **Pattern learning:** Pattern section appears after analysis. Feedback buttons work. Reset modal works.
- [ ] **Settings drawer:** Opens/closes. Preset buttons apply module selections. Toggles save. Active count updates.
- [ ] **Dashboard:** Loads KPIs and charts. Refresh button works. Module performance section collapses/expands.
- [ ] **Bulk upload — ZIP:** Drop zone accepts .zip. Upload section shows file name.
- [ ] **Bulk upload — CSV:** Drop zone accepts .csv. Template download works.
- [ ] **Bulk upload — Manual:** Add slot → remove slot → PDF upload per slot → submit.
- [ ] **Bulk submit:** All 50 rows process. No pending. Sustainability badges display. Share links copy correctly.
- [ ] **Bulk export:** CSV export produces correct columns including sustainability_grade, sustainability_signals, active_modules.
- [ ] **Deep links:** `/#result/{id}` and `?batch={id}` routing works through login.
- [ ] **PDF compliance report download:** Generates and downloads correctly.
- [ ] **Logout:** Clears auth state. Shows login overlay.
- [ ] **107/107 tests pass:** Run `pytest tests/` and confirm no regressions.

---

## Implementation Order

Work must proceed in this exact dependency order:

1. **Phase 1 first** — CSS foundation must be in place before any visual work. `:root` expansion, new keyframes, ocean layer CSS, frosted glass, reduced motion block. No visual difference until Phase 2 HTML is added.

2. **Phase 2 second** — Add ocean scene HTML. The `#wave-bg` replacement. Dock SVG. Ocean water SVG. Cloud puffs. This makes the scene visible for the first time.

3. **Phase 3 third** — JavaScript engine. `PALETTES` constant, `getPalette`, `applyPalette`, `drawStars`, ship builders, `spawnShip`, `startShipSpawner`, ripple functions, `initOceanScene`. Call `initOceanScene()` from `DOMContentLoaded`.

4. **Phase 4 fourth** — Button and nav enhancements. These depend on Phase 3 JS (`attachRipple`, `injectQuickLoadEmojis`, `injectNavTabIcons`).

5. **Phase 5 fifth** — Bulk upload polish. Drop zone CSS can go in Phase 1. Ship SVG replacements and progress bar ship rider require Phase 2 SVG patterns as reference.

6. **Phase 6 sixth** — Auth and dashboard polish. Auth overlay transparency requires Phase 2 ocean scene to be visible behind it. Decision banner frosted glass is purely CSS, can be done in Phase 1.

7. **Phase 7 last** — Testing. Run after all code is written.

---

## Notes and Constraints

- **No backend changes.** All work in `demo.html` only.
- **Preserve all existing JS function names and signatures.** `analyze()`, `loadScenario()`, `bulkSubmitClick()`, `loadDashboard()`, `openSettingsDrawer()`, etc. must not be renamed or have their signatures changed.
- **No new files.** Everything goes into `demo.html`. The file will grow by approximately 400–600 lines.
- **CSS variable updates only.** `applyPalette()` must only set CSS custom properties and inline styles on ocean scene elements. It must never touch `.card`, `.header`, or any component CSS.
- **Frosted glass `backdrop-filter` fallback.** Any element with `backdrop-filter` must also have a `background` fallback with sufficient opacity for browsers that don't support `backdrop-filter` (Firefox ESR, older Safari). The fallback is `var(--bg-surface)` at opacity ≥ 0.85.
- **`#wave-bg` removal is complete.** Do not keep any HTML or CSS from the old `#wave-bg`. The ocean scene replaces it entirely at the same z-index.
- **Dock SVG uses the brand palette, not independent colors.** All container colors in the dock reference the `--coral`, `--amber-500`, `--teal-500` tokens at low opacity so they respond to the palette without explicit JS wiring.

---

*End of ocean theme build plan. Implementation begins in the next sprint.*
