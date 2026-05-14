# Pattern Intelligence — Code Audit

**Date:** 2026-05-14  
**Branch:** master (commit 3b5065b — post-Sprint B)

---

## 1. What does `pattern_engine.py` actually store? What schema?

**`pattern_engine.py` stores nothing.** It is a pure read-only query layer. Every write goes through `PatternDB` in `portguard/pattern_db.py`.

### Database schema (9 migrations)

| Table | Primary key | Purpose |
|-------|-------------|---------|
| `schema_migrations` | `migration_name` | Migration tracking |
| `shipment_history` | `analysis_id` (UUID) | Per-analysis snapshot |
| `pattern_outcomes` | `outcome_id` (auto) | Officer verdicts |
| `shipper_profiles` | `(organization_id, shipper_key)` | Bayesian shipper reputation |
| `consignee_profiles` | `(organization_id, consignee_key)` | Bayesian consignee reputation |
| `route_risk_profiles` | `(organization_id, route_key)` | Bayesian route fraud rate |
| `hs_code_baselines` | `(organization_id, hs_prefix)` | Welford running stats for unit value |
| `bulk_batches` | `batch_id` | Bulk upload batch state |
| `bulk_shipments` | `(batch_id, shipment_ref)` | Per-row bulk results |

### `shipment_history` columns of note

```
analysis_id, analyzed_at, organization_id
shipper_name, shipper_key, consignee_name, consignee_key
origin_iso2, port_of_entry, route_key
hs_codes (JSON array), hs_chapter_primary
declared_value_usd, quantity, unit_value_usd
rule_risk_score, rule_decision, rule_confidence
rules_fired (JSON), inconsistency_count, missing_field_count
pattern_score, pattern_shipper_score, pattern_consignee_score
pattern_route_score, pattern_value_z_score, pattern_flag_frequency
pattern_history_depth, pattern_cold_start
final_risk_score, final_decision, final_confidence
report_payload (full AnalyzeResponse JSON for PDF generation)
sustainability_grade, sustainability_signals, active_modules_snapshot, module_findings
```

### `shipper_profiles` / `consignee_profiles` columns of note

```
organization_id, shipper_key / consignee_key, shipper_name / consignee_name
first_seen_at, last_seen_at
total_analyses, total_flagged, total_confirmed_fraud, total_cleared, total_unresolved
weighted_analyses, weighted_flagged, weighted_confirmed_fraud, weighted_cleared
reputation_score   ← Bayesian Beta: α/(α+β) where α = weighted_fraud+1, β = weighted_cleared+5
is_trusted, trust_set_at, trust_set_by
```

### `route_risk_profiles` columns of note

```
organization_id, route_key (origin_iso2||"→"||port_of_entry)
origin_iso2, port_of_entry
total_analyses, total_confirmed_fraud, weighted_analyses, weighted_confirmed_fraud
fraud_rate   ← Bayesian Beta: (w_fraud+0.5) / (w_analyses+1.0) [Jeffrey's prior]
```

### `hs_code_baselines` columns of note

```
organization_id, hs_prefix (first 6 chars of HTS code)
sample_count, running_mean, running_m2 (Welford), running_min/max, cached_stddev
```

---

## 2. Does `/api/v1/analyze` call `pattern_engine`? Where exactly?

**Yes.** `api/app.py`, lines 1739–1787.

### Call sequence within the analyze endpoint

```
POST /api/v1/analyze
  ↓
_validate_documents()           ← old validator, metadata only
_analyze_documents()            ← rule engine (risk score, decision, explanations)
  ↓
lines 1739–1764: PatternEngine.score()
  _build_scoring_request(sd, org_id)   ← always returns ScoringRequest (all fields optional)
  _pattern_engine.score(req)           ← read-only; queries shipper/consignee/route/hs profiles
  pattern_result.history_depth >= 1?
    YES → blend: 0.65 × rule_score + 0.35 × effective_pattern_score
    NO  → final_score = rule_score (rule-only; depth=0)
  ↓
lines 1769–1787: Confirmed-fraud hard flag
  _pattern_db.get_shipper_profile(exporter, org_id)
  _pattern_db.get_consignee_profile(importer, org_id)
  if total_confirmed_fraud > 0:
    prepend "⚠ CONFIRMED FRAUD HISTORY" signal
    if final_decision == "APPROVE": escalate → "REVIEW_RECOMMENDED"
  ↓
_record_shipment_bg()           ← writes to shipment_history + upserts profiles
analyze_response.pattern_history_depth = depth + 1  ← off-by-one correction
```

### `_build_scoring_request()` fields extracted from `sd`

| ScoringRequest field | Source in `sd` |
|---------------------|---------------|
| `shipper_name` | `sd["exporter"]` |
| `consignee_name` | `sd["importer"]` or `sd["consignee"]` |
| `origin_iso2` | `sd["origin_country_iso2"]` |
| `port_of_entry` | `sd["port_of_entry"]` or `sd["port_of_discharge"]` |
| `hs_codes` | `sd["hts_codes_declared"]` |
| `declared_value_usd` | `sd["declared_value"]` (cast to float) |
| `quantity` | `sd["quantity"]` (cast to float) |

The same pattern scoring block (with slight variable name differences) is duplicated in:
- `/api/v1/analyze` — lines 1739–1787
- `/api/v1/analyze-files` — lines 2046–2106
- `_run_bulk_single_analysis()` — lines 3260–3323

---

## 3. Does `risk_agent.py` read from `pattern_engine`?

**No.** `portguard/agents/risk.py` is a pure rule-based agent. Its only imports are:

```python
from portguard.data.section301 import get_section_301
from portguard.data.sanctions import get_sanctions_programs
from portguard.data.adcvd import get_adcvd_orders
from portguard.models.shipment import ParsedShipment
from portguard.models.classification import ClassificationResult
from portguard.models.risk import RiskAssessment, RiskFactor, RiskType, RiskSeverity
```

No reference to `PatternEngine`, `PatternDB`, `PatternScoreResult`, or any pattern module.

The pattern overlay is applied in `app.py` AFTER `_analyze_documents()` returns, not inside any agent.

---

## 4. What does the frontend show in the Pattern Intelligence panel? What API calls?

### Pattern Intelligence card (`#pattern-section`)

**Visibility:** hidden by default; shown when `data.pattern_score != null` OR `data.pattern_signals.length > 0`.  
Since `pattern_score` is always non-null after the first analysis (engine returns ~0.25 for brand-new entities), the card always shows after the first submission.

**Contents:**

| Element | Source field | Renders |
|---------|-------------|---------|
| `#phi-dot` | `data.history_available` + `data.pattern_history_depth` | CSS class: `active` (depth≥3) or `building` (cold start) |
| `#phi-text` | same | "Scoring active — based on N prior shipments" / "Building confidence — N shipment(s) in history (M more for full scoring)" / "Pattern learning started — this analysis has been recorded" |
| Pattern Score gauge | `data.pattern_score` | SVG arc, 0.0–1.0, colored: ≤0.25 green / ≤0.50 amber / ≤0.75 orange / >0.75 red |
| `#signal-list` | `data.pattern_signals` (list of strings) | Signal cards with severity-colored dot. Severity inferred from keywords in the explanation text. |

**Score label tiers in gauge:**
- ≤ 0.25 → `LOW` (green)
- ≤ 0.50 → `MEDIUM` (amber)
- ≤ 0.75 → `HIGH` (orange)
- > 0.75 → `CRITICAL` (red)

**After the first analysis with a brand-new entity:**
- `pattern_score ≈ 0.25` (cold-start neutral: 0.50 × 0.5)
- `pattern_signals = ["Insufficient history for pattern analysis."]`
- `history_depth = 1` (post-recording, with sprint B fix)
- `history_available = False`
- phi-text: "Building confidence — 1 shipment in history (2 more for full scoring)"

### Officer Feedback section (`#feedback-section`)

**Visibility:** only shown when `data.shipment_id` is non-null AND `data.decision` is one of `FLAG_FOR_INSPECTION`, `REVIEW_RECOMMENDED`, `REJECT`.

**Buttons:**
- "Confirmed Fraud" → `POST /api/v1/feedback` `{shipment_id, outcome: "CONFIRMED_FRAUD"}`
- "Cleared — Legitimate" → `POST /api/v1/feedback` `{shipment_id, outcome: "CLEARED"}`

**API call format:**
```javascript
fetch(apiUrl() + '/api/v1/feedback', authedJson('POST', { shipment_id: _lastShipmentId, outcome }))
```

### Pattern History panel (`#ph-panel`)

**API calls made by this panel:**

| Action | Method + URL | Response fields used |
|--------|-------------|---------------------|
| Load stats | `GET /api/v1/pattern-history` | `total_shipments`, `total_confirmed_fraud`, `top_riskiest_shippers`, `top_riskiest_routes` |
| Reset modal count | `GET /api/v1/pattern-history` | `total_shipments` |
| Reset history | `DELETE /api/v1/pattern-history/reset` `{confirm: true}` | `success`, `message` |

**What `/api/v1/pattern-history` actually returns** (from `PatternDB.get_summary_stats()`):
```json
{
  "total_shipments": <int>,
  "total_confirmed_fraud": <int>,
  "top_riskiest_shippers": [
    {"name": "...", "reputation_score": 0.0–1.0, "total_analyses": int, "confirmed_fraud_count": int}
  ],
  "top_riskiest_routes": [
    {"origin_iso2": "CN", "port_of_entry": "Los Angeles", "fraud_rate": 0.0–1.0, "total_analyses": int}
  ]
}
```

Frontend reads all of these correctly — field names match.

---

## 5. The exact bug: why did "Building history — first shipment recorded" always show?

### Root cause (pre-Sprint B)

The sequence in the analyze endpoint was:

```
1. PatternEngine.score() runs       ← reads DB, gets history_depth = N
2. AnalyzeResponse built with       ← pattern_history_depth = N   (snapshot before write)
3. _record_shipment_bg() runs       ← DB depth becomes N+1
4. analyze_response.shipment_id = ...
5. return analyze_response          ← still sends depth = N
```

On the **first ever analysis**: `N = 0`, response sends `depth = 0`.  
UI logic (pre-sprint B):
```javascript
textEl.textContent = depth > 0
  ? `Building history — ${depth} shipment(s) analyzed so far`
  : 'Building history — first shipment recorded';   // ← fires when depth === 0
```

This showed "first shipment recorded" with `depth=0` even though the recording was completing inline before the response was sent. The text implied past tense ("recorded") but the score was computed before the write.

On the **second analysis**: `N=1`, response sent `depth=1` → correct text. But on every first analysis for any new shipper entity, the depth would be 0 → "first shipment recorded" again, even if the org had other shippers' history.

### Post-Sprint B fix

After `_record_shipment_bg()` succeeds:
```python
if shipment_id is not None and pattern_history_depth_val is not None:
    analyze_response.pattern_history_depth = pattern_history_depth_val + 1
```

And the UI text was updated to three states:
- `depth > 0, !history_available` → "Building confidence — N shipment(s) in history (M more for full scoring)"
- `depth === 0, !history_available` → "Pattern learning started — this analysis has been recorded"
- `history_available` → "Scoring active — based on N prior shipments"

### Residual edge case

If `_record_shipment_bg()` returns `None` (DB failure), the `+1` increment is skipped, `depth` stays at the pre-recording value. If this is the first analysis and `depth=0`, the fallback text "Pattern learning started — this analysis has been recorded" shows even though recording failed. This is technically a lie but is a non-fatal edge case — the primary concern was the normal path.

---

## 6. What signal types are currently being stored?

### Signals computed by `PatternEngine.score()` (read from DB)

| Signal class | Algorithm | DB source | Composite weight |
|-------------|-----------|-----------|-----------------|
| `ShipperRiskSignal` | 0.60 × Bayesian Beta reputation + 0.40 × sigmoid flag frequency | `shipper_profiles` + `shipment_history` | 30% |
| `ConsigneeRiskSignal` | Same algorithm, independent profile | `consignee_profiles` + `shipment_history` | 20% |
| `RouteRiskSignal` | Bayesian Beta P(fraud\|route), Jeffrey's prior α₀=β₀=0.5 | `route_risk_profiles` | 20% |
| `ValueAnomalySignal` | z-score vs HS-code baseline; only undervaluation (z < −1.0) scores nonzero | `hs_code_baselines` | 15% |
| `FrequencyAnomalySignal` | Poisson tail P(X ≥ k) for shipper+consignee pair in 7-day rolling window | `shipment_history` | 15% |

**Composite:** `pattern_score = 0.30×shipper + 0.20×consignee + 0.20×route + 0.15×flag_freq + 0.15×value_anomaly`

**Cold-start damping:** if `history_depth < 3`, `effective_pattern_score = pattern_score × 0.5`

### Signals stored to `shipment_history` per analysis

| Column | Populated? | Source |
|--------|-----------|--------|
| `pattern_score` | ✅ | `pattern_result.pattern_score` |
| `pattern_shipper_score` | ✅ | `pattern_result.shipper_score` |
| `pattern_consignee_score` | ✅ | `pattern_result.consignee_score` |
| `pattern_route_score` | ✅ | `pattern_result.route_score` |
| `pattern_value_z_score` | ❌ **NEVER POPULATED** | Set to `None` in `ShipmentFingerprint` — z-score is computed by engine but not stored |
| `pattern_flag_frequency` | ✅ | `pattern_result.frequency_score` |
| `pattern_history_depth` | ✅ | `pattern_result.history_depth` (pre-recording) |
| `pattern_cold_start` | ✅ | `not history_available` |

### What profile tables store after `record_outcome()`

| Feedback type | Effect on shipper/consignee profile | Effect on route profile |
|--------------|-------------------------------------|------------------------|
| `CONFIRMED_FRAUD` | `weighted_confirmed_fraud += decay_weight(now)` → higher α → higher `reputation_score` | `weighted_confirmed_fraud += decay_weight(now)` → higher `fraud_rate` |
| `CLEARED` | `weighted_cleared += decay_weight(now)` → higher β → lower `reputation_score`; ≥20 weighted clears + 0 fraud → `is_trusted = 1` | `weighted_cleared += decay_weight(now)` → lower `fraud_rate` |
| `UNRESOLVED` | No change to reputation | No change to fraud rate |

---

## 7. What feedback endpoints exist?

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/v1/feedback` | `POST` | JWT Bearer | Submit officer verdict (CONFIRMED_FRAUD / CLEARED / UNRESOLVED) for a shipment |
| `/api/v1/pattern-history` | `GET` | JWT Bearer | Aggregate stats + top-5 risky shippers + top-5 risky routes |
| `/api/v1/pattern-history/reset` | `DELETE` | JWT Bearer | Permanently erase all pattern learning data for the org |

**`POST /api/v1/feedback` request body:**
```json
{
  "shipment_id": "<analysis_id UUID>",
  "outcome": "CONFIRMED_FRAUD" | "CLEARED" | "UNRESOLVED",
  "officer_notes": "<optional string>",
  "case_reference": "<optional string>"
}
```

**`POST /api/v1/feedback` effect chain:**
```
record_outcome(analysis_id, outcome, org_id)
  → _apply_fraud_outcome() or _apply_cleared_outcome()
    → _apply_entity_fraud/cleared() on shipper + consignee profiles
    → _apply_route_fraud/cleared() on route profile
    → INSERT INTO pattern_outcomes
```

No GET endpoint for feedback history per shipment.  
No endpoint to list all shipments with their outcomes.

---

## Known gaps / open issues after Sprint B

| # | Issue | Severity | Impact |
|---|-------|----------|--------|
| 1 | `pattern_value_z_score` never stored | Low | Column always NULL; analytics can't use it |
| 2 | `history_available` semantic inconsistency | Medium | Blending fires at depth≥1, but "active" dot only shows at depth≥3. UI implies "not yet active" while pattern IS influencing the score. |
| 3 | Pattern signals don't appear in main Findings panel | Medium | If fraud flag escalates APPROVE→REVIEW, the officer sees a changed decision but no explanation in the standard Findings section |
| 4 | Feedback UI only shows for FLAGGED decisions | Low | An APPROVE that has a fraud flag is escalated to REVIEW so it's fine, but officers can't provide CLEARED feedback on low-risk shipments to build trust |
| 5 | No per-shipment history detail in Pattern History panel | Low | Panel only shows aggregate stats; no way to see recent analysis timeline |
| 6 | Feedback endpoint accepts `UNRESOLVED` but UI never sends it | Low | Dead code path in the UI |
| 7 | Confirmed-fraud flag doesn't escalate beyond REVIEW_RECOMMENDED | Medium | Could argument for FLAG_FOR_INSPECTION or REJECT for repeat offenders |
