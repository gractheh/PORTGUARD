# PORTGUARD Analytics Dashboard — Technical Architecture

**Status:** Plan (pre-implementation)
**Scope:** `GET /api/v1/dashboard` backend endpoint + PatternDB method +
           Chart.js dashboard panel in `demo.html`
**Tenant isolation:** All queries are scoped to the authenticated organization's
`organization_id`, identical to the existing pattern-history endpoint.

---

## 1. DATA LAYER — SQL Queries Against Existing Schema

All queries run against `portguard_patterns.db`. The relevant tables and columns
confirmed by reading the schema:

```
shipment_history
  analysis_id, analyzed_at, shipper_name, shipper_key,
  consignee_name, consignee_key, origin_iso2, port_of_entry,
  hs_codes (JSON array string), hs_chapter_primary,
  rule_risk_score, rule_decision, final_risk_score, final_decision,
  final_confidence, pattern_score, pattern_cold_start,
  inconsistency_count, missing_field_count, organization_id

pattern_outcomes
  analysis_id, outcome, recorded_at, officer_id, organization_id
  outcome IN ('CONFIRMED_FRAUD', 'CLEARED', 'UNRESOLVED')

shipper_profiles
  organization_id, shipper_key, shipper_name,
  reputation_score, total_analyses, total_confirmed_fraud,
  total_cleared, is_trusted

route_risk_profiles
  organization_id, route_key, origin_iso2, port_of_entry,
  fraud_rate, total_analyses, total_confirmed_fraud
```

All queries below use parameterized `?` placeholders. `org_id` is always the
first parameter. `cutoff` is an ISO-8601 string for rolling-window queries
(`datetime.now(UTC) - timedelta(days=N)`).

---

### Metric 1 — Total shipments (all time + last N days)

```sql
-- All time
SELECT COUNT(*)
FROM shipment_history
WHERE organization_id = ?;

-- Last N days (default N=30)
SELECT COUNT(*)
FROM shipment_history
WHERE organization_id = ?
  AND analyzed_at >= ?;       -- cutoff = now - N days
```

**Return fields:** `total_all_time: int`, `total_last_n_days: int`

---

### Metric 2 — Decision breakdown (all time, scoped to org)

```sql
SELECT final_decision,
       COUNT(*) AS count
FROM shipment_history
WHERE organization_id = ?
GROUP BY final_decision
ORDER BY count DESC;
```

**Return fields:** `decision_breakdown: list[{decision: str, count: int}]`

Decision values in the data: `APPROVE`, `REVIEW_RECOMMENDED`, `FLAG_FOR_INSPECTION`,
`REQUEST_MORE_INFORMATION`, `REJECT`. Map these to display labels in the frontend.

---

### Metric 3 — Fraud rate trend (daily, last N days)

```sql
SELECT DATE(sh.analyzed_at)                              AS day,
       COUNT(DISTINCT sh.analysis_id)                    AS total_analyzed,
       COUNT(DISTINCT po.analysis_id)                    AS confirmed_fraud_count,
       CAST(COUNT(DISTINCT po.analysis_id) AS REAL)
         / NULLIF(COUNT(DISTINCT sh.analysis_id), 0)     AS fraud_rate
FROM shipment_history sh
LEFT JOIN pattern_outcomes po
       ON sh.analysis_id = po.analysis_id
      AND po.outcome = 'CONFIRMED_FRAUD'
      AND po.organization_id = ?
WHERE sh.organization_id = ?
  AND sh.analyzed_at >= ?                                -- cutoff
GROUP BY day
ORDER BY day ASC;
```

**Return fields:** `fraud_trend: list[{day: str, total: int, fraud_count: int, fraud_rate: float}]`

Note: days with zero activity are gaps in the series. The frontend fills missing
days with `null` (Chart.js `spanGaps: false` renders breaks as intended).

---

### Metric 4 — Average risk score trend (daily, last N days)

```sql
SELECT DATE(analyzed_at)          AS day,
       ROUND(AVG(final_risk_score), 4) AS avg_risk_score,
       COUNT(*)                    AS shipment_count
FROM shipment_history
WHERE organization_id = ?
  AND analyzed_at >= ?             -- cutoff
GROUP BY day
ORDER BY day ASC;
```

**Return fields:** `risk_trend: list[{day: str, avg_risk_score: float, shipment_count: int}]`

---

### Metric 5 — Top 10 riskiest shippers

```sql
SELECT shipper_name,
       reputation_score,
       total_analyses,
       total_confirmed_fraud,
       total_cleared,
       is_trusted
FROM shipper_profiles
WHERE organization_id = ?
  AND total_analyses >= 1
ORDER BY reputation_score DESC
LIMIT 10;
```

**Return fields:** `top_shippers: list[{name, reputation_score, total_analyses, confirmed_fraud, cleared, is_trusted}]`

Design note: `reputation_score` is the live Bayesian Beta score, already recomputed
on every `record_shipment()` call. No recomputation needed at query time.

---

### Metric 6 — Top 10 riskiest origin countries

```sql
SELECT sh.origin_iso2,
       COUNT(DISTINCT sh.analysis_id)                          AS total_shipments,
       COUNT(DISTINCT CASE WHEN po.outcome = 'CONFIRMED_FRAUD'
                           THEN po.analysis_id END)           AS confirmed_fraud_count,
       ROUND(AVG(sh.final_risk_score), 4)                      AS avg_risk_score,
       ROUND(
         CAST(COUNT(DISTINCT CASE WHEN po.outcome = 'CONFIRMED_FRAUD'
                                  THEN po.analysis_id END) AS REAL)
         / NULLIF(COUNT(DISTINCT sh.analysis_id), 0), 4)      AS fraud_rate
FROM shipment_history sh
LEFT JOIN pattern_outcomes po
       ON sh.analysis_id = po.analysis_id
      AND po.organization_id = ?
WHERE sh.organization_id = ?
  AND sh.origin_iso2 IS NOT NULL
GROUP BY sh.origin_iso2
ORDER BY confirmed_fraud_count DESC, avg_risk_score DESC
LIMIT 10;
```

**Return fields:** `top_countries: list[{iso2, total_shipments, confirmed_fraud_count, avg_risk_score, fraud_rate}]`

---

### Metric 7 — Top 10 most flagged HS chapters

```sql
SELECT hs_chapter_primary,
       COUNT(*)                                              AS total_shipments,
       COUNT(CASE WHEN final_decision != 'APPROVE' THEN 1 END) AS flagged_count,
       ROUND(AVG(final_risk_score), 4)                       AS avg_risk_score
FROM shipment_history
WHERE organization_id = ?
  AND hs_chapter_primary IS NOT NULL
GROUP BY hs_chapter_primary
ORDER BY flagged_count DESC, avg_risk_score DESC
LIMIT 10;
```

**Return fields:** `top_hs_chapters: list[{hs_chapter, total_shipments, flagged_count, avg_risk_score}]`

Note: `hs_chapter_primary` stores the leading 4-digit heading (e.g. `"8471"`,
`"8542"`). The frontend maps these to human-readable commodity names using a
small lookup table of common chapters (covered below in §4 Frontend).

---

### Metric 8 — Pattern learning growth (cumulative, daily)

```sql
SELECT DATE(analyzed_at) AS day,
       COUNT(*)           AS new_shipments
FROM shipment_history
WHERE organization_id = ?
ORDER BY day ASC;
```

**Return fields:** `learning_growth: list[{day: str, new_shipments: int}]`

The frontend computes the running cumulative total as a `reduce()` so the backend
stays stateless. This is safe because SQLite window functions (`SUM() OVER`) are
only available in SQLite ≥ 3.25.0 (2018), and we cannot assume that version on
all deployment targets without checking.

---

### Metric 9 — Confirmed fraud vs cleared ratio

```sql
SELECT outcome,
       COUNT(*) AS count
FROM pattern_outcomes
WHERE organization_id = ?
  AND outcome IN ('CONFIRMED_FRAUD', 'CLEARED')
GROUP BY outcome;
```

**Return fields:** `outcome_ratio: {confirmed_fraud: int, cleared: int}`

---

### Metric 10 — Recent shipment activity feed

```sql
SELECT sh.analysis_id,
       sh.analyzed_at,
       sh.shipper_name,
       sh.origin_iso2,
       sh.final_decision,
       ROUND(sh.final_risk_score, 4)  AS risk_score,
       sh.pattern_cold_start,
       po.outcome
FROM shipment_history sh
LEFT JOIN pattern_outcomes po
       ON sh.analysis_id = po.analysis_id
      AND po.organization_id = ?
WHERE sh.organization_id = ?
ORDER BY sh.analyzed_at DESC
LIMIT 20;
```

**Return fields:** `recent_activity: list[{id, analyzed_at, shipper, origin, decision, risk_score, cold_start, outcome}]`

`outcome` is `null` when no feedback has been submitted yet.

---

## 2. CHARTS — Visualization Specifications

All charts use **Chart.js 4.x loaded via CDN**. No build step, no npm install.

```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
```

One Chart.js instance per canvas. All instances stored in a `_charts` object so
they can be `.destroy()`ed cleanly before re-render (prevents canvas reuse errors).

---

### Chart A — Fraud Rate Trend (Line chart, primary)

**Metric:** `fraud_trend` + `risk_trend` overlaid as dual y-axis  
**Dataset 1:** Daily fraud rate (%) — left y-axis, scale 0–100%  
**Dataset 2:** Average risk score — right y-axis, scale 0.0–1.0

```javascript
{
  type: 'line',
  data: {
    labels: days,           // ['2026-03-12', '2026-03-13', ...]
    datasets: [
      {
        label: 'Daily Fraud Rate (%)',
        data: fraudRates,   // [0, 0, 33.3, 0, 50, ...]
        borderColor: '#ef4444',
        backgroundColor: 'rgba(239,68,68,0.08)',
        fill: true,
        tension: 0.3,
        spanGaps: false,
        yAxisID: 'y',
      },
      {
        label: 'Avg Risk Score',
        data: avgRiskScores, // [0.12, 0.45, 0.78, ...]
        borderColor: '#f97316',
        borderDash: [4, 4],
        fill: false,
        tension: 0.3,
        spanGaps: false,
        yAxisID: 'y2',
      }
    ]
  },
  options: {
    scales: {
      y:  { min: 0, max: 100, title: { text: 'Fraud Rate (%)' } },
      y2: { min: 0, max: 1.0, position: 'right', title: { text: 'Risk Score' } }
    },
    plugins: { legend: { position: 'top' } }
  }
}
```

---

### Chart B — Decision Breakdown (Donut chart)

**Metric:** `decision_breakdown`  
**Colors:** APPROVE → green, REVIEW_RECOMMENDED → blue,
FLAG_FOR_INSPECTION → orange, REQUEST_MORE_INFORMATION → amber, REJECT → red

```javascript
{
  type: 'doughnut',
  data: {
    labels: ['Approve', 'Review', 'Flag', 'More Info', 'Reject'],
    datasets: [{
      data: [approveCount, reviewCount, flagCount, moreInfoCount, rejectCount],
      backgroundColor: ['#22c55e','#3b82f6','#f97316','#eab308','#ef4444'],
      borderWidth: 2,
      borderColor: '#1a1a2e',
      hoverOffset: 8,
    }]
  },
  options: {
    cutout: '65%',
    plugins: {
      legend: { position: 'right' },
      tooltip: {
        callbacks: {
          label: (ctx) => {
            const pct = ((ctx.raw / total) * 100).toFixed(1);
            return `${ctx.label}: ${ctx.raw} (${pct}%)`;
          }
        }
      }
    }
  }
}
```

---

### Chart C — Top Origin Countries by Risk (Horizontal bar)

**Metric:** `top_countries`  
**X-axis:** confirmed fraud count (primary bar) + average risk score (secondary bar)  
**Sorted:** by confirmed_fraud_count DESC

```javascript
{
  type: 'bar',
  data: {
    labels: isoLabels,      // ['CN', 'VN', 'MY', ...] mapped to display names
    datasets: [
      {
        label: 'Confirmed Fraud Cases',
        data: fraudCounts,
        backgroundColor: '#ef4444',
        borderRadius: 4,
      },
      {
        label: 'Total Shipments',
        data: totalCounts,
        backgroundColor: 'rgba(99,102,241,0.3)',
        borderColor: '#6366f1',
        borderWidth: 1,
        borderRadius: 4,
      }
    ]
  },
  options: {
    indexAxis: 'y',
    plugins: { legend: { position: 'top' } },
    scales: { x: { stacked: false, beginAtZero: true } }
  }
}
```

---

### Chart D — Top Riskiest Shippers (Horizontal bar)

**Metric:** `top_shippers`  
**X-axis:** Bayesian reputation score (0.0–1.0)  
**Color coding:** score > 0.6 → red, 0.3–0.6 → orange, < 0.3 → yellow

```javascript
{
  type: 'bar',
  data: {
    labels: shipperNames,   // truncated to 25 chars + '…'
    datasets: [{
      label: 'Reputation Score (higher = riskier)',
      data: reputationScores,
      backgroundColor: reputationScores.map(s =>
        s > 0.6 ? '#ef4444' : s > 0.3 ? '#f97316' : '#eab308'
      ),
      borderRadius: 4,
    }]
  },
  options: {
    indexAxis: 'y',
    scales: { x: { min: 0, max: 1.0, title: { text: 'Reputation Score' } } },
    plugins: { legend: { display: false } }
  }
}
```

---

### Chart E — Pattern Learning Growth (Area line chart)

**Metric:** `learning_growth` (cumulative total computed in JS)

```javascript
{
  type: 'line',
  data: {
    labels: days,
    datasets: [{
      label: 'Total Shipments in History',
      data: cumulativeCounts,
      borderColor: '#6366f1',
      backgroundColor: 'rgba(99,102,241,0.1)',
      fill: true,
      tension: 0.2,
    }]
  },
  options: {
    scales: { y: { beginAtZero: true } },
    plugins: { legend: { display: false } }
  }
}
```

---

### Chart F — Top Flagged HS Chapters (Vertical bar)

**Metric:** `top_hs_chapters`

```javascript
{
  type: 'bar',
  data: {
    labels: chapterLabels,  // '8471 — ADP Machines', '8542 — ICs', ...
    datasets: [{
      label: 'Flagged Shipments',
      data: flaggedCounts,
      backgroundColor: '#f97316',
      borderRadius: 4,
    }, {
      label: 'Total Shipments',
      data: totalCounts,
      backgroundColor: 'rgba(156,163,175,0.3)',
      borderColor: '#9ca3af',
      borderWidth: 1,
      borderRadius: 4,
    }]
  },
  options: {
    scales: { y: { beginAtZero: true } },
    plugins: { legend: { position: 'top' } }
  }
}
```

---

### KPI Cards (No chart library — pure HTML)

Six stat cards rendered as styled `<div>` elements, updated via DOM manipulation:

| Card | Value | Source |
|---|---|---|
| Total Shipments | `total_all_time` | Metric 1 |
| Shipments (30d) | `total_last_n_days` | Metric 1 |
| Confirmed Fraud | `outcome_ratio.confirmed_fraud` | Metric 9 |
| Cleared | `outcome_ratio.cleared` | Metric 9 |
| Fraud / Cleared Ratio | `fraud / (fraud + cleared)` as % | Metric 9 |
| Active Learning | `learning_growth[-1].cumulative` | Metric 8 |

---

### Activity Feed (No chart library — HTML list)

A scrollable `<ul>` of the 20 most recent shipments. Each entry:
- Timestamp (relative: "3 hours ago") using a small helper
- Shipper name (truncated)
- Origin ISO2 flag emoji lookup (e.g. CN → 🇨🇳)
- Decision badge with color-coded pill (reuses existing demo.html badge CSS)
- Risk score gauge bar (inline `<div>` with `width: X%`)
- Outcome badge if feedback has been submitted (green CLEARED / red FRAUD)

---

## 3. BACKEND — New API Endpoint and PatternDB Method

### 3a. New PatternDB method: `get_dashboard_data()`

**File:** `portguard/pattern_db.py`  
**Location:** Add after `get_summary_stats()` (~line 1760)

```python
def get_dashboard_data(
    self,
    organization_id: str = "__system__",
    days: int = 30,
) -> dict:
    """Return all metrics needed by the analytics dashboard in one call.

    Parameters
    ----------
    organization_id:
        Tenant scope.
    days:
        Rolling window for trend data (default 30).  KPI totals always
        span all time.

    Returns
    -------
    dict with keys:
        summary           — total_all_time, total_last_n_days
        decision_breakdown — list[{decision, count}]
        fraud_trend        — list[{day, total, fraud_count, fraud_rate}]
        risk_trend         — list[{day, avg_risk_score, shipment_count}]
        top_shippers       — list[{name, reputation_score, total_analyses,
                                   confirmed_fraud, cleared, is_trusted}]
        top_countries      — list[{iso2, total_shipments, confirmed_fraud_count,
                                   avg_risk_score, fraud_rate}]
        top_hs_chapters    — list[{hs_chapter, total_shipments, flagged_count,
                                   avg_risk_score}]
        learning_growth    — list[{day, new_shipments}]
        outcome_ratio      — {confirmed_fraud, cleared, unresolved}
        recent_activity    — list[{id, analyzed_at, shipper, origin,
                                   decision, risk_score, cold_start, outcome}]
    """
```

All ten SQL queries from §1 are executed sequentially within this method.
No new indices required — all WHERE predicates match existing indices:
- `idx_history_org_id` covers `organization_id =` on `shipment_history`
- `idx_outcomes_org_id` covers `organization_id =` on `pattern_outcomes`
- `idx_history_analyzed_at` covers `analyzed_at >=` on `shipment_history`
- `idx_shipper_profiles_org` covers `organization_id =` on `shipper_profiles`
- `idx_route_profiles_org` covers `organization_id =` on `route_risk_profiles`

The `DATE(analyzed_at)` grouping in trend queries is unindexed but operates on
a bounded date range (30 rows maximum output), so a full-table scan over the
org-filtered rows is acceptable at demo scale.

---

### 3b. New FastAPI endpoint: `GET /api/v1/dashboard`

**File:** `api/app.py`  
**Location:** After the `GET /api/v1/pattern-history` endpoint (~line 1830)  
**Auth:** `current_org: dict = Depends(get_current_organization)` — protected

```
GET /api/v1/dashboard?days=30
```

**Query parameters:**

| Param | Type | Default | Constraint | Description |
|---|---|---|---|---|
| `days` | int | 30 | 1 ≤ days ≤ 365 | Rolling window for trend charts |

**Success response:** `200 OK`

```json
{
  "organization_id": "uuid",
  "generated_at": "2026-04-11T14:22:00Z",
  "window_days": 30,
  "summary": {
    "total_all_time": 142,
    "total_last_n_days": 37
  },
  "decision_breakdown": [
    { "decision": "APPROVE",            "count": 89 },
    { "decision": "FLAG_FOR_INSPECTION","count": 31 },
    { "decision": "REVIEW_RECOMMENDED", "count": 14 },
    { "decision": "REQUEST_MORE_INFORMATION", "count": 6 },
    { "decision": "REJECT",             "count": 2  }
  ],
  "fraud_trend": [
    { "day": "2026-03-12", "total": 4, "fraud_count": 1, "fraud_rate": 0.25 },
    ...
  ],
  "risk_trend": [
    { "day": "2026-03-12", "avg_risk_score": 0.42, "shipment_count": 4 },
    ...
  ],
  "top_shippers": [
    {
      "name": "Dragon Phoenix Trading Ltd",
      "reputation_score": 0.7143,
      "total_analyses": 12,
      "confirmed_fraud": 3,
      "cleared": 1,
      "is_trusted": false
    },
    ...
  ],
  "top_countries": [
    {
      "iso2": "CN",
      "total_shipments": 61,
      "confirmed_fraud_count": 8,
      "avg_risk_score": 0.56,
      "fraud_rate": 0.131
    },
    ...
  ],
  "top_hs_chapters": [
    {
      "hs_chapter": "8542",
      "total_shipments": 22,
      "flagged_count": 14,
      "avg_risk_score": 0.71
    },
    ...
  ],
  "learning_growth": [
    { "day": "2026-03-01", "new_shipments": 5 },
    ...
  ],
  "outcome_ratio": {
    "confirmed_fraud": 11,
    "cleared": 24,
    "unresolved": 3
  },
  "recent_activity": [
    {
      "id": "a3f7c2d1-...",
      "analyzed_at": "2026-04-11T14:21:55Z",
      "shipper": "Shenzhen Apex Imports Co",
      "origin": "CN",
      "decision": "FLAG_FOR_INSPECTION",
      "risk_score": 0.84,
      "cold_start": false,
      "outcome": "CONFIRMED_FRAUD"
    },
    ...
  ]
}
```

**Error responses:**

| HTTP | `code` | Cause |
|---|---|---|
| 401 | `MISSING_TOKEN` | No Authorization header |
| 401 | `INVALID_TOKEN` | Bad/expired JWT |
| 400 | `INVALID_DAYS` | `days` outside 1–365 |
| 503 | `PATTERN_LEARNING_DISABLED` | Pattern DB not initialized |
| 500 | `DASHBOARD_ERROR` | Unexpected DB error |

**Implementation skeleton:**

```python
@app.get("/api/v1/dashboard")
def dashboard(
    days: int = Query(default=30, ge=1, le=365),
    current_org: dict = Depends(get_current_organization),
):
    if _pattern_db is None:
        raise HTTPException(503, detail={...})
    try:
        data = _pattern_db.get_dashboard_data(
            organization_id=current_org["organization_id"],
            days=days,
        )
    except Exception as exc:
        raise HTTPException(500, detail={"code": "DASHBOARD_ERROR", "message": str(exc)})

    return {
        "organization_id": current_org["organization_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        **data,
    }
```

Import addition needed: `from fastapi import Query` (already partially imported;
`Query` must be added to the existing import line).

---

## 4. FRONTEND — Integration Into demo.html

### 4a. Dependencies

Add one `<script>` tag to the `<head>` of `demo.html`, after the existing CSS:

```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
```

No npm, no build step, no package.json changes.

---

### 4b. Navigation tab

The existing demo.html has a tab-based layout. Add a fourth tab to the existing
navigation bar:

```
[ Analyze Shipment ]  [ Pattern Learning History ]  [ Analytics Dashboard ]
```

Tab click handler shows `#dashboard-panel`, hides other panels, and calls
`loadDashboard()` if data has not yet been loaded for this session.

---

### 4c. HTML structure for `#dashboard-panel`

```html
<div id="dashboard-panel" class="tab-panel hidden">

  <!-- Header row: title + date range selector + refresh button -->
  <div class="dashboard-header">
    <h2>Analytics Dashboard</h2>
    <div class="dashboard-controls">
      <select id="dashboard-days">
        <option value="7">Last 7 days</option>
        <option value="30" selected>Last 30 days</option>
        <option value="90">Last 90 days</option>
        <option value="365">Last 365 days</option>
      </select>
      <button id="dashboard-refresh-btn">↻ Refresh</button>
    </div>
  </div>

  <!-- KPI cards row -->
  <div class="kpi-row">
    <div class="kpi-card" id="kpi-total-shipments">
      <span class="kpi-label">Total Shipments</span>
      <span class="kpi-value" id="kpi-total-val">—</span>
    </div>
    <div class="kpi-card" id="kpi-period-shipments">
      <span class="kpi-label">Shipments (window)</span>
      <span class="kpi-value" id="kpi-period-val">—</span>
    </div>
    <div class="kpi-card kpi-danger" id="kpi-fraud">
      <span class="kpi-label">Confirmed Fraud</span>
      <span class="kpi-value" id="kpi-fraud-val">—</span>
    </div>
    <div class="kpi-card kpi-success" id="kpi-cleared">
      <span class="kpi-label">Cleared</span>
      <span class="kpi-value" id="kpi-cleared-val">—</span>
    </div>
    <div class="kpi-card" id="kpi-ratio">
      <span class="kpi-label">Fraud Rate</span>
      <span class="kpi-value" id="kpi-ratio-val">—</span>
    </div>
    <div class="kpi-card kpi-info" id="kpi-learning">
      <span class="kpi-label">History Depth</span>
      <span class="kpi-value" id="kpi-learning-val">—</span>
    </div>
  </div>

  <!-- Chart grid: 2 columns -->
  <div class="chart-grid">

    <!-- Row 1: Trend line (wide) + Donut (narrow) -->
    <div class="chart-card chart-wide">
      <h3>Fraud Rate &amp; Risk Score Trend</h3>
      <canvas id="chart-fraud-trend" height="120"></canvas>
    </div>
    <div class="chart-card chart-narrow">
      <h3>Decision Breakdown</h3>
      <canvas id="chart-decision-donut" height="200"></canvas>
    </div>

    <!-- Row 2: Country bar + Shipper bar -->
    <div class="chart-card">
      <h3>Top Origin Countries by Risk</h3>
      <canvas id="chart-countries" height="200"></canvas>
    </div>
    <div class="chart-card">
      <h3>Riskiest Shippers</h3>
      <canvas id="chart-shippers" height="200"></canvas>
    </div>

    <!-- Row 3: HS chapters + Pattern growth -->
    <div class="chart-card">
      <h3>Top Flagged HS Chapters</h3>
      <canvas id="chart-hs-chapters" height="200"></canvas>
    </div>
    <div class="chart-card">
      <h3>Pattern Learning Growth</h3>
      <canvas id="chart-learning-growth" height="200"></canvas>
    </div>

  </div>

  <!-- Activity feed -->
  <div class="chart-card activity-feed-card">
    <h3>Recent Shipment Activity</h3>
    <ul id="activity-feed" class="activity-list"></ul>
  </div>

  <!-- Loading / error states -->
  <div id="dashboard-loading" class="dashboard-loading hidden">Loading analytics…</div>
  <div id="dashboard-error"   class="dashboard-error hidden"></div>

</div>
```

---

### 4d. JavaScript — `loadDashboard()` and render functions

All dashboard JS is added in a clearly delimited section of `demo.html`'s
existing `<script>` block, after the auth functions.

```javascript
// ============================================================
//  DASHBOARD STATE
// ============================================================

const _charts = {};   // canvas id -> Chart instance
let _dashboardLoaded = false;

// HS chapter number -> human-readable label (top 20 chapters seen in data)
const _HS_CHAPTER_LABELS = {
  '8471': '8471 — ADP Machines',
  '8542': '8542 — Semiconductors',
  '8517': '8517 — Smartphones/Telecom',
  '7209': '7209 — Cold-Rolled Steel',
  '7208': '7208 — Hot-Rolled Steel',
  '7604': '7604 — Aluminum Extrusions',
  '9403': '9403 — Furniture',
  '4412': '4412 — Hardwood Plywood',
  '8541': '8541 — Solar Cells',
  '0306': '0306 — Crustaceans/Shrimp',
  '7317': '7317 — Steel Nails',
  '6109': '6109 — Cotton T-Shirts',
  '8528': '8528 — LCD/OLED Displays',
};

// ISO2 -> country display name (supplement _COUNTRY_MAP)
const _ISO2_NAMES = {
  CN: 'China', VN: 'Vietnam', MY: 'Malaysia', SG: 'Singapore',
  KR: 'South Korea', TW: 'Taiwan', IN: 'India', TH: 'Thailand',
  ID: 'Indonesia', BD: 'Bangladesh', DE: 'Germany', MX: 'Mexico',
  IR: 'Iran', KP: 'North Korea', RU: 'Russia', JP: 'Japan',
  HK: 'Hong Kong', PH: 'Philippines', US: 'United States',
};

const _DECISION_LABELS = {
  APPROVE: 'Approve',
  REVIEW_RECOMMENDED: 'Review',
  FLAG_FOR_INSPECTION: 'Flag',
  REQUEST_MORE_INFORMATION: 'More Info',
  REJECT: 'Reject',
};

const _DECISION_COLORS = {
  APPROVE: '#22c55e',
  REVIEW_RECOMMENDED: '#3b82f6',
  FLAG_FOR_INSPECTION: '#f97316',
  REQUEST_MORE_INFORMATION: '#eab308',
  REJECT: '#ef4444',
};

// ============================================================
//  MAIN LOAD FUNCTION
// ============================================================

async function loadDashboard(forceRefresh = false) {
  if (_dashboardLoaded && !forceRefresh) return;

  const days = document.getElementById('dashboard-days').value;
  document.getElementById('dashboard-loading').classList.remove('hidden');
  document.getElementById('dashboard-error').classList.add('hidden');

  try {
    const res = await fetch(`/api/v1/dashboard?days=${days}`, {
      headers: { 'Authorization': 'Bearer ' + _authToken }
    });
    if (res.status === 401) { handle401(); return; }
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err?.detail?.message || `HTTP ${res.status}`);
    }

    const data = await res.json();
    _renderDashboard(data);
    _dashboardLoaded = true;

  } catch (e) {
    document.getElementById('dashboard-error').textContent = 'Failed to load dashboard: ' + e.message;
    document.getElementById('dashboard-error').classList.remove('hidden');
  } finally {
    document.getElementById('dashboard-loading').classList.add('hidden');
  }
}

// ============================================================
//  RENDER
// ============================================================

function _renderDashboard(data) {
  _renderKpis(data);
  _renderChart('chart-fraud-trend',     _buildFraudTrendConfig(data));
  _renderChart('chart-decision-donut',  _buildDecisionDonutConfig(data));
  _renderChart('chart-countries',       _buildCountriesConfig(data));
  _renderChart('chart-shippers',        _buildShippersConfig(data));
  _renderChart('chart-hs-chapters',     _buildHsChaptersConfig(data));
  _renderChart('chart-learning-growth', _buildLearningGrowthConfig(data));
  _renderActivityFeed(data.recent_activity);
}

function _renderChart(canvasId, config) {
  if (_charts[canvasId]) {
    _charts[canvasId].destroy();   // prevent canvas reuse error
    delete _charts[canvasId];
  }
  const ctx = document.getElementById(canvasId).getContext('2d');
  _charts[canvasId] = new Chart(ctx, config);
}
```

The six `_build*Config()` functions each return a Chart.js configuration object
exactly matching the specs in §2. They are straightforward data mappings —
no complex logic.

```javascript
function _renderKpis(data) {
  const { confirmed_fraud: fraud, cleared } = data.outcome_ratio;
  const total = fraud + cleared;
  const fraudRate = total > 0 ? ((fraud / total) * 100).toFixed(1) + '%' : '—';

  // Cumulative count from last entry of learning_growth
  const growth = data.learning_growth;
  const cumulative = growth.reduce((sum, d) => sum + d.new_shipments, 0);

  document.getElementById('kpi-total-val').textContent    = data.summary.total_all_time;
  document.getElementById('kpi-period-val').textContent   = data.summary.total_last_n_days;
  document.getElementById('kpi-fraud-val').textContent    = fraud;
  document.getElementById('kpi-cleared-val').textContent  = cleared;
  document.getElementById('kpi-ratio-val').textContent    = fraudRate;
  document.getElementById('kpi-learning-val').textContent = cumulative;
}

function _renderActivityFeed(items) {
  const ul = document.getElementById('activity-feed');
  ul.innerHTML = '';
  if (!items || items.length === 0) {
    ul.innerHTML = '<li class="activity-empty">No shipments analyzed yet.</li>';
    return;
  }
  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'activity-item';
    const relTime = _relativeTime(item.analyzed_at);
    const country  = _ISO2_NAMES[item.origin] || item.origin || '—';
    const outcome  = item.outcome
      ? `<span class="badge badge-${item.outcome.toLowerCase()}">${item.outcome.replace('_',' ')}</span>`
      : '';
    const decColor = _DECISION_COLORS[item.decision] || '#9ca3af';
    li.innerHTML = `
      <span class="activity-time">${relTime}</span>
      <span class="activity-shipper">${_esc(item.shipper || 'Unknown shipper')}</span>
      <span class="activity-origin">${country}</span>
      <span class="activity-decision" style="color:${decColor}">
        ${_DECISION_LABELS[item.decision] || item.decision}
      </span>
      <span class="activity-score">${(item.risk_score * 100).toFixed(0)}%</span>
      ${outcome}
    `;
    ul.appendChild(li);
  }
}

// Small helpers
function _esc(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}
function _relativeTime(iso) {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1)  return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}
```

**Wire up the refresh button and date selector:**

```javascript
document.getElementById('dashboard-refresh-btn').addEventListener('click', () => {
  _dashboardLoaded = false;
  loadDashboard(true);
});
document.getElementById('dashboard-days').addEventListener('change', () => {
  _dashboardLoaded = false;
  loadDashboard(true);
});
```

**Clear dashboard state on logout** (add to `handleLogout()`):

```javascript
// In handleLogout(), after clearing _authToken:
Object.values(_charts).forEach(c => c.destroy());
Object.keys(_charts).forEach(k => delete _charts[k]);
_dashboardLoaded = false;
```

---

### 4e. CSS additions

New classes to add to `demo.html`'s `<style>` block:

```css
/* KPI row */
.kpi-row            { display: grid; grid-template-columns: repeat(6,1fr); gap:12px; margin-bottom:24px; }
.kpi-card           { background:#1e1e3a; border:1px solid #2d2d4e; border-radius:10px;
                      padding:16px; text-align:center; }
.kpi-label          { display:block; font-size:0.72rem; color:#9ca3af; margin-bottom:6px; }
.kpi-value          { display:block; font-size:1.8rem; font-weight:700; color:#e2e8f0; }
.kpi-danger .kpi-value  { color:#ef4444; }
.kpi-success .kpi-value { color:#22c55e; }
.kpi-info .kpi-value    { color:#6366f1; }

/* Chart grid */
.chart-grid         { display:grid; grid-template-columns:repeat(2,1fr); gap:16px; margin-bottom:24px; }
.chart-card         { background:#1e1e3a; border:1px solid #2d2d4e; border-radius:10px; padding:20px; }
.chart-card h3      { margin:0 0 16px; font-size:0.9rem; color:#9ca3af; font-weight:600;
                      text-transform:uppercase; letter-spacing:0.05em; }
.chart-wide         { grid-column: span 1; }    /* same width as others in 2-col grid */

/* Activity feed */
.activity-feed-card     { margin-bottom:24px; }
.activity-list          { list-style:none; margin:0; padding:0; max-height:360px; overflow-y:auto; }
.activity-item          { display:grid; grid-template-columns:90px 1fr 120px 130px 60px auto;
                          gap:12px; align-items:center; padding:10px 0;
                          border-bottom:1px solid #2d2d4e; font-size:0.83rem; }
.activity-item:last-child { border-bottom:none; }
.activity-time          { color:#6b7280; white-space:nowrap; }
.activity-shipper       { color:#e2e8f0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.activity-origin        { color:#9ca3af; }
.activity-score         { font-variant-numeric:tabular-nums; color:#9ca3af; text-align:right; }
.activity-empty         { color:#6b7280; padding:16px 0; text-align:center; }

/* Dashboard controls */
.dashboard-header   { display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; }
.dashboard-controls { display:flex; gap:12px; align-items:center; }
.dashboard-controls select { background:#1e1e3a; color:#e2e8f0; border:1px solid #3d3d6e;
                              padding:6px 10px; border-radius:6px; font-size:0.85rem; }
.dashboard-loading  { color:#9ca3af; text-align:center; padding:40px; }
.dashboard-error    { color:#ef4444; background:rgba(239,68,68,0.1); border:1px solid #ef4444;
                      border-radius:8px; padding:12px 16px; margin-bottom:16px; }

/* Badge variants for activity feed outcomes */
.badge-confirmed_fraud  { background:rgba(239,68,68,0.15); color:#ef4444; }
.badge-cleared          { background:rgba(34,197,94,0.15); color:#22c55e; }
```

---

## 5. IMPLEMENTATION ORDER

This is the correct sequence — each step is independently testable:

1. **Add `get_dashboard_data()` to `portguard/pattern_db.py`** — pure SQL, no
   dependencies on anything new. Test with `python -c "from portguard.pattern_db
   import PatternDB; db=PatternDB(); print(db.get_dashboard_data())"`.

2. **Add `GET /api/v1/dashboard` to `api/app.py`** — thin wrapper around
   `get_dashboard_data()`. Test with curl after server starts.

3. **Add Chart.js CDN script tag to `demo.html`**.

4. **Add `#dashboard-panel` HTML structure to `demo.html`**.

5. **Add navigation tab** and show/hide logic.

6. **Add CSS** for KPI cards, chart grid, activity feed.

7. **Add JavaScript** — `loadDashboard()`, six `_build*Config()` functions,
   `_renderKpis()`, `_renderActivityFeed()`, helpers, event listeners.

8. **Wire logout cleanup** in `handleLogout()`.

9. **Manual end-to-end test in browser** — verify all six charts render, KPI
   cards update, date range selector triggers re-fetch, logout clears charts,
   401 triggers redirect to login.

---

## 6. WHAT IS NOT IN SCOPE

- **No new DB migrations.** All queries use the existing schema as-is. The
  `organization_id` column, all indices, and all tables required exist from
  migration 001–003.

- **No new dependencies.** Chart.js is loaded from jsDelivr CDN. No npm changes.

- **No changes to `portguard/pattern_engine.py`** or any agent.

- **No changes to the structured pipeline** (`portguard/api/`). Dashboard is
  scoped to Entry Point 1 (`api/app.py`) only.

- **No server-sent events or WebSocket push.** Dashboard data is fetched on
  tab click and on explicit Refresh. Real-time auto-refresh is a future epic.

- **No chart export or PDF generation.** Future scope.

---

## 7. RISKS AND MITIGATIONS

| Risk | Mitigation |
|---|---|
| Empty database (no shipments analyzed yet) | All queries return empty arrays; frontend renders "No data yet" states for each chart section |
| `hs_chapter_primary` is NULL for shipments where no HTS was extracted | `WHERE hs_chapter_primary IS NOT NULL` in the query; chart simply shows fewer bars |
| Chart.js CDN unavailable (offline deployment) | Document that Chart.js can be self-hosted; add `onerror` handler on the script tag that shows a graceful error message |
| Large result sets on high-volume deployments | All queries use `LIMIT 10`/`LIMIT 20`; trend queries are bounded by `days` parameter (max 365 rows output) |
| `DATE(analyzed_at)` performance on large `shipment_history` | Bounded by org_id index; acceptable at SQLite single-tenant scale. For high-volume production, add `CREATE INDEX idx_history_org_date ON shipment_history(organization_id, analyzed_at)` |
