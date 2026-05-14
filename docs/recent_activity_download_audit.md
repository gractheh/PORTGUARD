# Recent Activity PDF Download — Failure Audit
**Date:** 2026-05-14  
**Files:** `demo.html`, `api/app.py`, `portguard/analytics.py`, `portguard/pattern_db.py`, `portguard/report_generator.py`

---

## 1. Complete Data Flow (Working Parts)

```
GET /api/v1/dashboard/recent-activity?limit=20
  → analytics.py get_recent_activity() (line 815)
  → SQL: SELECT analysis_id, shipper_name, origin_iso2, final_decision,
           final_risk_score, pattern_cold_start, outcome
         FROM shipment_history
         LEFT JOIN pattern_outcomes ON analysis_id
         WHERE organization_id = ?
  → returns { activity: [...], total_shown: N }
  → each item has field: analysis_id  ✓

Activity feed row rendered at demo.html:9831:
  <button class="act-dl-btn" onclick="downloadActivityReport(r.analysis_id)">
  → correctly passes analysis_id ✓

downloadActivityReport(analysisId) → demo.html:8112
  → POST /api/v1/report/generate  with body { shipment_id: analysisId }
  → authedJson('POST', ...) sets Authorization: Bearer <token>  ✓

Backend app.py:2986  report_generate():
  → get_report_payload(analysis_id, organization_id) from shipment_history.report_payload ✓
  → if payload_json is None → HTTP 404  ✓
  → generate_report_from_dict(payload_dict) → PDF bytes  ✓
  → returns application/pdf with Content-Disposition header  ✓
```

---

## 2. Root Causes of Failure

### 2a. CRITICAL: Silent error swallowing (Primary bug)
The existing `downloadActivityReport` catch block does only `console.error`. No user-facing
feedback. When the download fails (404, 500, network error), the user sees nothing — the
button just stops loading with no message.

```javascript
// CURRENT (broken):
} catch (e) {
  console.error('Report download failed:', e.message);  // silent to user
}
```

### 2b. No loading/disabled state on button
The button is not disabled and doesn't change appearance during the download. Users can
click multiple times, triggering parallel requests. There's no visual indication that a
download is in progress.

### 2c. No `_authToken` guard at function entry
The function doesn't check `_authToken` before fetching. If the user's session expired,
`authedJson()` still sets the header (with null token), the server returns 401, `handle401()`
is called, but the `return` inside the 401 handler doesn't exit the function's `finally`
block cleanly.

### 2d. `event.currentTarget` not reliably available inside function
The button uses `onclick="downloadActivityReport(r.analysis_id)"` — the button element
itself is not passed to the function. Adding a loading state requires `this` to be passed
from the inline handler.

### 2e. Filename doesn't include result ID
Current filename: `PortGuard_Report_YYYYMMDD.pdf` — same name for every download.
If a user downloads multiple reports the same day, files overwrite each other.

---

## 3. Backend — Verified Working

The backend is **correctly implemented**:
- `POST /api/v1/report/generate` exists at app.py:2986 ✓
- Auth guarded by `get_current_organization` ✓
- Org-scoped: `WHERE analysis_id = ? AND organization_id = ?` ✓
- 404 on missing payload ✓
- Streams PDF bytes with correct headers ✓
- `store_report_payload()` called after every analysis (lines 1857, 2199, 3480) ✓
- `analysis_id` returned in recent activity and used as `shipment_id` in POST body ✓

No backend changes required.

---

## 4. Fix Plan

### Frontend only:

1. **Replace `downloadActivityReport`** — add auth check, loading state, toast feedback,
   empty-blob guard, filename includes analysis_id.

2. **Update button onclick** — pass `this` from inline handler so loading state can be
   applied to the button element.

3. **Update `bulkDownloadRowPdf`** — pass `null` as first arg (no button reference) so
   existing bulk download still works.

4. **Add `act-dl-btn:disabled` CSS** — opacity + cursor for disabled state.

5. **Add `activity-download-btn` CSS** — new companion class (referenced in task spec).
