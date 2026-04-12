# PortGuard — UI Design System v1.0

**Purpose:** Complete visual language and component specification for the PortGuard UI overhaul.
**Scope:** All surfaces in `demo.html` — auth overlay, header, analyze panel, results, dashboard.
**Philosophy:** Professional but alive. This is serious government and enterprise software that still feels modern and energetic. Every design decision earns its place.

---

## Table of Contents

1. [Color System](#1-color-system)
2. [Logo & Brand Mark](#2-logo--brand-mark)
3. [Typography](#3-typography)
4. [Spacing & Layout](#4-spacing--layout)
5. [Animation Principles](#5-animation-principles)
6. [Component Library](#6-component-library)
7. [Background Treatment](#7-background-treatment)
8. [Page-Level Composition](#8-page-level-composition)
9. [Implementation Notes](#9-implementation-notes)

---

## 1. Color System

### Conceptual Inspiration

The palette is drawn from three visual references:
- **Ocean depth** — the rich teal-turquoise of deep harbor water under sunlight
- **Shipping containers** — the bold coral reds, burnt oranges, and signal yellows stacked on cargo ships
- **Navigation screens** — dark navy backgrounds with glowing indicator points, like a port authority radar display

The result is a palette that reads instantly as maritime commerce while feeling modern and data-forward.

---

### Core Tokens

```
/* === BACKGROUNDS === */
--bg-deep:     #0A1628   /* Page canvas — deepest navy, like midnight harbor water */
--bg-surface:  #0F2040   /* Card backgrounds — slightly elevated from page */
--bg-card:     #162845   /* Inner surfaces, inputs, code blocks */
--bg-card-hi:  #1A3259   /* Hover state elevations */

/* === TEAL — PRIMARY BRAND COLOR === */
--teal-900:    #072B30   /* Deep teal for subtle tints */
--teal-800:    #0A4A54   /* Dark teal, used in section backgrounds */
--teal-700:    #0D6B7A   /* Mid teal */
--teal-500:    #1B9AAA   /* Primary teal — the ocean reference color */
--teal-400:    #22B5C8   /* Active states, hover on teal elements */
--teal-300:    #4DCFDF   /* Focus rings, glow halos */
--teal-100:    #B8EEF4   /* Light teal text on dark */

/* === CORAL / CONTAINER RED — SECONDARY ACTION COLOR === */
--coral-700:   #8B1A1A   /* Deep red, danger backgrounds */
--coral-600:   #B02020   /* Destructive button hover */
--coral-500:   #C0392B   /* Container red — the primary secondary color */
--coral-400:   #D94F42   /* Hover state */
--coral-300:   #E87D74   /* Lighter coral for labels */
--coral-100:   #F9D4D0   /* Very light coral tint */

/* === AMBER / SUNSET GOLD — ACCENT / WARNING === */
--amber-700:   #7A4F0A   /* Deep amber tints */
--amber-500:   #E8A838   /* Sunset gold — highlight accent */
--amber-400:   #F0BE5A   /* Lighter amber */
--amber-300:   #F5D07A   /* Label text, warnings */

/* === SEMANTIC STATUS COLORS === */
--success:     #1DB87A   /* Green — approve / cleared */
--success-dim: #0F6B47   /* Success background tint */
--success-glow:rgba(29,184,122,.25)

--warning:     #E8A838   /* Amber — review recommended / caution */
--warning-dim: #5C3D08   /* Warning background tint */

--danger:      #E05050   /* Red — reject / confirmed fraud */
--danger-dim:  #5A1515   /* Danger background tint */
--danger-glow: rgba(224,80,80,.25)

--info:        #1B9AAA   /* Teal — general information */
--info-dim:    #072B30   /* Info background tint */

--flag:        #D4773A   /* Orange — flag for inspection */
--flag-dim:    #5C2D0A   /* Flag background tint */

--purple:      #9B6CD6   /* Purple — review recommended */
--purple-dim:  #3A1F6E   /* Purple background tint */

/* === BORDERS === */
--border:      #1A3259   /* Default border — subtle, same family as bg-card */
--border-hi:   #254778   /* Elevated border — hover, focused elements */
--border-teal: rgba(27,154,170,.3)   /* Teal-tinted border for featured cards */
--border-teal-hi: rgba(27,154,170,.6) /* Teal border on hover */

/* === TEXT === */
--text-primary:  #EDF2F8  /* Main content — crisp warm white */
--text-secondary:#8BAABF  /* Supporting text — soft blue-gray */
--text-muted:    #4A6880  /* Tertiary text, labels, placeholders */
--text-faint:    #2C4560  /* Barely-there text, separators */
--text-teal:     #4DCFDF  /* Teal-tinted text for interactive elements */
--text-coral:    #E87D74  /* Coral-tinted text for alerts */

/* === OVERLAYS === */
--overlay-dark:  rgba(10,22,40,.85)  /* Modal backdrops */
--overlay-card:  rgba(15,32,64,.92)  /* Card overlays */
```

---

### Color Usage Guide

| Element | Background | Text | Border |
|---|---|---|---|
| Page | `--bg-deep` | — | — |
| Standard card | `--bg-surface` | `--text-primary` | `--border` |
| Featured/active card | `--bg-surface` | `--text-primary` | `--border-teal` |
| Input field | `--bg-card` | `--text-primary` | `--border` |
| Input focused | `--bg-card` | `--text-primary` | `--teal-300` |
| Primary button | `--teal-500→--teal-700` gradient | white | none |
| Destructive button | `--coral-500→--coral-700` gradient | white | none |
| Ghost button | transparent | `--text-secondary` | `--border` |
| APPROVE banner | `--success-dim` | `--success` | `rgba(29,184,122,.35)` |
| FLAG banner | `--flag-dim` | `--flag` | `rgba(212,119,58,.4)` |
| REVIEW banner | `--purple-dim` | `--purple` | `rgba(155,108,214,.35)` |
| REQUEST INFO banner | `--warning-dim` | `--warning` | `rgba(232,168,56,.4)` |
| REJECT banner | `--danger-dim` | `--danger` | `rgba(224,80,80,.45)` |
| Code / data | `--bg-card` | `--text-teal` | — |

---

### Glow System

Glow effects are used sparingly to direct attention. They are always `box-shadow` values, never `filter: glow()`.

```
/* Teal glow — used on primary interactive elements and the logo */
--glow-teal-sm:  0 0 10px rgba(27,154,170,.25);
--glow-teal-md:  0 0 20px rgba(27,154,170,.35), 0 0 6px rgba(27,154,170,.2);
--glow-teal-lg:  0 0 32px rgba(27,154,170,.45), 0 0 12px rgba(27,154,170,.25);

/* Danger glow — used on REJECT decision banner */
--glow-danger-sm: 0 0 10px rgba(224,80,80,.2);
--glow-danger-md: 0 0 20px rgba(224,80,80,.3);

/* Success glow — used on APPROVE decision banner */
--glow-success-sm: 0 0 10px rgba(29,184,122,.2);
--glow-success-md: 0 0 22px rgba(29,184,122,.3);

/* Elevation shadows — used on all raised cards */
--shadow-card: 0 4px 24px rgba(0,0,0,.35), 0 1px 4px rgba(0,0,0,.2);
--shadow-card-hover: 0 8px 36px rgba(0,0,0,.45), 0 2px 8px rgba(0,0,0,.25);
--shadow-modal: 0 24px 80px rgba(0,0,0,.6), 0 8px 24px rgba(0,0,0,.3);
```

---

## 2. Logo & Brand Mark

### Concept

The PortGuard logomark is a **stylized cargo ship silhouette** built from clean geometric rectangles. It reads as a shipping vessel at small sizes and reveals container stacks at larger sizes. The hull uses teal, the containers use coral red and amber — the exact palette references. A teal outer glow makes it feel alive against the dark navy page.

No gradients inside the logomark itself — it is flat, bold, and icon-sharp. The glow is applied via `filter: drop-shadow()` on the SVG wrapper.

---

### SVG Logo Specification

#### Full Logomark (32×32px icon)

```
Construction grid: 32 × 32px, 2px baseline grid

Components:
  Hull:
    - Main body: rect x=2, y=18, w=28, h=10, rx=2
      fill: --teal-500 (#1B9AAA)
    - Bow rake: path from (2,18) to (6,14) to (2,14) close
      fill: --teal-700 (#0D6B7A)
    - Stern block: rect x=24, y=14, w=6, h=4, rx=1
      fill: --teal-700 (#0D6B7A)
    - Waterline stripe: rect x=2, y=25, w=28, h=3, rx=1
      fill: --teal-700 (#0D6B7A), opacity: .6

  Containers (3 stacks):
    - Container A (left, coral):
        rect x=4, y=10, w=7, h=8, rx=1
        fill: --coral-500 (#C0392B)
    - Container B (center, amber):
        rect x=13, y=10, w=7, h=8, rx=1
        fill: --amber-500 (#E8A838)
    - Container C (right, coral):
        rect x=22, y=10, w=6, h=8, rx=1
        fill: --coral-500 (#C0392B)

  Container details (horizontal ribs, 1px lines):
    - Each container has 2 horizontal lines at y+3 and y+5
      stroke: rgba(255,255,255,.2), stroke-width: .75

  Wake lines:
    - 3 short horizontal lines below the hull (y=29, y=30.5, y=32)
      x-range decreasing: 4-28, 6-26, 8-24
      stroke: --teal-300 (#4DCFDF), stroke-width: .75, opacity: .5
```

#### Logo Wrapper Glow

```css
.logo-icon svg {
  filter: drop-shadow(0 0 8px rgba(27,154,170,.5))
          drop-shadow(0 0 3px rgba(27,154,170,.3));
  transition: filter .3s ease;
}
.logo:hover .logo-icon svg {
  filter: drop-shadow(0 0 14px rgba(27,154,170,.7))
          drop-shadow(0 0 5px rgba(27,154,170,.45));
}
```

#### Favicon Version (16×16px)

Simplified to hull + 2 containers only. No wake lines. Containers merged into a single rect stack.

---

### Wordmark

```
Font:    Inter 800 (ExtraBold)
Size:    In header: 1.1rem
Tracking: 0.08em letter-spacing
Color:   --text-primary (#EDF2F8)

Subtitle:
Font:    Inter 500
Size:    0.65rem
Color:   --text-muted (#4A6880)
Tracking: 0.12em letter-spacing
Transform: uppercase
```

The wordmark NEVER uses a gradient. It is always solid `--text-primary`. The teal energy comes from the icon, not the text.

---

### Logo Clearspace

Minimum clearspace = 1× the height of the icon on all sides. The logo icon must never be placed directly against the page edge.

Logo minimum sizes:
- Header: 32px icon
- Auth card: 40px icon
- Favicon: 16px icon (simplified)
- OG/social: 128px icon (full detail)

---

## 3. Typography

### Font Stack

```css
--font-ui:   'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
--font-mono: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
```

Both loaded from Google Fonts with `display=swap`. Weights preloaded: Inter 300, 400, 500, 600, 700, 800. JetBrains Mono 400, 500.

---

### Type Scale

```
Label XS:    0.65rem / 700 / 0.12em tracking / uppercase    → Section labels, table headers
Label SM:    0.72rem / 600 / 0.08em tracking / uppercase    → Card titles, badge text
Body SM:     0.82rem / 400 / normal                         → Secondary content, helper text
Body MD:     0.9rem  / 400 / normal                         → Primary body copy
Body LG:     1.0rem  / 400 / normal                         → Long-form content
UI MD:       0.875rem/ 600 / normal                         → Navigation, buttons
UI LG:       1.0rem  / 700 / normal                         → Primary button labels
Heading SM:  1.1rem  / 700 / 0.02em                         → Card section heads, modal titles
Heading MD:  1.35rem / 800 / 0.01em                         → Page section titles
Heading LG:  1.75rem / 800 / normal                         → Dashboard KPI values
Hero:        clamp(2rem, 5vw, 3.2rem) / 800 / -0.01em      → Hero h1
Data SM:     0.75rem / 500 (mono)                           → Short IDs, codes
Data MD:     0.85rem / 400 (mono)                           → Data values, timestamps
Data LG:     1.1rem  / 500 (mono)                           → Risk scores inline
```

---

### Typography Rules

1. **All-caps labels** always use `letter-spacing: 0.1em` or wider. Never all-caps without tracking.
2. **Monospace** is reserved for: risk scores, HTS codes, shipment IDs, B/L numbers, timestamps, API URLs, and all data field values. Never for prose.
3. **Line height** for reading text: `1.65`. For single-line UI elements: `1.2`. For headings: `1.15`.
4. **Maximum line length** for any block of prose: `68ch`. Results panel text wraps at `640px` max-width.
5. **Gradient text** only for the hero h1. Never on body copy, labels, or interactive elements.

---

## 4. Spacing & Layout

### Baseline Grid

All spacing values are multiples of `0.25rem` (4px). The 8px grid is the primary rhythm.

```
--space-1:  0.25rem   (4px)
--space-2:  0.5rem    (8px)
--space-3:  0.75rem   (12px)
--space-4:  1rem      (16px)
--space-5:  1.25rem   (20px)
--space-6:  1.5rem    (24px)
--space-8:  2rem      (32px)
--space-10: 2.5rem    (40px)
--space-12: 3rem      (48px)
--space-16: 4rem      (64px)
```

### Border Radius

```
--radius-sm:  6px     → Badges, small buttons, tabs
--radius-md:  10px    → Cards, standard inputs
--radius-lg:  14px    → Modals, auth card, decision banner
--radius-xl:  20px    → Hero pill badges
--radius-full: 9999px → Pill badges, circular elements
```

### Layout Containers

```
Analyze panel main: max-width 900px, centered, 0 1.5rem padding
Dashboard container: max-width 1100px, centered, 0 1.5rem padding
Header: full-width, 0 2rem padding
Section nav: full-width, 0 2rem padding
```

---

## 5. Animation Principles

### Core Philosophy

- **Purposeful motion only.** Every animation communicates state change, guides attention, or rewards interaction. No decorative motion for its own sake.
- **Fast in, slow out.** Enter animations are quick (150–350ms). Exit animations are slightly faster (100–200ms).
- **Easing is always `ease-out` or `cubic-bezier`** for exits and reveals. Never `linear` except for infinite loops. Never `ease-in` for reveals.
- **Never block interaction.** Animations never delay a user action. They run alongside or beneath it.

---

### Timing Reference

```
--dur-instant:  80ms    → Immediate feedback (button active state)
--dur-fast:     150ms   → Hover states, color transitions
--dur-normal:   250ms   → Tab switches, badge color changes
--dur-medium:   400ms   → Card reveals, dropdown opens
--dur-slow:     600ms   → Gauge fills (first portion), modal enters
--dur-dramatic: 1100ms  → Gauge fill (full arc), primary risk reveal

--ease-out:     cubic-bezier(0.16, 1, 0.3, 1)   → Most reveals
--ease-spring:  cubic-bezier(0.34, 1.56, 0.64, 1) → Lift/bounce on hover
--ease-data:    cubic-bezier(0.4, 0, 0.2, 1)     → Gauge/chart fills
```

---

### Animation Catalog

#### Page Load — Staggered Element Entry
Content sections enter sequentially from top to bottom. Each element translates up from 20px and fades from opacity 0 to 1.

```
Stagger order and timing:
  Header:          instant (already visible)
  Hero badge:      delay 80ms,  dur 350ms
  Hero h1:         delay 160ms, dur 400ms
  Hero subtitle:   delay 220ms, dur 350ms
  Hero stats row:  delay 300ms, dur 350ms
  Section labels:  delay 380ms, dur 300ms
  Quick load btns: delay 420ms, dur 300ms, stagger 40ms per button
  Tabs bar:        delay 500ms, dur 300ms
  Document pane:   delay 560ms, dur 350ms
  Analyze button:  delay 640ms, dur 400ms

Transform: translateY(20px) → translateY(0)
Opacity:   0 → 1
Easing:    --ease-out
```

#### Results Panel Reveal
After the API returns, results do not appear all at once. They enter in two phases:

Phase 1 (immediate): The decision banner slides in from opacity 0, translateY(-12px).
Phase 2 (staggered): Each subsequent card fades up with a 60ms stagger.

```
Decision banner:   dur 450ms, ease --ease-out, from translateY(-12px)
Download btn row:  delay 80ms,  dur 300ms, fadeUp
Gauge + stats row: delay 160ms, dur 400ms, fadeUp
Findings card:     delay 260ms, dur 350ms, fadeUp
Steps card:        delay 340ms, dur 350ms, fadeUp
Pattern section:   delay 420ms, dur 350ms, fadeUp
Feedback section:  delay 480ms, dur 350ms, fadeUp
Shipment data:     delay 540ms, dur 300ms, fadeUp
```

"fadeUp" = `opacity 0→1` + `translateY(16px→0)`.

#### Decision Banner — Severity Flash
When the decision is FLAG, REJECT, or REQUEST_MORE_INFORMATION, the banner plays a single-shot severity flash: the border glow pulses from 0 to full brightness and back twice, then settles at its steady state.

```
Keyframes (banner-alert):
  0%:   box-shadow: none
  20%:  box-shadow: 0 0 28px [decision-color, .6 opacity]
  40%:  box-shadow: none
  60%:  box-shadow: 0 0 20px [decision-color, .4 opacity]
  80%:  box-shadow: none
  100%: box-shadow: 0 0 14px [decision-color, .2 opacity]  ← steady state

Duration: 1.4s, runs once, delay 300ms after banner appears.
Only on FLAG_FOR_INSPECTION, REQUEST_MORE_INFORMATION, REJECT.
```

#### Risk Gauge — Animated Fill
The gauge always resets to 0 before animating forward to the target value. Never skips to final value immediately.

```
Step 1: Instantly reset stroke-dasharray to "0 [arc-length+20]"
        and stroke color to --text-faint, transition: none
Step 2: After 80ms, re-enable transition:
        stroke-dasharray 1.1s cubic-bezier(.4,0,.2,1),
        stroke .6s ease-out
Step 3: Set final stroke-dasharray = "[score × arc-length] [arc-length+20]"
        and final stroke color = score-based color

Color thresholds:
  ≤ 0.25:  --success  (#1DB87A)  level: LOW
  ≤ 0.50:  --warning  (#E8A838)  level: MEDIUM
  ≤ 0.75:  --flag     (#D4773A)  level: HIGH
  > 0.75:  --danger   (#E05050)  level: CRITICAL

The score number text (e.g. "0.74") counts up from "0.00" in sync with the arc.
Use requestAnimationFrame for the text counter — 60fps linear interpolation
over the same 1.1s duration.
```

#### Skeleton Loading State
Dashboard KPI cards and chart areas show a shimmer skeleton while data loads.

```css
@keyframes skel-shimmer {
  0%   { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
.skel {
  background: linear-gradient(
    90deg,
    var(--bg-card-hi)    0%,
    rgba(27,154,170,.06) 40%,
    var(--bg-card-hi)    60%,
    var(--bg-card-hi)    100%
  );
  background-size: 200% 100%;
  animation: skel-shimmer 1.6s ease infinite;
  border-radius: var(--radius-sm);
}
```

The shimmer tint is a faint teal (the brand color) rather than pure gray — a subtle but meaningful differentiation.

#### Chart Entry Animation
All Chart.js charts use:
```
animation: { duration: 700, easing: 'easeOutQuart' }
```
Bars grow from the baseline. Line charts draw left-to-right. Donut segments draw clockwise from 12 o'clock.

#### Hover — Card Lift
All interactive cards (results cards, KPI cards, activity rows) lift slightly on hover.

```css
transition: transform 200ms var(--ease-spring),
            box-shadow 200ms ease-out,
            border-color 200ms ease;
&:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-card-hover);
  border-color: var(--border-teal);
}
```

#### Button States
```
Default:  [base style]
Hover:    translateY(-1px), box-shadow grows, brightness +5%
Active:   translateY(0), box-shadow shrinks
Disabled: opacity .5, cursor not-allowed, no transform
Loading:  opacity .8, cursor wait, spinner replaces icon
```

#### Tab Switching
When switching between Analyze and Dashboard tabs:

```
Departing panel:  opacity 1→0, translateX(0→-8px), dur 180ms, ease-in
Arriving panel:   opacity 0→1, translateX(8px→0),  dur 250ms, ease-out
                  starts after departing is at opacity .3
```

#### Success State — Green Pulse
Triggered on: feedback submitted, report downloaded successfully, history cleared.

```
@keyframes success-pulse {
  0%:   box-shadow: 0 0 0 0 rgba(29,184,122,.4)
  50%:  box-shadow: 0 0 0 10px rgba(29,184,122,.0)
  100%: box-shadow: 0 0 0 0 rgba(29,184,122,.0)
}
Duration: 600ms, runs once.
```

#### Error State — Gentle Shake
Triggered on: API error, validation failure.

```
@keyframes error-shake {
  0%, 100%: translateX(0)
  20%:      translateX(-6px)
  40%:      translateX(6px)
  60%:      translateX(-4px)
  80%:      translateX(4px)
}
Duration: 400ms, runs once, ease-out.
```

#### Infinite Loops (used sparingly)
```
API status dot:  opacity pulse, 2s, ease-in-out
Background waves: see §7, very slow 20–30s cycle
Logo hover glow:  subtle brightness oscillation on hover, 1.5s
```

---

## 6. Component Library

### Cards

All cards share a base style. Variants are additive.

```
Base card:
  background:    var(--bg-surface)
  border:        1px solid var(--border)
  border-radius: var(--radius-md)
  padding:       1.25rem 1.4rem
  box-shadow:    var(--shadow-card)
  transition:    border-color 200ms ease,
                 box-shadow 200ms ease,
                 transform 200ms var(--ease-spring)

Hover state:
  border-color:  var(--border-teal)
  box-shadow:    var(--shadow-card-hover), var(--glow-teal-sm)
  transform:     translateY(-2px)

Featured card (e.g. active pattern section):
  border-color:  var(--border-teal)
  box-shadow:    var(--shadow-card), var(--glow-teal-sm)

Card title:
  font-size:   0.65rem
  font-weight: 700
  letter-spacing: 0.12em
  text-transform: uppercase
  color:       var(--text-muted)
  margin-bottom: 1rem
  padding-bottom: 0.6rem
  border-bottom: 1px solid var(--border)
```

---

### Buttons

#### Primary Button (Teal)
```
background:    linear-gradient(135deg, var(--teal-700) 0%, var(--teal-500) 100%)
color:         white
border:        none
border-radius: var(--radius-md)
padding:       0.85rem 2rem
font-weight:   700
font-size:     1rem
letter-spacing: 0.04em
box-shadow:    0 4px 20px rgba(27,154,170,.35)
transition:    transform 150ms var(--ease-spring),
               box-shadow 150ms ease-out

hover:
  transform:   translateY(-1px)
  box-shadow:  0 6px 28px rgba(27,154,170,.55)

active:
  transform:   translateY(0)
  box-shadow:  0 2px 12px rgba(27,154,170,.25)

loading state:
  Replace label with spinner + "Analyzing…"
  opacity: .8, pointer-events: none
```

#### Secondary Button (Ghost)
```
background:    transparent
color:         var(--text-secondary)
border:        1px solid var(--border)
border-radius: var(--radius-md)
padding:       0.5rem 1rem
font-weight:   600
font-size:     0.85rem

hover:
  border-color: var(--border-teal)
  color:        var(--text-teal)
  background:   rgba(27,154,170,.05)
```

#### Destructive Button (Coral Red)
```
background:    linear-gradient(135deg, var(--coral-700) 0%, var(--coral-500) 100%)
color:         white
border:        none
border-radius: var(--radius-md)
padding:       0.55rem 1.2rem
font-weight:   700

hover:
  filter: brightness(1.1)
  transform: translateY(-1px)
```

#### Icon Button (Toolbar)
```
background:    transparent
border:        none
color:         var(--text-muted)
padding:       0.3rem 0.4rem
border-radius: var(--radius-sm)
transition:    color 150ms, background 150ms

hover:
  color:       var(--text-teal)
  background:  rgba(27,154,170,.1)
```

---

### Badges (Pill)

```
base:
  display:       inline-flex
  align-items:   center
  gap:           0.3rem
  padding:       0.2rem 0.7rem
  border-radius: var(--radius-full)
  font-size:     0.72rem
  font-weight:   700
  letter-spacing: 0.05em
  text-transform: uppercase

--badge-approve:
  background: rgba(29,184,122,.15)
  color:      #2ECC9A
  border:     1px solid rgba(29,184,122,.3)

--badge-review:
  background: rgba(155,108,214,.15)
  color:      #B07FE8
  border:     1px solid rgba(155,108,214,.3)

--badge-flag:
  background: rgba(212,119,58,.15)
  color:      #E89044
  border:     1px solid rgba(212,119,58,.3)

--badge-info:
  background: rgba(232,168,56,.15)
  color:      #F0C060
  border:     1px solid rgba(232,168,56,.3)

--badge-reject:
  background: rgba(224,80,80,.15)
  color:      #F08080
  border:     1px solid rgba(224,80,80,.3)

--badge-high:    (same as --badge-reject)
--badge-medium:  (same as --badge-info)
--badge-low:
  background: rgba(139,170,191,.1)
  color:      #8BAABF
  border:     1px solid rgba(139,170,191,.2)
```

---

### Decision Banner

The most visually significant component. It communicates the primary output.

```
structure:
  - Full-width card, border-radius: var(--radius-lg)
  - Left side: SVG icon + decision name (1.5rem/800) + subtitle
  - Right side: metadata row (docs, issues, time)

icon size:    40px SVG, stroked (not filled)
icon colors:  match decision color exactly

typography:
  decision name:  1.5rem, weight 800, decision color
  decision sub:   0.8rem, var(--text-secondary)
  meta value:     1.35rem, weight 700, var(--text-primary)
  meta label:     0.68rem, var(--text-muted), uppercase, 0.08em tracking

animation on reveal:
  translateY(-12px) → translateY(0), opacity 0→1, dur 450ms
  followed by severity flash (see §5) for high-severity decisions
```

---

### Risk Gauge (SVG Semicircle)

```
viewBox:        0 0 200 116
Track radius:   80px (semicircle)
Track stroke:   14px, --bg-card
Fill stroke:    14px, animated, color by risk level
Background:     17px, --bg-surface (creates inner bevel)
Score text:     28px JetBrains Mono 700, centered at (100, 88)
Level text:     10px Inter 700, uppercase, centered at (100, 108)
Corner labels:  9px mono, --text-faint, at 0 and 1.0 ends

Glow on fill:
  filter: drop-shadow(0 0 6px [fill-color at .4 opacity])

Score counter:
  Counts up from 0.00 in sync with arc fill, 2 decimal places.
  Uses requestAnimationFrame, linear interpolation.
```

---

### Input Fields

```
background:    var(--bg-card)
border:        1px solid var(--border)
border-radius: var(--radius-md)
padding:       0.65rem 0.9rem
color:         var(--text-primary)
font-family:   var(--font-ui)
font-size:     0.9rem
outline:       none
transition:    border-color 180ms, box-shadow 180ms

placeholder:
  color: var(--text-faint)

focus:
  border-color: var(--teal-300)
  box-shadow:   0 0 0 3px rgba(77,207,223,.15)

error:
  border-color: var(--danger)
  box-shadow:   0 0 0 3px rgba(224,80,80,.15)
  animation:    error-shake 400ms once
```

#### Textarea (Document Input)
```
Same as input field, plus:
  height:      220px
  resize:      vertical
  min-height:  120px
  max-height:  600px
  font-family: var(--font-mono)
  font-size:   0.78rem
  line-height: 1.75
```

---

### Navigation Tabs (Section Nav)

```
container:
  background:   var(--bg-surface)
  border-bottom: 1px solid var(--border)
  padding:      0 2rem

tab button:
  background:   none
  border:       none
  padding:      0.9rem 1.25rem
  color:        var(--text-muted)
  font-weight:  600
  font-size:    0.875rem
  border-bottom: 2.5px solid transparent
  margin-bottom: -1px
  transition:   color 180ms, border-color 180ms

tab active:
  color:          var(--teal-400)
  border-bottom:  2.5px solid var(--teal-500)

tab hover (inactive):
  color: var(--text-primary)

icon inside tab:
  width/height: 14px
  opacity: .6 (inactive), 1.0 (active)
  transition:   opacity 180ms
```

---

### Tables

```
Activity table:

  header row:
    font-size:   0.68rem
    font-weight: 600
    text-transform: uppercase
    letter-spacing: 0.07em
    color:       var(--text-muted)
    border-bottom: 1px solid var(--border)
    padding:     0.6rem 1rem

  body row:
    padding:     0.65rem 1rem
    border-bottom: 1px solid rgba(26,45,69,.5)
    transition:  background 150ms

  body row:hover:
    background: var(--bg-card-hi)

  last row:
    border-bottom: none

  alternating shade:
    Even rows: background rgba(22,40,69,.4) — subtle, not jarring

  Risk score color coding:
    ≥ 65:  color var(--danger)
    ≥ 40:  color var(--flag)
    < 40:  color var(--success)
```

---

### Signal Cards (Pattern Intelligence)

```
base:
  display:       flex
  align-items:   flex-start
  gap:           0.65rem
  padding:       0.6rem 0.8rem
  border-radius: var(--radius-sm)
  font-size:     0.82rem
  line-height:   1.5
  border-left:   3px solid [severity-color]
  transition:    background 150ms

dot indicator:
  width/height: 8px
  border-radius: 50%
  flex-shrink:  0
  margin-top:   0.38rem
  background:   [severity-color]

CRITICAL: background rgba(224,80,80,.1),   border-left var(--danger)
HIGH:     background rgba(212,119,58,.1),  border-left var(--flag)
MEDIUM:   background rgba(232,168,56,.1),  border-left var(--warning)
LOW:      background rgba(27,154,170,.1),  border-left var(--teal-500)
```

---

### Modal

```
overlay:
  background:     rgba(10,22,40,.85)
  backdrop-filter: blur(6px)
  transition:     opacity 200ms

modal card:
  background:     var(--bg-surface)
  border:         1px solid var(--danger, or --border-teal for non-destructive)
  border-radius:  var(--radius-lg)
  padding:        2rem 2.2rem
  max-width:      440px
  box-shadow:     var(--shadow-modal)
  animation:      modal-in 200ms var(--ease-out)

@keyframes modal-in:
  from: opacity 0; transform: scale(.96) translateY(8px)
  to:   opacity 1; transform: scale(1)   translateY(0)
```

---

### File Upload Area

```
Upload button (inline in filename row):
  Same as Ghost button, smaller (padding .28rem .75rem)
  Icon: upward arrow (↑)
  hover: teal tint

File badge (appears after upload):
  display:   inline-flex
  background: rgba(27,154,170,.12)
  border:    1px solid rgba(27,154,170,.25)
  color:     var(--text-teal)
  border-radius: var(--radius-full)
  padding:   0.15rem 0.65rem
  font-size: 0.7rem, weight 600

PDF badge variant:
  background: rgba(155,108,214,.12)
  border:    1px solid rgba(155,108,214,.3)
  color:     #B07FE8
```

---

## 7. Background Treatment

### Concept

The page background should feel like the surface of a navigation chart or a port authority radar screen. Subtle, purposeful motion that suggests the ocean without being literal. The animation must never distract from data — it is atmosphere, not feature.

**Chosen approach: Animated SVG wave lines + a subtle radial gradient overlay.**

This is lighter than canvas-based particle systems, fully CSS-driven after the SVG is defined, and can be paused by `prefers-reduced-motion`.

---

### Implementation Specification

#### Layer Structure (bottom to top)
```
1. Base color:     background: var(--bg-deep)                  — static
2. Radial glow:    radial gradient, teal center (very faint)   — static
3. Wave layer:     SVG pattern, animated                        — 20s cycle
4. Grid overlay:   SVG pattern, static                         — very faint
5. Content:        all UI components                           — above all
```

#### Radial Glow (CSS)
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

A faint teal glow bleeds from the top of the page — like light reflecting off harbor water from above. It is invisible at full opacity but adds warmth to the deep navy.

#### Wave Lines (SVG + CSS Animation)

```svg
<!-- Define in <defs>, use as background-image via data URI or inline SVG pattern -->

<!-- Single wave path (cubic bezier, one full cycle) -->
<path d="M0 60 C 200 40, 400 80, 600 60 S 1000 40, 1200 60" 
      stroke="rgba(27,154,170,.035)" stroke-width="1.5" fill="none"/>
```

Three wave lines are placed at different vertical positions and animated with different speeds:

| Wave | Y Position | Animation Speed | Opacity | Direction |
|---|---|---|---|---|
| Deep | 30% from top | 28s | 0.025 | left |
| Mid | 55% from top | 22s | 0.035 | right |
| Surface | 75% from top | 18s | 0.02 | left |

```css
@keyframes wave-drift-left {
  from { transform: translateX(0); }
  to   { transform: translateX(-600px); }
}
@keyframes wave-drift-right {
  from { transform: translateX(0); }
  to   { transform: translateX(600px); }
}
```

The wave paths are 2× the viewport width so they tile seamlessly.

#### Navigation Grid Overlay (very subtle)

```svg
<!-- 60px grid, teal lines at .015 opacity -->
<pattern id="nav-grid" width="60" height="60" patternUnits="userSpaceOnUse">
  <path d="M 60 0 L 0 0 0 60" fill="none" 
        stroke="rgba(27,154,170,.015)" stroke-width=".5"/>
</pattern>
```

The grid gives the impression of a navigation chart without being legible or distracting. At the opacity level specified (.015), it is barely visible on most displays.

#### Reduced Motion
```css
@media (prefers-reduced-motion: reduce) {
  .wave-line { animation: none; }
  body::before { opacity: .5; }
}
```

---

## 8. Page-Level Composition

### Visual Hierarchy Map

Every screen has exactly three levels of visual weight:

1. **Primary** (one element only): The current decision banner OR the analyze button. Never both at once.
2. **Secondary** (2–4 elements): Cards containing active data, primary nav item.
3. **Supporting** (everything else): Labels, metadata, secondary cards, nav, footer-level elements.

The eye should always know where to look first.

---

### Header

```
Height:          60px
Background:      rgba(10,22,40,.92), backdrop-filter: blur(20px)
Border-bottom:   1px solid var(--border)
Left:            Logo (32px icon + wordmark + subtitle)
Center:          [empty on analyze, section nav on dashboard could move here — future]
Right:           API status dot + URL, org name, sign out
z-index:         100
```

The header background is slightly more opaque than the current implementation (`rgba(7,16,31,.92)` → `rgba(10,22,40,.92)`) to better match the new bg-deep value.

---

### Hero Section

```
Layout:         centered, max-width 900px
Padding:        4rem top, 3rem bottom
Background:     inherits page bg-deep + wave layer shows through

Badge:          teal pill, 0.72rem, uppercase, 0.06em tracking
               "◆ AI-POWERED COMPLIANCE ENGINE"  (or triangle icon)
               Background: rgba(27,154,170,.12)
               Border:     rgba(27,154,170,.3)
               Color:      var(--teal-300)

H1:             Gradient text, clamp(2rem, 5vw, 3.2rem)
               Gradient:   #EDF2F8 → #4DCFDF (warm white → bright teal)
               No coral in the gradient — reserve coral for alerts.

Subtitle:       1.05rem, var(--text-secondary), max-width 580px

Stats row:      Stat pills — subtle card style
               Add a teal left-border (2px) to each pill for structure
```

---

### Analyze Panel Composition

```
Document input area:
  - Tabs bar: dark surface, teal active underline, +button dashed border
  - Doc pane: no top border-radius (continues from tabs bar)
  - Textarea: mono font, slight teal focus glow

Analyze button:
  Full-width, primary teal, this is the #1 CTA on the page.
  Arrow icon → spinner during loading.
  After analysis completes: checkmark icon briefly, then back to arrow.
```

---

### Results Panel Composition

```
Flow (top to bottom):
  ├── Download report (right-aligned, ghost button)
  ├── Decision banner ← HIGHEST VISUAL WEIGHT
  ├── [2 col] Risk gauge | Assessment details
  ├── Findings
  ├── Next steps
  ├── Pattern Intelligence (if present)
  ├── Officer Feedback (if present)
  └── Shipment data (collapsible)
```

The decision banner is the most visually assertive element. Its padding is generous (1.5rem 1.8rem), the decision name is large (1.5rem/800), and the severity flash animation ensures it captures focus immediately.

---

### Dashboard Composition

```
Layout: max-width 1100px, more breathing room than analyze panel

Header row: Title + last-updated timestamp (left) | Refresh button (right)

KPI cards: 5-col grid
  Each card: subtle hover lift, teal glow on hover
  Values: JetBrains Mono, 1.75rem, weight 800
  Color coding: green (good), amber (warn), red (danger)

Charts row 1: 1fr / 1fr
  - Trend line: left axis = fraud %, right axis = total (subtle)
  - Donut: custom legend, total in center (mono)

Charts row 2: 1fr / 1fr
  - Horizontal bars: teal theme for normal, coral for high-risk
  - Both charts: consistent bar border-radius 4px

Activity table:
  - Full width
  - Alternating row shading (every other row slightly lighter)
  - Decision badges use the pill badge component
  - Risk score: mono, color-coded
  - Download icon: shows on hover of each row
```

---

## 9. Implementation Notes

### CSS Variable Migration

The existing demo.html CSS variable names (`--bg`, `--surface`, `--card`, `--card-hi`, `--border`, `--border-hi`, `--blue`, `--blue-dim`, `--text`, `--muted`, `--faint`, `--green`, `--amber`, `--orange`, `--red`, `--purple`, `--radius`) must be replaced with the new token names. To avoid breaking JS that reads inline styles, update both the `:root` block and all usages simultaneously.

### Chart.js Theme

Set globally once:
```javascript
Chart.defaults.color       = '#8BAABF';         // --text-secondary
Chart.defaults.borderColor = '#1A3259';         // --border
Chart.defaults.font.family = "'Inter', system-ui, sans-serif";
Chart.defaults.font.size   = 11;

// Custom tooltip:
{
  backgroundColor: '#0F2040',   // --bg-surface
  borderColor:     '#1A3259',   // --border
  borderWidth:     1,
  titleColor:      '#EDF2F8',   // --text-primary
  bodyColor:       '#8BAABF',   // --text-secondary
  cornerRadius:    6,
  padding:         10,
}
```

### SVG Logo Inline vs External

The logo SVG must be **inline** in the HTML, not an `<img src>`. This allows:
- CSS `filter: drop-shadow()` to apply correctly
- `currentColor` for any theme-responsive elements
- No additional network request

### z-index Stack

```
Background waves:   -1
Page content:        1
Sticky header:     100
Dashboard panel:     1  (full-page, behind header)
Modal overlay:     200
Auth overlay:     9000
```

### Performance Constraints

- Wave animations use `transform: translateX()` only — no `top/left/width/height` — so they run on the compositor thread.
- Gauge fill uses `stroke-dasharray` — also compositor-safe via SVG animation.
- No JavaScript is involved in the background animation.
- `will-change: transform` on wave lines to hint the GPU.
- Chart.js must only be initialized/destroyed with the `_charts` registry — no orphaned Chart instances.

### Accessibility

- All color choices maintain minimum 4.5:1 contrast for body text against their backgrounds.
- `--text-primary` (#EDF2F8) on `--bg-surface` (#0F2040): contrast ratio ~14:1. ✓
- `--text-secondary` (#8BAABF) on `--bg-surface` (#0F2040): contrast ratio ~5.2:1. ✓
- `--teal-300` (#4DCFDF) on `--bg-surface` (#0F2040): contrast ratio ~8.1:1. ✓
- All interactive elements have visible focus states (teal `box-shadow: 0 0 0 3px`).
- Wave animation and glow effects respect `prefers-reduced-motion: reduce`.
- Decision banners communicate severity through color AND icon AND text label (never color alone).

---

*PortGuard UI Design System v1.0 — written for the demo.html overhaul. This document is the source of truth. No implementation decisions should deviate from it without updating this file first.*
