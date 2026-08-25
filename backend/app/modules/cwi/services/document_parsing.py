"""Document parsing and chunking helpers (M6.4, Requirement 29.3).

A document is parsed **once** into plain text and chunked **once** before its
chunks are embedded. Parsing is format-aware and **degrades gracefully**:

* TXT / plain text — decoded directly, no third-party dependency.
* PDF — via ``pypdf`` when installed; otherwise a clear
  :class:`DocumentParseError` is raised rather than guessing.
* DOCX — via ``python-docx`` when installed; otherwise a clear error.
* Common email attachments that are plain-text (``message/rfc822``, ``.eml``)
  are treated as text.

The chunker splits on paragraph boundaries and packs them into bounded,
slightly-overlapping windows so retrieval evidence excerpts stay coherent.
"""

from __future__ import annotations

import io
from dataclasses import dataclass


class DocumentParseError(RuntimeError):
    """Raised when a document cannot be parsed into text."""


@dataclass(frozen=True)
class ParsedChunk:
    """One chunk of parsed text with its position metadata."""

    text: str
    chunk_index: int
    page_number: int | None = None


# Default chunk sizing (characters). Kept modest so a single chunk maps to a
# focused, citable evidence excerpt.
_CHUNK_SIZE = 1000
_CHUNK_OVERLAP = 150
_MAX_EXTRACTED_CHARS = 5_000_000
_MAX_PDF_PAGES = 500

_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_TEXT_MIME_TYPES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "message/rfc822",
}


def parse_document(data: bytes, mime_type: str, filename: str) -> str:
    """Parse ``data`` into plain text based on its type (Requirement 29.1, 29.3).

    Raises :class:`DocumentParseError` when a format-specific library is needed
    but unavailable, or when the bytes cannot be decoded — the caller marks the
    document ``FAILED`` rather than persisting empty/garbage chunks.
    """

    del filename  # Dispatch only on the validated effective MIME type.
    normalized_type = mime_type.split(";", 1)[0].strip().lower()
    if normalized_type == "application/pdf":
        text = _parse_pdf(data)
    elif normalized_type == _DOCX_MIME:
        text = _parse_docx(data)
    elif normalized_type in _TEXT_MIME_TYPES:
        text = _parse_text(data)
    else:
        raise DocumentParseError("Unsupported document type.")

    if len(text) > _MAX_EXTRACTED_CHARS:
        raise DocumentParseError("The extracted document text is too large.")
    return text


def _parse_text(data: bytes) -> str:
    try:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16", errors="strict")
        else:
            text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise DocumentParseError(
            "Text documents must use UTF-8 or BOM-marked UTF-16."
        ) from exc

    if "\ufffd" in text or "\x00" in text:
        raise DocumentParseError("The text document contains invalid characters.")
    disallowed_controls = sum(
        ord(character) < 32 and character not in "\n\r\t\f"
        for character in text
    )
    if disallowed_controls:
        raise DocumentParseError("The text document contains invalid characters.")
    return text


def _parse_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise DocumentParseError(
            "PDF parsing requires the 'pypdf' package, which is not installed."
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > _MAX_PDF_PAGES:
            raise DocumentParseError("The PDF has too many pages to process.")
        pages = [page.extract_text() or "" for page in reader.pages]
    except DocumentParseError:
        raise
    except Exception as exc:  # pypdf raises a variety of read errors
        raise DocumentParseError("Could not parse the PDF document.") from exc
    return "\n\n".join(pages)


def _parse_docx(data: bytes) -> str:
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise DocumentParseError(
            "DOCX parsing requires the 'python-docx' package, which is not "
            "installed."
        ) from exc

    try:
        document = docx.Document(io.BytesIO(data))
        paragraphs = [p.text for p in document.paragraphs]
    except Exception as exc:
        raise DocumentParseError("Could not parse the DOCX document.") from exc
    return "\n".join(paragraphs)


def chunk_text(
    text: str,
    *,
    chunk_size: int = _CHUNK_SIZE,
    overlap: int = _CHUNK_OVERLAP,
) -> list[ParsedChunk]:
    """Split ``text`` into bounded, slightly-overlapping chunks (Req 29.3).

    Paragraphs are packed greedily up to ``chunk_size`` characters; a trailing
    ``overlap`` slice of the previous chunk is prepended to the next so context
    is not lost at boundaries. Whitespace-only input yields no chunks.
    """

    normalized = text.strip()
    if not normalized:
        return []

    paragraphs = [p.strip() for p in normalized.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [normalized]

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= chunk_size or not current:
            current = candidate
        else:
            chunks.append(current)
            tail = current[-overlap:] if overlap > 0 else ""
            current = f"{tail}\n\n{paragraph}" if tail else paragraph
        # A single oversized paragraph is hard-split so no chunk is unbounded.
        while len(current) > chunk_size:
            chunks.append(current[:chunk_size])
            tail = current[chunk_size - overlap : chunk_size] if overlap > 0 else ""
            current = tail + current[chunk_size:]
    if current.strip():
        chunks.append(current)

    return [
        ParsedChunk(text=chunk_body, chunk_index=index)
        for index, chunk_body in enumerate(chunks)
    ]


__all__ = [
    "DocumentParseError",
    "ParsedChunk",
    "parse_document",
    "chunk_text",
]
