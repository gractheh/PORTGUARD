# Pattern Intelligence — Build Plan (Sprint C)

**Date:** 2026-05-14  
**Based on:** `docs/pattern_intelligence_audit.md`  
**Status:** Pre-implementation — plan only, no code yet

---

## What to build and why

### Gap 1 — `history_available` semantic mismatch (HIGH)

**What's wrong:** `history_available` is currently `not is_cold_start` = `depth >= 3`. But since Sprint B, blending fires at `depth >= 1`. The response field tells the UI "history not available" even while pattern scores ARE being applied to the decision.

**Side-effect:** The phi-dot shows `building` (amber) and phi-text says "Building confidence" for depth 1–2, but the analysis IS being influenced by pattern data. This is a lie of omission.

**Fix — two-tier model in the response:**
- `history_available: bool` stays as `depth >= 3` (full-confidence tier, warm-start)
- Add new response field `pattern_contributing: bool` = `depth >= 1` (any pattern data is in use)

**UI change:** Add a three-state phi-dot:
- `depth = 0`: class `phi-dot` (grey, no dot animation) — "No prior history"
- `depth 1–2`: class `phi-dot contributing` (teal, slow pulse) — "Pattern contributing — N shipment(s)"
- `depth ≥ 3`: class `phi-dot active` (green, pulse) — "Scoring active — N prior shipments"

**CSS to add:**
```css
.phi-dot.contributing { background: var(--teal-300); animation: phi-pulse 2.5s ease-in-out infinite; }
```

---

### Gap 2 — Decision escalation is silent in the Findings panel (MEDIUM)

**What's wrong:** When a confirmed-fraud hard flag fires and escalates APPROVE → REVIEW_RECOMMENDED, the decision banner changes but the Findings list (`#findings-list`) shows zero explanation for WHY the decision is REVIEW_RECOMMENDED. The fraud signal is in the Pattern Intelligence section but officers may not read that far, especially on mobile.

**What to change — `api/app.py` (three analysis paths):**

After the fraud flag block, if the decision was escalated by patterns, prepend the escalation reason to the main `explanations` list in AnalyzeResponse:

```python
# After fraud flag block and before building AnalyzeResponse:
_pattern_explanations_for_findings = []
if final_decision != rule_decision and pattern_signals:
    # Decision was changed by pattern intelligence — surface top signal in Findings
    for sig in pattern_signals:
        if sig.startswith("⚠"):
            _pattern_explanations_for_findings.append(f"[Pattern] {sig}")
            break  # only the first / most severe

# Then when building AnalyzeResponse:
explanations=_pattern_explanations_for_findings + result["explanations"],
```

**UI change — none required.** The existing `#findings-list` renderer handles any string in `explanations`. The `[Pattern]` prefix lets the officer quickly see the source.

---

### Gap 3 — phi-text says "(0 more for full scoring)" at depth=3 while state is still "building" (LOW)

**What's wrong:** At depth=3, `pattern_history_depth_val` in response = 2+1 = 3, but `history_available = False` (because scoring ran with pre-recording depth=2, which is cold-start). So the UI ends up in:  
`else if (depth > 0)` → "Building confidence — 3 shipments in history (0 more for full scoring)"

The message is grammatically correct but reads as "you're done" while the dot is still amber.

**Fix — `demo.html`, `renderPatternIntelligence`:**

```javascript
// Replace:
const _needStr = _needed > 0 ? ` (${_needed} more for full scoring)` : '';
// With:
const _needStr = _needed > 0
  ? ` (${_needed} more for full scoring)`
  : ' — full scoring unlocks next analysis';
```

This turns "3 shipments in history (0 more for full scoring)" into  
"3 shipments in history — full scoring unlocks next analysis"  
which accurately reflects that full scoring kicks in on the NEXT submission.

---

### Gap 4 — `pattern_value_z_score` column always NULL (LOW)

**What's wrong:** `_record_shipment_bg()` builds `ShipmentFingerprint` with `pattern_value_z_score=None`. The actual z-score is computed by `ValueAnomalySignal` and stored in `pattern_result.signals[3].z_score` (the fourth signal in the `all_signals` list), but it's never written to the DB column.

**Fix — `api/app.py`, `_record_shipment_bg()`:**

Add `pattern_value_z_score` population to the `ShipmentFingerprint` construction:

```python
# In _record_shipment_bg(), after pattern_result is passed in:
_value_z: Optional[float] = None
if pattern_result is not None:
    try:
        _value_z = pattern_result.signals[3].z_score  # ValueAnomalySignal
    except Exception:
        pass

fp = ShipmentFingerprint(
    ...
    pattern_value_z_score=_value_z,   # was: None
    ...
)
```

Note: `pattern_result.signals` is `[shipper, consignee, route, value_anomaly, frequency]`. Index 3 is `ValueAnomalySignal`. Its `.z_score` field is `Optional[float]` — None when insufficient HS baseline data exists.

---

### Gap 5 — Confirmed-fraud escalation cap: should distinguish repeat vs first-time offenders (MEDIUM)

**What's wrong:** The current fraud flag only escalates APPROVE → REVIEW_RECOMMENDED. A shipper with 5 confirmed fraud records gets the same treatment as one with 1. There's no graduated escalation.

**Fix — `api/app.py`, fraud flag block (all three paths):**

```python
if _fp.total_confirmed_fraud > 0:
    _fraud_count = _fp.total_confirmed_fraud
    pattern_signals.insert(0, f"⚠ CONFIRMED FRAUD HISTORY: {_fn} has ...")
    
    if _fraud_count >= 3:
        # Repeat offender: escalate to FLAG_FOR_INSPECTION minimum
        if final_decision in ("APPROVE", "REVIEW_RECOMMENDED"):
            final_decision = "FLAG_FOR_INSPECTION"
    else:
        # First or second confirmed fraud: escalate to REVIEW minimum
        if final_decision == "APPROVE":
            final_decision = "REVIEW_RECOMMENDED"
```

**Threshold rationale:**
- 1–2 confirmed frauds: REVIEW_RECOMMENDED (officer must look, but may be mistake)
- 3+ confirmed frauds: FLAG_FOR_INSPECTION (consistent pattern, inspector assigned)

---

## Summary of changes by file

| File | Change | Gap |
|------|--------|-----|
| `api/app.py` | Add `pattern_contributing: bool` field to `AnalyzeResponse` | Gap 1 |
| `api/app.py` | Set `pattern_contributing = pattern_history_depth_val >= 1` in three paths | Gap 1 |
| `api/app.py` | Prepend `[Pattern]` escalation reason to `explanations` when decision changed | Gap 2 |
| `api/app.py` | In `_record_shipment_bg()`: populate `pattern_value_z_score` from signal index 3 | Gap 4 |
| `api/app.py` | Graduated escalation: ≥3 confirmed fraud → FLAG_FOR_INSPECTION | Gap 5 |
| `demo.html` | CSS: `.phi-dot.contributing` style (teal, slow pulse) | Gap 1 |
| `demo.html` | `renderPatternIntelligence`: three-state phi-dot logic using `data.pattern_contributing` | Gap 1 |
| `demo.html` | `renderPatternIntelligence`: "(0 more)" → "— full scoring unlocks next analysis" | Gap 3 |

---

## What to NOT change

- `portguard/pattern_engine.py` — no changes needed; engine is correct
- `portguard/pattern_db.py` — no changes needed; schema is correct; `get_shipper_profile()` / `get_consignee_profile()` already exist
- `tests/` — no new tests needed (integration changes, not new API surface; existing 129 tests cover the paths)
- Cold-start threshold (`COLD_START_HISTORY_THRESHOLD = 3`) — keep at 3; correct for `history_available` semantics

---

## Sequencing (implementation order)

1. `api/app.py` — add `pattern_contributing` to `AnalyzeResponse` model
2. `api/app.py` — set `pattern_contributing` in all three analysis paths
3. `api/app.py` — prepend pattern escalation reason to `explanations`
4. `api/app.py` — graduated escalation (1–2 → REVIEW, 3+ → FLAG)
5. `api/app.py` — `_record_shipment_bg`: populate `pattern_value_z_score`
6. `demo.html` — CSS for `.phi-dot.contributing`
7. `demo.html` — three-state phi-dot logic
8. `demo.html` — "(0 more)" text fix
9. Run `pytest tests/ -x -q` — verify 129/129 pass
10. Import check: `python -c "import sys; sys.path.insert(0,'api'); import app"`
11. `git add`, `git commit`, `git push origin master`

---

## Acceptance criteria

| Criterion | How to verify |
|-----------|--------------|
| After 1st analysis (depth becomes 1): phi-dot is teal (contributing), text says "Pattern contributing — 1 shipment" | Submit first analysis; check pattern card |
| After 2nd analysis (depth becomes 2): phi-dot still teal, "(1 more for full scoring)" | Submit second analysis |
| After 3rd analysis (depth becomes 3): phi-dot still amber (building), text says "3 shipments in history — full scoring unlocks next analysis" | Submit third analysis |
| After 4th analysis (depth becomes 4): phi-dot green (active), text says "Scoring active — based on 4 prior shipments" | Submit fourth analysis |
| Shipper with 1 confirmed fraud + APPROVE decision → REVIEW_RECOMMENDED, "[Pattern] ⚠ CONFIRMED FRAUD HISTORY..." appears in Findings | Submit feedback CONFIRMED_FRAUD, then reanalyze same shipper |
| Shipper with 3+ confirmed frauds → FLAG_FOR_INSPECTION minimum | Same but after 3 fraud feedbacks |
| `pattern_value_z_score` column populated in DB after analysis with HS code + value | Check DB directly |
| 129/129 tests pass | `pytest tests/ -x -q` |
