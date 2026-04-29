# PortGuard — Certification & Expanded Screening Architecture

**Version:** 1.0  
**Date:** 2026-04-19  
**Status:** Planning — pre-implementation  
**Scope:** Three-part expansion: stats refresh, sustainability rating, toggleable certification modules

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Part 1 — Stats Labels Update](#2-part-1--stats-labels-update)
3. [Part 2 — Sustainability Rating](#3-part-2--sustainability-rating)
4. [Part 3 — Toggleable Certification Modules](#4-part-3--toggleable-certification-modules)
5. [Data Model](#5-data-model)
6. [Screening Logic Architecture](#6-screening-logic-architecture)
7. [Module Applicability Tables](#7-module-applicability-tables)
8. [Database Schema Changes](#8-database-schema-changes)
9. [API Changes](#9-api-changes)
10. [Frontend Changes](#10-frontend-changes)
11. [Agent Pipeline Changes](#11-agent-pipeline-changes)
12. [Implementation Phases](#12-implementation-phases)
13. [Risk & Edge Cases](#13-risk--edge-cases)

---

## 1. Executive Summary

This document specifies a three-part expansion to PortGuard's compliance screening capabilities:

**Part 1** is cosmetic: update the landing page stats badges to reflect the full capability set after the expansion is complete.

**Part 2** adds a **Sustainability Rating** (A/B/C/D/N/A) computed per-screening from document signals, product category risk, and country-of-origin sustainability profiles. The rating is always surfaced on every result card and PDF report alongside the existing compliance decision.

**Part 3** introduces a **Screening Modules** settings system with 5 layers containing 30+ named certification frameworks. Layer 1 is always-on core compliance. Layers 2–5 are toggleable per organization. The active module set is snapshotted at scan time so historical results remain reproducible.

All three parts must compose cleanly with the existing agent pipeline without breaking backward compatibility for existing screening results that have no sustainability rating or module snapshot.

---

## 2. Part 1 — Stats Labels Update

### 2.1 Current Stats

The six stats badges on the analyze/landing section of `demo.html` currently read:

```
333    Section 301 HTS prefixes
14     Active AD/CVD orders
10     OFAC sanctions programs
72     PGA-mapped HTS chapters
[?]    [currently missing additional rows]
```

### 2.2 New Stats

After implementation, the seven stats badges must read exactly:

| Value | Label |
|-------|-------|
| `333` | Section 301 tariff HTS prefixes |
| `14` | Active AD/CVD orders |
| `10` | OFAC sanctions programs |
| `72` | PGA-mapped HTS chapters |
| `50+` | Optional certification frameworks |
| `5` | Compliance layers |
| `30+` | Industry-specific screening modules |

### 2.3 Implementation Notes

- These are static display strings embedded in `demo.html`. No backend change required.
- The `50+` and `30+` values are intentionally rounded marketing numbers. The actual module count in the codebase is 30 toggleable modules across Layers 2–5 plus the 6 always-on Layer 1 modules = 36 total. `50+` refers to the total certification frameworks recognized (including sub-certifications, regional variants, and companion programs).
- The `5 compliance layers` directly references the 5-layer module architecture in Part 3.

---

## 3. Part 2 — Sustainability Rating

### 3.1 Overview

A sustainability rating is computed for every screening as a read-only output. It is not configurable by the organization. It is always present in the `AnalyzeResponse`. It grades the sustainability posture of the shipment on a 5-point scale: A, B, C, D, or N/A.

### 3.2 Rating Scale

| Grade | Criteria |
|-------|----------|
| **A** | Two or more credible sustainability certifications detected in document text AND country/product inherent risk is LOW |
| **B** | At least one sustainability certification detected OR inherent risk is LOW with no disqualifying signals |
| **C** | No certifications detected AND at least one moderate-risk signal present (high-deforestation country, sensitive product category, missing expected certification) |
| **D** | High inherent risk product/country combination with zero mitigating certifications AND at least one explicit negative signal (e.g. sourcing from OFAC-adjacent region with known forced labor in the commodity chain) |
| **N/A** | Product category has no applicable sustainability standards (e.g. industrial reagents with no environmental certification regime, pure financial instruments, certain machinery) |

### 3.3 Input Signals

The rating engine reads four independent signal groups. Each signal group produces a score of LOW / MEDIUM / HIGH / CRITICAL risk plus a list of evidence strings.

#### Signal Group 1: Detected Certifications

Scan the concatenated raw text of all submitted documents using a certification regex library (see §6.4). Each hit elevates the grade:
- 2+ credible hits → certification_score = STRONG
- 1 credible hit → certification_score = PRESENT
- 0 hits → certification_score = ABSENT

"Credible" means the regex matched both the program name AND a certificate number or code format. A bare mention of "FSC certified" with no certificate number is a WEAK signal, not a credible hit.

#### Signal Group 2: Country of Origin Sustainability Risk

Maintain a static `_COUNTRY_SUSTAINABILITY_RISK` table keyed on ISO2, with entries:

| Risk Level | Example Countries |
|------------|-------------------|
| `HIGH` | ID (Indonesia, palm oil/deforestation), MY (Malaysia, palm/timber), PG (Papua New Guinea, timber), BR (Brazil, soy/cattle/deforestation), KH (Cambodia, timber), MM (Myanmar), PH (Philippines, fisheries), VN (Vietnam, seafood/timber) |
| `MEDIUM` | CN (China, labor/environmental), IN (India, cotton/leather), BD (Bangladesh, garment effluent), PK (Pakistan, cotton), MX (Mexico), TH (Thailand, seafood), GH (Ghana, cocoa), NG (Nigeria, palm) |
| `LOW` | DE (Germany), FR (France), SE (Sweden), FI (Finland), AU (Australia), NZ (New Zealand), CA (Canada), NO (Norway), CH (Switzerland) |
| `UNKNOWN` | XX or any unmapped ISO2 — treated as MEDIUM for rating purposes |

#### Signal Group 3: Product Category Sustainability Risk

Map the primary HTS chapter to an inherent sustainability risk level:

| Risk Level | HTS Chapters | Reason |
|------------|-------------|--------|
| `HIGH` | 03 (seafood), 09 (coffee/cocoa), 10 (grain/soy), 12 (oilseeds, palm), 15 (edible oils, palm oil), 44 (wood, timber, plywood), 47 (pulp), 48 (paper), 52 (cotton), 41 (hides/leather), 71 (precious stones/minerals), 26 (ores, minerals) | High deforestation, forced labor, or extractive industry risk |
| `MEDIUM` | 02 (meat/poultry), 06 (plants), 07 (vegetables), 08 (fruit), 16 (prepared seafood), 61–63 (apparel/textiles), 64 (footwear), 72–73 (steel), 76 (aluminum), 28–29 (chemicals) | Supply chain opacity, regional risk, or labor concerns |
| `LOW` | 84–85 (machinery, electronics), 87 (vehicles), 90 (optics/medical), 39 (plastics), 30 (pharma), 94 (furniture — note: furniture from timber-risk regions overrides to MEDIUM) | Lower inherent sustainability complexity |
| `N/A` | 49 (printed matter), 97 (art), 99 (special classification), 98 (HTSUS special), financial instruments | No applicable sustainability standards |

#### Signal Group 4: Declared Supplier Certifications

Check the shipment's `additional_context` and all document text for compliance declarations of the form:
- "We certify that this shipment complies with [standard]"
- "Supplier is certified under [program]"
- "Certificate of Compliance: [program]"

These are WEAK signals (lower confidence than a certificate number match) but still elevate the grade from D→C or C→B.

### 3.4 Rating Computation Logic

```
function compute_sustainability_rating(signals):

  # N/A fast-path: if ALL line items map to N/A product category
  if all(item.category_risk == 'N/A' for all line items):
    return Rating('N/A', [])

  # Aggregate country and product risk
  country_risk = max(line_item.country_risk for all items)  # worst case
  product_risk = max(line_item.category_risk for all items)  # worst case
  inherent_risk = max(country_risk, product_risk)

  # Certification evidence
  cert_score = evaluate_certifications(document_text)
  # cert_score = STRONG | PRESENT | WEAK | ABSENT

  # Grade matrix
  if cert_score == STRONG and inherent_risk == LOW:    return A
  if cert_score == STRONG and inherent_risk == MEDIUM: return B
  if cert_score == STRONG and inherent_risk == HIGH:   return B  # certs partially mitigate
  if cert_score == PRESENT and inherent_risk == LOW:   return B
  if cert_score == PRESENT and inherent_risk == MEDIUM: return B
  if cert_score == PRESENT and inherent_risk == HIGH:  return C  # certs insufficient for high-risk
  if cert_score == WEAK and inherent_risk == LOW:      return B
  if cert_score == WEAK and inherent_risk == MEDIUM:   return C
  if cert_score == WEAK and inherent_risk == HIGH:     return D
  if cert_score == ABSENT and inherent_risk == LOW:    return B  # low inherent risk = passable
  if cert_score == ABSENT and inherent_risk == MEDIUM: return C
  if cert_score == ABSENT and inherent_risk == HIGH:   return D

  return C  # safe default
```

### 3.5 SustainabilityRating Model

```python
class SustainabilityRating(BaseModel):
    grade: Literal["A", "B", "C", "D", "N/A"]
    inherent_risk_level: Literal["LOW", "MEDIUM", "HIGH", "N/A"]
    country_risk_level: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN", "N/A"]
    product_risk_level: Literal["LOW", "MEDIUM", "HIGH", "N/A"]
    certifications_detected: list[str]   # list of cert names/numbers found
    certifications_missing: list[str]    # certs expected for category but not found
    signals: list[str]                   # plain-English explanation strings
    computation_notes: list[str]         # how the grade was reached
```

### 3.6 Where the Rating Appears

1. **`AnalyzeResponse`** — new top-level field `sustainability_rating: SustainabilityRating`
2. **Result card in `demo.html`** — grade badge displayed in header row next to the decision badge
3. **PDF compliance report** — dedicated "Sustainability Assessment" section after the risk factor table
4. **Bulk batch results** — `sustainability_grade` column added to per-shipment summary and CSV export

### 3.7 Backward Compatibility

Existing `AnalyzeResponse` consumers that do not reference `sustainability_rating` are unaffected. The field must be `Optional[SustainabilityRating] = None` in the Pydantic model to avoid breaking clients that receive responses during the migration window. After full rollout, the field is always populated.

---

## 4. Part 3 — Toggleable Certification Modules

### 4.1 Module Catalog

#### Layer 1 — Core Compliance (always on, not toggleable)

| Module ID | Name | Existing Implementation |
|-----------|------|------------------------|
| `OFAC_SANCTIONS` | OFAC Sanctions (10 programs) | `portguard/data/sanctions.py` + `RiskAgent._check_sanctions()` |
| `SECTION_301` | Section 301 Tariffs (333 HTS prefixes) | `portguard/data/section301.py` + `RiskAgent._check_section_301()` |
| `ADCVD_ORDERS` | AD/CVD Orders (14 active) | `portguard/data/adcvd.py` + `RiskAgent._check_adcvd()` |
| `UFLPA` | UFLPA Forced Labor | `RiskAgent._check_uflpa()` |
| `ISF_COMPLETENESS` | ISF 10+2 Completeness | `ValidationAgent._check_isf_completeness()` |
| `PGA_ROUTING` | PGA Routing (72 HTS chapters) | `portguard/data/pga.py` + `ValidationAgent._gather_pga_requirements()` |

#### Layer 2 — Environmental & Sustainability (toggleable)

| Module ID | Name | Applicable HTS Chapters | Key Certificate Patterns |
|-----------|------|------------------------|--------------------------|
| `FSC_COC` | FSC® Chain of Custody | 44, 47, 48, 94 | `FSC-C\d{6}`, `FSC® C\d{6}`, "Forest Stewardship Council" |
| `PEFC` | PEFC™ Certification | 44, 47, 48 | `PEFC/\d{2}-\d{2}-\d{3,4}`, "PEFC certified", "PEFC™" |
| `SFI` | SFI® Standard | 44, 47, 48 | "SFI certified", "Sustainable Forestry Initiative", `SFI-\d{4,}` |
| `RAINFOREST_ALLIANCE` | Rainforest Alliance | 09, 18, 20, 08 | "Rainforest Alliance Certified", "RA-Cert", `RAIN-\d+` |
| `RSPO` | RSPO (Roundtable on Sustainable Palm Oil) | 15, 21, 33, 38 | "RSPO certified", "RSPO Mass Balance", "RSPO SCC", `RSPO-\w+` |
| `MSC` | MSC (Marine Stewardship Council) | 03, 16 | "MSC certified", "Marine Stewardship Council", `MSC-\w+` |
| `BCI_COTTON` | BCI Cotton (Better Cotton Initiative) | 52, 53, 61, 62, 63 | "Better Cotton", "BCI licensed", "Better Cotton Initiative" |
| `CARBON_TRUST` | Carbon Trust Certification | Any | "Carbon Trust", "Carbon Neutral certified", "CarbonNeutral®" |
| `CRADLE_TO_CRADLE` | Cradle to Cradle® | Any | "Cradle to Cradle", "C2C Certified", "Cradle to Cradle Certified™" |
| `GREEN_SEAL` | Green Seal / ECOLOGO® | Any | "Green Seal", "GS-\d+", "ECOLOGO®", "UL ECOLOGO" |

#### Layer 3 — Ethical Sourcing & Labor (toggleable)

| Module ID | Name | Applicable HTS Chapters | Key Certificate Patterns |
|-----------|------|------------------------|--------------------------|
| `FAIRTRADE` | Fairtrade International | 09, 17, 18, 52 | "Fairtrade Certified", "FAIRTRADE Mark", `FLO-CERT`, "Fairtrade International" |
| `SA8000` | SA8000 (Social Accountability Intl) | 61, 62, 63, 64, 84, 85 | "SA8000 certified", "SAI SA8000", `SA8000:\d{4}` |
| `WRAP` | WRAP Certification | 61, 62, 63, 64 | "WRAP certified", "Worldwide Responsible Accredited Production", `WRAP-\d+` |
| `RBA` | RBA Code of Conduct | 84, 85, 90 | "RBA member", "Responsible Business Alliance", "EICC certified", "RBA VAP" |
| `SMETA` | SMETA / Sedex Audit | Any | "SMETA audit", "Sedex member", "SMETA 2-pillar", "SMETA 4-pillar" |
| `CONFLICT_MINERALS` | Conflict Minerals (Dodd-Frank §1502) | 26, 71, 72, 73, 74, 75, 76, 78, 79, 80, 81, 84, 85 | "DRC conflict-free", "RMAP certified", "conflict minerals declaration", "Section 1502" |
| `LEATHER_WORKING_GROUP` | Leather Working Group (LWG) | 41, 42, 64 | "LWG certified", "Leather Working Group", "LWG Gold", "LWG Silver", "LWG Bronze" |
| `OECD_DUE_DILIGENCE` | OECD Due Diligence | 26, 71, 72, 73, 74 | "OECD due diligence", "OECD Minerals Guidance", "responsible sourcing declaration" |

#### Layer 4 — Product Safety & Quality (toggleable)

| Module ID | Name | Applicable HTS Chapters | Key Certificate Patterns |
|-----------|------|------------------------|--------------------------|
| `ISO_9001` | ISO 9001 Quality Management | Any | `ISO 9001:\d{4}`, "ISO 9001 certified", "QMS certified ISO 9001" |
| `ISO_14001` | ISO 14001 Environmental Management | Any | `ISO 14001:\d{4}`, "ISO 14001 certified", "EMS certified" |
| `ISO_45001` | ISO 45001 Occupational Safety | Any | `ISO 45001:\d{4}`, "ISO 45001 certified", "OHSMS" |
| `CE_MARKING` | CE Marking (EU Conformity) | 84, 85, 87, 90, 94, 95 | "CE marked", "CE marking", `CE\s*\d{4}`, "Declaration of Conformity" |
| `UL_LISTED` | UL Listed (UL Solutions) | 84, 85, 87, 94, 95 | "UL Listed", "UL Recognized", `UL \d{4,}`, "Underwriters Laboratories" |
| `CSA_GROUP` | CSA Group Certification | 84, 85, 87 | "CSA certified", "CSA Group", `cCSAus`, `CAN/CSA` |
| `CPSC_EFILING` | CPSC eFiling Compliance | 61, 62, 63, 64, 84, 85, 94, 95 | "CPSC eFiling", "CPSC compliance certificate", "Children's Product Certificate", "General Conformity Certificate" |
| `EU_REACH` | EU REACH Regulation | 28, 29, 38, 39, 40 | "REACH compliant", "REACH declaration", "SVHC declaration", "Regulation (EC) No 1907/2006" |
| `US_TSCA` | US TSCA Compliance (EPA) | 28, 29, 38, 39, 40 | "TSCA compliant", "TSCA certification", "EPA TSCA §13", "negative declaration" |

#### Layer 5 — Advanced / Industry-Specific (toggleable)

| Module ID | Name | Applicable HTS Chapters | Key Certificate Patterns |
|-----------|------|------------------------|--------------------------|
| `CTPAT` | C-TPAT (CBP Trusted Trader) | Any | "C-TPAT member", "Customs-Trade Partnership Against Terrorism", `CTPAT-\d+` |
| `AEO` | AEO Status (Authorized Economic Operator) | Any | "AEO status", "Authorized Economic Operator", "AEO-F", "AEO-C", "AEO-S" |
| `BIS_EAR` | BIS Export Controls / EAR | 84, 85, 90, 93, 38, 29 | "EAR99", "ECCN", "Export Control Classification Number", "BIS license", "Commerce Control List" |
| `ISO_27001` | ISO/IEC 27001 (InfoSec) | 84, 85, 90 | `ISO/IEC 27001:\d{4}`, "ISO 27001 certified", "ISMS certified" |
| `SOC2` | SOC 2 Compliance | 84, 85 | "SOC 2 Type II", "SOC 2 certified", "System and Organization Controls" |
| `PCI_DSS` | PCI DSS | 84, 85 | "PCI DSS compliant", "PCI DSS Level", "Payment Card Industry Data Security" |
| `CITES` | CITES Permit Compliance | 01, 03, 41, 44, 71, 97 | "CITES permit", "CITES certificate", "Appendix I", "Appendix II", "CITES export permit" |
| `KIMBERLEY_PROCESS` | Kimberley Process (KP) | 71 | "Kimberley Process", "KP Certificate", "conflict-free diamond", "Kimberley Process Certification" |
| `RMI_RMAP` | Responsible Minerals Initiative (RMAP) | 26, 71, 72, 73, 74, 75, 76, 78, 79, 80, 81 | "RMAP certified", "RMI audit", "Responsible Minerals Initiative", "conformant smelter" |
| `DEA_PERMITS` | DEA Import/Export Permits | 29, 30 | "DEA permit", "DEA import license", "Drug Enforcement Administration", "Schedule I", "precursor chemical" |

### 4.2 Module Metadata Model

Each module is defined by a static `CertificationModule` dataclass:

```python
@dataclass
class CertificationModule:
    module_id: str                      # e.g. "FSC_COC"
    name: str                           # e.g. "FSC® Chain of Custody"
    layer: int                          # 1–5
    always_on: bool                     # True for Layer 1
    description: str                    # one-paragraph explanation
    applicable_hts_chapters: list[str]  # ["44", "47", "48", "94"]
    certificate_patterns: list[str]     # compiled regex patterns
    country_risk_list: list[str]        # ISO2 codes with elevated risk for this module
    finding_code_prefix: str            # e.g. "FSC", "RSPO", "SA8000"
    remediation_template: str           # plain-English remediation text
    regulatory_reference: str           # authoritative citation
```

The full catalog of 36 modules is declared in `portguard/data/certification_modules.py` (new file).

---

## 5. Data Model

### 5.1 Organization Module Settings

Stored in the `portguard_auth.db` database in a new `organization_modules` table:

```sql
CREATE TABLE IF NOT EXISTS organization_modules (
    organization_id     TEXT NOT NULL,
    module_id           TEXT NOT NULL,
    enabled             INTEGER NOT NULL DEFAULT 1,
    enabled_at          TEXT,
    disabled_at         TEXT,
    set_by              TEXT,           -- officer_id or 'system'
    PRIMARY KEY (organization_id, module_id),
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id)
);

CREATE INDEX IF NOT EXISTS idx_org_modules_org
    ON organization_modules(organization_id);
```

**Default state:** When an organization first registers, all Layer 2–5 modules are inserted as `enabled = 0` (off by default). Organizations must explicitly enable modules they want. Layer 1 modules are never inserted into this table — they are always applied unconditionally in the pipeline.

**Alternative design considered:** default all modules to enabled, require opt-out. Rejected because organizations importing benign goods (e.g. pure electronics manufacturer) should not receive CITES or DEA warnings that are irrelevant to their business. Opt-in is more respectful of the operator's context.

### 5.2 Screening Result Module Snapshot

Add two columns to `shipment_history` via a new migration `006_certification_modules`:

```sql
ALTER TABLE shipment_history ADD COLUMN sustainability_grade TEXT;
ALTER TABLE shipment_history ADD COLUMN sustainability_signals TEXT;   -- JSON array
ALTER TABLE shipment_history ADD COLUMN active_modules_snapshot TEXT;  -- JSON array of module_ids
ALTER TABLE shipment_history ADD COLUMN module_findings TEXT;          -- JSON array of ModuleFinding
```

`active_modules_snapshot` is a JSON array of module IDs that were active **at the time of the scan**. This makes historical results reproducible — if an organization later enables additional modules, the snapshot on prior scans is not retroactively altered.

### 5.3 ModuleFinding Model

```python
@dataclass
class ModuleFinding:
    module_id: str
    module_name: str
    triggered: bool
    finding_type: Literal["CERTIFICATION_MISSING", "CERTIFICATION_DETECTED",
                          "HIGH_RISK_PRODUCT", "HIGH_RISK_COUNTRY",
                          "PATTERN_MATCH", "DECLARATION_PRESENT"]
    severity: Literal["INFO", "WARNING", "ERROR", "CRITICAL"]
    message: str
    evidence: list[str]          # matched text snippets or pattern hits
    regulatory_reference: str
    remediation: str
```

### 5.4 Full AnalyzeResponse Extensions

```python
class AnalyzeResponse(BaseModel):
    # ... existing fields unchanged ...
    
    # New fields (all Optional for backward compatibility during migration)
    sustainability_rating: Optional[SustainabilityRating] = None
    module_findings: list[ModuleFinding] = []
    active_modules_at_scan: list[str] = []    # module IDs that ran
    modules_triggered: list[str] = []          # module IDs that produced findings
```

---

## 6. Screening Logic Architecture

### 6.1 Where Module Screening Runs in the Pipeline

Module screening runs as a new **Stage 3.5** in the OrchestratorAgent, inserted between `ValidationAgent` and `RiskAgent`:

```
Stage 1: ParserAgent
Stage 2: ClassifierAgent
Stage 3: ValidationAgent (ISF, PGA, marking — unchanged)
Stage 3.5: CertificationScreener  ← NEW
Stage 4: RiskAgent (unchanged)
Stage 4.5: PatternEngine (unchanged)
Stage 5: DecisionAgent (unchanged, but receives ModuleFindings)
Stage 5.5: SustainabilityRater  ← NEW (runs after decision, no influence on decision level)
```

**Critical design constraint:** The `SustainabilityRating` does not influence the `DecisionLevel` (CLEAR / REVIEW / HOLD / REJECT). It is a parallel informational output, not a compliance gate. The `CertificationScreener` findings DO feed into the `DecisionAgent` but only at `INFO` or `WARNING` severity for missing certs (not `ERROR` or `CRITICAL`). A missing FSC certificate should generate a WARNING, never a HOLD. Only Layer 1 modules (already wired into RiskAgent) can produce HOLD or REJECT outcomes.

### 6.2 CertificationScreener

New class in `portguard/agents/certification_screener.py`:

```python
class CertificationScreener:
    """Stage 3.5: Run all enabled certification modules against the shipment."""

    def __init__(self, enabled_module_ids: list[str]) -> None:
        self._modules = [
            m for m in ALL_MODULES
            if m.module_id in enabled_module_ids or m.always_on
        ]

    def screen(
        self,
        parsed_shipment: ParsedShipment,
        classification_result: ClassificationResult,
        raw_document_texts: list[str],
    ) -> CertificationScreeningResult:
        findings: list[ModuleFinding] = []
        triggered_modules: list[str] = []

        for module in self._modules:
            if not self._is_applicable(module, classification_result):
                continue
            module_findings = self._run_module(module, parsed_shipment,
                                               classification_result, raw_document_texts)
            findings.extend(module_findings)
            if any(f.triggered for f in module_findings):
                triggered_modules.append(module.module_id)

        return CertificationScreeningResult(
            findings=findings,
            triggered_modules=triggered_modules,
            modules_run=[m.module_id for m in self._modules
                         if self._is_applicable(m, classification_result)],
        )
```

### 6.3 Module Applicability Check

A module is applicable to a shipment if at least one classified line item's HTS chapter appears in the module's `applicable_hts_chapters` list. If a module has an empty `applicable_hts_chapters` (meaning "applies to any goods"), it always runs.

```python
def _is_applicable(
    self,
    module: CertificationModule,
    classification_result: ClassificationResult,
) -> bool:
    if not module.applicable_hts_chapters:
        return True  # universal applicability
    for cls in classification_result.classifications:
        chapter = cls.hts_code[:2]
        if chapter in module.applicable_hts_chapters:
            return True
    return False
```

### 6.4 Certificate Pattern Matching

Each module carries a list of regex patterns. The screener concatenates all document raw_text strings (preserving line boundaries) and runs each pattern against the full corpus.

```python
def _scan_for_certifications(
    self,
    patterns: list[str],
    document_texts: list[str],
) -> list[str]:
    """Return list of matched evidence snippets."""
    corpus = "\n".join(document_texts)
    hits: list[str] = []
    for pattern in patterns:
        for m in re.finditer(pattern, corpus, re.IGNORECASE):
            start = max(0, m.start() - 40)
            end = min(len(corpus), m.end() + 40)
            snippet = corpus[start:end].replace("\n", " ").strip()
            hits.append(snippet)
    return hits[:5]  # cap evidence snippets to prevent response bloat
```

**Security note:** All patterns are static, compiled from the module catalog at startup. No user input is ever compiled into a regex. The document text is the input to the regex, not a pattern source.

### 6.5 SustainabilityRater

New class in `portguard/agents/sustainability_rater.py`:

```python
class SustainabilityRater:
    """Stage 5.5: Compute sustainability grade from document signals."""

    def rate(
        self,
        parsed_shipment: ParsedShipment,
        classification_result: ClassificationResult,
        certification_findings: CertificationScreeningResult,
        raw_document_texts: list[str],
    ) -> SustainabilityRating:
        ...
```

The rater does not call any external API. It reads from:
1. `certification_findings.findings` — for certification detection results
2. `_COUNTRY_SUSTAINABILITY_RISK[iso2]` — for country risk
3. `_HTS_SUSTAINABILITY_RISK[chapter]` — for product risk
4. A secondary scan of raw document text for supplier declaration keywords

### 6.6 Module Config Loading in the Orchestrator

The `OrchestratorAgent` must be initialized with knowledge of which modules are enabled for the current organization. This requires passing the `organization_id` through the pipeline, which is already available from the JWT dependency in `api/app.py`.

```python
class OrchestratorAgent:
    def __init__(self, db=None) -> None:
        # ... existing init ...
        self._module_registry = load_module_catalog()  # loads all 36 modules

    async def screen(
        self,
        shipment_input: ShipmentInput,
        organization_id: str = "__system__",
    ) -> ScreeningReport:
        # Load enabled modules for this org
        enabled_modules = self._load_enabled_modules(organization_id)

        # Stage 3.5
        screener = CertificationScreener(enabled_modules)
        cert_result = screener.screen(parsed, classification, raw_texts)

        # Stage 5.5
        rater = SustainabilityRater()
        sustainability = rater.rate(parsed, classification, cert_result, raw_texts)
```

`_load_enabled_modules(organization_id)` queries the `organization_modules` table from the auth DB (or pattern DB — see §8 for the migration decision). Returns a `list[str]` of module IDs. Falls back to empty list (Layer 1 only) if the query fails.

---

## 7. Module Applicability Tables

### 7.1 HTS Chapter → Applicable Modules Matrix

This is the authoritative mapping used by `_is_applicable()`. Only chapters with non-universal modules are listed; universal modules (no chapter restriction) apply to everything.

| HTS Chapter | Layer 2 Modules | Layer 3 Modules | Layer 4 Modules | Layer 5 Modules |
|-------------|-----------------|-----------------|-----------------|-----------------|
| 01 (Live Animals) | — | — | — | CITES |
| 03 (Seafood) | MSC | — | — | CITES |
| 09 (Coffee/Cocoa) | RAINFOREST_ALLIANCE, FAIRTRADE | FAIRTRADE | — | — |
| 12 (Oilseeds) | RSPO | — | — | — |
| 15 (Edible oils, palm) | RSPO | — | — | — |
| 16 (Prepared seafood) | MSC | — | — | — |
| 17 (Sugar/cocoa) | RAINFOREST_ALLIANCE | FAIRTRADE | — | — |
| 18 (Cocoa products) | RAINFOREST_ALLIANCE | FAIRTRADE | — | — |
| 21 (Misc food) | RSPO | — | — | — |
| 26 (Ores/minerals) | — | CONFLICT_MINERALS, OECD_DUE_DILIGENCE | — | RMI_RMAP |
| 28–29 (Chemicals) | — | — | EU_REACH, US_TSCA | BIS_EAR, DEA_PERMITS |
| 33 (Cosmetics) | RSPO | — | EU_REACH | — |
| 38 (Chemical products) | — | — | EU_REACH, US_TSCA | BIS_EAR |
| 39 (Plastics) | — | — | EU_REACH, US_TSCA | — |
| 41 (Hides/leather) | — | LEATHER_WORKING_GROUP | — | CITES |
| 42 (Leather articles) | — | LEATHER_WORKING_GROUP | — | — |
| 44 (Wood/timber) | FSC_COC, PEFC, SFI | — | — | CITES |
| 47 (Pulp) | FSC_COC, PEFC, SFI | — | — | — |
| 48 (Paper) | FSC_COC, PEFC, SFI | — | — | — |
| 52 (Cotton) | BCI_COTTON | FAIRTRADE | — | — |
| 53–55 (Textiles) | BCI_COTTON | — | — | — |
| 61–63 (Apparel) | BCI_COTTON | SA8000, WRAP, SMETA | CPSC_EFILING | — |
| 64 (Footwear) | — | SA8000, WRAP, LEATHER_WORKING_GROUP | CPSC_EFILING | — |
| 71 (Gems/jewelry) | — | CONFLICT_MINERALS, OECD_DUE_DILIGENCE | — | CITES, KIMBERLEY_PROCESS, RMI_RMAP |
| 72–76 (Metals) | — | CONFLICT_MINERALS, OECD_DUE_DILIGENCE | — | RMI_RMAP |
| 84 (Machinery) | — | RBA, SMETA | ISO_9001, CE_MARKING, UL_LISTED, CSA_GROUP | BIS_EAR, CTPAT, AEO, ISO_27001 |
| 85 (Electronics) | — | RBA, SMETA, CONFLICT_MINERALS | ISO_9001, CE_MARKING, UL_LISTED, CSA_GROUP, CPSC_EFILING | BIS_EAR, CTPAT, AEO, ISO_27001, SOC2, PCI_DSS |
| 87 (Vehicles) | — | — | CE_MARKING, UL_LISTED, CSA_GROUP | BIS_EAR |
| 90 (Medical/optics) | — | — | CE_MARKING, ISO_9001 | BIS_EAR, ISO_27001 |
| 93 (Firearms/ammo) | — | — | — | BIS_EAR |
| 94 (Furniture) | FSC_COC | — | CPSC_EFILING | — |
| 95 (Toys/games) | — | — | CE_MARKING, CPSC_EFILING, UL_LISTED | — |
| 97 (Art) | — | — | — | CITES |

### 7.2 Country Risk Lists by Module

Some modules have country-specific elevated risk lists beyond the general sustainability country table. These are per-module and override or augment the general country risk:

| Module | Elevated Risk Countries | Reason |
|--------|------------------------|--------|
| `FSC_COC` | ID, MY, PG, KH, MM, VN | High illegal logging rates |
| `RSPO` | ID, MY, NG, GH, CO | Major palm oil producers |
| `MSC` | CN, TH, VN, ID | Documented IUU fishing |
| `CONFLICT_MINERALS` | CD (DRC), CG, RW, UG, BI, SS, TZ | DRC-adjacent region per Dodd-Frank |
| `UFLPA` (existing) | CN (Xinjiang) | Existing implementation |
| `KIMBERLEY_PROCESS` | CD, AO, ZW, CF, LR, SL | Historical conflict diamond sourcing |
| `CITES` | CN, VN, TH, ID, MY | High wildlife trade enforcement cases |
| `BCI_COTTON` | UZ (Uzbekistan), TM (Turkmenistan) | State-sponsored forced labor in cotton harvest |

---

## 8. Database Schema Changes

### 8.1 Migration 006 — Certification Module Tables

Added to `portguard/pattern_db.py` `_MIGRATIONS` list:

```python
(
    "006_certification_modules",
    """
    ALTER TABLE shipment_history ADD COLUMN sustainability_grade TEXT;
    ALTER TABLE shipment_history ADD COLUMN sustainability_signals TEXT;
    ALTER TABLE shipment_history ADD COLUMN active_modules_snapshot TEXT;
    ALTER TABLE shipment_history ADD COLUMN module_findings TEXT;
    """
)
```

### 8.2 Migration Auth-002 — Organization Modules Table

Added to `portguard/auth.py` `_AUTH_SCHEMA_STMTS` (or as a separate migration mechanism for the auth DB):

```sql
CREATE TABLE IF NOT EXISTS organization_modules (
    organization_id     TEXT NOT NULL,
    module_id           TEXT NOT NULL,
    enabled             INTEGER NOT NULL DEFAULT 0,
    enabled_at          TEXT,
    disabled_at         TEXT,
    set_by              TEXT,
    PRIMARY KEY (organization_id, module_id),
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_org_modules_org
    ON organization_modules(organization_id, enabled);
```

**Note on DB placement:** `organization_modules` lives in the **auth DB** (`portguard_auth.db` / PostgreSQL `organizations` schema) because it is an organization configuration concern, not a pattern learning concern. The auth DB already owns the `organizations` table with the foreign key. Keeping module config adjacent to the org record avoids cross-DB joins.

However, the `OrchestratorAgent` does not import from `portguard.auth` to avoid circular dependencies. A thin `ModuleConfigDB` adapter class will wrap the auth DB connection specifically for module queries, with its own `get_engine()` call to the same underlying database.

### 8.3 Default Module Initialization

When a new organization is created (in `AuthDB.create_organization()`), insert default rows for all Layer 2–5 modules with `enabled = 0`:

```python
def _init_org_modules(self, conn, org_id: str, now: str) -> None:
    from portguard.data.certification_modules import ALL_TOGGLEABLE_MODULES
    for module in ALL_TOGGLEABLE_MODULES:
        conn.execute(text("""
            INSERT OR IGNORE INTO organization_modules
            (organization_id, module_id, enabled, set_by)
            VALUES (:org_id, :module_id, 0, 'system_default')
        """), {"org_id": org_id, "module_id": module.module_id})
```

This must be inside the same transaction as the `INSERT INTO organizations` so a failed org creation never leaves orphaned module rows.

---

## 9. API Changes

### 9.1 New Endpoints

#### `GET /api/v1/modules`

Returns the full module catalog with per-org enabled state.

```
Response 200:
{
  "layers": [
    {
      "layer": 1,
      "name": "Core Compliance",
      "description": "Always active — cannot be disabled",
      "modules": [
        {
          "module_id": "OFAC_SANCTIONS",
          "name": "OFAC Sanctions (10 programs)",
          "enabled": true,
          "always_on": true,
          "description": "...",
          "applicable_hts_chapters": []
        },
        ...
      ]
    },
    {
      "layer": 2,
      "name": "Environmental & Sustainability",
      "modules": [
        {
          "module_id": "FSC_COC",
          "name": "FSC® Chain of Custody",
          "enabled": false,  // org-specific current state
          "always_on": false,
          "description": "...",
          "applicable_hts_chapters": ["44", "47", "48", "94"]
        },
        ...
      ]
    },
    ...
  ],
  "total_enabled": 6,
  "total_modules": 36
}
```

**Auth:** Requires `Bearer` token. Response is scoped to the authenticated org's module settings.

#### `PATCH /api/v1/modules/{module_id}`

Toggle a single module on or off.

```
Request body: { "enabled": true | false }

Response 200: { "module_id": "FSC_COC", "enabled": true, "updated_at": "..." }
Response 400: { "code": "MODULE_NOT_FOUND", "message": "..." }
Response 403: { "code": "MODULE_ALWAYS_ON", "message": "Layer 1 modules cannot be disabled." }
```

#### `PUT /api/v1/modules`

Bulk update — set the full enabled/disabled state of all toggleable modules in one request.

```
Request body: { "modules": { "FSC_COC": true, "RSPO": false, ... } }
Response 200: { "updated": 12, "ignored_always_on": 6 }
```

### 9.2 Modified Endpoints

#### `POST /api/v1/analyze`

The `organization_id` from the JWT is now passed to `OrchestratorAgent.screen()`. The response adds:
- `sustainability_rating` (SustainabilityRating object)
- `module_findings` (list of ModuleFinding)
- `active_modules_at_scan` (list of module_id strings)

The existing `decision`, `risk_score`, `explanations`, etc. are unchanged.

#### `POST /api/v1/bulk/upload`

The `organization_id` is already passed through. Each shipment in the batch will use the same module snapshot (captured once at batch creation time, not per-shipment) to avoid inconsistency within a batch.

#### PDF Report (`GET /api/v1/report/{shipment_id}`)

Report gains a new "Sustainability Assessment" section showing:
- Grade badge (A/B/C/D/N/A) with color coding
- Inherent product/country risk level
- Certifications detected (if any) with evidence
- Certifications missing/expected for the product category
- Active modules at time of scan

#### `GET /api/v1/bulk/{batch_id}/export` (CSV export)

Adds columns: `sustainability_grade`, `modules_triggered` (comma-separated module IDs).

### 9.3 No Breaking Changes

All new response fields are additive. Existing clients that ignore unknown JSON keys (standard practice) will not break. The `sustainability_rating` field is `Optional` during the migration window.

---

## 10. Frontend Changes

### 10.1 Stats Badges Update (`demo.html`)

Find the stats section (currently shows "333 Section 301 HTS prefixes" etc.) and update the 7 badge values and labels as specified in §2.2. This is a targeted string replacement — no structural change to the HTML.

### 10.2 Sustainability Rating on Result Card

In the result card rendered after analysis, add a sustainability badge row immediately below the decision badge row:

```
[APPROVE]  [A — Sustainability]
```

The badge color coding:
- A: `--teal-600` background (matches the "clear" green)
- B: `--teal-800` background (darker teal)
- C: `#b45309` (amber)
- D: `var(--red)` (red)
- N/A: `var(--border-hi)` (neutral gray)

On click/hover, expand to show the sustainability signals panel listing detected certs, missing certs, and inherent risk levels.

### 10.3 Screening Modules Panel (`demo.html`)

Add a new "Modules" tab or collapsible settings section in the authenticated UI. Layout:

```
⚙ Screening Modules
─────────────────────────────────────
Layer 1 — Core Compliance        [always active — 6 modules]
  ✓ OFAC Sanctions               [locked]
  ✓ Section 301 Tariffs          [locked]
  ...

Layer 2 — Environmental & Sustainability
  □ FSC® Chain of Custody        [toggle]
  □ PEFC™ Certification          [toggle]
  ✓ Rainforest Alliance          [toggle — currently ON]
  ...

Layer 3 — Ethical Sourcing & Labor
  ...

[Save Module Settings]
```

The panel makes a `GET /api/v1/modules` call on load to populate current states, then `PATCH /api/v1/modules/{module_id}` on each toggle, or `PUT /api/v1/modules` on bulk save.

### 10.4 Module Findings in Result Card

Below the existing "Risk Factors" accordion, add a "Certification Findings" accordion (only shown when `module_findings.length > 0`):

```
▼ Certification Findings  (3 modules triggered)
  
  [WARNING] FSC® Chain of Custody
  Wood products (HTS 44.12) detected with no FSC certificate found in 
  documents. Supplier should provide FSC Chain of Custody certificate.
  
  [INFO] ISO 9001 Quality Management
  ISO 9001 certification detected: "ISO 9001:2015 Certified — Certificate 
  No. QMS-20394..." Certification is current.
  
  [WARNING] Conflict Minerals (Dodd-Frank §1502)
  Electronics components from China detected. Conflict minerals disclosure
  required per SEC Rule 13p-1.
```

---

## 11. Agent Pipeline Changes

### 11.1 OrchestratorAgent Changes

```python
async def screen(
    self,
    shipment_input: ShipmentInput,
    organization_id: str = "__system__",
) -> ScreeningReport:
    # ... existing stages 1-4 unchanged ...
    
    # Stage 3.5: Certification screening
    cert_result = None
    if parsed and classification:
        try:
            enabled_modules = self._load_enabled_modules(organization_id)
            raw_texts = [doc.raw_text for doc in shipment_input.documents
                         if hasattr(doc, 'raw_text')] or [shipment_input.raw_text or ""]
            screener = CertificationScreener(enabled_modules)
            cert_result = screener.screen(parsed, classification, raw_texts)
        except Exception as e:
            errors.append(f"CertificationScreener failed (non-fatal): {e}")

    # ... existing stages 4, 4.5 unchanged ...

    # Stage 5.5: Sustainability rating (post-decision, does not affect decision)
    sustainability = None
    if parsed and classification:
        try:
            rater = SustainabilityRater()
            raw_texts = [shipment_input.raw_text or ""]
            sustainability = rater.rate(parsed, classification,
                                        cert_result, raw_texts)
        except Exception as e:
            errors.append(f"SustainabilityRater failed (non-fatal): {e}")
```

### 11.2 DecisionAgent Changes

The `DecisionAgent` receives `ModuleFinding` objects from the `CertificationScreener` and incorporates WARNING-level findings into `key_findings` and `required_actions`. It does NOT elevate decision levels based on certification findings alone.

```python
async def decide(
    self,
    parsed_shipment: ParsedShipment,
    classification_result: ClassificationResult,
    validation_result: ValidationResult,
    risk_assessment: RiskAssessment,
    certification_findings: Optional[CertificationScreeningResult] = None,
) -> ComplianceDecision:
    # ... existing decision logic unchanged ...
    
    # Append certification warnings to key_findings (INFO/WARNING only)
    if certification_findings:
        for finding in certification_findings.findings:
            if finding.triggered and finding.severity in ("WARNING", "ERROR"):
                key_findings.append(finding.message[:200])
```

### 11.3 New File Structure

```
portguard/
├── agents/
│   ├── base.py                    (unchanged)
│   ├── classifier.py              (unchanged)
│   ├── parser.py                  (unchanged)
│   ├── validator.py               (unchanged)
│   ├── risk.py                    (unchanged)
│   ├── decision.py                (minor: accepts cert_findings)
│   ├── orchestrator.py            (updated: stages 3.5 + 5.5, org_id param)
│   ├── certification_screener.py  ← NEW
│   └── sustainability_rater.py    ← NEW
├── data/
│   ├── sanctions.py               (unchanged)
│   ├── adcvd.py                   (unchanged)
│   ├── section301.py              (unchanged)
│   ├── pga.py                     (unchanged)
│   └── certification_modules.py   ← NEW (full 36-module catalog)
├── models/
│   ├── shipment.py                (unchanged)
│   ├── classification.py          (unchanged)
│   ├── validation.py              (unchanged)
│   ├── risk.py                    (unchanged)
│   ├── decision.py                (unchanged)
│   ├── report.py                  (updated: new fields)
│   └── certification.py           ← NEW (SustainabilityRating, ModuleFinding, etc.)
├── auth.py                        (updated: _init_org_modules, migration)
├── pattern_db.py                  (updated: migration 006)
└── module_config_db.py            ← NEW (thin read adapter for org module settings)
```

---

## 12. Implementation Phases

### Phase 1 — Data Foundation (no pipeline changes)

**Deliverables:**
1. `portguard/data/certification_modules.py` — full 36-module catalog with patterns, HTS chapters, country risk lists
2. `portguard/models/certification.py` — `CertificationModule`, `ModuleFinding`, `SustainabilityRating`, `CertificationScreeningResult` Pydantic/dataclass models
3. DB migration 006 in `pattern_db.py` (additive ALTER TABLE columns — non-breaking)
4. `organization_modules` table in `auth.py` + `_init_org_modules()` on org creation
5. `portguard/module_config_db.py` — thin read adapter: `get_enabled_modules(org_id) -> list[str]`

**Tests:** Unit tests for module catalog completeness (every module has required fields), pattern regex compilation (no invalid patterns), applicability logic.

### Phase 2 — Stats Badges Update

**Deliverables:**
1. Update the 7 stats badge values/labels in `demo.html` (§2.2)

This is safe to ship standalone with no backend dependency.

### Phase 3 — Certification Screener

**Deliverables:**
1. `portguard/agents/certification_screener.py` — `CertificationScreener` class
2. `portguard/agents/sustainability_rater.py` — `SustainabilityRater` class
3. `OrchestratorAgent.screen()` updated with stages 3.5 and 5.5, `organization_id` parameter
4. `AnalyzeResponse` model updated with new optional fields
5. PatternDB `record_shipment()` updated to persist `sustainability_grade`, `active_modules_snapshot`, `module_findings`

**Tests:**
- `tests/test_certification_screener.py` — pattern matching, applicability, finding generation
- `tests/test_sustainability_rater.py` — grade matrix, all 5 grades, N/A fast-path
- Updated `tests/test_orchestrator.py` — verify new fields present in ScreeningReport

### Phase 4 — API Endpoints

**Deliverables:**
1. `GET /api/v1/modules` — full catalog with org-scoped enabled state
2. `PATCH /api/v1/modules/{module_id}` — single toggle
3. `PUT /api/v1/modules` — bulk update
4. Updated `POST /api/v1/analyze` — passes `organization_id` to orchestrator
5. Updated CSV export with sustainability columns

**Tests:** `tests/test_module_api.py` — all three endpoints, auth enforcement, Layer 1 locked behavior.

### Phase 5 — Frontend

**Deliverables:**
1. Sustainability badge on result card
2. Certification Findings accordion in result card
3. Screening Modules settings panel (toggle UI with save)
4. PDF report "Sustainability Assessment" section

### Phase 6 — Hardening

**Deliverables:**
1. Pattern regex performance audit — compile all patterns at startup, not per-scan
2. Module snapshot stored in bulk batches (captured once at batch creation)
3. Retroactive rating for existing shipment history rows (batch migration script — best-effort, marks pre-migration rows as `sustainability_grade = 'N/A'` with a `legacy_no_rating` signal)
4. Monitoring: log which modules triggered per scan to enable future analytics on which frameworks are most commonly flagged

---

## 13. Risk & Edge Cases

### 13.1 Pattern False Positives

**Risk:** "ISO 9001" appears in general text like "our products meet ISO 9001 standards" without a certificate number, triggering a CERTIFICATION_DETECTED finding with no actual certificate on file.

**Mitigation:** The `_scan_for_certifications()` function distinguishes STRONG hits (cert name + certificate number regex) from WEAK hits (cert name only). Only STRONG hits count as `CERTIFICATION_DETECTED`. Weak hits are logged in `signals` as `DECLARATION_PRESENT` and do not fully satisfy a certification requirement.

### 13.2 Module Config DB Availability

**Risk:** The `module_config_db` query fails (DB locked, connection timeout). The certification screener runs with no modules enabled.

**Mitigation:** `_load_enabled_modules()` catches all exceptions and returns `[]` (empty list), meaning only Layer 1 always-on modules run. The pipeline continues without interruption. A warning is logged. This is the same best-effort pattern used by PatternEngine.

### 13.3 Sustainability Rating on Non-Physical Goods

**Risk:** A shipment of software licenses or data services submitted through the API gets rated D because no certifications are found.

**Mitigation:** HTS chapters 49 (printed matter), 97 (art), 98–99 (special classification) map to N/A product risk. If all line items resolve to N/A category, the fast-path returns grade `N/A` immediately. Non-physical goods that lack HTS codes entirely (`9999.00.0000` fallback) are also treated as N/A.

### 13.4 Organization Module State on First Registration

**Risk:** A newly registered organization has no rows in `organization_modules`. Queries return empty list → only Layer 1 runs.

**Mitigation:** `_init_org_modules()` is called in the same transaction as org creation, inserting all Layer 2–5 modules with `enabled = 0`. On query, if the org exists but has no module rows (legacy org before migration), treat as all disabled (Layer 1 only). A future migration can backfill default rows for existing orgs.

### 13.5 Bulk Batch Module Snapshot Consistency

**Risk:** An organization toggles a module ON mid-batch. Some shipments in the batch run with the old config, some with the new.

**Mitigation:** The module snapshot is captured once at `BulkProcessor.create_batch()` time and stored on the `bulk_batches` row as `modules_snapshot TEXT`. Each shipment's `active_modules_at_scan` is set from this batch-level snapshot, not from a live DB query. The batch is internally consistent.

### 13.6 PDF Report Length

**Risk:** Enabling 30 modules that all trigger findings produces a PDF report that is 20+ pages.

**Mitigation:** The PDF report groups module findings by layer and truncates each finding's evidence snippets to 80 characters. A summary count is shown ("12 certification findings — see full detail in digital report") for orgs with many modules enabled. The digital (JSON) response always contains the full findings list.

### 13.7 Section 301 Data Staleness

**Risk:** This architecture document was written noting the existing Section 301 data is stale (does not reflect 2025/145% tariff actions). The certification expansion does not address this.

**Action item:** Section 301 data update is a separate task. The `SECTION_301` module description in the UI should note the data vintage (Lists 1–4A, effective through 2019-09-01).

### 13.8 Backward Compatibility for Existing ScreeningReport Consumers

The existing `portguard/api/routes.py` (Entry Point 2) uses `ScreeningReport` which does not have certification fields. Adding Optional fields to `ScreeningReport` and `AnalyzeResponse` is safe. Entry Point 2 will simply omit the new fields (they default to `None` / `[]`).

---

*End of document. Implementation begins with Phase 1.*
