# Pattern Intelligence Engine — Real Integration Sprint

**Date:** 2026-05-14  
**Objective:** Make the Localized Pattern Learning feature real — patterns must actually change decisions, fraud history must hard-flag future analyses, and the UI must accurately reflect history counts.

---

## Root Cause Analysis

| Issue | Root cause |
|-------|-----------|
| "Cosmetic" complaint | Blending only fires when `history_available = not is_cold_start` (depth ≥ 3). First 3 analyses are rule-only. |
| CONFIRMED_FRAUD doesn't hard-flag | `_apply_fraud_outcome()` updates Bayesian Beta weights but no code enforces a minimum decision. A fraud shipper can still APPROVE if rule score is low. |
| "Building history — first shipment recorded" shown with history | `pattern_history_depth` in response = depth BEFORE `_record_shipment_bg()` runs, so depth is always off by 1. depth=0 always shows "first shipment recorded". |
| Pattern signals invisible during cold start | `history_available=False` → `renderPatternIntelligence` shows "Building history" but no meaningful status on contributing vs not. |

---

## Changes

### 1. `api/app.py` — Three pattern scoring blocks (lines 1749, 2055, 3220)

**Old behaviour:** blend only when `history_available = not is_cold_start` (depth ≥ 3).

**New behaviour:** blend from depth ≥ 1 using `effective_pattern_score` (which already applies ×0.5 cold-start damping at depth 1–2). `history_available` stays True only at depth ≥ 3 (UI indicator semantics unchanged).

```python
# OLD
if history_available:
    blended = _RULE_WEIGHT * rule_score + _PATTERN_WEIGHT * pattern_score_val

# NEW
if pattern_result.history_depth >= 1:
    blended = _RULE_WEIGHT * rule_score + _PATTERN_WEIGHT * pattern_result.effective_pattern_score
```

### 2. `api/app.py` — Confirmed-fraud hard flag (after each pattern scoring block)

After pattern scoring in all three paths, add:

```python
if _pattern_db is not None:
    for name, getter in (
        (sd.get("exporter"), "get_shipper_profile"),
        (sd.get("importer") or sd.get("consignee"), "get_consignee_profile"),
    ):
        if name:
            profile = getattr(_pattern_db, getter)(name, org_id)
            if profile.total_confirmed_fraud > 0:
                pattern_signals.insert(0, "⚠ CONFIRMED FRAUD HISTORY: ...")
                if final_decision == "APPROVE":
                    final_decision = "REVIEW_RECOMMENDED"
```

### 3. `api/app.py` — Fix off-by-one depth (after `_record_shipment_bg`)

```python
shipment_id = _record_shipment_bg(...)
if shipment_id is not None and pattern_history_depth_val is not None:
    analyze_response.pattern_history_depth = pattern_history_depth_val + 1
analyze_response.shipment_id = shipment_id
```

Applied to all three paths.

### 4. `demo.html` — Fix "Building history" text

```javascript
// OLD
textEl.textContent = depth > 0
  ? `Building history — ${depth} shipment(s) analyzed so far`
  : 'Building history — first shipment recorded';

// NEW
if (depth > 0) {
    const needed = Math.max(0, 3 - depth);
    textEl.textContent = `Building confidence — ${depth} shipment(s) in history${needed > 0 ? ` (${needed} more for full scoring)` : ''}`;
} else {
    textEl.textContent = 'Pattern learning started — this analysis has been recorded';
}
```

---

## Files changed

| File | Change |
|------|--------|
| `api/app.py` | 3 pattern scoring blocks + 3 post-recording depth fixes + fraud flag check |
| `demo.html` | `renderPatternIntelligence` text logic |

---

## Not changed

- `portguard/pattern_engine.py` — no changes needed; `effective_pattern_score` already exists
- `portguard/pattern_db.py` — `get_shipper_profile()` / `get_consignee_profile()` already exist
- All test files — no new tests required for this sprint (integration changes, not new API surface)

---

## Scoring behaviour after this sprint

| History depth | blend_weight | effective_score_used | decision override |
|--------------|-------------|---------------------|-------------------|
| 0 | 0 (rule only) | — | none |
| 1–2 (cold) | 35% × 0.5 = 17.5% | effective (×0.5) | fraud → REVIEW min |
| ≥3 (warm) | 35% | full pattern_score | fraud → REVIEW min |
