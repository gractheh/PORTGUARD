"""
portguard/agents/document_classifier.py — Public façade for the hardened classifier.

Delegates to document_classifier_hardened.DocumentClassifier.  Import only this
module from external callers (api/app.py, tests, etc.) so the implementation can
be swapped without touching import paths.

Public API
----------
classify_document(text: str) -> dict
    Classifies a single document.  Returns:
    {
        "accepted":              bool,
        "confidence":            float,        # 0.0 – 1.0
        "confidence_label":      str,          # HIGH / MEDIUM / LOW / REJECTED
        "detected_doc_type":     str | None,   # e.g. "Bill of Lading"
        "detected_doc_type_code": str | None,  # e.g. "BOL"
        "rejection_reason":      str | None,
        "rejection_category":    str | None,   # RESUME / SHOPPING / ACADEMIC / …
        "warning":               str | None,
        "anti_pattern_matches":  list,
        "pro_signals":           list,
        "hard_signals":          list,
        "raw_scores":            dict,
    }
"""

from portguard.agents.document_classifier_hardened import (  # noqa: F401
    classify_document,
    DocumentClassifier,
    ClassificationResult,
    DOC_TYPE_FINGERPRINTS,
    ANTI_PATTERNS,
    CATEGORY_NAMES,
)

__all__ = [
    "classify_document",
    "DocumentClassifier",
    "ClassificationResult",
]


# ---------------------------------------------------------------------------
# Self-test — run with: python -m portguard.agents.document_classifier
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        # (label, expected_accepted, text)
        ("Bill of Lading — ACCEPT", True,
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

        ("Commercial Invoice — ACCEPT", True,
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

        ("Packing List — ACCEPT", True,
         """PACKING LIST
Exporter: Vietnam Garment Co.
Consignee: Fashion Forward Inc., Chicago
Invoice No.: PL-VGC-20240312
Gross Weight: 890 kgs  Net Weight: 812 kgs
No. of Cartons: 48  CBM: 6.4
Item 1: Men's cotton shirts (HTS 6205.20) – 240 pcs – 12 cartons
Item 2: Women's blouses (HTS 6206.10) – 300 pcs – 18 cartons"""),

        ("ISF Filing — ACCEPT", True,
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

        ("Sparse Shipment Notice — ACCEPT LOW", True,
         """SHIPMENT NOTICE
Cargo from Shanghai to Rotterdam
Weight approximately 500 kg
Contains industrial equipment
Vessel expected arrival next week"""),

        ("Resume — REJECT", False,
         """JOHN SMITH
john.smith@email.com | LinkedIn: linkedin.com/in/johnsmith

PROFESSIONAL EXPERIENCE
Software Engineer — Google, Mountain View, CA (Jan 2020 – Present)
Senior Developer — Microsoft, Seattle, WA (Mar 2017 – Dec 2019)

EDUCATION
Bachelor of Science in Computer Science, MIT, GPA: 3.9

SKILLS: Python, Java, C++, Machine Learning, Docker

REFERENCES AVAILABLE UPON REQUEST"""),

        ("Shopping List — REJECT", False,
         """GROCERY LIST
- Milk (2%)  x2
- Bread (whole wheat)
- Eggs (dozen)
- Butter (unsalted)
- Apples x6
- Chicken breast 2 lbs
- Laundry detergent
Don't forget coupon for cereal!"""),

        ("Academic Paper — REJECT", False,
         """Abstract
This paper presents a novel methodology for deep learning model robustness.
We propose a hypothesis that adversarial training improves generalization.

Literature Review
Previous work by Smith et al. [1] demonstrated peer-reviewed evidence of...

Bibliography
[1] Smith, J. (2023). Journal of Machine Learning, 14(2).
[2] Doe, R. (2022). Proceedings of ICML."""),

        ("Medical Record — REJECT", False,
         """PATIENT RECORD
Patient ID: 00234-B
Patient Name: Jane Doe  DOB: 04/15/1985
Physician: Dr. Sarah Johnson, MD

Diagnosis: Hypertension, ICD-10 I10
Prescription: Lisinopril 10mg — dosage: 1 tablet per day
Blood pressure: 145/92 mmHg"""),

        ("Recipe — REJECT", False,
         """Classic Chocolate Chip Cookies
Ingredients:
- 2 1/4 cups all-purpose flour
- 1 cup butter, softened
- 3/4 cup granulated sugar
- 2 large eggs

Instructions:
Preheat the oven to 375°F. Mix butter and sugar. Add eggs.
Bake for 9-11 minutes until golden brown."""),

        ("Cover Letter — REJECT", False,
         """Dear Hiring Manager,

I am writing to express my strong interest in the Software Engineer position
at your company. With a Bachelor's degree in Computer Science and 5 years of
professional experience, I am confident in my ability to contribute.

During my career objective period at my previous employer, I developed skills
in Python and React. My references are available upon request.

Yours sincerely,
Jane Doe"""),
    ]

    print("=" * 70)
    print("PortGuard DocumentClassifier — Self-Test (11 tests)")
    print("=" * 70)
    passed = 0
    failed = 0
    for label, expected, text in tests:
        result = classify_document(text)
        ok = result["accepted"] == expected
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"\n[{status}]  {label}")
        print(f"  accepted={result['accepted']}  expected={expected}")
        print(f"  confidence={result['confidence']:.2f} ({result['confidence_label']})")
        if result["detected_doc_type"]:
            print(f"  doc_type={result['detected_doc_type']} ({result['detected_doc_type_code']})")
        if result["rejection_reason"]:
            print(f"  rejection: {result['rejection_reason']}")
        if result["warning"]:
            print(f"  warning: {result['warning']}")

    print(f"\n{'=' * 70}")
    print(f"Results: {passed}/{len(tests)} passed, {failed} failed")
    print("=" * 70)
    if failed:
        raise SystemExit(1)
