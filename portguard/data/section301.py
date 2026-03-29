"""Section 301 tariff data — US tariffs on Chinese goods under USTR investigations."""

from dataclasses import dataclass


@dataclass
class Section301Entry:
    hts_prefix: str
    rate: str
    list_name: str
    effective_date: str
    description: str


# Only applies to China
SECTION_301_COUNTRIES = {"CN"}

# ---------------------------------------------------------------------------
# List 1 — 25%, effective 2018-07-06
# Industrial machinery, equipment, motors, pumps, etc.
# ---------------------------------------------------------------------------
_LIST1_PREFIXES = [
    "8411", "8412", "8413", "8414", "8415", "8418", "8419", "8420",
    "8421", "8422", "8424", "8425", "8426", "8427", "8428", "8429",
    "8430", "8431", "8432", "8433", "8434", "8435", "8436", "8437",
    "8438", "8439", "8440", "8441", "8443", "8444", "8445", "8446",
    "8447", "8448", "8449", "8451", "8452", "8453", "8454", "8455",
    "8456", "8803",
]

# ---------------------------------------------------------------------------
# List 2 — 25%, effective 2018-08-23
# Engines, generators, motors, vehicles, etc.
# ---------------------------------------------------------------------------
_LIST2_PREFIXES = [
    "8407", "8408", "8409", "8483", "8501", "8503", "8504", "8505",
    "8507", "8511", "8512", "8513", "8526", "8703", "8704", "8705",
    "8706", "8707", "8708", "8711", "8712", "8714", "8716",
]

# ---------------------------------------------------------------------------
# List 3 — 25%, effective 2018-09-24
# Broad range: food, steel, plastics, electronics, furniture, medical, etc.
# ---------------------------------------------------------------------------
_LIST3_PREFIXES = [
    "0201", "0202", "0203", "0204", "0207", "0208", "0210",
    "0301", "0302", "0303", "0304", "0305", "0306", "0307",
    "0401", "0402", "0403", "0404",
    "0801", "0901",
    "1001", "1006", "1201", "1507",
    "2106", "2203", "2401", "2402", "2403",
    "3901", "3902", "3903", "3904", "3905", "3906", "3907", "3912",
    "3916", "3917", "3919", "3920", "3921", "3923", "3924", "3925",
    "4001", "4002", "4011", "4016", "4418", "4802", "4811", "4901",
    "6001", "6002", "6003", "6004", "6006",
    "7201", "7202", "7203", "7204", "7205", "7206", "7207", "7208",
    "7209", "7210", "7211", "7212", "7213", "7214", "7215", "7216",
    "7217", "7218", "7219", "7220", "7221", "7222", "7223", "7224",
    "7225", "7226", "7227", "7228", "7229",
    "7301", "7302", "7303", "7304", "7305", "7306", "7307", "7308",
    "7309", "7310", "7311", "7312", "7315", "7317", "7318", "7319",
    "7320", "7321", "7322", "7323", "7324", "7325", "7326",
    "8207", "8301", "8302", "8305", "8306", "8307", "8309", "8310", "8311",
    "8457", "8458", "8459", "8460", "8461", "8462", "8463", "8464", "8465",
    "8466", "8467", "8468", "8469", "8470", "8471", "8472", "8473", "8474",
    "8475", "8476", "8477", "8478", "8479", "8480", "8481", "8482", "8484",
    "8485", "8486", "8487",
    "8501", "8502", "8504", "8505", "8506", "8507", "8508", "8509", "8510",
    "8514", "8515", "8516", "8519", "8521", "8522", "8523", "8524", "8527",
    "8528", "8529", "8531", "8532", "8533", "8534", "8535", "8536", "8537",
    "8538", "8539", "8540", "8541", "8542", "8543", "8544", "8545", "8546",
    "8547", "8548",
    "8703",
    "9001", "9002", "9003", "9004", "9005", "9006", "9007", "9008", "9010",
    "9011", "9012", "9013", "9014", "9015", "9016", "9017", "9018", "9019",
    "9020", "9021", "9022", "9023", "9024", "9025", "9026", "9027", "9028",
    "9029", "9030", "9031", "9032", "9033",
    "9401", "9402", "9403", "9404", "9405", "9406",
    "9506", "9507", "9508",
    "9601", "9602", "9603", "9604", "9605", "9606", "9607", "9608", "9609",
    "9610", "9611", "9612", "9613", "9614", "9615", "9616", "9617", "9618",
]

# ---------------------------------------------------------------------------
# List 4A — 7.5%, effective 2019-09-01
# Consumer electronics, apparel, furniture
# Note: List 3 prefixes that overlap take precedence (25% > 7.5%)
# ---------------------------------------------------------------------------
_LIST4A_ENTRIES = [
    # Full heading prefixes
    ("8471", "Automatic data processing machines"),
    ("6110", "Sweaters, pullovers, sweatshirts, knitted"),
    ("6201", "Men's overcoats, anoraks, windbreakers"),
    ("6202", "Women's overcoats, anoraks, windbreakers"),
    ("6203", "Men's suits, ensembles, jackets, trousers"),
    ("6204", "Women's suits, ensembles, jackets, skirts"),
    ("6301", "Blankets and traveling rugs"),
    ("6302", "Bed linen, table linen, toilet linen"),
    ("6303", "Curtains, drapes, interior blinds"),
    ("6304", "Other furnishing articles, excl. knitted"),
    ("6305", "Sacks and bags of a kind used for packing"),
    ("6306", "Tarpaulins, awnings, tents, sails"),
    ("6307", "Other made up articles, including dress patterns"),
    # Subheading-level entries
    ("8517.12", "Telephones for cellular networks/smartphones"),
    ("9401.61", "Other seats, upholstered, wooden frame"),
    ("9401.69", "Other seats, wooden frame, not upholstered"),
    ("9401.71", "Other seats, metal frame, upholstered"),
    ("9401.79", "Other seats, metal frame, not upholstered"),
    ("9403.20", "Other metal furniture"),
    ("9403.50", "Wooden furniture of a kind used in bedrooms"),
    ("9403.60", "Other wooden furniture"),
]

# Build set of List 3 prefixes for overlap resolution
_LIST3_PREFIX_SET = set(_LIST3_PREFIXES)


def _build_section301_table() -> list[Section301Entry]:
    entries: list[Section301Entry] = []

    # List 1
    for prefix in _LIST1_PREFIXES:
        entries.append(Section301Entry(
            hts_prefix=prefix,
            rate="25%",
            list_name="List 1",
            effective_date="2018-07-06",
            description=f"Section 301 List 1 — HTS heading {prefix}",
        ))

    # List 2 — deduplicate against List 1
    list1_set = set(_LIST1_PREFIXES)
    for prefix in _LIST2_PREFIXES:
        if prefix not in list1_set:
            entries.append(Section301Entry(
                hts_prefix=prefix,
                rate="25%",
                list_name="List 2",
                effective_date="2018-08-23",
                description=f"Section 301 List 2 — HTS heading {prefix}",
            ))

    # List 3
    existing_prefixes = {e.hts_prefix for e in entries}
    for prefix in _LIST3_PREFIXES:
        if prefix not in existing_prefixes:
            entries.append(Section301Entry(
                hts_prefix=prefix,
                rate="25%",
                list_name="List 3",
                effective_date="2018-09-24",
                description=f"Section 301 List 3 — HTS heading {prefix}",
            ))
        existing_prefixes.add(prefix)

    # List 4A — skip if already covered by List 3 at the 4-digit level
    for subheading, description in _LIST4A_ENTRIES:
        four_digit = subheading[:4]
        # If the 4-digit heading is in List 3, List 3 takes precedence
        if four_digit in _LIST3_PREFIX_SET:
            continue
        entries.append(Section301Entry(
            hts_prefix=subheading,
            rate="7.5%",
            list_name="List 4A",
            effective_date="2019-09-01",
            description=f"Section 301 List 4A — {description}",
        ))

    return entries


SECTION_301_TABLE: list[Section301Entry] = _build_section301_table()


def get_section_301(hts_code: str, country_of_origin_iso2: str) -> "Section301Entry | None":
    """Return the most specific (longest prefix) Section 301 entry for a given HTS code.

    Only applies to goods originating in China (CN).
    Returns None if no match or if country is not China.
    """
    if country_of_origin_iso2.upper() not in SECTION_301_COUNTRIES:
        return None

    # Normalize HTS code: remove dots and spaces
    normalized = hts_code.replace(".", "").replace(" ", "")

    best_match: "Section301Entry | None" = None
    best_length = 0

    for entry in SECTION_301_TABLE:
        prefix = entry.hts_prefix.replace(".", "").replace(" ", "")
        if normalized.startswith(prefix) and len(prefix) > best_length:
            best_match = entry
            best_length = len(prefix)

    return best_match
