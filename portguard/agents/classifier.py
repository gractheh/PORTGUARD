"""ClassifierAgent — keyword-based HTSUS tariff classification (no external API)."""

import re

from portguard.agents.base import BaseAgent
from portguard.models.shipment import ParsedShipment, ParsedLineItem
from portguard.models.classification import ClassificationResult, HTSLineClassification, GRIAnalysis

# ---------------------------------------------------------------------------
# Keyword -> HTS lookup table
# Each entry: (keywords, hts_code, description, duty_rate_general)
# Keywords are matched against the lowercased product description.
# More specific entries should appear before more general ones.
# ---------------------------------------------------------------------------
_HTS_TABLE: list[tuple[list[str], str, str, str]] = [
    # ADP machines / computers
    (["laptop", "notebook pc", "portable computer", "adp machine", "notebook computer"],
     "8471.30.0100",
     "Portable automatic data processing machines, weighing <= 10 kg, "
     "with CPU, keyboard, and display",
     "Free"),
    (["desktop computer", "workstation", "personal computer", "pc tower"],
     "8471.41.0150",
     "Other automatic data processing machines, comprising a CPU, input/output unit",
     "Free"),
    (["server", "blade server", "rack server"],
     "8471.49.0000",
     "Other automatic data processing machines",
     "Free"),
    # Semiconductors / ICs
    (["integrated circuit", "ic board", "semiconductor", "microchip", "monolithic",
      "memory chip", "processor chip", "cpu chip", "flash memory", "dram"],
     "8542.31.0000",
     "Electronic integrated circuits — processors and controllers",
     "Free"),
    # Smartphones / mobile
    (["smartphone", "mobile phone", "cell phone", "cellular phone"],
     "8517.12.0050",
     "Smartphones",
     "Free"),
    # Telecom equipment
    (["router", "network switch", "ethernet switch", "wifi access point", "base station"],
     "8517.62.0090",
     "Machines for the reception, conversion and transmission of data",
     "Free"),
    # Solar
    (["solar panel", "photovoltaic module", "pv module", "solar cell"],
     "8541.40.6020",
     "Photovoltaic cells assembled into modules or made up into panels",
     "Free"),
    # Display / monitors
    (["lcd monitor", "led monitor", "flat panel display", "computer monitor"],
     "8528.52.0000",
     "Monitors of a kind solely or principally used in automatic data processing machines",
     "Free"),
    # Steel — cold-rolled flat products
    (["cold-rolled steel", "cold rolled steel flat", "cold-rolled flat"],
     "7209.16.0030",
     "Flat-rolled products of iron or non-alloy steel, cold-rolled, width >= 600mm",
     "Free"),
    # Steel — hot-rolled flat products
    (["hot-rolled steel", "hot rolled steel flat", "hot-rolled flat"],
     "7208.36.0030",
     "Flat-rolled products of iron or non-alloy steel, hot-rolled, width >= 600mm",
     "Free"),
    # Steel wire rod
    (["wire rod", "steel wire rod", "carbon steel wire rod"],
     "7213.91.3011",
     "Bars and rods of iron or non-alloy steel, hot-rolled, in irregularly wound coils",
     "Free"),
    # Aluminum extrusions
    (["aluminum extrusion", "aluminium extrusion", "aluminum profile", "aluminium profile"],
     "7604.29.1010",
     "Bars, rods and profiles of aluminum alloys",
     "Free"),
    # Wooden furniture
    (["wooden cabinet", "wood cabinet", "kitchen cabinet", "wooden furniture"],
     "9403.40.9060",
     "Wooden furniture of a kind used in the kitchen",
     "Free"),
    (["wooden bedroom", "bedroom furniture", "bed frame", "dresser"],
     "9403.50.9042",
     "Wooden furniture of a kind used in the bedroom",
     "Free"),
    # Hardwood plywood
    (["hardwood plywood", "plywood"],
     "4412.33.0571",
     "Plywood of other wood, at least one ply of tropical wood",
     "Free"),
    # Steel nails
    (["steel nail", "iron nail", "wire nail"],
     "7317.00.5500",
     "Nails, tacks, drawing pins, staples of iron or steel",
     "Free"),
    # Seafood — shrimp
    (["shrimp", "prawn"],
     "0306.17.0020",
     "Frozen shrimp and prawns",
     "Free"),
    # Seafood — fish
    (["salmon", "tuna", "cod", "tilapia", "catfish", "pollock"],
     "0303.89.0000",
     "Fish, frozen",
     "Free"),
    # Cotton t-shirts / knitted garments
    (["cotton t-shirt", "cotton tee", "t-shirt", "tee shirt"],
     "6109.10.0012",
     "T-shirts, singlets and similar garments, knitted or crocheted, of cotton, men's or boys'",
     "16.5%"),
    # Cotton woven shirts
    (["cotton shirt", "woven cotton shirt", "dress shirt"],
     "6205.20.2016",
     "Men's or boys' shirts of cotton",
     "19.7%"),
    # Footwear
    (["shoe", "sneaker", "boot", "sandal", "footwear"],
     "6403.99.9060",
     "Footwear with outer soles of rubber/plastics and uppers of leather",
     "8.5%"),
    # Toys
    (["toy", "action figure", "doll", "board game", "puzzle"],
     "9503.00.0073",
     "Toys representing animals or non-human creatures",
     "Free"),
    # Pumps / machinery
    (["pump", "industrial pump", "centrifugal pump", "submersible pump"],
     "8413.70.2004",
     "Centrifugal pumps other than for liquids",
     "Free"),
    (["motor", "electric motor", "ac motor", "dc motor"],
     "8501.52.4000",
     "AC motors, multi-phase, of an output exceeding 750W but not exceeding 7.5kW",
     "2.8%"),
    # Chemicals
    (["chemical", "polymer", "resin", "adhesive"],
     "3901.20.5000",
     "Polymers of ethylene, in primary forms",
     "Free"),
]

# HTS chapter duty rate defaults (used for declared-code lookup)
_HTS_CHAPTER_DEFAULTS: dict[str, str] = {
    "84": "Free", "85": "Free", "90": "Free",
    "72": "Free", "73": "Free", "76": "Free",
    "03": "Free", "04": "Free", "07": "Free", "08": "Free",
    "61": "12%", "62": "12%", "63": "9%",
    "94": "Free", "95": "Free",
}


def _lookup_duty_rate(hts_code: str) -> str:
    """Look up approximate duty rate for a declared HTS code."""
    chapter = hts_code[:2]
    # Check our full table first
    code_clean = hts_code.replace(".", "")
    for _, tbl_code, _, duty in _HTS_TABLE:
        if tbl_code.replace(".", "").startswith(code_clean[:6]):
            return duty
    return _HTS_CHAPTER_DEFAULTS.get(chapter, "Free")


def _lookup_description(hts_code: str, fallback_desc: str) -> str:
    """Look up standard description for an HTS code, falling back to item description."""
    for _, tbl_code, desc, _ in _HTS_TABLE:
        if tbl_code == hts_code:
            return desc
    return fallback_desc[:120]


def _normalize_hts(code: str) -> str:
    """Pad or normalize an HTS code to 10-digit format XXXX.XX.XXXX."""
    code = code.strip()
    # Already 10-digit: XXXX.XX.XXXX
    if re.match(r'^\d{4}\.\d{2}\.\d{4}$', code):
        return code
    # 8-digit: XXXX.XX.XX -> XXXX.XX.XX00
    if re.match(r'^\d{4}\.\d{2}\.\d{2}$', code):
        return code + "00"
    # 6-digit: XXXX.XX -> XXXX.XX.0000
    if re.match(r'^\d{4}\.\d{2}$', code):
        return code + ".0000"
    # No dots, 10 digits: XXXXXXXXXX -> XXXX.XX.XXXX
    if re.match(r'^\d{10}$', code):
        return f"{code[:4]}.{code[4:6]}.{code[6:]}"
    return code


def _classify_line_item(item: ParsedLineItem) -> HTSLineClassification:
    """Classify a single line item using declared HTS or keyword matching."""
    desc_lower = item.description.lower()

    # Priority 1: use declared HTS code if present
    if item.hts_declared:
        hts = _normalize_hts(item.hts_declared)
        duty = _lookup_duty_rate(hts)
        description = _lookup_description(hts, item.description)
        return HTSLineClassification(
            line_number=item.line_number,
            hts_code=hts,
            hts_description=description,
            duty_rate_general=duty,
            duty_rate_special=None,
            gri_analysis=GRIAnalysis(
                primary_gri="GRI 1",
                secondary_gri=None,
                rationale=f"Classified per importer-declared HTS code {item.hts_declared}.",
            ),
            confidence=0.80,
            classification_notes="Based on declared HTS code. Verify with licensed customs broker.",
        )

    # Priority 2: keyword matching against lookup table
    for keywords, hts_code, description, duty in _HTS_TABLE:
        if any(kw in desc_lower for kw in keywords):
            return HTSLineClassification(
                line_number=item.line_number,
                hts_code=hts_code,
                hts_description=description,
                duty_rate_general=duty,
                duty_rate_special=None,
                gri_analysis=GRIAnalysis(
                    primary_gri="GRI 1",
                    secondary_gri=None,
                    rationale=(
                        f"Classified under {hts_code} based on keyword match in product "
                        f"description. Primary rule GRI 1 applied."
                    ),
                ),
                confidence=0.65,
                classification_notes=(
                    "Keyword-based classification. Manual review by a licensed customs broker "
                    "is recommended before filing the entry."
                ),
            )

    # Priority 3: fallback based on goods_category
    category = item.goods_category.lower()
    if "electronics" in category or "machinery" in category:
        hts_code, duty = "8479.89.9899", "Free"
        desc = "Machines and mechanical appliances not elsewhere specified"
    elif "textile" in category or "apparel" in category:
        hts_code, duty = "6307.90.9889", "7%"
        desc = "Other made-up textile articles"
    elif "food" in category or "agriculture" in category:
        hts_code, duty = "2106.90.9998", "6.4%"
        desc = "Food preparations not elsewhere specified"
    elif "steel" in category or "metal" in category:
        hts_code, duty = "7326.90.8688", "2.9%"
        desc = "Other articles of iron or steel"
    else:
        hts_code, duty = "9999.00.0000", "Free"
        desc = "Unclassified goods — manual classification required"

    return HTSLineClassification(
        line_number=item.line_number,
        hts_code=hts_code,
        hts_description=desc,
        duty_rate_general=duty,
        duty_rate_special=None,
        gri_analysis=GRIAnalysis(
            primary_gri="GRI 4",
            secondary_gri=None,
            rationale=(
                "Unable to classify from description alone. Classified to nearest "
                "heading based on goods category. Manual review required."
            ),
        ),
        confidence=0.30,
        classification_notes=(
            "Classification requires manual review by a licensed customs broker. "
            "The declared HTS code should be provided by the importer."
        ),
    )


class ClassifierAgent(BaseAgent):
    AGENT_NAME = "ClassifierAgent"

    async def classify(self, parsed_shipment: ParsedShipment) -> ClassificationResult:
        """Classify all line items using declared HTS codes or keyword matching.

        No external API calls — uses a keyword table and declared HTS codes.
        """
        classifications = [
            _classify_line_item(item) for item in parsed_shipment.line_items
        ]

        notes: list[str] = []
        declared = sum(1 for c in classifications if c.confidence >= 0.75)
        keyword = sum(1 for c in classifications if 0.50 <= c.confidence < 0.75)
        fallback = sum(1 for c in classifications if c.confidence < 0.50)

        if declared:
            notes.append(f"{declared} line item(s) classified using declared HTS code.")
        if keyword:
            notes.append(f"{keyword} line item(s) classified via keyword matching.")
        if fallback:
            notes.append(
                f"{fallback} line item(s) require manual classification "
                "(description insufficient for keyword match)."
            )

        return ClassificationResult(
            classifications=classifications,
            classifier_notes=notes,
        )
