"""
PortGuard — DocumentClassifier Agent (Hardened v2)
===================================================
Multi-signal, troll-proof document classification.

Defense-in-depth approach:
  Layer 1 — Pro-shipping vocabulary scoring
  Layer 2 — Document type fingerprinting (20 known types)
  Layer 3 — Hard format signals (HTS codes, container numbers, port names, etc.)
  Layer 4 — Anti-pattern detection (resumes, shopping lists, academic docs, medical, etc.)
  Layer 5 — Composite confidence scoring + definitive decision

Confidence levels:
  HIGH   ≥ 0.75  → proceed
  MEDIUM ≥ 0.50  → proceed
  LOW    ≥ 0.35  → proceed with warning
  REJECTED       → hard block, user-facing reason

No external API calls. All logic is deterministic and regex/keyword-based.
"""

import re
import math
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ClassificationResult:
    accepted: bool
    confidence: float                        # 0.0 – 1.0
    confidence_label: str                    # HIGH / MEDIUM / LOW / REJECTED
    detected_doc_type: Optional[str]         # e.g. "Bill of Lading"
    detected_doc_type_code: Optional[str]    # e.g. "BOL"
    rejection_reason: Optional[str]          # user-facing sentence if rejected
    rejection_category: Optional[str]        # RESUME / SHOPPING / ACADEMIC / MEDICAL / GENERIC
    anti_pattern_matches: list               # which anti-patterns fired
    pro_signals: list                        # which pro-signals fired
    hard_signals: list                       # HTS codes, container numbers, etc.
    warning: Optional[str]                   # shown for LOW confidence accepts
    raw_scores: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Term & pattern definitions
# ---------------------------------------------------------------------------

# Each pro-shipping term: (pattern, weight)
PRO_SHIPPING_TERMS = [
    # Universal trade vocabulary — high weight
    (r"\bshipper\b", 3.0),
    (r"\bconsignee\b", 3.0),
    (r"\bnotify\s+party\b", 3.0),
    (r"\bport\s+of\s+(loading|discharge|origin|destination)\b", 3.0),
    (r"\bplace\s+of\s+(receipt|delivery)\b", 2.5),
    (r"\bbill\s+of\s+lading\b", 4.0),
    (r"\b(b/?l|bol|hbl|mbl|obl)\b", 3.0),
    (r"\bcommercial\s+invoice\b", 4.0),
    (r"\bpacking\s+list\b", 4.0),
    (r"\bcertificate\s+of\s+origin\b", 4.0),
    (r"\bair\s+waybill\b", 4.0),
    (r"\b(awb|hawb|mawb)\b", 3.0),
    (r"\bletter\s+of\s+credit\b", 3.5),
    (r"\bfreight\s+(manifest|forwarder|charges?)\b", 3.0),
    (r"\bcustoms\s+(declaration|entry|clearance|broker)\b", 3.5),
    (r"\bharmonized\s+(tariff|system|code)\b", 3.5),
    (r"\b(hts|hs)\s*(code|number|classification)?\s*[:\-]?\s*\d{4}", 4.0),
    (r"\bisf\b", 3.0),
    (r"\b10\s*\+\s*2\b", 3.0),
    (r"\bimporter\s+of\s+record\b", 3.0),
    (r"\bexporter\b", 2.5),
    (r"\bincoterm", 3.5),
    (r"\b(fob|cif|cip|cfr|exw|dap|ddp|ddu|fas|fca)\b", 3.0),
    (r"\bcontainer\s+(number|no\.?|id|seal)\b", 3.0),
    (r"\b(fcl|lcl|20gp|40gp|40hc|45hc)\b", 3.0),
    (r"\bvoyage\s+(no\.?|number)\b", 2.5),
    (r"\bvessel\s+(name|no\.?)\b", 2.5),
    (r"\bmanifest\b", 2.5),
    (r"\bcargo\b", 2.0),
    (r"\bshipment\b", 2.0),
    (r"\bconsignment\b", 2.5),
    (r"\bwaybill\b", 3.0),
    (r"\bentry\s+(summary|type|number)\b", 2.5),
    (r"\bduty\s+(rate|paid|free|drawback)\b", 2.5),
    (r"\btariff\b", 2.5),
    (r"\bcountry\s+of\s+origin\b", 3.0),
    (r"\bmanufacture[rd]?\s+in\b", 2.0),
    (r"\bpga\b", 2.5),
    (r"\bfda\b.*\b(entry|prior notice|registration)\b", 2.0),
    (r"\bsanction(ed|s)?\b", 1.5),
    (r"\bofac\b", 3.0),
    (r"\buflpa\b", 3.0),
    (r"\bad/?cvd\b", 3.0),
    (r"\banti[-\s]dumping\b", 2.5),
    (r"\bcountervailing\b", 2.5),
    (r"\bsection\s+301\b", 3.0),
    (r"\bphytosanitary\b", 3.5),
    (r"\bfumigation\b", 2.5),
    (r"\bsds\b|\bmsds\b|safety\s+data\s+sheet", 2.5),
    (r"\bdangerous\s+goods\b", 2.5),
    (r"\bimdg\b|\bun\s+number\b|\bun\d{4}\b", 2.5),
    (r"\binsurance\s+(certificate|policy|value)\b", 2.5),
    (r"\bpro\s*forma\s+invoice\b", 3.5),
    (r"\barrival\s+notice\b", 3.0),
    (r"\bdelivery\s+order\b", 2.5),
    (r"\btelex\s+release\b", 3.0),
    (r"\bsea\s+waybill\b", 3.5),
    (r"\bshipper['\s]+s?\s+letter\s+of\s+instruction", 3.5),
    (r"\b(sli|slo)\b", 2.0),
    (r"\bweight\s+certificate\b", 3.0),
    (r"\binspection\s+certificate\b", 2.5),
    (r"\bnet\s+weight\b", 2.0),
    (r"\bgross\s+weight\b", 2.0),
    (r"\b(kg|kgs|lbs|mt|cbm|cft)\b", 1.5),
    (r"\bnumber\s+of\s+(packages|cartons|pallets|cases)\b", 2.0),
    (r"\btotal\s+(packages|cartons|pieces|units)\b", 1.5),
    (r"\bdescription\s+of\s+(goods|cargo|merchandise)\b", 2.5),
    (r"\bunit\s+(price|value|cost)\b", 1.5),
    (r"\b(cif|fob|c&f)\s+value\b", 2.5),
    (r"\btotal\s+(invoice\s+)?value\b", 1.5),
    (r"\bcurrency\s*:\s*(usd|eur|gbp|cny|jpy)", 1.5),
    (r"\b(origin|destination)\s+country\b", 2.0),
    (r"\btrading\s+(partner|company|house)\b", 1.5),
    (r"\bimport\b", 1.5),
    (r"\bexport\b", 1.5),
    (r"\bclearance\b", 1.5),
    (r"\bcbp\b|\bcustoms\s+and\s+border\s+protection\b", 3.0),
    (r"\bscac\b", 2.5),
    (r"\bmid\b.*\b(code|manufacturer)\b", 2.0),
    (r"\bein\b|\bfein\b|\btax\s+id\b", 1.0),
    (r"\b(c-tpat|ctpat)\b", 2.5),
    (r"\baeo\b", 2.0),
    (r"\bkimberley\s+process\b", 3.0),
    (r"\bcites\b", 2.5),
    (r"\breach\b.*\bregulation\b|\btsca\b", 2.0),
]

# Document type fingerprints — (name, code, required_patterns, supporting_patterns, min_required)
DOC_TYPE_FINGERPRINTS = [
    {
        "name": "Bill of Lading",
        "code": "BOL",
        "required": [r"\bbill\s+of\s+lading\b|\b(hbl|mbl|obl|b/?l)\b"],
        "supporting": [r"\bshipper\b", r"\bconsignee\b", r"\bvessel\b", r"\bvoyage\b",
                       r"\bport\s+of\s+(loading|discharge)\b", r"\bcontainer\b", r"\bfreight\b"],
        "min_required": 1,
    },
    {
        "name": "Commercial Invoice",
        "code": "CI",
        "required": [r"\bcommercial\s+invoice\b|\binvoice\s+no\.?\b|\binvoice\s+(number|date)\b"],
        "supporting": [r"\bshipper\b|\bseller\b|\bexporter\b", r"\bconsignee\b|\bbuyer\b|\bimporter\b",
                       r"\bunit\s+price\b", r"\btotal\s+(value|amount)\b", r"\bcountry\s+of\s+origin\b",
                       r"\bpayment\s+terms\b|\bincoterm"],
        "min_required": 1,
    },
    {
        "name": "Packing List",
        "code": "PL",
        "required": [r"\bpacking\s+list\b"],
        "supporting": [r"\bnet\s+weight\b", r"\bgross\s+weight\b", r"\bnumber\s+of\s+(packages|cartons|cases)\b",
                       r"\bdimensions?\b", r"\bmarks?\s+(and\s+numbers?)?\b", r"\bcbm\b"],
        "min_required": 1,
    },
    {
        "name": "Certificate of Origin",
        "code": "CO",
        "required": [r"\bcertificate\s+of\s+origin\b|\bgsp\s+form\s+a\b|\beur\.?1\b"],
        "supporting": [r"\bmanufacture[rd]\s+in\b", r"\bcountry\s+of\s+origin\b",
                       r"\bexporter\b", r"\bconsignee\b", r"\btariff\b"],
        "min_required": 1,
    },
    {
        "name": "Air Waybill",
        "code": "AWB",
        "required": [r"\bair\s+waybill\b|\b(awb|hawb|mawb)\b"],
        "supporting": [r"\bairport\s+of\s+(departure|destination)\b", r"\bcarrier\b",
                       r"\bflight\b", r"\bcharges?\b"],
        "min_required": 1,
    },
    {
        "name": "ISF Filing",
        "code": "ISF",
        "required": [r"\bisf\b|\bimporter\s+security\s+filing\b|\b10\s*\+\s*2\b"],
        "supporting": [r"\bseller\b", r"\bbuyer\b", r"\bmanufacturer\b", r"\bship\s+to\s+party\b",
                       r"\bcontainer\s+stuffing\b", r"\bconsolidator\b"],
        "min_required": 1,
    },
    {
        "name": "Customs Declaration",
        "code": "CF7501",
        "required": [r"\bcustoms\s+(entry|declaration)\b|\b(cf-?7501|entry\s+summary)\b|\bsad\b"],
        "supporting": [r"\bentry\s+(type|number)\b", r"\bduty\b", r"\bimporter\b", r"\bhts\b"],
        "min_required": 1,
    },
    {
        "name": "Phytosanitary Certificate",
        "code": "PHYTO",
        "required": [r"\bphytosanitary\b|\bplant\s+health\s+certificate\b"],
        "supporting": [r"\bquarantine\b", r"\btreatment\b", r"\bpest\b", r"\bexporting\s+country\b"],
        "min_required": 1,
    },
    {
        "name": "Safety Data Sheet",
        "code": "SDS",
        "required": [r"\b(safety\s+data\s+sheet|sds|msds)\b"],
        "supporting": [r"\bhazardous\b|\bdangerous\b", r"\bun\s*number\b", r"\bflammable\b",
                       r"\btoxic\b", r"\bhandling\b"],
        "min_required": 1,
    },
    {
        "name": "Insurance Certificate",
        "code": "INS",
        "required": [r"\binsurance\s+certificate\b|\bcargo\s+insurance\b"],
        "supporting": [r"\bpolicyholder\b|\bassured\b", r"\bpremium\b", r"\bcoverage\b",
                       r"\binsured\s+value\b", r"\bclaim\b"],
        "min_required": 1,
    },
    {
        "name": "Letter of Credit",
        "code": "LC",
        "required": [r"\bletter\s+of\s+credit\b|\bl/?c\s+number\b|\bdocumentary\s+credit\b"],
        "supporting": [r"\bbeneficiary\b", r"\bapplicant\b", r"\bissuing\s+bank\b",
                       r"\bpresentation\b", r"\bdraft\b"],
        "min_required": 1,
    },
    {
        "name": "Freight Manifest",
        "code": "MAN",
        "required": [r"\bfreight\s+manifest\b|\bcargo\s+manifest\b|\bmanifest\b"],
        "supporting": [r"\bvessel\b", r"\bvoyage\b", r"\bmaster\b", r"\bcontainer\b"],
        "min_required": 1,
    },
    {
        "name": "Dangerous Goods Declaration",
        "code": "DGD",
        "required": [r"\bdangerous\s+goods\s+declaration\b|\bdgd\b|\bimdg\b"],
        "supporting": [r"\bun\s*number\b|\bun\d{4}\b", r"\bpacking\s+group\b",
                       r"\bflash\s+point\b", r"\bclass\s+\d\b"],
        "min_required": 1,
    },
    {
        "name": "Fumigation Certificate",
        "code": "FUM",
        "required": [r"\bfumigation\s+certificate\b|\bfumigation\s+(report|statement)\b"],
        "supporting": [r"\bmethyl\s+bromide\b|\bphosphine\b", r"\btreatment\b", r"\bdosage\b"],
        "min_required": 1,
    },
    {
        "name": "Arrival Notice",
        "code": "AN",
        "required": [r"\barrival\s+notice\b"],
        "supporting": [r"\bnotify\s+party\b", r"\bconsignee\b", r"\barrival\s+date\b"],
        "min_required": 1,
    },
    {
        "name": "Delivery Order",
        "code": "DO",
        "required": [r"\bdelivery\s+order\b"],
        "supporting": [r"\bconsignee\b", r"\bcontainer\b", r"\brelease\b"],
        "min_required": 1,
    },
    {
        "name": "Telex Release / Sea Waybill",
        "code": "TR",
        "required": [r"\btelex\s+release\b|\bsea\s+waybill\b|\bexpress\s+release\b"],
        "supporting": [r"\boriginal\s+b/?l\b", r"\bshipper\b", r"\bconsignee\b"],
        "min_required": 1,
    },
    {
        "name": "Pro Forma Invoice",
        "code": "PFI",
        "required": [r"\bpro\s*forma\s+invoice\b"],
        "supporting": [r"\bquotation\b|\bquote\b", r"\bpayment\s+terms\b", r"\bshipment\b"],
        "min_required": 1,
    },
    {
        "name": "Shipper's Letter of Instruction",
        "code": "SLI",
        "required": [r"\bshipper['\s]+s?\s+letter\s+of\s+instruction\b|\bsli\b"],
        "supporting": [r"\bforwarder\b", r"\bfreight\b", r"\bexport\b"],
        "min_required": 1,
    },
    {
        "name": "Weight / Inspection Certificate",
        "code": "WIC",
        "required": [r"\b(weight|inspection)\s+certificate\b"],
        "supporting": [r"\bnet\s+weight\b", r"\bgross\s+weight\b", r"\binspector\b",
                       r"\bcertif(y|ied)\b"],
        "min_required": 1,
    },
]

# Hard format signals — structural evidence this is a trade document
HARD_SIGNAL_PATTERNS = [
    (r"\b\d{4,10}\.\d{2,4}\b",           "HTS/HS code format", 2.0),
    (r"\b[A-Z]{4}\d{7}\b",               "Container number (IICL format)", 2.5),
    (r"\b[A-Z]{3,4}\d{6,12}\b",          "AWB/BOL number format", 1.5),
    (r"\b[A-Z]{2}\s?\d{9}\b",            "Tracking/reference number", 1.0),
    (r"\bscac\b|\b[A-Z]{4}\s+\d{4,}\b",  "SCAC code or manifest ref", 1.5),
    (r"\b\d{1,3}\.\d{3}\.\d{3}/\d{4}-\d{2}\b", "Brazilian CNPJ format", 1.0),
    (r"\b\d{2,3}[\s.]\d{3}[\s.]\d{3}\b", "MID/registration format", 0.5),
    (r"\b(USD|EUR|GBP|CNY|JPY|AUD|CAD)\s*[\d,]+\.\d{2}\b", "Trade currency amount", 2.0),
    (r"\b\d+(\.\d+)?\s*(kg|kgs|mt|cbm|cft|lbs?|g)\b", "Weight/volume units", 1.5),
    (r"\bincoterms?\s*(20\d{2})?\b",      "Incoterms reference", 2.5),
    (r"\b(auckland|shanghai|rotterdam|hamburg|singapore|busan|los angeles|"
     r"long beach|new york|miami|savannah|houston|ningbo|guangzhou|"
     r"shenzhen|hong kong|dubai|antwerp|felixstowe|le havre|genoa)\b",
     "Major port name", 2.0),
    (r"\b(china|prc|usa|united states|germany|japan|south korea|taiwan|"
     r"vietnam|india|malaysia|thailand|indonesia|mexico|brazil|canada|"
     r"netherlands|belgium|france|italy|spain|turkey|iran|russia|ukraine|"
     r"north korea|myanmar|cuba|venezuela|syria|sudan)\b.*\b(port|origin|"
     r"destination|export|import|manufacture)\b",
     "Country + trade context", 1.5),
    (r"\b(container|vessel|flight|truck)\s+(no\.?|number|id)\s*[:\-]?\s*[A-Z0-9]+",
     "Transport ID reference", 2.0),
    (r"\b(net\s+weight|gross\s+weight|tare\s+weight)\b", "Weight fields", 1.5),
    (r"\bpayment\s+terms?\s*[:\-]?\s*(t/t|l/c|d/p|d/a|o/a|cad|wire|swift)",
     "Trade payment terms", 2.5),
    (r"\bswift\s*(code)?\s*[:\-]?\s*[A-Z]{6,11}\b", "SWIFT code", 1.5),
]

# Anti-pattern definitions — each: (pattern, category, weight, description)
ANTI_PATTERNS = [
    # ---- RESUME / CV ----
    (r"\b(resume|curriculum\s+vitae|cv)\b",             "RESUME", 8.0, "Resume/CV title"),
    (r"\bwork\s+experience\b",                           "RESUME", 6.0, "Work experience section"),
    (r"\bprofessional\s+experience\b",                   "RESUME", 6.0, "Professional experience section"),
    (r"\beducation\b.*\b(university|college|school)\b",  "RESUME", 5.0, "Education section"),
    (r"\bskills?\s*(summary|section|:\s*\n)\b",          "RESUME", 4.0, "Skills section"),
    (r"\b(references?\s+available|reference\s+list)\b",  "RESUME", 5.0, "References section"),
    (r"\blinkedin\.com\b",                               "RESUME", 5.0, "LinkedIn URL"),
    (r"\b(gpa|grade\s+point\s+average)\s*[:\-]?\s*\d",  "RESUME", 5.0, "GPA"),
    (r"\b(bachelor|master|phd|doctorate|mba|bsc|ba\b|msc)\b.*\b(degree|of|in|from)\b",
                                                         "RESUME", 4.0, "Academic degree"),
    (r"\bsummary\s+of\s+qualifications\b",               "RESUME", 5.0, "Qualifications summary"),
    (r"\bcareer\s+(objective|summary|goal)\b",           "RESUME", 5.0, "Career objective"),
    (r"\bcover\s+letter\b",                              "RESUME", 6.0, "Cover letter"),
    (r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{4}\s*[-–]\s*(present|current|now)\b",
                                                         "RESUME", 3.0, "Employment date range"),
    (r"\bhired\s+by\b|\bjob\s+(title|position|description)\b", "RESUME", 4.0, "Job listing language"),

    # ---- SHOPPING / GROCERY LIST ----
    (r"\bshopping\s+list\b",                             "SHOPPING", 8.0, "Shopping list title"),
    (r"\bgrocery\s+(list|store|shopping)\b",             "SHOPPING", 8.0, "Grocery reference"),
    (r"\b(aisle|checkout|cart|cashier|coupon|sale\s+price)\b", "SHOPPING", 5.0, "Retail store language"),
    (r"\b(milk|bread|eggs|butter|cheese|yogurt|flour|sugar|cereal|"
     r"pasta|rice|beans|chicken|beef|pork|salmon|tuna|apples?|bananas?|"
     r"oranges?|tomatoes?|lettuce|spinach|kale|broccoli|carrots?)\b",
                                                         "SHOPPING", 1.5, "Grocery food items"),
    (r"\b(dishwasher|laundry|soap|shampoo|toothpaste|toilet\s+paper|"
     r"paper\s+towels?|cleaning\s+supplies?)\b",         "SHOPPING", 2.0, "Household supplies"),
    (r"(\d+\s*x\s*)?(milk|bread|eggs|butter|cheese|yogurt)\b", "SHOPPING", 3.0, "Quantity + grocery"),

    # ---- ACADEMIC / RESEARCH ----
    (r"\babstract\b.*\b(this\s+(paper|study|research)|we\s+(present|propose|examine))\b",
                                                         "ACADEMIC", 6.0, "Research abstract"),
    (r"\bbibliography\b|\breferences?\s*\n\s*\[?\d+\]",  "ACADEMIC", 5.0, "Bibliography/references"),
    (r"\bhypothesis\b",                                  "ACADEMIC", 4.0, "Hypothesis"),
    (r"\bmethodology\b|\bresearch\s+method\b",           "ACADEMIC", 3.0, "Methodology section"),
    (r"\bthesis\s+(statement|proposal|defense|committee)\b", "ACADEMIC", 5.0, "Thesis"),
    (r"\b(professor|dr\.|lecturer|supervisor|advisor)\b.*\b(department|faculty|university)\b",
                                                         "ACADEMIC", 4.0, "Academic faculty"),
    (r"\b(semester|quarter|term|course|credit\s+hours?|enrollment)\b", "ACADEMIC", 3.0, "Academic terms"),
    (r"\bliterature\s+review\b",                         "ACADEMIC", 4.0, "Literature review"),
    (r"\bp[-\s]value\b|\bstatistical\s+significance\b",  "ACADEMIC", 4.0, "Statistical analysis"),
    (r"\bpeer[-\s]reviewed?\b",                          "ACADEMIC", 3.0, "Peer review"),
    (r"\b(doi|arxiv|pubmed|journal\s+of|proceedings\s+of)\b", "ACADEMIC", 3.0, "Academic publication"),

    # ---- MEDICAL / CLINICAL ----
    (r"\b(diagnosis|diagnoses)\b",                       "MEDICAL", 6.0, "Medical diagnosis"),
    (r"\bprescription\b",                                "MEDICAL", 6.0, "Prescription"),
    (r"\b(patient|patient\s+(id|name|dob))\b",           "MEDICAL", 5.0, "Patient record"),
    (r"\bdosage\b|\b(mg|mcg)\s+per\s+(day|dose|kg)\b",   "MEDICAL", 4.0, "Medical dosage"),
    (r"\b(physician|doctor|md|rn|nurse|surgeon)\b",      "MEDICAL", 4.0, "Medical professional"),
    (r"\b(icd[-\s]?\d+|cpt\s+code|ndc\s+code)\b",        "MEDICAL", 5.0, "Medical billing codes"),
    (r"\b(hospital|clinic|medical\s+center|emergency\s+room)\b", "MEDICAL", 3.0, "Medical facility"),
    (r"\bhealth\s+insurance\b|\bmedical\s+coverage\b",   "MEDICAL", 3.0, "Health insurance"),
    (r"\b(allergy|symptom|treatment\s+plan|medication|antibiotic)\b", "MEDICAL", 3.0, "Medical terms"),
    (r"\bblood\s+(pressure|type|test|count)\b",          "MEDICAL", 4.0, "Medical test"),

    # ---- LEGAL (non-trade) ----
    (r"\b(plaintiff|defendant|petitioner|respondent)\b", "LEGAL", 5.0, "Litigation party"),
    (r"\b(attorney|counsel|esquire|barrister|solicitor)\b.*\b(for\s+the|representing)\b",
                                                         "LEGAL", 4.0, "Legal representation"),
    (r"\b(subpoena|deposition|affidavit|injunction|motion\s+to)\b", "LEGAL", 5.0, "Legal filing"),
    (r"\b(court\s+of|supreme\s+court|district\s+court|appellate\s+court)\b", "LEGAL", 5.0, "Court reference"),
    (r"\bjury\b|\bjudge\b|\bjudgment\b",                 "LEGAL", 3.0, "Court proceedings"),
    (r"\bhereby\s+(declare|certify|grant|assign)\b",     "LEGAL", 1.5, "Legal declaration — could be trade too"),

    # ---- PERSONAL CORRESPONDENCE ----
    (r"\bdear\s+(sir|madam|mr|ms|mrs|dr|prof|friend|mom|dad|[a-z]{2,}),",
                                                         "PERSONAL", 5.0, "Personal salutation"),
    (r"\b(yours\s+(sincerely|truly|faithfully)|best\s+regards|love,|warm\s+regards)\b",
                                                         "PERSONAL", 4.0, "Personal closing"),
    (r"\bhow\s+are\s+you\b|\bi\s+hope\s+this\s+(email|letter|message)\s+finds\b",
                                                         "PERSONAL", 4.0, "Personal greeting"),
    (r"\b(birthday|anniversary|wedding|graduation|congratulations)\b", "PERSONAL", 3.0, "Personal event"),

    # ---- FINANCIAL / BANKING (non-trade) ----
    (r"\b(bank\s+statement|account\s+balance|transaction\s+history)\b", "FINANCIAL", 4.0, "Bank statement"),
    (r"\b(mortgage|loan\s+application|credit\s+score|fico)\b", "FINANCIAL", 4.0, "Consumer finance"),
    (r"\b(w-?2|1099|tax\s+return|irs\s+form|schedule\s+[a-z])\b", "FINANCIAL", 4.0, "Tax document"),
    (r"\b(pay\s*stub|payroll|salary|wages|401k|retirement\s+plan)\b", "FINANCIAL", 3.0, "Payroll document"),

    # ---- REAL ESTATE ----
    (r"\b(lease\s+agreement|rental\s+(agreement|property)|landlord|tenant|eviction)\b",
                                                         "REAL_ESTATE", 5.0, "Lease/rental"),
    (r"\b(property\s+(address|value|tax)|square\s+feet|bedroom|bathroom)\b",
                                                         "REAL_ESTATE", 4.0, "Property description"),
    (r"\b(closing\s+disclosure|title\s+(insurance|company)|escrow)\b",
                                                         "REAL_ESTATE", 5.0, "Real estate transaction"),

    # ---- SOCIAL MEDIA / MARKETING ----
    (r"\b(hashtag|@\w+|retweet|follow\s+us|subscribe|like\s+and\s+share)\b",
                                                         "SOCIAL", 5.0, "Social media content"),
    (r"\b(click\s+here|buy\s+now|limited\s+time|offer\s+expires|promo\s+code)\b",
                                                         "MARKETING", 4.0, "Marketing language"),

    # ---- FOOD RECIPE ----
    (r"\b(recipe|ingredients?|instructions?)\b.*\b(cup|tablespoon|teaspoon|ounce|pound)\b",
                                                         "RECIPE", 5.0, "Recipe with measurements"),
    (r"\bpreheat\s+(the\s+)?oven\b",                     "RECIPE", 5.0, "Recipe instruction"),
    (r"\b(bake|roast|sauté|simmer|stir\s+in|fold\s+in|add\s+the)\b.*\b(minutes?|hours?)\b",
                                                         "RECIPE", 4.0, "Cooking instruction"),
]

# Human-readable category names for rejection messages
CATEGORY_NAMES = {
    "RESUME":      ("resume or CV", "PortGuard screens shipping and customs documents only. Please upload a Bill of Lading, Commercial Invoice, Packing List, or similar trade document."),
    "SHOPPING":    ("shopping or grocery list", "PortGuard processes trade and customs documentation only."),
    "ACADEMIC":    ("academic or research document", "Please upload a shipping document such as a Bill of Lading, Commercial Invoice, or Packing List."),
    "MEDICAL":     ("medical record or clinical document", "PortGuard processes trade and customs documentation only."),
    "LEGAL":       ("legal filing or court document", "PortGuard screens shipping and customs documents only."),
    "PERSONAL":    ("personal correspondence", "Please upload a trade or customs document."),
    "FINANCIAL":   ("personal financial document", "PortGuard processes trade and customs documentation only."),
    "REAL_ESTATE": ("real estate document", "PortGuard processes trade and customs documentation only."),
    "SOCIAL":      ("social media or marketing content", "PortGuard processes trade and customs documentation only."),
    "MARKETING":   ("marketing or promotional content", "PortGuard processes trade and customs documentation only."),
    "RECIPE":      ("recipe or cooking instructions", "PortGuard processes trade and customs documentation only."),
}


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class DocumentClassifier:
    """
    Hardened multi-signal document classifier.

    Usage:
        clf = DocumentClassifier()
        result = clf.classify(text)
        if not result.accepted:
            return {"error": result.rejection_reason}
    """

    # Thresholds
    HIGH_THRESHOLD   = 0.75
    MEDIUM_THRESHOLD = 0.50
    LOW_THRESHOLD    = 0.35
    ANTI_HARD_REJECT = 12.0   # anti-score above this → definitive reject regardless of pro-score
    ANTI_DOMINATE    = 0.60   # anti/(pro+anti) ratio above this → reject

    def __init__(self):
        # Pre-compile all patterns for speed
        flags = re.IGNORECASE | re.MULTILINE

        self._pro_compiled = [
            (re.compile(pat, flags), weight)
            for pat, weight in PRO_SHIPPING_TERMS
        ]
        self._fingerprints_compiled = []
        for fp in DOC_TYPE_FINGERPRINTS:
            self._fingerprints_compiled.append({
                "name": fp["name"],
                "code": fp["code"],
                "required": [re.compile(p, flags) for p in fp["required"]],
                "supporting": [re.compile(p, flags) for p in fp["supporting"]],
                "min_required": fp["min_required"],
            })
        self._hard_compiled = [
            (re.compile(pat, flags), desc, weight)
            for pat, desc, weight in HARD_SIGNAL_PATTERNS
        ]
        self._anti_compiled = [
            (re.compile(pat, flags), cat, weight, desc)
            for pat, cat, weight, desc in ANTI_PATTERNS
        ]

    def classify(self, text: str) -> ClassificationResult:
        """Main entry point. Returns a ClassificationResult."""
        text = self._normalize(text)

        # --- Layer 1: Pro-shipping vocabulary ---
        pro_score, pro_signals = self._score_pro(text)

        # --- Layer 2: Document type fingerprinting ---
        doc_type, doc_type_code, fp_bonus = self._fingerprint(text)

        # --- Layer 3: Hard format signals ---
        hard_score, hard_signals = self._score_hard_signals(text)

        # --- Layer 4: Anti-pattern detection ---
        anti_score, anti_matches, dominant_category = self._score_anti(text)

        # --- Layer 5: Composite decision ---
        return self._decide(
            pro_score, pro_signals,
            fp_bonus, doc_type, doc_type_code,
            hard_score, hard_signals,
            anti_score, anti_matches, dominant_category,
        )

    # ------------------------------------------------------------------
    # Internal scoring methods
    # ------------------------------------------------------------------

    def _normalize(self, text: str) -> str:
        """Lowercase, collapse whitespace, keep structure."""
        # Preserve newlines (some patterns rely on them) but collapse runs
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _score_pro(self, text: str):
        """Score pro-shipping vocabulary. Returns (score, matched_terms)."""
        score = 0.0
        matched = []
        for pattern, weight in self._pro_compiled:
            if pattern.search(text):
                score += weight
                matched.append(pattern.pattern)
        return score, matched

    def _fingerprint(self, text: str):
        """
        Match against known document type fingerprints.
        Returns (doc_type_name, doc_type_code, bonus_score).
        """
        best_name = None
        best_code = None
        best_score = 0.0

        for fp in self._fingerprints_compiled:
            # Check required patterns
            req_hits = sum(1 for p in fp["required"] if p.search(text))
            if req_hits < fp["min_required"]:
                continue
            # Score supporting patterns
            sup_hits = sum(1 for p in fp["supporting"] if p.search(text))
            score = req_hits * 5.0 + sup_hits * 1.5
            if score > best_score:
                best_score = score
                best_name = fp["name"]
                best_code = fp["code"]

        return best_name, best_code, best_score

    def _score_hard_signals(self, text: str):
        """Score structural trade format signals. Returns (score, matched)."""
        score = 0.0
        matched = []
        for pattern, desc, weight in self._hard_compiled:
            if pattern.search(text):
                score += weight
                matched.append(desc)
        return score, matched

    def _score_anti(self, text: str):
        """
        Score anti-patterns. Returns (total_score, matched_list, dominant_category).
        dominant_category is the highest-scoring anti-pattern category.
        """
        score = 0.0
        matched = []
        category_scores = {}

        for pattern, cat, weight, desc in self._anti_compiled:
            if pattern.search(text):
                score += weight
                matched.append({"category": cat, "weight": weight, "description": desc})
                category_scores[cat] = category_scores.get(cat, 0.0) + weight

        dominant = max(category_scores, key=category_scores.get) if category_scores else None
        return score, matched, dominant

    def _decide(
        self,
        pro_score, pro_signals,
        fp_bonus, doc_type, doc_type_code,
        hard_score, hard_signals,
        anti_score, anti_matches, dominant_category,
    ) -> ClassificationResult:
        """Composite decision logic."""

        # Total positive evidence
        total_pro = pro_score + fp_bonus + hard_score

        # --- Hard reject conditions ---

        # 1) Anti-patterns dominate overwhelmingly
        if anti_score >= self.ANTI_HARD_REJECT:
            # Still give benefit of doubt if pro evidence is very strong + doc type identified
            if not (doc_type and total_pro >= 30.0):
                return self._make_rejection(
                    anti_score, anti_matches, dominant_category,
                    pro_score, hard_signals, doc_type, doc_type_code,
                    total_pro, fp_bonus
                )

        # 2) Anti dominates relative to pro
        if total_pro > 0:
            anti_ratio = anti_score / (total_pro + anti_score)
        else:
            anti_ratio = 1.0

        if anti_ratio >= self.ANTI_DOMINATE and anti_score >= 6.0:
            # Unless we have a strong doc-type fingerprint match
            if not (doc_type and fp_bonus >= 8.0):
                return self._make_rejection(
                    anti_score, anti_matches, dominant_category,
                    pro_score, hard_signals, doc_type, doc_type_code,
                    total_pro, fp_bonus
                )

        # 3) Essentially no positive evidence at all
        # Note: threshold is kept low (2.0) so that sparse but
        # legitimately-shipping documents can still scrape through
        # with a LOW confidence accept. Anti-pattern dominance check above
        # already handles the troll cases.
        if total_pro < 2.0 and not doc_type:
            # Build a generic rejection
            reason = (
                "This document does not appear to contain shipping or customs content. "
                "Please upload a trade document such as a Bill of Lading, Commercial Invoice, "
                "Packing List, Certificate of Origin, or Air Waybill."
            )
            return ClassificationResult(
                accepted=False,
                confidence=0.0,
                confidence_label="REJECTED",
                detected_doc_type=None,
                detected_doc_type_code=None,
                rejection_reason=reason,
                rejection_category="GENERIC",
                anti_pattern_matches=anti_matches,
                pro_signals=[],
                hard_signals=hard_signals,
                warning=None,
                raw_scores={"pro": pro_score, "fp_bonus": fp_bonus,
                            "hard": hard_score, "anti": anti_score},
            )

        # --- Compute normalized confidence ---
        # Sigmoid-like normalization: map total_pro to [0,1]
        # Scale point: ~12 pro-points → 0.50 confidence; 20+ → high confidence
        # This allows sparse but legitimate shipping notices to reach LOW/MEDIUM
        raw_conf = 1.0 - (1.0 / (1.0 + math.exp((total_pro - 12.0) / 6.0)))
        # Anti penalty — softly reduces confidence
        anti_penalty = min(0.40, anti_score * 0.02)
        confidence = max(0.0, min(1.0, raw_conf - anti_penalty))

        # If we have a clear doc type fingerprint, floor confidence at 0.40
        if doc_type and confidence < 0.40:
            confidence = 0.40

        # Label
        if confidence >= self.HIGH_THRESHOLD:
            label = "HIGH"
        elif confidence >= self.MEDIUM_THRESHOLD:
            label = "MEDIUM"
        elif confidence >= self.LOW_THRESHOLD:
            label = "LOW"
        else:
            # Below low threshold AND no doc type → reject
            if not doc_type:
                return self._make_rejection(
                    anti_score, anti_matches, dominant_category,
                    pro_score, hard_signals, doc_type, doc_type_code,
                    total_pro, fp_bonus
                )
            label = "LOW"

        # Warning for LOW confidence accepts
        warning = None
        if label == "LOW":
            warning = (
                f"Document type could not be confirmed with high confidence "
                f"(confidence: {confidence:.0%}). Screening results may be limited. "
                f"{'Detected as: ' + doc_type if doc_type else 'Document type unknown.'}"
            )
        elif anti_matches:
            # Soft warning if anti-patterns fired but we're still accepting
            top_anti = sorted(anti_matches, key=lambda x: x["weight"], reverse=True)[:2]
            descs = ", ".join(x["description"] for x in top_anti)
            warning = f"Some non-shipping content patterns detected ({descs}). Review recommended."

        return ClassificationResult(
            accepted=True,
            confidence=confidence,
            confidence_label=label,
            detected_doc_type=doc_type,
            detected_doc_type_code=doc_type_code,
            rejection_reason=None,
            rejection_category=None,
            anti_pattern_matches=anti_matches,
            pro_signals=pro_signals[:10],  # top 10 to keep payload small
            hard_signals=hard_signals,
            warning=warning,
            raw_scores={"pro": pro_score, "fp_bonus": fp_bonus,
                        "hard": hard_score, "anti": anti_score,
                        "total_pro": total_pro, "confidence_raw": raw_conf},
        )

    def _make_rejection(
        self, anti_score, anti_matches, dominant_category,
        pro_score, hard_signals, doc_type, doc_type_code,
        total_pro, fp_bonus,
    ) -> ClassificationResult:
        """Build a rich rejection result."""
        if dominant_category and dominant_category in CATEGORY_NAMES:
            detected_as, guidance = CATEGORY_NAMES[dominant_category]
            reason = (
                f"This appears to be a {detected_as}. {guidance}"
            )
        elif dominant_category:
            reason = (
                f"This document does not appear to be a shipping or customs document "
                f"(detected pattern: {dominant_category.lower().replace('_', ' ')}). "
                f"Please upload a trade document such as a Bill of Lading, Commercial Invoice, "
                f"or Packing List."
            )
        else:
            reason = (
                "This document does not contain sufficient shipping or customs content "
                "to be processed. Please upload a trade document such as a Bill of Lading, "
                "Commercial Invoice, Packing List, Certificate of Origin, or Air Waybill."
            )

        return ClassificationResult(
            accepted=False,
            confidence=0.0,
            confidence_label="REJECTED",
            detected_doc_type=None,
            detected_doc_type_code=None,
            rejection_reason=reason,
            rejection_category=dominant_category or "GENERIC",
            anti_pattern_matches=anti_matches,
            pro_signals=[],
            hard_signals=hard_signals,
            warning=None,
            raw_scores={"pro": pro_score, "fp_bonus": fp_bonus,
                        "hard": 0.0, "anti": anti_score, "total_pro": total_pro},
        )


# ---------------------------------------------------------------------------
# Convenience function — drop-in replacement for existing classify() calls
# ---------------------------------------------------------------------------

_classifier = None

def classify_document(text: str) -> dict:
    """
    Drop-in replacement. Returns a dict compatible with existing PortGuard usage:
    {
        "accepted": bool,
        "confidence": float,
        "confidence_label": "HIGH"|"MEDIUM"|"LOW"|"REJECTED",
        "detected_doc_type": str|None,
        "detected_doc_type_code": str|None,
        "rejection_reason": str|None,
        "rejection_category": str|None,
        "warning": str|None,
        "anti_pattern_matches": [...],
        "pro_signals": [...],
        "hard_signals": [...],
        "raw_scores": {...},
    }
    """
    global _classifier
    if _classifier is None:
        _classifier = DocumentClassifier()
    result = _classifier.classify(text)
    return {
        "accepted": result.accepted,
        "confidence": result.confidence,
        "confidence_label": result.confidence_label,
        "detected_doc_type": result.detected_doc_type,
        "detected_doc_type_code": result.detected_doc_type_code,
        "rejection_reason": result.rejection_reason,
        "rejection_category": result.rejection_category,
        "warning": result.warning,
        "anti_pattern_matches": result.anti_pattern_matches,
        "pro_signals": result.pro_signals,
        "hard_signals": result.hard_signals,
        "raw_scores": result.raw_scores,
    }


# ---------------------------------------------------------------------------
# Self-test — run with: python document_classifier.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    clf = DocumentClassifier()

    tests = [
        # (label, expected_accepted, text)
        ("Bill of Lading",          True,
         """BILL OF LADING
Shipper: Shenzhen Tech Export Co. Ltd
Consignee: ACME Import Corp., New York, USA
Notify Party: Same as consignee
Port of Loading: Yantian, China
Port of Discharge: Los Angeles, USA
Vessel: MSC AURORA  Voyage: 043E
Container No.: TCKU3456789  Seal: CN9823
Description of Goods: Electronic components, HTS 8542.31.0001
Gross Weight: 2,450 kgs  CBM: 14.2
Incoterms 2020: FOB Shenzhen
Total Invoice Value: USD 87,450.00"""),

        ("Commercial Invoice",       True,
         """COMMERCIAL INVOICE
Invoice No.: CI-2024-00783
Seller: Guangdong Manufacturing Ltd, Guangzhou, China
Buyer: Pacific Imports LLC, Miami, FL
Country of Origin: China
HTS Code: 6110.20.2075
Description: Cotton knit sweaters, 500 pcs
Unit Price: USD 12.50   Total Value: USD 6,250.00
Payment Terms: T/T 30 days
Incoterms: CIF Miami"""),

        ("Resume — REJECT",          False,
         """JOHN SMITH
john.smith@email.com | LinkedIn: linkedin.com/in/johnsmith

PROFESSIONAL EXPERIENCE
Software Engineer — Google, Mountain View, CA (Jan 2020 – Present)
Senior Developer — Microsoft, Seattle, WA (Mar 2017 – Dec 2019)

EDUCATION
Bachelor of Science in Computer Science, MIT, GPA: 3.9

SKILLS
Python, Java, C++, Machine Learning, Docker

REFERENCES AVAILABLE UPON REQUEST"""),

        ("Shopping list — REJECT",   False,
         """GROCERY LIST
- Milk (2%)  x2
- Bread (whole wheat)
- Eggs (dozen)
- Butter (unsalted)
- Apples x6
- Broccoli
- Chicken breast 2 lbs
- Laundry detergent
- Dish soap
Don't forget coupon for cereal!"""),

        ("Research paper — REJECT",  False,
         """Abstract
This paper presents a novel methodology for analyzing deep learning model robustness.
We propose a hypothesis that adversarial training improves generalization across domains.

Literature Review
Previous work by Smith et al. [1] demonstrated peer-reviewed evidence of...

Bibliography
[1] Smith, J. (2023). Adversarial training. Journal of Machine Learning, 14(2).
[2] Doe, R. (2022). Neural networks. Proceedings of ICML."""),

        ("Medical record — REJECT",  False,
         """PATIENT RECORD
Patient ID: 00234-B
Patient Name: Jane Doe  DOB: 04/15/1985
Physician: Dr. Sarah Johnson, MD

Diagnosis: Hypertension, ICD-10 I10
Prescription: Lisinopril 10mg — dosage: 1 tablet per day
Blood pressure: 145/92 mmHg
Next appointment: schedule follow-up in 3 months"""),

        ("Packing list",             True,
         """PACKING LIST
Exporter: Vietnam Garment Co.
Consignee: Fashion Forward Inc., Chicago
Invoice No.: PL-VGC-20240312
Gross Weight: 890 kgs  Net Weight: 812 kgs
No. of Cartons: 48  CBM: 6.4
Marks: FF/CHI/0312
Item 1: Men's cotton shirts (HTS 6205.20) – 240 pcs – 12 cartons
Item 2: Women's blouses (HTS 6206.10) – 300 pcs – 18 cartons"""),

        ("Cover letter — REJECT",    False,
         """Dear Hiring Manager,

I am writing to express my strong interest in the Software Engineer position
at your company. With a Bachelor's degree in Computer Science and 5 years of
professional experience, I am confident in my ability to contribute to your team.

During my career objective period at my previous employer, I developed skills
in Python and React. My references are available upon request.

Yours sincerely,
Jane Doe"""),

        ("Recipe — REJECT",         False,
         """Classic Chocolate Chip Cookies

Ingredients:
- 2 1/4 cups all-purpose flour
- 1 teaspoon baking soda
- 2 large eggs
- 1 cup butter, softened
- 3/4 cup granulated sugar

Instructions:
Preheat the oven to 375°F. Mix butter and sugar. Add eggs.
Bake for 9-11 minutes until golden brown."""),

        ("ISF Filing",               True,
         """IMPORTER SECURITY FILING (ISF 10+2)
Importer of Record: West Coast Imports LLC
Seller: Ningbo Trading Co., China
Buyer: West Coast Imports LLC
Manufacturer: Ningbo Manufacturing Plant, Zhejiang, CN
Ship to Party: LA Distribution Center
Container Stuffing Location: Ningbo CFS Yard
Consolidator: Pacific Freight Services
HTS: 8471.30.0100  Country of Origin: CN
SCAC: MSCU  Booking No.: MSC-BK-20241109"""),

        ("Ambiguous — Low conf accept", True,
         """SHIPMENT NOTICE
Cargo from Shanghai to Rotterdam
Weight approximately 500 kg
Contains industrial equipment
Vessel expected arrival next week"""),
    ]

    print("=" * 70)
    print("PortGuard DocumentClassifier — Self-Test")
    print("=" * 70)
    passed = 0
    failed = 0
    for label, expected, text in tests:
        result = clf.classify(text)
        ok = result.accepted == expected
        status = "✅ PASS" if ok else "❌ FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"\n{status}  [{label}]")
        print(f"  accepted={result.accepted}  expected={expected}")
        print(f"  confidence={result.confidence:.2f} ({result.confidence_label})")
        if result.detected_doc_type:
            print(f"  doc_type={result.detected_doc_type} ({result.detected_doc_type_code})")
        if result.rejection_reason:
            print(f"  rejection: {result.rejection_reason}")
        if result.warning:
            print(f"  warning: {result.warning}")
        print(f"  scores: {result.raw_scores}")

    print(f"\n{'=' * 70}")
    print(f"Results: {passed}/{len(tests)} passed, {failed} failed")
    print("=" * 70)
