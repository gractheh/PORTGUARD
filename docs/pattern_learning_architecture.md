# PORTGUARD — Localized Pattern Learning System
## Technical Architecture Document

**Version:** 1.0  
**Status:** Design — pre-implementation  
**Scope:** Local, single-tenant pattern learning with no external data sharing

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Data Model](#2-data-model)
3. [Pattern Detection Engine](#3-pattern-detection-engine)
4. [Feedback Loop](#4-feedback-loop)
5. [Integration with Existing Pipeline](#5-integration-with-existing-pipeline)
6. [Storage — SQLite](#6-storage--sqlite)
7. [Privacy and Isolation](#7-privacy-and-isolation)
8. [API Surface](#8-api-surface)
9. [Cold Start Strategy](#9-cold-start-strategy)
10. [Operational Considerations](#10-operational-considerations)

---

## 1. System Overview

The Localized Pattern Learning (LPL) system adds an adaptive intelligence layer on top of PORTGUARD's deterministic rule engine. Where the rule engine fires on known regulatory facts (333 Section 301 HTS prefixes, 10 OFAC programs, 14 AD/CVD orders), the LPL system fires on emergent behavioral patterns discovered from the company's own shipment history.

**The core value proposition:** the rule engine catches what is *known to be illegal*. The LPL system surfaces what is *statistically unusual in this company's experience* — and over time, those two signals converge on the same fraud patterns, often before the rule engine is updated.

### Design Principles

**Local-first, never shared.** Every company's pattern data lives in a SQLite file on their own infrastructure. No telemetry, no centralized model, no data leaves the instance.

**Additive, not replacing.** Pattern scores augment the deterministic risk score. They cannot reduce a high rule-based score. A zero-history company gets full rule-based coverage from day one.

**Auditable by design.** Every score emitted by the pattern engine can be traced to the specific historical events that produced it. Officers can always explain why a score moved.

**Conservative priors.** The system starts from a presumption of innocence. A new shipper is not penalized for having no history. Scores drift toward risk only as evidence accumulates.

---

## 2. Data Model

### 2.1 Entity Relationship Overview

```
shipment_analyses  ──< analysis_rule_firings
       │
       └──< shipment_outcomes  (1:1, one outcome per analysis)

shipper_profiles   (updated by analyses + outcomes)
consignee_profiles (updated by analyses + outcomes)
route_profiles     (updated by analyses + outcomes)
hs_value_stats     (updated by analyses — declared value distributions per HS prefix)
```

---

### 2.2 Table: `shipment_analyses`

The canonical record for every analysis run. One row per call to `POST /api/v1/analyze`.

```sql
CREATE TABLE shipment_analyses (
    -- Identity
    analysis_id         TEXT PRIMARY KEY,         -- UUID v4
    analyzed_at         TEXT NOT NULL,            -- ISO-8601 UTC timestamp

    -- Shipment fingerprint
    shipper_name        TEXT,                     -- normalized (see §2.7)
    shipper_key         TEXT,                     -- SHA-256(normalized_shipper_name)
    consignee_name      TEXT,
    consignee_key       TEXT,                     -- SHA-256(normalized_consignee_name)
    origin_iso2         TEXT,                     -- 2-letter country code
    destination_iso2    TEXT,                     -- always "US" for now
    port_of_entry       TEXT,                     -- e.g. "Los Angeles"
    route_key           TEXT,                     -- origin_iso2 + "|" + port_of_entry
    carrier             TEXT,
    vessel_name         TEXT,
    hs_codes            TEXT NOT NULL,            -- JSON array: ["8471.30.0100", "8542.31"]
    hs_chapter_primary  TEXT,                     -- first 2 digits of first HTS code
    declared_value_usd  REAL,
    quantity            REAL,
    unit_value_usd      REAL,                     -- declared_value_usd / quantity (NULL if either absent)
    gross_weight_kg     REAL,
    incoterms           TEXT,
    documents_present   TEXT,                     -- JSON array of filenames

    -- Rule engine output (snapshot at decision time)
    rule_risk_score     REAL NOT NULL,            -- output of _compute_score(), range [0.0, 1.0]
    rule_decision       TEXT NOT NULL,            -- APPROVE | REVIEW_RECOMMENDED | FLAG_FOR_INSPECTION
                                                  -- REQUEST_MORE_INFORMATION | REJECT
    rule_confidence     TEXT NOT NULL,            -- HIGH | MEDIUM | LOW
    rules_fired         TEXT NOT NULL,            -- JSON array of {type, severity, score} objects
    inconsistency_count INTEGER NOT NULL DEFAULT 0,
    missing_field_count INTEGER NOT NULL DEFAULT 0,

    -- Pattern engine output (snapshot at decision time; NULL if insufficient history)
    pattern_score              REAL,              -- [0.0, 1.0] or NULL
    pattern_shipper_score      REAL,              -- reputation score at time of analysis
    pattern_consignee_score    REAL,
    pattern_route_score        REAL,
    pattern_value_z_score      REAL,             -- z-score of unit_value vs HS history
    pattern_flag_frequency     REAL,             -- recent flag rate for this shipper
    pattern_history_depth      INTEGER,          -- how many prior analyses informed the scores
    pattern_cold_start         INTEGER NOT NULL DEFAULT 1,  -- 1 = cold start applied

    -- Final blended output
    final_risk_score    REAL NOT NULL,           -- blended score submitted to decision
    final_decision      TEXT NOT NULL,           -- decision after pattern injection
    final_confidence    TEXT NOT NULL
);

CREATE INDEX idx_analyses_shipper_key   ON shipment_analyses(shipper_key, analyzed_at);
CREATE INDEX idx_analyses_consignee_key ON shipment_analyses(consignee_key, analyzed_at);
CREATE INDEX idx_analyses_route_key     ON shipment_analyses(route_key, analyzed_at);
CREATE INDEX idx_analyses_hs_primary    ON shipment_analyses(hs_chapter_primary, analyzed_at);
CREATE INDEX idx_analyses_analyzed_at   ON shipment_analyses(analyzed_at);
```

**Design notes:**
- `shipper_key` and `consignee_key` are SHA-256 hashes of the normalized entity name. The plain-text name is also stored for officer use, but all pattern queries use the key — this allows an optional privacy mode where names are dropped after recording (see §7).
- `hs_codes` stores the full array as JSON so no schema change is needed when multi-HTS shipments arrive.
- Snapshotting both `rule_*` and `pattern_*` separately preserves the audit trail: you can always reconstruct what the rule engine said before pattern learning adjusted the output.

---

### 2.3 Table: `analysis_rule_firings`

Individual rule contributions, normalized out of `shipment_analyses` for efficient aggregation.

```sql
CREATE TABLE analysis_rule_firings (
    id              INTEGER PRIMARY KEY,
    analysis_id     TEXT NOT NULL REFERENCES shipment_analyses(analysis_id) ON DELETE CASCADE,
    rule_type       TEXT NOT NULL,    -- SANCTIONS | SECTION_301 | TRANSSHIPMENT | ADCVD |
                                      -- UNDERVALUATION | VAGUE_DESCRIPTION | NEGOTIABLE_BL |
                                      -- SECTORAL_SANCTIONS | PATTERN_SHIPPER | PATTERN_ROUTE | etc.
    severity        TEXT NOT NULL,    -- CRITICAL | HIGH | MEDIUM | LOW
    score_contrib   REAL NOT NULL,    -- this rule's contribution to total risk score
    detail          TEXT              -- human-readable explanation (first 500 chars)
);

CREATE INDEX idx_rule_firings_analysis ON analysis_rule_firings(analysis_id);
CREATE INDEX idx_rule_firings_type     ON analysis_rule_firings(rule_type, severity);
```

---

### 2.4 Table: `shipment_outcomes`

Officer-recorded outcomes for flagged shipments. One row per analysis, created when an officer submits feedback.

```sql
CREATE TABLE shipment_outcomes (
    outcome_id      INTEGER PRIMARY KEY,
    analysis_id     TEXT NOT NULL UNIQUE REFERENCES shipment_analyses(analysis_id) ON DELETE CASCADE,
    recorded_at     TEXT NOT NULL,              -- ISO-8601 UTC
    officer_id      TEXT,                       -- optional; who recorded it
    outcome         TEXT NOT NULL,              -- CONFIRMED_FRAUD | CLEARED | UNRESOLVED
    outcome_notes   TEXT,                       -- free text; evidence, case number, etc.
    case_reference  TEXT                        -- optional external case/seizure number
);

CREATE INDEX idx_outcomes_analysis ON shipment_outcomes(analysis_id);
CREATE INDEX idx_outcomes_outcome  ON shipment_outcomes(outcome, recorded_at);
```

**Outcome semantics:**
- `CONFIRMED_FRAUD` — the flag was correct; evidence of fraud, smuggling, sanctions violation, or customs fraud was confirmed. Increases risk weight for the shipper/consignee/route.
- `CLEARED` — the flag was a false positive; shipment was legitimate. Increases trust weight. Prevents the same legitimate shipper from accumulating erroneous risk.
- `UNRESOLVED` — the investigation is ongoing or evidence was inconclusive. Stored but excluded from score updates until resolved.

---

### 2.5 Table: `shipper_profiles`

Running statistical profile for each shipper entity. Updated after every analysis and every outcome recording.

```sql
CREATE TABLE shipper_profiles (
    shipper_key             TEXT PRIMARY KEY,    -- SHA-256 of normalized name
    shipper_name            TEXT,               -- most recent plain-text form seen
    first_seen_at           TEXT NOT NULL,
    last_seen_at            TEXT NOT NULL,

    -- Raw counts (unweighted, for reference)
    total_analyses          INTEGER NOT NULL DEFAULT 0,
    total_flagged           INTEGER NOT NULL DEFAULT 0,  -- any decision != APPROVE
    total_confirmed_fraud   INTEGER NOT NULL DEFAULT 0,
    total_cleared           INTEGER NOT NULL DEFAULT 0,
    total_unresolved        INTEGER NOT NULL DEFAULT 0,

    -- Decay-weighted counts (used for live score computation)
    -- Updated as a running sum; each count = sum of exp(-λ * days_ago)
    weighted_analyses       REAL NOT NULL DEFAULT 0.0,
    weighted_flagged        REAL NOT NULL DEFAULT 0.0,
    weighted_confirmed_fraud REAL NOT NULL DEFAULT 0.0,
    weighted_cleared        REAL NOT NULL DEFAULT 0.0,

    -- Bayesian reputation score [0.0 = no risk, 1.0 = confirmed fraudster]
    -- alpha = weighted_confirmed_fraud + 1 (prior)
    -- beta  = weighted_cleared + 1 (prior)
    -- score = alpha / (alpha + beta)
    reputation_score        REAL NOT NULL DEFAULT 0.5,

    -- Metadata
    last_score_update       TEXT NOT NULL,
    is_trusted              INTEGER NOT NULL DEFAULT 0,  -- manually set by officers
    trust_set_at            TEXT,
    trust_set_by            TEXT
);
```

---

### 2.6 Table: `consignee_profiles`

Identical structure to `shipper_profiles`, keyed on `consignee_key`.

```sql
CREATE TABLE consignee_profiles (
    consignee_key           TEXT PRIMARY KEY,
    consignee_name          TEXT,
    first_seen_at           TEXT NOT NULL,
    last_seen_at            TEXT NOT NULL,
    total_analyses          INTEGER NOT NULL DEFAULT 0,
    total_flagged           INTEGER NOT NULL DEFAULT 0,
    total_confirmed_fraud   INTEGER NOT NULL DEFAULT 0,
    total_cleared           INTEGER NOT NULL DEFAULT 0,
    total_unresolved        INTEGER NOT NULL DEFAULT 0,
    weighted_analyses       REAL NOT NULL DEFAULT 0.0,
    weighted_flagged        REAL NOT NULL DEFAULT 0.0,
    weighted_confirmed_fraud REAL NOT NULL DEFAULT 0.0,
    weighted_cleared        REAL NOT NULL DEFAULT 0.0,
    reputation_score        REAL NOT NULL DEFAULT 0.5,
    last_score_update       TEXT NOT NULL,
    is_trusted              INTEGER NOT NULL DEFAULT 0,
    trust_set_at            TEXT,
    trust_set_by            TEXT
);
```

---

### 2.7 Table: `route_profiles`

Risk profile for each origin-country → port-of-entry lane.

```sql
CREATE TABLE route_profiles (
    route_key               TEXT PRIMARY KEY,   -- e.g. "CN|Los Angeles"
    origin_iso2             TEXT NOT NULL,
    port_of_entry           TEXT NOT NULL,
    first_seen_at           TEXT NOT NULL,
    last_seen_at            TEXT NOT NULL,
    total_analyses          INTEGER NOT NULL DEFAULT 0,
    total_flagged           INTEGER NOT NULL DEFAULT 0,
    total_confirmed_fraud   INTEGER NOT NULL DEFAULT 0,
    total_cleared           INTEGER NOT NULL DEFAULT 0,
    weighted_analyses       REAL NOT NULL DEFAULT 0.0,
    weighted_flagged        REAL NOT NULL DEFAULT 0.0,
    weighted_confirmed_fraud REAL NOT NULL DEFAULT 0.0,
    weighted_cleared        REAL NOT NULL DEFAULT 0.0,
    -- Bayesian fraud rate: P(fraud | this route) with conservative priors
    -- alpha = weighted_confirmed_fraud + 0.5 (Jeffrey's prior)
    -- beta  = (weighted_analyses - weighted_confirmed_fraud) + 0.5
    -- rate  = alpha / (alpha + beta)
    fraud_rate              REAL NOT NULL DEFAULT 0.05,   -- starts at prior 5%
    last_score_update       TEXT NOT NULL
);
```

**Design note on route vs. shipper profiles:** Route risk captures systemic corridor-level risk (e.g., a particular origin port has an elevated fraud rate across many shippers). Shipper risk captures entity-specific behavior. Both are computed independently and combined at injection time (§5).

---

### 2.8 Table: `hs_value_stats`

Running distribution of declared unit values per HS code prefix. Used for anomaly detection.

```sql
CREATE TABLE hs_value_stats (
    hs_prefix           TEXT PRIMARY KEY,    -- 6-digit prefix, e.g. "8471.30"
    sample_count        INTEGER NOT NULL DEFAULT 0,
    -- Welford's online algorithm state for running mean/variance
    -- (avoids storing all historical values)
    running_mean        REAL NOT NULL DEFAULT 0.0,
    running_m2          REAL NOT NULL DEFAULT 0.0,    -- sum of squared deviations
    running_min         REAL,
    running_max         REAL,
    last_updated        TEXT NOT NULL,
    -- Derived (recomputed on each update)
    -- variance = running_m2 / (sample_count - 1) when sample_count > 1
    -- stddev   = sqrt(variance)
    -- z_score for a new value v = (v - running_mean) / stddev
    cached_stddev       REAL                           -- NULL until sample_count >= 2
);
```

**Welford's algorithm** computes exact running mean and variance in a single pass with O(1) memory regardless of history length — no need to store individual transaction values. The variance is numerically stable even for very large sample counts. This is the standard online algorithm used in statistics and is well-suited for incremental updates after each analysis.

---

### 2.9 Entity Name Normalization

Before any hashing or profile lookup, entity names are normalized to reduce false splits between the same real-world entity.

**Normalization pipeline:**
1. Unicode normalize to NFKD, encode to ASCII with `errors='ignore'`
2. Lowercase
3. Strip punctuation except hyphens
4. Remove common legal suffixes: `ltd`, `limited`, `co`, `corp`, `corporation`, `inc`, `incorporated`, `llc`, `pte`, `sa`, `gmbh`, `bv`, `nv`, `srl`, `pty`, `plc`
5. Collapse whitespace
6. Strip leading/trailing whitespace
7. SHA-256 the result for the `_key` field

**Example:**
```
"Viet Star Electronics Manufacturing Co., Ltd." 
  → "viet star electronics manufacturing"
  → SHA-256 → "a3f9..."
```

This means `Viet Star Electronics Mfg Co Ltd` and `Viet Star Electronics Manufacturing Co., Ltd.` resolve to the same profile. The normalization is deterministic, so the key is stable across sessions.

---

## 3. Pattern Detection Engine

The pattern detection engine is a read-only query layer. It takes a shipment fingerprint extracted from the current analysis and returns a `PatternScores` object. It does not write to the database — all writes happen in the feedback loop (§4) and the analysis recorder.

### 3.1 Temporal Decay

All frequency calculations use exponential decay to weight recent events more heavily than old ones.

**Decay function:**
```
weight(event) = exp(-λ × days_since_event)
```

Where `λ = 0.023`, giving a **30-day half-life**: an event 30 days ago has 50% weight; an event 90 days ago has 12.5% weight; an event 6 months ago has ~1.5% weight.

This means a shipper that was legitimate for 3 years but had one fraud episode 6 months ago is not permanently branded. Conversely, a burst of fraud activity in the past month dominates the score.

The `weighted_*` columns in the profile tables are maintained as running sums where each historical event's contribution decays daily. On each new analysis, the running sums are recomputed by applying the decay to all events since the last update time. This is computed incrementally: `new_weighted = old_weighted × exp(-λ × days_elapsed)` — one multiplication per profile per analysis, not a scan of all historical events.

---

### 3.2 Shipper Flag Frequency Score

**Input:** `shipper_key`, current timestamp  
**Output:** `flag_frequency_score` ∈ [0.0, 1.0]

**Algorithm:**
1. Query `shipment_analyses` for all rows matching `shipper_key` in the past 90 days
2. Compute decay-weighted count of all analyses: `W_total = Σ exp(-λ × days_i)`
3. Compute decay-weighted count of flagged analyses (decision != APPROVE): `W_flagged = Σ exp(-λ × days_i)` for flagged only
4. Flag rate: `rate = W_flagged / W_total` (0 if W_total = 0)
5. Apply a sigmoid amplifier to increase sensitivity at moderate rates:
   ```
   flag_frequency_score = 1 / (1 + exp(-8 × (rate - 0.4)))
   ```
   This maps:
   - 0% flag rate → ~0.018 (near zero)
   - 20% flag rate → ~0.12 (low signal)
   - 40% flag rate → ~0.50 (midpoint — meaningful)
   - 60% flag rate → ~0.88 (strong signal)
   - 80%+ flag rate → ~0.98 (near certain)

**Threshold triggers (used in explanation generation, not score computation):**
- ≥2 flags in 7 days: `FREQUENT_FLAGS_7D`
- ≥3 flags in 30 days: `FREQUENT_FLAGS_30D`
- ≥5 flags in 90 days: `FREQUENT_FLAGS_90D`

These trigger human-readable explanations like: *"This shipper has been flagged 3 times in the past 30 days (decay-weighted flag rate: 62%)."*

---

### 3.3 Declared Value Anomaly Score

**Input:** `hs_prefix` (6-digit), `unit_value_usd`  
**Output:** `value_anomaly_score` ∈ [0.0, 1.0], `z_score` ∈ ℝ

**Algorithm:**
1. Retrieve `running_mean`, `cached_stddev`, `sample_count` for `hs_prefix` from `hs_value_stats`
2. If `sample_count < 10` or `cached_stddev IS NULL` or `cached_stddev = 0`: return `score=0.0, z_score=NULL` (insufficient history)
3. Compute z-score: `z = (unit_value_usd - running_mean) / cached_stddev`
4. Map to score — only low values are anomalous (undervaluation); high values are not penalized:
   ```
   if z >= -1.0:   value_anomaly_score = 0.0       # within 1 stddev, no signal
   if z < -1.0:    value_anomaly_score = min(1.0, (-z - 1.0) / 3.0)
   ```
   This maps:
   - z = -1.0 → 0.00 (no anomaly)
   - z = -2.0 → 0.33
   - z = -3.0 → 0.67
   - z = -4.0 → 1.00 (extreme undervaluation)

**Integration with existing rule engine:** The current `api/app.py` already has static commodity benchmarks (`_VALUE_BENCHMARKS`). The pattern engine's value anomaly is *orthogonal* — it computes anomaly relative to *this company's historical data for this HS code*, not against global benchmarks. Both signals are emitted separately and combined at injection time.

---

### 3.4 Route Risk Score

**Input:** `route_key` (origin_iso2 + "|" + port_of_entry)  
**Output:** `route_risk_score` ∈ [0.0, 1.0]

**Algorithm:** Bayesian Beta distribution with Jeffrey's prior (α₀ = β₀ = 0.5).

```
α = weighted_confirmed_fraud + 0.5
β = (weighted_analyses - weighted_confirmed_fraud) + 0.5
route_risk_score = α / (α + β)
```

**Behavior:**
- New route (no data): α = 0.5, β = 0.5 → score = 0.50. This is intentionally neutral — a new route is not presumed risky or safe.
- Route with 100 analyses, 0 confirmed fraud: α = 0.5, β = 100.5 → score = 0.005 (near zero)
- Route with 10 analyses, 3 confirmed fraud: α = 3.5, β = 7.5 → score = 0.318 (moderate risk)
- Route with 50 analyses, 30 confirmed fraud: α = 30.5, β = 20.5 → score = 0.598 (high risk)

**Why Jeffrey's prior over Laplace smoothing:** Jeffrey's prior (0.5, 0.5) is the non-informative prior for a Binomial proportion — it makes no assumption about the base rate. Laplace smoothing (1, 1) implicitly assumes a 50% base rate, which would bias new routes toward high risk too aggressively.

---

### 3.5 Shipper Reputation Score

**Input:** `shipper_key`  
**Output:** `shipper_reputation_score` ∈ [0.0, 1.0]

**Algorithm:** Bayesian Beta with informative innocent prior (α₀ = 1, β₀ = 5 — assumes 1 in 6 probability of being problematic for a brand-new entity).

```
α = weighted_confirmed_fraud + 1
β = weighted_cleared + 5
shipper_reputation_score = α / (α + β)
```

**Behavior:**
- Unknown shipper: α = 1, β = 5 → score = 0.167 (slight caution for unknown entities)
- 10 clears, 0 frauds: α = 1, β = 15 → score = 0.063 (trusted)
- 0 clears, 3 frauds: α = 4, β = 5 → score = 0.444 (concerning)
- 0 clears, 8 frauds: α = 9, β = 5 → score = 0.643 (high risk)
- 5 clears, 5 frauds: α = 6, β = 10 → score = 0.375 (mixed history, moderate risk)

**Trusted shipper override:** Officers can manually set `is_trusted = 1` on a profile. When set, `shipper_reputation_score` is hard-clamped to `0.0`, regardless of computed value. This is used for known-good partners (e.g., large established importers). Trust flags are logged with timestamp and officer ID for audit purposes.

---

### 3.6 Consignee Reputation Score

Identical algorithm to §3.5 applied to `consignee_profiles`. Consignees and shippers are scored independently because a legitimate consignee may receive goods from a fraudulent shipper, or a legitimate shipper may ship to an unusual consignee.

---

### 3.7 Composite Pattern Score

The five signals above are combined into a single `pattern_score` using a weighted sum, then clamped to [0.0, 1.0]:

```
pattern_score = (
    0.30 × shipper_reputation_score    +
    0.20 × consignee_reputation_score  +
    0.20 × route_risk_score            +
    0.15 × flag_frequency_score        +
    0.15 × value_anomaly_score
)
```

**Weight rationale:**
- Shipper gets the largest weight (0.30) because shipper behavior is the most direct indicator of intent.
- Consignee gets 0.20 — significant but secondary; consignees have less control over document fraud.
- Route gets 0.20 — captures systemic corridor risk that transcends individual entities.
- Flag frequency (0.15) is a leading indicator before outcomes are recorded.
- Value anomaly (0.15) is the most objective signal but also the noisiest on low sample counts; kept conservative.

**Minimum history guard:** If `pattern_history_depth < 3` for the shipper (i.e., fewer than 3 prior analyses for this entity), the system sets `pattern_cold_start = 1` and reduces the pattern score contribution by 50%:

```
if pattern_cold_start:
    effective_pattern_score = pattern_score * 0.5
```

This prevents a single prior flagged shipment from aggressively penalizing what might be a legitimate new entrant.

---

## 4. Feedback Loop

### 4.1 Recording an Outcome

Officers access a dedicated endpoint to record the result of an investigation. This is the only write path for outcome data.

```
POST /api/v1/analyses/{analysis_id}/outcome
Body: {
    "outcome": "CONFIRMED_FRAUD" | "CLEARED" | "UNRESOLVED",
    "officer_id": "string (optional)",
    "notes": "string (optional)",
    "case_reference": "string (optional)"
}
```

**Preconditions checked before writing:**
1. `analysis_id` must exist in `shipment_analyses`
2. If an outcome already exists: reject with `409 Conflict` unless the existing outcome is `UNRESOLVED` (resolved outcomes are immutable — fraud findings cannot be walked back without an explicit override endpoint)
3. `outcome` must be one of the three valid values

---

### 4.2 Profile Updates After CONFIRMED_FRAUD

When `outcome = CONFIRMED_FRAUD` is recorded for `analysis_id`:

**Step 1 — Retrieve the analysis snapshot:**
Load `shipper_key`, `consignee_key`, `route_key`, `hs_codes`, `unit_value_usd`, `analyzed_at` from `shipment_analyses`.

**Step 2 — Update shipper profile:**
```
days_ago = (now - analyzed_at).days
decay_weight = exp(-0.023 × days_ago)

weighted_confirmed_fraud += decay_weight
weighted_flagged += decay_weight   (if not already counted)
total_confirmed_fraud += 1

α = weighted_confirmed_fraud + 1
β = weighted_cleared + 5
reputation_score = α / (α + β)
last_score_update = now
```

**Step 3 — Update consignee profile:** Same computation on `consignee_profiles`.

**Step 4 — Update route profile:**
```
weighted_confirmed_fraud += decay_weight
fraud_rate = (weighted_confirmed_fraud + 0.5) / (weighted_analyses + 1.0)
```

**Step 5 — Update HS value stats** (fraud-informed tightening):
If the fraudulent shipment had a known `unit_value_usd`, add it to the Welford running stats for its `hs_prefix`. This is correct behavior — fraudulent undervaluation samples should be included in the historical distribution because they represent real market prices that CBP observed; the z-score will naturally detect future undervaluation.

---

### 4.3 Profile Updates After CLEARED

When `outcome = CLEARED` is recorded:

**Step 1 — Update shipper profile:**
```
decay_weight = exp(-0.023 × days_since_analysis)

weighted_cleared += decay_weight
total_cleared += 1

α = weighted_confirmed_fraud + 1
β = weighted_cleared + 5          -- β increases; score drops toward 0
reputation_score = α / (α + β)
```

**Step 2 — Update consignee profile:** Same.

**Step 3 — Route profile:** Cleared shipments do **not** update the route's fraud rate. A cleared flag means the individual shipment was fine, but it doesn't exonerate the route. Route risk reflects the systemic rate of confirmed fraud, not the flag rate.

**Step 4 — Flag frequency mitigation:** The cleared analysis's flag is retrospectively excluded from future `flag_frequency_score` calculations for this shipper. This is implemented by marking the `shipment_analyses` row with a `outcome_cleared` flag and excluding it from the frequency query's denominator. Without this, repeatedly clearing a false-positive pattern would not reduce future false-positive rates.

**Step 5 — Auto-trust threshold:** If a shipper accumulates `weighted_cleared >= 20` and `weighted_confirmed_fraud = 0`, automatically set `is_trusted = 1` on the shipper profile and log it as `trust_set_by = "system_auto"`. This rewards consistently legitimate shippers with reduced scrutiny and prevents alert fatigue.

---

### 4.4 Idempotency and Audit Integrity

**Outcomes are append-only. Analyses are immutable.** Once an analysis is recorded, its `rule_risk_score`, `rule_decision`, and `pattern_score` fields are never updated. The snapshot captured at decision time is the permanent record.

This means:
- A compliance auditor can always see exactly what the system knew and decided at the time of each analysis.
- Score changes caused by new outcomes only affect *future* analyses, never retroactively alter historical records.
- The only exception: `UNRESOLVED` outcomes can be updated to `CONFIRMED_FRAUD` or `CLEARED`. All other transitions are blocked.

---

## 5. Integration with Existing Pipeline

### 5.1 Injection Point

Pattern scores are injected **after `_assess_risk()` and before `_compute_score()`** in `api/app.py`.

Current flow:
```
_extract_shipment_data()
    ↓
_find_inconsistencies()
    ↓
_check_missing_fields()
    ↓
_assess_risk()                ← rule-based factors computed here
    ↓
_compute_score()              ← weighted sum of rule factors
    ↓
_make_decision()
```

New flow with LPL:
```
_extract_shipment_data()
    ↓
_find_inconsistencies()
    ↓
_check_missing_fields()
    ↓
_assess_risk()                ← unchanged; rule factors computed here
    ↓
PatternEngine.score()         ← NEW: queries pattern DB, returns PatternScores
    ↓
_compute_blended_score()      ← NEW: replaces _compute_score(); combines both signals
    ↓
_make_decision()              ← unchanged; operates on final blended score
    ↓
PatternEngine.record()        ← NEW: writes analysis snapshot to DB (async, non-blocking)
```

For the `portguard/` structured pipeline, `PatternAgent` becomes Stage 4.5 — inserted between `RiskAgent` and `DecisionAgent`. It takes `ParsedShipment + RiskAssessment` as input and emits `PatternScores` which `DecisionAgent.decide()` receives as an additional parameter.

---

### 5.2 Score Combination: Additive with Ceiling

**Formula:**
```
pattern_contribution = pattern_score × PATTERN_WEIGHT × (1 - cold_start_penalty)

final_risk_score = min(1.0,
    rule_risk_score + pattern_contribution
)
```

Where:
- `PATTERN_WEIGHT = 0.35` — the maximum fraction by which pattern learning can increase the risk score
- `cold_start_penalty = 0.5` if `pattern_history_depth < 3`, else `0.0`

**Why additive, not multiplicative:**
- Multiplicative (`rule × (1 + k × pattern)`) is undesirable because a rule score near zero (clean shipment by all rules) multiplied by even a high pattern score produces a very small final score — pattern learning would have almost no effect on clean-looking but historically suspicious shipments.
- Additive means pattern learning can *elevate* a shipment's score even when the rule engine finds nothing, which is precisely the case for novel fraud patterns that haven't been codified into rules yet.

**Why a ceiling (not uncapped):**
- Pattern learning cannot push a score above 1.0.
- A high pattern score for a shipment that the rule engine sees as clean should surface as `REVIEW_RECOMMENDED`, not `REJECT`. The `REJECT` decision is reserved for confirmed sanctions violations detected by the rule engine.

**Example scenarios:**

| Rule Score | Pattern Score | Pattern Contribution | Final Score | Decision |
|---|---|---|---|---|
| 0.00 | 0.00 | 0.000 | 0.00 | APPROVE |
| 0.12 | 0.00 | 0.000 | 0.12 | APPROVE |
| 0.12 | 0.75 | 0.263 | 0.38 | REVIEW_RECOMMENDED |
| 0.00 | 0.85 | 0.298 | 0.30 | REVIEW_RECOMMENDED |
| 0.55 | 0.80 | 0.280 | 0.83 | FLAG_FOR_INSPECTION |
| 0.90 | 0.90 | 0.315 | 1.00 (capped) | REJECT (from rule) |

---

### 5.3 Pattern Factors as Explicit Risk Factors

Pattern scores are surfaced as first-class `RiskFactor` objects (matching the existing `RiskFactor` model in `portguard/models/risk.py`) with a new `RiskType.PATTERN_LEARNING` enum value. This ensures:
- They appear in `explanations` alongside rule-based findings
- They are stored in `analysis_rule_firings`
- Officers can see exactly which pattern signal drove the score up

**Example pattern-generated RiskFactor:**
```json
{
    "risk_type": "PATTERN_LEARNING",
    "severity": "MEDIUM",
    "description": "Shipper 'Guangzhou Apex Trading Ltd' has been flagged in 3 of 5 analyses
                    in the past 30 days (decay-weighted flag rate: 58%). Reputation score: 0.61.
                    History depth: 5 analyses.",
    "additional_duty_rate": null,
    "regulatory_reference": "PORTGUARD Local Pattern Learning — internal risk signal",
    "recommended_action": "Cross-reference against OFAC SDN and BIS Entity List.
                           Request additional documentation before release."
}
```

---

### 5.4 API Response Changes

The `AnalyzeResponse` model gains new optional fields:

```python
class PatternScores(BaseModel):
    pattern_score:          float | None = None     # composite [0.0, 1.0]
    shipper_score:          float | None = None
    consignee_score:        float | None = None
    route_score:            float | None = None
    value_z_score:          float | None = None
    flag_frequency_score:   float | None = None
    history_depth:          int = 0
    cold_start:             bool = True

class AnalyzeResponse(BaseModel):
    # ... existing fields unchanged ...
    rule_risk_score:        float            # NEW: deterministic score only
    pattern_scores:         PatternScores    # NEW: pattern breakdown
    # risk_score remains the blended final score (backwards compatible)
```

`rule_risk_score` is new but additive — `risk_score` retains its meaning as the actionable final score, maintaining backwards compatibility with existing clients.

---

## 6. Storage — SQLite

### 6.1 Why SQLite

| Requirement | SQLite | Postgres |
|---|---|---|
| Zero-config deployment | ✓ | ✗ (server process) |
| Single-file backup | ✓ | ✗ |
| ACID per write | ✓ | ✓ |
| Concurrent reads | ✓ (WAL mode) | ✓ |
| Concurrent writes | Sequential (one writer at a time) | ✓ |
| Python stdlib | ✓ (no dep) | ✗ (psycopg2) |
| Scale to 10M rows | ✓ | ✓ |
| On-prem, no cloud | ✓ | ✓ |

For the target use case (a single-process compliance API handling hundreds to low thousands of analyses per day), SQLite's sequential write model is not a bottleneck. SQLite with WAL mode handles concurrent reads (health checks, score queries) without blocking writes.

The schema is portable to Postgres with minimal changes if a customer's scale ever requires it — column types and SQL dialect are kept generic.

### 6.2 Database Location and Configuration

```python
# Default: ~/.portguard/patterns.db
# Override with env var:
PORTGUARD_DB_PATH = os.getenv("PORTGUARD_DB_PATH", "~/.portguard/patterns.db")
```

On first run, the directory is created and the schema is applied via embedded migration SQL. Schema version is tracked in a `schema_migrations` table; migrations are applied idempotently on startup.

### 6.3 Connection Configuration

```python
# Applied on every new connection:
PRAGMA journal_mode = WAL;          -- enables concurrent reads during writes
PRAGMA foreign_keys = ON;           -- enforces referential integrity
PRAGMA synchronous = NORMAL;        -- durable without fsync on every write
PRAGMA cache_size = -32000;         -- 32 MB page cache
PRAGMA temp_store = MEMORY;         -- temp tables in memory
PRAGMA mmap_size = 268435456;       -- 256 MB memory-mapped I/O
```

### 6.4 Write Performance

Pattern analysis recording is **fire-and-forget** relative to the HTTP response. The sequence is:

1. `POST /api/v1/analyze` is received
2. Compliance analysis runs (synchronous, <50ms)
3. Pattern scores are queried from DB (synchronous, <5ms)
4. HTTP response is returned to client
5. Analysis snapshot is written to DB (async background task via FastAPI's `BackgroundTasks`)

This means DB write latency never adds to client-perceived response time. If the DB write fails (e.g., disk full), it is logged but does not fail the HTTP response — analysis always completes even if learning does not persist.

### 6.5 Data Retention Policy

A configurable retention window prevents unbounded growth:

```python
PORTGUARD_RETENTION_DAYS = int(os.getenv("PORTGUARD_RETENTION_DAYS", "730"))  # 2 years default
```

A background job runs on server startup and every 24 hours thereafter, deleting `shipment_analyses` rows older than `RETENTION_DAYS` (cascading to `analysis_rule_firings` and `shipment_outcomes`). Profile tables (`shipper_profiles`, `route_profiles`, etc.) are **not** deleted by retention — the aggregate statistics persist even after individual analysis records age out. This preserves learned trust/risk scores while preventing unbounded growth of the analyses table.

---

## 7. Privacy and Isolation

### 7.1 Single-Tenant Architecture

Each company installation runs its own PORTGUARD instance with its own SQLite file. There is no shared database, no centralized model, and no cross-company data aggregation by design. The architecture is explicitly single-tenant at the data layer.

**What this means in practice:**
- A shipper who is flagged at Company A's PORTGUARD instance has no effect on Company B's instance.
- A compliance officer at Company B cannot query Company A's data.
- There is no "network effect" benefit from shared learning — this is intentional. Trade data is competitively sensitive and legally protected in many jurisdictions.

### 7.2 Data Minimization

The pattern learning system stores only the minimum fields necessary for score computation:

**Stored:**
- Entity names (normalized) and their SHA-256 keys
- HTS codes
- Declared values and quantities
- Route information (origin country, port of entry)
- Decisions and outcomes

**Not stored:**
- Full document text (remains in memory only during analysis)
- Individual names of people (importer officers, signatories, agents)
- Bank account numbers or payment details
- Any data element not directly used for pattern computation

### 7.3 Optional Name Pseudonymization

For customers with heightened privacy requirements, an opt-in mode stores entity names in hashed form only:

```python
PORTGUARD_PSEUDONYMIZE_NAMES = os.getenv("PORTGUARD_PSEUDONYMIZE_NAMES", "false")
```

When enabled:
- `shipper_name` and `consignee_name` in `shipment_analyses` are set to `NULL` after the profile key is computed
- `shipper_name` in `shipper_profiles` is set to `"[pseudonymized]"`
- Pattern scoring still functions fully via the SHA-256 key
- Officers lose the ability to see entity names in the pattern history UI (they see only the decision and score)

This mode is suitable when compliance staff should see risk scores and decisions but not raw counterparty data (segregation of duties).

### 7.4 GDPR/CCPA Compliance Support

Two endpoints support data subject rights:

```
GET  /api/v1/privacy/shipper/{shipper_key}/export
     Returns all analyses and outcomes associated with this shipper key as JSON.

DELETE /api/v1/privacy/shipper/{shipper_key}
       Deletes all analyses, outcomes, and profile data for this shipper.
       Requires officer_id in request body for audit logging.
       This operation is irreversible.
```

**Note:** Shipper keys are SHA-256 hashes. Fulfilling a GDPR right-to-erasure request requires knowing the plaintext name to compute the key — the requestor provides their own name to identify their data.

### 7.5 Database Access Control

The SQLite file's Unix permissions are set to `600` (owner read/write only) on creation. The process runs as a dedicated service account. No other system users have read access to the file.

If deployed in Docker, the database file should be on a named volume, not a bind-mounted host path, to prevent accidental exposure via the host filesystem.

---

## 8. API Surface

### 8.1 New Endpoints

```
POST /api/v1/analyze
     MODIFIED: response now includes pattern_scores and rule_risk_score fields.
     Existing fields (risk_score, decision, explanations) are unchanged.

POST /api/v1/analyses/{analysis_id}/outcome
     NEW: Record an officer outcome for a flagged analysis.
     Body: { outcome, officer_id, notes, case_reference }
     Returns: 201 Created with the recorded outcome.

GET  /api/v1/analyses/{analysis_id}
     NEW: Retrieve a single analysis with its pattern scores and outcome (if any).
     Returns: analysis snapshot + PatternScores + ShipmentOutcome | null.

GET  /api/v1/analyses
     NEW: List recent analyses with filtering.
     Query params: decision, outcome, shipper_key, date_from, date_to, limit (max 100).

GET  /api/v1/patterns/shipper/{shipper_key}
     NEW: View the pattern profile for a shipper.
     Returns: ShipperProfile with reputation score, history stats, trust status.

POST /api/v1/patterns/shipper/{shipper_key}/trust
     NEW: Set or revoke trusted status for a shipper.
     Body: { trusted: true | false, officer_id, notes }

GET  /api/v1/patterns/route/{origin_iso2}/{port_of_entry}
     NEW: View the risk profile for a route.

GET  /api/v1/patterns/hs/{hs_prefix}/value-stats
     NEW: View declared value distribution for a 6-digit HS prefix.

GET  /api/v1/patterns/stats
     NEW: Dashboard summary — total analyses, outcomes pending, top flagged shippers.
```

### 8.2 Outcome Workflow

The intended officer workflow:

1. Analyst runs `POST /api/v1/analyze` and receives a `FLAG_FOR_INSPECTION` decision with `analysis_id`.
2. Officer reviews physical documents and investigates the shipment.
3. Officer calls `POST /api/v1/analyses/{analysis_id}/outcome` with result.
4. System updates profiles immediately; next analysis of the same shipper reflects updated scores.

There is no UI mandated — the endpoints are designed for integration into existing customs management workflows or a simple dashboard front-end.

---

## 9. Cold Start Strategy

### 9.1 The Problem

A new PORTGUARD installation has zero historical data. The pattern engine cannot produce meaningful scores. Any shipment has `pattern_history_depth = 0`.

### 9.2 Solution: Conservative Priors + Graceful Degradation

The system operates correctly with zero history. The cold start strategy has three phases:

**Phase 0 — First analysis (0 prior records):**
- `pattern_cold_start = true`
- `effective_pattern_score = 0.0` (no contribution)
- Rule-based score operates at full weight
- The analysis is recorded to DB; it becomes the first data point

**Phase 1 — Sparse history (1–9 prior analyses per entity):**
- `pattern_cold_start = true` (for entities with < 3 records)
- `effective_pattern_score = pattern_score × 0.5`
- Pattern contribution is present but conservative
- This prevents a single flagged analysis from aggressively penalizing the next

**Phase 2 — Established history (10+ analyses per entity):**
- `pattern_cold_start = false`
- Pattern scores operate at full weight
- HS value anomaly detection activates when `sample_count >= 10`

**Phase 3 — Mature system (50+ analyses per key entity):**
- Decay-weighted statistics are stable
- Reputation scores have converged away from priors
- Value anomaly detection is reliable

**For the route profiles specifically:** new routes start at a 50% neutral score (Jeffrey's prior). This is acceptable because routes are high-volume — a common trade lane like `CN|Los Angeles` will have many analyses within weeks and will quickly converge to its true fraud rate.

### 9.3 Seeding with Historical Data

If a company has historical shipment records in another system, they can seed the database via a bulk import endpoint:

```
POST /api/v1/patterns/import
Body: Array of { shipper_name, consignee_name, origin_iso2, port_of_entry,
                 hs_codes, unit_value_usd, analyzed_at, decision, outcome }
```

Seeding does not require `analysis_id` (UUIDs are generated). It applies the same normalization pipeline and increments profile statistics exactly as live analyses do. Seeded records are flagged with `source = "import"` for audit purposes.

---

## 10. Operational Considerations

### 10.1 Performance Budget

| Operation | Target Latency | Notes |
|---|---|---|
| Pattern score query | < 5ms | All queries are indexed point-lookups or small range scans |
| Analysis record write | < 10ms | Background task, not on critical path |
| Profile update (outcome) | < 20ms | Sequential writes to 3–4 tables |
| Retention job | < 2s | Runs during low-traffic window |

### 10.2 Monitoring

The following metrics should be exposed at `GET /api/v1/patterns/stats`:

- Total analyses in DB, analyses in last 24h / 7d / 30d
- Outcomes by type (CONFIRMED_FRAUD / CLEARED / UNRESOLVED / PENDING)
- Pending outcomes (flagged shipments with no outcome recorded yet)
- Average days-to-outcome (how quickly officers are resolving flags)
- Top 10 shippers by flag frequency in last 30 days
- Top 10 routes by fraud rate
- DB file size

### 10.3 Score Drift Detection

Over time, pattern weights can drift in undesirable directions if the feedback loop is underutilized (e.g., officers flag but don't record outcomes). The system detects this:

- If `pending_outcomes / total_flagged > 0.7` over the past 30 days: emit a warning in `GET /api/v1/patterns/stats` — *"Pattern learning effectiveness is reduced: 73% of flagged analyses have no outcome recorded. Outcome recording is required for the system to learn."*
- If no outcomes have been recorded in 60 days but analyses are occurring: emit a warning — *"No outcomes have been recorded in 60 days. Pattern scores are frozen at priors."*

### 10.4 Rollback and Disable

Pattern learning can be disabled entirely without code changes:

```python
PORTGUARD_PATTERN_LEARNING_ENABLED = os.getenv("PORTGUARD_PATTERN_LEARNING_ENABLED", "true")
```

When `false`:
- The pattern engine is not consulted
- `effective_pattern_score = 0.0` always
- Analyses are still recorded (the DB continues accumulating history)
- `pattern_scores` in the response is `null`
- The rule engine output is unaffected

This allows gradual rollout: enable recording first, accumulate data, then enable scoring once sufficient history exists.

### 10.5 Schema Migration Strategy

Schema versions are tracked in:

```sql
CREATE TABLE schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    description TEXT NOT NULL
);
```

On startup, the application checks the current version and applies any pending migrations in order. Migrations are embedded in the application code as versioned SQL strings. Downgrade migrations are not supported — forward-only migration matches SQLite's single-file, single-user model.

---

## Summary: Component Map

```
PORTGUARD Analysis Request
        │
        ▼
┌─────────────────────────────────────────┐
│           api/app.py                    │
│                                         │
│  _extract_shipment_data()               │
│  _find_inconsistencies()                │
│  _check_missing_fields()                │
│  _assess_risk()          ─── rule factors (unchanged)
│          │                              │
│          ▼                              │
│  PatternEngine.score()  ◄─── SQLite DB (read: profiles, hs_stats)
│          │                              │
│          ▼                              │
│  _compute_blended_score()               │
│  _make_decision()                       │
│          │                              │
│          ▼                              │
│  HTTP Response (with pattern_scores)    │
│          │                              │
│          ▼ (background)                 │
│  PatternEngine.record() ──► SQLite DB (write: analyses, rule_firings)
└─────────────────────────────────────────┘

Officer Feedback
        │
        ▼
POST /api/v1/analyses/{id}/outcome
        │
        ▼
PatternEngine.apply_outcome() ──► SQLite DB (write: outcomes, shipper/consignee/route profiles)
```

---

*Document ends. Implementation begins in the next sprint.*
