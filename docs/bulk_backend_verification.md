# Bulk Backend Verification Notes
**Date:** 2026-05-15
**Scope:** Manual trace and runtime verification of all 5 backend fixes

---

## Verification 1 — Field name is 'file' in the endpoint

**Check:** `@app.post("/api/bulk/upload")` uses `file: Optional[UploadFile] = File(None)`

**Result:** PASS

```
grep result from api/app.py:
  4705: @app.post("/api/bulk/upload", status_code=200)
  4707:     file: Optional[UploadFile] = File(None),
```

The parameter is named `file` exactly as specified. File type is detected by `filename.endswith(".zip")`, `.endswith(".csv")`, `.endswith(".txt")`, `.endswith(".pdf")` — filename extension only, never by `Content-Type` header.

---

## Verification 2 — CSV parser handles files with only a 'text' column

**Input:**
```
text
Shipper: ABC Corp to Miami from CN
Bill of Lading - Consignee: XYZ
```

**Result:** PASS — 2 shipments extracted, names "Row 1" and "Row 2".

`text_col_raw` is set when a column named `text` is found (case-insensitive). Each row's value from that column is used directly. Rows under 10 chars are skipped.

---

## Verification 3 — CSV parser handles files with shipper/consignee columns

**Input:**
```
shipper,consignee,origin,destination,value,hts
ABC Corp,XYZ Inc,CN,US,5000,8471.30
```

**Result:** PASS — 1 shipment extracted with reconstructed text:
```
Shipper: ABC Corp
Consignee: XYZ Inc
Origin: CN
Destination: US
Value: 5000
HTS: 8471.30
```

When no text/document column is found, `struct_map` accumulates the recognised structured columns and reconstructs a labelled document string. The CSV parser handles BOM-prefixed UTF-8 via `lstrip("﻿")` after decoding with `errors='ignore'`.

---

## Verification 4 — ZIP parser skips \_\_MACOSX entries

**Test ZIP contents:**
- `__MACOSX/._document.txt` — macOS metadata
- `.DS_Store` — macOS desktop services
- `.hidden.txt` — hidden dot-file
- `SHP001/bill_of_lading.txt` — valid TXT
- `SHP002/shipment.csv` — valid CSV (1 row)
- `SHP003/unknown.xyz` — unsupported extension

**Result:** PASS — 2 shipments extracted. `__MACOSX`, `.DS_Store`, `.hidden.txt`, and `unknown.xyz` were all skipped. No hidden filenames appear in the output.

Skip logic (in `extract_shipments_from_zip`):
```python
if any(p.startswith("__MACOSX") for p in parts):
    continue
if base == ".DS_Store" or base.startswith("."):
    continue
```

---

## Verification 5 — bulk_classify does NOT reject shipping metadata

**Input:** `"Shipper: ABC Corp, Consignee: XYZ Inc, Origin: CN"`

**Result:** PASS — `bulk_classify` returns `True`

Full trace:
- `len(text.strip())` = 49 ≥ 20 → not rejected by length check
- Hard reject patterns checked:
  - `resume|curriculum vitae` → no match (score 0)
  - `work experience` → no match (score 0)
  - `shopping list|grocery list` → no match (score 0)
  - `preheat (the )?oven` → no match (score 0)
  - `patient (id|name|dob)` → no match (score 0)
  - `diagnosis.*(patient|physician)` → no match (score 0)
  - `gpa.*(university|college|school)` → no match (score 0)
  - `bachelor.*degree|master.*degree` → no match (score 0)
- Final score = 0, which is < 4 → returns `True`

Additional tested cases:
| Input | Expected | Result |
|---|---|---|
| `"Shipper: ABC Corp, Consignee: XYZ Inc, Origin: CN"` | True | PASS |
| `"short"` (< 20 chars) | False | PASS |
| `"John Smith - work experience at Google, Bachelor degree in CS"` | False | PASS (score ≥ 4) |
| `"Preheat the oven to 375F, mix butter and sugar"` | False | PASS |
| `"Bill of Lading - Shipper: Shenzhen Tech, Consignee: ACME Corp, HTS: 8542.31"` | True | PASS |
| Reconstructed CSV row (Shipper/Consignee/Origin/Destination/Value/HTS) | True | PASS |

---

## Verification 6 — process_bulk_shipments uses asyncio.gather

**Check:** `portguard/bulk_processor.py`

```
grep result:
  1048:     semaphore = asyncio.Semaphore(_SEMAPHORE_SIZE)
  1087:     results = await asyncio.gather(*tasks)
```

`asyncio.Semaphore(5)` bounds concurrency to `_SEMAPHORE_SIZE = 5` (matching the thread pool). `asyncio.gather(*tasks)` is called with all shipment coroutines at once — they run concurrently, bounded by the semaphore, and all complete before the result dict is returned.

---

## Architecture Summary — How the Fixes Connect

```
POST /api/bulk/upload  (api/app.py)
    │
    ├── file.filename.endswith(".csv") → extract_shipments_from_csv(content)
    ├── file.filename.endswith(".zip") → extract_shipments_from_zip(content)
    ├── file.filename.endswith(".txt") → single-shipment dict
    ├── file.filename.endswith(".pdf") → extract_text() → single-shipment dict
    └── manual_texts → parse_manual_batch(manual_texts)
                         ↓
               [{"text": str, "name": str}, ...]
                         ↓
    process_bulk_shipments(shipments, org_email, module_config, db)
                         ↓
    asyncio.gather([process_one(s, i) for i, s in enumerate(shipments)])
         │                    ↓
         │          bulk_classify(shipment["text"])
         │          ├── False → {"status": "rejected", "decision": "REJECTED"}
         │          └── True  → run_full_pipeline(text, org_email, module_config, db)
         │                              ↓
         │               _module_pipeline_fn (registered at startup)
         │               = partial(_run_bulk_single_analysis, skip_classifier_gate=True)
         │                              ↓
         │               Full 6-stage pipeline:
         │               rule engine → pattern engine → CertificationScreener
         │               → SustainabilityRater → AnalyzeResponse.model_dump()
         │
         └── {"status": "complete", "decision": ..., "risk_score": ..., ...}
                         ↓
    {"total": N, "results": [...], "summary": {...}}
```

**Key invariant:** `skip_classifier_gate=True` is ONLY passed via the registered partial — the existing `/api/v1/analyze/bulk` endpoint continues to use `_run_bulk_single_analysis` with `skip_classifier_gate=False` (default), so the hardened classifier gate is unchanged for that path.

---

## Files Changed

| File | What changed |
|---|---|
| `portguard/bulk_processor.py` | Added `import re`; added `_module_pipeline_fn`, `register_pipeline()`, `bulk_classify()`, `run_full_pipeline()`, `process_bulk_shipments()` |
| `api/app.py` | Added `Any, Generator` to typing imports; added `Form` to fastapi imports; added `skip_classifier_gate: bool = False` param to `_run_bulk_single_analysis`; wrapped rejection check with `and not skip_classifier_gate`; added pipeline registration block; added `get_db()`, `extract_shipments_from_csv()`, `extract_shipments_from_zip()`, `parse_manual_batch()`, and `@app.post("/api/bulk/upload")` |

---

## No Regressions

- `_run_bulk_single_analysis` default: `skip_classifier_gate=False` — hardened gate active for `/api/v1/analyze/bulk`
- Existing `BulkProcessor.process_batch` — untouched, still uses hardened gate
- All auth endpoints — untouched
- Pattern learning endpoints — untouched
- Analytics endpoints — untouched
- `_build_bulk_response` — untouched
- `demo.html` — not touched per task instructions
