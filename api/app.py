"""
PORTGUARD Analyze API
POST /api/v1/analyze  — stateless document screening (rule-based, no external API)
GET  /api/v1/health   — liveness check
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from portguard.auth import get_current_organization
from api.auth_routes import router as auth_router

from api.document_parser import (
    extract_text,
    DocumentParserError,
    ScannedPDFError,
    PasswordProtectedPDFError,
    CorruptPDFError,
    FileSizeError,
    PageLimitError,
    UnsupportedFormatError,
)

from portguard.document_validator import (
    validate_documents as _validate_documents,
    build_rejection_error,
)

from portguard.data.sanctions import get_sanctions_programs
from portguard.data.section301 import get_section_301
from portguard.data.adcvd import get_adcvd_orders
from portguard.report_generator import (
    generate_report_from_dict,
    generate_report_from_payload,
    ReportGenerationError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pattern learning — module-level singleton (best-effort; never crashes app)
# ---------------------------------------------------------------------------
# Enabled by default.  Set PORTGUARD_PATTERN_LEARNING_ENABLED=false to disable.
# DB path is controlled by PORTGUARD_PATTERN_DB_PATH (default: portguard_patterns.db
# in the process working directory).

_pattern_db = None
_pattern_engine = None

if os.getenv("PORTGUARD_PATTERN_LEARNING_ENABLED", "true").lower() not in ("0", "false", "no"):
    try:
        from portguard.pattern_db import PatternDB
        from portguard.pattern_engine import PatternEngine
        _db_path = os.getenv("PORTGUARD_PATTERN_DB_PATH", "portguard_patterns.db")
        _pattern_db = PatternDB(_db_path)
        _pattern_engine = PatternEngine(_pattern_db)
        logger.info("Pattern learning initialized at %s", _db_path)
    except Exception as _exc:
        logger.warning(
            "Pattern learning init failed (%s) — running rule-only mode", _exc
        )

# ---------------------------------------------------------------------------
# Dashboard analytics — module-level singleton (best-effort; never crashes app)
# ---------------------------------------------------------------------------
# Shares the same db_path as the pattern learning DB.  Opens a separate
# read-only connection so analytics queries never contend with PatternDB writes.

_dashboard_analytics = None

try:
    from portguard.analytics import DashboardAnalytics as _DashboardAnalytics
    _analytics_db_path = os.getenv("PORTGUARD_PATTERN_DB_PATH", "portguard_patterns.db")
    _dashboard_analytics = _DashboardAnalytics(_analytics_db_path)
    logger.info("DashboardAnalytics initialized at %s", _analytics_db_path)
except Exception as _exc:
    logger.warning(
        "DashboardAnalytics init failed (%s) — dashboard endpoints will return 503", _exc
    )

# Blend weights (task spec: rule 65%, pattern 35%)
_RULE_WEIGHT = 0.65
_PATTERN_WEIGHT = 0.35

# ---------------------------------------------------------------------------
# Country normalization
# ---------------------------------------------------------------------------

_COUNTRY_MAP: dict[str, tuple[str, str]] = {
    # keyword (lower) -> (display name, ISO2)
    "china": ("China", "CN"),
    "prc": ("China", "CN"),
    "people's republic of china": ("China", "CN"),
    "mainland china": ("China", "CN"),
    "vietnam": ("Vietnam", "VN"),
    "viet nam": ("Vietnam", "VN"),
    "bangladesh": ("Bangladesh", "BD"),
    "malaysia": ("Malaysia", "MY"),
    "singapore": ("Singapore", "SG"),
    "south korea": ("South Korea", "KR"),
    "republic of korea": ("South Korea", "KR"),
    "taiwan": ("Taiwan", "TW"),
    "chinese taipei": ("Taiwan", "TW"),
    "india": ("India", "IN"),
    "thailand": ("Thailand", "TH"),
    "indonesia": ("Indonesia", "ID"),
    "cambodia": ("Cambodia", "KH"),
    "germany": ("Germany", "DE"),
    "mexico": ("Mexico", "MX"),
    "iran": ("Iran", "IR"),
    "islamic republic of iran": ("Iran", "IR"),
    "cuba": ("Cuba", "CU"),
    "north korea": ("North Korea", "KP"),
    "dprk": ("North Korea", "KP"),
    "syria": ("Syria", "SY"),
    "russia": ("Russia", "RU"),
    "russian federation": ("Russia", "RU"),
    "belarus": ("Belarus", "BY"),
    "venezuela": ("Venezuela", "VE"),
    "myanmar": ("Myanmar", "MM"),
    "burma": ("Myanmar", "MM"),
    "united states": ("United States", "US"),
    "usa": ("United States", "US"),
    "japan": ("Japan", "JP"),
    "hong kong": ("Hong Kong", "HK"),
    "philippines": ("Philippines", "PH"),
    "pakistan": ("Pakistan", "PK"),
    "sri lanka": ("Sri Lanka", "LK"),
    "turkey": ("Turkey", "TR"),
    "united kingdom": ("United Kingdom", "GB"),
    "great britain": ("United Kingdom", "GB"),
    "france": ("France", "FR"),
    "netherlands": ("Netherlands", "NL"),
    "italy": ("Italy", "IT"),
    "brazil": ("Brazil", "BR"),
    "canada": ("Canada", "CA"),
    "australia": ("Australia", "AU"),
}

# Port / city name -> ISO2 for transshipment detection
_PORT_ISO2: dict[str, str] = {
    "yantian": "CN", "shenzhen": "CN", "guangzhou": "CN", "guangdong": "CN",
    "shanghai": "CN", "ningbo": "CN", "qingdao": "CN", "tianjin": "CN",
    "dalian": "CN", "xiamen": "CN", "zhuhai": "CN", "nansha": "CN",
    "ho chi minh": "VN", "hcmc": "VN", "cat lai": "VN", "hai phong": "VN",
    "hanoi": "VN",
    "chittagong": "BD", "dhaka": "BD",
    "port klang": "MY", "klang": "MY", "penang": "MY",
    "singapore": "SG",
    "nhava sheva": "IN", "mundra": "IN", "mumbai": "IN", "chennai": "IN",
    "busan": "KR", "incheon": "KR",
    "hong kong": "HK",
    "kaohsiung": "TW",
    "los angeles": "US", "long beach": "US", "new york": "US",
    "miami": "US", "seattle": "US", "savannah": "US",
    "rotterdam": "NL", "hamburg": "DE", "antwerp": "BE",
}

# Commodity unit-value thresholds for undervaluation detection
# (keywords, min_plausible_unit_value_usd, label)
_VALUE_BENCHMARKS: list[tuple[set[str], float, str]] = [
    ({"integrated circuit", "ic", "semiconductor", "microchip", "monolithic",
      "memory chip", "memory type"},
     2.50, "semiconductor ICs typically $3-12/unit"),
    ({"laptop", "notebook", "portable computer"},
     100.0, "laptops typically $200-1200/unit"),
    ({"smartphone", "mobile phone", "cell phone"},
     60.0, "smartphones typically $100-800/unit"),
    ({"solar panel", "photovoltaic", "pv module"},
     20.0, "solar panels typically $30-200/unit"),
    ({"flat screen", "lcd monitor", "oled display"},
     30.0, "displays typically $50-500/unit"),
]

# HTS chapters that require FDA Prior Notice (food products)
_FOOD_HTS_CHAPTERS = {
    "01", "02", "03", "04", "07", "08", "09", "10", "11", "12",
    "15", "16", "17", "18", "19", "20", "21", "22",
}

_FOOD_KEYWORDS = {
    "shrimp", "prawn", "seafood", "fish", "salmon", "tuna", "crab",
    "lobster", "meat", "beef", "pork", "chicken", "poultry", "lamb",
    "vegetable", "fruit", "grain", "rice", "wheat", "flour", "dairy",
    "milk", "cheese", "egg", "frozen", "food", "edible",
}


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------

def _resolve_country(text: str) -> tuple[str | None, str | None]:
    """Return (display_name, ISO2) from free text, longest match wins."""
    t = text.strip().lower().rstrip(".,;:()")
    for key, (name, iso2) in sorted(_COUNTRY_MAP.items(), key=lambda x: -len(x[0])):
        if key == t or key in t:
            return name, iso2
    return None, None


def _extract_field(text: str, labels: list[str]) -> str | None:
    """Return first non-empty value following any of the given labels."""
    for label in labels:
        m = re.search(
            rf'(?:^|\n)\s*{re.escape(label)}\s*[:/]?\s*([^\n]+)',
            text, re.I | re.M,
        )
        if m:
            val = m.group(1).strip().rstrip(".,;")
            if val and not re.match(
                r'^(same|see above|n/a|none|not provided|to order|\[)', val, re.I
            ) and len(val) > 1:
                return val
    return None


def _extract_hts_codes(text: str) -> list[str]:
    """Find all HTS/HS codes in text."""
    codes: list[str] = []
    seen: set[str] = set()
    # 10-digit first (most specific)
    for m in re.finditer(r'\b(\d{4}\.\d{2}\.\d{4})\b', text):
        c = m.group(1)
        if c not in seen:
            codes.append(c)
            seen.add(c)
    # 8-digit
    for m in re.finditer(r'\b(\d{4}\.\d{2}\.\d{2})\b', text):
        c = m.group(1)
        if c not in seen and not any(x.startswith(c) for x in seen):
            codes.append(c)
            seen.add(c)
    # Explicit "HTS Code: XXXX.XX" labels (6-digit minimum)
    for m in re.finditer(r'(?:HTS|HS)\s*(?:Code)?[:\s]+([\d.]{6,})', text, re.I):
        c = m.group(1).strip()
        if c not in seen and re.match(r'^\d{4}\.\d{2}', c):
            codes.append(c)
            seen.add(c)
    return codes


def _extract_value(text: str) -> tuple[str | None, str | None]:
    """Extract declared total value and currency."""
    patterns = [
        r'(?:TOTAL\s+INVOICE\s+VALUE|INVOICE\s+VALUE|TOTAL\s+VALUE|GRAND\s+TOTAL|'
        r'TOTAL\s+AMOUNT)[:\s]*(?:[A-Z]{3})?\s*\$?\s*([\d,]+(?:\.\d+)?)',
        r'TOTAL[:\s]+(?:[A-Z]{3}\s*)?\$?([\d,]+(?:\.\d+)?)',
        r'(?:USD|EUR|GBP|CNY)\s+([\d,]+(?:\.\d+)?)',
        r'\$([\d,]+(?:\.\d+)?)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            raw = m.group(1).replace(",", "")
            try:
                val = float(raw)
                if val > 0:
                    # Find currency in the vicinity
                    window = text[max(0, m.start() - 30): m.end() + 20]
                    cm = re.search(r'\b(USD|EUR|GBP|CNY|JPY|AUD|CAD)\b', window)
                    currency = cm.group(1) if cm else "USD"
                    return raw, currency
            except ValueError:
                pass
    return None, None


def _extract_quantity(text: str) -> str | None:
    for pat in [
        r'TOTAL\s+UNITS?[:\s]+([\d,]+)',
        r'(?:QUANTITY|QTY|TOTAL\s+QTY)[:\s]+([\d,]+)\s*(?:pcs|units?|pieces?|cartons?)?',
        r'([\d,]+)\s+(?:pcs|units?|pieces?)\s+(?:total|net)',
    ]:
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1).replace(",", "")
    return None


def _extract_weight(text: str) -> str | None:
    m = re.search(r'(?:GROSS\s+WEIGHT|GW)[:\s]*([\d,]+\.?\d*)\s*(KG|LBS?|MT)?', text, re.I)
    return f"{m.group(1)} {m.group(2) or 'KG'}" if m else None


def _infer_port_country(port_text: str) -> str | None:
    t = port_text.lower()
    for port, iso2 in sorted(_PORT_ISO2.items(), key=lambda x: -len(x[0])):
        if port in t:
            return iso2
    return None


def _names_differ(a: str, b: str) -> bool:
    """True if two party names appear to represent different entities."""
    def tokens(s: str) -> set[str]:
        s = re.sub(r'\b(ltd|co|llc|inc|corp|limited|pte|sa|gmbh|company|trading|group|'
                   r'exports?|imports?|international|global)\b\.?', ' ', s.lower())
        return {w for w in re.findall(r'\b[a-z]{4,}\b', s)}
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    overlap = ta & tb
    score = len(overlap) / max(len(ta), len(tb))
    return score < 0.35


# ---------------------------------------------------------------------------
# Shipment data extraction
# ---------------------------------------------------------------------------

def _extract_shipment_data(all_text: str) -> dict:
    importer = _extract_field(all_text, [
        "CONSIGNEE", "BUYER", "IMPORTER OF RECORD", "IMPORTER", "SOLD TO",
    ])
    exporter = _extract_field(all_text, [
        "SHIPPER / EXPORTER", "SHIPPER/EXPORTER", "SHIPPER", "EXPORTER", "SELLER", "FROM",
    ])
    # Extract first non-empty multi-line exporter name (stop at blank line)
    if not exporter:
        m = re.search(
            r'(?:SHIPPER|SELLER|EXPORTER)[:\s]*\n([A-Z][^\n]{3,80})', all_text, re.I
        )
        if m:
            exporter = m.group(1).strip()

    notify_m = re.search(r'NOTIFY\s+PARTY[:\s]+([^\n]+)', all_text, re.I)
    notify = notify_m.group(1).strip() if notify_m else None

    # Origin country — explicit label first
    origin_name: str | None = None
    origin_iso2: str | None = None
    for pat in [
        r'COUNTRY\s+OF\s+ORIGIN[:\s]+([^\n\[\(]+)',
        r'ORIGIN\s+COUNTRY[:\s]+([^\n]+)',
        r'MADE\s+IN[:\s]+([^\n]+)',
    ]:
        m = re.search(pat, all_text, re.I)
        if m:
            candidate = m.group(1).strip().rstrip(".,;:()")
            # Skip "NOT STATED" patterns
            if re.match(r'not\s+stat|n/?a|unknown|\[', candidate, re.I):
                break
            n, i = _resolve_country(candidate)
            if n:
                origin_name, origin_iso2 = n, i
                break

    pol_raw = _extract_field(all_text, [
        "PORT OF LOADING", "PORT OF SHIPMENT", "PLACE OF RECEIPT", "LOADING PORT",
    ])
    pod_raw = _extract_field(all_text, [
        "PORT OF DISCHARGE", "PORT OF DELIVERY", "FINAL DESTINATION",
    ])

    vessel = _extract_field(all_text, ["VESSEL / VOYAGE", "VESSEL/VOYAGE", "VESSEL"])
    if vessel:
        vessel = vessel.split("/")[0].strip()[:80]

    bl = _extract_field(all_text, ["B/L NUMBER", "B/L NO", "B/L NO.", "BILL OF LADING NUMBER"])

    # Shipment date
    ship_date: str | None = None
    for pat in [
        r'ON\s+BOARD\s+DATE[:\s]+([\d\-/]+)',
        r'DATE\s+OF\s+ISSUE[:\s]+([\d\-/]+)',
        r'DATE[:\s]+([\d]{4}-[\d]{2}-[\d]{2})',
    ]:
        m = re.search(pat, all_text, re.I)
        if m:
            ship_date = m.group(1).strip()
            break

    # Incoterms
    incoterms: str | None = None
    for term in ["EXW", "FCA", "CPT", "CIP", "DAP", "DPU", "DDP", "FAS", "FOB", "CFR", "CIF"]:
        if re.search(rf'\b{term}\b', all_text):
            incoterms = term
            break

    hts_codes = _extract_hts_codes(all_text)
    value_str, currency = _extract_value(all_text)
    qty = _extract_quantity(all_text)
    weight = _extract_weight(all_text)

    marks_m = re.search(r'MARKS?\s*(?:AND|&)?\s*NUMBERS?[:\s]+([^\n]+)', all_text, re.I)
    marks = marks_m.group(1).strip()[:80] if marks_m else None

    commodity = _extract_field(all_text, [
        "COMMODITY", "DESCRIPTION OF GOODS", "DESCRIPTION OF CARGO",
        "GOODS DESCRIPTION", "DESCRIPTION",
    ])
    if not commodity and hts_codes:
        # Try to find description near HTS code
        for code in hts_codes:
            idx = all_text.find(code)
            if idx > 0:
                before = all_text[max(0, idx - 120):idx]
                lines = [l.strip() for l in before.split("\n") if l.strip()]
                if lines:
                    commodity = lines[-1][:120]
                    break

    return {
        "importer": importer,
        "exporter": exporter,
        "consignee": importer,
        "notify_party": notify,
        "origin_country": origin_name,
        "origin_country_iso2": origin_iso2,
        "destination_country": pod_raw,
        "port_of_loading": pol_raw,
        "port_of_discharge": pod_raw,
        "port_of_entry": pod_raw,
        "vessel_or_flight": vessel,
        "bill_of_lading_number": bl,
        "shipment_date": ship_date,
        "arrival_date": None,
        "incoterms": incoterms,
        "commodity_description": commodity,
        "hts_codes_declared": hts_codes,
        "quantity": qty,
        "gross_weight": weight,
        "declared_value": value_str,
        "declared_currency": currency,
        "marks_and_numbers": marks,
    }


# ---------------------------------------------------------------------------
# Inconsistency detection
# ---------------------------------------------------------------------------

def _find_inconsistencies(documents: list) -> tuple[list[str], list[str]]:
    """Return (code_list, explanation_list) for cross-document inconsistencies."""
    codes: list[str] = []
    msgs: list[str] = []

    def is_bl(doc) -> bool:
        fn = (doc.filename or "").lower()
        return "lading" in fn or fn.startswith("bl") or "_bl" in fn or "b_l" in fn

    def is_invoice(doc) -> bool:
        fn = (doc.filename or "").lower()
        return "invoice" in fn

    # Gather per-document shipper and seller
    bl_shippers: list[tuple[str, str]] = []
    inv_sellers: list[tuple[str, str]] = []
    doc_origins: list[tuple[str, str, str]] = []

    for doc in documents:
        fn = doc.filename or "document"
        if is_bl(doc):
            s = _extract_field(doc.raw_text, ["SHIPPER / EXPORTER", "SHIPPER/EXPORTER", "SHIPPER"])
            if not s:
                m = re.search(r'(?:SHIPPER)[:\s]*\n([A-Z][^\n]{3,80})', doc.raw_text, re.I)
                if m:
                    s = m.group(1).strip()
            if s:
                bl_shippers.append((fn, s))
        if is_invoice(doc):
            s = _extract_field(doc.raw_text, ["SELLER", "EXPORTER", "FROM"])
            if not s:
                m = re.search(r'(?:SELLER|FROM)[:\s]*\n([A-Z][^\n]{3,80})', doc.raw_text, re.I)
                if m:
                    s = m.group(1).strip()
            if s:
                inv_sellers.append((fn, s))

        # Per-document origin
        for pat in [r'COUNTRY\s+OF\s+ORIGIN[:\s]+([^\n\[]+)',
                    r'ORIGIN[:\s]+([A-Za-z ]+?)(?:\n|$)']:
            om = re.search(pat, doc.raw_text, re.I)
            if om:
                candidate = om.group(1).strip().rstrip(".,;")
                if not re.match(r'not\s+stat|n/?a|\[', candidate, re.I):
                    _, iso2 = _resolve_country(candidate)
                    if iso2:
                        doc_origins.append((fn, iso2, candidate))
                        break

    # Shipper vs seller mismatch
    for b_fn, shipper in bl_shippers:
        for i_fn, seller in inv_sellers:
            if _names_differ(shipper, seller):
                codes.append("SHIPPER_SELLER_MISMATCH")
                msgs.append(
                    f"Shipper on B/L ('{shipper[:50]}') does not match seller on "
                    f"invoice ('{seller[:50]}') — possible transshipment or document fraud"
                )

    # Origin mismatch across documents
    unique_origins = {iso2 for _, iso2, _ in doc_origins}
    if len(unique_origins) > 1:
        detail = "; ".join(f"{fn}: {cand}" for fn, _, cand in doc_origins)
        codes.append("ORIGIN_MISMATCH")
        msgs.append(f"Country of origin differs across documents: {detail}")

    # Port of loading vs declared origin transshipment check
    all_text = "\n".join(doc.raw_text for doc in documents)
    pol_raw = _extract_field(all_text, [
        "PORT OF LOADING", "PORT OF SHIPMENT", "LOADING PORT",
    ])
    if pol_raw:
        pol_country = _infer_port_country(pol_raw)
        origin_iso2: str | None = None
        om = re.search(r'COUNTRY\s+OF\s+ORIGIN[:\s]+([^\n\[]+)', all_text, re.I)
        if om:
            candidate = om.group(1).strip().rstrip(".,;")
            _, origin_iso2 = _resolve_country(candidate)

        if pol_country and origin_iso2 and pol_country != origin_iso2:
            pol_name = next(
                (k.title() for k, v in _COUNTRY_MAP.items() if v[1] == pol_country), pol_country
            )
            codes.append("TRANSSHIPMENT_INDICATOR")
            msgs.append(
                f"Port of loading ({pol_raw[:60]}) is in {pol_name} ({pol_country}), "
                f"inconsistent with declared origin ({origin_iso2}) — "
                "classic transshipment indicator"
            )

    # Value/quantity inconsistency (wildly different values per doc)
    values: list[float] = []
    for doc in documents:
        vs, _ = _extract_value(doc.raw_text)
        if vs:
            try:
                values.append(float(vs))
            except ValueError:
                pass
    if len(values) >= 2:
        max_v, min_v = max(values), min(values)
        if min_v > 0 and max_v / min_v > 2.5:
            codes.append("VALUE_DISCREPANCY")
            msgs.append(
                f"Declared values differ significantly across documents "
                f"(${min_v:,.0f} vs ${max_v:,.0f})"
            )

    return codes, msgs


# ---------------------------------------------------------------------------
# Missing-field / documentation checks
# ---------------------------------------------------------------------------

def _check_missing_fields(sd: dict, all_text: str) -> list[str]:
    """Return list of explanations for critical missing fields."""
    missing: list[str] = []

    # Country of origin
    if not sd.get("origin_country"):
        # Check if explicitly stated as "NOT STATED"
        if re.search(r'COUNTRY\s+OF\s+ORIGIN[:\s]+NOT\s+STAT', all_text, re.I):
            missing.append(
                "Country of origin is explicitly 'NOT STATED' on commercial invoice "
                "(ISF element 7; required per 19 CFR 134)"
            )
        elif not re.search(r'COUNTRY\s+OF\s+ORIGIN', all_text, re.I):
            missing.append(
                "Country of origin not found in any document "
                "(required for ISF, HTS classification, and duty assessment)"
            )
        else:
            missing.append(
                "Country of origin could not be determined from document text"
            )

    # HTS codes — check for completeness
    hts_codes = sd.get("hts_codes_declared", [])
    if not hts_codes:
        missing.append(
            "No HTS/HS code declared — required for ISF (element 8), "
            "duty assessment, and CBP processing"
        )
    else:
        for code in hts_codes:
            # 6-digit only (no subheading)
            if re.match(r'^\d{4}\.\d{2}$', code):
                missing.append(
                    f"HTS code {code} is 6-digit only — a 10-digit HTSUS subheading is required "
                    "for formal entry (CBP Form 7501)"
                )

    # Importer
    if not sd.get("importer"):
        missing.append(
            "Importer/consignee name not identified — required ISF element 3/4"
        )

    # Manufacturer (ISF element 5)
    if not re.search(r'\b(manufacturer|producer|factory|plant)\b', all_text, re.I):
        if "[not specified]" in all_text.lower() or "manufacturer" in all_text.lower():
            missing.append(
                "Manufacturer/supplier not identified — required ISF element 5 "
                "(19 CFR 149.2)"
            )

    # Currency
    if sd.get("declared_value") and not re.search(r'\b(USD|EUR|GBP|CNY|JPY)\b', all_text):
        missing.append(
            "Currency not specified on invoice — only '$' symbol found; "
            "specific currency code required for CBP valuation"
        )

    # Water-damaged / illegible documents
    if re.search(r'\b(illegible|water.?damage|smudge|not.?legible)\b', all_text, re.I):
        missing.append(
            "One or more documents are water-damaged or partially illegible — "
            "CBP requires legible, complete documentation for entry processing"
        )

    # FDA Prior Notice for food imports
    commodity = (sd.get("commodity_description") or "").lower()
    hts_chapter = (hts_codes[0][:2] if hts_codes else "")
    is_food = (
        hts_chapter in _FOOD_HTS_CHAPTERS
        or any(kw in commodity for kw in _FOOD_KEYWORDS)
        or any(kw in all_text.lower()
               for kw in {"shrimp", "prawn", "frozen shrimp", "seafood", "fish fillet"})
    )
    if is_food:
        if not re.search(r'\b(FDA|Prior\s+Notice|PNSI|21\s+CFR\s+1\.27[89])\b', all_text, re.I):
            missing.append(
                "FDA Prior Notice not filed — mandatory for all imported food under "
                "21 CFR 1.279; CBP will automatically detain shipment without it"
            )
        if not re.search(r'\b(USDA|NMFS|health\s+certificate|HACCP|inspection\s+cert)\b',
                         all_text, re.I):
            missing.append(
                "USDA/NMFS inspection certificate or health certificate not referenced — "
                "required for imported seafood and aquaculture products"
            )

    # ISF completeness check
    missing_isf: list[str] = []
    if not sd.get("importer"):
        missing_isf.append("importer of record number")
    if not re.search(r'\b(importer\s+of\s+record|IOR|EIN|CBP\s+bond)\b', all_text, re.I):
        missing_isf.append("importer of record number (ISF element 3)")
    if missing_isf:
        missing.append(
            f"ISF incomplete — missing: {', '.join(missing_isf)} (19 CFR 149.2)"
        )

    return missing


# ---------------------------------------------------------------------------
# Risk assessment
# ---------------------------------------------------------------------------

def _assess_risk(sd: dict, all_text: str) -> tuple[list[dict], list[str]]:
    """
    Return (risk_factors, explanation_strings).
    Each risk_factor: {"type": str, "severity": str, "score": float}
    """
    factors: list[dict] = []
    msgs: list[str] = []

    origin_iso2 = sd.get("origin_country_iso2")
    hts_codes = sd.get("hts_codes_declared", [])
    value_str = sd.get("declared_value")
    qty_str = sd.get("quantity")

    # 1. OFAC sanctions check
    if origin_iso2:
        programs = get_sanctions_programs(origin_iso2)
        for prog in programs:
            severity = "CRITICAL" if prog.program_type == "COMPREHENSIVE" else "HIGH"
            score = 0.90 if prog.program_type == "COMPREHENSIVE" else 0.35
            factors.append({"type": "SANCTIONS", "severity": severity, "score": score})
            msgs.append(
                f"{prog.program_type} OFAC sanctions apply to {prog.country_name} ({origin_iso2}) "
                f"under {prog.program_name} ({prog.cfr_citation}). {prog.notes}"
            )

    # 2. Transshipment: check port-of-loading country vs declared origin
    pol = sd.get("port_of_loading") or ""
    pol_country = _infer_port_country(pol) if pol else None

    # Also check if shipper address country differs from declared origin
    shipper_text = (sd.get("exporter") or "").lower()
    shipper_country: str | None = None
    for city, iso2 in _PORT_ISO2.items():
        if city in shipper_text:
            shipper_country = iso2
            break

    if pol_country and origin_iso2 and pol_country != origin_iso2:
        factors.append({"type": "TRANSSHIPMENT", "severity": "HIGH", "score": 0.28})
        msgs.append(
            f"Transshipment indicator: port of loading ({pol[:60]}) is in {pol_country}, "
            f"but declared origin is {origin_iso2} — goods likely originated in {pol_country}"
        )
        # If transshipment through China, check Section 301 on actual inferred origin
        if pol_country == "CN":
            for code in hts_codes:
                entry = get_section_301(code, "CN")
                if entry:
                    factors.append({"type": "SECTION_301_TRANSSHIP", "severity": "HIGH", "score": 0.20})
                    msgs.append(
                        f"HTS {code} subject to Section 301 {entry.list_name} at {entry.rate} "
                        f"if actual origin is China (inferred from port of loading) — "
                        "transshipment to evade tariffs is a federal offense"
                    )
                    break

    elif shipper_country and origin_iso2 and shipper_country != origin_iso2:
        factors.append({"type": "TRANSSHIPMENT", "severity": "MEDIUM", "score": 0.15})
        msgs.append(
            f"Shipper address country ({shipper_country}) differs from declared origin "
            f"({origin_iso2}) — possible transshipment risk"
        )

    # 3. Section 301 (direct CN origin)
    if origin_iso2 == "CN":
        for code in hts_codes:
            entry = get_section_301(code, "CN")
            if entry:
                factors.append({"type": "SECTION_301", "severity": "HIGH", "score": 0.22})
                msgs.append(
                    f"HTS {code} subject to Section 301 {entry.list_name} additional duty "
                    f"of {entry.rate} on China-origin imports"
                )

    # 4. AD/CVD orders
    if origin_iso2:
        for code in hts_codes:
            orders = get_adcvd_orders(code, origin_iso2)
            for order in orders:
                factors.append({"type": "ADCVD", "severity": "HIGH", "score": 0.20})
                msgs.append(
                    f"Active {order.order_type} order {order.case_number}: "
                    f"{order.product_description} — rate: {order.duty_rate}"
                )

    # 5. Undervaluation check
    try:
        total_val = float(value_str) if value_str else 0.0
        qty = float(qty_str) if qty_str else 0.0
        if total_val > 0 and qty > 0:
            unit_val = total_val / qty
            commodity = (sd.get("commodity_description") or "").lower()
            for keywords, min_val, label in _VALUE_BENCHMARKS:
                if any(kw in commodity or kw in all_text.lower() for kw in keywords):
                    if unit_val < min_val * 0.40:  # less than 40% of minimum benchmark
                        factors.append({"type": "UNDERVALUATION", "severity": "HIGH", "score": 0.18})
                        msgs.append(
                            f"Declared value ${unit_val:.2f}/unit is far below market for this "
                            f"commodity type ({label}) — probable customs undervaluation"
                        )
                    elif unit_val < min_val * 0.70:
                        factors.append({"type": "UNDERVALUATION", "severity": "MEDIUM", "score": 0.10})
                        msgs.append(
                            f"Declared value ${unit_val:.2f}/unit appears low for this commodity "
                            f"({label}) — review recommended"
                        )
                    break
    except (ValueError, TypeError, ZeroDivisionError):
        pass

    # 6. Vague/suspicious B/L description
    if re.search(r'\b(general merchandise|general cargo|misc goods|sundry goods)\b',
                 all_text, re.I):
        factors.append({"type": "VAGUE_DESCRIPTION", "severity": "MEDIUM", "score": 0.08})
        msgs.append(
            "B/L contains vague cargo description ('General Merchandise' or similar) — "
            "CBP requires specific commodity descriptions; vague descriptions are a red flag"
        )

    # 7. Consignee "TO ORDER" (negotiable B/L without named consignee)
    if re.search(r'CONSIGNEE[:\s]+TO\s+ORDER', all_text, re.I):
        factors.append({"type": "NEGOTIABLE_BL", "severity": "LOW", "score": 0.04})
        msgs.append(
            "Consignee listed as 'TO ORDER' — negotiable B/L; title not yet transferred to "
            "named party at time of filing"
        )

    # 8. Russia/Belarus sectoral sanctions
    if origin_iso2 in ("RU", "BY"):
        factors.append({"type": "SECTORAL_SANCTIONS", "severity": "HIGH", "score": 0.30})
        country_name = "Russia" if origin_iso2 == "RU" else "Belarus"
        msgs.append(
            f"Sectoral OFAC/BIS sanctions apply to {country_name} — broad restrictions "
            f"on industrial goods, technology, finance, and energy sectors"
        )

    return factors, msgs


# ---------------------------------------------------------------------------
# Scoring, decision, and next steps
# ---------------------------------------------------------------------------

def _compute_score(
    risk_factors: list[dict],
    n_inconsistencies: int,
    missing: list[str],
) -> float:
    """Compute composite risk score [0.0, 1.0]."""
    score = 0.0
    for f in risk_factors:
        score += f.get("score", 0.0)
    score += min(n_inconsistencies * 0.06, 0.24)
    score += min(len(missing) * 0.06, 0.30)
    return round(min(score, 1.0), 2)


def _make_decision(
    risk_factors: list[dict],
    missing: list[str],
    inconsistencies: list[str],
    score: float,
    sd: dict,
) -> str:
    # REJECT: comprehensive OFAC sanctions
    if any(f["type"] == "SANCTIONS" and f["severity"] == "CRITICAL" for f in risk_factors):
        return "REJECT"

    # REQUEST_MORE_INFORMATION: multiple critical documentation gaps
    n_critical_missing = sum(
        1 for m in missing
        if any(kw in m.lower() for kw in
               ("fda prior notice", "country of origin", "hts", "illegible",
                "manufacturer", "isf"))
    )
    if n_critical_missing >= 3:
        return "REQUEST_MORE_INFORMATION"
    # Single very critical gap (illegible docs + missing origin + missing FDA)
    has_illegible = any("illegible" in m.lower() or "water" in m.lower() for m in missing)
    has_no_origin = not sd.get("origin_country")
    has_no_hts = not sd.get("hts_codes_declared")
    if (has_illegible and has_no_origin) or (has_illegible and has_no_hts):
        return "REQUEST_MORE_INFORMATION"

    # FLAG_FOR_INSPECTION: transshipment or high-risk indicators or high score
    has_transshipment = any(f["type"] in ("TRANSSHIPMENT", "SECTION_301_TRANSSHIP")
                            for f in risk_factors)
    has_mismatch = "SHIPPER_SELLER_MISMATCH" in inconsistencies
    has_undervaluation = any(f["type"] == "UNDERVALUATION" and f["severity"] == "HIGH"
                             for f in risk_factors)
    high_risk = any(f["severity"] in ("HIGH", "CRITICAL") for f in risk_factors)

    if has_transshipment and (has_mismatch or has_undervaluation):
        return "FLAG_FOR_INSPECTION"
    if score > 0.50 and high_risk:
        return "FLAG_FOR_INSPECTION"
    if has_transshipment or has_mismatch or has_undervaluation:
        return "FLAG_FOR_INSPECTION"

    # REVIEW_RECOMMENDED: medium risk
    if score > 0.20 or any(f["severity"] == "MEDIUM" for f in risk_factors):
        return "REVIEW_RECOMMENDED"

    return "APPROVE"


def _generate_next_steps(
    decision: str,
    risk_factors: list[dict],
    missing: list[str],
    sd: dict,
) -> list[str]:
    steps: list[str] = []

    if decision == "REJECT":
        steps.append(
            "STOP all transaction activity immediately — do not import, pay, or transfer funds."
        )
        steps.append(
            "Contact OFAC at ofac@treasury.gov and consult licensed trade counsel before "
            "taking any further action."
        )
        steps.append(
            "Return or abandon the shipment; do not accept delivery under any circumstances."
        )
        return steps

    if decision == "REQUEST_MORE_INFORMATION":
        for m in missing:
            if "fda prior notice" in m.lower():
                steps.append(
                    "File FDA Prior Notice via PNSI (Prior Notice System Interface) before "
                    "vessel arrives — CBP will automatically hold shipment without it."
                )
            elif "country of origin" in m.lower():
                steps.append(
                    "Obtain corrected commercial invoice explicitly stating country of origin "
                    "per 19 CFR 134 and ISF element 7."
                )
            elif "hts" in m.lower():
                steps.append(
                    "Obtain complete 10-digit HTSUS subheading from a licensed customs broker "
                    "before filing the formal entry."
                )
            elif "manufacturer" in m.lower():
                steps.append(
                    "Identify manufacturing facility and obtain complete contact information "
                    "for ISF element 5 (manufacturer/supplier)."
                )
            elif "illegible" in m.lower() or "water" in m.lower():
                steps.append(
                    "Request legible replacement copies of all damaged documents from shipper "
                    "before CBP processing."
                )
            elif "isf" in m.lower():
                steps.append(
                    "File complete ISF (10+2) with all required data elements at least 24 hours "
                    "before vessel departure."
                )
        if not steps:
            steps.append(
                "Resolve all documentation gaps before shipment can be processed for entry."
            )
        return steps

    if decision == "FLAG_FOR_INSPECTION":
        if any(f["type"] in ("TRANSSHIPMENT", "SECTION_301_TRANSSHIP") for f in risk_factors):
            steps.append(
                "Request CF-28 (Request for Information) for certified proof of origin from "
                "manufacturer — do not release cargo pending origin verification."
            )
            steps.append(
                "Refer to CBP for suspected transshipment investigation under 19 USC 1592."
            )
        if any(f["type"] == "UNDERVALUATION" for f in risk_factors):
            steps.append(
                "Obtain market comparable pricing data to support or revise declared value; "
                "CBP may issue a Form 28 or Form 29 for valuation verification."
            )
        if any(f["type"] in ("SECTION_301", "SECTION_301_TRANSSHIP") for f in risk_factors):
            steps.append(
                "Deposit Section 301 additional duties with CBP at time of entry; "
                "verify if any exclusion or first-sale valuation applies."
            )
        if any(f["type"] == "ADCVD" for f in risk_factors):
            steps.append(
                "Obtain producer/exporter-specific AD/CVD rate from ITA (Commerce); "
                "deposit estimated duties and obtain cash deposit or bond."
            )
        if not steps:
            steps.append(
                "Request physical or documentary inspection by CBP before cargo release."
            )
        steps.append(
            "Do not release cargo until all flagged compliance issues are resolved."
        )
        return steps

    # REVIEW_RECOMMENDED
    if any(f["type"] == "SECTION_301" for f in risk_factors):
        steps.append("Verify Section 301 duty applicability and deposit additional duties if confirmed.")
    if any(f["type"] == "ADCVD" for f in risk_factors):
        steps.append("Confirm AD/CVD order applicability and obtain correct cash deposit rate.")
    steps.append(
        "File ISF at least 24 hours prior to vessel loading if not already filed."
    )
    steps.append(
        "File standard consumption entry (CBP Form 7501) within 10 working days of arrival."
    )
    return steps or [
        "File ISF 24 hours prior to vessel loading.",
        "File standard consumption entry (CBP Form 7501).",
    ]


def _compute_confidence(sd: dict, documents: list) -> str:
    """Estimate confidence level based on data completeness."""
    score = 0
    if sd.get("origin_country"):
        score += 2
    if sd.get("hts_codes_declared"):
        score += 2
    if sd.get("declared_value"):
        score += 1
    if sd.get("importer"):
        score += 1
    if len(documents) >= 2:
        score += 1
    if len(documents) >= 3:
        score += 1
    if score >= 7:
        return "HIGH"
    if score >= 4:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Main analysis function
# ---------------------------------------------------------------------------

def _analyze_documents(documents: list) -> dict:
    """Rule-based compliance screening — no external API calls."""
    all_text = "\n\n".join(
        f"=== {doc.filename or f'Document {i+1}'} ===\n{doc.raw_text.strip()}"
        for i, doc in enumerate(documents)
    )

    sd = _extract_shipment_data(all_text)
    inconsistency_codes, inconsistency_msgs = _find_inconsistencies(documents)
    missing_msgs = _check_missing_fields(sd, all_text)
    risk_factors, risk_msgs = _assess_risk(sd, all_text)

    all_explanations = risk_msgs + inconsistency_msgs + missing_msgs

    risk_score = _compute_score(risk_factors, len(inconsistency_codes), missing_msgs)

    if risk_score <= 0.25:
        risk_level = "LOW"
    elif risk_score <= 0.50:
        risk_level = "MEDIUM"
    elif risk_score <= 0.75:
        risk_level = "HIGH"
    else:
        risk_level = "CRITICAL"

    decision = _make_decision(risk_factors, missing_msgs, inconsistency_codes, risk_score, sd)

    next_steps = _generate_next_steps(decision, risk_factors, missing_msgs, sd)

    if not all_explanations:
        all_explanations = [
            "No sanctions, Section 301, or AD/CVD exposure identified.",
            "All documents appear consistent.",
            "No critical missing fields detected.",
        ]

    return {
        "shipment_data": sd,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "decision": decision,
        "confidence": _compute_confidence(sd, documents),
        "explanations": all_explanations,
        "recommended_next_steps": next_steps,
        "inconsistencies_found": len(inconsistency_codes),
        # Internal lists exposed for pattern score re-derivation in the handlers.
        # These are prefixed with _ to signal they are implementation details,
        # not part of the public response shape.
        "_risk_factors": risk_factors,
        "_missing_msgs": missing_msgs,
        "_inconsistency_codes": inconsistency_codes,
    }


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class Document(BaseModel):
    raw_text: str = Field(..., description="Full raw text of the shipping document")
    filename: str | None = Field(None, description="Original filename, used for document type hints")


class AnalyzeRequest(BaseModel):
    documents: list[Document] = Field(..., min_length=1)


class ShipmentData(BaseModel):
    importer: str | None = None
    exporter: str | None = None
    consignee: str | None = None
    notify_party: str | None = None
    origin_country: str | None = None
    origin_country_iso2: str | None = None
    destination_country: str | None = None
    port_of_loading: str | None = None
    port_of_discharge: str | None = None
    port_of_entry: str | None = None
    vessel_or_flight: str | None = None
    bill_of_lading_number: str | None = None
    shipment_date: str | None = None
    arrival_date: str | None = None
    incoterms: str | None = None
    commodity_description: str | None = None
    hts_codes_declared: list[str] = []
    quantity: str | None = None
    gross_weight: str | None = None
    declared_value: str | None = None
    declared_currency: str | None = None
    marks_and_numbers: str | None = None


class AnalyzeResponse(BaseModel):
    status: str
    shipment_data: ShipmentData
    risk_score: float = Field(..., ge=0.0, le=1.0)
    risk_level: str
    decision: str
    confidence: str
    explanations: list[str]
    recommended_next_steps: list[str]
    inconsistencies_found: int
    documents_analyzed: int
    processing_time_seconds: float

    # Pattern learning fields — always present; defaults make them safe for
    # existing callers that don't reference them.
    shipment_id: Optional[str] = Field(
        None,
        description="ID of the recorded shipment.  Pass to POST /api/v1/feedback "
                    "to record an outcome and improve future pattern scores.",
    )
    pattern_score: Optional[float] = Field(
        None,
        ge=0.0, le=1.0,
        description="Raw pattern risk score (0–1) from the LPL engine.  None when "
                    "history is insufficient.",
    )
    history_available: bool = Field(
        False,
        description="True when the pattern engine had enough history to contribute "
                    "to the final risk score.",
    )
    pattern_signals: list[str] = Field(
        default_factory=list,
        description="Plain-English explanations from triggered pattern signals, "
                    "sorted by severity (CRITICAL first).",
    )
    pattern_history_depth: Optional[int] = Field(
        None,
        description="Number of prior analyses found for the shipper entity in the "
                    "pattern DB.  0 means this is the first time this shipper has "
                    "been seen.  None when the pattern engine is disabled.",
    )
    # Document validation fields — always present, default to empty so existing
    # callers that do not reference them are unaffected.
    validation_warnings: list[str] = Field(
        default_factory=list,
        description="Per-document validation warnings for documents that passed "
                    "with LOW confidence.  Analysis proceeded but results may be incomplete.",
    )
    document_validations: list[dict] = Field(
        default_factory=list,
        description="Validation metadata for each submitted document: detected type, "
                    "confidence tier, signal count, and verdict.",
    )


class FeedbackRequest(BaseModel):
    """Body for POST /api/v1/feedback."""
    shipment_id: str = Field(
        ...,
        description="The shipment_id returned by POST /api/v1/analyze.",
    )
    outcome: str = Field(
        ...,
        description="Officer verdict: CONFIRMED_FRAUD | CLEARED | UNRESOLVED",
    )
    officer_id: Optional[str] = Field(
        None,
        description="Optional officer identifier for audit logging.",
    )
    notes: Optional[str] = Field(
        None,
        description="Free-text notes — evidence summary, case number, etc.",
    )
    case_reference: Optional[str] = Field(
        None,
        description="Optional external case or seizure reference number.",
    )


class FeedbackResponse(BaseModel):
    """Response from POST /api/v1/feedback."""
    status: str
    shipment_id: str
    outcome: str
    message: str


# ---------------------------------------------------------------------------
# Pattern learning helpers
# ---------------------------------------------------------------------------


def _build_scoring_request(sd: dict, organization_id: str = "__system__"):
    """Build a PatternEngine ScoringRequest from a shipment-data dict.

    Parameters
    ----------
    sd:
        The ``shipment_data`` dict returned by :func:`_analyze_documents`.
    organization_id:
        Tenant scope to embed in the request so PatternEngine queries are
        correctly scoped to the authenticated organization.

    Returns
    -------
    ScoringRequest or None if the import fails.
    """
    try:
        from portguard.pattern_engine import ScoringRequest
        declared_value: Optional[float] = None
        quantity: Optional[float] = None
        try:
            declared_value = float(sd["declared_value"]) if sd.get("declared_value") else None
        except (ValueError, TypeError):
            pass
        try:
            quantity = float(sd["quantity"]) if sd.get("quantity") else None
        except (ValueError, TypeError):
            pass
        return ScoringRequest(
            organization_id=organization_id,
            shipper_name=sd.get("exporter"),
            consignee_name=sd.get("importer") or sd.get("consignee"),
            origin_iso2=sd.get("origin_country_iso2"),
            port_of_entry=sd.get("port_of_entry") or sd.get("port_of_discharge"),
            hs_codes=sd.get("hts_codes_declared") or [],
            declared_value_usd=declared_value,
            quantity=quantity,
        )
    except Exception as exc:
        logger.warning("_build_scoring_request failed: %s", exc)
        return None


def _record_shipment_bg(
    sd: dict,
    rule_score: float,
    rule_decision: str,
    rule_confidence: str,
    risk_factors: list[dict],
    pattern_result,
    final_score: float,
    final_decision: str,
    final_confidence: str,
    organization_id: str = "__system__",
    report_payload_json: Optional[str] = None,
) -> Optional[str]:
    """Write a shipment analysis snapshot to PatternDB and return the analysis_id.

    Records the analysis fingerprint, upserts entity/route profiles, and — when
    *report_payload_json* is supplied — stores the full AnalyzeResponse JSON so
    that POST /api/v1/report/generate can reconstruct the PDF from a shipment_id
    alone without requiring the client to re-send analysis data.

    All errors are caught and logged; the function always returns either an ID
    string or None so the analyze endpoint never fails due to DB issues.

    Parameters
    ----------
    sd:
        Shipment data dict from ``_extract_shipment_data``.
    rule_score, rule_decision, rule_confidence:
        Pre-pattern rule engine outputs.
    risk_factors:
        List of rule-firing dicts for the rules_fired column.
    pattern_result:
        PatternEngine result object (or None when pattern learning is off).
    final_score, final_decision, final_confidence:
        Blended outputs after the pattern overlay.
    organization_id:
        Authenticated tenant UUID.
    report_payload_json:
        Serialised AnalyzeResponse JSON to persist for PDF generation.
        When None, the report_payload column is left NULL and report
        generation will return 404 for this shipment.
    """
    if _pattern_db is None:
        return None
    try:
        from portguard.pattern_db import ShipmentFingerprint
        fp = ShipmentFingerprint(
            organization_id=organization_id,
            shipper_name=sd.get("exporter"),
            consignee_name=sd.get("importer") or sd.get("consignee"),
            origin_iso2=sd.get("origin_country_iso2"),
            port_of_entry=sd.get("port_of_entry") or sd.get("port_of_discharge"),
            hs_codes=sd.get("hts_codes_declared") or [],
            declared_value_usd=(
                float(sd["declared_value"]) if sd.get("declared_value") else None
            ),
            quantity=(
                float(sd["quantity"]) if sd.get("quantity") else None
            ),
            gross_weight_kg=None,
            incoterms=sd.get("incoterms"),
            rule_risk_score=rule_score,
            rule_decision=rule_decision,
            rule_confidence=rule_confidence,
            pattern_score=getattr(pattern_result, "pattern_score", None),
            pattern_shipper_score=(
                getattr(pattern_result, "shipper_score", None)
            ),
            pattern_consignee_score=(
                getattr(pattern_result, "consignee_score", None)
            ),
            pattern_route_score=(
                getattr(pattern_result, "route_score", None)
            ),
            pattern_value_z_score=None,
            pattern_flag_frequency=(
                getattr(pattern_result, "frequency_score", None)
            ),
            pattern_history_depth=(
                getattr(pattern_result, "history_depth", None)
            ),
            pattern_cold_start=(
                getattr(pattern_result, "is_cold_start", True)
            ),
            final_risk_score=final_score,
            final_decision=final_decision,
            final_confidence=final_confidence,
        )
        analysis_id = _pattern_db.record_shipment(fp, final_decision, risk_factors, final_confidence)
        if analysis_id and report_payload_json:
            try:
                _pattern_db.store_report_payload(analysis_id, report_payload_json, organization_id)
            except Exception as payload_exc:
                logger.warning(
                    "store_report_payload(%s) failed (non-fatal): %s", analysis_id, payload_exc
                )
        return analysis_id
    except Exception as exc:
        logger.warning("PatternDB.record_shipment() failed (non-fatal): %s", exc)
        return None


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="PORTGUARD Document Analysis API",
    description="Stateless trade compliance screening for shipping documents — rule-based engine",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Type"],
)

app.include_router(auth_router)


@app.get("/", include_in_schema=False)
@app.get("/demo", include_in_schema=False)
def serve_demo():
    demo_path = Path(__file__).parent.parent / "demo.html"
    try:
        return FileResponse(demo_path, media_type="text/html")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="demo.html not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/health")
def health():
    try:
        return {
            "status": "ok",
            "engine": "portguard-rule-based",
            "service": "portguard-analyze",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/analyze", response_model=AnalyzeResponse)
def analyze(
    request: AnalyzeRequest,
    background_tasks: BackgroundTasks,
    current_org: dict = Depends(get_current_organization),
):
    start = time.monotonic()
    org_id: str = current_org["organization_id"]

    # --- Document validation gate ---
    _val_results = _validate_documents(request.documents)
    _rejected = [r for r in _val_results if not r.is_valid]
    if _rejected:
        _filenames = [
            (doc.filename or f"Document {i+1}")
            for i, doc in enumerate(request.documents)
        ]
        _rej_filenames = [
            _filenames[i] for i, r in enumerate(_val_results) if not r.is_valid
        ]
        raise HTTPException(
            status_code=422,
            detail=build_rejection_error(_rejected, _rej_filenames, len(request.documents)),
        )
    _val_warnings = [
        f"{(doc.filename or f'Document {i+1}')}: {r.warning_message}"
        for i, (doc, r) in enumerate(zip(request.documents, _val_results))
        if r.warning_message
    ]
    _val_metadata = [r.to_dict() for r in _val_results]

    try:
        result = _analyze_documents(request.documents)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    elapsed = round(time.monotonic() - start, 3)
    sd = result.get("shipment_data", {})

    # --- Pattern learning overlay ---
    rule_score: float = result["risk_score"]
    rule_decision: str = result["decision"]
    rule_confidence: str = result["confidence"]

    pattern_result = None
    pattern_score_val: Optional[float] = None
    history_available: bool = False
    pattern_signals: list[str] = []
    pattern_history_depth_val: Optional[int] = None
    final_score: float = rule_score
    final_decision: str = rule_decision

    if _pattern_engine is not None:
        try:
            scoring_req = _build_scoring_request(sd, organization_id=org_id)
            if scoring_req is not None:
                pattern_result = _pattern_engine.score(scoring_req)
                pattern_score_val = pattern_result.pattern_score
                history_available = not pattern_result.is_cold_start
                pattern_signals = pattern_result.explanations
                pattern_history_depth_val = pattern_result.history_depth

                if history_available:
                    # Weighted blend: rule 65%, pattern 35%
                    blended = _RULE_WEIGHT * rule_score + _PATTERN_WEIGHT * pattern_score_val
                    final_score = round(min(1.0, blended), 4)
                    # Re-derive decision from the blended score so that pattern
                    # signals can push a borderline APPROVE to REVIEW_RECOMMENDED.
                    final_decision = _make_decision(
                        result.get("_risk_factors", []),
                        result.get("_missing_msgs", []),
                        result.get("_inconsistency_codes", []),
                        final_score,
                        sd,
                    )
        except Exception as exc:
            logger.warning("PatternEngine.score() failed (non-fatal): %s", exc)

    # Recompute risk level from the final (possibly blended) score
    if final_score <= 0.25:
        final_risk_level = "LOW"
    elif final_score <= 0.50:
        final_risk_level = "MEDIUM"
    elif final_score <= 0.75:
        final_risk_level = "HIGH"
    else:
        final_risk_level = "CRITICAL"

    # Build the response object first so we can serialise it for PDF storage.
    analyze_response = AnalyzeResponse(
        status="completed",
        shipment_data=ShipmentData(**sd),
        risk_score=final_score,
        risk_level=final_risk_level,
        decision=final_decision,
        confidence=rule_confidence,
        explanations=result["explanations"],
        recommended_next_steps=result["recommended_next_steps"],
        inconsistencies_found=result["inconsistencies_found"],
        documents_analyzed=len(request.documents),
        processing_time_seconds=elapsed,
        shipment_id=None,  # filled in below after DB write
        pattern_score=pattern_score_val,
        history_available=history_available,
        pattern_signals=pattern_signals,
        pattern_history_depth=pattern_history_depth_val,
        validation_warnings=_val_warnings,
        document_validations=_val_metadata,
    )

    # Record analysis to PatternDB inline (fast — < 10 ms); shipment_id is
    # needed in the response so we cannot defer it to a true background task.
    # Serialise the response for report_payload — shipment_id will be None in
    # the JSON but that is fine; the PDF generator uses the stored payload only
    # for narrative content, not the ID.
    try:
        _report_payload_json: Optional[str] = analyze_response.model_dump_json()
    except Exception:
        _report_payload_json = None

    shipment_id: Optional[str] = _record_shipment_bg(
        sd=sd,
        rule_score=rule_score,
        rule_decision=rule_decision,
        rule_confidence=rule_confidence,
        risk_factors=result.get("_risk_factors", []),
        pattern_result=pattern_result,
        final_score=final_score,
        final_decision=final_decision,
        final_confidence=rule_confidence,
        organization_id=org_id,
        report_payload_json=_report_payload_json,
    )

    analyze_response.shipment_id = shipment_id
    return analyze_response


# ---------------------------------------------------------------------------
# File upload response model
# ---------------------------------------------------------------------------

class ExtractTextResponse(BaseModel):
    """Response from POST /api/v1/extract-text."""

    text: str = Field(..., description="Extracted plain text from the uploaded file")
    filename: str = Field(..., description="Original filename as provided by the client")
    page_count: int = Field(..., description="Number of pages (always 1 for plain-text files)")
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal extraction warnings, e.g. image-only pages in a mixed PDF",
    )


# ---------------------------------------------------------------------------
# File upload endpoints
# ---------------------------------------------------------------------------


def _parser_error_to_http(exc: DocumentParserError) -> HTTPException:
    """Map a DocumentParserError to the appropriate HTTPException.

    FileSizeError and PageLimitError are client errors caused by uploading
    files that exceed enforced limits (413 / 422).  All others are 422
    because the file was received successfully but cannot be processed.
    """
    status = 413 if isinstance(exc, FileSizeError) else 422
    return HTTPException(
        status_code=status,
        detail={"code": exc.code, "message": str(exc)},
    )


@app.post("/api/v1/extract-text", response_model=ExtractTextResponse)
async def extract_text_endpoint(
    file: UploadFile = File(...),
    current_org: dict = Depends(get_current_organization),
):
    """Extract plain text from an uploaded PDF or text file.

    Returns the extracted text so the caller can review or edit it before
    submitting to /api/v1/analyze.  This two-step flow is used by the
    browser demo: the user uploads a file, sees the extracted text in the
    textarea, and can correct any extraction errors before running analysis.

    Accepts: .pdf (machine-readable), .txt, and other plain-text files.
    Rejects: scanned PDFs (no text layer), password-protected PDFs,
             corrupt PDFs, files over 10 MB, PDFs over 50 pages.
    """
    raw = await file.read()
    filename = file.filename or "upload"

    try:
        result = extract_text(raw, filename)
    except DocumentParserError as exc:
        raise _parser_error_to_http(exc)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected extraction error: {exc}")

    return ExtractTextResponse(
        text=result.text,
        filename=filename,
        page_count=result.page_count,
        warnings=result.warnings,
    )


@app.post("/api/v1/analyze-files", response_model=AnalyzeResponse)
async def analyze_files(
    files: list[UploadFile] = File(...),
    current_org: dict = Depends(get_current_organization),
):
    """Run full compliance analysis directly from uploaded files.

    Accepts 1–10 PDF or text files in a single multipart request.  Each
    file is extracted to plain text and then passed through the identical
    analysis pipeline as POST /api/v1/analyze — the response schema is
    the same.

    This endpoint is for API clients that prefer a single-step file →
    analysis flow.  The browser demo uses the two-step extract-then-analyze
    flow instead (upload to /extract-text, review text, then POST to
    /analyze).
    """
    if not files:
        raise HTTPException(status_code=422, detail="At least one file is required.")
    if len(files) > 10:
        raise HTTPException(
            status_code=422,
            detail=f"Maximum 10 files per request; {len(files)} were uploaded.",
        )

    documents: list[Document] = []
    extraction_warnings: list[str] = []

    for upload in files:
        raw = await upload.read()
        filename = upload.filename or "upload"

        try:
            result = extract_text(raw, filename)
        except DocumentParserError as exc:
            raise _parser_error_to_http(exc)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Unexpected extraction error for '{filename}': {exc}",
            )

        documents.append(Document(raw_text=result.text, filename=filename))
        for w in result.warnings:
            extraction_warnings.append(f"{filename}: {w}")

    # --- Document validation gate ---
    _val_results_f = _validate_documents(documents)
    _rejected_f = [r for r in _val_results_f if not r.is_valid]
    if _rejected_f:
        _filenames_f = [doc.filename or f"Document {i+1}" for i, doc in enumerate(documents)]
        _rej_filenames_f = [
            _filenames_f[i] for i, r in enumerate(_val_results_f) if not r.is_valid
        ]
        raise HTTPException(
            status_code=422,
            detail=build_rejection_error(_rejected_f, _rej_filenames_f, len(documents)),
        )
    _val_warnings_f = [
        f"{(doc.filename or f'Document {i+1}')}: {r.warning_message}"
        for i, (doc, r) in enumerate(zip(documents, _val_results_f))
        if r.warning_message
    ]
    _val_metadata_f = [r.to_dict() for r in _val_results_f]

    start = time.monotonic()
    try:
        result_data = _analyze_documents(documents)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    elapsed = round(time.monotonic() - start, 3)
    sd = result_data.get("shipment_data", {})

    # Surface any per-file extraction warnings as additional explanations.
    if extraction_warnings:
        result_data["explanations"] = extraction_warnings + result_data.get("explanations", [])

    # --- Pattern learning overlay (identical logic to /api/v1/analyze) ---
    rule_score: float = result_data["risk_score"]
    rule_decision: str = result_data["decision"]
    rule_confidence: str = result_data["confidence"]

    pattern_result = None
    pattern_score_val: Optional[float] = None
    history_available: bool = False
    pattern_signals: list[str] = []
    pattern_history_depth_val: Optional[int] = None
    final_score: float = rule_score
    final_decision: str = rule_decision

    org_id: str = current_org["organization_id"]

    if _pattern_engine is not None:
        try:
            scoring_req = _build_scoring_request(sd, organization_id=org_id)
            if scoring_req is not None:
                pattern_result = _pattern_engine.score(scoring_req)
                pattern_score_val = pattern_result.pattern_score
                history_available = not pattern_result.is_cold_start
                pattern_signals = pattern_result.explanations
                pattern_history_depth_val = pattern_result.history_depth
                if history_available:
                    blended = _RULE_WEIGHT * rule_score + _PATTERN_WEIGHT * pattern_score_val
                    final_score = round(min(1.0, blended), 4)
                    final_decision = _make_decision(
                        result_data.get("_risk_factors", []),
                        result_data.get("_missing_msgs", []),
                        result_data.get("_inconsistency_codes", []),
                        final_score,
                        sd,
                    )
        except Exception as exc:
            logger.warning("PatternEngine.score() failed (non-fatal): %s", exc)

    if final_score <= 0.25:
        final_risk_level = "LOW"
    elif final_score <= 0.50:
        final_risk_level = "MEDIUM"
    elif final_score <= 0.75:
        final_risk_level = "HIGH"
    else:
        final_risk_level = "CRITICAL"

    analyze_response_files = AnalyzeResponse(
        status="completed",
        shipment_data=ShipmentData(**sd),
        risk_score=final_score,
        risk_level=final_risk_level,
        decision=final_decision,
        confidence=rule_confidence,
        explanations=result_data["explanations"],
        recommended_next_steps=result_data["recommended_next_steps"],
        inconsistencies_found=result_data["inconsistencies_found"],
        documents_analyzed=len(documents),
        processing_time_seconds=elapsed,
        shipment_id=None,
        pattern_score=pattern_score_val,
        history_available=history_available,
        pattern_signals=pattern_signals,
        pattern_history_depth=pattern_history_depth_val,
        validation_warnings=_val_warnings_f,
        document_validations=_val_metadata_f,
    )

    try:
        _report_payload_json_files: Optional[str] = analyze_response_files.model_dump_json()
    except Exception:
        _report_payload_json_files = None

    shipment_id: Optional[str] = _record_shipment_bg(
        sd=sd,
        rule_score=rule_score,
        rule_decision=rule_decision,
        rule_confidence=rule_confidence,
        risk_factors=result_data.get("_risk_factors", []),
        pattern_result=pattern_result,
        final_score=final_score,
        final_decision=final_decision,
        final_confidence=rule_confidence,
        organization_id=org_id,
        report_payload_json=_report_payload_json_files,
    )

    analyze_response_files.shipment_id = shipment_id
    return analyze_response_files


# ---------------------------------------------------------------------------
# Feedback endpoint — officers close the loop
# ---------------------------------------------------------------------------


@app.post("/api/v1/feedback", response_model=FeedbackResponse)
def feedback(
    request: FeedbackRequest,
    current_org: dict = Depends(get_current_organization),
):
    """Record an officer's verdict for a previously analyzed shipment.

    This is the feedback loop that teaches the pattern learning system.
    The ``shipment_id`` is the value returned by ``POST /api/v1/analyze``.

    Outcomes:
    - ``CONFIRMED_FRAUD`` — the flag was correct; increases future risk scores
      for this shipper, consignee, and route corridor.
    - ``CLEARED`` — the flag was a false positive; reduces future false-positive
      rates for this entity and may auto-trust consistently clean shippers.
    - ``UNRESOLVED`` — investigation ongoing; stored but not yet applied to scores.

    Resolved outcomes (CONFIRMED_FRAUD or CLEARED) are immutable.
    UNRESOLVED outcomes can be updated to a resolved state.
    """
    if _pattern_db is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PATTERN_LEARNING_DISABLED",
                "message": (
                    "Pattern learning is not enabled on this instance. "
                    "Set PORTGUARD_PATTERN_LEARNING_ENABLED=true and restart."
                ),
            },
        )

    valid_outcomes = {"CONFIRMED_FRAUD", "CLEARED", "UNRESOLVED"}
    if request.outcome not in valid_outcomes:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_OUTCOME",
                "message": (
                    f"Invalid outcome '{request.outcome}'. "
                    f"Must be one of: {sorted(valid_outcomes)}"
                ),
            },
        )

    try:
        _pattern_db.record_outcome(
            analysis_id=request.shipment_id,
            outcome=request.outcome,
            officer_id=request.officer_id,
            notes=request.notes,
            case_reference=request.case_reference,
        )
    except Exception as exc:
        # Import specific exception types for precise HTTP status codes
        try:
            from portguard.pattern_db import (
                RecordNotFoundError,
                DuplicateOutcomeError,
                InvalidOutcomeError,
            )
            if isinstance(exc, RecordNotFoundError):
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "SHIPMENT_NOT_FOUND",
                        "message": f"No shipment found with id '{request.shipment_id}'.",
                    },
                )
            if isinstance(exc, DuplicateOutcomeError):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "OUTCOME_ALREADY_RECORDED",
                        "message": str(exc),
                    },
                )
            if isinstance(exc, InvalidOutcomeError):
                raise HTTPException(
                    status_code=422,
                    detail={"code": "INVALID_OUTCOME", "message": str(exc)},
                )
        except HTTPException:
            raise
        except Exception:
            pass
        raise HTTPException(
            status_code=500,
            detail={"code": "FEEDBACK_ERROR", "message": str(exc)},
        )

    outcome_messages = {
        "CONFIRMED_FRAUD": (
            "Fraud outcome recorded. Shipper, consignee, and route risk profiles "
            "have been updated. Future screenings for these entities will reflect "
            "this confirmed fraud event."
        ),
        "CLEARED": (
            "Cleared outcome recorded. Entity trust scores have been updated. "
            "Continued clear outcomes will reduce future false-positive rates "
            "for this shipper and consignee."
        ),
        "UNRESOLVED": (
            "Unresolved outcome recorded. No score changes applied yet. "
            "Submit CONFIRMED_FRAUD or CLEARED when the investigation concludes."
        ),
    }

    return FeedbackResponse(
        status="ok",
        shipment_id=request.shipment_id,
        outcome=request.outcome,
        message=outcome_messages[request.outcome],
    )


# ---------------------------------------------------------------------------
# Pattern History — read and reset
# ---------------------------------------------------------------------------


class ResetRequest(BaseModel):
    """Body for DELETE /api/v1/pattern-history/reset."""
    confirm: bool = False


class ResetResponse(BaseModel):
    success: bool
    message: str
    shipments_deleted: int


@app.delete("/api/v1/pattern-history/reset", response_model=ResetResponse)
def reset_pattern_history(
    request: ResetRequest,
    current_org: dict = Depends(get_current_organization),
):
    """Permanently delete all pattern learning data.

    Clears ``shipment_history``, ``pattern_outcomes``,
    ``shipper_profiles``, ``consignee_profiles``,
    ``route_risk_profiles``, and ``hs_code_baselines``.
    The schema_migrations table is preserved.

    The caller must include ``{"confirm": true}`` in the request body;
    omitting it or sending ``false`` returns HTTP 400.

    Returns
    -------
    ResetResponse
        ``success``, human-readable ``message``, and ``shipments_deleted``
        count for UI feedback and audit purposes.
    """
    if _pattern_db is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PATTERN_LEARNING_DISABLED",
                "message": "Pattern learning is not enabled on this instance.",
            },
        )

    if not request.confirm:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "CONFIRMATION_REQUIRED",
                "message": (
                    "Reset requires explicit confirmation. "
                    "Send {\"confirm\": true} to proceed."
                ),
            },
        )

    try:
        deleted = _pattern_db.reset(organization_id=current_org["organization_id"])
    except Exception as exc:
        logger.error("pattern_db.reset() failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"code": "RESET_ERROR", "message": str(exc)},
        )

    logger.warning(
        "PATTERN HISTORY RESET at %s — org=%s — %d shipment record(s) deleted",
        __import__("datetime").datetime.utcnow().isoformat(timespec="seconds"),
        current_org["organization_id"],
        deleted,
    )

    return ResetResponse(
        success=True,
        message="Pattern history cleared",
        shipments_deleted=deleted,
    )


@app.get("/api/v1/pattern-history")
def pattern_history(current_org: dict = Depends(get_current_organization)):
    """Return aggregate pattern learning statistics for the dashboard.

    Returns total shipments analyzed, total confirmed fraud events, and
    the top-5 riskiest shippers and routes by learned risk score.

    Returns 503 when pattern learning is disabled.
    """
    if _pattern_db is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PATTERN_LEARNING_DISABLED",
                "message": "Pattern learning is not enabled on this instance.",
            },
        )
    try:
        return _pattern_db.get_summary_stats(organization_id=current_org["organization_id"])
    except Exception as exc:
        logger.warning("get_summary_stats() failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail={"code": "STATS_ERROR", "message": str(exc)},
        )


# ---------------------------------------------------------------------------
# Dashboard analytics endpoints
# ---------------------------------------------------------------------------
# All endpoints:
#   • Require a valid JWT Bearer token (get_current_organization dependency).
#   • Return HTTP 503 when the analytics DB is unavailable (not initialized).
#   • Never return HTTP 500 for empty-data scenarios — the DashboardAnalytics
#     methods always return safe zero/empty responses in that case.
#   • Accept an optional `days` query parameter where relevant (1–365).
# ---------------------------------------------------------------------------


def _require_analytics() -> None:
    """Raise HTTP 503 if the DashboardAnalytics singleton is not available.

    Called at the top of every dashboard endpoint so the error response is
    consistent and the reason is machine-readable via the ``code`` field.
    """
    if _dashboard_analytics is None or not _dashboard_analytics.available:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "ANALYTICS_UNAVAILABLE",
                "message": (
                    "Analytics are not available on this instance. "
                    "Ensure pattern learning is enabled and the database "
                    "file is accessible, then restart the server."
                ),
            },
        )


@app.get("/api/v1/dashboard/summary")
def dashboard_summary(
    current_org: dict = Depends(get_current_organization),
):
    """Return high-level KPI metrics for the dashboard summary cards.

    Counts all shipments and officer outcomes recorded for the authenticated
    organization across all time.  Returns zeros when no history exists.

    Response fields
    ---------------
    total_shipments (int):
        Total rows in ``shipment_history`` for this organization.
    total_confirmed_fraud (int):
        Count of ``CONFIRMED_FRAUD`` outcomes submitted via
        ``POST /api/v1/feedback``.
    total_cleared (int):
        Count of ``CLEARED`` outcomes.
    total_unresolved (int):
        Count of ``UNRESOLVED`` outcomes still pending resolution.
    fraud_rate (float):
        ``confirmed_fraud / (confirmed_fraud + cleared)`` rounded to 4 dp.
        ``0.0`` when no resolved outcomes exist.
    avg_risk_score (float):
        Mean ``final_risk_score`` across all shipments, rounded to 4 dp.
        ``0.0`` when no shipments exist.
    avg_pattern_score (float | null):
        Mean ``pattern_score`` for shipments where the pattern engine
        contributed (non-cold-start).  ``null`` when none exist.
    """
    _require_analytics()
    return _dashboard_analytics.get_summary_stats(
        organization_id=current_org["organization_id"]
    )


@app.get("/api/v1/dashboard/decisions")
def dashboard_decisions(
    current_org: dict = Depends(get_current_organization),
):
    """Return the count and percentage for each decision type.

    All five decision types are always present in the response even if their
    count is zero, so the frontend can render a complete donut chart without
    needing to handle missing keys.

    Response fields
    ---------------
    total (int):
        Sum of all decision counts.
    decisions (list):
        One entry per decision type, sorted highest-count-first.
        Each entry: ``{decision, label, count, percentage}``.
        ``label`` is a short human-readable string (e.g. ``"Flag"``).
        ``percentage`` is ``count / total * 100`` rounded to 1 dp.
    """
    _require_analytics()
    return _dashboard_analytics.get_decision_breakdown(
        organization_id=current_org["organization_id"]
    )


@app.get("/api/v1/dashboard/fraud-trend")
def dashboard_fraud_trend(
    days: int = Query(default=30, ge=1, le=365, description="Rolling window in calendar days"),
    current_org: dict = Depends(get_current_organization),
):
    """Return a daily time-series of fraud counts and fraud rates.

    The series spans exactly *days* calendar days ending today.  Days with no
    recorded shipments return ``total=0, fraud_count=0, fraud_rate=0.0`` so
    the frontend always receives a continuous x-axis.

    Query parameters
    ----------------
    days (int, 1–365):
        Number of calendar days to include.  Default: 30.

    Response fields
    ---------------
    window_days (int):
        The *days* value used.
    total_in_window (int):
        Total shipments analyzed within the window.
    fraud_in_window (int):
        Total confirmed fraud events within the window.
    trend (list):
        One entry per day, oldest first.
        Each: ``{day (YYYY-MM-DD), total, fraud_count, fraud_rate}``.
    """
    _require_analytics()
    return _dashboard_analytics.get_fraud_trend(
        organization_id=current_org["organization_id"],
        days=days,
    )


@app.get("/api/v1/dashboard/top-shippers")
def dashboard_top_shippers(
    limit: int = Query(default=10, ge=1, le=50, description="Maximum number of shippers to return"),
    current_org: dict = Depends(get_current_organization),
):
    """Return the riskiest shippers ranked by Bayesian reputation score.

    Uses the live ``reputation_score`` stored in ``shipper_profiles``, which
    is updated on every ``POST /api/v1/analyze`` and ``POST /api/v1/feedback``
    call.  No recomputation is performed at query time.

    Only shippers with at least one recorded analysis are included.  Trusted
    shippers (auto-trusted or manually marked) are included with their
    ``is_trusted`` flag set so officers can audit the trust list.

    Query parameters
    ----------------
    limit (int, 1–50):
        Maximum number of shippers to return.  Default: 10.

    Response fields
    ---------------
    total_profiles (int):
        Total distinct shipper profiles for this org (regardless of limit).
    shippers (list):
        Each entry: ``{name, reputation_score, total_analyses,
        total_confirmed_fraud, total_cleared, is_trusted}``.
        Sorted by ``reputation_score`` descending (most suspicious first).
    """
    _require_analytics()
    return _dashboard_analytics.get_top_risky_shippers(
        organization_id=current_org["organization_id"],
        limit=limit,
    )


@app.get("/api/v1/dashboard/top-countries")
def dashboard_top_countries(
    limit: int = Query(default=10, ge=1, le=50, description="Maximum number of countries to return"),
    current_org: dict = Depends(get_current_organization),
):
    """Return origin countries ranked by confirmed fraud count and average risk score.

    Only shipments where ``origin_iso2`` was successfully extracted from the
    document are included.  The primary sort is confirmed fraud count (hard
    evidence); the secondary sort is average risk score (leading indicator
    for countries with few or no confirmed outcomes yet).

    Query parameters
    ----------------
    limit (int, 1–50):
        Maximum number of countries to return.  Default: 10.

    Response fields
    ---------------
    total_countries (int):
        Number of distinct origin countries seen for this org.
    countries (list):
        Each entry: ``{iso2, country_name, total_shipments,
        confirmed_fraud_count, avg_risk_score, fraud_rate}``.
        Sorted by ``confirmed_fraud_count`` then ``avg_risk_score`` descending.
    """
    _require_analytics()
    return _dashboard_analytics.get_top_risky_countries(
        organization_id=current_org["organization_id"],
        limit=limit,
    )


@app.get("/api/v1/dashboard/top-hs-codes")
def dashboard_top_hs_codes(
    limit: int = Query(default=10, ge=1, le=50, description="Maximum number of HS chapters to return"),
    current_org: dict = Depends(get_current_organization),
):
    """Return HTS chapters ranked by flagged shipment count and flag rate.

    Groups shipments by ``hs_chapter_primary`` (the 2-digit HTS chapter of
    the primary declared HTS code).  A shipment is "flagged" when its
    ``final_decision`` is anything other than ``APPROVE``.

    Shipments where no HTS code was extracted from the document
    (``hs_chapter_primary IS NULL``) are excluded.

    Query parameters
    ----------------
    limit (int, 1–50):
        Maximum number of chapters to return.  Default: 10.

    Response fields
    ---------------
    total_chapters (int):
        Number of distinct HTS chapters seen for this org.
    hs_codes (list):
        Each entry: ``{chapter, label, total_shipments, flagged_count,
        flag_rate, avg_risk_score}``.
        Sorted by ``flagged_count`` then ``avg_risk_score`` descending.
        ``label`` maps the 2-digit chapter to a human-readable description
        (e.g. ``"Ch.85 — Electronics"``).
    """
    _require_analytics()
    return _dashboard_analytics.get_top_flagged_hs_codes(
        organization_id=current_org["organization_id"],
        limit=limit,
    )


@app.get("/api/v1/dashboard/recent-activity")
def dashboard_recent_activity(
    limit: int = Query(default=20, ge=1, le=100, description="Maximum number of rows to return"),
    current_org: dict = Depends(get_current_organization),
):
    """Return the most recently analyzed shipments as an activity feed.

    Each entry carries enough data to render one row in the activity feed:
    timestamp, shipper name, origin country, decision, risk score, and the
    resolved officer verdict if one has been submitted.

    Results are ordered by ``analyzed_at`` descending (newest first).
    The ``outcome`` field is ``null`` when no feedback has been submitted
    for a shipment.

    Query parameters
    ----------------
    limit (int, 1–100):
        Maximum number of rows to return.  Default: 20.

    Response fields
    ---------------
    total_shown (int):
        Number of entries in *activity* (≤ *limit*).
    activity (list):
        Each entry: ``{analysis_id, analyzed_at, shipper_name, origin_iso2,
        final_decision, final_risk_score, pattern_cold_start, outcome,
        officer_id}``.
        ``outcome`` is one of ``"CONFIRMED_FRAUD"``, ``"CLEARED"``,
        ``"UNRESOLVED"``, or ``null`` (no feedback submitted yet).
    """
    _require_analytics()
    return _dashboard_analytics.get_recent_activity(
        organization_id=current_org["organization_id"],
        limit=limit,
    )


# ---------------------------------------------------------------------------
# PDF report generation endpoints
# ---------------------------------------------------------------------------
# Two complementary paths:
#
#   POST /api/v1/report/generate
#     → Retrieves the stored report_payload from shipment_history by
#       shipment_id and generates the PDF server-side.  Used when the
#       officer downloads the report at any point after analysis.
#
#   POST /api/v1/report/generate-direct
#     → Accepts a full AnalyzeResponse JSON body (as returned by
#       /api/v1/analyze) and generates the PDF immediately.  Used for
#       the instant-download button shown right after analysis completes,
#       before the user navigates away or refreshes.
# ---------------------------------------------------------------------------


class ReportRequest(BaseModel):
    """Body for POST /api/v1/report/generate."""

    shipment_id: str = Field(
        ...,
        description=(
            "The shipment_id returned by POST /api/v1/analyze.  "
            "The stored report_payload for this shipment is fetched from the "
            "database and used to render the PDF."
        ),
    )


def _pdf_response(pdf_bytes: bytes, shipment_id: str) -> Response:
    """Wrap PDF bytes in a streaming Response with correct headers.

    Sets ``Content-Type: application/pdf`` and a ``Content-Disposition``
    header that causes browsers to trigger a file-save dialog with a
    human-readable filename derived from the shipment ID and today's date.

    Parameters
    ----------
    pdf_bytes:
        Raw PDF binary content from :func:`generate_report_from_payload`
        or :func:`generate_report_from_dict`.
    shipment_id:
        Used in the suggested filename.  Only the first 8 characters are
        included to keep the filename short.

    Returns
    -------
    fastapi.responses.Response
        HTTP 200 with the PDF as the body and appropriate headers.
    """
    import datetime
    date_str     = datetime.date.today().strftime("%Y%m%d")
    short_id     = (shipment_id or "unknown")[:8]
    filename     = f"PortGuard_Report_{short_id}_{date_str}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Report-Shipment-Id": shipment_id or "",
        },
    )


@app.post("/api/v1/report/generate")
def report_generate(
    request: ReportRequest,
    current_org: dict = Depends(get_current_organization),
):
    """Generate and download a PDF compliance report for a stored shipment.

    Fetches the serialised analysis payload from ``shipment_history`` using
    the supplied *shipment_id* and renders a complete multi-page PDF report.

    The report includes:
    - Report metadata (unique report ID, generation timestamp, classification)
    - Full shipment summary table
    - Colour-coded final decision banner
    - Risk score with visual progress bar
    - All rule violations with severity classification
    - Compliance screening grid (OFAC / Section 301 / AD/CVD / UFLPA / ISF / PGA)
    - Pattern intelligence findings (when history is available)
    - Recommended next steps
    - Officer review / signature section
    - Legal disclaimer

    Prerequisites
    -------------
    The shipment must have been analyzed **after** migration 004 was applied
    (i.e. the ``report_payload`` column was backfilled at analysis time).
    Shipments analyzed before the migration return HTTP 404 — re-analyze to
    generate a fresh record.

    Parameters
    ----------
    request.shipment_id:
        The ``shipment_id`` value returned by ``POST /api/v1/analyze``.

    Returns
    -------
    application/pdf
        Binary PDF file.
        ``Content-Disposition: attachment; filename="PortGuard_Report_<id>_<date>.pdf"``

    Raises
    ------
    HTTP 404:
        Shipment not found, belongs to a different organization, or was
        analyzed before the report payload feature was introduced.
    HTTP 503:
        Pattern learning / database is not available on this instance.
    HTTP 500:
        PDF generation failed (caught ``ReportGenerationError``).
    """
    if _pattern_db is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PATTERN_LEARNING_DISABLED",
                "message": (
                    "Report generation requires the pattern learning database. "
                    "Set PORTGUARD_PATTERN_LEARNING_ENABLED=true and restart."
                ),
            },
        )

    org_id = current_org["organization_id"]

    # Fetch stored payload
    try:
        payload_json = _pattern_db.get_report_payload(
            analysis_id=request.shipment_id,
            organization_id=org_id,
        )
    except Exception as exc:
        logger.error(
            "get_report_payload(%s) raised unexpectedly: %s",
            request.shipment_id, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "DB_ERROR",
                "message": "Failed to retrieve report data from the database.",
            },
        )

    if payload_json is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "REPORT_NOT_AVAILABLE",
                "message": (
                    f"No report payload found for shipment '{request.shipment_id}'. "
                    "The shipment may not exist, belong to a different organization, "
                    "or have been analyzed before the PDF report feature was introduced. "
                    "Re-analyze the shipment to generate a storable report."
                ),
            },
        )

    # Generate PDF — inject shipment_id if the stored payload was captured before it was assigned
    import json as _json
    payload_dict = _json.loads(payload_json)
    if not payload_dict.get("shipment_id"):
        payload_dict["shipment_id"] = request.shipment_id
    try:
        pdf_bytes = generate_report_from_dict(payload_dict)
    except (ValueError, ReportGenerationError) as exc:
        logger.error(
            "PDF generation failed for shipment %s: %s",
            request.shipment_id, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "PDF_GENERATION_FAILED",
                "message": f"Failed to generate PDF report: {exc}",
            },
        )

    return _pdf_response(pdf_bytes, request.shipment_id)


@app.post("/api/v1/report/generate-direct")
def report_generate_direct(
    payload: dict,
    current_org: dict = Depends(get_current_organization),
):
    """Generate and download a PDF report directly from an analysis result payload.

    This endpoint accepts the full ``AnalyzeResponse`` dict (as returned by
    ``POST /api/v1/analyze`` and serialised to JSON) in the request body and
    renders a PDF without any database lookup.

    Use this endpoint for the **immediate-download** flow: when the officer
    clicks "Download PDF Report" directly after viewing an analysis result in
    the browser, the frontend already has the full analysis payload in memory
    and can POST it here without needing a stored ``shipment_id``.

    This endpoint is also useful for:
    - Generating reports for analyses that pre-date the ``report_payload``
      storage feature (migration 004), as long as the caller has retained the
      original response payload.
    - Integration testing without database state.
    - Programmatic API clients that analyze → immediately download without a
      second round-trip.

    Parameters
    ----------
    payload:
        Full ``AnalyzeResponse`` JSON object (any subset of fields works —
        missing fields render as "—" in the report rather than causing errors).

    Returns
    -------
    application/pdf
        Binary PDF file.
        ``Content-Disposition: attachment; filename="PortGuard_Report_<id>_<date>.pdf"``

    Raises
    ------
    HTTP 422:
        Request body is not a valid JSON object.
    HTTP 500:
        PDF generation failed (caught ``ReportGenerationError``).
    """
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_PAYLOAD",
                "message": "Request body must be a JSON object (AnalyzeResponse).",
            },
        )

    shipment_id: str = payload.get("shipment_id") or "unknown"

    try:
        pdf_bytes = generate_report_from_dict(payload)
    except ReportGenerationError as exc:
        logger.error(
            "Direct PDF generation failed for shipment %s: %s",
            shipment_id, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "PDF_GENERATION_FAILED",
                "message": f"Failed to generate PDF report: {exc}",
            },
        )

    return _pdf_response(pdf_bytes, shipment_id)
