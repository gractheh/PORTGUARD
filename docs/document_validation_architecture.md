# PORTGUARD — Document Authenticity & Relevance Validation System
## Technical Architecture Document

**Version:** 1.0  
**Status:** Design — pre-implementation  
**Scope:** Pre-analysis input validation gate for all document ingestion paths

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [System Overview](#2-system-overview)
3. [Signal Architecture](#3-signal-architecture)
4. [Document Type Definitions](#4-document-type-definitions)
5. [Non-Trade Rejection Patterns](#5-non-trade-rejection-patterns)
6. [Confidence Scoring](#6-confidence-scoring)
7. [Validation Result Model](#7-validation-result-model)
8. [Multi-Document Validation](#8-multi-document-validation)
9. [Integration Points](#9-integration-points)
10. [User Messaging](#10-user-messaging)
11. [API Response Shape](#11-api-response-shape)
12. [Edge Cases and Boundary Conditions](#12-edge-cases-and-boundary-conditions)

---

## 1. Problem Statement

### Current Behavior

PortGuard accepts any string as a document. When a user submits a recipe, a tweet, or a paragraph of prose, the analysis pipeline runs to completion and returns a response with all fields empty, a zero risk score, and an APPROVE decision. This has two consequences:

- **False APPROVE**: Garbage input produces a green result, which an officer might interpret as a cleared shipment.
- **System credibility**: The product looks unreliable when users — intentionally or accidentally — test it with non-trade content.

### Root Cause

The `_analyze_documents()` function in `api/app.py` (line 1002) has no pre-flight check. It calls `_extract_shipment_data()` on whatever text it receives. When no regex patterns match, all fields return `None`, missing-field penalties accumulate, and the decision logic routes to APPROVE because no risk factors were triggered.

### What We Are Building

A **pre-analysis validation gate** — a new module (`api/document_validator.py`) that runs before `_analyze_documents()` is called in any endpoint. It answers two questions for each submitted document:

1. Is this a trade document at all?
2. If yes, what kind — and how complete does it appear to be?

The gate is **additive and non-destructive**: it never modifies documents, never changes the analysis logic downstream, and when it passes a document, behavior is identical to today. It only adds a new early-exit path for documents that would produce meaningless results.

---

## 2. System Overview

### Processing Flow

```
POST /api/v1/analyze          POST /api/v1/analyze-files
        │                               │
        │                               │ (after file extraction)
        ▼                               ▼
┌───────────────────────────────────────────────────────┐
│              validate_documents(documents)             │
│                                                       │
│  For each document:                                   │
│    1. Pre-flight checks (word count, emoji density,   │
│       URL density, non-trade pattern detection)       │
│    2. Signal scanning (count matched trade signals)   │
│    3. Document type classification (best-fit)         │
│    4. Confidence tier assignment                      │
│    5. Verdict: PASS / WARN / REJECT                   │
│                                                       │
│  After all documents checked:                         │
│    - Any REJECT → raise HTTP 422, stop here           │
│    - Any WARN → attach warnings to response           │
│    - All PASS → proceed                               │
└───────────────────────────────────────────────────────┘
        │
        ▼
_analyze_documents()  ← unchanged, only called on validated input
```

### Module Location

```
api/
  document_validator.py   ← new module (all validation logic lives here)
  app.py                  ← modified: calls validate_documents() in two endpoints
  document_parser.py      ← unchanged
```

The validator is a pure function module. It imports nothing from `app.py` and has no state. It can be unit tested in complete isolation.

---

## 3. Signal Architecture

### What Is a Signal

A **signal** is a named logical concept — "this document identifies a shipper party", "this document mentions a vessel" — satisfied by matching any keyword from that signal's keyword list against the document text.

Signals are not raw keyword counts. Two keywords from the same signal list count as one matched signal. This prevents a document that says "SHIPPER / EXPORTER / SHIPPER NAME" three times from appearing stronger than it is.

### Signal Matching Rules

- Matching is **case-insensitive** (`re.IGNORECASE`)
- Each signal's keyword list is checked with `\b` word boundaries to prevent partial matches (e.g., "port" should not match "report" or "import")
- Abbreviations and full-form variants are combined into the same signal's keyword list
- A signal is marked **matched** if at least one keyword from its list appears anywhere in the document text
- The total count of distinct matched signals — across all categories — determines the confidence tier
- Document type classification uses the signal category with the most matched signals, subject to a minimum threshold

### Signal Categories

There are two tiers of signals:

**Type-specific signals**: Signals that belong to a particular document type. A B/L signal set, an invoice signal set, etc. Matching enough signals from one set classifies the document as that type.

**General trade signals**: Terms that appear in multiple document types and indicate trade context without pointing to a specific type. These contribute to the total matched signal count used for the UNRECOGNIZED_TRADE case but do not count toward any specific type's classification threshold.

---

## 4. Document Type Definitions

### 4.1 Bill of Lading

**Classification threshold:** 3 distinct signals matched from this set (7 signals defined).

| Signal Name | Keywords |
|---|---|
| `shipper_party` | `shipper`, `shipper/exporter`, `shipper exporter`, `consignor` |
| `consignee_party` | `consignee`, `notify party`, `notify`, `to order`, `to the order of` |
| `vessel_reference` | `vessel`, `vessel name`, `m/v`, `mv `, `s/s`, `ship name`, `ocean vessel`, `carrier` |
| `port_reference` | `port of loading`, `port of discharge`, `pol`, `pod`, `loading port`, `discharge port`, `port of origin`, `place of receipt` |
| `bl_number` | `bill of lading`, `b/l no`, `b/l number`, `b/l #`, `bol`, `bol no`, `bol number`, `master b/l`, `house b/l`, `mbl`, `hbl` |
| `weight_quantity` | `gross weight`, `net weight`, `g.w.`, `n.w.`, `total weight`, `packages`, `no. of packages`, `number of packages`, `pieces`, `cartons`, `pallets` |
| `freight_terms` | `freight prepaid`, `freight collect`, `freight payable`, `prepaid`, `collect`, `incoterms`, `fob`, `cif`, `cfr`, `exw` |

**Example REJECT case**: A bill of lading that has shipper and consignee but no vessel, port, B/L number, weight, or freight terms — only 2 signals — is classified LOW confidence, not a B/L.

---

### 4.2 Commercial Invoice

**Classification threshold:** 3 distinct signals matched from this set (7 signals defined).

| Signal Name | Keywords |
|---|---|
| `seller_party` | `seller`, `exporter`, `shipper`, `sold by`, `from`, `manufacturer` |
| `buyer_party` | `buyer`, `consignee`, `importer`, `sold to`, `bill to`, `ship to` |
| `invoice_identifier` | `invoice no`, `invoice number`, `invoice #`, `inv no`, `inv. no`, `commercial invoice`, `proforma invoice`, `invoice date` |
| `value_price` | `unit price`, `unit cost`, `total price`, `total value`, `total amount`, `invoice value`, `grand total`, `subtotal`, `amount due`, `extended price` |
| `commodity_description` | `description of goods`, `commodity`, `item description`, `goods`, `product`, `merchandise`, `description` |
| `hs_hts` | `hs code`, `hts code`, `hts`, `hs`, `h.s.`, `harmonized`, `tariff code`, `tariff number` |
| `currency_mark` | `usd`, `eur`, `gbp`, `cny`, `rmb`, `jpy`, `cad`, `aud`, `total usd`, `total eur` |

---

### 4.3 Packing List

**Classification threshold:** 3 distinct signals matched from this set (6 signals defined).

| Signal Name | Keywords |
|---|---|
| `packing_identifier` | `packing list`, `packing slip`, `pack list`, `pl no`, `p/l`, `p.l.` |
| `package_count` | `number of packages`, `no. of packages`, `total packages`, `total cartons`, `total pieces`, `total units`, `case count`, `package count` |
| `weight_signal` | `gross weight`, `net weight`, `g.w.`, `n.w.`, `kgs`, `lbs`, `metric tons`, `m.t.` |
| `dimensions` | `dimensions`, `measurement`, `cbm`, `cubic meter`, `cubic metres`, `l x w x h`, `length`, `width`, `height` |
| `marks_numbers` | `marks and numbers`, `marks & numbers`, `shipping marks`, `case marks`, `container number`, `seal number`, `container no`, `cntr no` |
| `item_detail` | `item no`, `item number`, `description`, `quantity`, `qty`, `per carton`, `pcs/ctn` |

---

### 4.4 Certificate of Origin

**Classification threshold:** 3 distinct signals matched from this set (5 signals defined).

| Signal Name | Keywords |
|---|---|
| `origin_declaration` | `country of origin`, `origin country`, `made in`, `manufactured in`, `produced in`, `place of origin`, `country of manufacture` |
| `certifying_authority` | `chamber of commerce`, `chamber of industry`, `certifying authority`, `certifying body`, `we hereby certify`, `hereby certify`, `this is to certify` |
| `co_identifier` | `certificate of origin`, `cert of origin`, `c/o`, `co no`, `co number`, `form a`, `gsp form`, `nafta certificate`, `usmca certificate` |
| `exporter_detail` | `exporter`, `producer`, `manufacturer`, `applicant`, `company name`, `shipper` |
| `authentication` | `signature`, `stamp`, `seal`, `authorized signatory`, `signatory`, `duly authorized`, `notarized`, `legalized` |

---

### 4.5 Arrival Notice

**Classification threshold:** 3 distinct signals matched from this set (5 signals defined).

| Signal Name | Keywords |
|---|---|
| `arrival_identifier` | `arrival notice`, `delivery notice`, `arrival notification`, `pre-arrival`, `cargo arrival` |
| `vessel_eta` | `vessel`, `vessel name`, `m/v`, `eta`, `estimated time of arrival`, `estimated arrival`, `arrival date`, `expected arrival` |
| `discharge_port` | `port of discharge`, `pod`, `discharge port`, `destination port`, `port of destination`, `terminal` |
| `consignee_notify` | `consignee`, `notify party`, `notify`, `advise party` |
| `cargo_reference` | `b/l no`, `b/l number`, `bol`, `container no`, `container number`, `booking no`, `booking number` |

---

### 4.6 ISF Filing

**Classification threshold:** 3 distinct signals matched from this set (6 signals defined).

| Signal Name | Keywords |
|---|---|
| `isf_identifier` | `isf`, `importer security filing`, `10+2`, `10 plus 2`, `cbp form`, `entry filing` |
| `importer_of_record` | `importer of record`, `ior`, `ein number`, `employer identification`, `cbp bond`, `bond number` |
| `carrier_scac` | `scac`, `scac code`, `standard carrier alpha code`, `carrier code`, `vessel name`, `carrier name` |
| `hts_element` | `hts code`, `hs code`, `hts`, `harmonized tariff`, `tariff number`, `6-digit`, `10-digit` |
| `manufacturer_supplier` | `manufacturer`, `supplier`, `producer`, `manufacturer address`, `country of origin`, `isf element 5` |
| `isf_timing` | `24 hours`, `24-hour rule`, `vessel departure`, `sailing date`, `19 cfr 149`, `149.2` |

---

### 4.7 General Trade Signals (Non-Type-Specific)

These terms indicate a trade context without classifying the document as a specific type. They contribute to total signal count for the UNRECOGNIZED_TRADE threshold.

```
customs, duty, tariff, freight, cargo, shipment, shipping, import, export,
consignment, invoice, declaration, clearance, manifest, loading, discharge,
warehouse, forwarder, broker, entry, incoterm, lc, letter of credit,
aes, cbp, uscbp, pga, fda notice, usda, bonded, container, fcl, lcl,
seal, booking, vgm, imo, mawb, hawb, airway bill, delivery order
```

---

## 5. Non-Trade Rejection Patterns

Hard rejection occurs when pre-flight checks detect content that is structurally incompatible with trade documents, regardless of signal count.

### 5.1 Word Count Floor

**Rule**: Reject if the document contains fewer than 20 words.

**Rationale**: The shortest valid trade document — a brief arrival notice — still contains at minimum a vessel name, ETA, port, and consignee. Twenty words is a generous floor. Documents below this threshold cannot contain enough information to be useful and are most often test inputs or accidental pastes.

**Implementation**: `len(text.split()) < 20`

---

### 5.2 Emoji Density

**Rule**: Reject if the ratio of emoji characters to total non-whitespace characters exceeds 15%.

**Rationale**: Trade documents do not contain emojis. A high emoji ratio is a reliable signal of social media content, chat messages, or intentionally adversarial input.

**Implementation**: Count characters in Unicode emoji ranges (U+1F300–U+1FAFF and related blocks) divided by total non-whitespace character count. Any document exceeding the threshold is tagged `detected_content: "social_media"`.

---

### 5.3 URL/Social Media Density

**Rule**: Reject if the document contains 3 or more URLs (http/https patterns) with fewer than 2 trade signals matched.

**Rationale**: Shipping documents may reference a website once (e.g., a carrier's booking portal). Three or more URLs in a short document is characteristic of a news article, blog post, or social media content paste.

**Pattern**: `r'https?://\S+'` match count ≥ 3, and total trade signals < 2.

---

### 5.4 Social Media Pattern Detection

**Rule**: Reject if 2 or more of the following are present: `@mention` patterns, `#hashtag` patterns, "retweet", "RT @", "like and share", "follow me", "subscribe", "DM me", "link in bio".

**Rationale**: These are structural markers of social platform content. They are mutually exclusive with trade document vocabulary.

---

### 5.5 Recipe Pattern Detection

**Rule**: Reject if 3 or more of the following recipe signals are present AND total trade signals are 0: "tablespoon", "teaspoon", "cup of", "preheat", "bake at", "oven", "ingredient", "recipe", "serves", "prep time", "cook time", "calories".

**Rationale**: Recipe content is the most common non-trade input in document classification testing. The conjunction with zero trade signals prevents false rejects on documents that discuss food commodities in a trade context (e.g., an invoice for dried herbs may contain "tablespoon equivalent" weight descriptions).

---

### 5.6 Source Code Pattern Detection

**Rule**: Reject if 3 or more of the following code signals are present AND total trade signals are 0: `def `, `function(`, `import `, `const `, `var `, `return `, `console.log`, `print(`, `<html`, `</`, `{`, `}` appearing ≥ 10 times in a 500-character window.

**Rationale**: Developers occasionally test the API with code files. This is the second most common non-trade input pattern. The zero-trade-signal conjunction prevents rejection of documents that contain structured data formatting with braces.

---

## 6. Confidence Scoring

Confidence is assigned based on the total count of distinct trade signals matched across the entire document, regardless of document type.

| Tier | Signal Count | Verdict | Meaning |
|---|---|---|---|
| `HIGH` | 5 or more signals + recognized type | `PASS` | Document is clearly a trade document of a known type |
| `MEDIUM` | 3–4 signals + recognized type | `PASS` | Likely a trade document, analysis should proceed |
| `LOW` | 1–2 signals | `WARN` | Possibly trade-related; analysis proceeds with warning |
| `REJECTED` | 0 signals OR pre-flight check failure | `REJECT` | Not a trade document; analysis blocked |

### Confidence Tier Assignment Logic

```
total_signals = count of distinct signals matched (type-specific + general)
best_type_signals = count of signals matched in the best-fitting type category
recognized_type = (best_type_signals >= 3)

if pre_flight_failed:
    tier = REJECTED
elif total_signals == 0:
    tier = REJECTED
elif total_signals <= 2:
    tier = LOW → verdict = WARN
elif total_signals <= 4 and recognized_type:
    tier = MEDIUM → verdict = PASS
elif total_signals >= 5 and recognized_type:
    tier = HIGH → verdict = PASS
elif total_signals >= 3 and not recognized_type:
    tier = MEDIUM → verdict = PASS (UNRECOGNIZED_TRADE type)
else:
    tier = LOW → verdict = WARN
```

### Detected Document Type

The document type with the highest count of matched signals, provided that count is at least 3, is assigned as the `detected_type`. If no type reaches the threshold, `detected_type` is set to `"UNRECOGNIZED_TRADE"` when total signals are ≥ 3, or `"UNKNOWN"` when below.

Tie-breaking rule: when two types have equal matched signal counts, the type whose signals are more concentrated (higher ratio of matched signals to total defined signals for that type) wins. This prevents a document with scattered general terms from being classified as, e.g., an ISF filing.

---

## 7. Validation Result Model

The validator returns one `ValidationResult` per document. This is a pure data class — no FastAPI types, no HTTP concepts.

```python
@dataclass
class ValidationResult:
    filename: str                  # original filename or "Document N"
    verdict: str                   # "PASS" | "WARN" | "REJECT"
    confidence_tier: str           # "HIGH" | "MEDIUM" | "LOW" | "REJECTED"
    detected_type: str             # "BILL_OF_LADING" | "COMMERCIAL_INVOICE" |
                                   # "PACKING_LIST" | "CERTIFICATE_OF_ORIGIN" |
                                   # "ARRIVAL_NOTICE" | "ISF_FILING" |
                                   # "UNRECOGNIZED_TRADE" | "UNKNOWN"
    signals_matched: int           # total distinct signals found
    signal_names: list[str]        # names of the matched signals
    rejection_reason: str | None   # populated only when verdict == "REJECT"
    detected_content: str | None   # "social_media" | "recipe" | "code" |
                                   # "too_short" | "no_trade_signals" | None
    user_message: str              # ready-to-display message for this document
    warning_message: str | None    # populated when verdict == "WARN"
```

The top-level validator function:

```python
def validate_documents(documents: list[Document]) -> list[ValidationResult]:
    """Validate each document independently.

    Returns one ValidationResult per document in the same order as input.
    Does not raise; all results are returned regardless of verdict.
    The caller is responsible for inspecting verdicts and deciding whether
    to proceed or return an error.
    """
```

---

## 8. Multi-Document Validation

### Per-Document Independence

Each document is validated in isolation. A valid B/L does not "carry" validity to an accompanying document that fails validation. Every document must pass independently.

### Aggregation Logic

After all individual results are collected:

```
rejected_docs = [r for r in results if r.verdict == "REJECT"]
warned_docs   = [r for r in results if r.verdict == "WARN"]
passed_docs   = [r for r in results if r.verdict == "PASS"]

if len(rejected_docs) > 0:
    raise HTTP 422 — return all rejection details, stop here

if len(warned_docs) > 0:
    proceed to _analyze_documents()
    attach warnings to response

if all PASS:
    proceed to _analyze_documents()
```

### Rejection Response for Multi-Document Submissions

When one or more documents are rejected, the error response identifies each failing document individually. The user is not told simply "one document failed" — they are told which document and why.

Example (2 documents submitted, 1 rejected):

```json
{
  "code": "DOCUMENT_VALIDATION_FAILED",
  "message": "1 of 2 documents could not be validated as trade documents.",
  "rejected_documents": [
    {
      "filename": "notes.txt",
      "reason": "NO_TRADE_SIGNALS",
      "detected_content": "recipe",
      "signals_matched": 0,
      "message": "This doesn't look like a trade document. PortGuard analyzes bills of lading, commercial invoices, packing lists, and certificates of origin. Please upload a valid shipping document."
    }
  ]
}
```

---

## 9. Integration Points

### 9.1 `POST /api/v1/analyze` (JSON body)

**Location**: `api/app.py`, `analyze()` function, line ~1373.

**Change**: After unpacking `request.documents`, before calling `_analyze_documents()`, call `validate_documents()`. If any rejections, raise `HTTPException(422)`. If warnings, collect them for attachment to the response.

```python
# NEW — inserted before _analyze_documents()
from api.document_validator import validate_documents, build_rejection_error

validation_results = validate_documents(request.documents)
rejected = [r for r in validation_results if r.verdict == "REJECT"]
if rejected:
    raise HTTPException(status_code=422, detail=build_rejection_error(rejected, total=len(request.documents)))

validation_warnings = [r.warning_message for r in validation_results if r.warning_message]

# EXISTING — unchanged
result = _analyze_documents(request.documents)
```

---

### 9.2 `POST /api/v1/analyze-files` (multipart upload)

**Location**: `api/app.py`, `analyze_files()` function, line ~1592.

**Change**: After all files have been extracted to `documents[]`, before calling `_analyze_documents()`, apply the same validation gate.

The validation runs on extracted text, not raw bytes. This means validation always sees the same content that the analysis engine will see — no discrepancy from encoding or table formatting.

---

### 9.3 `POST /api/v1/extract-text` (single file, two-step demo flow)

**Behavior**: This endpoint only extracts text — it does not analyze. The demo UI extracts text, displays it in a textarea for review, then submits to `/api/v1/analyze`.

**No validation gate here.** The user may be extracting text specifically to see what was extracted before deciding whether to submit for analysis. Blocking extraction before the user can even review the text would be overly aggressive. Validation fires at the `/api/v1/analyze` step instead.

---

### 9.4 `AnalyzeResponse` model additions

Two new optional fields are added to `AnalyzeResponse`:

```python
validation_warnings: list[str] = Field(
    default_factory=list,
    description="Per-document validation warnings for documents that passed with LOW confidence. "
                "Analysis proceeded but results may be incomplete.",
)
validation_results: list[dict] = Field(
    default_factory=list,
    description="Validation metadata for each submitted document: detected type, "
                "confidence tier, and signal count.",
)
```

These fields are always present in the response (empty list when no warnings). Existing clients that do not reference them are unaffected.

---

## 10. User Messaging

All user-facing messages are defined as constants in `document_validator.py`. They are never constructed inline.

### 10.1 Rejection Messages

**`NO_TRADE_SIGNALS`** (0 signals matched, no pre-flight failure):
> "This doesn't look like a trade document. PortGuard analyzes bills of lading, commercial invoices, packing lists, and certificates of origin. Please upload a valid shipping document."

**`TOO_SHORT`** (fewer than 20 words):
> "This document is too short to analyze. Please upload the complete document — not a summary, title page, or excerpt."

**`SOCIAL_MEDIA_CONTENT`** (social media patterns detected):
> "This appears to be social media content, not a trade document. PortGuard analyzes bills of lading, commercial invoices, packing lists, and certificates of origin."

**`RECIPE_CONTENT`** (recipe patterns detected):
> "This appears to be a recipe or food preparation document, not a trade document. If you're importing food products, please upload the commercial invoice or certificate of origin for the shipment."

**`CODE_CONTENT`** (source code detected):
> "This appears to be source code or technical markup, not a trade document. Please upload a valid shipping document."

### 10.2 Warning Messages

**`LOW_CONFIDENCE`** (1–2 signals, verdict WARN):
> "This document has limited trade signals. Results may be incomplete. Make sure you're uploading the full document, not a cover page or summary."

### 10.3 Unrecognized Trade Document

When a document passes with `UNRECOGNIZED_TRADE` type:
> "Document type not recognized, but trade signals were detected. Analyzing as a general trade document. For best results, upload complete bills of lading, commercial invoices, or packing lists."

This message is placed in `validation_results[i].user_message` and surfaced in `validation_warnings` in the response.

---

## 11. API Response Shape

### Successful Analysis with Validation Metadata

HTTP 200 — analysis proceeded normally:

```json
{
  "status": "completed",
  "shipment_data": { ... },
  "risk_score": 0.28,
  "validation_warnings": [],
  "validation_results": [
    {
      "filename": "bill_of_lading.txt",
      "detected_type": "BILL_OF_LADING",
      "confidence_tier": "HIGH",
      "signals_matched": 6,
      "verdict": "PASS"
    },
    {
      "filename": "commercial_invoice.txt",
      "detected_type": "COMMERCIAL_INVOICE",
      "confidence_tier": "MEDIUM",
      "signals_matched": 4,
      "verdict": "PASS"
    }
  ],
  ...
}
```

### Analysis with Warnings

HTTP 200 — analysis proceeded, but one document had LOW confidence:

```json
{
  "status": "completed",
  "validation_warnings": [
    "commercial_invoice.txt: This document has limited trade signals. Results may be incomplete. Make sure you're uploading the full document, not a cover page or summary."
  ],
  "validation_results": [
    {
      "filename": "bill_of_lading.txt",
      "detected_type": "BILL_OF_LADING",
      "confidence_tier": "HIGH",
      "signals_matched": 6,
      "verdict": "PASS"
    },
    {
      "filename": "commercial_invoice.txt",
      "detected_type": "UNKNOWN",
      "confidence_tier": "LOW",
      "signals_matched": 1,
      "verdict": "WARN"
    }
  ],
  ...
}
```

### Rejection Response

HTTP 422 — analysis blocked:

```json
{
  "detail": {
    "code": "DOCUMENT_VALIDATION_FAILED",
    "message": "1 of 2 documents could not be validated as trade documents.",
    "rejected_documents": [
      {
        "filename": "pasta_recipe.txt",
        "reason": "NO_TRADE_SIGNALS",
        "detected_content": "recipe",
        "signals_matched": 0,
        "signal_names": [],
        "message": "This doesn't look like a trade document. PortGuard analyzes bills of lading, commercial invoices, packing lists, and certificates of origin. Please upload a valid shipping document."
      }
    ]
  }
}
```

---

## 12. Edge Cases and Boundary Conditions

### 12.1 Document that mentions food commodities

A commercial invoice for frozen shrimp may contain "tablespoon equivalent" or "recipe-grade" in the commodity description. The recipe rejection rule requires **3 recipe signals AND 0 trade signals**. A food commodity invoice will have trade signals (seller, buyer, invoice number, value, HS code) that prevent the recipe rejection from triggering.

### 12.2 Document that contains a URL

Carrier websites, CBP references, and bank payment portals are legitimately referenced in trade documents. A single URL does not trigger rejection. The URL density rule requires **3 or more URLs combined with fewer than 2 trade signals**.

### 12.3 Very short but valid document

An amendment notice or a one-field correction document may be legitimately short. The 20-word floor is intentionally low — it is not meant to reject real edge-case documents, only empty strings, single-line pastes, and test inputs like `"hello world"`.

### 12.4 Mixed-language documents

Some trade documents include fields in the origin country's language alongside English labels. The keyword lists are English-only. Documents where the structure is English but the values are non-English (e.g., "SHIPPER: 上海贸易有限公司") will still match the "SHIPPER" signal. Documents entirely in a non-English script will likely score LOW confidence and receive a warning, but will not be rejected, since the word-count and pre-flight checks do not depend on language.

### 12.5 Documents with OCR artifacts

Scanned PDFs that pass text extraction (text layer present but with OCR errors) may have garbled keywords. "CONS\x00GNEE" or "SH|PPER" would not match signals. This is an inherent limitation of text-layer-only PDF support and is documented in the existing `docs/vision.md` known limitations. The validator cannot compensate for corrupt extraction without fuzzy matching (out of scope for this version).

### 12.6 Adversarial input — trade keywords stuffed into non-trade content

A user could paste trade keywords into a non-trade document to bypass rejection. This is an intentional action by an authenticated user of their own organization's account. The system is not a security gate against authenticated users — it is a quality gate against accidental or confused input. Deliberate bypass by an authenticated user is out of scope.

### 12.7 Empty string

An empty string has 0 words and 0 signals. It fails the word-count pre-flight check. Rejection reason: `TOO_SHORT`.

### 12.8 Document submitted as both extracted text and file

The `/api/v1/analyze` (JSON) endpoint accepts pre-extracted text. The `/api/v1/analyze-files` endpoint accepts files and runs `document_parser.py` before validation. The validator always receives plain text — it never sees raw bytes. Validation logic is identical regardless of how the text arrived.

---

## Implementation Notes (for reference during build)

- `document_validator.py` should have no FastAPI imports. Keep it a pure Python module so it can be tested without spinning up the app.
- The signal keyword lists should be defined as module-level constants (dicts keyed by type → signal name → list of keywords), not embedded in functions. This makes them easy to extend without touching logic.
- Signal matching should compile all keyword patterns once at module load time using `re.compile`, not re-compile on every call.
- `build_rejection_error(rejected_results, total_docs)` is a helper function that constructs the structured 422 detail dict. It should live in `document_validator.py` alongside the validator so all error shape decisions are in one place.
- The `detected_content` field in `ValidationResult` is diagnostic metadata for the error response. It should only be set when a specific non-trade pattern fires. When rejection is due to `NO_TRADE_SIGNALS` without a matching pattern, it is `None`.
