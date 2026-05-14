# Classifier False-Positive Audit
**Date:** 2026-05-14  
**File:** `portguard/agents/document_classifier_hardened.py`  
**Issue:** Legitimate shipping-adjacent compliance/quality certificates are being rejected.

---

## 1. Scoring Trace — Three Target Documents

### 1a. Certificate of Analysis

**Input:**  
> "Certificate of Analysis — Product: Cotton Yarn Batch #CY-2024-0891. Supplier: Guangdong
> Textile Mill. Test results: tensile strength 42N, moisture content 8.2%. Batch approved
> for export. Issued by: QC Department."

| Layer | Result |
|---|---|
| Pro score | **~1.5** — only `\bexport\b` fires. "Certificate", "Supplier", "Batch", "QC", "approved for export", "test results" — none of these exist in `PRO_SHIPPING_TERMS`. |
| Fingerprint | **None** — no BOL/Invoice/Packing List/COO/WIC match. "Weight / Inspection Certificate" requires `\b(weight\|inspection)\s+certificate\b`; "Certificate of Analysis" does not match. |
| Hard signals | **~0** — no HTS code, no container number, no port name. |
| Anti score | **0** — no anti-patterns fire. |
| Decision | `total_pro (1.5) < 2.0 AND not doc_type` → **GENERIC REJECT** |

**Root cause:** Zero fingerprint match + sub-threshold pro score → hits the `total_pro < 2.0 and not doc_type` guard.

---

### 1b. Factory Compliance Certificate

**Input:**  
> "Factory Compliance Certificate — This certifies that Shenzhen Electronics Manufacturing
> Co. meets all required standards for export production. Facility ID: SZ-FAC-00234.
> Issued under ISO 9001."

| Layer | Result |
|---|---|
| Pro score | **~1.5** — only `\bexport\b` fires. "Factory", "Compliance", "Certificate", "Manufacturing", "meets", "standards", "Facility", "ISO 9001" — none in PRO_SHIPPING_TERMS. |
| Fingerprint | **None** — "Factory Compliance Certificate" does not match any of the 20 known fingerprints. |
| Hard signals | **~0** |
| Anti score | **0** |
| Decision | `total_pro (1.5) < 2.0 AND not doc_type` → **GENERIC REJECT** |

**Root cause:** Same as COA — no vocabulary match and no fingerprint.

---

### 1c. Supplier Compliance Declaration

**Input:**  
> "Supplier Compliance Declaration — We, Vietnam Garment Co., declare that goods supplied
> under PO #VGC-2024-0312 comply with all applicable regulations including REACH, RoHS,
> and country of origin requirements."

| Layer | Result |
|---|---|
| Pro score | **~3.0** — `\bcountry\s+of\s+origin\b` (3.0) fires. "Supplier", "Compliance", "Declaration", "PO #", "REACH", "RoHS", "goods supplied", "comply with all applicable" — none in PRO_SHIPPING_TERMS. |
| Fingerprint | **None** |
| Hard signals | **~0** |
| Anti score | **0** |
| Decision | total_pro 3.0 > 2.0, anti_ratio 0.0 → not rejected by anti-guard. Confidence = `1 - 1/(1+exp((3-12)/6))` = **0.18** — below LOW_THRESHOLD (0.35) with no doc_type → **CONFIDENCE REJECT** |

**Root cause:** `country of origin` gives 3.0 pro points but the sigmoid confidence formula maps 3.0 → 0.18, and without a fingerprint floor (0.40) this falls below the accept threshold.

---

## 2. Anti-Patterns Firing Incorrectly

**None fire on these three documents.** The rejection is entirely pro-signal starvation — these documents use a legitimate compliance vocabulary that does not exist in `PRO_SHIPPING_TERMS` and doesn't match any of the 20 fingerprints.

---

## 3. Pro-Patterns Not Firing That Should

All of the following appear naturally in trade compliance/quality documents but are absent from `PRO_SHIPPING_TERMS`:

| Term / Phrase | Example in docs |
|---|---|
| `certificate of analysis` | "Certificate of Analysis" |
| `factory compliance` | "Factory Compliance Certificate" |
| `supplier compliance` / `supplier declaration` | "Supplier Compliance Declaration" |
| `quality inspection` / `quality certificate` | "quality inspection report.txt" |
| `mill test certificate` | "mill_test_certificate.txt" |
| `batch number` / `batch approved` | "Batch #CY-2024-0891", "Batch approved" |
| `test results` / `test report` | "Test results: tensile strength 42N" |
| `QC approved` / `QC department` | "Issued by: QC Department" |
| `approved for export` | "Batch approved for export" |
| `ISO 9001 / 14001 / 45001` | "Issued under ISO 9001" |
| `REACH` / `RoHS` | "REACH, RoHS" |
| `PO #` / `purchase order` | "PO #VGC-2024-0312" |
| `facility ID` | "Facility ID: SZ-FAC-00234" |
| `export production` | "export production" |
| `goods supplied` | "goods supplied under PO" |
| `comply with all applicable` | "comply with all applicable regulations" |
| `country of origin requirement` | "country of origin requirements" |
| `traceability` | "supply chain traceability" |
| `supply chain` | "Supply chain traceability confirmed" |

No fingerprint for **Compliance / Quality Certificate (CQC)** exists — this entire document class is unrecognized.

---

## 4. Anti-Pattern Over-Firing (Separate Risk)

Two existing anti-patterns are too broad and risk false positives on trade docs:

| Pattern | Problem |
|---|---|
| `\bdosage\b\|\b(mg\|mcg)\s+per\s+(day\|dose\|kg)\b` (MEDICAL 4.0) | "dosage" alone fires — fumigation certificates legitimately use "dosage" of fumigant |
| `\b(semester\|quarter\|term\|course\|credit\s+hours?\|enrollment)\b` (ACADEMIC 3.0) | "term" fires — "payment term" is standard trade vocabulary; "term" in trade documents should not trigger ACADEMIC |
| `\bmethodology\b\|\bresearch\s+method\b` (ACADEMIC 3.0) | "methodology" fires — compliance audit methodology is legitimate |
| `\b(allergy\|symptom\|treatment\s+plan\|medication\|antibiotic)\b` (MEDICAL 3.0) | "medication" fires in isolation — could appear in pharmaceutical cargo docs |

---

## 5. Fix Plan

1. **Add 35 pro-shipping terms** covering certificate, compliance, quality, audit, ISO, REACH, RoHS, PO, batch, lot, QC vocabulary.
2. **Add CQC fingerprint** — "Compliance / Quality Certificate" with flexible required + supporting patterns.
3. **Tighten 4 anti-patterns** — require clinical/academic context words to fire; bare "dosage", "term", "methodology" no longer trip medical/academic categories.
4. **Raise anti-dominate threshold to 0.75 when fingerprint matched** — a document with an identified type needs a much stronger anti-signal to get rejected.
