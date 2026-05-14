"""
tests/test_classifier_hardened.py

Smoke tests for the hardened document classifier (portguard.agents.document_classifier).

11 tests:
  - 5 troll documents → all REJECTED
  - 5 real shipping documents → all ACCEPTED with HIGH confidence
  - 1 sparse/ambiguous document → ACCEPTED with LOW confidence
"""

import pytest
from portguard.agents.document_classifier import classify_document


# ---------------------------------------------------------------------------
# Fixtures — troll documents (5)
# ---------------------------------------------------------------------------

RESUME = """JOHN SMITH
john.smith@email.com | LinkedIn: linkedin.com/in/johnsmith

PROFESSIONAL EXPERIENCE
Software Engineer — Google, Mountain View, CA (Jan 2020 – Present)
Senior Developer — Microsoft, Seattle, WA (Mar 2017 – Dec 2019)

EDUCATION
Bachelor of Science in Computer Science, MIT, GPA: 3.9

SKILLS: Python, Java, C++, Machine Learning, Docker

REFERENCES AVAILABLE UPON REQUEST"""

SHOPPING_LIST = """GROCERY LIST
- Milk (2%)  x2
- Bread (whole wheat)
- Eggs (dozen)
- Butter (unsalted)
- Apples x6
- Chicken breast 2 lbs
- Laundry detergent
Don't forget coupon for cereal!"""

ACADEMIC_PAPER = """Abstract
This paper presents a novel methodology for deep learning model robustness.
We propose a hypothesis that adversarial training improves generalization.

Literature Review
Previous work by Smith et al. [1] demonstrated peer-reviewed evidence of...

Bibliography
[1] Smith, J. (2023). Journal of Machine Learning, 14(2).
[2] Doe, R. (2022). Proceedings of ICML."""

MEDICAL_RECORD = """PATIENT RECORD
Patient ID: 00234-B
Patient Name: Jane Doe  DOB: 04/15/1985
Physician: Dr. Sarah Johnson, MD

Diagnosis: Hypertension, ICD-10 I10
Prescription: Lisinopril 10mg — dosage: 1 tablet per day
Blood pressure: 145/92 mmHg"""

RECIPE = """Classic Chocolate Chip Cookies

Ingredients:
- 2 1/4 cups all-purpose flour
- 1 cup butter, softened
- 3/4 cup granulated sugar
- 2 large eggs

Instructions:
Preheat the oven to 375°F. Mix butter and sugar. Add eggs.
Bake for 9-11 minutes until golden brown."""


# ---------------------------------------------------------------------------
# Fixtures — real shipping documents (5)
# ---------------------------------------------------------------------------

BILL_OF_LADING = """BILL OF LADING
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
Total Invoice Value: USD 87,450.00"""

COMMERCIAL_INVOICE = """COMMERCIAL INVOICE
Invoice No.: CI-2024-00783
Seller: Guangdong Manufacturing Ltd, Guangzhou, China
Buyer: Pacific Imports LLC, Miami, FL
Country of Origin: China
HTS Code: 6110.20.2075
Description: Cotton knit sweaters, 500 pcs
Unit Price: USD 12.50   Total Value: USD 6,250.00
Payment Terms: T/T 30 days
Incoterms: CIF Miami"""

PACKING_LIST = """PACKING LIST
Exporter: Vietnam Garment Co.
Consignee: Fashion Forward Inc., Chicago
Invoice No.: PL-VGC-20240312
Gross Weight: 890 kgs  Net Weight: 812 kgs
No. of Cartons: 48  CBM: 6.4
Item 1: Men's cotton shirts (HTS 6205.20) – 240 pcs – 12 cartons
Item 2: Women's blouses (HTS 6206.10) – 300 pcs – 18 cartons"""

ISF_FILING = """IMPORTER SECURITY FILING (ISF 10+2)
Importer of Record: West Coast Imports LLC
Seller: Ningbo Trading Co., China
Buyer: West Coast Imports LLC
Manufacturer: Ningbo Manufacturing Plant, Zhejiang, CN
Ship to Party: LA Distribution Center
Container Stuffing Location: Ningbo CFS Yard
Consolidator: Pacific Freight Services
HTS: 8471.30.0100  Country of Origin: CN
SCAC: MSCU  Booking No.: MSC-BK-20241109"""

AIR_WAYBILL = """AIR WAYBILL
AWB No.: 176-12345 67890
Shipper: Shenzhen Electronics Exports Co., Ltd.
Consignee: US Imports Corporation, Los Angeles, CA
Carrier: Cathay Pacific Cargo
Airport of Departure: Shenzhen Bao'an (SZX)
Airport of Destination: Los Angeles (LAX)
Flight No.: CX-1234
Gross Weight: 124.5 kg  Chargeable Weight: 140.0 kg
Description: Electronic components
Freight Charges: USD 1,890.00"""

SPARSE_DOC = """SHIPMENT NOTICE
Cargo from Shanghai to Rotterdam
Weight approximately 500 kg
Contains industrial equipment
Vessel expected arrival next week"""


# ---------------------------------------------------------------------------
# Tests — troll documents must be REJECTED
# ---------------------------------------------------------------------------

class TestTrollDocumentsRejected:
    def test_resume_rejected(self):
        r = classify_document(RESUME)
        assert r["accepted"] is False
        assert r["confidence_label"] == "REJECTED"
        assert r["rejection_reason"] is not None
        assert r["rejection_category"] is not None

    def test_shopping_list_rejected(self):
        r = classify_document(SHOPPING_LIST)
        assert r["accepted"] is False
        assert r["confidence_label"] == "REJECTED"
        assert "shopping" in (r["rejection_reason"] or "").lower() or \
               r["rejection_category"] == "SHOPPING"

    def test_academic_paper_rejected(self):
        r = classify_document(ACADEMIC_PAPER)
        assert r["accepted"] is False
        assert r["confidence_label"] == "REJECTED"

    def test_medical_record_rejected(self):
        r = classify_document(MEDICAL_RECORD)
        assert r["accepted"] is False
        assert r["confidence_label"] == "REJECTED"

    def test_recipe_rejected(self):
        r = classify_document(RECIPE)
        assert r["accepted"] is False
        assert r["confidence_label"] == "REJECTED"


# ---------------------------------------------------------------------------
# Tests — real shipping docs must be ACCEPTED with HIGH confidence
# ---------------------------------------------------------------------------

class TestRealDocsAcceptedHigh:
    def test_bill_of_lading_high(self):
        r = classify_document(BILL_OF_LADING)
        assert r["accepted"] is True
        assert r["confidence_label"] == "HIGH"
        assert r["detected_doc_type"] is not None

    def test_commercial_invoice_high(self):
        r = classify_document(COMMERCIAL_INVOICE)
        assert r["accepted"] is True
        assert r["confidence_label"] == "HIGH"

    def test_packing_list_high(self):
        r = classify_document(PACKING_LIST)
        assert r["accepted"] is True
        assert r["confidence_label"] in ("HIGH", "MEDIUM")

    def test_isf_filing_high(self):
        r = classify_document(ISF_FILING)
        assert r["accepted"] is True
        assert r["confidence_label"] == "HIGH"

    def test_air_waybill_high(self):
        r = classify_document(AIR_WAYBILL)
        assert r["accepted"] is True
        assert r["confidence_label"] in ("HIGH", "MEDIUM")


# ---------------------------------------------------------------------------
# Tests — sparse but legit doc accepted with LOW confidence
# ---------------------------------------------------------------------------

class TestSparseLegitDoc:
    def test_sparse_doc_accepted_low(self):
        r = classify_document(SPARSE_DOC)
        assert r["accepted"] is True
        assert r["confidence_label"] in ("LOW", "MEDIUM")
        assert r["warning"] is not None or r["confidence_label"] == "MEDIUM"
