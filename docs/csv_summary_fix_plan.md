# CSV Export — Fix Plan
**Date:** 2026-05-17
**Sprint:** 12 — plan only

---

## SECTION 1 — CURRENT STATE

### What the CSV contains today

The "Export CSV Summary" button (`id="bulk-export-csv-btn"`, demo.html line 4899) calls `bulkExportCsv()` (demo.html line 11314). That function reads the in-memory JavaScript array `_bulkAllResults` and produces an 11-column CSV entirely client-side. No server round-trip occurs.

**Current 11 columns:**

| Column | Source | Problem |
|---|---|---|
| `reference_id` | `r.ref` | Correct. |
| `decision` | `r.decision` | Correct, but values use internal names (APPROVE, FLAG_FOR_INSPECTION, etc.) — no normalization. |
| `risk_score` | `r.risk_score.toFixed(4)` | Stored internally as 0.0–1.0. Exported as 0.0–1.0 with 4 decimals. Unintuitive for end users expecting 0–10. |
| `risk_level` | `r.risk_level` | **Bug:** mapper never assigns `risk_level` to `_bulkAllResults` items. Always blank. |
| `n_findings` | `r.n_findings` | Counts all flags but only one flag is exported. Misleading. |
| `top_finding` | `r.top_finding` | Only first flag string. All remaining flags are silently discarded. |
| `sustainability_grade` | `r.sustainability_grade` | Correct field, wrong column name vs target spec. |
| `sustainability_signals` | joined array | Useful but sub-scores (country_risk_level, product_risk_level, certs_detected, certs_missing) are absent. |
| `active_modules` | joined array | Module names only, no outcomes per module. |
| `status` | `r.status` | Correct. |
| `error_message` | `r.error_message` | Correct. |

### Why it is not detailed enough

1. **No shipment identity fields** — shipper name, origin country, destination country, declared value, HTS code are all extracted by the analysis pipeline and stored in `result_json` but never surfaced to the frontend or CSV.

2. **No compliance module hits** — OFAC, Section 301, AD/CVD, UFLPA, and ISF completeness are the core compliance outputs of PortGuard. None appear as dedicated columns. Customs officers cannot filter the spreadsheet on "show me all OFAC hits."

3. **No pattern intelligence** — `pattern_signals` and `pattern_intelligence.hard_flag` (confirmed fraud history) are computed by the LPL engine, stored in `result_json`, but never reach the CSV.

4. **No document type** — `document_type` (e.g. "Bill of Lading") is detected by the hardened classifier and stored in `result_json` but not surfaced.

5. **No sustainability certification detail** — `sustainability_rating.certifications_detected` and `certifications_missing` are computed by `SustainabilityRater`, stored in `result_json`, but not surfaced.

6. **No timestamp** — `processed_at` is in the backend response and flat DB columns but is never mapped into `_bulkAllResults`, so it cannot be written to CSV.

7. **risk_level is always blank** — mapper bug means the column header exists but the column is empty for every row.

8. **Flags are lossy** — `n_findings` says N flags exist but only `top_finding` (first flag, truncated to 200 chars) is written. The full `findings` array IS available on every `_bulkAllResults` item but is never used in CSV export.

---

## SECTION 2 — TARGET CSV COLUMNS

The new CSV must have exactly these 25 columns in this exact order:

| # | Column name | Type | Source |
|---|---|---|---|
| 1 | `reference_id` | string | Shipment name/filename from batch input |
| 2 | `timestamp` | ISO 8601 string | When the shipment was screened |
| 3 | `decision` | string | APPROVE / FLAG_FOR_INSPECTION / REQUEST_MORE_INFO / REJECTED / ERROR |
| 4 | `risk_score` | numeric (0–10, 1 decimal) | Internal 0–1 score × 10 |
| 5 | `risk_level` | string | LOW / MEDIUM / HIGH / CRITICAL per Section 5 mapping |
| 6 | `sustainability_rating` | string | A / B / C / D / N/A |
| 7 | `document_type` | string | Detected document type (e.g. "Bill of Lading") or blank |
| 8 | `shipper` | string | Extracted exporter/shipper name or blank |
| 9 | `origin_country` | string | Extracted origin country or blank |
| 10 | `destination_country` | string | Extracted destination country or blank |
| 11 | `declared_value` | string | Extracted declared value (with currency if present) or blank |
| 12 | `hts_code` | string | HTS/HS codes pipe-joined, or blank |
| 13 | `flags_count` | integer | Total number of compliance flags |
| 14 | `flags_detail` | string | All flags joined with ` \| ` separator |
| 15 | `ofac_hit` | YES / NO / N/A | OFAC sanctions triggered |
| 16 | `section301_hit` | YES / NO / N/A | Section 301 tariff exposure triggered |
| 17 | `adcvd_hit` | YES / NO / N/A | AD/CVD order triggered |
| 18 | `uflpa_hit` | YES / NO / N/A | UFLPA forced-labour flag triggered |
| 19 | `isf_complete` | YES / NO / N/A | ISF (Importer Security Filing) complete |
| 20 | `pattern_warnings` | string | Pattern intelligence warnings joined with ` \| ` |
| 21 | `pattern_hard_flag` | YES / NO | Confirmed fraud history flag from LPL engine |
| 22 | `sustainability_certs_detected` | string | Detected certifications joined with ` \| ` |
| 23 | `sustainability_certs_missing` | string | Expected but missing certifications joined with ` \| ` |
| 24 | `status` | string | COMPLETE / ERROR / TIMEOUT |
| 25 | `error_detail` | string | Error message if status is ERROR, blank otherwise |

**Encoding and format rules (unchanged from current):**
- Delimiter: comma (`,`)
- Line terminator: `\n`
- Encoding: UTF-8, no BOM
- Quoting: RFC 4180 — wrap in `"..."` and double internal quotes only if value contains `,`, `"`, or `\n`
- Filename: `portguard_batch_{batchId.slice(0,8)}.csv`

---

## SECTION 3 — BACKEND CHANGES NEEDED

### 3A — Fields currently in `result_json` but not in the bulk API response

`_build_bulk_response()` (`api/app.py` line 3757) reads from two sources for each shipment: flat DB columns and `full_result` (parsed `result_json`). It currently extracts `explanations → flags` and `active_modules_at_scan → active_mods` from `full_result`, but leaves the rest untouched.

The following fields exist in `full_result` today and must be extracted and added to the per-result object returned by `_build_bulk_response()`:

| Target column | Source in `full_result` | Key path |
|---|---|---|
| `timestamp` | flat DB column `processed_at` | `s.get("processed_at")` |
| `document_type` | `full_result["document_type"]` | `full.get("document_type")` |
| `shipper` | `full_result["shipment_data"]["exporter"]` | `full.get("shipment_data", {}).get("exporter")` |
| `origin_country` | `full_result["shipment_data"]["origin_country"]` | `full.get("shipment_data", {}).get("origin_country")` |
| `destination_country` | `full_result["shipment_data"]["destination_country"]` | `full.get("shipment_data", {}).get("destination_country")` |
| `declared_value` | `full_result["shipment_data"]["declared_value"]` + optional `declared_currency` | Concatenate value and currency if both present |
| `hts_code` | `full_result["shipment_data"]["hts_codes_declared"]` (list) | `"\|".join(...)` or blank |
| `pattern_warnings` | `full_result["pattern_signals"]` (list) | `full.get("pattern_signals", [])` |
| `pattern_hard_flag` | `full_result["pattern_intelligence"]["hard_flag"]` | `(full.get("pattern_intelligence") or {}).get("hard_flag", False)` |
| `sustainability_certs_detected` | `full_result["sustainability_rating"]["certifications_detected"]` | `(sus_dict or {}).get("certifications_detected", [])` |
| `sustainability_certs_missing` | `full_result["sustainability_rating"]["certifications_missing"]` | `(sus_dict or {}).get("certifications_missing", [])` |

### 3B — Fields that do NOT yet exist in `result_json` and must be added

The five compliance hit booleans (`ofac_hit`, `section301_hit`, `adcvd_hit`, `uflpa_hit`, `isf_complete`) are computed during analysis via `_risk_factors` (tagged dicts) and `missing_isf` (ISF gap list), but these internal lists are stripped before `AnalyzeResponse` is built — they never reach `result_json`.

**Required additions to `AnalyzeResponse` model (`api/app.py` ~line 1127):**

Five new optional boolean fields:

```
ofac_hit:          Optional[bool] = None
section301_hit:    Optional[bool] = None
adcvd_hit:         Optional[bool] = None
uflpa_hit:         Optional[bool] = None
isf_complete:      Optional[bool] = None
```

**Required computation in `_run_bulk_single_analysis()` (`api/app.py` ~line 3339):**

After `_analyze_documents(docs)` returns `result` (which includes `_risk_factors` and `_missing_msgs`), compute before building `AnalyzeResponse`:

```
risk_factors = result.get("_risk_factors", [])
ofac_hit          = any(f.get("type") == "OFAC" for f in risk_factors)
section301_hit    = any(f.get("type") == "Section301" for f in risk_factors)
adcvd_hit         = any(f.get("type") == "ADCVD" for f in risk_factors)
uflpa_hit         = any(f.get("type") == "UFLPA" for f in risk_factors)
isf_complete      = not any("ISF incomplete" in m for m in result.get("explanations", []))
```

Pass these five values when constructing `AnalyzeResponse(...)`.

**Required computation in the main `POST /api/v1/analyze` handler:**

The same five fields must be computed and populated in the single-shipment handler so they are stored in `shipment_history.report_payload` for single-result downloads. This keeps the two code paths consistent.

### 3C — Complete list of new fields added to the per-result object in `_build_bulk_response()`

After changes, each item in `results` will contain these additional keys (on top of the current 13):

```
"timestamp"                    # str | None — processed_at from flat col
"document_type"                # str | None — from full_result
"shipper"                      # str | None — shipment_data.exporter
"origin_country"               # str | None — shipment_data.origin_country
"destination_country"          # str | None — shipment_data.destination_country
"declared_value"               # str | None — value + currency concatenated
"hts_codes"                    # list[str]  — hts_codes_declared
"pattern_warnings"             # list[str]  — pattern_signals
"pattern_hard_flag"            # bool       — pattern_intelligence.hard_flag
"sustainability_certs_detected" # list[str] — from sustainability_rating
"sustainability_certs_missing"  # list[str] — from sustainability_rating
"ofac_hit"                     # bool | None — new AnalyzeResponse field
"section301_hit"               # bool | None — new AnalyzeResponse field
"adcvd_hit"                    # bool | None — new AnalyzeResponse field
"uflpa_hit"                    # bool | None — new AnalyzeResponse field
"isf_complete"                 # bool | None — new AnalyzeResponse field
```

Total per-result fields after changes: 29 (13 existing + 16 new).

---

## SECTION 4 — FRONTEND CHANGES NEEDED

### 4A — `_bulkRenderFromResponse()` (demo.html ~line 10865) — mapper update

The mapper must store all new backend fields onto each `_bulkAllResults` item. Fields to add:

| New item field | Source from backend `r` | Default if missing |
|---|---|---|
| `risk_level` | `r.risk_level` | `null` (fixes existing bug) |
| `timestamp` | `r.timestamp` | `null` |
| `document_type` | `r.document_type` | `null` |
| `shipper` | `r.shipper` | `null` |
| `origin_country` | `r.origin_country` | `null` |
| `destination_country` | `r.destination_country` | `null` |
| `declared_value` | `r.declared_value` | `null` |
| `hts_codes` | `r.hts_codes \|\| []` | `[]` |
| `pattern_warnings` | `r.pattern_warnings \|\| []` | `[]` |
| `pattern_hard_flag` | `r.pattern_hard_flag \|\| false` | `false` |
| `sustainability_certs_detected` | `r.sustainability_certs_detected \|\| []` | `[]` |
| `sustainability_certs_missing` | `r.sustainability_certs_missing \|\| []` | `[]` |
| `ofac_hit` | `r.ofac_hit` | `null` |
| `section301_hit` | `r.section301_hit` | `null` |
| `adcvd_hit` | `r.adcvd_hit` | `null` |
| `uflpa_hit` | `r.uflpa_hit` | `null` |
| `isf_complete` | `r.isf_complete` | `null` |

`processed_at` is already returned in `r` from `_build_bulk_response` — it just needs to be stored as `timestamp`.

### 4B — `bulkExportCsv()` (demo.html ~line 11314) — complete rewrite

The function must be rewritten with 25 headers and 25 corresponding value expressions.

**Headers array (exact order):**
```javascript
const headers = [
  'reference_id', 'timestamp', 'decision', 'risk_score', 'risk_level',
  'sustainability_rating', 'document_type', 'shipper', 'origin_country',
  'destination_country', 'declared_value', 'hts_code',
  'flags_count', 'flags_detail',
  'ofac_hit', 'section301_hit', 'adcvd_hit', 'uflpa_hit', 'isf_complete',
  'pattern_warnings', 'pattern_hard_flag',
  'sustainability_certs_detected', 'sustainability_certs_missing',
  'status', 'error_detail',
];
```

**Value derivation rules per row:**

| Column | Expression | Notes |
|---|---|---|
| `reference_id` | `r.ref` | |
| `timestamp` | `r.timestamp \|\| ''` | ISO string from processed_at |
| `decision` | `r.decision \|\| ''` | |
| `risk_score` | `r.risk_score != null ? (r.risk_score * 10).toFixed(1) : ''` | Scale 0–1 → 0–10 |
| `risk_level` | Recomputed from scaled score per Section 5 mapping | Do not use `r.risk_level` from backend — recompute using 0-10 thresholds to match the exported `risk_score` |
| `sustainability_rating` | `r.sustainability_grade \|\| 'N/A'` | |
| `document_type` | `r.document_type \|\| ''` | |
| `shipper` | `r.shipper \|\| ''` | |
| `origin_country` | `r.origin_country \|\| ''` | |
| `destination_country` | `r.destination_country \|\| ''` | |
| `declared_value` | `r.declared_value \|\| ''` | |
| `hts_code` | `(r.hts_codes \|\| []).join(' \| ')` | |
| `flags_count` | `r.n_findings \|\| 0` | |
| `flags_detail` | `(r.findings \|\| []).join(' \| ')` | Full list, not just top_finding |
| `ofac_hit` | `r.ofac_hit === true ? 'YES' : r.ofac_hit === false ? 'NO' : 'N/A'` | |
| `section301_hit` | Same pattern as ofac_hit using `r.section301_hit` | |
| `adcvd_hit` | Same pattern using `r.adcvd_hit` | |
| `uflpa_hit` | Same pattern using `r.uflpa_hit` | |
| `isf_complete` | `r.isf_complete === true ? 'YES' : r.isf_complete === false ? 'NO' : 'N/A'` | |
| `pattern_warnings` | `(r.pattern_warnings \|\| []).join(' \| ')` | |
| `pattern_hard_flag` | `r.pattern_hard_flag ? 'YES' : 'NO'` | |
| `sustainability_certs_detected` | `(r.sustainability_certs_detected \|\| []).join(' \| ')` | |
| `sustainability_certs_missing` | `(r.sustainability_certs_missing \|\| []).join(' \| ')` | |
| `status` | `r.status \|\| ''` | |
| `error_detail` | `r.status === 'ERROR' \|\| r.status === 'TIMEOUT' ? (r.error_message \|\| '') : ''` | Blank unless error |

The `csvEsc()` helper is unchanged — it handles quoting correctly already.

### 4C — `risk_level` recomputation helper

A small inline helper inside `bulkExportCsv()` derives `risk_level` from the scaled score:

```javascript
function scaledRiskLevel(rawScore) {
  if (rawScore == null) return '';
  const s = rawScore * 10;
  if (s <= 2)  return 'LOW';
  if (s <= 4)  return 'MEDIUM';
  if (s <= 7)  return 'HIGH';
  return 'CRITICAL';
}
```

This is used only inside `bulkExportCsv()` for the CSV column. The on-screen table display is not changed.

---

## SECTION 5 — RISK LEVEL MAPPING

The CSV `risk_level` column is derived from `risk_score` after it has been scaled to 0–10:

| Scaled score range | risk_level |
|---|---|
| 0.0 – 2.0 | LOW |
| 2.1 – 4.0 | MEDIUM |
| 4.1 – 7.0 | HIGH |
| 7.1 – 10.0 | CRITICAL |

**Implementation note:** The backend currently uses different thresholds on the 0–1 scale (≤0.25 LOW, ≤0.50 MEDIUM, ≤0.75 HIGH, >0.75 CRITICAL). These do not align identically with the new 0–10 mapping above (which implies ≤0.20, ≤0.40, ≤0.70, >0.70). The CSV must use the plan's thresholds, not the backend's stored `risk_level`. The backend's stored `risk_level` is used for the on-screen table display and is not changed.

---

## SECTION 6 — STEP BY STEP BUILD ORDER

Steps are ordered to minimize the risk of breaking existing functionality. Backend additions are additive (new optional fields, no existing fields removed). Frontend changes are isolated to `_bulkRenderFromResponse()` and `bulkExportCsv()`.

**Step 1 — Add 5 new fields to `AnalyzeResponse` model (`api/app.py`)**

Location: after `pattern_intelligence` field (~line 1225).
Add: `ofac_hit`, `section301_hit`, `adcvd_hit`, `uflpa_hit`, `isf_complete` — all `Optional[bool] = None`.
These are new optional fields with defaults; no existing callers break.

**Step 2 — Compute the 5 boolean hits in `_run_bulk_single_analysis()` (`api/app.py`)**

Location: after `result = _analyze_documents(docs)` and before `AnalyzeResponse(...)` construction (~line 3532).
Derive from `result.get("_risk_factors", [])` and `result.get("explanations", [])`.
Pass the 5 values into `AnalyzeResponse(...)`.

**Step 3 — Compute the 5 boolean hits in the single-shipment `POST /api/v1/analyze` handler (`api/app.py`)**

Location: the main analyze endpoint handler, same pattern as Step 2.
This keeps `report_payload` consistent for single-result PDF downloads.

**Step 4 — Update `_build_bulk_response()` to extract and surface all 16 new fields (`api/app.py`)**

Location: inside the `for s in raw_shipments:` loop (~line 3770), after `full_result` is parsed.
Extract all 16 new fields listed in Section 3C from `full_result` and `s` (flat columns).
Append them to each result dict before `results.append(...)`.

**Step 5 — Update `_bulkRenderFromResponse()` to store all new fields on `_bulkAllResults` items (`demo.html`)**

Location: ~line 10865.
Add all 17 new fields (16 from backend + fix `risk_level` bug) to the `.map(r => ({...}))` return object.
Existing fields are not touched.

**Step 6 — Rewrite `bulkExportCsv()` with 25 columns (`demo.html`)**

Location: ~line 11314.
Replace the `headers` array and the `rows.push(...)` block entirely.
Add the `scaledRiskLevel()` inline helper before the `rows` construction.
The blob creation, anchor click, and filename logic are unchanged.

**Step 7 — Manual end-to-end verification**

Run a bulk batch of at least 3 shipments (one APPROVE, one FLAG/REJECT, one ERROR).
Download the CSV and verify:
- All 25 columns present with correct headers
- `risk_score` is on 0–10 scale
- `risk_level` matches the Section 5 thresholds
- `ofac_hit` / `section301_hit` / `adcvd_hit` are YES for shipments that triggered those checks
- `isf_complete` is NO for shipments with ISF gaps
- `flags_detail` contains all flags (not just the first)
- `shipper`, `origin_country`, `destination_country`, `declared_value`, `hts_code` are populated where the document contained that information
- `pattern_hard_flag` is YES for a shipper with confirmed fraud history (if test data available)
- `timestamp` is a valid ISO timestamp for each completed row
- `error_detail` is blank for COMPLETE rows, populated for ERROR rows

**Step 8 — Write validation script**

Write a Python script (similar to the download validation script used in Sprint 7) that:
- Reads a sample CSV output
- Asserts all 25 headers are present in the correct order
- Asserts `risk_score` is in [0.0, 10.0] for all non-error rows
- Asserts `risk_level` is one of LOW / MEDIUM / HIGH / CRITICAL
- Asserts `ofac_hit`, `section301_hit`, `adcvd_hit`, `uflpa_hit`, `isf_complete` are one of YES / NO / N/A
- Asserts `pattern_hard_flag` is one of YES / NO
- Asserts `error_detail` is blank when `status` is COMPLETE

**Step 9 — Commit and push**

```
git add api/app.py demo.html
git commit -m "feat(csv): 25-column bulk export — compliance hits, shipper fields, pattern intel, cert detail"
git push origin master
```

---

## Appendix — Data availability map

This table traces each target column from its origin in the analysis pipeline to the CSV, showing where each gap exists and which step closes it.

| Target column | Computed by | Stored in | In backend response today? | In `_bulkAllResults` today? | Closed by step |
|---|---|---|---|---|---|
| `reference_id` | Input CSV | flat col `shipment_ref` | YES | YES (`r.ref`) | — already works |
| `timestamp` | BulkProcessor | flat col `processed_at` | YES (in response item) | NO | Step 5 |
| `decision` | `_run_bulk_single_analysis` | flat col `decision` | YES | YES | — already works |
| `risk_score` | `_run_bulk_single_analysis` | flat col `risk_score` | YES | YES | Step 6 (scale ×10) |
| `risk_level` | Plan's thresholds | derived | YES (wrong thresholds) | NO (mapper bug) | Steps 5 + 6 |
| `sustainability_rating` | `SustainabilityRater` | flat col `sustainability_grade` | YES | YES | Step 6 (rename) |
| `document_type` | `_classify_document` | `result_json` | NO | NO | Steps 4 + 5 |
| `shipper` | `_analyze_documents` | `result_json → shipment_data.exporter` | NO | NO | Steps 4 + 5 |
| `origin_country` | `_analyze_documents` | `result_json → shipment_data.origin_country` | NO | NO | Steps 4 + 5 |
| `destination_country` | `_analyze_documents` | `result_json → shipment_data.destination_country` | NO | NO | Steps 4 + 5 |
| `declared_value` | `_analyze_documents` | `result_json → shipment_data.declared_value` | NO | NO | Steps 4 + 5 |
| `hts_code` | `_analyze_documents` | `result_json → shipment_data.hts_codes_declared` | NO | NO | Steps 4 + 5 |
| `flags_count` | `_store_shipment_result` | flat col `n_findings` | YES (via flags len) | YES (`r.n_findings`) | Step 6 (rename) |
| `flags_detail` | `_analyze_documents` | `result_json → explanations → flags` | YES (as `flags` array) | YES (`r.findings`) | Step 6 (use `r.findings`) |
| `ofac_hit` | `_analyze_documents (_risk_factors)` | NOT in result_json currently | NO | NO | Steps 1 + 2 + 4 + 5 |
| `section301_hit` | Same | Same | NO | NO | Steps 1 + 2 + 4 + 5 |
| `adcvd_hit` | Same | Same | NO | NO | Steps 1 + 2 + 4 + 5 |
| `uflpa_hit` | Same | Same | NO | NO | Steps 1 + 2 + 4 + 5 |
| `isf_complete` | Same (ISF gap logic) | Same | NO | NO | Steps 1 + 2 + 4 + 5 |
| `pattern_warnings` | `apply_pattern_adjustments` | `result_json → pattern_signals` | NO | NO | Steps 4 + 5 |
| `pattern_hard_flag` | `apply_pattern_adjustments` | `result_json → pattern_intelligence.hard_flag` | NO | NO | Steps 4 + 5 |
| `sustainability_certs_detected` | `SustainabilityRater` | `result_json → sustainability_rating.certifications_detected` | NO | NO | Steps 4 + 5 |
| `sustainability_certs_missing` | `SustainabilityRater` | `result_json → sustainability_rating.certifications_missing` | NO | NO | Steps 4 + 5 |
| `status` | `BulkProcessor` | flat col `status` | YES | YES | — already works |
| `error_detail` | `BulkProcessor` | flat col `error_message` | YES | YES (`r.error_message`) | Step 6 (rename + conditional blank) |
