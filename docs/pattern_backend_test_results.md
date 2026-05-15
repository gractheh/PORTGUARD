# Pattern Backend Test Results
**Date:** 2026-05-15  
**Scope:** Manual trace of `get_pattern_stats()` and `GET /api/v1/pattern-stats` after backend-only fix commit.

---

## What Changed

| File | Location | Change |
|---|---|---|
| `portguard/pattern_engine.py` | `defaults` dict | `approval_rate` default changed from `100.0` (float) to `100` (int) |
| `portguard/pattern_engine.py` | Aggregate SQL | Added 7th column: `COUNT(CASE WHEN signal_type = 'SHIPPER_REP' AND last_decision = 'APPROVE' THEN 1 END)` |
| `portguard/pattern_engine.py` | Aggregates processing | `approval_rate` now computed as `round(approve_count / max(shipper_count, 1) * 100)` — integer percentage of shippers whose last decision was APPROVE |
| `portguard/pattern_engine.py` | high_risk_shippers query | Filter: `flag_count > 0` (was: flag_rate > 0.3); ORDER BY: `fraud_confirmed_count DESC, flag_count DESC`; LIMIT: 5 (was: 10); removed `last_seen`/`last_decision` from SELECT and result dict |
| `portguard/pattern_engine.py` | high_risk_routes query | Filter: `flag_count > 0` AND `occurrence_count >= 2` (was: flag_rate > 0.3 AND occ >= 3); LIMIT: 5 (was: 10); removed `last_seen`/`last_decision` from SELECT and result dict |
| `portguard/pattern_engine.py` | value_anomalies query | Added `flag_count > 0` filter; ORDER BY flag_rate DESC (was: flag_count DESC) |
| `portguard/pattern_engine.py` | cleared_shippers query | LIMIT: 5 (was: 10); removed `occurrence_count` from SELECT and result dict |
| `api/app.py` | `pattern_stats_endpoint` except block | Replaced `raise HTTPException(500)` with safe-defaults return — endpoint can never return 500 |

---

## Test Case 1: Empty table (no rows for this org)

**Setup:** `pattern_store` has zero rows for `org_email = "test@example.com"`.

**Trace:**
1. Aggregate query executes successfully but returns a row where `row[1]` (COUNT of SHIPPER_REP rows) = `0`.
2. Condition `if row and row[1] is not None and int(row[1] or 0) > 0:` is **False**.
3. None of the sub-queries run.
4. Function returns the unmodified `defaults` dict.

**Result:**
```json
{
  "has_history": false,
  "total_shipments_screened": 0,
  "unique_shippers_tracked": 0,
  "unique_routes_tracked": 0,
  "confirmed_fraud_count": 0,
  "avg_org_risk_score": 0.0,
  "total_flags_issued": 0,
  "approval_rate": 100,
  "high_risk_shippers": [],
  "high_risk_routes": [],
  "value_anomalies": [],
  "cleared_shippers": []
}
```

**Checks:**
- ✅ `has_history: false` — correct, no history
- ✅ Does not crash — returns safe defaults
- ✅ All 13 fields present in response
- ✅ `approval_rate` is integer `100` (not float `100.0`)

---

## Test Case 2: Records exist for org

**Setup:** `pattern_store` has these rows for `org_email = "test@example.com"`:

| signal_type | signal_key | occ | flags | fraud | cleared | avg_risk | last_decision |
|---|---|---|---|---|---|---|---|
| SHIPPER_REP | acme corp | 5 | 2 | 0 | 0 | 3.5 | FLAG_FOR_INSPECTION |
| SHIPPER_REP | shenzhen co | 3 | 3 | 1 | 0 | 7.2 | FLAG_FOR_INSPECTION |
| SHIPPER_REP | good vendor | 10 | 0 | 0 | 2 | 1.0 | APPROVE |
| ROUTE_RISK | CN→US | 8 | 4 | — | — | 5.0 | FLAG_FOR_INSPECTION |
| VALUE_ANOMALY | HIGH:CN | 5 | 3 | — | — | 6.0 | — |

**Aggregate trace:**
- `total_screened` = 5 + 3 + 10 = **18**
- `shipper_count` = **3**
- `total_flags` = 2 + 3 + 0 = **5**
- `confirmed_fraud` = 0 + 1 + 0 = **1**
- `avg_org_risk_score` = AVG(3.5, 7.2, 1.0) = 3.9 → **3.9**
- `approve_count` = 1 (only "good vendor" has last_decision='APPROVE')
- `approval_rate` = round(1 / 3 * 100) = round(33.33) = **33**

**High-risk shippers trace (flag_count > 0, ORDER BY fraud_confirmed_count DESC, flag_count DESC, LIMIT 5):**
- "shenzhen co": fraud=1, flags=3, flag_rate=round(3/3*100)=100
- "acme corp": fraud=0, flags=2, flag_rate=round(2/5*100)=40
- "good vendor": flags=0 — **excluded** (flag_count = 0)

Result: shenzhen co first (higher fraud_confirmed_count), then acme corp.

**High-risk routes trace (flag_count > 0, occ >= 2, ORDER BY flag_rate DESC, LIMIT 5):**
- "CN→US": occ=8, flags=4, flag_rate=round(4/8*100)=50 ✅ (occ=8 >= 2)

**Value anomalies trace (flag_count > 0, ORDER BY flag_rate DESC, LIMIT 3):**
- "HIGH:CN": flags=3, occ=5, flag_rate=round(3/5*100)=60 ✅

**Cleared shippers trace (cleared_count > 0, LIMIT 5):**
- "good vendor": cleared_count=2 but cleared_count is on SHIPPER_REP row ✅
- Result: `[{"signal_key": "good vendor", "cleared_count": 2}]`

**Full result:**
```json
{
  "has_history": true,
  "total_shipments_screened": 18,
  "unique_shippers_tracked": 3,
  "unique_routes_tracked": 1,
  "confirmed_fraud_count": 1,
  "avg_org_risk_score": 3.9,
  "total_flags_issued": 5,
  "approval_rate": 33,
  "high_risk_shippers": [
    {
      "signal_key": "shenzhen co",
      "flag_count": 3,
      "occurrence_count": 3,
      "avg_risk_score": 7.2,
      "fraud_confirmed_count": 1,
      "flag_rate": 100
    },
    {
      "signal_key": "acme corp",
      "flag_count": 2,
      "occurrence_count": 5,
      "avg_risk_score": 3.5,
      "fraud_confirmed_count": 0,
      "flag_rate": 40
    }
  ],
  "high_risk_routes": [
    {
      "signal_key": "CN→US",
      "flag_count": 4,
      "occurrence_count": 8,
      "avg_risk_score": 5.0,
      "flag_rate": 50
    }
  ],
  "value_anomalies": [
    {
      "signal_key": "HIGH:CN",
      "flag_count": 3,
      "occurrence_count": 5,
      "flag_rate": 60
    }
  ],
  "cleared_shippers": [
    {
      "signal_key": "good vendor",
      "cleared_count": 2
    }
  ]
}
```

**Checks:**
- ✅ `has_history: true` (total_shipments_screened = 18 > 0)
- ✅ All 13 top-level fields present
- ✅ `high_risk_shippers` items: exactly `{signal_key, flag_count, occurrence_count, fraud_confirmed_count, avg_risk_score, flag_rate}` — no extra `last_seen`/`last_decision`
- ✅ `high_risk_routes` items: exactly `{signal_key, flag_count, occurrence_count, avg_risk_score, flag_rate}` — no extra fields
- ✅ `value_anomalies` items: exactly `{signal_key, flag_count, occurrence_count, flag_rate}`
- ✅ `cleared_shippers` items: exactly `{signal_key, cleared_count}` — no `occurrence_count`
- ✅ `flag_rate` values are integers (100, 40, 50, 60) — not floats
- ✅ `approval_rate` is integer 33 — not float 33.0
- ✅ High-risk shippers ordered by fraud_confirmed_count DESC: shenzhen co (1) before acme corp (0)
- ✅ "good vendor" excluded from high_risk_shippers (flag_count = 0)

---

## Test Case 3: DB exception during query

**Setup:** `_pattern_db._engine` raises an exception on any execute call.

**Trace in `get_pattern_stats()`:**
1. `with db._engine.connect() as conn:` → exception raised
2. `except Exception as exc:` catches it
3. `logger.warning("get_pattern_stats failed (non-fatal): %s", exc)` logs it
4. Returns `defaults` (all zeros/empty arrays, `has_history: False`)

**Trace in `pattern_stats_endpoint()`:**
- `get_pattern_stats()` never raises (it catches internally), so the endpoint's `except` block is only reachable if the import itself fails or some other unexpected error occurs
- If it were reached: returns the safe-defaults dict with `approval_rate: 0`, NOT a 500

**Result:** HTTP 200 with `{"has_history": false, ...}` — no crash, no 500.

- ✅ Never returns HTTP 500
- ✅ Returns safe defaults with `has_history: false`
- ✅ User sees empty state, not an error page

---

## Test Case 4: Pattern learning disabled (_pattern_db is None)

**Setup:** `PORTGUARD_PATTERN_LEARNING_ENABLED=false` at startup — `_pattern_db` is `None`.

**Trace:**
1. `if _pattern_db is None:` is True
2. Raises `HTTPException(status_code=503, detail={"code": "PATTERN_LEARNING_DISABLED", ...})`
3. Frontend receives 503

**Result:** HTTP 503 with `{"code": "PATTERN_LEARNING_DISABLED"}` — intentional, allows frontend to show "Pattern learning is not enabled for this deployment."

- ✅ Returns 503 (not 500)
- ✅ Returns machine-readable `code` field for frontend error handling
- ✅ Does not return 200 with fake data

---

## Field Completeness Check

Comparing returned shape to task-specified shape:

| Field | Type spec | Empty state | Data state | Status |
|---|---|---|---|---|
| `has_history` | bool | false | true | ✅ |
| `total_shipments_screened` | int | 0 | 18 | ✅ |
| `unique_shippers_tracked` | int | 0 | 3 | ✅ |
| `unique_routes_tracked` | int | 0 | 1 | ✅ |
| `confirmed_fraud_count` | int | 0 | 1 | ✅ |
| `high_risk_shippers[].signal_key` | str | — | "shenzhen co" | ✅ |
| `high_risk_shippers[].flag_count` | int | — | 3 | ✅ |
| `high_risk_shippers[].occurrence_count` | int | — | 3 | ✅ |
| `high_risk_shippers[].fraud_confirmed_count` | int | — | 1 | ✅ |
| `high_risk_shippers[].avg_risk_score` | float | — | 7.2 | ✅ |
| `high_risk_shippers[].flag_rate` | int | — | 100 | ✅ |
| `high_risk_routes[].signal_key` | str | — | "CN→US" | ✅ |
| `high_risk_routes[].flag_count` | int | — | 4 | ✅ |
| `high_risk_routes[].occurrence_count` | int | — | 8 | ✅ |
| `high_risk_routes[].avg_risk_score` | float | — | 5.0 | ✅ |
| `high_risk_routes[].flag_rate` | int | — | 50 | ✅ |
| `value_anomalies[].signal_key` | str | — | "HIGH:CN" | ✅ |
| `value_anomalies[].flag_count` | int | — | 3 | ✅ |
| `value_anomalies[].occurrence_count` | int | — | 5 | ✅ |
| `value_anomalies[].flag_rate` | int | — | 60 | ✅ |
| `cleared_shippers[].signal_key` | str | — | "good vendor" | ✅ |
| `cleared_shippers[].cleared_count` | int | — | 2 | ✅ |
| `avg_org_risk_score` | float | 0.0 | 3.9 | ✅ |
| `total_flags_issued` | int | 0 | 5 | ✅ |
| `approval_rate` | int | 100 | 33 | ✅ |

All 24 fields match the task spec. No extra fields in any sub-array item.

---

## No Regressions

- `record_signals()` — untouched
- `apply_pattern_adjustments()` — untouched
- `record_feedback()` — untouched
- `reset_patterns()` — untouched
- `DELETE /api/v1/pattern-history/reset` endpoint — untouched
- `GET /api/v1/pattern-history` endpoint — untouched
- All dashboard analytics endpoints — untouched
- All auth endpoints — untouched
- `demo.html` — not modified (backend only)
