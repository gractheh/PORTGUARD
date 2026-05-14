"""
tests/test_document_classifier.py — 11 self-tests for the hardened DocumentValidator.

Covers:
  - 6 classic shipping doc types correctly classified at HIGH/MEDIUM confidence
  - Sparse-but-legit document receives LOW confidence + warning (not rejected)
  - Resume, medical record, academic paper, and legal filing definitively rejected
    with specific, user-friendly messages naming the detected non-shipping type

Run with:  python -m pytest tests/test_document_classifier.py -v
All 11 tests must pass (11/11).
"""

from __future__ import annotations

import pytest

from portguard.document_validator import (
    DocumentValidator,
    ConfidenceLevel,
    DocumentType,
)

# ---------------------------------------------------------------------------
# Shared validator instance
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def v() -> DocumentValidator:
    return DocumentValidator()


# ---------------------------------------------------------------------------
# Sample documents
# ---------------------------------------------------------------------------

BILL_OF_LADING = """
BILL OF LADING

B/L No.: MAEU2024031500123
Date of Issue: 15 March 2024

SHIPPER/EXPORTER:
Shenzhen Tech Manufacturing Co., Ltd.
Building 8, Longhua Industrial Park
Shenzhen, Guangdong 518109, China

CONSIGNEE:
NexGen Components Inc.
4500 Harbor Boulevard, Suite 200
Los Angeles, CA 90058, USA

NOTIFY PARTY: Same as Consignee

VESSEL / VOYAGE: MAERSK EDINBURGH / 014E
PORT OF LOADING: YANTIAN, CHINA
PORT OF DISCHARGE: LOS ANGELES, CA, USA
PLACE OF DELIVERY: DOOR, LOS ANGELES

FREIGHT PREPAID

Description of Goods: Electronic Components, Consumer Electronics
HTS: 8542.31.0000

No. of Packages: 245 CARTONS
Gross Weight: 4,820.00 KGS
SHIPPED ON BOARD
"""

COMMERCIAL_INVOICE = """
COMMERCIAL INVOICE

Invoice No.: CI-2024-00891
Invoice Date: March 15, 2024
Payment Terms: T/T 30 days

SELLER (EXPORTER):
Guangzhou Fashion Exports Ltd.
No. 58 Zhongshan Road, Haizhu District
Guangzhou, Guangdong 510220, China

BUYER (IMPORTER):
American Apparel Wholesale LLC
1200 Fashion District Blvd
New York, NY 10018, USA

Country of Origin: CHINA
Port of Loading: GUANGZHOU, CHINA
Port of Discharge: NEW YORK, USA
Incoterms: FOB Guangzhou

Description of Goods: Men's Cotton T-Shirts, 100% Cotton, Assorted Colors
HTS Code: 6109.10.0012

Quantity: 5,000 PCS
Unit Price: USD 2.50 each
Total Invoice Value: USD 12,500.00
"""

PACKING_LIST = """
PACKING LIST

Packing List No.: PL-2024-00891
Date: March 15, 2024

Shipper: Guangzhou Fashion Exports Ltd.
Consignee: American Apparel Wholesale LLC

Container No.: TCKU3456789
Seal No.: SH-998877

Item  | Description         | Qty/Ctn | Cartons | Net Wt   | Gross Wt
------|---------------------|---------|---------| ---------|----------
001   | Men's T-Shirts S    | 12 PCS  | 100     | 2.20 KGS | 2.40 KGS
002   | Men's T-Shirts M    | 12 PCS  | 150     | 2.20 KGS | 2.40 KGS
003   | Men's T-Shirts L    | 12 PCS  | 100     | 2.20 KGS | 2.40 KGS

Total Number of Cartons: 350
Total Net Weight: 770.00 KGS
Total Gross Weight: 840.00 KGS
Marks and Numbers: PO#4521 / AWL / NY-USA
"""

AIRWAY_BILL = """
AIR WAYBILL

AWB No.: 176-12345 67890
Master AWB: MAWB-2024-CX-001
House AWB No.: HAWB-SZX-20240315

Shipper:
Shenzhen Electronics Exports Co., Ltd.
Shenzhen, Guangdong, CHINA

Consignee:
US Imports Corporation
JFK Cargo Center, New York, NY 10019, USA

Carrier: Cathay Pacific Cargo
Flight No.: CX-1234
Airport of Departure: HKG — Hong Kong International
Airport of Destination: JFK — John F. Kennedy International

Country of Origin: CHINA
HTS Code: 8542.31.0000
Description: Electronic integrated circuits, consumer grade

Gross Weight: 250.00 KGS
Number of Pieces: 10 cartons

Total Invoice Value: USD 48,500.00
Incoterms: CIP Hong Kong
"""

ISF_FILING = """
IMPORTER SECURITY FILING (ISF / 10+2)

Filing Reference: ISF-2024-LAX-00456
Vessel: MAERSK EDINBURGH
Voyage: 014E
SCAC Code: MAEU

IMPORTER OF RECORD:
NexGen Components Inc.
EIN: 45-6789012
CBP Bond No.: 98765432

ISF Data Elements:

1. Seller (Manufacturer/Supplier): Shenzhen Tech Manufacturing Co., Ltd.
2. Buyer (Importer of Record): NexGen Components Inc.
3. Importer of Record Number: 45-6789012
4. Consignee Number: 4567890120001
5. Manufacturer/Supplier: Shenzhen Tech Manufacturing Co., Ltd.
6. Ship-to Party: NexGen Components Inc.
7. Country of Origin: CHINA
8. HTS Code: 8542.31.0000
9. Carrier SCAC: MAEU
10. Container Stuffing Location: YANTIAN, CHINA

Filed pursuant to 19 CFR 149.2(a) — at least 24 hours before vessel departure.
"""

CERTIFICATE_OF_ORIGIN = """
CERTIFICATE OF ORIGIN

Certificate No.: CO-GZ-2024-112233
Date: March 15, 2024

Exporter (Name, Address, Country):
Guangzhou Fashion Exports Ltd.
No. 58 Zhongshan Road, Haizhu District
Guangzhou, Guangdong, CHINA

Consignee (Name, Address, Country):
American Apparel Wholesale LLC
New York, NY, USA

Country of Origin: PEOPLE'S REPUBLIC OF CHINA

We hereby certify that the goods described herein were manufactured in
China and are of Chinese origin.

Goods Description: Men's Cotton T-Shirts, 100% Cotton
HTS Code: 6109.10.0012
Quantity: 5,000 PCS

Authorised Signatory: Zhang Wei, Director of Export Operations
Certified by: Guangzhou Chamber of Commerce
Stamp and Seal
"""

# Sparse-but-legit: only 1 trade signal category (general); should WARN, not reject.
# Deliberately avoids port, consignee, vessel, hs, weight, etc. so total_trade == 1.
SPARSE_LEGIT_DOC = """
Shipping documentation — page 3 of 7.

This excerpt is from a larger freight arrangement file.
The original cargo details appear on the preceding pages.
The terms for this freight consignment are described in full
on pages 1 and 2, which must be read in conjunction with this page.
Please contact the forwarding office for the complete documentation
package relating to the goods described on those pages.
"""

RESUME = """
John A. Smith
john.smith@email.com | (555) 234-5678 | linkedin.com/in/johnsmith

PROFESSIONAL SUMMARY
Experienced logistics coordinator with 8+ years in supply chain management.
Adept at cross-functional collaboration and operational improvement.

WORK EXPERIENCE

Senior Logistics Coordinator | Global Trade Corp | 2019 – Present
- Managed coordination between importers and overseas suppliers
- Oversaw shipping schedules for 200+ monthly shipments
- Reduced transit delays by 18% through process optimization

Logistics Associate | FastFreight LLC | 2016 – 2019
- Assisted with documentation review and customs clearance
- Tracked cargo status across multiple carriers

EDUCATION
Bachelor of Science in Supply Chain Management
State University, 2016
GPA: 3.7/4.0

CORE COMPETENCIES
SAP ERP | Microsoft Excel | Freight Management Systems | Data Analysis

References upon request
"""

MEDICAL_RECORD = """
PATIENT MEDICAL RECORD

Patient Name: Jane Doe
Patient ID: MRN-2024-00456
Date of Birth: 1985-04-22
Visit Date: March 15, 2024

Referring Physician: Dr. Robert Nguyen, MD
Attending Physician: Dr. Sarah Kim, MD

Chief Complaint:
Patient presents with persistent lower back pain for 3 weeks.

Vitals:
Blood Pressure: 118/76 mmHg
Pulse: 72 bpm
Temperature: 98.6°F
Weight: 145 lbs

Diagnosis: Lumbar strain, ICD-10: M54.5

Treatment Plan:
1. Ibuprofen 400mg twice daily as needed
2. Physical therapy — 3 sessions per week for 4 weeks
3. Follow-up in 3 weeks

Allergies: Penicillin (rash)
Medical History: Mild asthma, no surgeries

Laboratory Results: Within normal limits
Clinical Notes: Patient advised to avoid heavy lifting.

Prescription: Cyclobenzaprine 5mg, 30 tablets, no refills
"""

ACADEMIC_PAPER = """
Optimizing Supply Chain Resilience Through Multi-Modal Logistics Networks

Abstract:
This paper presents a comprehensive analysis of supply chain disruptions
and proposes a multi-modal logistics optimization framework.

Keywords: supply chain, resilience, logistics, optimization, disruption

1. Introduction

Global supply chains have faced unprecedented challenges in recent years.
Our study examines the factors contributing to supply chain fragility.

2. Literature Review

Prior research by Smith et al. (2021) demonstrates that single-modal
dependency correlates with increased vulnerability. Jones and Williams
(2022) extended this work by examining peer-reviewed case studies across
five industries.

3. Methodology

We applied a mixed-methods approach with a sample size of 247 firms.
Statistical significance was set at p < 0.05. A control group of firms
with redundant logistics networks was compared against the experimental
group.

4. Results

The results suggest that multi-modal adoption reduces disruption impact
by 34% (95% CI: 28–40%). This paper presents robust evidence for the
hypothesis that resilience investment yields measurable returns.

References:
Smith, A. et al. (2021). Journal of Supply Chain Management, 57(3).
DOI: 10.1111/jscm.12234
"""

LEGAL_FILING = """
IN THE UNITED STATES DISTRICT COURT
FOR THE SOUTHERN DISTRICT OF NEW YORK

Case No.: 24-CV-00123-ABC

GLOBAL TRADE DISPUTES LLC,
          Plaintiff,

     v.

OCEAN FREIGHT CARRIERS INC.,
          Defendant.

AMENDED COMPLAINT FOR BREACH OF CONTRACT

Plaintiff Global Trade Disputes LLC (hereinafter referred to as "Plaintiff"),
by and through its attorney at law, submits this Amended Complaint against
Defendant Ocean Freight Carriers Inc., and alleges as follows:

1. Plaintiff is a corporation organized under the laws of Delaware.
2. Defendant is a shipping company incorporated in the State of New York.
3. On or about January 15, 2024, the parties entered into a contract.
4. Defendant breached the contract by failing to deliver cargo on time.
5. Plaintiff seeks damages in the amount of $450,000.

WHEREFORE, Plaintiff respectfully requests this Court enter judgment in
favor of Plaintiff. Filed with the court on March 15, 2024.

Respectfully submitted,
Margaret K. Wilson, Esquire
Docket Number: 24-CV-00123
Motion to dismiss denied per prior order.
"""


# ---------------------------------------------------------------------------
# Test 1: Bill of Lading classified correctly
# ---------------------------------------------------------------------------

def test_bill_of_lading_classified_correctly(v):
    """A standard B/L must be valid, type=BILL_OF_LADING, confidence=HIGH."""
    result = v.validate_document(BILL_OF_LADING)
    assert result.is_valid is True, f"B/L should be valid. reason={result.rejection_reason}"
    assert result.detected_type == DocumentType.BILL_OF_LADING, (
        f"Expected BILL_OF_LADING, got {result.detected_type}"
    )
    assert result.confidence == ConfidenceLevel.HIGH, (
        f"Expected HIGH confidence, got {result.confidence} ({result.signal_count} signals)"
    )


# ---------------------------------------------------------------------------
# Test 2: Commercial Invoice classified correctly
# ---------------------------------------------------------------------------

def test_commercial_invoice_classified_correctly(v):
    """A standard commercial invoice must be valid, type=COMMERCIAL_INVOICE, confidence=HIGH."""
    result = v.validate_document(COMMERCIAL_INVOICE)
    assert result.is_valid is True, f"Invoice should be valid. reason={result.rejection_reason}"
    assert result.detected_type == DocumentType.COMMERCIAL_INVOICE, (
        f"Expected COMMERCIAL_INVOICE, got {result.detected_type}"
    )
    assert result.confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM), (
        f"Expected HIGH or MEDIUM, got {result.confidence}"
    )


# ---------------------------------------------------------------------------
# Test 3: Packing List classified correctly
# ---------------------------------------------------------------------------

def test_packing_list_classified_correctly(v):
    """A standard packing list must be valid, type=PACKING_LIST."""
    result = v.validate_document(PACKING_LIST)
    assert result.is_valid is True, f"PL should be valid. reason={result.rejection_reason}"
    assert result.detected_type == DocumentType.PACKING_LIST, (
        f"Expected PACKING_LIST, got {result.detected_type}"
    )
    assert result.confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM), (
        f"Expected HIGH or MEDIUM, got {result.confidence}"
    )


# ---------------------------------------------------------------------------
# Test 4: Air Waybill classified correctly
# ---------------------------------------------------------------------------

def test_airway_bill_classified_correctly(v):
    """An Air Waybill must be valid, type=AIRWAY_BILL."""
    result = v.validate_document(AIRWAY_BILL)
    assert result.is_valid is True, f"AWB should be valid. reason={result.rejection_reason}"
    assert result.detected_type == DocumentType.AIRWAY_BILL, (
        f"Expected AIRWAY_BILL, got {result.detected_type}"
    )
    assert result.confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM), (
        f"Expected HIGH or MEDIUM, got {result.confidence}"
    )


# ---------------------------------------------------------------------------
# Test 5: ISF Filing classified correctly
# ---------------------------------------------------------------------------

def test_isf_classified_correctly(v):
    """An ISF 10+2 filing must be valid, type=ISF_FILING."""
    result = v.validate_document(ISF_FILING)
    assert result.is_valid is True, f"ISF should be valid. reason={result.rejection_reason}"
    assert result.detected_type == DocumentType.ISF_FILING, (
        f"Expected ISF_FILING, got {result.detected_type}"
    )
    assert result.confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM), (
        f"Expected HIGH or MEDIUM, got {result.confidence}"
    )


# ---------------------------------------------------------------------------
# Test 6: Certificate of Origin classified correctly
# ---------------------------------------------------------------------------

def test_certificate_of_origin_classified_correctly(v):
    """A Certificate of Origin must be valid, type=CERTIFICATE_OF_ORIGIN."""
    result = v.validate_document(CERTIFICATE_OF_ORIGIN)
    assert result.is_valid is True, f"COO should be valid. reason={result.rejection_reason}"
    assert result.detected_type == DocumentType.CERTIFICATE_OF_ORIGIN, (
        f"Expected CERTIFICATE_OF_ORIGIN, got {result.detected_type}"
    )
    assert result.confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM), (
        f"Expected HIGH or MEDIUM, got {result.confidence}"
    )


# ---------------------------------------------------------------------------
# Test 7: Sparse-but-legit document gets LOW confidence + warning
# ---------------------------------------------------------------------------

def test_sparse_doc_gets_low_confidence_warning(v):
    """
    A partial document with only 1-2 trade signals should pass with
    LOW confidence and a non-None warning_message (not outright rejected).
    """
    result = v.validate_document(SPARSE_LEGIT_DOC)
    assert result.is_valid is True, (
        f"Sparse-but-legit doc should pass (not be rejected). "
        f"reason={result.rejection_reason}, signals={result.signals_found}"
    )
    assert result.confidence == ConfidenceLevel.LOW, (
        f"Expected LOW confidence for sparse doc, got {result.confidence}"
    )
    assert result.warning_message is not None, (
        "Sparse LOW-confidence doc must have a non-None warning_message"
    )


# ---------------------------------------------------------------------------
# Test 8: Resume rejected with specific message mentioning "resume"
# ---------------------------------------------------------------------------

def test_resume_is_rejected_with_specific_message(v):
    """
    A resume/CV must be definitively rejected.
    rejection_reason must be RESUME_CONTENT.
    user_message must specifically mention 'resume' or 'CV'.
    """
    result = v.validate_document(RESUME)
    assert result.is_valid is False, "Resume should be rejected"
    assert result.confidence == ConfidenceLevel.REJECTED
    assert result.rejection_reason == "RESUME_CONTENT", (
        f"Expected RESUME_CONTENT, got {result.rejection_reason}"
    )
    msg_lower = result.user_message.lower()
    assert "resume" in msg_lower or "cv" in msg_lower, (
        f"user_message should mention 'resume' or 'CV'. Got: {result.user_message!r}"
    )


# ---------------------------------------------------------------------------
# Test 9: Medical record rejected with specific message mentioning "medical"
# ---------------------------------------------------------------------------

def test_medical_record_is_rejected_with_specific_message(v):
    """
    A medical record must be definitively rejected.
    rejection_reason must be MEDICAL_RECORD_CONTENT.
    user_message must specifically mention 'medical'.
    """
    result = v.validate_document(MEDICAL_RECORD)
    assert result.is_valid is False, "Medical record should be rejected"
    assert result.confidence == ConfidenceLevel.REJECTED
    assert result.rejection_reason == "MEDICAL_RECORD_CONTENT", (
        f"Expected MEDICAL_RECORD_CONTENT, got {result.rejection_reason}"
    )
    assert "medical" in result.user_message.lower(), (
        f"user_message should mention 'medical'. Got: {result.user_message!r}"
    )


# ---------------------------------------------------------------------------
# Test 10: Academic paper rejected with specific message mentioning "academic"
# ---------------------------------------------------------------------------

def test_academic_paper_is_rejected_with_specific_message(v):
    """
    An academic paper must be definitively rejected.
    rejection_reason must be ACADEMIC_CONTENT.
    user_message must specifically mention 'academic'.
    """
    result = v.validate_document(ACADEMIC_PAPER)
    assert result.is_valid is False, "Academic paper should be rejected"
    assert result.confidence == ConfidenceLevel.REJECTED
    assert result.rejection_reason == "ACADEMIC_CONTENT", (
        f"Expected ACADEMIC_CONTENT, got {result.rejection_reason}"
    )
    assert "academic" in result.user_message.lower(), (
        f"user_message should mention 'academic'. Got: {result.user_message!r}"
    )


# ---------------------------------------------------------------------------
# Test 11: Legal filing rejected with specific message mentioning "legal" or "court"
# ---------------------------------------------------------------------------

def test_legal_filing_is_rejected_with_specific_message(v):
    """
    A legal court filing must be definitively rejected.
    rejection_reason must be LEGAL_FILING_CONTENT.
    user_message must mention 'legal' or 'court'.
    """
    result = v.validate_document(LEGAL_FILING)
    assert result.is_valid is False, "Legal filing should be rejected"
    assert result.confidence == ConfidenceLevel.REJECTED
    assert result.rejection_reason == "LEGAL_FILING_CONTENT", (
        f"Expected LEGAL_FILING_CONTENT, got {result.rejection_reason}"
    )
    msg_lower = result.user_message.lower()
    assert "legal" in msg_lower or "court" in msg_lower, (
        f"user_message should mention 'legal' or 'court'. Got: {result.user_message!r}"
    )
