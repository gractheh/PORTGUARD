# Classifier Hardening Audit

**Sprint:** Hardened Document Classifier — Troll-Proof Multi-Signal Detection  
**Date:** 2026-05-13

---

## 1. Current Classifier Path

| Component | Location | Purpose |
|-----------|----------|---------|
| `DocumentValidator` | `portguard/document_validator.py` | Pre-analysis gate — validates document type before pipeline runs |
| `validate_documents()` | `portguard/document_validator.py` | Module-level helper: runs `DocumentValidator` on a list of `Document` objects |
| `build_rejection_error()` | `portguard/document_validator.py` | Builds the 422 error dict from a list of `ValidationResult` objects |
| `DocumentClassifier` (hardened) | `portguard/agents/document_classifier_hardened.py` | Replacement — 5-layer defense, anti-pattern scoring, 19 doc-type fingerprints |
| `classify_document()` | `portguard/agents/document_classifier.py` | Public façade for the hardened classifier |

---

## 2. Document Type Coverage

### Original validator (6 types)
`BILL_OF_LADING`, `COMMERCIAL_INVOICE`, `PACKING_LIST`, `CERTIFICATE_OF_ORIGIN`, `ARRIVAL_NOTICE`, `ISF_FILING`

### Hardened classifier (20 types via `DocumentValidator`, 19 fingerprints via `DocumentClassifier`)
All originals plus: `AIRWAY_BILL`, `FREIGHT_INVOICE`, `LETTER_OF_CREDIT`, `CARGO_MANIFEST`, `DOCK_RECEIPT`, `CUSTOMS_ENTRY`, `EXPORT_DECLARATION`, `PHYTOSANITARY_CERTIFICATE`, `FUMIGATION_CERTIFICATE`, `INSPECTION_CERTIFICATE`, `WEIGHT_CERTIFICATE`, `DANGEROUS_GOODS_DECLARATION`, `CARGO_INSURANCE`, `DELIVERY_ORDER`

---

## 3. How the Endpoint Calls the Classifier

### `/api/v1/analyze` (primary path)
```
POST /api/v1/analyze
  → classify_document(doc.raw_text)  for each Document   [hardened classifier — new]
    → if any rejected: raise HTTPException 422
       detail.code == "DOCUMENT_VALIDATION_FAILED"
       detail.rejected_documents[].rejection_category    [NEW field]
  → _validate_documents(documents)                        [old validator — for metadata only]
  → _analyze_documents(documents)                         [core rule engine]
  → AnalyzeResponse(
        document_type=...,          [NEW]
        document_type_code=...,     [NEW]
        classification_confidence=..., [NEW]
        classification_warning=..., [NEW]
        ...
    )
```

### `/api/v1/analyze-files` (file upload path)
Same gate — hardened classifier runs after file extraction, before core pipeline.

### `_run_bulk_single_analysis()` (bulk batch path)
Same gate — raises `ValueError` on rejection (caught per-shipment by `BulkProcessor`).

---

## 4. How the Frontend Handles Rejection (Before This Sprint)

```javascript
// fetch handler (demo.html ~line 7349)
if (res.status === 422) {
  const detail = err.detail || {};
  if (detail.code === 'DOCUMENT_VALIDATION_FAILED' && detail.rejected_documents) {
    fetchData = { _rejection: true, docs: detail.rejected_documents };
  }
}

// render path (~line 7420)
if (fetchData && fetchData._rejection) {
  showRejectionScreen(fetchData.docs);
}

// showRejectionScreen (~line 7539)
// Renders: filename + doc.message (generic "not a trade document")
// No rejection category, no doc-type pill, no accepted-types grid
```

---

## 5. What Changed in This Sprint

### Backend (`api/app.py`)
- Added `classify_document` import from `portguard.agents.document_classifier`
- In all three analysis paths (analyze, analyze-files, bulk): hardened classifier runs first; old `_validate_documents` kept for `document_validations` metadata
- New 422 payload includes `rejection_category` per rejected document
- `AnalyzeResponse` model gains 4 new optional fields: `document_type`, `document_type_code`, `classification_confidence`, `classification_warning`

### Frontend (`demo.html`)
- `showRejectionScreen` — renders `rejection_category` pill badge per rejected doc
- `renderResults` — shows `classification_warning` banner when confidence is LOW; shows detected doc-type badge in decision banner
- CSS: `.rejection-cat-pill` styles for category badges; doc-type chip in decision banner

### New Classifier (`portguard/agents/document_classifier.py`)
- Thin re-export façade for `portguard.agents.document_classifier_hardened`
- Public API: `classify_document(text: str) -> dict`
- 11 self-tests in `__main__` block (run with `python -m portguard.agents.document_classifier`)

---

## 6. Anti-Pattern Categories Supported

| Category | Example triggers | Threshold to reject |
|----------|-----------------|---------------------|
| `RESUME` | "curriculum vitae", "work experience", GPA | Anti-score ≥ 12 or ratio ≥ 0.6 |
| `SHOPPING` | "grocery list", "aisle", retail food items | Same composite threshold |
| `ACADEMIC` | "abstract", "bibliography", "p-value" | Same |
| `MEDICAL` | "diagnosis", "patient id", ICD codes | Same |
| `LEGAL` | "plaintiff", "subpoena", "court of" | Same |
| `PERSONAL` | "dear sir/madam", "yours sincerely" | Same |
| `FINANCIAL` | "bank statement", "W-2", "FICO score" | Same |
| `REAL_ESTATE` | "lease agreement", "landlord", "escrow" | Same |
| `SOCIAL` | "@user", "hashtag", "retweet" | Same |
| `RECIPE` | "preheat the oven", cooking measurements | Same |
| `GENERIC` | No pro-signals AND no doc-type fingerprint | total_pro < 2.0 |

---

## 7. Confidence Tiers

| Label | Condition | Action |
|-------|-----------|--------|
| `HIGH` | raw_conf ≥ 0.75 | Accept silently |
| `MEDIUM` | raw_conf ≥ 0.50 | Accept silently |
| `LOW` | raw_conf ≥ 0.35 (or doc_type found and conf ≥ 0.40) | Accept + warning banner |
| `REJECTED` | anti dominates or no pro-signal | Hard block with category reason |

---

## 8. Test Coverage

- `tests/test_document_validator.py` — 74 tests for `DocumentValidator` (old gate, unchanged)
- `tests/test_document_classifier.py` — 11 tests for `DocumentValidator` with 20-type logic
- `tests/test_classifier_hardened.py` — smoke tests for hardened `classify_document()`: 5 troll rejections, 5 real doc accepts, 1 sparse LOW-confidence accept
