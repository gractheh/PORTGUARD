"""
portguard/document_validator.py — Document authenticity and relevance validation.

Runs as a pre-analysis gate on every document submitted to the analyze endpoints.
Determines whether submitted text is a genuine trade document before allowing
the analysis pipeline to proceed.

Design principles:
- Pure Python — no FastAPI imports, no database access, fully testable in isolation.
- Patterns compiled once at module load; validate_document() does no re.compile() calls.
- Conservative: documents with trade signals but unrecognized type are warned, not rejected.
- Non-destructive: never modifies the document text.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ConfidenceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    REJECTED = "REJECTED"


class DocumentType(str, Enum):
    BILL_OF_LADING = "BILL_OF_LADING"
    COMMERCIAL_INVOICE = "COMMERCIAL_INVOICE"
    PACKING_LIST = "PACKING_LIST"
    CERTIFICATE_OF_ORIGIN = "CERTIFICATE_OF_ORIGIN"
    ARRIVAL_NOTICE = "ARRIVAL_NOTICE"
    ISF_FILING = "ISF_FILING"
    UNRECOGNIZED_TRADE = "UNRECOGNIZED_TRADE"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# ValidationResult dataclass
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """Result of validating a single document."""
    is_valid: bool
    confidence: ConfidenceLevel
    detected_type: DocumentType
    signals_found: list
    signal_count: int
    rejection_reason: Optional[str]
    user_message: str
    warning_message: Optional[str]

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "confidence": self.confidence.value,
            "detected_type": self.detected_type.value,
            "signals_found": self.signals_found,
            "signal_count": self.signal_count,
            "rejection_reason": self.rejection_reason,
            "user_message": self.user_message,
            "warning_message": self.warning_message,
        }


# ---------------------------------------------------------------------------
# User-facing message constants
# ---------------------------------------------------------------------------

_MSG_REJECTED = (
    "This doesn't look like a trade document. PortGuard analyzes bills of lading, "
    "commercial invoices, packing lists, and certificates of origin. "
    "Please upload a valid shipping document."
)
_MSG_TOO_SHORT = (
    "This document is too short to analyze. Please upload the complete document — "
    "not a summary, title page, or excerpt."
)
_MSG_SOCIAL_MEDIA = (
    "This appears to be social media content, not a trade document. "
    "PortGuard analyzes bills of lading, commercial invoices, packing lists, "
    "and certificates of origin."
)
_MSG_RECIPE = (
    "This appears to be a recipe or food preparation document, not a trade document. "
    "If you're importing food products, please upload the commercial invoice or "
    "certificate of origin for the shipment."
)
_MSG_CODE = (
    "This appears to be source code or technical markup, not a trade document. "
    "Please upload a valid shipping document."
)
_MSG_NEWS = (
    "This appears to be a news article or press release, not a trade document. "
    "Please upload a valid shipping document."
)
_MSG_WARN_LOW = (
    "This document has limited trade signals. Results may be incomplete. "
    "Make sure you're uploading the full document, not a cover page or summary."
)
_MSG_UNRECOGNIZED_TRADE = (
    "Document type not recognized, but trade signals were detected. "
    "Analyzing as a general trade document. For best results, upload complete "
    "bills of lading, commercial invoices, or packing lists."
)


# ---------------------------------------------------------------------------
# DocumentValidator
# ---------------------------------------------------------------------------

class DocumentValidator:
    """
    Pre-analysis gate that validates document authenticity and trade relevance.

    Usage
    -----
    validator = DocumentValidator()
    result = validator.validate_document(text)
    if not result.is_valid:
        # raise 422 or surface error to user
        ...
    """

    # -------------------------------------------------------------------
    # Trade signal keyword lists (10 categories)
    # Each list drives one signal category. A signal is "matched" when
    # ANY keyword from its list appears in the text (case-insensitive,
    # word-boundary anchored where the term is a standalone word).
    # -------------------------------------------------------------------

    SHIPPER_SIGNALS = [
        "shipper", "exporter", "sender", "consignor", "shipped by",
        "from:", "shipper/exporter", "seller", "exporting party",
        "shipper name", "export by", "shipped from",
        "name of shipper", "party to notify",
    ]

    CONSIGNEE_SIGNALS = [
        "consignee", "importer", "receiver", "buyer", "ship to",
        "deliver to", "sold to", "bill to", "importer of record",
        "consignee name", "notify party", "notify", "notified party",
        "delivery to", "addressee",
    ]

    VESSEL_SIGNALS = [
        "vessel", "ship name", "vessel name", "v/v", "m/v", "m.v.",
        "s/s", "flight no", "flight number", "carrier", "scac",
        "ocean carrier", "air carrier", "voyage no", "voyage number",
        "voyage", "airway", "on board",
    ]

    PORT_SIGNALS = [
        "port of loading", "pol", "port of discharge", "pod",
        "port of entry", "port of origin", "place of receipt",
        "loading port", "discharge port", "port of destination",
        "place of delivery", "final destination", "origin port",
        "departure port", "arrival port", "port of arrival",
        "place of delivery",
    ]

    VALUE_SIGNALS = [
        "invoice value", "declared value", "total value",
        "fob value", "cif value", "unit price", "amount",
        "total amount", "grand total", "total invoice",
        "extended value", "subtotal", "amount due",
        "total price", "customs value", "transaction value",
        "dutiable value", "assessed value",
    ]

    WEIGHT_SIGNALS = [
        "gross weight", "net weight", "g.w.", "n.w.", "gw:", "nw:",
        "total weight", "weight kg", "weight lbs",
        "metric tons", "metric tonnes",
    ]

    HS_SIGNALS = [
        "hs code", "hts code", "hts:", "hs:", "tariff code",
        "harmonized", "commodity code", "schedule b",
        "h.s. code", "harmonized system", "harmonized tariff",
        "tariff number", "hs no", "hts no", "tariff no",
        "6-digit", "10-digit hts", "chapter ",
    ]

    ORIGIN_SIGNALS = [
        "country of origin", "made in", "origin country",
        "manufactured in", "produced in", "place of origin",
        "country of manufacture", "origin:", "c/o certificate",
        "country of production",
    ]

    INVOICE_SIGNALS = [
        "invoice number", "invoice no", "inv no", "inv.",
        "invoice date", "commercial invoice", "proforma invoice",
        "invoice #", "invoice ref", "invoice no:",
        "invoice number:", "debit note", "credit note",
    ]

    PACKAGE_SIGNALS = [
        "packages", "cartons", "pallets", "pieces",
        "total packages", "no. of packages", "number of packages",
        "total cartons", "total pieces", "total units",
        "pkgs", "ctns", "pcs", "quantity",
        "package count", "case count",
    ]

    # -------------------------------------------------------------------
    # General trade signals (not type-specific; contribute to total count
    # for UNRECOGNIZED_TRADE detection, not to type classification)
    # -------------------------------------------------------------------

    _GENERAL_TRADE_TERMS = [
        "customs", "duty", "tariff", "freight", "cargo", "shipment",
        "shipping", "consignment", "clearance", "manifest",
        "forwarder", "broker", "customs entry", "incoterm",
        "letter of credit", "cbp", "aes filing", "bonded",
        "container", "seal no", "booking no", "vgm",
        "delivery order", "arrival", "release order",
        "packing list", "bill of lading", "airway bill",
        "mawb", "hawb", "b/l", "bol", "lc no",
    ]

    # -------------------------------------------------------------------
    # Non-trade rejection keyword lists
    # -------------------------------------------------------------------

    _RECIPE_PATTERNS = [
        "ingredients", "tablespoon", "teaspoon", "preheat", "bake at",
        "cup of", "cups of", "oven to", "mixing bowl", "stir until",
        "add the", "recipe", "serves", "prep time", "cook time",
        "calories", "flour", "butter", "sugar", "baking", "simmer",
        "marinate", "sauté", "garnish",
    ]

    _SOCIAL_PATTERNS = [
        "follow me", "like and subscribe", "dm me", "retweet",
        "rt @", "hashtag", "trending", "link in bio",
        "hit the like", "share this", "follow us", "going viral",
        "posted on", "instagram", "tiktok", "facebook",
        "like and share", "subscribe to", "join our",
    ]
    # Note: "twitter", "youtube", bare "@" and "#" intentionally excluded —
    # trade documents may reference port/carrier twitter accounts or use
    # # symbols in code / reference numbers.

    _CODE_PATTERNS = [
        "function(", "function (", "def ", "import ",
        "select ", "console.log", "print(", "<html",
        "return {", "const ", "var ", "let ",
        "if (", "for (", "while (", "class ",
        "public static", "void main", "<?php",
        "#!/usr/bin", "npm install",
    ]

    _NEWS_PATTERNS = [
        "according to", "said in a statement", "reported that",
        "breaking news", "sources say", "officials said",
        "the government said", "told reporters", "announced that",
        "press release", "spokesperson said", "in a statement",
        "reuters", "associated press", "ap news",
        "published on", "updated:", "read more",
    ]

    # -------------------------------------------------------------------
    # Document type anchor patterns — strongly indicate a specific type.
    # Anchors are checked before general signal scoring for classification.
    # -------------------------------------------------------------------

    _BL_ANCHORS = [
        r"\bb/?l\s*(?:no|number|#|:)",
        r"\bbol\s*(?:no|number|#|:)",
        r"\bbill\s+of\s+lading\b",
        r"\bfreight\s+(?:prepaid|collect)\b",
        r"\bclean\s+on\s+board\b",
        r"\bmaster\s+b/?l\b",
        r"\bhouse\s+b/?l\b",
        r"\bnegotiable\s+bill\b",
        r"\bshipped\s+on\s+board\b",
    ]

    _INVOICE_ANCHORS = [
        r"\bcommercial\s+invoice\b",
        r"\binvoice\s+(?:no|number|#|date)\b",
        r"\binv\.?\s*(?:no|number|#)\b",
        r"\bproforma\s+invoice\b",
    ]

    _PACKING_ANCHORS = [
        r"\bpacking\s+list\b",
        r"\bpack(?:ing)?\s+slip\b",
        r"\bp/?l\s*(?:no|number|#|:)\b",
    ]

    _COO_ANCHORS = [
        r"\bcertificate\s+of\s+origin\b",
        r"\bwe\s+hereby\s+certify\b",
        r"\bchamber\s+of\s+commerce\b",
        r"\bform\s+a\b",
        r"\bgsp\s+form\b",
        r"\busmca\s+cert",
        r"\bnafta\s+cert",
    ]

    _ARRIVAL_ANCHORS = [
        r"\barrival\s+notice\b",
        r"\bpre.?arrival\b",
        r"\bdelivery\s+notice\b",
        r"\beta\b",
        r"\bestimated\s+(?:time\s+of\s+)?arrival\b",
    ]

    _ISF_ANCHORS = [
        r"\bisf\b",
        r"\b10\s*\+\s*2\b",
        r"\bimporter\s+security\s+filing\b",
        r"\b19\s+cfr\s+149\b",
        r"\bimporter\s+of\s+record\b",
        r"\bcbp\s+bond\b",
    ]

    # Maps document type → which of the 10 signal categories are relevant
    # for type scoring (used when anchors are absent)
    _TYPE_SIGNAL_MAP = {
        DocumentType.BILL_OF_LADING: {
            "shipper", "consignee", "vessel", "port", "weight", "package",
        },
        DocumentType.COMMERCIAL_INVOICE: {
            "shipper", "consignee", "invoice", "value", "hs", "origin",
        },
        DocumentType.PACKING_LIST: {
            "shipper", "consignee", "package", "weight", "origin",
        },
        DocumentType.CERTIFICATE_OF_ORIGIN: {
            "origin", "shipper", "consignee", "hs",
        },
        DocumentType.ARRIVAL_NOTICE: {
            "vessel", "port", "consignee",
        },
        DocumentType.ISF_FILING: {
            "hs", "shipper", "consignee", "vessel",
        },
    }

    # Short signal-category names (keys used in count_trade_signals return dict
    # and in _TYPE_SIGNAL_MAP above)
    _SIGNAL_CATEGORIES = {
        "shipper": None,    # resolved to compiled pattern at __init__
        "consignee": None,
        "vessel": None,
        "port": None,
        "value": None,
        "weight": None,
        "hs": None,
        "origin": None,
        "invoice": None,
        "package": None,
        "general": None,
    }

    def __init__(self) -> None:
        """Compile all regex patterns once at construction time."""

        def _make_pattern(keywords: list) -> re.Pattern:
            """
            Build a single alternation pattern from keyword list.
            Longer phrases are sorted first so they match before their
            component words (e.g., "port of loading" before "port").
            """
            sorted_kws = sorted(keywords, key=len, reverse=True)
            escaped = [re.escape(k) for k in sorted_kws]
            return re.compile("|".join(escaped), re.IGNORECASE)

        # Compile the 10 trade signal categories
        self._signal_patterns = {
            "shipper":   _make_pattern(self.SHIPPER_SIGNALS),
            "consignee": _make_pattern(self.CONSIGNEE_SIGNALS),
            "vessel":    _make_pattern(self.VESSEL_SIGNALS),
            "port":      _make_pattern(self.PORT_SIGNALS),
            "value":     _make_pattern(self.VALUE_SIGNALS),
            "weight":    _make_pattern(self.WEIGHT_SIGNALS),
            "hs":        _make_pattern(self.HS_SIGNALS),
            "origin":    _make_pattern(self.ORIGIN_SIGNALS),
            "invoice":   _make_pattern(self.INVOICE_SIGNALS),
            "package":   _make_pattern(self.PACKAGE_SIGNALS),
            "general":   _make_pattern(self._GENERAL_TRADE_TERMS),
        }

        # Compile non-trade patterns
        self._recipe_pattern  = _make_pattern(self._RECIPE_PATTERNS)
        self._social_pattern  = _make_pattern(self._SOCIAL_PATTERNS)
        self._code_pattern    = _make_pattern(self._CODE_PATTERNS)
        self._news_pattern    = _make_pattern(self._NEWS_PATTERNS)

        # Compile document type anchor patterns.
        # Each anchor pattern is compiled individually so findall() can count
        # matches per pattern (needed for score-based type classification).
        def _compile_anchor_list(patterns: list) -> list:
            return [re.compile(p, re.IGNORECASE) for p in patterns]

        self._bl_anchors_compiled      = _compile_anchor_list(self._BL_ANCHORS)
        self._invoice_anchors_compiled = _compile_anchor_list(self._INVOICE_ANCHORS)
        self._packing_anchors_compiled = _compile_anchor_list(self._PACKING_ANCHORS)
        self._coo_anchors_compiled     = _compile_anchor_list(self._COO_ANCHORS)
        self._arrival_anchors_compiled = _compile_anchor_list(self._ARRIVAL_ANCHORS)
        self._isf_anchors_compiled     = _compile_anchor_list(self._ISF_ANCHORS)

        # Ordered list for anchor scoring in detect_document_type.
        self._anchor_type_pairs = [
            (self._bl_anchors_compiled,      DocumentType.BILL_OF_LADING),
            (self._invoice_anchors_compiled, DocumentType.COMMERCIAL_INVOICE),
            (self._packing_anchors_compiled, DocumentType.PACKING_LIST),
            (self._coo_anchors_compiled,     DocumentType.CERTIFICATE_OF_ORIGIN),
            (self._arrival_anchors_compiled, DocumentType.ARRIVAL_NOTICE),
            (self._isf_anchors_compiled,     DocumentType.ISF_FILING),
        ]

        # URL pattern for URL density check
        self._url_pattern = re.compile(r'https?://\S+', re.IGNORECASE)

        # Emoji unicode ranges — compiled once
        self._emoji_pattern = re.compile(
            "["
            "\U0001F300-\U0001F9FF"   # misc symbols and pictographs
            "\U00002600-\U000027BF"   # misc symbols
            "\U0001FA00-\U0001FAFF"   # chess, symbols extended
            "\U00002700-\U000027BF"   # dingbats
            "]+",
            re.UNICODE,
        )

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def validate_document(self, text: str) -> ValidationResult:
        """
        Run all validation checks on a single document text.

        Returns a ValidationResult. Never raises — all errors produce a
        REJECTED result with an appropriate rejection_reason.

        Parameters
        ----------
        text:
            The plain-text content of the document.
        """
        if not text or not text.strip():
            return self._make_rejected("TOO_SHORT", _MSG_TOO_SHORT, DocumentType.UNKNOWN)

        # 1. Pre-flight checks (structural, format-based)
        preflight = self._run_preflight_checks(text)
        if preflight is not None:
            return preflight

        # 2. Count trade signals across all 10 categories
        signal_counts = self.count_trade_signals(text)
        matched_categories = [cat for cat, matched in signal_counts.items()
                               if matched and cat != "general"]
        general_matched = signal_counts.get("general", False)
        total_trade = len(matched_categories) + (1 if general_matched else 0)

        # 3. Non-trade content checks (only when trade signals are low).
        # Threshold is 3: code and recipe documents that mention trade keywords
        # inside string literals or commodity descriptions should still be
        # caught when their overall signal count is low.
        if total_trade < 3:
            nontrade = self._check_non_trade_content(text, total_trade)
            if nontrade is not None:
                return nontrade

        # 4. Zero signal check
        if total_trade == 0:
            return self._make_rejected(
                "NO_TRADE_SIGNALS", _MSG_REJECTED, DocumentType.UNKNOWN,
            )

        # 5. URL density check
        url_count = len(self._url_pattern.findall(text))
        if url_count >= 3 and total_trade < 2:
            return self._make_rejected(
                "NO_TRADE_SIGNALS", _MSG_REJECTED, DocumentType.UNKNOWN,
            )

        # 6. Detect document type
        detected_type = self.detect_document_type(text, signal_counts)

        # 7. Assign confidence tier and build result
        confidence = self.calculate_confidence(total_trade)
        all_signals = [cat for cat, matched in signal_counts.items() if matched]

        if confidence == ConfidenceLevel.REJECTED:
            # Shouldn't reach here (caught above) but guard defensively
            return self._make_rejected(
                "NO_TRADE_SIGNALS", _MSG_REJECTED, detected_type,
                signals=all_signals,
            )

        if confidence == ConfidenceLevel.LOW:
            return ValidationResult(
                is_valid=True,   # warned but not blocked
                confidence=ConfidenceLevel.LOW,
                detected_type=detected_type,
                signals_found=all_signals,
                signal_count=total_trade,
                rejection_reason=None,
                user_message=_MSG_WARN_LOW,
                warning_message=_MSG_WARN_LOW,
            )

        # MEDIUM or HIGH — unrecognized type gets a softer message
        if detected_type == DocumentType.UNRECOGNIZED_TRADE:
            return ValidationResult(
                is_valid=True,
                confidence=confidence,
                detected_type=detected_type,
                signals_found=all_signals,
                signal_count=total_trade,
                rejection_reason=None,
                user_message=_MSG_UNRECOGNIZED_TRADE,
                warning_message=_MSG_UNRECOGNIZED_TRADE,
            )

        return ValidationResult(
            is_valid=True,
            confidence=confidence,
            detected_type=detected_type,
            signals_found=all_signals,
            signal_count=total_trade,
            rejection_reason=None,
            user_message="Document validated successfully.",
            warning_message=None,
        )

    def count_trade_signals(self, text: str) -> dict:
        """
        Check which of the 11 signal categories are present in the text.

        Returns
        -------
        dict
            Keys are signal category names; values are True (matched) or
            False (not matched).  Includes "general" as an 11th category.
        """
        return {
            cat: bool(pattern.search(text))
            for cat, pattern in self._signal_patterns.items()
        }

    def detect_document_type(self, text: str, signal_counts: Optional[dict] = None) -> DocumentType:
        """
        Classify the document into one of the known trade document types.

        Uses anchor patterns first (strong indicators), then falls back to
        signal scoring. The type with the most matched signals (above
        threshold ≥ 3 total) wins.

        Parameters
        ----------
        text:
            The document text.
        signal_counts:
            Pre-computed result from count_trade_signals(). If None,
            computed internally (avoids double-scanning in validate_document).

        Returns
        -------
        DocumentType
        """
        if signal_counts is None:
            signal_counts = self.count_trade_signals(text)

        # --- Anchor scoring: count matches for each type, pick the winner ---
        # Documents like arrival notices often contain "B/L No." as a reference,
        # so scoring all anchors prevents the B/L anchor from dominating.
        anchor_scores: dict = {}
        for compiled_list, doc_type in self._anchor_type_pairs:
            score = sum(
                1 for pattern in compiled_list if pattern.search(text)
            )
            anchor_scores[doc_type] = score

        best_anchor_type = max(anchor_scores, key=lambda t: anchor_scores[t])
        best_anchor_score = anchor_scores[best_anchor_type]

        if best_anchor_score > 0:
            # Check for a tie — if tied, fall through to signal scoring
            tied_anchor = [t for t, s in anchor_scores.items() if s == best_anchor_score]
            if len(tied_anchor) == 1:
                return best_anchor_type
            # On tie, the type with the highest signal score among tied wins

        # --- Signal scoring fallback ---
        # Count how many of each type's relevant signal categories matched
        type_scores: dict = {}
        for doc_type, relevant_cats in self._TYPE_SIGNAL_MAP.items():
            score = sum(1 for cat in relevant_cats if signal_counts.get(cat, False))
            type_scores[doc_type] = score

        best_type = max(type_scores, key=lambda t: type_scores[t])
        best_score = type_scores[best_type]

        if best_score < 3:
            # Not enough type-specific signals to classify
            total_any = sum(1 for v in signal_counts.values() if v)
            return DocumentType.UNRECOGNIZED_TRADE if total_any >= 3 else DocumentType.UNKNOWN

        # Tie-breaking: if two types share the best score, pick the one whose
        # matched ratio (matched / total defined categories) is highest
        tied = [t for t, s in type_scores.items() if s == best_score]
        if len(tied) > 1:
            best_type = max(
                tied,
                key=lambda t: type_scores[t] / len(self._TYPE_SIGNAL_MAP[t]),
            )

        return best_type

    def calculate_confidence(self, signal_count: int) -> ConfidenceLevel:
        """
        Map a total signal count to a confidence tier.

        Parameters
        ----------
        signal_count:
            Total count of distinct trade signal categories matched.
            Includes the "general" category if matched.

        Returns
        -------
        ConfidenceLevel
        """
        if signal_count == 0:
            return ConfidenceLevel.REJECTED
        if signal_count <= 2:
            return ConfidenceLevel.LOW
        if signal_count <= 4:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.HIGH

    def is_non_trade_content(self, text: str) -> bool:
        """
        Return True if the text matches known non-trade content patterns.

        Checks recipe, social media, code, and news patterns.  Requires zero
        trade signals to avoid false positives on trade documents that incidentally
        contain non-trade vocabulary (e.g., food commodity invoices mentioning
        cooking terms).

        This method is informational — use validate_document() for the full decision.
        """
        signal_counts = self.count_trade_signals(text)
        total_trade = sum(1 for cat, matched in signal_counts.items()
                          if matched and cat != "general")

        social_hits = len(self._social_pattern.findall(text))
        if social_hits >= 2:
            return True

        if total_trade < 3:
            recipe_hits = len(self._recipe_pattern.findall(text))
            code_hits   = len(self._code_pattern.findall(text))
            news_hits   = len(self._news_pattern.findall(text))
            if (recipe_hits >= 3 and total_trade == 0) or \
               (code_hits >= 3 and total_trade < 3) or \
               (news_hits >= 2 and total_trade < 2):
                return True

        return False

    def generate_user_message(self, result: ValidationResult) -> str:
        """Return the user-facing message for a ValidationResult."""
        return result.user_message

    # -------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------

    def _run_preflight_checks(self, text: str) -> Optional[ValidationResult]:
        """
        Run structural pre-flight checks that do not require signal scanning.
        Returns a REJECTED ValidationResult if any check fails, else None.
        """
        # Word count floor
        words = text.split()
        if len(words) < 20:
            return self._make_rejected("TOO_SHORT", _MSG_TOO_SHORT, DocumentType.UNKNOWN)

        # Emoji density check
        non_ws = text.replace(" ", "").replace("\n", "").replace("\t", "")
        if non_ws:
            emoji_chars = sum(len(m.group()) for m in self._emoji_pattern.finditer(text))
            emoji_ratio = emoji_chars / len(non_ws)
            if emoji_ratio > 0.15:
                return self._make_rejected(
                    "SOCIAL_MEDIA_CONTENT", _MSG_SOCIAL_MEDIA, DocumentType.UNKNOWN,
                )

        # Social media structural markers (independent of trade signals)
        social_hits = len(self._social_pattern.findall(text))
        if social_hits >= 2:
            return self._make_rejected(
                "SOCIAL_MEDIA_CONTENT", _MSG_SOCIAL_MEDIA, DocumentType.UNKNOWN,
            )

        return None

    def _check_non_trade_content(self, text: str, total_trade_signals: int) -> Optional[ValidationResult]:
        """
        Check for recipe, code, and news content patterns.
        Only called when total_trade_signals < 3 (outer gate in validate_document).
        Returns a REJECTED ValidationResult if detected, else None.
        """
        # Recipe check: 3+ recipe signals AND 0 trade signals
        recipe_hits = len(self._recipe_pattern.findall(text))
        if recipe_hits >= 3 and total_trade_signals == 0:
            return self._make_rejected(
                "RECIPE_CONTENT", _MSG_RECIPE, DocumentType.UNKNOWN,
            )

        # Code check: 3+ code signals AND < 3 trade signals.
        # Threshold < 3 (not == 0) because code parsing trade documents may
        # reference trade keywords inside string literals (e.g., r'CONSIGNEE:').
        code_hits = len(self._code_pattern.findall(text))
        if code_hits >= 3 and total_trade_signals < 3:
            return self._make_rejected(
                "CODE_CONTENT", _MSG_CODE, DocumentType.UNKNOWN,
            )

        # News check: 3+ news signals AND < 2 trade signals
        news_hits = len(self._news_pattern.findall(text))
        if news_hits >= 3 and total_trade_signals < 2:
            return self._make_rejected(
                "NEWS_CONTENT", _MSG_NEWS, DocumentType.UNKNOWN,
            )

        return None

    @staticmethod
    def _make_rejected(
        reason: str,
        message: str,
        detected_type: DocumentType,
        signals: Optional[list] = None,
    ) -> ValidationResult:
        return ValidationResult(
            is_valid=False,
            confidence=ConfidenceLevel.REJECTED,
            detected_type=detected_type,
            signals_found=signals or [],
            signal_count=0,
            rejection_reason=reason,
            user_message=message,
            warning_message=None,
        )


# ---------------------------------------------------------------------------
# Module-level singleton and convenience functions
# ---------------------------------------------------------------------------

_validator: Optional[DocumentValidator] = None


def get_validator() -> DocumentValidator:
    """Return the module-level DocumentValidator singleton."""
    global _validator
    if _validator is None:
        _validator = DocumentValidator()
    return _validator


def validate_documents(documents: list) -> list:
    """
    Validate a list of document objects.

    Parameters
    ----------
    documents:
        List of objects with `raw_text` (str) and `filename` (str | None) attributes.

    Returns
    -------
    list[ValidationResult]
        One result per document, in the same order as the input list.
    """
    validator = get_validator()
    results = []
    for doc in documents:
        text = getattr(doc, "raw_text", "") or ""
        result = validator.validate_document(text)
        results.append(result)
    return results


def build_rejection_error(
    rejected_results: list,
    filenames: list,
    total_docs: int,
) -> dict:
    """
    Build the structured 422 error detail dict for rejected documents.

    Parameters
    ----------
    rejected_results:
        ValidationResult objects with is_valid=False.
    filenames:
        Original filenames in the same order as results (for display).
    total_docs:
        Total number of documents submitted (for the summary message).
    """
    n = len(rejected_results)
    noun = "document" if n == 1 else "documents"
    rejected_list = []
    for result, filename in zip(rejected_results, filenames):
        rejected_list.append({
            "filename": filename or "Unknown document",
            "reason": result.rejection_reason,
            "signals_matched": result.signal_count,
            "signal_names": result.signals_found,
            "message": result.user_message,
        })
    return {
        "code": "DOCUMENT_VALIDATION_FAILED",
        "message": (
            f"{n} of {total_docs} {noun} could not be validated as "
            f"trade {'documents' if total_docs > 1 else 'document'}."
        ),
        "rejected_documents": rejected_list,
    }
