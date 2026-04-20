"""
portguard.report_generator
==========================

Generates a formal, multi-page PDF compliance screening report from a
serialised AnalyzeResponse payload.

The entry points are:

* :func:`generate_report_from_payload` — takes a raw JSON string (as stored
  in ``shipment_history.report_payload``) and returns PDF bytes.
* :func:`generate_report_from_dict` — takes an already-parsed ``dict`` (or
  the result of ``AnalyzeResponse.model_dump()``) and returns PDF bytes.
  Use this for the immediate-download path right after analysis.

Both functions delegate to :class:`ReportGenerator`, which constructs a
:class:`PortGuardPDF` (an fpdf2 ``FPDF`` subclass with a branded
header/footer) and assembles each report section in order.

Design decisions
----------------
* **fpdf2 only** — pure Python, zero system-library dependencies, MIT licence.
* **No external font files** — uses fpdf2's built-in Helvetica throughout.
* **Decision banner is first and dominant** — an officer scanning a printed
  stack should know the outcome before reading a single word of detail.
* **B&W-safe risk bar** — tick marks and percentage labels ensure the bar is
  readable when printed on a monochrome laser printer.
* **Multi-cell long values** — consignee addresses and commodity descriptions
  wrap gracefully instead of being silently truncated.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fpdf import FPDF

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Colour palette (RGB tuples) — mirrors demo.html CSS tokens
# ---------------------------------------------------------------------------

_C_WHITE          = (255, 255, 255)
_C_NEAR_BLACK     = (15,  25,  45)
_C_DARK_NAVY      = (12,  26,  46)
_C_CARD           = (16,  31,  51)
_C_MID_GRAY       = (110, 125, 145)
_C_LIGHT_GRAY     = (225, 229, 236)
_C_RULE_GRAY      = (210, 215, 225)
_C_ROW_ALT        = (245, 247, 250)
_C_LABEL_GRAY     = (95,  115, 140)

# Decision colours
_C_GREEN          = (34,  197, 94)
_C_AMBER          = (245, 158, 11)
_C_ORANGE         = (249, 115, 22)
_C_BLUE           = (59,  130, 246)
_C_RED            = (220, 48,  48)

# Derived
_C_SEV_CRITICAL   = _C_RED
_C_SEV_HIGH       = _C_ORANGE
_C_SEV_MEDIUM     = _C_AMBER
_C_SEV_LOW        = _C_BLUE

_C_HEADER_BG      = _C_DARK_NAVY
_C_TABLE_HEADER   = (28,  48,  78)

# ---------------------------------------------------------------------------
# Decision display config
# ---------------------------------------------------------------------------

_DECISION_CONFIG: dict[str, dict] = {
    "APPROVE": {
        "label":      "APPROVED FOR RELEASE",
        "sublabel":   "Automated screening found no significant compliance violations.",
        "color":      (22, 140, 68),
        "text_color": _C_WHITE,
        "icon":       "OK",
    },
    "REVIEW_RECOMMENDED": {
        "label":      "REVIEW RECOMMENDED",
        "sublabel":   "One or more findings require officer review before release.",
        "color":      (180, 110, 0),
        "text_color": _C_WHITE,
        "icon":       "!",
    },
    "FLAG_FOR_INSPECTION": {
        "label":      "FLAG FOR INSPECTION",
        "sublabel":   "Significant violations detected. Physical inspection required.",
        "color":      (200, 80, 10),
        "text_color": _C_WHITE,
        "icon":       "!",
    },
    "REQUEST_MORE_INFORMATION": {
        "label":      "ADDITIONAL INFORMATION REQUIRED",
        "sublabel":   "Screening is inconclusive. Supplementary documentation must be obtained.",
        "color":      (35, 90, 190),
        "text_color": _C_WHITE,
        "icon":       "?",
    },
    "REJECT": {
        "label":      "REJECTED — DO NOT RELEASE",
        "sublabel":   "Critical violations detected. Cargo must be held pending investigation.",
        "color":      (180, 30, 30),
        "text_color": _C_WHITE,
        "icon":       "X",
    },
}

_DECISION_DEFAULT = {
    "label":      "DECISION UNKNOWN",
    "sublabel":   "Decision could not be determined. Manual review required.",
    "color":      (80, 90, 110),
    "text_color": _C_WHITE,
    "icon":       "?",
}

# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------

_SEVERITY_RULES: list[tuple[list[str], str]] = [
    (
        ["ofac", "sanctions", "embargo", "iran", "north korea", "cuba", "syria",
         "russia", "dprk", "crimea", "donetsk", "luhansk", "sdn"],
        "CRITICAL",
    ),
    (
        ["section 301", "301 tariff", "ad/cvd", "antidumping", "countervailing",
         "uflpa", "forced labor", "xinjiang"],
        "HIGH",
    ),
    (
        ["missing", "incomplete", "inconsistenc", "discrepan", "not stated",
         "not provided", "mismatch", "isf", "10+2", "undervaluation", "deficiency"],
        "MEDIUM",
    ),
]

_SEVERITY_COLORS: dict[str, tuple[int, int, int]] = {
    "CRITICAL": _C_SEV_CRITICAL,
    "HIGH":     _C_SEV_HIGH,
    "MEDIUM":   _C_SEV_MEDIUM,
    "LOW":      _C_SEV_LOW,
}

# B&W-safe grayscale equivalents for severity (used in B&W print legend)
_SEVERITY_BW: dict[str, str] = {
    "CRITICAL": "||||| (Critical)",
    "HIGH":     "||||  (High)",
    "MEDIUM":   "|||   (Medium)",
    "LOW":      "||    (Low)",
}


def _classify_severity(text: str) -> str:
    """Return CRITICAL / HIGH / MEDIUM / LOW for a finding string."""
    lower = text.lower()
    for keywords, severity in _SEVERITY_RULES:
        if any(kw in lower for kw in keywords):
            return severity
    return "LOW"


# ---------------------------------------------------------------------------
# Compliance grid
# ---------------------------------------------------------------------------

_COMPLIANCE_PROGRAMS: list[tuple[str, list[str]]] = [
    ("OFAC Sanctions",       ["ofac", "sanctions", "embargo", "sdn"]),
    ("Section 301 Tariffs",  ["section 301", "301 tariff"]),
    ("AD/CVD Orders",        ["ad/cvd", "antidumping", "countervailing"]),
    ("UFLPA (Forced Labor)", ["uflpa", "forced labor", "xinjiang"]),
    ("ISF (10+2 Filing)",    ["isf", "importer security filing", "10+2"]),
    ("PGA Requirements",     ["pga", "fda", "usda", "epa", "fcc", "atf", "prior notice",
                               "fcc id", "equipment authorization"]),
]


def _build_compliance_grid(explanations: list[str]) -> list[dict]:
    """Map findings to compliance programs and return a status grid."""
    rows: list[dict] = []
    for program_name, keywords in _COMPLIANCE_PROGRAMS:
        matches = [e for e in explanations
                   if any(kw in e.lower() for kw in keywords)]
        if matches:
            detail = matches[0]
            if "isf" in program_name.lower():
                status = "INCOMPLETE"
            elif "pga" in program_name.lower():
                status = "FLAGGED"
            else:
                status = "HIT"
        else:
            detail = "No issues identified."
            status = "CLEAR"
        rows.append({"program": program_name, "status": status, "detail": detail})
    return rows


# ---------------------------------------------------------------------------
# Risk helpers
# ---------------------------------------------------------------------------

def _risk_level_from_score(score: float) -> str:
    if score <= 0.25:
        return "LOW"
    if score <= 0.50:
        return "MEDIUM"
    if score <= 0.75:
        return "HIGH"
    return "CRITICAL"


def _risk_color(score: float) -> tuple[int, int, int]:
    return {
        "LOW":      _C_GREEN,
        "MEDIUM":   _C_AMBER,
        "HIGH":     _C_ORANGE,
        "CRITICAL": _C_RED,
    }[_risk_level_from_score(score)]


# ---------------------------------------------------------------------------
# PortGuardPDF — FPDF subclass
# ---------------------------------------------------------------------------

class PortGuardPDF(FPDF):
    """fpdf2 FPDF subclass with PortGuard branded header/footer on every page."""

    def __init__(self, report_id: str, generated_at: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self._report_id    = report_id
        self._generated_at = generated_at
        self.set_auto_page_break(auto=True, margin=25)
        self.alias_nb_pages()

    def header(self) -> None:
        """Dark navy header band: wordmark left, report metadata right."""
        # Full-width band
        self.set_fill_color(*_C_HEADER_BG)
        self.rect(0, 0, 210, 18, style="F")

        # Thin accent line at bottom of band
        self.set_fill_color(59, 130, 246)
        self.rect(0, 17.2, 210, 0.8, style="F")

        # Wordmark
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(*_C_WHITE)
        self.set_xy(10, 3)
        self.cell(70, 7, "PORTGUARD", ln=False)

        # Tagline
        self.set_font("Helvetica", "", 7)
        self.set_text_color(155, 180, 210)
        self.set_xy(10, 10.5)
        self.cell(70, 4, "Customs Compliance Screening System", ln=False)

        # Right: report ID and timestamp
        self.set_font("Helvetica", "", 6.5)
        self.set_text_color(140, 168, 200)
        self.set_xy(105, 3.5)
        self.cell(95, 4, f"Report ID: {self._report_id}", ln=False, align="R")
        self.set_xy(105, 8.5)
        self.cell(95, 4, f"Generated: {self._generated_at}", ln=False, align="R")

        # Cursor below band
        self.set_xy(10, 22)
        self.set_text_color(*_C_NEAR_BLACK)

    def footer(self) -> None:
        """Thin rule + page number + classification tag."""
        self.set_y(-20)
        self.set_draw_color(*_C_RULE_GRAY)
        self.set_line_width(0.4)
        self.line(10, self.get_y(), 200, self.get_y())

        self.set_font("Helvetica", "B", 7.5)
        self.set_text_color(*_C_MID_GRAY)
        self.set_y(-17)
        self.cell(0, 5, f"Page {self.page_no()} of " + "{nb}", align="C")

        self.set_y(-12)
        self.set_font("Helvetica", "I", 6)
        self.set_text_color(150, 160, 175)
        self.cell(0, 4,
                  "CONTROLLED  -  LAW ENFORCEMENT SENSITIVE  -  NOT FOR PUBLIC RELEASE",
                  align="C")
        self.set_line_width(0.2)


# ---------------------------------------------------------------------------
# ReportGenerator
# ---------------------------------------------------------------------------

class ReportGenerator:
    """Assembles the full PortGuard PDF compliance report.

    Section render order (as required by the architecture plan):
    1. Decision banner        — dominant, full-width, colour-coded, FIRST element
    2. Key metrics strip      — risk score, confidence, documents, processing time
    3. Report metadata        — IDs, timestamps, classification
    4. Shipment summary       — all parties, routing, cargo details
    5. Risk assessment        — visual bar with B&W-safe tick marks
    6. Rule violations        — severity-badged numbered findings
    7. Compliance grid        — six-program screening results table
    8. Pattern intelligence   — signals and blend weights
    9. Recommended next steps — numbered action list
    10. Officer review        — signature block, checkboxes, notes
    11. Legal disclaimer      — mandatory advisory notice
    """

    _MARGIN_LEFT  = 10.0
    _MARGIN_RIGHT = 10.0
    _PAGE_W       = 210.0
    _USABLE_W     = 190.0   # _PAGE_W - _MARGIN_LEFT - _MARGIN_RIGHT

    def __init__(
        self,
        payload: dict,
        report_id: Optional[str] = None,
        generated_at: Optional[str] = None,
    ) -> None:
        self._payload      = payload
        self._report_id    = report_id or str(uuid.uuid4())
        self._generated_at = generated_at or datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        self._pdf: Optional[PortGuardPDF] = None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def build(self) -> bytes:
        """Assemble the full PDF and return raw bytes."""
        self._pdf = PortGuardPDF(
            report_id=self._report_id,
            generated_at=self._generated_at,
        )
        self._pdf.add_page()

        try:
            # Page 1: decision, key metrics, metadata, shipment summary, risk
            self._section_decision_banner()
            self._section_key_metrics()
            self._section_metadata()
            self._section_shipment_summary()
            self._section_risk_panel()
            # Subsequent pages: findings, compliance, pattern, steps
            self._section_findings()
            self._section_compliance_grid()
            self._section_sustainability()
            self._section_pattern_intelligence()
            self._section_next_steps()
            # Final page: officer review + disclaimer
            # Only add a new page if there's not enough room (< 90 mm remaining)
            if self._pdf.get_y() > 177:
                self._pdf.add_page()
            else:
                self._pdf.ln(6)
                self._horizontal_rule(weight=0.8)
                self._pdf.ln(4)
            self._section_officer_review()
            self._section_disclaimer()
        except Exception as exc:
            raise ReportGenerationError(f"PDF assembly failed: {exc}") from exc

        return bytes(self._pdf.output())

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def _section_decision_banner(self) -> None:
        """Full-width colour-coded decision banner — the dominant element on page 1.

        Renders a tall (42 mm) rectangle filled with the decision colour.
        The decision label is centred in 26pt bold uppercase text so it is
        legible from arm's length.  A sub-label below the main label gives
        a one-sentence plain-English interpretation.  Confidence level is
        shown bottom-right in a small badge.
        """
        pdf = self._pdf
        decision   = self._payload.get("decision", "UNKNOWN")
        confidence = self._payload.get("confidence") or "-"
        cfg        = _DECISION_CONFIG.get(decision, _DECISION_DEFAULT)

        x = self._MARGIN_LEFT
        w = self._USABLE_W
        y = pdf.get_y()
        h = 38  # total banner height in mm

        # Main filled rectangle
        pdf.set_fill_color(*cfg["color"])
        pdf.rect(x, y, w, h, style="F")

        # Thin bottom accent stripe (white, 1mm)
        pdf.set_fill_color(255, 255, 255)
        pdf.rect(x, y + h - 1, w, 1, style="F")

        # Decision label — centred, large, bold
        pdf.set_font("Helvetica", "B", 26)
        pdf.set_text_color(*cfg["text_color"])
        pdf.set_xy(x, y + 7)
        pdf.cell(w, 12, self._t(cfg["label"]), align="C", ln=True)

        # Sub-label — smaller, lighter
        pdf.set_font("Helvetica", "", 9)
        r, g, b = cfg["text_color"]
        # Slightly transparent — blend toward colour background
        pdf.set_text_color(
            min(255, r + 40) if r < 200 else r - 30,
            min(255, g + 40) if g < 200 else g - 30,
            min(255, b + 40) if b < 200 else b - 30,
        )
        pdf.set_xy(x, y + 22)
        pdf.cell(w - 35, 6, self._t(cfg["sublabel"]), align="C", ln=False)

        # Confidence badge — bottom-right corner of banner
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*cfg["text_color"])
        pdf.set_xy(x + w - 34, y + h - 11)
        pdf.cell(32, 5, f"Confidence: {confidence}", align="R", ln=False)

        pdf.set_xy(x, y + h + 2)
        pdf.set_text_color(*_C_NEAR_BLACK)

    def _section_key_metrics(self) -> None:
        """Render a horizontal strip of four key metric boxes below the banner.

        Shows: Risk Score, Risk Level, Documents Analysed, Processing Time.
        Uses a light-bordered box layout so the metrics are scannable at a
        glance without competing with the decision banner above.
        """
        pdf = self._pdf
        pdf.ln(2)

        risk_score  = float(self._payload.get("risk_score") or 0.0)
        risk_level  = self._payload.get("risk_level") or _risk_level_from_score(risk_score)
        docs        = self._payload.get("documents_analyzed")
        proc_time   = self._payload.get("processing_time_seconds")
        inconsist   = self._payload.get("inconsistencies_found")

        metrics = [
            ("RISK SCORE",     f"{risk_score * 100:.0f} / 100"),
            ("RISK LEVEL",     risk_level),
            ("DOCS ANALYZED",  str(docs) if docs is not None else "-"),
            ("INCONSISTENCIES", str(inconsist) if inconsist is not None else "-"),
        ]

        box_w = self._USABLE_W / len(metrics)
        x     = self._MARGIN_LEFT
        y     = pdf.get_y()
        bh    = 14  # box height

        for i, (label, value) in enumerate(metrics):
            bx = x + i * box_w
            # Box border
            pdf.set_draw_color(*_C_RULE_GRAY)
            pdf.set_line_width(0.3)
            pdf.rect(bx, y, box_w, bh)

            # Alternating very subtle fill
            if i % 2 == 0:
                pdf.set_fill_color(248, 249, 252)
                pdf.rect(bx, y, box_w, bh, style="F")
                pdf.set_draw_color(*_C_RULE_GRAY)
                pdf.rect(bx, y, box_w, bh)

            # Label
            pdf.set_font("Helvetica", "B", 6.5)
            pdf.set_text_color(*_C_LABEL_GRAY)
            pdf.set_xy(bx + 2, y + 1.5)
            pdf.cell(box_w - 4, 4, label, align="C", ln=False)

            # Value
            is_risk_score = (label == "RISK SCORE")
            is_risk_level = (label == "RISK LEVEL")
            val_color = _C_NEAR_BLACK
            if is_risk_score or is_risk_level:
                val_color = _risk_color(risk_score)
            pdf.set_font("Helvetica", "B", 13)
            pdf.set_text_color(*val_color)
            pdf.set_xy(bx + 2, y + 6)
            pdf.cell(box_w - 4, 7, value, align="C", ln=False)

        pdf.set_xy(self._MARGIN_LEFT, y + bh + 3)
        pdf.set_text_color(*_C_NEAR_BLACK)

    def _section_metadata(self) -> None:
        """Compact report metadata: IDs, classification, timestamps."""
        self._section_heading("REPORT INFORMATION")
        shipment_id = self._payload.get("shipment_id") or "Not recorded"
        analyzed_at = self._payload.get("analyzed_at") or "Unknown"
        rows: list[tuple[str, str]] = [
            ("Report ID",           self._report_id),
            ("Generated At",        self._generated_at),
            ("Classification",      "CONTROLLED - LAW ENFORCEMENT SENSITIVE"),
            ("Shipment ID",         shipment_id),
            ("Analyzed At",         analyzed_at),
            ("Documents Analyzed",  str(self._payload.get("documents_analyzed", "-"))),
        ]
        self._two_col_table(rows)

    def _section_shipment_summary(self) -> None:
        """Full shipment party and cargo summary.

        Long values (consignee address, commodity description) wrap to
        multiple lines rather than being silently truncated.
        """
        self._section_heading("SHIPMENT SUMMARY")
        sd: dict = self._payload.get("shipment_data") or {}

        hs_codes = sd.get("hts_codes_declared") or []
        hs_str   = ", ".join(hs_codes) if hs_codes else None

        rows: list[tuple[str, str]] = [
            ("Shipper / Exporter",     sd.get("exporter") or sd.get("importer") or "-"),
            ("Importer of Record",     sd.get("importer") or "-"),
            ("Consignee",              sd.get("consignee") or "-"),
            ("Notify Party",           sd.get("notify_party") or "-"),
            ("Origin Country",         self._fmt_country(sd)),
            ("Destination Country",    sd.get("destination_country") or "-"),
            ("Port of Loading",        sd.get("port_of_loading") or "-"),
            ("Port of Discharge",      sd.get("port_of_discharge") or "-"),
            ("Port of Entry",          sd.get("port_of_entry") or "-"),
            ("Vessel / Flight",        sd.get("vessel_or_flight") or "-"),
            ("Bill of Lading No.",     sd.get("bill_of_lading_number") or "-"),
            ("Shipment Date",          sd.get("shipment_date") or "-"),
            ("Arrival Date",           sd.get("arrival_date") or "-"),
            ("Incoterms",              sd.get("incoterms") or "-"),
            ("HS Codes (Declared)",    hs_str or "-"),
            ("Commodity Description",  sd.get("commodity_description") or "-"),
            ("Declared Value",         self._fmt_value(sd)),
            ("Gross Weight",           sd.get("gross_weight") or "-"),
            ("Quantity",               sd.get("quantity") or "-"),
            ("Marks & Numbers",        sd.get("marks_and_numbers") or "-"),
        ]
        self._two_col_table(rows)

    def _section_risk_panel(self) -> None:
        """Risk score with a horizontal progress bar that is B&W-print safe.

        The bar is annotated with:
        - Numeric percentage at the fill point
        - Tick marks at 25 %, 50 %, 75 % with zone labels (LOW / MEDIUM /
          HIGH / CRITICAL) so the risk level is unambiguous on a monochrome
          laser printout
        - The filled portion uses a dark fill colour that renders as a
          distinct grey in black-and-white
        """
        pdf = self._pdf
        self._section_heading("RISK ASSESSMENT")

        risk_score    = float(self._payload.get("risk_score") or 0.0)
        risk_level    = self._payload.get("risk_level") or _risk_level_from_score(risk_score)
        pattern_score = self._payload.get("pattern_score")
        history_avail = self._payload.get("history_available", False)
        pct           = risk_score * 100
        bar_color     = _risk_color(risk_score)

        x = self._MARGIN_LEFT
        y = pdf.get_y()

        # ---- Large percentage number (left) ----
        pdf.set_font("Helvetica", "B", 32)
        pdf.set_text_color(*bar_color)
        pdf.set_xy(x, y)
        pdf.cell(28, 14, f"{pct:.0f}%", ln=False)

        # Risk level label + description
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*_C_NEAR_BLACK)
        pdf.set_xy(x + 30, y + 1)
        pdf.cell(40, 6, f"Risk Level:", ln=False)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*bar_color)
        pdf.set_xy(x + 55, y + 1)
        pdf.cell(30, 6, risk_level, ln=False)

        # ---- Horizontal bar ----
        bx = x + 30
        by = y + 8.5
        bar_total = self._USABLE_W - 30   # mm
        bh        = 7.0

        # Track background (light gray)
        pdf.set_fill_color(*_C_LIGHT_GRAY)
        pdf.set_draw_color(*_C_RULE_GRAY)
        pdf.set_line_width(0.3)
        pdf.rect(bx, by, bar_total, bh, style="FD")

        # Coloured fill — sized proportionally
        fill_w = bar_total * risk_score
        if fill_w > 0.5:
            pdf.set_fill_color(*bar_color)
            pdf.rect(bx, by, fill_w, bh, style="F")

        # Tick marks at 25%, 50%, 75% — visible in B&W
        pdf.set_draw_color(*_C_DARK_NAVY)
        pdf.set_line_width(0.5)
        for frac in (0.25, 0.50, 0.75):
            tx = bx + bar_total * frac
            pdf.line(tx, by, tx, by + bh)

        # Zone labels below bar
        pdf.set_font("Helvetica", "", 6)
        pdf.set_text_color(*_C_MID_GRAY)
        zone_labels = [
            (0.125, "LOW"),
            (0.375, "MEDIUM"),
            (0.625, "HIGH"),
            (0.875, "CRITICAL"),
        ]
        for frac, label in zone_labels:
            lx = bx + bar_total * frac
            pdf.set_xy(lx - 8, by + bh + 0.5)
            pdf.cell(16, 3.5, label, align="C", ln=False)

        # Percentage label at right end of fill
        if fill_w > 10:
            pdf.set_font("Helvetica", "B", 6.5)
            pdf.set_text_color(*_C_WHITE)
            pdf.set_xy(bx + fill_w - 14, by + 0.5)
            pdf.cell(13, bh - 1, f"{pct:.0f}%", align="R", ln=False)

        pdf.set_xy(x, by + bh + 5)

        # Component scores
        if history_avail and pattern_score is not None:
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*_C_MID_GRAY)
            pdf.set_x(x)
            pdf.cell(
                self._USABLE_W, 5,
                f"Rule-based component: {risk_score:.3f}   |   "
                f"Pattern intelligence component: {pattern_score:.3f}   |   "
                "Blend: Rule 65% / Pattern 35%",
                ln=True,
            )
            pdf.ln(1)

        pdf.set_text_color(*_C_NEAR_BLACK)
        pdf.ln(2)

    def _section_findings(self) -> None:
        """Numbered finding blocks with colour-coded severity badges.

        Each block shows:
        - A coloured severity pill (CRITICAL / HIGH / MEDIUM / LOW)
        - A ``Finding N of M`` counter
        - The full finding text, word-wrapped to the page width
        """
        pdf = self._pdf
        self._section_heading("RULE VIOLATIONS & FINDINGS")

        explanations: list[str] = self._payload.get("explanations") or []

        if not explanations:
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(*_C_MID_GRAY)
            pdf.set_x(self._MARGIN_LEFT)
            pdf.cell(self._USABLE_W, 6, "No rule violations detected.", ln=True)
            pdf.set_text_color(*_C_NEAR_BLACK)
            pdf.ln(2)
            return

        total = len(explanations)
        for i, finding in enumerate(explanations, start=1):
            severity  = _classify_severity(finding)
            sev_color = _SEVERITY_COLORS[severity]

            # Need ~20 mm for a typical finding block
            if pdf.get_y() > 254:
                pdf.add_page()

            x = self._MARGIN_LEFT
            y = pdf.get_y()

            # Severity pill — wider and taller for better legibility
            pill_w = 28
            pill_h = 6
            pdf.set_fill_color(*sev_color)
            pdf.rect(x, y, pill_w, pill_h, style="F")
            pdf.set_font("Helvetica", "B", 7.5)
            pdf.set_text_color(*_C_WHITE)
            pdf.set_xy(x, y + 0.5)
            pdf.cell(pill_w, pill_h - 1, severity, align="C", ln=False)

            # Finding counter
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*_C_NEAR_BLACK)
            pdf.set_xy(x + pill_w + 3, y + 0.5)
            pdf.cell(50, pill_h - 1, f"Finding {i} of {total}", ln=True)

            # Finding text — word-wrapped
            pdf.set_font("Helvetica", "", 8.5)
            pdf.set_text_color(*_C_NEAR_BLACK)
            pdf.set_x(x)
            pdf.multi_cell(self._USABLE_W, 5, self._t(finding), ln=True)
            pdf.ln(2)

        pdf.ln(1)

    def _section_compliance_grid(self) -> None:
        """Six-program compliance screening results table.

        Columns: Program (52 mm), Status badge (26 mm), Detail (remainder).
        The detail column uses ``multi_cell`` so longer findings wrap
        gracefully.  Row height is calculated before drawing the alternating
        background so it always covers the wrapped text.
        """
        pdf = self._pdf
        self._section_heading("COMPLIANCE SCREENING RESULTS")

        explanations = self._payload.get("explanations") or []
        grid         = _build_compliance_grid(explanations)

        col_w   = [52.0, 26.0, self._USABLE_W - 78.0]
        headers = ["Program", "Status", "Detail / Finding"]
        self._table_header_row(headers, col_w)

        for idx, row in enumerate(grid):
            if pdf.get_y() > 255:
                pdf.add_page()
                self._table_header_row(headers, col_w)

            status     = row["status"]
            stat_color = (
                _C_RED    if status in ("HIT", "FLAGGED") else
                _C_AMBER  if status == "INCOMPLETE" else
                _C_GREEN
            )
            detail_str = self._t(row["detail"])
            # Estimate wrapped lines for the detail column
            chars_per_line = max(1, int(col_w[2] / 2.1))
            num_lines      = max(1, math.ceil(len(detail_str) / chars_per_line))
            row_h          = max(9.0, 5.5 * num_lines + 2)

            x = self._MARGIN_LEFT
            y = pdf.get_y()

            # Alternating row background
            if idx % 2 == 1:
                pdf.set_fill_color(*_C_ROW_ALT)
                pdf.rect(x, y, self._USABLE_W, row_h, style="F")

            # Program name (vertically centred)
            pdf.set_font("Helvetica", "", 8.5)
            pdf.set_text_color(*_C_NEAR_BLACK)
            pdf.set_xy(x + 1, y + (row_h - 5) / 2)
            pdf.cell(col_w[0] - 2, 5, row["program"], border=0, ln=False)

            # Status badge (vertically centred)
            badge_x = x + col_w[0] + 2
            badge_y = y + (row_h - 5.5) / 2
            badge_w = col_w[1] - 4
            badge_h = 5.5
            pdf.set_fill_color(*stat_color)
            pdf.rect(badge_x, badge_y, badge_w, badge_h, style="F")
            pdf.set_font("Helvetica", "B", 7)
            pdf.set_text_color(*_C_WHITE)
            pdf.set_xy(badge_x, badge_y + 0.25)
            pdf.cell(badge_w, badge_h - 0.5, status, align="C", ln=False)

            # Detail — word-wrapped
            pdf.set_font("Helvetica", "", 7.5)
            pdf.set_text_color(*_C_NEAR_BLACK)
            detail_x = x + col_w[0] + col_w[1]
            pdf.set_xy(detail_x + 1, y + 1.5)
            pdf.multi_cell(col_w[2] - 2, 5, detail_str, ln=False)

            # Advance cursor past row
            pdf.set_xy(x, y + row_h)

        # Bottom border
        pdf.set_draw_color(*_C_RULE_GRAY)
        pdf.set_line_width(0.3)
        pdf.line(self._MARGIN_LEFT, pdf.get_y(),
                 self._MARGIN_LEFT + self._USABLE_W, pdf.get_y())
        pdf.ln(5)
        pdf.set_text_color(*_C_NEAR_BLACK)

    def _section_sustainability(self) -> None:
        """Sustainability Assessment section — grade, risk signals, certifications."""
        rating = self._payload.get("sustainability_rating")
        if not rating:
            return

        grade = rating.get("grade", "N/A") if isinstance(rating, dict) else getattr(rating, "grade", "N/A")
        if grade == "N/A":
            return

        pdf = self._pdf
        self._section_heading("SUSTAINABILITY ASSESSMENT")

        def _get(key: str, default=None):
            if isinstance(rating, dict):
                return rating.get(key, default)
            return getattr(rating, key, default)

        # Grade badge row
        _GRADE_COLORS: dict[str, tuple[int, int, int]] = {
            "A": (29, 184, 122),
            "B": (27, 168, 168),
            "C": (232, 168, 56),
            "D": (224, 80, 80),
        }
        grade_color = _GRADE_COLORS.get(grade, _C_MID_GRAY)

        x = self._MARGIN_LEFT
        y = pdf.get_y()

        # Grade square
        sq = 18
        pdf.set_fill_color(*grade_color)
        pdf.rect(x, y, sq, sq, style="F")
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(*_C_WHITE)
        pdf.set_xy(x, y + 2)
        pdf.cell(sq, sq - 4, grade, align="C", ln=False)

        # Grade label + risk pills (same row, right of square)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*_C_NEAR_BLACK)
        pdf.set_xy(x + sq + 4, y + 1)
        grade_labels = {
            "A": "Strong sustainability profile",
            "B": "Good sustainability profile",
            "C": "Moderate sustainability concerns",
            "D": "Significant sustainability gaps",
        }
        pdf.cell(self._USABLE_W - sq - 4, 5, grade_labels.get(grade, ""), ln=True)

        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*_C_MID_GRAY)
        pdf.set_x(x + sq + 4)
        inherent = _get("inherent_risk_level", "N/A")
        country  = _get("country_risk_level",  "N/A")
        product  = _get("product_risk_level",  "N/A")
        pdf.cell(
            self._USABLE_W - sq - 4, 5,
            f"Inherent Risk: {inherent}   Country Risk: {country}   Product Risk: {product}",
            ln=True,
        )
        pdf.ln(max(0, (y + sq + 2) - pdf.get_y()))

        pdf.set_text_color(*_C_NEAR_BLACK)
        pdf.ln(3)

        # Signals
        signals = _get("signals", []) or []
        if signals:
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_x(x)
            pdf.cell(self._USABLE_W, 5, "Sustainability Signals:", ln=True)
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*_C_MID_GRAY)
            for sig in signals[:8]:
                if pdf.get_y() > 265:
                    pdf.add_page()
                pdf.set_x(x + 3)
                pdf.cell(self._USABLE_W - 3, 4.5, f"\u2022 {self._t(sig)}", ln=True)
            pdf.set_text_color(*_C_NEAR_BLACK)
            pdf.ln(2)

        # Detected certifications
        detected = _get("certifications_detected", []) or []
        missing  = _get("certifications_missing",  []) or []

        if detected:
            if pdf.get_y() > 255:
                pdf.add_page()
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_x(x)
            pdf.cell(self._USABLE_W, 5, "Certifications Detected:", ln=True)
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(29, 184, 122)
            for cert in detected:
                if pdf.get_y() > 265:
                    pdf.add_page()
                pdf.set_x(x + 3)
                pdf.cell(self._USABLE_W - 3, 4.5, f"\u2713 {self._t(cert)}", ln=True)
            pdf.set_text_color(*_C_NEAR_BLACK)
            pdf.ln(2)

        if missing:
            if pdf.get_y() > 255:
                pdf.add_page()
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_x(x)
            pdf.cell(self._USABLE_W, 5, "Recommended Certifications:", ln=True)
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(200, 140, 40)
            for cert in missing[:10]:
                if pdf.get_y() > 265:
                    pdf.add_page()
                pdf.set_x(x + 3)
                pdf.cell(self._USABLE_W - 3, 4.5, f"\u26a0 {self._t(cert)}", ln=True)
            pdf.set_text_color(*_C_NEAR_BLACK)
            pdf.ln(2)

        pdf.ln(2)

    def _section_pattern_intelligence(self) -> None:
        """Pattern intelligence signals and blend weight information."""
        pdf = self._pdf
        self._section_heading("PATTERN INTELLIGENCE ANALYSIS")

        history_avail = self._payload.get("history_available", False)
        pattern_score = self._payload.get("pattern_score")
        signals       = self._payload.get("pattern_signals") or []

        if not history_avail:
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(*_C_MID_GRAY)
            pdf.set_x(self._MARGIN_LEFT)
            pdf.multi_cell(
                self._USABLE_W, 5.5,
                "Pattern intelligence is unavailable for this shipment. "
                "Insufficient history for this shipper / route combination "
                "(cold start). Pattern analysis activates after 3 or more "
                "prior shipments from the same entity.",
                ln=True,
            )
            pdf.set_text_color(*_C_NEAR_BLACK)
            pdf.ln(2)
            return

        if pattern_score is not None:
            pct   = pattern_score * 100
            color = _risk_color(pattern_score)
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*_C_NEAR_BLACK)
            pdf.set_x(self._MARGIN_LEFT)
            pdf.cell(52, 5.5, "Pattern Risk Score:", ln=False)
            pdf.set_text_color(*color)
            pdf.cell(25, 5.5, f"{pct:.1f}%", ln=True)
            pdf.set_text_color(*_C_NEAR_BLACK)
            pdf.ln(1)

        pdf.set_font("Helvetica", "", 8.5)
        if signals:
            for signal in signals:
                if pdf.get_y() > 258:
                    pdf.add_page()
                pdf.set_x(self._MARGIN_LEFT)
                pdf.cell(5, 5.5, "*", ln=False)
                pdf.set_x(self._MARGIN_LEFT + 5)
                pdf.multi_cell(self._USABLE_W - 5, 5.5, self._t(signal), ln=True)
        else:
            pdf.set_font("Helvetica", "I", 8.5)
            pdf.set_text_color(*_C_MID_GRAY)
            pdf.set_x(self._MARGIN_LEFT)
            pdf.cell(self._USABLE_W, 5.5, "No pattern signals triggered.", ln=True)
            pdf.set_text_color(*_C_NEAR_BLACK)

        pdf.ln(3)

    def _section_next_steps(self) -> None:
        """Numbered recommended next steps list."""
        pdf = self._pdf
        self._section_heading("RECOMMENDED NEXT STEPS")

        steps: list[str] = self._payload.get("recommended_next_steps") or []

        if not steps:
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(*_C_MID_GRAY)
            pdf.set_x(self._MARGIN_LEFT)
            pdf.cell(self._USABLE_W, 5.5, "No specific action steps recorded.", ln=True)
            pdf.set_text_color(*_C_NEAR_BLACK)
            pdf.ln(2)
            return

        pdf.set_font("Helvetica", "", 9)
        for i, step in enumerate(steps, start=1):
            if pdf.get_y() > 255:
                pdf.add_page()
            pdf.set_x(self._MARGIN_LEFT)
            pdf.set_text_color(*_C_NEAR_BLACK)
            pdf.cell(8, 5.5, f"{i}.", ln=False)
            pdf.set_x(self._MARGIN_LEFT + 8)
            pdf.multi_cell(self._USABLE_W - 8, 5.5, self._t(step), ln=True)

        pdf.ln(3)

    def _section_officer_review(self) -> None:
        """Officer review and certification block.

        Renders:
        - Four fillable fields (name, badge, date, signature) with adequate
          vertical space — the Signature field is taller (20 mm) so officers
          can actually sign legibly.
        - A six-option action checkbox grid.
        - Four ruled notes lines with generous line spacing.
        """
        pdf = self._pdf
        self._section_heading("OFFICER REVIEW & CERTIFICATION")

        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(*_C_NEAR_BLACK)

        def _label_field(label: str, field_height: float = 10.0) -> None:
            """Draw a labelled field with a ruled underline."""
            if pdf.get_y() + field_height + 4 > 272:
                pdf.add_page()
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*_C_LABEL_GRAY)
            pdf.set_x(self._MARGIN_LEFT)
            pdf.cell(self._USABLE_W, 5, label.upper(), ln=True)
            # Ruled underline at bottom of field
            pdf.set_draw_color(*_C_RULE_GRAY)
            pdf.set_line_width(0.5)
            fy = pdf.get_y() + field_height
            pdf.line(self._MARGIN_LEFT, fy,
                     self._MARGIN_LEFT + self._USABLE_W, fy)
            pdf.ln(field_height + 4)

        _label_field("Officer Name")
        _label_field("Badge / Employee ID")
        _label_field("Date of Review")
        _label_field("Signature", field_height=20.0)  # extra height for actual signature

        pdf.ln(2)

        # Action checkboxes
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*_C_NEAR_BLACK)
        pdf.set_x(self._MARGIN_LEFT)
        pdf.cell(self._USABLE_W, 6, "ACTION TAKEN (check all that apply):", ln=True)
        pdf.ln(1)

        actions = [
            "No Action Required",
            "Referred for Inspection",
            "Escalated to Supervisor",
            "Detained",
            "Seized / Forfeited",
            "Released Under Bond",
        ]
        pdf.set_font("Helvetica", "", 9)
        box_w = (self._USABLE_W - 6) / 3
        for i, action in enumerate(actions):
            col  = i % 3
            bx   = self._MARGIN_LEFT + col * (box_w + 3)
            by   = pdf.get_y()
            # Checkbox square (5 × 5 mm)
            pdf.set_draw_color(*_C_NEAR_BLACK)
            pdf.set_line_width(0.5)
            pdf.rect(bx, by, 5, 5)
            pdf.set_text_color(*_C_NEAR_BLACK)
            pdf.set_xy(bx + 7, by)
            pdf.cell(box_w - 8, 5, action, ln=False)
            if col == 2 or i == len(actions) - 1:
                pdf.ln(9)

        pdf.ln(3)

        # Notes / Remarks ruled lines
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*_C_NEAR_BLACK)
        pdf.set_x(self._MARGIN_LEFT)
        pdf.cell(self._USABLE_W, 6, "NOTES / REMARKS:", ln=True)
        pdf.ln(2)

        pdf.set_draw_color(*_C_RULE_GRAY)
        pdf.set_line_width(0.4)
        for _ in range(5):
            if pdf.get_y() + 10 > 272:
                break
            y_line = pdf.get_y() + 8
            pdf.line(self._MARGIN_LEFT, y_line,
                     self._MARGIN_LEFT + self._USABLE_W, y_line)
            pdf.ln(10)

    def _section_disclaimer(self) -> None:
        """Legal disclaimer block on the last page."""
        pdf = self._pdf
        # Ensure at least 28 mm for disclaimer; otherwise push to next page
        if pdf.get_y() > 225:
            pdf.add_page()

        pdf.ln(4)
        self._horizontal_rule(weight=0.6)
        pdf.ln(3)

        # "LEGAL DISCLAIMER" label
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*_C_NEAR_BLACK)
        pdf.set_x(self._MARGIN_LEFT)
        pdf.cell(self._USABLE_W, 5, "LEGAL DISCLAIMER", ln=True)
        pdf.ln(1)

        disclaimer = (
            "This report was generated by PortGuard, an automated customs "
            "compliance screening system. Findings are based on automated rule "
            "evaluation and statistical pattern analysis and are advisory in nature "
            "only. This report does not constitute a final determination of "
            "admissibility, legal compliance, or enforcement action. All enforcement "
            "decisions must be made by a qualified U.S. Customs and Border Protection "
            "(CBP) officer in accordance with applicable federal law, including but not "
            "limited to 19 U.S.C. 1499 and 19 CFR Parts 141-163. Automated findings "
            "must be independently verified by a licensed customs broker or attorney "
            "before any enforcement action is taken. PortGuard is not a government "
            "system; results do not have the force of law."
        )
        pdf.set_font("Helvetica", "I", 7.5)
        pdf.set_text_color(*_C_MID_GRAY)
        pdf.set_x(self._MARGIN_LEFT)
        pdf.multi_cell(self._USABLE_W, 4.5, self._t(disclaimer), ln=True)

        pdf.ln(3)
        pdf.set_font("Helvetica", "", 6.5)
        pdf.set_text_color(160, 170, 185)
        pdf.set_x(self._MARGIN_LEFT)
        pdf.cell(
            self._USABLE_W, 4,
            f"Report ID: {self._report_id}  |  Generated: {self._generated_at}  "
            "|  Confidential - Not for Public Release",
            align="C",
            ln=True,
        )
        pdf.set_text_color(*_C_NEAR_BLACK)

    # ------------------------------------------------------------------
    # Layout primitives
    # ------------------------------------------------------------------

    @staticmethod
    def _t(text: str) -> str:
        """Sanitise text for Helvetica (Latin-1) rendering.

        Replaces the most common Unicode symbols with safe ASCII/Latin-1
        equivalents, then encodes with ``errors='replace'`` to catch anything
        that slipped through.
        """
        replacements = {
            "\u2014": "-",    # em dash
            "\u2013": "-",    # en dash
            "\u2018": "'",    # left single quote
            "\u2019": "'",    # right single quote
            "\u201c": '"',    # left double quote
            "\u201d": '"',    # right double quote
            "\u2022": "*",    # bullet
            "\u00a0": " ",    # non-breaking space
            "\u2026": "...",  # ellipsis
            "\u00ae": "(R)",  # registered trademark
            "\u2122": "(TM)", # trademark
        }
        for src, dst in replacements.items():
            text = text.replace(src, dst)
        return text.encode("latin-1", errors="replace").decode("latin-1")

    def _section_heading(self, title: str) -> None:
        """Bold section heading with a full-width rule below.

        Adds a new page automatically if fewer than 25 mm remain.
        """
        pdf = self._pdf
        pdf.ln(2)
        if pdf.get_y() > 252:
            pdf.add_page()

        # Heading text
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*_C_NEAR_BLACK)
        pdf.set_x(self._MARGIN_LEFT)
        pdf.cell(self._USABLE_W, 6, title, ln=True)

        # Rule with slightly heavier weight than body rules
        self._horizontal_rule(weight=0.6)
        pdf.ln(2)

    def _horizontal_rule(self, weight: float = 0.3) -> None:
        """Draw a full-width horizontal rule at the current cursor y."""
        pdf = self._pdf
        pdf.set_draw_color(*_C_RULE_GRAY)
        pdf.set_line_width(weight)
        y = pdf.get_y()
        pdf.line(self._MARGIN_LEFT, y,
                 self._MARGIN_LEFT + self._USABLE_W, y)
        pdf.ln(1)
        pdf.set_line_width(0.2)

    def _two_col_table(self, rows: list[tuple[str, str]]) -> None:
        """Label / value table with alternating row tints and multi-line wrap.

        Each row adapts its height to the value length — long values (addresses,
        commodity descriptions) wrap to multiple lines and the alternating
        background rect is drawn at the correct expanded height so it always
        fully covers the text.
        """
        pdf      = self._pdf
        label_w  = 52.0
        value_w  = self._USABLE_W - label_w
        line_h   = 5.0
        # Rough estimate of characters per line in the value column at 8.5pt
        chars_per_line = max(1, int(value_w / 2.05))

        for i, (label, value) in enumerate(rows):
            value_str = self._t(str(value) if value is not None else "-")

            # Estimate number of wrapped lines
            num_lines = max(1, math.ceil(len(value_str) / chars_per_line))
            row_h     = max(6.5, line_h * num_lines + 1.5)

            if pdf.get_y() + row_h > 272:
                pdf.add_page()

            y = pdf.get_y()
            x = self._MARGIN_LEFT

            # Alternating background
            if i % 2 == 1:
                pdf.set_fill_color(*_C_ROW_ALT)
                pdf.rect(x, y, self._USABLE_W, row_h, style="F")

            # Label (vertically centred, bold, muted colour)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*_C_LABEL_GRAY)
            pdf.set_xy(x + 1, y + (row_h - 5) / 2)
            pdf.cell(label_w - 2, 5, self._t(label), ln=False)

            # Value — multi_cell for wrapping
            pdf.set_font("Helvetica", "", 8.5)
            pdf.set_text_color(*_C_NEAR_BLACK)
            pdf.set_xy(x + label_w, y + 1)
            pdf.multi_cell(value_w - 1, line_h, value_str, ln=False)

            # Advance cursor to the bottom of the row
            pdf.set_xy(x, y + row_h)

        pdf.ln(2)
        pdf.set_text_color(*_C_NEAR_BLACK)

    def _table_header_row(
        self,
        headers: list[str],
        col_widths: list[float],
    ) -> None:
        """Dark-navy header row for multi-column tables."""
        pdf     = self._pdf
        x       = self._MARGIN_LEFT
        y       = pdf.get_y()
        total_w = sum(col_widths)

        pdf.set_fill_color(*_C_TABLE_HEADER)
        pdf.rect(x, y, total_w, 8, style="F")

        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*_C_WHITE)
        pdf.set_xy(x, y)
        for header, w in zip(headers, col_widths):
            pdf.cell(w, 8, f"  {header}", ln=False)
        pdf.ln(8)
        pdf.set_text_color(*_C_NEAR_BLACK)

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt_country(sd: dict) -> str:
        name = sd.get("origin_country")
        iso2 = sd.get("origin_country_iso2")
        if name and iso2:
            return f"{name} ({iso2})"
        return name or iso2 or "-"

    @staticmethod
    def _fmt_value(sd: dict) -> str:
        val      = sd.get("declared_value")
        currency = sd.get("declared_currency") or "USD"
        if not val:
            return "-"
        try:
            return f"{currency} {float(val):,.2f}"
        except (ValueError, TypeError):
            return f"{currency} {val}"


# ---------------------------------------------------------------------------
# ReportGenerationError
# ---------------------------------------------------------------------------

class ReportGenerationError(Exception):
    """Raised when the PDF generator encounters a fatal error."""


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

def generate_report_from_dict(
    payload: dict,
    report_id: Optional[str] = None,
    generated_at: Optional[str] = None,
) -> bytes:
    """Generate a PDF from a parsed AnalyzeResponse dict.

    Parameters
    ----------
    payload:
        ``dict`` matching ``AnalyzeResponse.model_dump()``.
    report_id:
        Optional UUID override; auto-generated when omitted.
    generated_at:
        Optional timestamp override; current UTC time when omitted.

    Returns
    -------
    bytes
        Raw PDF binary content.

    Raises
    ------
    ReportGenerationError
        When the PDF cannot be assembled.
    """
    gen = ReportGenerator(payload, report_id=report_id, generated_at=generated_at)
    return gen.build()


def generate_report_from_payload(
    payload_json: str,
    report_id: Optional[str] = None,
    generated_at: Optional[str] = None,
) -> bytes:
    """Generate a PDF from a raw JSON string (as stored in the DB).

    Parameters
    ----------
    payload_json:
        JSON string of the serialised ``AnalyzeResponse``.
    report_id:
        Optional UUID override; auto-generated when omitted.
    generated_at:
        Optional timestamp override; current UTC time when omitted.

    Returns
    -------
    bytes
        Raw PDF binary content.

    Raises
    ------
    ReportGenerationError
        When JSON parsing or PDF assembly fails.
    """
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise ReportGenerationError(f"Invalid payload JSON: {exc}") from exc
    return generate_report_from_dict(payload, report_id=report_id, generated_at=generated_at)
