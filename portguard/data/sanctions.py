"""OFAC sanctions programs data — US Treasury Office of Foreign Assets Control."""

from dataclasses import dataclass


@dataclass
class SanctionsProgram:
    country_iso2: str
    country_name: str
    program_name: str
    cfr_citation: str
    program_type: str       # "COMPREHENSIVE" or "SECTORAL"
    sectors: list[str]      # empty for comprehensive, list for sectoral
    notes: str


SANCTIONS_PROGRAMS: list[SanctionsProgram] = [
    # -------------------------------------------------------------------------
    # Comprehensive sanctions (full trade embargo)
    # -------------------------------------------------------------------------
    SanctionsProgram(
        "CU", "Cuba", "CACR", "31 CFR 515",
        "COMPREHENSIVE", [],
        "Cuba Assets Control Regulations — comprehensive embargo since 1963",
    ),
    SanctionsProgram(
        "IR", "Iran", "ITSR", "31 CFR 560",
        "COMPREHENSIVE", [],
        "Iranian Transactions and Sanctions Regulations — comprehensive embargo",
    ),
    SanctionsProgram(
        "KP", "North Korea", "NKSR", "31 CFR 510",
        "COMPREHENSIVE", [],
        "North Korea Sanctions Regulations — comprehensive embargo",
    ),
    SanctionsProgram(
        "SY", "Syria", "SySR", "31 CFR 542",
        "COMPREHENSIVE", [],
        "Syrian Sanctions Regulations — comprehensive embargo",
    ),

    # -------------------------------------------------------------------------
    # Sectoral sanctions
    # -------------------------------------------------------------------------
    SanctionsProgram(
        "RU", "Russia", "RUSSIA-EO14024", "31 CFR 587",
        "SECTORAL", ["finance", "energy", "defense", "aviation", "technology"],
        "EO 14024 — Russia-related sanctions following invasion of Ukraine",
    ),
    SanctionsProgram(
        "BY", "Belarus", "BELARUS-EO14038", "31 CFR 548",
        "SECTORAL", ["finance", "defense", "technology"],
        "EO 14038 — Belarus sanctions",
    ),
    SanctionsProgram(
        "VE", "Venezuela", "VZLA-EO13884", "31 CFR 591",
        "SECTORAL", ["government", "gold", "oil"],
        "EO 13884 — Venezuela government sanctions",
    ),
    SanctionsProgram(
        "MM", "Myanmar/Burma", "BURMA-EO14014", "31 CFR 582",
        "SECTORAL", ["defense", "government"],
        "EO 14014 — Burma sanctions following military coup",
    ),
    SanctionsProgram(
        "CF", "Central African Republic", "CAR-EO13667", "31 CFR 553",
        "SECTORAL", ["defense", "minerals"],
        "EO 13667 — CAR sanctions",
    ),
    SanctionsProgram(
        "ZW", "Zimbabwe", "ZIMBABWE-EO13391", "31 CFR 541",
        "SECTORAL", ["government", "minerals"],
        "EO 13391 — Zimbabwe sanctions",
    ),
]

# Crimea and occupied Ukraine regions
OCCUPIED_TERRITORY_SANCTIONS = [
    {
        "region": "Crimea",
        "authority": "EO 13685",
        "cfr": "31 CFR 589",
        "type": "COMPREHENSIVE",
    },
    {
        "region": "Donetsk People's Republic (DNR)",
        "authority": "EO 14065",
        "cfr": "31 CFR 587",
        "type": "COMPREHENSIVE",
    },
    {
        "region": "Luhansk People's Republic (LNR)",
        "authority": "EO 14065",
        "cfr": "31 CFR 587",
        "type": "COMPREHENSIVE",
    },
]

SANCTIONED_COUNTRY_ISO2: set[str] = {p.country_iso2 for p in SANCTIONS_PROGRAMS}
COMPREHENSIVELY_SANCTIONED_ISO2: set[str] = {
    p.country_iso2 for p in SANCTIONS_PROGRAMS if p.program_type == "COMPREHENSIVE"
}


def get_sanctions_programs(country_iso2: str) -> list[SanctionsProgram]:
    """Return all active sanctions programs for the given country ISO-2 code."""
    return [p for p in SANCTIONS_PROGRAMS if p.country_iso2.upper() == country_iso2.upper()]


def is_comprehensively_sanctioned(country_iso2: str) -> bool:
    """Return True if the country is subject to a comprehensive OFAC embargo."""
    return any(
        p.program_type == "COMPREHENSIVE"
        for p in get_sanctions_programs(country_iso2)
    )
