"""ParserAgent — extract and normalize shipment data using regex (no external API)."""

from __future__ import annotations

import re

from portguard.agents.base import BaseAgent
from portguard.models.shipment import ShipmentInput, ParsedShipment, ParsedLineItem

# Country name -> ISO2
_COUNTRY_ISO2: dict[str, str] = {
    "china": "CN", "prc": "CN", "people's republic of china": "CN",
    "vietnam": "VN", "viet nam": "VN",
    "bangladesh": "BD",
    "malaysia": "MY",
    "singapore": "SG",
    "south korea": "KR", "korea": "KR", "republic of korea": "KR",
    "taiwan": "TW",
    "india": "IN",
    "thailand": "TH",
    "indonesia": "ID",
    "cambodia": "KH",
    "germany": "DE",
    "mexico": "MX",
    "iran": "IR",
    "cuba": "CU",
    "north korea": "KP", "dprk": "KP",
    "syria": "SY",
    "russia": "RU", "russian federation": "RU",
    "belarus": "BY",
    "venezuela": "VE",
    "myanmar": "MM",
    "united states": "US", "usa": "US",
    "japan": "JP",
    "hong kong": "HK",
    "philippines": "PH",
    "pakistan": "PK",
    "sri lanka": "LK",
    "ethiopia": "ET",
    "turkey": "TR",
    "united kingdom": "GB", "uk": "GB", "great britain": "GB",
    "france": "FR", "netherlands": "NL", "italy": "IT",
    "brazil": "BR", "canada": "CA", "australia": "AU",
}

_CURRENCY_RATES = {
    "USD": 1.0, "EUR": 1.09, "GBP": 1.27, "CNY": 0.14,
    "VND": 0.000040, "JPY": 0.0067, "KRW": 0.00074,
    "INR": 0.012, "BDT": 0.0091, "MXN": 0.051,
    "CAD": 0.74, "AUD": 0.65,
}

_GOODS_CATEGORIES = [
    (["laptop", "computer", "tablet", "smartphone", "electronic", "semiconductor",
      "circuit", "processor", "display", "monitor", "printer", "router"],
     "electronics"),
    (["motor", "pump", "engine", "turbine", "compressor", "generator",
      "machinery", "machine", "equipment", "tool"],
     "machinery"),
    (["shirt", "t-shirt", "trousers", "dress", "garment", "apparel",
      "clothing", "textile", "fabric", "cotton", "polyester"],
     "textiles/apparel"),
    (["steel", "iron", "aluminum", "aluminium", "copper", "zinc", "metal",
      "wire rod", "flat-rolled"],
     "steel/metals"),
    (["shrimp", "fish", "seafood", "meat", "poultry", "vegetable", "fruit",
      "food", "frozen", "grain", "rice"],
     "food/agriculture"),
    (["cabinet", "furniture", "chair", "table", "shelf", "wood", "plywood"],
     "furniture/wood products"),
    (["drug", "medicine", "pharmaceutical", "vaccine", "medical device"],
     "pharmaceuticals"),
    (["car", "truck", "vehicle", "automotive", "spare part", "tire", "brake"],
     "automotive"),
    (["plastic", "polymer", "resin", "rubber", "silicone"],
     "plastics"),
    (["chemical", "solvent", "acid", "adhesive", "paint", "coating"],
     "chemicals"),
    (["solar", "photovoltaic", "battery", "lithium", "cell"],
     "electronics"),
    (["toy", "game", "doll", "puzzle", "sports"],
     "toys/games"),
    (["shoe", "boot", "sandal", "footwear"],
     "footwear"),
    (["paper", "book", "print", "cardboard", "packaging"],
     "paper/printing"),
    (["glass", "ceramic", "porcelain", "tile"],
     "glass/ceramics"),
]


def _resolve_iso2(country_text: str) -> str:
    """Resolve a country name or code to ISO2."""
    if not country_text:
        return "XX"
    t = country_text.strip().lower()
    # Direct 2-letter code
    if len(t) == 2:
        return t.upper()
    for key, iso2 in sorted(_COUNTRY_ISO2.items(), key=lambda x: -len(x[0])):
        if key in t:
            return iso2
    return "XX"


def _infer_goods_category(description: str) -> str:
    """Infer goods category from product description."""
    d = description.lower()
    for keywords, category in _GOODS_CATEGORIES:
        if any(kw in d for kw in keywords):
            return category
    return "other manufactured goods"


def _to_usd(value: float, currency: str) -> float:
    """Convert a value to USD using approximate exchange rates."""
    rate = _CURRENCY_RATES.get(currency.upper(), 1.0)
    return round(value * rate, 2)


def _extract_line_items_from_raw(raw_text: str) -> list[dict]:
    """Extract line items from raw document text using regex patterns."""
    items: list[dict] = []

    # Try: "Item: <desc>, qty <n>, unit price <currency> <amount>"
    pattern1 = re.compile(
        r'(?:Item|Goods)[:\s]+(.+?),?\s+qty\s+(\d+(?:\.\d+)?),?\s+'
        r'unit\s+price\s+(?:[A-Z]{3})?\s*([\d,]+(?:\.\d+)?)',
        re.I,
    )

    # Try: "<qty> <desc> <currency> <unit_price> <total>"  (tabular)
    pattern2 = re.compile(
        r'^(\d+(?:\.\d+)?)\s+(.{10,60}?)\s+(?:USD\s+)?([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)$',
        re.M,
    )

    for m in pattern1.finditer(raw_text):
        desc = m.group(1).strip()
        qty = float(m.group(2))
        unit_val = float(m.group(3).replace(",", ""))

        country_text = ""
        co_m = re.search(r'country\s+of\s+origin[:\s]+([A-Za-z ]+)', raw_text, re.I)
        if co_m:
            country_text = co_m.group(1).strip().rstrip(".,;")

        iso2 = _resolve_iso2(country_text) if country_text else "XX"

        hts_m = re.search(r'HTS[:\s]+([\d.]+)', raw_text, re.I)
        hts = hts_m.group(1) if hts_m else None

        items.append({
            "line_number": len(items) + 1,
            "description": desc,
            "quantity": qty,
            "unit": "units",
            "unit_value_usd": unit_val,
            "total_value_usd": round(qty * unit_val, 2),
            "country_of_origin": country_text or "Unknown",
            "country_of_origin_iso2": iso2,
            "manufacturer": None,
            "hts_declared": hts,
            "goods_category": _infer_goods_category(desc),
        })

    if not items:
        # Fallback: tabular pattern
        for m in pattern2.finditer(raw_text):
            qty = float(m.group(1))
            desc = m.group(2).strip()
            unit_val = float(m.group(3).replace(",", ""))
            total = float(m.group(4).replace(",", ""))

            country_text = ""
            co_m = re.search(r'country\s+of\s+origin[:\s]+([A-Za-z ]+)', raw_text, re.I)
            if co_m:
                country_text = co_m.group(1).strip().rstrip(".,;")
            iso2 = _resolve_iso2(country_text) if country_text else "XX"

            items.append({
                "line_number": len(items) + 1,
                "description": desc,
                "quantity": qty,
                "unit": "units",
                "unit_value_usd": unit_val,
                "total_value_usd": total,
                "country_of_origin": country_text or "Unknown",
                "country_of_origin_iso2": iso2,
                "manufacturer": None,
                "hts_declared": None,
                "goods_category": _infer_goods_category(desc),
            })

    return items


def _extract_total_value_from_raw(raw_text: str) -> float | None:
    """Try to extract the total value from raw text."""
    for pat in [
        r'(?:TOTAL|GRAND\s+TOTAL|INVOICE\s+VALUE|TOTAL\s+VALUE)[:\s]*(?:[A-Z]{3})?\s*\$?([\d,]+(?:\.\d+)?)',
        r'(?:USD|EUR|GBP)\s+([\d,]+(?:\.\d+)?)',
    ]:
        m = re.search(pat, raw_text, re.I)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                pass
    return None


class ParserAgent(BaseAgent):
    AGENT_NAME = "ParserAgent"

    async def parse(self, shipment_input: ShipmentInput) -> ParsedShipment:
        """Parse and normalize a ShipmentInput into a ParsedShipment.

        Uses structured fields where available; falls back to regex extraction
        from raw_text for unstructured documents. No external API calls.
        """
        notes: list[str] = []

        # ---- Resolve exporter country ISO2 ----
        exporter_iso2: str | None = None
        if shipment_input.exporter_country:
            exporter_iso2 = _resolve_iso2(shipment_input.exporter_country)
            if exporter_iso2 == "XX":
                exporter_iso2 = None

        # ---- Build line items from structured input ----
        parsed_items: list[ParsedLineItem] = []

        if shipment_input.line_items:
            for i, item in enumerate(shipment_input.line_items, 1):
                unit_val_usd = _to_usd(item.unit_value, item.currency)
                total_usd = round(unit_val_usd * item.quantity, 2)
                iso2 = _resolve_iso2(item.country_of_origin)
                if iso2 == "XX":
                    iso2 = exporter_iso2 or "XX"
                    if iso2 == "XX":
                        notes.append(f"Line {i}: could not resolve country ISO2 for '{item.country_of_origin}'")

                parsed_items.append(ParsedLineItem(
                    line_number=i,
                    description=item.description,
                    quantity=float(item.quantity),
                    unit=item.unit,
                    unit_value_usd=unit_val_usd,
                    total_value_usd=total_usd,
                    country_of_origin=item.country_of_origin,
                    country_of_origin_iso2=iso2,
                    manufacturer=item.manufacturer,
                    hts_declared=item.hts_declared,
                    goods_category=_infer_goods_category(item.description),
                ))
            if shipment_input.line_items[0].currency != "USD":
                notes.append(
                    f"Values converted from {shipment_input.line_items[0].currency} to USD "
                    f"at approximate rate {_CURRENCY_RATES.get(shipment_input.line_items[0].currency, 1.0)}."
                )

        elif shipment_input.raw_text:
            # Extract line items from raw text
            raw_items = _extract_line_items_from_raw(shipment_input.raw_text)
            for rd in raw_items:
                parsed_items.append(ParsedLineItem(**rd))
            if raw_items:
                notes.append("Line items extracted from raw document text via regex.")
            else:
                notes.append("Could not extract line items from raw text; manual review required.")

        # ---- Compute total value ----
        if parsed_items:
            total_value_usd = round(sum(i.total_value_usd for i in parsed_items), 2)
        elif shipment_input.raw_text:
            extracted_total = _extract_total_value_from_raw(shipment_input.raw_text)
            total_value_usd = extracted_total or 0.0
            if extracted_total:
                notes.append(f"Total value ${total_value_usd:,.2f} extracted from raw text.")
        else:
            total_value_usd = 0.0

        # ---- Parsing confidence ----
        has_all_fields = bool(
            shipment_input.importer_name
            and (shipment_input.exporter_name or shipment_input.raw_text)
            and parsed_items
            and total_value_usd > 0
        )
        parsing_confidence = 0.90 if has_all_fields and shipment_input.line_items else (
            0.75 if has_all_fields else 0.55
        )

        return ParsedShipment(
            importer_name=shipment_input.importer_name,
            importer_country=shipment_input.importer_country or "US",
            exporter_name=shipment_input.exporter_name,
            exporter_country=shipment_input.exporter_country,
            exporter_country_iso2=exporter_iso2,
            shipment_date=shipment_input.shipment_date,
            port_of_entry=shipment_input.port_of_entry,
            incoterms=shipment_input.incoterms,
            total_value_usd=total_value_usd,
            line_items=parsed_items,
            parser_notes=notes or ["All values in USD. Countries resolved to ISO-2."],
            parsing_confidence=parsing_confidence,
            additional_context=shipment_input.additional_context,
        )
