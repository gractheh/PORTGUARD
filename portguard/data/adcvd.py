"""Antidumping (AD) and Countervailing Duty (CVD) orders — active US Commerce orders."""

from dataclasses import dataclass


@dataclass
class ADCVDOrder:
    case_number: str            # e.g. "A-570-029"
    order_type: str             # "AD" or "CVD"
    product_description: str
    hts_prefixes: list[str]     # HTS headings/subheadings subject to the order
    country_iso2: str
    duty_rate: str              # e.g. "265.79%" or range
    effective_date: str
    federal_register: str
    notes: str


ADCVD_ORDERS: list[ADCVDOrder] = [
    # -------------------------------------------------------------------------
    # Steel products — China
    # -------------------------------------------------------------------------
    ADCVDOrder(
        case_number="A-570-029",
        order_type="AD",
        product_description="Cold-Rolled Steel Flat Products from China",
        hts_prefixes=["7209", "7211"],
        country_iso2="CN",
        duty_rate="265.79% (all others)",
        effective_date="2016-09-12",
        federal_register="81 FR 62873",
        notes="Covers cold-rolled steel in coils or cut lengths; does not include stainless.",
    ),
    ADCVDOrder(
        case_number="A-570-028",
        order_type="AD",
        product_description="Hot-Rolled Steel Flat Products from China",
        hts_prefixes=["7208", "7211"],
        country_iso2="CN",
        duty_rate="199.43% (all others)",
        effective_date="2016-09-12",
        federal_register="81 FR 62890",
        notes="Covers hot-rolled steel in coils or cut lengths.",
    ),
    ADCVDOrder(
        case_number="A-570-967",
        order_type="AD",
        product_description="Aluminum Extrusions from China",
        hts_prefixes=["7604", "7608", "7610"],
        country_iso2="CN",
        duty_rate="374.15% (all others)",
        effective_date="2011-05-26",
        federal_register="76 FR 30650",
        notes="Covers aluminum extrusions; excludes certain solar frame components.",
    ),
    ADCVDOrder(
        case_number="A-570-979",
        order_type="AD",
        product_description="Crystalline Silicon Photovoltaic Cells from China",
        hts_prefixes=["8541.40"],
        country_iso2="CN",
        duty_rate="238.95% (all others)",
        effective_date="2012-12-07",
        federal_register="77 FR 73018",
        notes="Covers CSPV cells and modules; see also companion CVD order C-570-980.",
    ),
    ADCVDOrder(
        case_number="A-570-106",
        order_type="AD",
        product_description="Wooden Cabinets and Vanities from China",
        hts_prefixes=["9403.40", "9403.90"],
        country_iso2="CN",
        duty_rate="262.18% (all others)",
        effective_date="2020-02-10",
        federal_register="85 FR 7839",
        notes="Covers wooden kitchen cabinets and bathroom vanities.",
    ),
    ADCVDOrder(
        case_number="A-570-116",
        order_type="AD",
        product_description="Hardwood Plywood from China",
        hts_prefixes=["4412"],
        country_iso2="CN",
        duty_rate="183.36% (all others)",
        effective_date="2018-01-04",
        federal_register="83 FR 504",
        notes="Covers hardwood and decorative plywood; see also companion CVD C-570-116.",
    ),
    ADCVDOrder(
        case_number="C-570-116",
        order_type="CVD",
        product_description="Hardwood Plywood from China (CVD)",
        hts_prefixes=["4412"],
        country_iso2="CN",
        duty_rate="22.98% (all others)",
        effective_date="2018-01-04",
        federal_register="83 FR 504",
        notes="Countervailing duty companion to AD order A-570-116.",
    ),
    ADCVDOrder(
        case_number="A-570-099",
        order_type="AD",
        product_description="Carbon and Alloy Steel Wire Rod from China",
        hts_prefixes=["7213", "7227"],
        country_iso2="CN",
        duty_rate="110.25% (all others)",
        effective_date="2014-10-22",
        federal_register="79 FR 68551",
        notes="Covers carbon and alloy steel wire rod in coils.",
    ),
    ADCVDOrder(
        case_number="A-570-918",
        order_type="AD",
        product_description="Prestressed Concrete Steel Wire Strand from China",
        hts_prefixes=["7312.10"],
        country_iso2="CN",
        duty_rate="43.80% - 194.90%",
        effective_date="2010-05-14",
        federal_register="75 FR 27401",
        notes="Covers PC strand used in pre-tensioned and post-tensioned concrete.",
    ),
    ADCVDOrder(
        case_number="A-570-601",
        order_type="AD",
        product_description="Wooden Bedroom Furniture from China",
        hts_prefixes=["9403.50", "9403.60"],
        country_iso2="CN",
        duty_rate="216.01% (all others)",
        effective_date="2005-01-04",
        federal_register="70 FR 329",
        notes="Covers bedroom furniture including beds, wardrobes, chests of drawers.",
    ),

    # -------------------------------------------------------------------------
    # Steel products — Other countries
    # -------------------------------------------------------------------------
    ADCVDOrder(
        case_number="A-428-830",
        order_type="AD",
        product_description="Hot-Rolled Steel Flat Products from Germany",
        hts_prefixes=["7208", "7211"],
        country_iso2="DE",
        duty_rate="3.44%",
        effective_date="2016-09-12",
        federal_register="81 FR 67961",
        notes="Covers hot-rolled steel flat products originating in Germany.",
    ),
    ADCVDOrder(
        case_number="A-580-883",
        order_type="AD",
        product_description="Cold-Rolled Steel Flat Products from Korea",
        hts_prefixes=["7209", "7211"],
        country_iso2="KR",
        duty_rate="6.32%",
        effective_date="2016-09-12",
        federal_register="81 FR 62876",
        notes="Covers cold-rolled steel flat products originating in South Korea.",
    ),
    ADCVDOrder(
        case_number="A-201-848",
        order_type="AD",
        product_description="Cold-Rolled Steel Flat Products from Mexico",
        hts_prefixes=["7209", "7211"],
        country_iso2="MX",
        duty_rate="7.68%",
        effective_date="2016-09-12",
        federal_register="81 FR 62882",
        notes="Covers cold-rolled steel flat products originating in Mexico.",
    ),

    # -------------------------------------------------------------------------
    # Steel nails — Vietnam
    # -------------------------------------------------------------------------
    ADCVDOrder(
        case_number="A-552-818",
        order_type="AD",
        product_description="Steel Nails from Vietnam",
        hts_prefixes=["7317"],
        country_iso2="VN",
        duty_rate="323.99% (all others)",
        effective_date="2015-07-16",
        federal_register="80 FR 41962",
        notes="Covers steel nails, including roofing nails, packaged for retail or bulk.",
    ),
]


def get_adcvd_orders(hts_code: str, country_iso2: str) -> list[ADCVDOrder]:
    """Return all active AD/CVD orders that match the given HTS code and country of origin.

    Matching is performed by checking whether any of the order's hts_prefixes
    is a prefix of the normalized HTS code (dots removed).
    """
    normalized = hts_code.replace(".", "").replace(" ", "")
    country_upper = country_iso2.upper()
    results: list[ADCVDOrder] = []

    for order in ADCVD_ORDERS:
        if order.country_iso2.upper() != country_upper:
            continue
        for prefix in order.hts_prefixes:
            prefix_normalized = prefix.replace(".", "").replace(" ", "")
            if normalized.startswith(prefix_normalized):
                results.append(order)
                break  # Don't add same order twice

    return results
