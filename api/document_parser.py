"""
document_parser.py — File-to-text extraction for PORTGUARD.

Supports .pdf and plain-text files (.txt and any other text format).
Returns a clean string in all cases so the analysis pipeline never needs
to know what format the original document was in.

Public API
----------
extract_text(file_bytes, filename) -> ExtractionResult
    Main entry point.  Dispatch on filename extension / magic bytes.

Exceptions (all subclass DocumentParserError)
----------------------------------------------
ScannedPDFError          — PDF has no machine-readable text layer
PasswordProtectedPDFError — PDF is encrypted
CorruptPDFError          — PDF bytes are malformed / unreadable
FileSizeError            — File exceeds MAX_FILE_BYTES
PageLimitError           — PDF exceeds MAX_PAGES
UnsupportedFormatError   — File is not a recognised format
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfminer.pdfparser import PDFSyntaxError

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

MAX_FILE_BYTES: int = 10 * 1024 * 1024   # 10 MB
MAX_PAGES: int = 50

# Minimum total characters before we consider a PDF "scanned-only".
# A single-page document with a handful of header lines can be ~80 chars,
# so the threshold is kept low enough not to false-positive on short docs.
_MIN_TEXT_CHARS: int = 80

# Magic bytes that identify a PDF regardless of filename extension.
_PDF_MAGIC: bytes = b"%PDF"

# ---------------------------------------------------------------------------
# Result and error types
# ---------------------------------------------------------------------------


@dataclass
class ExtractionResult:
    """Holds extracted text and metadata about the extraction."""

    text: str
    """Full extracted text, ready to pass to the analysis pipeline."""

    page_count: int
    """Number of pages (1 for plain-text files)."""

    warnings: list[str] = field(default_factory=list)
    """Non-fatal issues encountered during extraction (e.g. image-only pages)."""


class DocumentParserError(Exception):
    """Base class for all document-parser errors."""

    #: Short machine-readable code surfaced in API error responses.
    code: str = "PARSER_ERROR"


class ScannedPDFError(DocumentParserError):
    """Raised when a PDF contains no machine-readable text layer."""

    code = "SCANNED_PDF"


class PasswordProtectedPDFError(DocumentParserError):
    """Raised when a PDF is encrypted and cannot be opened without a password."""

    code = "PASSWORD_PROTECTED"


class CorruptPDFError(DocumentParserError):
    """Raised when PDF bytes are malformed and cannot be parsed at all."""

    code = "CORRUPT_PDF"


class FileSizeError(DocumentParserError):
    """Raised when the uploaded file exceeds MAX_FILE_BYTES."""

    code = "FILE_TOO_LARGE"


class PageLimitError(DocumentParserError):
    """Raised when a PDF contains more pages than MAX_PAGES."""

    code = "TOO_MANY_PAGES"


class UnsupportedFormatError(DocumentParserError):
    """Raised when the file is neither a PDF nor a readable text file."""

    code = "UNSUPPORTED_FORMAT"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_pdf(file_bytes: bytes, filename: str) -> bool:
    """Return True if the file is a PDF, checked by magic bytes first.

    The extension is only used as a secondary signal; magic bytes are
    authoritative so that spoofed filenames are handled correctly.
    """
    if file_bytes[:4] == _PDF_MAGIC:
        return True
    return filename.lower().endswith(".pdf")


def _table_to_text(table: list[list[str | None]]) -> str:
    """Serialise a pdfplumber table (list-of-rows) to tab-separated text.

    None cells — which pdfplumber uses for merged/empty cells — are
    replaced with an empty string so the row structure stays readable.
    """
    rows = [
        "\t".join("" if cell is None else cell.strip() for cell in row)
        for row in table
        if any(cell for cell in row)   # skip completely empty rows
    ]
    return "\n".join(rows)


def _extract_page_text(page: "pdfplumber.page.Page") -> tuple[str, bool]:
    """Extract all text from a single pdfplumber page.

    Returns (text, image_only) where image_only is True when the page
    yielded no text at all (likely a scanned/rasterised page).

    Strategy:
    1. Extract text with layout=True to preserve column order.
    2. Extract any detected tables and append them as tab-separated rows.
       Tables often contain the most structured data (line items, values)
       and layout-mode text can merge adjacent cells incorrectly.
    """
    body = page.extract_text(layout=True) or ""

    table_texts: list[str] = []
    try:
        for table in page.extract_tables():
            serialised = _table_to_text(table)
            if serialised:
                table_texts.append(serialised)
    except Exception:
        # Table extraction is best-effort; never let it abort page extraction.
        pass

    combined = body
    if table_texts:
        combined = body + "\n\n[TABLES]\n" + "\n\n".join(table_texts)

    image_only = len(combined.strip()) == 0
    return combined, image_only


def _extract_pdf_text(file_bytes: bytes) -> ExtractionResult:
    """Extract all text from a PDF given its raw bytes.

    Iterates every page, collects body text and table data, and joins
    pages with a clear separator.  Raises specific exceptions for
    password-protected, corrupt, over-limit, and scanned-only PDFs.
    """
    try:
        pdf = pdfplumber.open(io.BytesIO(file_bytes))
    except PDFPasswordIncorrect:
        raise PasswordProtectedPDFError(
            "This PDF is password-protected. Remove the password and re-upload."
        )
    except PDFSyntaxError as exc:
        raise CorruptPDFError(f"PDF syntax error — file may be corrupt: {exc}") from exc
    except Exception as exc:
        # Catch-all for other pdfminer/pdfplumber parse failures.
        raise CorruptPDFError(f"Could not open PDF: {exc}") from exc

    with pdf:
        page_count = len(pdf.pages)

        if page_count > MAX_PAGES:
            raise PageLimitError(
                f"PDF has {page_count} pages; maximum allowed is {MAX_PAGES}. "
                "Split the document and re-upload."
            )

        page_texts: list[str] = []
        image_only_pages: list[int] = []
        warnings: list[str] = []

        for i, page in enumerate(pdf.pages, start=1):
            text, image_only = _extract_page_text(page)
            if image_only:
                image_only_pages.append(i)
                page_texts.append(f"[Page {i}: image content — text not extractable]")
            else:
                page_texts.append(f"--- Page {i} ---\n{text.strip()}")

    if image_only_pages:
        if len(image_only_pages) == page_count:
            raise ScannedPDFError(
                "This PDF appears to be a scanned image with no machine-readable text. "
                "Use OCR software to convert it to searchable text before uploading."
            )
        warnings.append(
            f"Page(s) {', '.join(str(p) for p in image_only_pages)} appear to be "
            "scanned images; text could not be extracted from those pages."
        )

    full_text = "\n\n".join(page_texts)

    if len(full_text.strip()) < _MIN_TEXT_CHARS:
        raise ScannedPDFError(
            "Very little text was extracted from this PDF. "
            "It may be a scanned document. "
            "Use OCR software to convert it to searchable text before uploading."
        )

    return ExtractionResult(text=full_text, page_count=page_count, warnings=warnings)


def _extract_plain_text(file_bytes: bytes, filename: str) -> ExtractionResult:
    """Decode a plain-text file to a string.

    Tries UTF-8 first, then falls back to Latin-1 (Windows-1252 superset),
    which covers virtually all legacy customs EDI exports.  The filename is
    only used for error messages.
    """
    try:
        return ExtractionResult(text=file_bytes.decode("utf-8"), page_count=1)
    except UnicodeDecodeError:
        pass

    try:
        return ExtractionResult(
            text=file_bytes.decode("latin-1"),
            page_count=1,
            warnings=["File was not valid UTF-8; decoded as Latin-1."],
        )
    except UnicodeDecodeError as exc:
        raise UnsupportedFormatError(
            f"Could not decode '{filename}' as UTF-8 or Latin-1."
        ) from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_text(file_bytes: bytes, filename: str) -> ExtractionResult:
    """Extract clean text from an uploaded file, regardless of format.

    This is the single entry point for all document parsing in PORTGUARD.
    Callers receive an ExtractionResult with a plain string in .text that
    can be passed directly to the analysis pipeline — no format-specific
    logic is needed downstream.

    Supported formats:
      - PDF (.pdf or %PDF magic bytes) — via pdfplumber
      - Plain text (.txt and any other non-PDF file) — UTF-8 / Latin-1

    Parameters
    ----------
    file_bytes:
        Raw bytes of the uploaded file.
    filename:
        Original filename, used for extension-based dispatch and error messages.

    Returns
    -------
    ExtractionResult
        .text        — extracted text string
        .page_count  — number of pages (always 1 for plain-text files)
        .warnings    — list of non-fatal messages (e.g. image-only pages)

    Raises
    ------
    FileSizeError             if len(file_bytes) > MAX_FILE_BYTES
    PageLimitError            if the PDF has more than MAX_PAGES pages
    ScannedPDFError           if no machine-readable text could be found
    PasswordProtectedPDFError if the PDF is encrypted
    CorruptPDFError           if the PDF cannot be parsed
    UnsupportedFormatError    if the file cannot be decoded as text
    """
    if len(file_bytes) > MAX_FILE_BYTES:
        mb = len(file_bytes) / (1024 * 1024)
        raise FileSizeError(
            f"File is {mb:.1f} MB; maximum allowed size is "
            f"{MAX_FILE_BYTES // (1024 * 1024)} MB."
        )

    if _is_pdf(file_bytes, filename):
        return _extract_pdf_text(file_bytes)

    return _extract_plain_text(file_bytes, filename)
