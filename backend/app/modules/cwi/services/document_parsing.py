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


def _looks_like_pdf(mime_type: str, filename: str) -> bool:
    return "pdf" in mime_type.lower() or filename.lower().endswith(".pdf")


def _looks_like_docx(mime_type: str, filename: str) -> bool:
    lowered = mime_type.lower()
    return (
        "officedocument.wordprocessingml" in lowered
        or "msword" in lowered
        or filename.lower().endswith(".docx")
    )


def parse_document(data: bytes, mime_type: str, filename: str) -> str:
    """Parse ``data`` into plain text based on its type (Requirement 29.1, 29.3).

    Raises :class:`DocumentParseError` when a format-specific library is needed
    but unavailable, or when the bytes cannot be decoded — the caller marks the
    document ``FAILED`` rather than persisting empty/garbage chunks.
    """

    if _looks_like_pdf(mime_type, filename):
        return _parse_pdf(data)
    if _looks_like_docx(mime_type, filename):
        return _parse_docx(data)
    # Default: treat as text (covers TXT and plain-text email attachments).
    return _parse_text(data)


def _parse_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    # Last resort: decode with replacement so we never crash on odd bytes.
    return data.decode("utf-8", errors="replace")


def _parse_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise DocumentParseError(
            "PDF parsing requires the 'pypdf' package, which is not installed."
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
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
