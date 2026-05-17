# CSV Export Chain — Read Report
**Date:** 2026-05-15
**Sprint:** 11 — read-only audit

---

## 1. Button location in demo.html

**Line:** 4899
**Element:** `<button class="bulk-export-btn" id="bulk-export-csv-btn" onclick="bulkExportCsv()">`
**Label:** "Export CSV Summary"
**Container:** `<div class="bulk-export-bar">` — the export bar at the bottom of the bulk results screen (`#bulk-results-screen`), alongside the "Download All PDF Reports (ZIP)" and "Share Batch Results" buttons.

---

## 2. The CSV-generating function

**Function:** `bulkExportCsv()` — **demo.html line 11314**

**What it does:**
- Reads from the in-memory JavaScript array `_bulkAllResults` (populated after a bulk run completes)
- Builds a CSV string in memory using a manual `csvEsc()` quoting helper
- Creates a `Blob` and triggers a browser anchor click to download it
- Filename pattern: `portguard_batch_{batchId.slice(0,8)}.csv`

**Current columns written (in order):**

| CSV header | Source field on `_bulkAllResults` item | Notes |
|---|---|---|
| `reference_id` | `r.ref` | Shipment reference from input CSV |
| `decision` | `r.decision` | APPROVED / FLAGGED / REJECTED / ERROR |
| `risk_score` | `r.risk_score.toFixed(4)` | 4 decimal places |
| `risk_level` | `r.risk_level` | LOW / MEDIUM / HIGH |
| `n_findings` | `r.n_findings` | Count of flags |
| `top_finding` | `r.top_finding` | First flag string only |
| `sustainability_grade` | `r.sustainability_grade` | Letter grade (A–F) |
| `sustainability_signals` | `(r.sustainability_signals \|\| []).join(' \| ')` | Pipe-delimited string |
| `active_modules` | `(r.active_modules \|\| []).join(' \| ')` | Pipe-delimited string |
| `status` | `r.status` | COMPLETE / ERROR / TIMEOUT |
| `error_message` | `r.error_message` | Null → empty string |

---

## 3. Fields available on each `_bulkAllResults` item

`_bulkAllResults` is populated by `_bulkRenderFromResponse()` at **demo.html line 10865** (primary path for `POST /api/v1/analyze/bulk`) and by `renderBulkResults()` at line 10905 (older `/api/bulk/upload` path).

Every field present on each array item (primary path):

| Field | Type | Source |
|---|---|---|
| `ref` | string | `r.reference_id` from backend |
| `result_id` | string (UUID) | `r.result_id` from backend |
| `analysis_id` | string (UUID) | Same as `result_id` — compat alias for PDF download |
| `decision` | string | `r.decision` |
| `risk_score` | number \| null | `+r.risk_score` |
| `n_findings` | number | `(r.flags \|\| []).length` — derived |
| `top_finding` | string | `r.flags[0] \|\| r.summary \|\| ''` — derived |
| `findings` | string[] | Full `r.flags` array (all explanations) |
| `status` | string | `r.status` |
| `error_message` | string \| null | `r.error_message` |
| `sustainability_grade` | string \| null | `sus.grade` from `r.sustainability_rating` |
| `sustainability_signals` | string[] | `r.sustainability_signals` or `sus.signals` |
| `module_findings` | [] | Hardcoded empty — not in bulk POST response |
| `active_modules` | string[] | `r.active_modules_snapshot` |

`risk_level` is **not stored** on the `_bulkAllResults` item itself — it is written to CSV by reading `r.risk_level` directly from the backend response object that was passed into the mapper (the mapper doesn't assign it to the item). This means `_bulkAllResults[i].risk_level` is `undefined` at runtime; the CSV gets `''` for any row accessed post-render.

---

## 4. Backend bulk response shape (`_build_bulk_response`, api/app.py line 3757)

**Wrapper:**
```json
{
  "batch_id": "<uuid>",
  "total": 42,
  "processed": 42,
  "summary": { "total": 42, "approved": 30, "flagged": 8, "rejected": 3, "errors": 1 },
  "results": [ ... ]
}
```

**Each result item (13 fields):**
```json
{
  "reference_id":          "<shipment ref string>",
  "result_id":             "<analysis UUID>",
  "decision":              "APPROVED | FLAGGED | REJECTED | ERROR",
  "risk_score":            0.2341,
  "risk_level":            "LOW | MEDIUM | HIGH",
  "flags":                 ["<full explanation string>", ...],
  "summary":               "<top_finding string>",
  "sustainability_rating": { "grade": "B", "signals": [...], ... } | null,
  "sustainability_signals": ["<signal>", ...],
  "active_modules_snapshot": ["<module_name>", ...],
  "status":                "COMPLETE | ERROR | TIMEOUT",
  "error_message":         "<string> | null",
  "processed_at":          "<ISO timestamp> | null"
}
```

`flags` is the full `AnalyzeResponse.explanations` list — every finding string, not just the first.

---

## 5. Fields currently MISSING from the frontend CSV

### Missing despite being present on `_bulkAllResults` (no code change needed to add):

| Missing field | Available as | Why it matters |
|---|---|---|
| `result_id` / `analysis_id` | `r.result_id` | UUID needed to reconstruct the PDF download URL |
| `findings` (full list) | `r.findings` array | CSV only writes `top_finding` (first flag); all others are silently dropped |
| `processed_at` | present in backend response, not mapped to item | Needed for audit trail; backend CSV endpoint includes it |

### `risk_level` bug:
`risk_level` appears in the CSV header but the mapper (`_bulkRenderFromResponse`) never assigns it to the item object. The value `r.risk_level` in the `bulkExportCsv` forEach reads `undefined` → `''`.

### Missing because backend does not surface them through `_build_bulk_response`:

| Missing field | Where it exists | Notes |
|---|---|---|
| Shipper / exporter name | `AnalyzeResponse.shipment_data.shipper_name` | Not in bulk response |
| Origin country | `AnalyzeResponse.shipment_data.origin_iso2` | Not in bulk response |
| Destination country | `AnalyzeResponse.shipment_data` | Not in bulk response |
| Declared value + currency | `AnalyzeResponse.shipment_data` | Not in bulk response |
| HTS / tariff codes | `AnalyzeResponse.shipment_data` | Not in bulk response |
| Incoterms | `AnalyzeResponse.shipment_data` | Not in bulk response |
| `document_type` | `AnalyzeResponse.document_type` | Not in bulk response |
| `document_type_code` | `AnalyzeResponse.document_type_code` | Not in bulk response |
| `classification_confidence` | `AnalyzeResponse.classification_confidence` | Not in bulk response |
| `pattern_score` | `AnalyzeResponse.pattern_score` | Not in bulk response |
| `pattern_signals` | `AnalyzeResponse.pattern_signals` | Not in bulk response |
| `processing_time_seconds` | `AnalyzeResponse.processing_time_seconds` | Not in bulk response |
| `module_findings` (per-module) | stored in `result_json` blob | Not extracted in `_build_bulk_response` |
| Sustainability sub-scores | `sustainability_rating.country_risk_level`, `.product_risk_level` | Grade only is surfaced, not the sub-scores |

---

## 6. Frontend vs backend generation

**The frontend CSV is generated entirely client-side** — `bulkExportCsv()` reads `_bulkAllResults` (an in-memory JS array) and produces a `Blob` with no server request.

A separate backend endpoint also exists:

| | Frontend CSV | Backend CSV |
|---|---|---|
| Trigger | `onclick="bulkExportCsv()"` | `GET /api/v1/analyze/bulk/{batch_id}/export/csv` (line 4313) |
| Data source | `_bulkAllResults` in-memory array | `PatternDB.get_export_rows()` — direct SQL query |
| Authentication | None (reads JS array) | Bearer token required |
| Invoked by UI | Yes | No (button not wired to this endpoint) |

The backend endpoint is never called by the frontend. The "Export CSV" button exclusively uses the client-side path.

---

## 7. Delimiter, encoding, and header row format

**Frontend `bulkExportCsv()`:**
- **Delimiter:** comma (`,`)
- **Line terminator:** `\n` (LF only — single newline, not CRLF)
- **Encoding:** UTF-8, no BOM (Blob type is `'text/csv'` with no `charset` declaration)
- **Header row:** single row, comma-separated bare strings, no quoting
- **Multi-value fields:** pipe-delimited with spaces: `value1 | value2 | value3` (produced by `.join(' | ')`)
- **Quoting rule:** RFC 4180-like — wraps in `"..."` and doubles internal quotes only if the value contains `,`, `"`, or `\n`

**Backend `GET /api/v1/analyze/bulk/{batch_id}/export/csv` (line 4362):**
- **Delimiter:** comma (`,`)
- **Line terminator:** `\r\n` (CRLF — Python `csv.DictWriter` with `lineterminator="\r\n"`)
- **Encoding:** UTF-8 (`media_type="text/csv; charset=utf-8"`)
- **Streaming:** yes (`StreamingResponse`, one row per yield)
- **Filename:** `PortGuard_Batch_{batch_id[:8]}_{YYYY-MM-DD}.csv`

The two exports use different line terminators (frontend `\n` vs backend `\r\n`) and different filename conventions.

---

## 8. Columns that are too vague or should be split

| Current column | Problem | Recommended replacement |
|---|---|---|
| `top_finding` | Only first flag; `n_findings` says N flags exist but only 1 is shown | Add `all_findings` column (full `findings` array, pipe-delimited) alongside `top_finding` |
| `sustainability_signals` | Raw pipe-delimited string; no sub-scores | Add `sustainability_country_risk` and `sustainability_product_risk` columns extracted from `sustainability_rating` object |
| `active_modules` | Pipe-delimited list with no per-module outcomes | Acceptable as-is for summary; module results need `module_findings` column if detail is required |
| `risk_score` | 4 decimal places of precision implies false accuracy | `risk_level` column alongside it is fine, but `risk_score` should be 2 decimal places |
| `n_findings` | Count alone is not auditable | Pair with full `all_findings` column so the count is verifiable |
| *(missing)* `result_id` | UUID absent — no way to trace to PDF or re-query result | Add `result_id` column |
| *(missing)* `processed_at` | No timestamp — no audit trail | Add `processed_at` column (already in backend response, already in backend CSV endpoint) |
| *(bug)* `risk_level` | Mapper never assigns `risk_level` to item; column is always empty | Fix mapper in `_bulkRenderFromResponse`: add `risk_level: r.risk_level` |
