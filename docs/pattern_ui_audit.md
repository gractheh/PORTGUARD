# Pattern Intelligence UI Audit
**Date:** 2026-05-14  
**Files:** `demo.html`, `portguard/pattern_engine.py`  
**Issue:** 5 confirmed UI bugs + shallow intelligence content in the Pattern Intelligence panel.

---

## 1. Confirmed Bugs

### 1a. Fraud bubble clipped / off-center (`.pi-hard-flag`)
Long shipper names like "CONFIRMED FRAUD HISTORY: Shenzhen Electronics Manufacturing Co. has 2 confirmed fraud outcome(s) on record — manual review required" overflow their container and get clipped. The element lacks `word-break`, `overflow-wrap`, and `box-sizing: border-box`.

**Root cause:** `.pi-hard-flag` has no text wrapping constraints. Parent `.pi-result-card` has no `overflow: hidden`.

### 1b. "Pattern Learning History" label low opacity
The `.section-label` base class uses `color: var(--faint)` = `#4A6880`, making the panel title barely visible against the dark background.

**Root cause:** `.ph-header .section-label` inherits the faint base color with no override.

### 1c. High-risk shipper name uses Arial / overflows
`.pattern-key` has no `font-family` declaration (falls back to system Arial) and uses `max-width: 60%` with `flex: none`, causing badge overlap.

**Root cause:** Missing `font-family: 'Inter'`, `flex: 1 1 0`, and `min-width: 0` on `.pattern-key`.

### 1d. Badge clipped by parent tile
`.pattern-badge` has no `flex-shrink: 0` or `min-width: fit-content`, so it compresses when `.pattern-key` grows.

**Root cause:** Missing `flex-shrink: 0` and `min-width: fit-content`.

### 1e. Pattern Intelligence content too shallow
`get_pattern_stats()` returns only raw counts (`flag_count/occurrence_count`) with no rates, no per-entity `fraud_confirmed_count`, no value anomalies, no org-level health metrics. The JS renders only the basic counts.

---

## 2. Fix Plan

### Backend (`portguard/pattern_engine.py`)
- Add `flag_rate`, `fraud_confirmed_count`, `avg_risk_score` (1 decimal), `last_seen`, `last_decision` to each `high_risk_shippers` item
- Add `flag_rate`, `avg_risk_score`, `last_seen`, `last_decision` to each `high_risk_routes` item
- Add top-level: `avg_org_risk_score`, `total_flags_issued`, `approval_rate`, `value_anomalies` (top 3)
- Update SQL SELECTs to fetch new columns

### Frontend CSS (`demo.html`)
- `.pi-hard-flag`: add `word-break`, `overflow-wrap`, `width: 100%`, `box-sizing: border-box`, `display: block`
- `.pi-result-card`: add `width: 100%`, `box-sizing: border-box`, `overflow: hidden`
- `.ph-header .section-label`: `color: rgba(255,255,255,0.85); font-weight: 600`
- `.pattern-stat-label` and `.pattern-section-title`: raise opacity to `rgba(255,255,255,0.6)`
- `.pattern-row`: add `gap: 0.5rem; min-width: 0; box-sizing: border-box`
- `.pattern-key`: add `font-family: 'Inter'; flex: 1 1 0; min-width: 0`; remove `max-width: 60%`
- `.pattern-badge`: add `flex-shrink: 0; min-width: fit-content`
- Add `.pattern-badge.fraud` (red, bold, 0.2 alpha bg)
- `.pattern-section`: add `overflow: visible`
- Add: `.pattern-badge-stack`, `.pattern-health-row`, `.pattern-health-label`, `.pattern-health-bar-wrap`, `.pattern-health-bar` (+ `.risk` variant), `.pattern-health-pct`

### Frontend JS (`demo.html`)
- Replace `renderPatternStats(stats)` with richer version:
  - 4-stat grid + approval rate bar + avg risk bar
  - High-risk shippers: `pattern-badge-stack` with FRAUD badge (if `fraud_confirmed_count > 0`) + flag_rate%
  - High-risk routes: flag_rate%
  - Value anomalies section
  - Cleared shippers
