"""
portguard/bulk_parsers.py — Input parsers for the bulk shipment screening feature.

Three input formats are supported:

  parse_zip_upload(zip_bytes)   — ZIP where each top-level subfolder = one shipment
  parse_csv_upload(csv_bytes)   — CSV where each row = one shipment, columns = doc types
  validate_manual_input(list)   — JSON list already parsed from the request body

All three functions return the same shape:
  list of {"ref": str, "documents": [{"filename": str, "raw_text": str}]}

Exceptions raised on bad input:
  BulkParseError      — base class
  InvalidZipError     — ZIP is corrupt, encrypted, or contains no valid shipments
  InvalidCsvError     — CSV is malformed, missing required columns, or empty
  BatchTooLargeError  — batch exceeds MAX_BATCH_SIZE
  EmptyBatchError     — no valid shipments found after parsing
"""

from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_BATCH_SIZE: int = 50
MAX_ZIP_BYTES: int = 50 * 1024 * 1024    # 50 MB
MAX_CSV_BYTES: int = 5 * 1024 * 1024     # 5 MB
MAX_DOCS_PER_SHIPMENT: int = 10

# Columns that are recognised as document text in a CSV upload — named/canonical set.
# Order determines how they appear as filenames.
_CSV_DOC_COLUMNS: list[str] = [
    "bill_of_lading",
    "commercial_invoice",
    "packing_list",
    "certificate_of_origin",
    "isf_filing",
    "other_doc_1",
]

# Generic column names also accepted as document text — tried when no canonical
# column is present.  A CSV with a single "text" or "description" column is a
# valid input: the entire cell becomes the shipment document.
_CSV_DOC_COLUMNS_GENERIC: list[str] = [
    "document_text",
    "text",
    "content",
    "shipment",
    "description",
]

# Columns that are recognised as the shipment reference ID in a CSV upload.
_CSV_REF_COLUMNS: list[str] = [
    "reference_id",
    "shipment_ref",
    "ref",
    "id",
    "reference",
    "shipment_id",
]

# Characters stripped from reference IDs — only word chars, spaces, hyphens,
# dots, slashes, and parentheses are allowed.
_INVALID_REF_CHARS = re.compile(r"[^\w\s\-\./()\[\]]")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BulkParseError(Exception):
    """Base class for all bulk-input parse errors."""
    code: str = "BULK_PARSE_ERROR"


class InvalidZipError(BulkParseError):
    """ZIP file is corrupt, encrypted, password-protected, or contains no usable shipments."""
    code = "INVALID_ZIP"


class InvalidCsvError(BulkParseError):
    """CSV file is malformed, missing required columns, or unreadable."""
    code = "INVALID_CSV"


class BatchTooLargeError(BulkParseError):
    """Batch contains more shipments than MAX_BATCH_SIZE."""
    code = "BATCH_TOO_LARGE"


class EmptyBatchError(BulkParseError):
    """No valid shipments could be extracted from the input."""
    code = "EMPTY_BATCH"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sanitize_ref(ref: str) -> str:
    """Strip disallowed characters from a shipment reference ID.

    Returns an empty string if nothing survives the strip, so the caller
    must handle the empty-string case.
    """
    cleaned = _INVALID_REF_CHARS.sub("", ref).strip()
    return cleaned[:120]


# ---------------------------------------------------------------------------
# ZIP parser
# ---------------------------------------------------------------------------


def parse_zip_upload(zip_bytes: bytes) -> list[dict]:
    """Parse a ZIP archive into a list of shipment document sets.

    Expected ZIP structure::

        batch.zip
        ├── SHP-001/
        │   ├── bill_of_lading.txt
        │   ├── commercial_invoice.txt
        │   └── packing_list.txt
        └── SHP-002/
            ├── bill_of_lading.pdf
            └── commercial_invoice.txt

    Each top-level subfolder becomes one shipment.  The folder name is used as
    the shipment reference ID.  Files at the root level are ignored.  Nested
    subdirectories are flattened into the parent shipment folder.  Hidden files
    (starting with ``.``) and ``__MACOSX`` entries are skipped.  Only ``.txt``
    and ``.pdf`` files are read.

    Parameters
    ----------
    zip_bytes:
        Raw bytes of the uploaded ZIP file.

    Returns
    -------
    list of ``{"ref": str, "documents": [{"filename": str, "raw_text": str}]}``

    Raises
    ------
    InvalidZipError
        ZIP is too large, corrupt, password-protected, or empty.
    BatchTooLargeError
        More than MAX_BATCH_SIZE shipment folders found.
    EmptyBatchError
        No extractable documents found in any folder.
    """
    if len(zip_bytes) > MAX_ZIP_BYTES:
        mb = len(zip_bytes) // (1024 * 1024)
        raise InvalidZipError(
            f"ZIP file is {mb} MB; maximum allowed size is "
            f"{MAX_ZIP_BYTES // (1024 * 1024)} MB."
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise InvalidZipError(f"ZIP file is corrupt or not a valid ZIP archive: {exc}") from exc
    except Exception as exc:
        raise InvalidZipError(f"Could not open ZIP file: {exc}") from exc

    # Reject password-protected entries (flag bit 0 = encrypted).
    for info in zf.infolist():
        if info.flag_bits & 0x1:
            raise InvalidZipError(
                "ZIP file is password-protected. "
                "Please provide an unencrypted ZIP archive."
            )

    shipments: dict[str, list[dict]] = {}

    for entry in zf.namelist():
        # Normalize path separators and strip outer slashes.
        norm = entry.replace("\\", "/").strip("/")
        parts = norm.split("/")

        # Root-level entries (no subfolder) are ignored.
        if len(parts) < 2:
            continue

        folder = parts[0]
        filename = parts[-1]

        # Skip directory entries and macOS metadata.
        if not filename or entry.endswith("/"):
            continue
        if filename.startswith(".") or "__MACOSX" in parts:
            continue

        # Only process recognised text/document extensions.
        lower_fn = filename.lower()
        if not (lower_fn.endswith(".txt") or lower_fn.endswith(".pdf")):
            logger.debug("Skipping non-text/PDF entry in ZIP: %s", entry)
            continue

        # Enforce per-shipment document limit.
        if folder in shipments and len(shipments[folder]) >= MAX_DOCS_PER_SHIPMENT:
            logger.warning(
                "Shipment '%s' already has %d documents; skipping '%s'",
                folder, MAX_DOCS_PER_SHIPMENT, filename,
            )
            continue

        # Read raw bytes.
        try:
            raw_bytes = zf.read(entry)
        except Exception as exc:
            logger.warning("Could not read ZIP entry '%s': %s", entry, exc)
            continue

        # Extract text.
        if lower_fn.endswith(".pdf"):
            try:
                from api.document_parser import extract_text, DocumentParserError
                result = extract_text(raw_bytes, filename)
                text = result.text
            except Exception as exc:
                logger.warning("PDF text extraction failed for '%s': %s — skipping", entry, exc)
                continue
        else:
            text = raw_bytes.decode("utf-8", errors="replace")

        if not text or not text.strip():
            logger.debug("Skipping empty file: %s", entry)
            continue

        shipments.setdefault(folder, []).append(
            {"filename": filename, "raw_text": text.strip()}
        )

    zf.close()

    # Filter folders that yielded at least one document and sanitize refs.
    result_list: list[dict] = []
    for folder, docs in shipments.items():
        if not docs:
            continue
        ref = _sanitize_ref(folder) or folder[:120]
        result_list.append({"ref": ref, "documents": docs})

    if not result_list:
        raise EmptyBatchError(
            "No valid shipments found in ZIP. "
            "Ensure each top-level subfolder contains at least one .txt or .pdf file."
        )

    if len(result_list) > MAX_BATCH_SIZE:
        raise BatchTooLargeError(
            f"ZIP contains {len(result_list)} shipment folders; "
            f"maximum batch size is {MAX_BATCH_SIZE}."
        )

    return result_list


# ---------------------------------------------------------------------------
# CSV parser
# ---------------------------------------------------------------------------


def parse_csv_upload(csv_bytes: bytes) -> list[dict]:
    """Parse a CSV file into a list of shipment document sets.

    Expected columns::

        reference_id | shipment_ref | ref | id | reference  (one of these)
        bill_of_lading
        commercial_invoice
        packing_list
        certificate_of_origin
        isf_filing
        other_doc_1

    Only the reference column is required.  Document columns are optional;
    any non-empty document column value is included.  Rows with no document
    text are skipped.  Duplicate reference IDs keep the first occurrence.

    Parameters
    ----------
    csv_bytes:
        Raw bytes of the uploaded CSV file.

    Returns
    -------
    list of ``{"ref": str, "documents": [{"filename": str, "raw_text": str}]}``

    Raises
    ------
    InvalidCsvError
        File is too large, unreadable, malformed, or missing the reference column.
    BatchTooLargeError
        More than MAX_BATCH_SIZE rows found after filtering.
    EmptyBatchError
        No valid shipment rows found.
    """
    if len(csv_bytes) > MAX_CSV_BYTES:
        kb = len(csv_bytes) // 1024
        raise InvalidCsvError(
            f"CSV file is {kb} KB; maximum allowed size is "
            f"{MAX_CSV_BYTES // (1024 * 1024)} MB."
        )

    try:
        text = csv_bytes.decode("utf-8-sig", errors="replace")
    except Exception as exc:
        raise InvalidCsvError(f"Could not decode CSV as UTF-8: {exc}") from exc

    try:
        sample = io.StringIO(text)
        dialect = csv.Sniffer().sniff(sample.read(4096), delimiters=",\t|")
        sample.seek(0)
    except csv.Error:
        dialect = csv.excel  # default fallback

    try:
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    except csv.Error as exc:
        raise InvalidCsvError(f"CSV parse error: {exc}") from exc

    # Materialise to get fieldnames.
    try:
        rows = list(reader)
    except csv.Error as exc:
        raise InvalidCsvError(f"CSV parse error reading rows: {exc}") from exc

    if not rows:
        raise EmptyBatchError("CSV file contains no data rows.")

    fieldnames = reader.fieldnames or []
    fn_lower = {f.lower().strip(): f for f in fieldnames if f}

    # Locate the reference column (case-insensitive).
    ref_col: Optional[str] = None
    for opt in _CSV_REF_COLUMNS:
        if opt in fn_lower:
            ref_col = fn_lower[opt]
            break

    if ref_col is None:
        raise InvalidCsvError(
            "CSV is missing a reference ID column. "
            f"Expected one of: {', '.join(_CSV_REF_COLUMNS)}. "
            f"Found columns: {', '.join(fieldnames) or '(none)'}."
        )

    # Build document-column mapping (case-insensitive, canonical → actual fieldname).
    # Priority 1: named/canonical document columns.
    doc_col_map: dict[str, str] = {}
    for col in _CSV_DOC_COLUMNS:
        if col in fn_lower:
            doc_col_map[col] = fn_lower[col]

    # Priority 2: generic column names (document_text, text, content, shipment, description).
    if not doc_col_map:
        for col in _CSV_DOC_COLUMNS_GENERIC:
            if col in fn_lower:
                doc_col_map[col] = fn_lower[col]

    # Priority 3: raw-row fallback — no recognized doc column found at all.
    # Treat every column that isn't the reference column as document text,
    # concatenated into a single "document.txt" per shipment.
    use_raw_row_fallback: bool = not doc_col_map

    shipments: list[dict] = []
    seen_refs: set[str] = set()

    for row_num, row in enumerate(rows, start=2):
        ref_raw = row.get(ref_col, "").strip()
        if not ref_raw:
            continue

        ref = _sanitize_ref(ref_raw)
        if not ref:
            logger.warning(
                "CSV row %d: reference_id is empty after sanitisation; skipping.", row_num
            )
            continue

        # Keep first occurrence of each reference ID.
        if ref in seen_refs:
            logger.info("CSV row %d: duplicate reference '%s'; skipping.", row_num, ref)
            continue
        seen_refs.add(ref)

        docs: list[dict] = []
        if use_raw_row_fallback:
            # No recognized doc columns — concatenate all non-reference field values.
            parts = [
                f"{k}: {v}".strip()
                for k, v in row.items()
                if k != ref_col and str(v).strip()
            ]
            if parts:
                docs.append({"filename": "document.txt", "raw_text": "\n".join(parts)})
        else:
            for canonical, actual in doc_col_map.items():
                val = row.get(actual, "").strip()
                if val:
                    docs.append({"filename": canonical + ".txt", "raw_text": val})

        if not docs:
            logger.debug("CSV row %d (ref=%s): no document text; skipping.", row_num, ref)
            continue

        shipments.append({"ref": ref, "documents": docs[:MAX_DOCS_PER_SHIPMENT]})

        if len(shipments) >= MAX_BATCH_SIZE:
            logger.info(
                "CSV: reached max batch size (%d) at row %d; remaining rows ignored.",
                MAX_BATCH_SIZE, row_num,
            )
            break

    if not shipments:
        raise EmptyBatchError(
            "No valid shipments found in CSV. "
            "Ensure rows have a reference ID and at least one non-empty document column."
        )

    return shipments


# ---------------------------------------------------------------------------
# Manual input validator
# ---------------------------------------------------------------------------


def validate_manual_input(shipments: list) -> list[dict]:
    """Validate and normalise manually submitted shipment data.

    Parameters
    ----------
    shipments:
        List of ``{"ref": str, "documents": [{"filename": str, "raw_text": str}]}``
        as parsed from the JSON request body.

    Returns
    -------
    list of cleaned ``{"ref": str, "documents": [...]}`` dicts.

    Raises
    ------
    EmptyBatchError
        No shipments provided.
    BatchTooLargeError
        More than MAX_BATCH_SIZE shipments provided.
    ValueError
        A specific entry is malformed (non-dict, missing documents, etc.).
    """
    if not shipments:
        raise EmptyBatchError("No shipments provided.")

    if len(shipments) > MAX_BATCH_SIZE:
        raise BatchTooLargeError(
            f"Batch contains {len(shipments)} shipments; "
            f"maximum batch size is {MAX_BATCH_SIZE}."
        )

    result: list[dict] = []
    seen_refs: set[str] = set()

    for i, s in enumerate(shipments):
        if not isinstance(s, dict):
            raise ValueError(
                f"Shipment {i + 1}: expected a JSON object, got {type(s).__name__}."
            )

        # Reference ID — auto-generate if missing.
        ref_raw = str(s.get("ref", s.get("reference_id", ""))).strip()
        if not ref_raw:
            ref_raw = f"SHP-{i + 1:03d}"
        ref = _sanitize_ref(ref_raw) or f"SHP-{i + 1:03d}"

        # Resolve duplicate refs by appending a suffix.
        original_ref = ref
        suffix = 1
        while ref in seen_refs:
            ref = f"{original_ref}-{suffix}"
            suffix += 1
        seen_refs.add(ref)

        # Documents list.
        docs_raw = s.get("documents", [])
        if not isinstance(docs_raw, list):
            raise ValueError(
                f"Shipment '{ref}': 'documents' must be a list, "
                f"got {type(docs_raw).__name__}."
            )
        if not docs_raw:
            raise ValueError(
                f"Shipment '{ref}': 'documents' list is empty. "
                "Provide at least one document."
            )

        docs: list[dict] = []
        for j, d in enumerate(docs_raw[:MAX_DOCS_PER_SHIPMENT]):
            if not isinstance(d, dict):
                continue
            # Accept either "raw_text" or "text" as the content key.
            raw_text = str(d.get("raw_text", d.get("text", ""))).strip()
            filename = str(d.get("filename", f"document_{j + 1}.txt")).strip()
            if not filename:
                filename = f"document_{j + 1}.txt"
            if raw_text:
                docs.append({"filename": filename, "raw_text": raw_text})

        if not docs:
            raise ValueError(
                f"Shipment '{ref}': no non-empty document text found."
            )

        result.append({"ref": ref, "documents": docs})

    return result
