# PDF Compliance Report Export — Technical Architecture

**Status:** Approved plan — implementation pending  
**Author:** Engineering  
**Date:** 2026-04-11

---

## 1. Overview

After every shipment analysis, an officer must be able to download a formal, printable PDF compliance report suitable for filing, sharing with supervisors, or use as evidentiary documentation in enforcement actions. The report must be self-contained, professionally formatted, and include every material finding from the analysis.

---

## 2. Report Content Specification

Every PDF report must contain the following sections, in order:

### 2.1 Header (every page)
- PortGuard wordmark (bold, styled text — no external image dependency)
- "CUSTOMS COMPLIANCE SCREENING REPORT" subtitle
- Page number: "Page N of M"
- Horizontal rule separator

### 2.2 Report Metadata Block (page 1)
| Field | Value |
|---|---|
| Report ID | Unique UUID generated at report creation time (not the shipment_id) |
| Generated At | ISO 8601 timestamp with timezone |
| Classification | "CONTROLLED — LAW ENFORCEMENT SENSITIVE" |
| Shipment ID | The analysis_id from shipment_history |
| Analyzed At | Original analysis timestamp |

### 2.3 Shipment Summary Table (page 1)
Two-column table with labels and values:
- Shipper / Exporter name
- Consignee name
- Notify Party (if present)
- Origin Country (name + ISO2)
- Destination Country
- Port of Loading
- Port of Discharge / Port of Entry
- Vessel or Flight number
- Bill of Lading number
- Shipment Date / Arrival Date
- Incoterms
- HS Codes (declared) — comma-separated
- Commodity Description
- Declared Value (with currency)
- Gross Weight
- Quantity / Marks & Numbers

### 2.4 Final Decision Banner (page 1)
- Full-width colored banner spanning the page
- Decision text in large font (e.g., "REJECT", "FLAG FOR INSPECTION")
- Color coding:
  - APPROVE → green (`#22c55e`)
  - REVIEW_RECOMMENDED → amber (`#f59e0b`)
  - FLAG_FOR_INSPECTION → orange (`#f97316`)
  - REQUEST_MORE_INFORMATION → blue (`#3b82f6`)
  - REJECT → red (`#ef4444`)
- Confidence level displayed beneath decision ("Confidence: HIGH / MEDIUM / LOW")

### 2.5 Risk Score Panel (page 1)
- Overall Risk Score: displayed as percentage (risk_score × 100), e.g., "78%"
- Visual risk bar: filled rectangle proportional to score, color-coded by level
  - 0–39%: green
  - 40–64%: amber
  - 65–79%: orange
  - 80–100%: red
- Risk Level label: LOW / MEDIUM / HIGH / CRITICAL
- Rule-based component score and pattern intelligence component score displayed separately if pattern history is available

### 2.6 Rule Violations & Findings (new page if needed)
One sub-section per finding from `explanations`. Each entry includes:
- Finding number (e.g., "Finding 1 of 5")
- Finding text (full string from explanations list)
- Severity indicator derived from keywords in the finding text:
  - Contains "OFAC", "sanctions", "embargo", "Iran", "North Korea", "Cuba", "Syria" → CRITICAL
  - Contains "Section 301", "tariff", "AD/CVD", "UFLPA" → HIGH
  - Contains "missing", "incomplete", "inconsistenc" → MEDIUM
  - Otherwise → LOW
- Severity rendered as a colored pill/badge beside the finding header

### 2.7 Compliance Screening Results Table
Structured table with one row per compliance program:

| Program | Status | Details |
|---|---|---|
| OFAC Sanctions | CLEAR / HIT | Matched entity or "No matches" |
| Section 301 Tariffs | CLEAR / HIT | HS code(s) flagged or "Not applicable" |
| AD/CVD Orders | CLEAR / HIT | Order name or "None" |
| UFLPA (Forced Labor) | CLEAR / FLAGGED | Entity/supply chain concern or "Not flagged" |
| ISF (10+2 Filing) | COMPLETE / INCOMPLETE | Missing fields or "All required fields present" |
| PGA Requirements | CLEAR / REVIEW | Agency requirements or "None identified" |

Status values are derived from the findings text: scan each explanation for program keywords and classify as HIT/FLAGGED/INCOMPLETE if any finding references that program, otherwise CLEAR/COMPLETE/CLEAR.

### 2.8 Pattern Intelligence Section
Rendered only if `history_available` is true:
- Header: "Pattern Intelligence Analysis"
- Each `pattern_signals` entry rendered as a bullet point
- Pattern score displayed as percentage
- Note if cold start (fewer than 3 prior analyses)

If `history_available` is false:
- Single line: "Pattern intelligence unavailable — insufficient shipment history for this shipper."

### 2.9 Recommended Next Steps
Numbered list from `recommended_next_steps`. Each item on its own line with a bullet/number prefix.

### 2.10 Officer Review Section (last page)
- Section header: "OFFICER REVIEW & CERTIFICATION"
- Printed name line: "Officer Name: _______________________________"
- Badge / ID line: "Badge / ID: _______________________________"
- Date line: "Date: _______________________________"
- Signature line: "Signature: _______________________________"
- Action taken checkbox block:
  ```
  [ ] No Action Required    [ ] Referred for Inspection    [ ] Escalated to Supervisor
  [ ] Detained              [ ] Seized                     [ ] Released Under Bond
  ```
- Notes field: "Notes / Remarks:" followed by blank lines

### 2.11 Legal Disclaimer (last page, footer area)
> This report was generated by PortGuard, an automated customs compliance screening system.
> Findings are based on automated rule evaluation and statistical pattern analysis.
> This report does not constitute a final determination of admissibility or legal compliance.
> All enforcement decisions must be made by a qualified CBP officer in accordance with applicable law.
> Report ID: [report_id] | Generated: [timestamp] | Confidential — Not for Public Release

---

## 3. Technology Evaluation

### 3.1 WeasyPrint
- **Approach:** HTML + CSS → PDF via Cairo/Pango rendering
- **Quality:** Excellent — pixel-perfect CSS layout, full HTML5 support
- **Dependencies:** Requires `cairocffi`, `Pango`, `libffi`, system-level libraries
- **PaaS compatibility:** **Problematic.** Render PaaS free/starter dynos do not provide Cairo or Pango system packages. Even if buildpacks are used, the dependency chain is fragile and has caused known deployment failures in similar stacks. This is a hard blocker.
- **License:** BSD
- **Verdict:** Eliminated — system dependency risk on Render PaaS is unacceptable

### 3.2 ReportLab
- **Approach:** Programmatic PDF via coordinate-based drawing API (Platypus for higher-level layout)
- **Quality:** Industry standard, high quality, full control
- **Dependencies:** Pure Python (pip only), no system libraries
- **PaaS compatibility:** Excellent
- **License:** **AGPL-3.0** — requires open-sourcing any modified version if distributed. This is a license risk for a proprietary product.
- **Cost:** Paid "ReportLab PLUS" version exists; open-source AGPL version is functional
- **API ergonomics:** Low-level coordinate math makes tables and multi-page documents verbose
- **Verdict:** Viable technically but AGPL license is a risk; eliminated in favor of simpler alternative

### 3.3 fpdf2
- **Approach:** Programmatic PDF generation, imperative drawing API
- **Quality:** Sufficient for structured compliance reports — supports multi-page, headers/footers, tables, colored rectangles, text styling, UTF-8
- **Dependencies:** Pure Python, zero system dependencies
- **PaaS compatibility:** Perfect — single `pip install fpdf2`, works on any PaaS including Render
- **License:** **MIT** — no restrictions
- **API ergonomics:** Clean modern API (fpdf2 is the actively maintained fork of PyFPDF), good table support via `FPDF.table()` introduced in 2.7+, auto page breaks, header/footer callbacks
- **Verdict:** **Selected**

### 3.4 Decision: fpdf2

fpdf2 is the correct choice for this stack. It is pure Python, MIT licensed, PaaS-safe, and provides all primitives needed for this report: multi-page documents, auto page breaks, styled headers and footers on every page, colored filled rectangles (decision banner, risk bar, severity badges), two-column tables, and UTF-8 text. The only capability it lacks compared to WeasyPrint is pixel-perfect CSS layout — which is not needed for a structured compliance report.

```
pip install fpdf2
```

Add to `requirements.txt`:
```
fpdf2>=2.7.9
```

---

## 4. Critical Design Problem: Missing Persistence

### 4.1 The Problem

The full analysis content required for a PDF report is returned in `AnalyzeResponse` at analysis time but only a subset is persisted to `shipment_history`:

**Persisted in `shipment_history`:**
- `analysis_id`, `analyzed_at`, shipper/consignee names and keys, origin/destination ISO2, route, HS codes, risk scores, decision, confidence, pattern_score, inconsistency_count, missing_field_count

**NOT persisted (lost after the response is returned):**
- `explanations` (list of strings — the actual rule findings narrative)
- `recommended_next_steps` (list of strings)
- `pattern_signals` (list of strings)
- Full `shipment_data` fields (notify_party, incoterms, marks_and_numbers, vessel_or_flight, bill_of_lading_number, declared_currency, quantity, gross_weight, declared_value, arrival_date)

A `POST /api/v1/report/generate` endpoint that receives only a `shipment_id` cannot reconstruct the PDF from the database alone because the narrative content — the most legally significant part of the report — is gone.

### 4.2 Solution: Store Full Response Payload (Migration 004)

**Chosen approach:** Add a `report_payload TEXT` column to `shipment_history` containing the full serialized `AnalyzeResponse` as a JSON string, stored at the moment of analysis.

**Why this approach over alternatives:**
- **Option B (accept full payload in POST body):** The frontend would need to cache the entire analysis response client-side and re-POST it on button click. This works but is fragile — the cached data is lost on page refresh, and it forces the client to manage server-originated data. Not suitable for a filing/evidence system where the officer may generate the report minutes or hours after the analysis.
- **Option C (new `shipment_reports` table):** Cleanest schema separation but adds a join and additional migration complexity without meaningful benefit over storing JSON in the existing table. Premature normalization.
- **Option A (report_payload column — chosen):** The simplest path. Add one nullable TEXT column to `shipment_history`. At analysis time, serialize `AnalyzeResponse` to JSON and store it. The report endpoint fetches the row, deserializes the payload, and renders the PDF. Works with `shipment_id` alone. Idempotent: the same analysis always produces the same report content.

**Migration 004 schema change:**
```sql
ALTER TABLE shipment_history ADD COLUMN report_payload TEXT;
```

This is additive and backward-compatible. Existing rows will have NULL (no report available for pre-migration analyses).

### 4.3 Storage Estimate

An `AnalyzeResponse` JSON blob is typically 2–8 KB depending on the number of findings. At 10,000 shipments/month, that is 20–80 MB/month of additional SQLite data — negligible.

---

## 5. Backend Implementation Plan

### 5.1 Migration 004

File: `portguard/pattern_db.py` — add to `_MIGRATIONS` list:

```python
{
    "name": "004_report_payload",
    "sql": """
        ALTER TABLE shipment_history ADD COLUMN report_payload TEXT;
    """
}
```

### 5.2 Store Payload at Analysis Time

In `api/app.py`, after the analysis pipeline completes and before returning the `AnalyzeResponse`, serialize the response to JSON and write it to `shipment_history`:

```python
# After building analyze_response and inserting the shipment_history row:
payload_json = analyze_response.model_dump_json()
# UPDATE shipment_history SET report_payload = ? WHERE analysis_id = ?
```

The insert at analysis time should include `report_payload` as a column in the initial INSERT, not a subsequent UPDATE, to avoid a second round-trip.

### 5.3 New Endpoint

```
POST /api/v1/report/generate
Authorization: Bearer <token>
Content-Type: application/json

{
    "shipment_id": "uuid-string"
}

Response: 200 OK
Content-Type: application/pdf
Content-Disposition: attachment; filename="portguard-report-{short_id}-{date}.pdf"
Body: <binary PDF bytes>

Error responses:
  404: {"detail": "Shipment not found or report payload not available"}
  403: Shipment belongs to different organization (org isolation)
```

The endpoint:
1. Validates JWT, extracts `organization_id`
2. Queries `shipment_history` WHERE `analysis_id = shipment_id AND organization_id = org_id`
3. Checks `report_payload IS NOT NULL`
4. Deserializes payload JSON back to a dict
5. Calls `generate_pdf_report(payload, report_id, generated_at)` → bytes
6. Returns `Response(content=pdf_bytes, media_type="application/pdf", headers={...})`

### 5.4 PDF Generation Module

New file: `portguard/pdf_report.py`

```
generate_pdf_report(payload: dict, report_id: str, generated_at: str) -> bytes
```

Internal structure:
```
class PortGuardPDF(FPDF):
    def header(self): ...   # wordmark + page number + rule
    def footer(self): ...   # thin rule + page number

def _draw_decision_banner(pdf, decision, confidence): ...
def _draw_risk_bar(pdf, risk_score, risk_level, rule_score, pattern_score): ...
def _draw_shipment_table(pdf, shipment_data): ...
def _draw_findings(pdf, explanations): ...
def _draw_compliance_grid(pdf, explanations): ...
def _draw_pattern_section(pdf, pattern_signals, pattern_score, history_available): ...
def _draw_next_steps(pdf, recommended_next_steps): ...
def _draw_officer_section(pdf): ...
def _draw_disclaimer(pdf, report_id, generated_at): ...

def generate_pdf_report(payload: dict, report_id: str, generated_at: str) -> bytes:
    pdf = PortGuardPDF(...)
    # assemble all sections
    return pdf.output()  # returns bytes
```

### 5.5 Severity Classification Logic

```python
SEVERITY_RULES = [
    (["OFAC", "sanctions", "embargo", "Iran", "North Korea", "Cuba", "Syria", "Russia"], "CRITICAL"),
    (["Section 301", "tariff", "AD/CVD", "antidumping", "countervailing", "UFLPA"], "HIGH"),
    (["missing", "incomplete", "inconsistenc", "discrepan"], "MEDIUM"),
]

def _classify_severity(finding_text: str) -> str:
    for keywords, severity in SEVERITY_RULES:
        if any(kw.lower() in finding_text.lower() for kw in keywords):
            return severity
    return "LOW"
```

Severity color mapping:
- CRITICAL → red `(239, 68, 68)`
- HIGH → orange `(249, 115, 22)`
- MEDIUM → amber `(245, 158, 11)`
- LOW → blue `(59, 130, 246)`

### 5.6 Compliance Program Grid Population

```python
PROGRAMS = [
    ("OFAC Sanctions",        ["OFAC", "sanctions", "embargo"]),
    ("Section 301 Tariffs",   ["Section 301", "301 tariff"]),
    ("AD/CVD Orders",         ["AD/CVD", "antidumping", "countervailing"]),
    ("UFLPA (Forced Labor)",  ["UFLPA", "forced labor", "Xinjiang"]),
    ("ISF (10+2 Filing)",     ["ISF", "Importer Security Filing", "10+2", "missing"]),
    ("PGA Requirements",      ["PGA", "FDA", "USDA", "EPA", "FCC", "ATF"]),
]

def _build_compliance_grid(explanations: list[str]) -> list[dict]:
    rows = []
    for program_name, keywords in PROGRAMS:
        matching = [e for e in explanations
                    if any(kw.lower() in e.lower() for kw in keywords)]
        status = "HIT" if matching else "CLEAR"
        detail = matching[0][:120] + "…" if matching else "No issues identified"
        rows.append({"program": program_name, "status": status, "detail": detail})
    return rows
```

---

## 6. Frontend Implementation Plan

### 6.1 Download Button Placement

The "Download PDF Report" button appears in the analysis results panel in `demo.html`, positioned immediately below the Final Decision banner and above the findings list. It is visible only after a successful analysis response.

### 6.2 Button State Machine

```
[Initial]   → Button hidden (no analysis result yet)
[Analyzing] → Button hidden
[Result received] → Button visible, enabled: "Download PDF Report"
[Downloading]     → Button disabled, text: "Generating PDF…"
[Download complete] → Button returns to enabled state
[Error]     → Button re-enabled, brief error message shown below button
```

### 6.3 JavaScript Implementation

```javascript
async function downloadPdfReport(shipmentId) {
    const btn = document.getElementById('btn-download-pdf');
    btn.disabled = true;
    btn.textContent = 'Generating PDF…';

    try {
        const resp = await fetch('/api/v1/report/generate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${_authToken}`
            },
            body: JSON.stringify({ shipment_id: shipmentId })
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || `HTTP ${resp.status}`);
        }

        // Stream blob and trigger browser download
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;

        // Extract filename from Content-Disposition or construct it
        const disposition = resp.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename="?([^"]+)"?/);
        a.download = match ? match[1] : `portguard-report-${shipmentId.slice(0, 8)}.pdf`;

        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);

    } catch (e) {
        showError(`PDF generation failed: ${e.message}`);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Download PDF Report';
    }
}
```

### 6.4 Button HTML

```html
<button
    id="btn-download-pdf"
    class="btn-download-pdf"
    style="display:none"
    onclick="downloadPdfReport(currentShipmentId)">
    ⬇ Download PDF Report
</button>
```

The `currentShipmentId` variable is set when an analysis result is received (alongside where `renderResults()` is called). The button's `display` is toggled from `none` to `inline-flex` when a result with a `shipment_id` is rendered.

### 6.5 No Client-Side PDF Generation

The frontend does NOT generate the PDF. It only triggers the server endpoint and streams the response blob into a browser download. All PDF rendering is server-side only. This ensures:
- The report content is authoritative and cannot be tampered with client-side
- Large PDF libraries are not bundled into the frontend
- The same PDF can be regenerated from the server at any future time using only the `shipment_id`

---

## 7. File Changes Summary

| File | Change Type | Description |
|---|---|---|
| `requirements.txt` | Edit | Add `fpdf2>=2.7.9` |
| `portguard/pattern_db.py` | Edit | Add migration 004 (`report_payload TEXT` column) |
| `portguard/pdf_report.py` | **New file** | All PDF generation logic |
| `api/app.py` | Edit | Store `report_payload` at analysis time; add `POST /api/v1/report/generate` endpoint |
| `demo.html` | Edit | Add download button, `downloadPdfReport()` JS function, `currentShipmentId` tracking |
| `tests/test_pdf_report.py` | **New file** | Unit tests for PDF generation functions |

---

## 8. Implementation Order

1. `requirements.txt` — add fpdf2
2. `portguard/pattern_db.py` — add migration 004
3. `portguard/pdf_report.py` — implement full PDF generation module with tests
4. `api/app.py` — store payload at analysis time + add generate endpoint
5. `demo.html` — add download button and JS
6. Run full test suite; verify end-to-end with a real analysis → download → open PDF

---

## 9. Out of Scope

- Digital signatures / cryptographic signing of PDFs
- Email delivery of reports
- Bulk report export (multiple shipments in one PDF)
- Report templates / branding customization per organization
- Audit log entries for report downloads (may be added in a future sprint)
- Pre-migration analyses: rows with `report_payload IS NULL` return 404 on report generation — officers must re-analyze the shipment to generate a report
