"""Validation for the document types KnowAct can process today.

The upload endpoint must not trust a browser supplied MIME type or filename on
its own.  This module cross-checks both with payload signatures before any
database row or storage object is created.  Image/audio types are deliberately
not accepted here: OCR and transcription are separate future capabilities and
must not be advertised as working until their processing paths exist.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePath


MAX_DOCUMENT_UPLOAD_BYTES = 20 * 1024 * 1024
_MAX_DOCX_EXPANDED_BYTES = 100 * 1024 * 1024
_MAX_DOCX_ENTRIES = 2048

_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

_TYPE_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".docx": _DOCX_MIME,
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".eml": "message/rfc822",
}

_TEXT_TYPES = {"text/plain", "text/markdown", "text/csv", "message/rfc822"}
_UNKNOWN_DECLARED_TYPES = {"", "application/octet-stream", "binary/octet-stream"}


class UploadValidationError(ValueError):
    """A safe upload rejection with the HTTP status the route should return."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    mime_type: str
    data: bytes


def _safe_filename(filename: str) -> str:
    normalized = (filename or "document").replace("\\", "/")
    basename = PurePath(normalized).name.strip()
    if not basename:
        raise UploadValidationError(400, "The uploaded file needs a filename.")
    if len(basename) > 512:
        raise UploadValidationError(400, "The uploaded filename is too long.")
    if any(ord(character) < 32 for character in basename):
        raise UploadValidationError(
            400, "The uploaded filename contains invalid characters."
        )
    return basename


def _declared_type(content_type: str) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _looks_like_known_non_text(data: bytes) -> bool:
    """Reject common binary families disguised as a supported text file."""

    return any(
        (
            data.startswith(b"%PDF-"),
            data.startswith(b"PK\x03\x04"),
            data.startswith(b"\x89PNG\r\n\x1a\n"),
            data.startswith(b"\xff\xd8\xff"),
            data.startswith((b"II*\x00", b"MM\x00*")),
            data.startswith(b"OggS"),
            data.startswith(b"ID3"),
            data.startswith(b"\x1aE\xdf\xa3"),
            len(data) >= 12
            and data.startswith(b"RIFF")
            and data[8:12] in {b"WAVE", b"WEBP"},
            len(data) >= 12 and data[4:8] == b"ftyp",
        )
    )


def _validate_docx(data: bytes) -> None:
    if not data.startswith(b"PK\x03\x04"):
        raise UploadValidationError(
            415, "The file extension says DOCX, but the payload is not a DOCX file."
        )
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise UploadValidationError(
                    415, "The uploaded ZIP payload is not a valid DOCX document."
                )
            if len(entries) > _MAX_DOCX_ENTRIES:
                raise UploadValidationError(413, "The DOCX contains too many entries.")
            expanded_size = sum(entry.file_size for entry in entries)
            if expanded_size > _MAX_DOCX_EXPANDED_BYTES:
                raise UploadValidationError(
                    413, "The expanded DOCX content is too large to process safely."
                )
            if any(entry.flag_bits & 0x1 for entry in entries):
                raise UploadValidationError(
                    415, "Password-protected DOCX files are not supported."
                )
    except UploadValidationError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise UploadValidationError(415, "The DOCX file is invalid or corrupted.") from exc


def _validate_text(data: bytes) -> None:
    try:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16", errors="strict")
        else:
            text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise UploadValidationError(
            415, "Text documents must use UTF-8 or BOM-marked UTF-16."
        ) from exc
    if "\ufffd" in text or "\x00" in text:
        raise UploadValidationError(
            415, "The text document contains invalid characters."
        )
    if any(
        ord(character) < 32 and character not in "\n\r\t\f"
        for character in text
    ):
        raise UploadValidationError(
            415, "The text document contains invalid characters."
        )


def validate_document_upload(
    *, filename: str, content_type: str, data: bytes
) -> ValidatedUpload:
    """Return canonical upload metadata or reject before storage/DB mutation."""

    if not data:
        raise UploadValidationError(400, "The uploaded file is empty.")
    if len(data) > MAX_DOCUMENT_UPLOAD_BYTES:
        raise UploadValidationError(
            413, "The uploaded document exceeds the 20 MB size limit."
        )

    safe_filename = _safe_filename(filename)
    extension = PurePath(safe_filename).suffix.lower()
    effective_type = _TYPE_BY_EXTENSION.get(extension)
    if effective_type is None:
        raise UploadValidationError(
            415,
            "Unsupported file type. Upload PDF, DOCX, TXT, Markdown, CSV, or EML.",
        )

    declared = _declared_type(content_type)
    allowed_declared = {effective_type, *_UNKNOWN_DECLARED_TYPES}
    if effective_type in _TEXT_TYPES:
        # Browsers commonly report Markdown, CSV, and EML as text/plain.
        allowed_declared.update(_TEXT_TYPES)
    elif effective_type == _DOCX_MIME:
        allowed_declared.add("application/zip")

    if declared not in allowed_declared:
        raise UploadValidationError(
            415,
            "The declared file type does not match the filename and payload.",
        )

    if effective_type == "application/pdf":
        if not data.startswith(b"%PDF-"):
            raise UploadValidationError(
                415,
                "The file extension says PDF, but the payload is not a PDF file.",
            )
    elif effective_type == _DOCX_MIME:
        _validate_docx(data)
    elif _looks_like_known_non_text(data):
        raise UploadValidationError(
            415, "The uploaded binary payload does not match the text file type."
        )
    else:
        _validate_text(data)

    return ValidatedUpload(
        filename=safe_filename,
        mime_type=effective_type,
        data=data,
    )


__all__ = [
    "MAX_DOCUMENT_UPLOAD_BYTES",
    "UploadValidationError",
    "ValidatedUpload",
    "validate_document_upload",
]
