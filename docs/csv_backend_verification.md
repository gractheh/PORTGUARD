# CSV Backend Verification
**Date:** 2026-05-17
**Sprint:** 12 — backend changes for detailed CSV export

---

## Files changed

| File | Change |
|---|---|
| `portguard/bulk_processor.py` | Added `compute_risk_level()` helper; augmented result dict in `_store_shipment_result()` |
| `api/app.py` | Added 5 compliance-hit fields to `AnalyzeResponse`; computed them in `_run_bulk_single_analysis()`; expanded `_build_bulk_response()` |

---

## Change 1 — `compute_risk_level(score: float) -> str` (`portguard/bulk_processor.py` ~line 95)

New module-level helper. Input is a **0–10 scale** score (internal 0–1 score × 10).

| Input range | Output |
|---|---|
| 0.0 – 2.0 | `"LOW"` |
| 2.1 – 4.0 | `"MEDIUM"` |
| 4.1 – 7.0 | `"HIGH"` |
| 7.1 – 10.0 | `"CRITICAL"` |

This function is exported at module level and importable by `api/app.py` if needed.

---

## Change 2 — `_store_shipment_result()` augments result dict (`portguard/bulk_processor.py`)

Before `json.dumps(result)`, two fields are injected:

```python
result["name"] = ref           # shipment_ref from the batch manifest
result["timestamp"] = now      # UTC ISO timestamp of when the shipment was stored
```

**Why here:** `_run_bulk_single_analysis()` receives only `documents_data` and `org_id` — it has no access to the shipment reference string. `_store_shipment_result()` is the earliest point where both `ref` (the shipment name) and `result` (the pipeline output dict) are available together. Adding them here means `result_json` stored in the DB will always carry `name` and `timestamp` for later extraction.

**Coverage — success case:** Both fields are injected for every `COMPLETE` shipment.

**Coverage — error case:** `_store_shipment_error()` is called instead of `_store_shipment_result()` for `ERROR`/`TIMEOUT` shipments. Those rows have no `result` dict, so `name` and `timestamp` are not in their `result_json` (which is NULL). `_build_bulk_response()` falls back to `s.get("ref")` for name and `s.get("processed_at")` for timestamp from the flat DB columns — both are always populated.

---

## Change 3 — Five compliance-hit fields added to `AnalyzeResponse` (`api/app.py` ~line 1227)

New optional bool fields on the Pydantic model:

```python
ofac_hit:       Optional[bool] = None
section301_hit: Optional[bool] = None
adcvd_hit:      Optional[bool] = None
uflpa_hit:      Optional[bool] = None
isf_complete:   Optional[bool] = None
```

**Backward compatibility:** All five default to `None`. Existing callers that do not reference these fields are unaffected. The fields are serialized into `result_json` by `analyze_response.model_dump_json()`.

---

## Change 4 — Compliance hit computation in `_run_bulk_single_analysis()` (`api/app.py` ~line 3556)

Computed after `_analyze_documents(docs)` returns (so `result["explanations"]` is available) and before `AnalyzeResponse(...)` is constructed.

### Detection rules (match task specification)

| Field | Triggers when... |
|---|---|
| `ofac_hit` | Any flag contains `"ofac"` or `"sanction"` (case-insensitive) |
| `section301_hit` | Any flag contains `"section 301"` or `" 301 "` (space-padded to avoid false positives on HTS codes ending in 301) |
| `adcvd_hit` | Any flag contains `"ad/cvd"`, `"antidumping"`, or `"countervailing"` |
| `uflpa_hit` | Any flag contains `"uflpa"`, `"forced labor"`, or `"xinjiang"` |
| `isf_complete` | See below |

### `isf_complete` logic

```
_is_sea = has vessel_or_flight OR port_of_loading OR port_of_discharge OR port_of_entry
_isf_flagged = any "isf incomplete" in flag text

isf_complete = None              if not _is_sea  (air/unknown — ISF not applicable)
isf_complete = not _isf_flagged  if _is_sea      (True = complete, False = incomplete)
```

**Coverage — success case:** `result["explanations"]` is always populated for successful analysis (the fallback fills default "no sanctions detected" messages if otherwise empty). All five values are reliably computed.

**Coverage — error case:** `_run_bulk_single_analysis()` raises an exception for error cases — `_store_shipment_error()` is called instead. The `AnalyzeResponse` is never built, so compliance hit fields are not set. In `_build_bulk_response()`, these fields come from `full.get(...)` on an empty `full` dict → all return `None`. This is correct — compliance hits are unknown for errored shipments.

---

## Change 5 — `_build_bulk_response()` expanded (`api/app.py` ~line 3866)

The per-result dict grew from 13 keys to 35 keys. All 25 target CSV columns are now present in the API response. Complete field list:

### Identity
| Key | Source | Notes |
|---|---|---|
| `name` | `full.get("name") or s.get("ref")` | Injected by `_store_shipment_result`; falls back to flat col |
| `reference_id` | `s.get("ref")` | Unchanged |
| `result_id` | `s.get("analysis_id")` | Unchanged |
| `timestamp` | `full.get("timestamp") or s.get("processed_at")` | Injected; falls back to flat col |

### Compliance decision
| Key | Source | Notes |
|---|---|---|
| `decision` | `s.get("decision") or "ERROR"` | Unchanged |
| `risk_score` | `s.get("risk_score")` | **0–1 scale** — unchanged, used by frontend display |
| `risk_score_scaled` | `round(risk_score * 10, 1)` | **0–10 scale** — new field for CSV export |
| `risk_level` | `s.get("risk_level")` | Unchanged (backend thresholds: ≤0.25/≤0.50/≤0.75/>0.75) |

### Shipment identity (all new)
| Key | Source |
|---|---|
| `document_type` | `full.get("document_type")` |
| `shipper` | `full["shipment_data"]["exporter"]` |
| `origin_country` | `full["shipment_data"]["origin_country"]` |
| `destination_country` | `full["shipment_data"]["destination_country"]` |
| `declared_value` | `declared_value + " " + declared_currency` (stripped) |
| `hts_codes` | `full["shipment_data"]["hts_codes_declared"]` (list) |
| `hts_code` | `" | ".join(hts_codes)` (string) |

### Flags
| Key | Source | Notes |
|---|---|---|
| `flags` | `full["explanations"]` | Full list — unchanged |
| `flags_count` | `len(flags)` | New derived field |
| `flags_detail` | `" | ".join(flags)` | New joined string |

### Compliance hit booleans (all new)
| Key | Source |
|---|---|
| `ofac_hit` | `full.get("ofac_hit")` — set by pipeline |
| `section301_hit` | `full.get("section301_hit")` |
| `adcvd_hit` | `full.get("adcvd_hit")` |
| `uflpa_hit` | `full.get("uflpa_hit")` |
| `isf_complete` | `full.get("isf_complete")` |

### Pattern intelligence (all new)
| Key | Source |
|---|---|
| `pattern_warnings` | `full.get("pattern_signals")` — list of strings |
| `pattern_hard_flag` | `full["pattern_intelligence"]["hard_flag"]` — bool |

### Sustainability
| Key | Source | Notes |
|---|---|---|
| `sustainability_rating` | `sus` dict | Unchanged |
| `sustainability_grade` | `sus_grade` string | New flat field alongside rating dict |
| `sustainability_signals` | `sus_signals` list | Unchanged |
| `sustainability_certs_detected` | `full["sustainability_rating"]["certifications_detected"]` | New |
| `sustainability_certs_missing` | `full["sustainability_rating"]["certifications_missing"]` | New |

### Status / error (enhanced)
| Key | Source | Notes |
|---|---|---|
| `status` | `s.get("status")` | Unchanged |
| `error_message` | `s.get("error_message")` | Unchanged |
| `error_detail` | `s.get("error_message") or ""` | New alias — blank string instead of null |
| `processed_at` | `s.get("processed_at")` | Unchanged |

---

## Verification — fields present for every case

### Successful shipment (COMPLETE)
All 35 fields are populated. Fields that depend on `full_result` (shipment identity, compliance hits, pattern intel, cert lists) are sourced from `result_json` which is always written for COMPLETE rows. `name` and `timestamp` are injected by `_store_shipment_result()` before `json.dumps`.

### Error / timeout shipment (ERROR or TIMEOUT)
`full` dict is empty (`result_json` is NULL). Fields that depend on `full` gracefully degrade:
- `name`: falls back to `s.get("ref")` ✓
- `timestamp`: falls back to `s.get("processed_at")` ✓
- `shipper`, `origin_country`, etc.: return `""` ✓
- `flags`: populated from `s.get("error_message")` if set ✓
- `flags_count`: `len(flags)` — 0 or 1 ✓
- `ofac_hit` / `section301_hit` / etc.: `None` (unknown for errors) ✓
- `error_detail`: `s.get("error_message") or ""` ✓

### Shipment with no sustainability data (modules disabled)
`_sus_full` is `{}`. `certifications_detected` and `certifications_missing` both return `[]`. `sustainability_grade` is `None`. No crash.

### Shipment with no pattern engine (PatternDB unavailable)
`full.get("pattern_intelligence")` is `None` → `_patt = {}` → `pattern_hard_flag = False`. `full.get("pattern_signals")` is `[]`. No crash.

---

## What was NOT changed

- `risk_level` in the API response still uses the backend's existing thresholds (≤0.25, ≤0.50, ≤0.75, >0.75 on the 0–1 scale). The CSV export frontend (future sprint) should use `risk_score_scaled` with `compute_risk_level()` for the plan's 0–10 thresholds.
- `risk_score` in the API response remains on the 0–1 scale. `risk_score_scaled` (0–10) is the new field for CSV.
- `demo.html` was not touched. The frontend mapper and CSV generator will be updated in the next sprint.
- The single-shipment `POST /api/v1/analyze` endpoint was not modified. The five new `AnalyzeResponse` fields default to `None` for that path — they will be populated in a future sprint if needed.
