"""
tests/test_document_validator.py — Comprehensive tests for DocumentValidator.

Covers:
- Valid trade documents → PASS at appropriate confidence tiers
- Non-trade content (recipes, tweets, news, code) → REJECT
- Partial/short documents → LOW confidence / REJECT
- Document type detection accuracy
- Signal counting
- Multi-document validation helpers
- Edge cases (empty, emoji-heavy, URL-dense, mixed content)
"""

from __future__ import annotations

import pytest

from portguard.document_validator import (
    DocumentValidator,
    ValidationResult,
    ConfidenceLevel,
    DocumentType,
    validate_documents,
    build_rejection_error,
)

# ---------------------------------------------------------------------------
# Fixtures — realistic sample documents
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

NOTIFY PARTY:
Same as Consignee

VESSEL / VOYAGE: MAERSK EDINBURGH / 014E
PORT OF LOADING: YANTIAN, CHINA
PORT OF DISCHARGE: LOS ANGELES, CA, USA
PLACE OF DELIVERY: DOOR, LOS ANGELES

FREIGHT PREPAID

Description of Goods:
Electronic Components, Consumer Electronics
HTS: 8542.31.0000

Marks and Numbers: N/A
No. of Packages: 245 CARTONS
Gross Weight: 4,820.00 KGS
Measurement: 62.5 CBM

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

Description of Goods:
Men's Cotton T-Shirts, 100% Cotton, Assorted Colors
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

Item No. | Description        | Qty/Ctn | No. Cartons | Net Weight | Gross Weight
---------|--------------------|---------| ------------|------------|-------------
001      | Men's T-Shirts S   | 12 PCS  | 100         | 2.20 KGS   | 2.40 KGS
002      | Men's T-Shirts M   | 12 PCS  | 150         | 2.20 KGS   | 2.40 KGS
003      | Men's T-Shirts L   | 12 PCS  | 100         | 2.20 KGS   | 2.40 KGS

Total Number of Cartons: 350
Total Net Weight: 770.00 KGS
Total Gross Weight: 840.00 KGS
Total Measurement: 18.5 CBM

Marks and Numbers: PO#4521 / AWL / NY-USA
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

Authorised Signatory:
[Signature]
Zhang Wei, Director of Export Operations

Certified by: Guangzhou Chamber of Commerce
Stamp and Seal
"""

ARRIVAL_NOTICE = """
ARRIVAL NOTICE

Notice Date: March 28, 2024

Dear Valued Customer,

We are pleased to inform you that the following shipment is scheduled to arrive
at the Port of Discharge.

Vessel: MAERSK EDINBURGH
Voyage: 014E
B/L No.: MAEU2024031500123
ETA (Estimated Time of Arrival): April 2, 2024

Port of Discharge: LOS ANGELES, CA
Terminal: APL TERMINAL PIER 400

Consignee: NexGen Components Inc.

Please contact your customs broker to arrange for timely customs clearance.
Cargo is available for release upon payment of applicable charges.
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

# ---------------------------------------------------------------------------
# Non-trade documents
# ---------------------------------------------------------------------------

RECIPE = """
Classic Chocolate Chip Cookies

Ingredients:
- 2 1/4 cups all-purpose flour
- 1 teaspoon baking soda
- 1 teaspoon salt
- 1 cup (2 sticks) butter, softened
- 3/4 cup granulated sugar
- 3/4 cup packed brown sugar
- 2 large eggs
- 2 teaspoons vanilla extract
- 2 cups chocolate chips

Instructions:
Preheat oven to 375°F (190°C). Combine flour, baking soda and salt in a bowl.
Beat butter and sugars until creamy. Add eggs one at a time, then vanilla.
Gradually blend in the flour mixture. Stir in chocolate chips.
Bake at 375°F for 9-11 minutes or until golden brown. Cool on baking sheets
for 2 minutes; remove to wire racks to cool completely.

Prep time: 15 minutes  |  Cook time: 11 minutes  |  Serves: 60 cookies
"""

TWEET = """
OMG just saw the new Marvel trailer and I literally cannot even 😱🔥💀
Like and subscribe if you agree this is going to be the BEST movie of the year!!!
RT if you're hyped!! Drop a 🔥 in the comments if you're watching opening day.
Follow me for more updates @moviefan2024 #Marvel #Movies #Trending
DM me if you want tickets!!
Going viral rn fr fr no cap 💯
"""

NEWS_ARTICLE = """
Trade Tensions Rise as New Tariffs Take Effect

WASHINGTON, D.C. — According to senior administration officials, the United States
is set to impose additional tariffs on a wide range of consumer goods imported from
several Asian economies, sources say.

Government spokespeople said in a statement that the measures are intended to
protect domestic manufacturing jobs. Officials said the tariffs could affect
hundreds of product categories.

Reuters and the Associated Press reported that industry groups are pushing back
strongly against the proposed measures. The Wall Street Journal noted that
several large retailers have already warned of price increases.

"We are deeply concerned," a trade association spokesperson told reporters during
a press conference Thursday. Published on March 15, 2024. Updated: 4:30 PM ET.

Read more at www.tradenews.com
"""

PYTHON_CODE = """
import re
from typing import Optional

def extract_shipment_data(text: str) -> dict:
    result = {}

    # Extract consignee
    if match := re.search(r'CONSIGNEE:\\s*([^\\n]+)', text):
        result['consignee'] = match.group(1).strip()

    return result

class ShipmentParser:
    def __init__(self, config: dict = None):
        self.config = config or {}

    def parse(self, text: str) -> Optional[dict]:
        if not text:
            return None
        return extract_shipment_data(text)

# Run parser
parser = ShipmentParser()
data = parser.parse("some text")
print(data)
console.log("done")
"""

TOO_SHORT = "Bill of lading document"

PARTIAL_DOCUMENT = """
PARTIAL DOCUMENT - PAGE 3 OF 7

(continued from previous page)

...and therefore the party shall be responsible for all applicable charges
incurred after the standard free time period expires at the place of delivery.

Please refer to the complete document for full terms and conditions applicable
to this shipment arrangement.
"""

GENERAL_TRADE_NO_TYPE = """
This document concerns a customs clearance and freight forwarding arrangement.
The cargo shipment is currently in transit and requires customs entry filing
before release. Standard duty rates apply to this consignment.
The importer should ensure all documentation for customs clearance is complete.
Contact your freight broker for details regarding the bonded warehouse.
Container is scheduled for delivery order issuance upon payment of tariff duties.
All shipping documentation must be submitted to customs within the prescribed timeframe.
"""

# ---------------------------------------------------------------------------
# Helper: simple Document stub for multi-doc tests
# ---------------------------------------------------------------------------

class _Doc:
    def __init__(self, raw_text: str, filename: str = "test.txt"):
        self.raw_text = raw_text
        self.filename = filename


# ---------------------------------------------------------------------------
# Test: Valid trade documents → PASS
# ---------------------------------------------------------------------------

class TestValidTradeDocuments:

    def setup_method(self):
        self.v = DocumentValidator()

    def test_bill_of_lading_passes(self):
        result = self.v.validate_document(BILL_OF_LADING)
        assert result.is_valid is True, f"B/L should be valid. Signals: {result.signals_found}"
        assert result.confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM)

    def test_bill_of_lading_type_detected(self):
        result = self.v.validate_document(BILL_OF_LADING)
        assert result.detected_type == DocumentType.BILL_OF_LADING, (
            f"Expected BILL_OF_LADING, got {result.detected_type}"
        )

    def test_bill_of_lading_high_confidence(self):
        result = self.v.validate_document(BILL_OF_LADING)
        assert result.confidence == ConfidenceLevel.HIGH, (
            f"Full B/L should be HIGH confidence, got {result.confidence}. "
            f"Signals matched: {result.signal_count} ({result.signals_found})"
        )

    def test_commercial_invoice_passes(self):
        result = self.v.validate_document(COMMERCIAL_INVOICE)
        assert result.is_valid is True
        assert result.confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM)

    def test_commercial_invoice_type_detected(self):
        result = self.v.validate_document(COMMERCIAL_INVOICE)
        assert result.detected_type == DocumentType.COMMERCIAL_INVOICE, (
            f"Expected COMMERCIAL_INVOICE, got {result.detected_type}"
        )

    def test_packing_list_passes(self):
        result = self.v.validate_document(PACKING_LIST)
        assert result.is_valid is True
        assert result.confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM)

    def test_packing_list_type_detected(self):
        result = self.v.validate_document(PACKING_LIST)
        assert result.detected_type == DocumentType.PACKING_LIST, (
            f"Expected PACKING_LIST, got {result.detected_type}"
        )

    def test_certificate_of_origin_passes(self):
        result = self.v.validate_document(CERTIFICATE_OF_ORIGIN)
        assert result.is_valid is True

    def test_certificate_of_origin_type_detected(self):
        result = self.v.validate_document(CERTIFICATE_OF_ORIGIN)
        assert result.detected_type == DocumentType.CERTIFICATE_OF_ORIGIN, (
            f"Expected CERTIFICATE_OF_ORIGIN, got {result.detected_type}"
        )

    def test_arrival_notice_passes(self):
        result = self.v.validate_document(ARRIVAL_NOTICE)
        assert result.is_valid is True

    def test_arrival_notice_type_detected(self):
        result = self.v.validate_document(ARRIVAL_NOTICE)
        assert result.detected_type == DocumentType.ARRIVAL_NOTICE, (
            f"Expected ARRIVAL_NOTICE, got {result.detected_type}"
        )

    def test_isf_filing_passes(self):
        result = self.v.validate_document(ISF_FILING)
        assert result.is_valid is True

    def test_isf_filing_type_detected(self):
        result = self.v.validate_document(ISF_FILING)
        assert result.detected_type == DocumentType.ISF_FILING, (
            f"Expected ISF_FILING, got {result.detected_type}"
        )

    def test_valid_document_no_rejection_reason(self):
        result = self.v.validate_document(BILL_OF_LADING)
        assert result.rejection_reason is None

    def test_valid_document_no_warning(self):
        result = self.v.validate_document(BILL_OF_LADING)
        assert result.warning_message is None


# ---------------------------------------------------------------------------
# Test: Non-trade content → REJECTED
# ---------------------------------------------------------------------------

class TestNonTradeRejection:

    def setup_method(self):
        self.v = DocumentValidator()

    def test_recipe_is_rejected(self):
        result = self.v.validate_document(RECIPE)
        assert result.is_valid is False, "Recipe should be rejected"
        assert result.confidence == ConfidenceLevel.REJECTED

    def test_recipe_rejection_reason(self):
        result = self.v.validate_document(RECIPE)
        assert result.rejection_reason == "RECIPE_CONTENT", (
            f"Expected RECIPE_CONTENT, got {result.rejection_reason}"
        )

    def test_recipe_user_message_is_friendly(self):
        result = self.v.validate_document(RECIPE)
        msg = result.user_message.lower()
        assert "trade document" in msg or "portguard" in msg.lower()

    def test_tweet_is_rejected(self):
        result = self.v.validate_document(TWEET)
        assert result.is_valid is False, "Tweet should be rejected"
        assert result.confidence == ConfidenceLevel.REJECTED

    def test_tweet_rejection_reason(self):
        result = self.v.validate_document(TWEET)
        assert result.rejection_reason == "SOCIAL_MEDIA_CONTENT", (
            f"Expected SOCIAL_MEDIA_CONTENT, got {result.rejection_reason}"
        )

    def test_tweet_user_message(self):
        result = self.v.validate_document(TWEET)
        assert "trade" in result.user_message.lower() or "portguard" in result.user_message.lower()

    def test_news_article_is_rejected(self):
        result = self.v.validate_document(NEWS_ARTICLE)
        assert result.is_valid is False, "News article should be rejected"
        assert result.confidence == ConfidenceLevel.REJECTED

    def test_news_rejection_reason(self):
        result = self.v.validate_document(NEWS_ARTICLE)
        assert result.rejection_reason in ("NEWS_CONTENT", "NO_TRADE_SIGNALS"), (
            f"Expected NEWS_CONTENT or NO_TRADE_SIGNALS, got {result.rejection_reason}"
        )

    def test_python_code_is_rejected(self):
        result = self.v.validate_document(PYTHON_CODE)
        assert result.is_valid is False, "Python code should be rejected"
        assert result.confidence == ConfidenceLevel.REJECTED

    def test_code_rejection_reason(self):
        result = self.v.validate_document(PYTHON_CODE)
        assert result.rejection_reason == "CODE_CONTENT", (
            f"Expected CODE_CONTENT, got {result.rejection_reason}"
        )

    def test_empty_string_is_rejected(self):
        result = self.v.validate_document("")
        assert result.is_valid is False
        assert result.confidence == ConfidenceLevel.REJECTED

    def test_whitespace_only_is_rejected(self):
        result = self.v.validate_document("   \n  \t  ")
        assert result.is_valid is False

    def test_too_short_is_rejected(self):
        result = self.v.validate_document(TOO_SHORT)
        assert result.is_valid is False
        assert result.rejection_reason == "TOO_SHORT"

    def test_too_short_user_message(self):
        result = self.v.validate_document(TOO_SHORT)
        assert "short" in result.user_message.lower() or "complete" in result.user_message.lower()


# ---------------------------------------------------------------------------
# Test: Partial / low-confidence documents → WARN
# ---------------------------------------------------------------------------

class TestLowConfidenceDocuments:

    def setup_method(self):
        self.v = DocumentValidator()

    def test_partial_document_warns_not_rejects(self):
        result = self.v.validate_document(PARTIAL_DOCUMENT)
        # Partial doc has vessel and carrier reference — at least 1-2 signals
        # Should warn, not reject
        assert result.is_valid is True, (
            f"Partial doc should pass (with warning), got rejected. "
            f"Reason: {result.rejection_reason}"
        )

    def test_partial_document_low_confidence(self):
        result = self.v.validate_document(PARTIAL_DOCUMENT)
        assert result.confidence == ConfidenceLevel.LOW, (
            f"Partial doc should be LOW confidence, got {result.confidence}"
        )

    def test_partial_document_has_warning_message(self):
        result = self.v.validate_document(PARTIAL_DOCUMENT)
        assert result.warning_message is not None
        assert "limited" in result.warning_message.lower() or "incomplete" in result.warning_message.lower()

    def test_general_trade_terms_pass_as_unrecognized(self):
        result = self.v.validate_document(GENERAL_TRADE_NO_TYPE)
        assert result.is_valid is True, (
            f"Doc with general trade terms should pass. "
            f"Signals: {result.signals_found}, reason: {result.rejection_reason}"
        )


# ---------------------------------------------------------------------------
# Test: Signal counting
# ---------------------------------------------------------------------------

class TestSignalCounting:

    def setup_method(self):
        self.v = DocumentValidator()

    def test_bill_of_lading_has_expected_signals(self):
        counts = self.v.count_trade_signals(BILL_OF_LADING)
        assert counts["shipper"] is True, "B/L should match shipper signal"
        assert counts["consignee"] is True, "B/L should match consignee signal"
        assert counts["vessel"] is True, "B/L should match vessel signal"
        assert counts["port"] is True, "B/L should match port signal"
        assert counts["weight"] is True, "B/L should match weight signal"
        assert counts["package"] is True, "B/L should match package signal"

    def test_invoice_has_invoice_signal(self):
        counts = self.v.count_trade_signals(COMMERCIAL_INVOICE)
        assert counts["invoice"] is True, "Invoice should match invoice signal"
        assert counts["value"] is True, "Invoice should match value signal"
        assert counts["hs"] is True, "Invoice should match HS signal"
        assert counts["origin"] is True, "Invoice should match origin signal"

    def test_packing_list_has_package_signal(self):
        counts = self.v.count_trade_signals(PACKING_LIST)
        assert counts["package"] is True
        assert counts["weight"] is True

    def test_certificate_of_origin_has_origin_signal(self):
        counts = self.v.count_trade_signals(CERTIFICATE_OF_ORIGIN)
        assert counts["origin"] is True
        assert counts["hs"] is True

    def test_recipe_has_no_trade_signals(self):
        counts = self.v.count_trade_signals(RECIPE)
        trade_matches = [cat for cat, matched in counts.items()
                         if matched and cat != "general"]
        assert len(trade_matches) == 0, (
            f"Recipe should have no trade signals, found: {trade_matches}"
        )

    def test_tweet_has_no_trade_signals(self):
        counts = self.v.count_trade_signals(TWEET)
        trade_matches = [cat for cat, matched in counts.items()
                         if matched and cat != "general"]
        assert len(trade_matches) == 0, (
            f"Tweet should have no trade signals, found: {trade_matches}"
        )

    def test_count_trade_signals_returns_dict(self):
        counts = self.v.count_trade_signals(BILL_OF_LADING)
        assert isinstance(counts, dict)
        expected_keys = {"shipper", "consignee", "vessel", "port", "value",
                         "weight", "hs", "origin", "invoice", "package", "general"}
        assert set(counts.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Test: Confidence calculation
# ---------------------------------------------------------------------------

class TestConfidenceCalculation:

    def setup_method(self):
        self.v = DocumentValidator()

    def test_zero_signals_is_rejected(self):
        assert self.v.calculate_confidence(0) == ConfidenceLevel.REJECTED

    def test_one_signal_is_low(self):
        assert self.v.calculate_confidence(1) == ConfidenceLevel.LOW

    def test_two_signals_is_low(self):
        assert self.v.calculate_confidence(2) == ConfidenceLevel.LOW

    def test_three_signals_is_medium(self):
        assert self.v.calculate_confidence(3) == ConfidenceLevel.MEDIUM

    def test_four_signals_is_medium(self):
        assert self.v.calculate_confidence(4) == ConfidenceLevel.MEDIUM

    def test_five_signals_is_high(self):
        assert self.v.calculate_confidence(5) == ConfidenceLevel.HIGH

    def test_ten_signals_is_high(self):
        assert self.v.calculate_confidence(10) == ConfidenceLevel.HIGH


# ---------------------------------------------------------------------------
# Test: Document type detection
# ---------------------------------------------------------------------------

class TestDocumentTypeDetection:

    def setup_method(self):
        self.v = DocumentValidator()

    def test_bl_anchor_overrides_scoring(self):
        # Minimal B/L with only the anchor term
        minimal_bl = "\n".join([
            "Bill of Lading",
            "B/L No.: TEST123",
            "Shipper: ABC Corp",
            "Consignee: XYZ Inc",
            "Vessel: TEST SHIP",
            "Port of Loading: Shanghai",
            "Port of Discharge: Los Angeles",
            "Gross Weight: 1000 KGS",
            "Packages: 50 CARTONS",
            "Freight Prepaid",
        ])
        doc_type = self.v.detect_document_type(minimal_bl)
        assert doc_type == DocumentType.BILL_OF_LADING

    def test_invoice_anchor_detected(self):
        minimal_inv = "\n".join([
            "COMMERCIAL INVOICE",
            "Invoice No.: INV-001",
            "Seller: ABC Ltd",
            "Buyer: XYZ Inc",
            "Total Value: USD 5,000",
            "Country of Origin: China",
            "HTS Code: 8471.30",
        ])
        doc_type = self.v.detect_document_type(minimal_inv)
        assert doc_type == DocumentType.COMMERCIAL_INVOICE

    def test_packing_list_anchor_detected(self):
        minimal_pl = "\n".join([
            "PACKING LIST",
            "Packing List No.: PL-001",
            "Shipper: ABC Ltd",
            "Consignee: XYZ Inc",
            "Total Cartons: 100",
            "Gross Weight: 500 KGS",
        ])
        doc_type = self.v.detect_document_type(minimal_pl)
        assert doc_type == DocumentType.PACKING_LIST

    def test_coo_anchor_detected(self):
        minimal_coo = "\n".join([
            "CERTIFICATE OF ORIGIN",
            "We hereby certify that the goods were manufactured in China.",
            "Chamber of Commerce stamp",
            "Exporter: ABC Ltd",
            "Country of Origin: China",
        ])
        doc_type = self.v.detect_document_type(minimal_coo)
        assert doc_type == DocumentType.CERTIFICATE_OF_ORIGIN

    def test_arrival_notice_anchor_detected(self):
        minimal_an = "\n".join([
            "ARRIVAL NOTICE",
            "Vessel: TEST VESSEL",
            "ETA: April 2, 2024",
            "Port of Discharge: Los Angeles",
            "Consignee: XYZ Inc",
        ])
        doc_type = self.v.detect_document_type(minimal_an)
        assert doc_type == DocumentType.ARRIVAL_NOTICE

    def test_isf_anchor_detected(self):
        minimal_isf = "\n".join([
            "IMPORTER SECURITY FILING (ISF)",
            "10+2 Filing",
            "Importer of Record: XYZ Inc",
            "SCAC Code: MAEU",
            "HTS Code: 8471.30.0100",
            "Country of Origin: China",
        ])
        doc_type = self.v.detect_document_type(minimal_isf)
        assert doc_type == DocumentType.ISF_FILING

    def test_unknown_text_returns_unknown_or_unrecognized(self):
        # Pure garbage with no trade signals
        result = self.v.validate_document("hello world this is just some random text that has nothing to do with trade or shipping at all really")
        assert result.detected_type in (DocumentType.UNKNOWN, DocumentType.UNRECOGNIZED_TRADE)


# ---------------------------------------------------------------------------
# Test: is_non_trade_content helper
# ---------------------------------------------------------------------------

class TestIsNonTradeContent:

    def setup_method(self):
        self.v = DocumentValidator()

    def test_recipe_is_non_trade(self):
        assert self.v.is_non_trade_content(RECIPE) is True

    def test_tweet_is_non_trade(self):
        assert self.v.is_non_trade_content(TWEET) is True

    def test_news_article_is_non_trade(self):
        assert self.v.is_non_trade_content(NEWS_ARTICLE) is True

    def test_code_is_non_trade(self):
        assert self.v.is_non_trade_content(PYTHON_CODE) is True

    def test_bill_of_lading_is_not_non_trade(self):
        assert self.v.is_non_trade_content(BILL_OF_LADING) is False

    def test_invoice_is_not_non_trade(self):
        assert self.v.is_non_trade_content(COMMERCIAL_INVOICE) is False


# ---------------------------------------------------------------------------
# Test: Multi-document validation
# ---------------------------------------------------------------------------

class TestMultiDocumentValidation:

    def test_all_valid_returns_all_pass(self):
        docs = [
            _Doc(BILL_OF_LADING, "bol.txt"),
            _Doc(COMMERCIAL_INVOICE, "invoice.txt"),
        ]
        results = validate_documents(docs)
        assert len(results) == 2
        assert all(r.is_valid for r in results)

    def test_one_rejected_both_returned(self):
        docs = [
            _Doc(BILL_OF_LADING, "bol.txt"),
            _Doc(RECIPE, "recipe.txt"),
        ]
        results = validate_documents(docs)
        assert len(results) == 2
        assert results[0].is_valid is True
        assert results[1].is_valid is False

    def test_order_preserved(self):
        docs = [
            _Doc(RECIPE, "recipe.txt"),
            _Doc(BILL_OF_LADING, "bol.txt"),
            _Doc(COMMERCIAL_INVOICE, "invoice.txt"),
        ]
        results = validate_documents(docs)
        assert results[0].is_valid is False   # recipe
        assert results[1].is_valid is True    # bol
        assert results[2].is_valid is True    # invoice

    def test_build_rejection_error_structure(self):
        result = ValidationResult(
            is_valid=False,
            confidence=ConfidenceLevel.REJECTED,
            detected_type=DocumentType.UNKNOWN,
            signals_found=[],
            signal_count=0,
            rejection_reason="RECIPE_CONTENT",
            user_message="This looks like a recipe.",
            warning_message=None,
        )
        error = build_rejection_error([result], ["pasta_recipe.txt"], 2)
        assert error["code"] == "DOCUMENT_VALIDATION_FAILED"
        assert "1 of 2" in error["message"]
        assert len(error["rejected_documents"]) == 1
        assert error["rejected_documents"][0]["filename"] == "pasta_recipe.txt"
        assert error["rejected_documents"][0]["reason"] == "RECIPE_CONTENT"

    def test_build_rejection_error_plural(self):
        rejected = [
            ValidationResult(
                is_valid=False,
                confidence=ConfidenceLevel.REJECTED,
                detected_type=DocumentType.UNKNOWN,
                signals_found=[],
                signal_count=0,
                rejection_reason="RECIPE_CONTENT",
                user_message="msg",
                warning_message=None,
            ),
            ValidationResult(
                is_valid=False,
                confidence=ConfidenceLevel.REJECTED,
                detected_type=DocumentType.UNKNOWN,
                signals_found=[],
                signal_count=0,
                rejection_reason="CODE_CONTENT",
                user_message="msg",
                warning_message=None,
            ),
        ]
        error = build_rejection_error(rejected, ["a.txt", "b.txt"], 3)
        assert "2 of 3" in error["message"]
        assert len(error["rejected_documents"]) == 2


# ---------------------------------------------------------------------------
# Test: ValidationResult.to_dict()
# ---------------------------------------------------------------------------

class TestValidationResultToDict:

    def test_to_dict_contains_required_keys(self):
        v = DocumentValidator()
        result = v.validate_document(BILL_OF_LADING)
        d = result.to_dict()
        for key in ("is_valid", "confidence", "detected_type", "signals_found",
                    "signal_count", "rejection_reason", "user_message", "warning_message"):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_enums_serialized_as_strings(self):
        v = DocumentValidator()
        result = v.validate_document(BILL_OF_LADING)
        d = result.to_dict()
        assert isinstance(d["confidence"], str)
        assert isinstance(d["detected_type"], str)


# ---------------------------------------------------------------------------
# Test: generate_user_message
# ---------------------------------------------------------------------------

class TestGenerateUserMessage:

    def setup_method(self):
        self.v = DocumentValidator()

    def test_returns_string(self):
        result = self.v.validate_document(BILL_OF_LADING)
        msg = self.v.generate_user_message(result)
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_rejected_message_mentions_trade(self):
        result = self.v.validate_document(RECIPE)
        msg = self.v.generate_user_message(result)
        assert "trade" in msg.lower()


# ---------------------------------------------------------------------------
# Test: Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def setup_method(self):
        self.v = DocumentValidator()

    def test_food_commodity_invoice_not_rejected_as_recipe(self):
        """Commercial invoice for food products should not trigger recipe rejection."""
        food_invoice = """
        COMMERCIAL INVOICE

        Invoice No.: INV-2024-FOOD-001
        Invoice Date: March 15, 2024

        Seller: Vietnam Seafood Exports Co., Ltd.
        Buyer: Pacific Foods Import LLC

        Country of Origin: VIETNAM
        HTS Code: 0306.17.0040

        Description: Frozen Shrimp, 16/20 count, peeled and deveined
        Quantity: 10,000 KGS
        Unit Price: USD 4.50 per KG
        Total Invoice Value: USD 45,000.00

        Port of Loading: Ho Chi Minh City
        Port of Discharge: Los Angeles
        """
        result = self.v.validate_document(food_invoice)
        assert result.is_valid is True, (
            f"Food commodity invoice should not be rejected as recipe. "
            f"Reason: {result.rejection_reason}, signals: {result.signals_found}"
        )

    def test_document_with_single_url_not_rejected(self):
        """One URL in an otherwise valid document should not trigger rejection."""
        bl_with_url = BILL_OF_LADING + "\nTracking: https://www.maersk.com/tracking/MAEU2024031500123"
        result = self.v.validate_document(bl_with_url)
        assert result.is_valid is True

    def test_very_long_document_passes(self):
        """A valid document repeated many times should still pass."""
        long_doc = BILL_OF_LADING * 5
        result = self.v.validate_document(long_doc)
        assert result.is_valid is True

    def test_mixed_case_signals_detected(self):
        """Signal matching must be case-insensitive."""
        mixed_case = """
        bill of lading
        B/L NO.: TEST-001
        SHIPPER: acme exports ltd
        consignee: global imports inc
        vessel: mv ocean express
        port of loading: shanghai
        PORT OF DISCHARGE: LOS ANGELES
        gross weight: 5000 KGS
        total packages: 200 CARTONS
        freight prepaid
        """
        result = self.v.validate_document(mixed_case)
        assert result.is_valid is True
        assert result.detected_type == DocumentType.BILL_OF_LADING

    def test_none_text_handled_gracefully(self):
        """None input should return REJECTED without raising."""
        result = self.v.validate_document(None)
        assert result.is_valid is False
        assert result.confidence == ConfidenceLevel.REJECTED
