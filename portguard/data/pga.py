"""Partner Government Agency (PGA) requirements by HTS chapter."""

# Dict mapping HTS chapter (2-digit zero-padded str) to list of PGA requirements
PGA_REQUIREMENTS: dict[str, list[str]] = {
    "01": ["USDA APHIS — Live Animals permit required"],
    "02": [
        "USDA FSIS — Meat and poultry inspection certificate",
        "FDA — Prior Notice of Imported Food",
    ],
    "03": [
        "FDA — Prior Notice of Imported Food",
        "NMFS — Seafood inspection (if applicable)",
    ],
    "04": [
        "FDA — Prior Notice of Imported Food",
        "USDA AMS — Dairy import license (if applicable)",
    ],
    "05": ["USDA APHIS — Animal byproduct permit may be required"],
    "06": ["USDA APHIS — Phytosanitary certificate required for plants"],
    "07": [
        "USDA APHIS — Phytosanitary certificate",
        "FDA — Prior Notice of Imported Food",
    ],
    "08": [
        "USDA APHIS — Phytosanitary certificate",
        "FDA — Prior Notice of Imported Food",
    ],
    "09": ["FDA — Prior Notice of Imported Food"],
    "10": [
        "USDA APHIS — Phytosanitary certificate",
        "USDA AMS — Grain standards",
    ],
    "11": ["FDA — Prior Notice of Imported Food"],
    "12": [
        "USDA APHIS — Phytosanitary certificate",
        "FDA — Prior Notice of Imported Food (food use)",
    ],
    "13": ["FDA — Prior Notice (food/drug use as applicable)"],
    "14": ["USDA APHIS — Phytosanitary certificate"],
    "15": ["FDA — Prior Notice of Imported Food (edible oils)"],
    "16": [
        "FDA — Prior Notice of Imported Food",
        "USDA FSIS (meat-containing)",
    ],
    "17": ["FDA — Prior Notice of Imported Food"],
    "18": ["FDA — Prior Notice of Imported Food"],
    "19": ["FDA — Prior Notice of Imported Food"],
    "20": ["FDA — Prior Notice of Imported Food"],
    "21": [
        "FDA — Prior Notice of Imported Food",
        "TTB — Approval for flavoring agents (if applicable)",
    ],
    "22": [
        "TTB — Certificate of Label Approval (COLA) for wine/spirits",
        "FDA — Prior Notice of Imported Food (non-alcoholic)",
    ],
    "23": ["FDA — Prior Notice of Imported Food (animal feed)"],
    "24": [
        "ATF — Tobacco import permit (cigarettes)",
        "FDA — Registration required",
    ],
    "27": ["EPA — Fuel standards compliance (petroleum products)"],
    "28": ["EPA — TSCA certification or exemption required"],
    "29": [
        "EPA — TSCA certification or exemption required",
        "FDA (pharmaceutical intermediates as applicable)",
    ],
    "30": [
        "FDA — Drug establishment registration",
        "FDA 510(k) or premarket approval (medical devices)",
    ],
    "31": ["EPA — Pesticide registration under FIFRA (if applicable)"],
    "33": ["FDA — Cosmetics registration"],
    "35": ["FDA (protein products for food/drug use)"],
    "36": ["ATF — Explosive import permit required"],
    "38": ["EPA — TSCA certification"],
    "39": [
        "EPA — TSCA certification",
        "CPSC — Compliance with safety standards",
    ],
    "40": ["EPA — TSCA certification (rubber chemicals)"],
    "44": [
        "USDA APHIS — Phytosanitary certificate for wood products",
        "APHIS — Lacey Act declaration",
    ],
    "48": ["EPA — TSCA (certain paper chemicals)"],
    "49": [
        "CBP — Country of origin marking on books/printed matter (country of manufacture)",
    ],
    "50": ["FTC — Textile fiber products labeling"],
    "51": ["FTC — Textile fiber products labeling"],
    "52": [
        "FTC — Textile fiber products labeling",
        "CPSC — Flammability standards for children's sleepwear",
    ],
    "53": ["FTC — Textile fiber products labeling"],
    "54": ["FTC — Textile fiber products labeling"],
    "55": ["FTC — Textile fiber products labeling"],
    "56": ["FTC — Textile fiber products labeling"],
    "57": ["FTC — Textile fiber products labeling (carpets/rugs)"],
    "58": ["FTC — Textile fiber products labeling"],
    "59": ["FTC — Textile fiber products labeling"],
    "60": ["FTC — Textile fiber products labeling"],
    "61": [
        "FTC — Textile fiber products labeling",
        "CPSC — Flammability (children's sleepwear ch.61)",
    ],
    "62": [
        "FTC — Textile fiber products labeling",
        "CPSC — Flammability (children's sleepwear ch.62)",
    ],
    "63": ["FTC — Textile fiber products labeling"],
    "64": ["CPSC — Children's footwear safety standards (if applicable)"],
    "68": ["EPA — TSCA for asbestos-containing articles"],
    "69": ["CPSC — Safety standards for ceramic ware"],
    "70": ["CPSC — Safety standards (lead in glassware)"],
    "71": ["US Fish & Wildlife — CITES permit for wildlife-derived jewelry"],
    "72": [
        "EPA — Section 232 Steel import license required",
        "Commerce — Steel import monitoring",
    ],
    "73": [
        "EPA — Section 232 Steel import license (derivative articles)",
        "Commerce — Steel import monitoring",
    ],
    "76": ["Commerce — Section 232 Aluminum import license"],
    "84": [
        "FCC — Equipment authorization for wireless devices (subchapters)",
        "EPA — Energy Star (if applicable)",
    ],
    "85": [
        "FCC — Equipment authorization for radiofrequency devices",
        "CPSC — Safety standards (consumer electronics)",
    ],
    "86": ["FRA — Railroad equipment safety standards (locomotives)"],
    "87": [
        "NHTSA — Federal Motor Vehicle Safety Standards (FMVSS)",
        "EPA — Emissions certification",
    ],
    "88": ["FAA — Airworthiness certificate required"],
    "90": [
        "FDA — 510(k) or PMA for medical devices",
        "NRC — Radioactive materials import license",
    ],
    "93": ["ATF — Import permit required for firearms and ammunition"],
    "94": ["CPSC — Safety standards (children's furniture, mattresses)"],
    "95": [
        "CPSC — Toy safety standards (ASTM F963)",
        "CPSC — ASTM flammability",
    ],
    "96": ["CPSC — Pencils/pens safety (lead content)"],
    "97": ["US Fish & Wildlife — CITES permits for wildlife art"],
    "99": ["CBP — Special classification; additional documentation required"],
}


def get_pga_requirements(hts_code: str) -> list[str]:
    """Return the list of PGA requirements for the given HTS code.

    Looks up by 2-digit chapter (first two digits of hts_code).
    Returns an empty list if no requirements are mapped for the chapter.
    """
    chapter_padded = hts_code[:2].zfill(2)
    return PGA_REQUIREMENTS.get(chapter_padded, [])
