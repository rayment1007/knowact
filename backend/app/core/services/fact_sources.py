"""Read-only, owner-scoped evidence snapshots for structured-fact services.

No AI call, database write, or full-text persistence happens here. The caller
stores only the exact reviewed excerpt, its locator, and the returned hashes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import ClassificationResult, SourceItem, User
from app.modules.cwi.models import (
    DocumentChunk, EmailMessageRecord, IntegrationConnection,
)
from app.modules.cwi.services.workspace_sources import _source


@dataclass(frozen=True)
class FactSourceRead:
    kind: str
    source_id: UUID
    origin_key: str
    fingerprint: str
    title: str
    text: str
    source_time: datetime | None
    locator: dict
    source: SourceItem | None


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _time(value: datetime | None) -> datetime | None:
    # SQLite loses offsets on DateTime(timezone=True); persisted timestamp
    # columns are UTC, so restoring UTC here preserves cross-dialect hashes.
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def read_fact_source(
    db: Session, user: User, kind: str, source_id: UUID, lock: bool = True,
    acknowledge_sensitive: bool = False,
) -> FactSourceRead:
    """Read complete extracted text and a stable content revision.

    ``source`` is the existing manual/pasted-source route; a raw source linked
    to an owned Gmail record canonicalizes to ``email`` before row locking.
    Provider/local sync timestamps and mutable classification are deliberately
    excluded from content fingerprints. Eligibility is checked on every read.
    """
    if kind == "note":
        kind = "source"
    if kind not in {"source", "email", "file", "calendar"}:
        raise HTTPException(422, "Unsupported source kind.")
    if kind == "source":
        linked_email_id = db.scalar(select(EmailMessageRecord.id).join(
            IntegrationConnection,
            IntegrationConnection.id == EmailMessageRecord.integration_connection_id,
        ).where(
            EmailMessageRecord.source_item_id == source_id,
            EmailMessageRecord.organization_id == user.organization_id,
            IntegrationConnection.organization_id == user.organization_id,
            IntegrationConnection.user_id == user.id,
        ).order_by(EmailMessageRecord.id))
        if linked_email_id is not None:
            kind, source_id = "email", linked_email_id

    row, source = _source(db, user, kind, source_id, lock=lock)
    classification = None
    sensitivity = getattr(row, "sensitivity", None)
    if source is not None:
        classification = db.scalar(select(ClassificationResult).where(
            ClassificationResult.source_item_id == source.id,
            ClassificationResult.status != "REJECTED",
        ).order_by(ClassificationResult.created_at.desc(), ClassificationResult.id))
        if classification is not None:
            sensitivity = classification.sensitivity
        if source.status == "DISMISSED" or (
            classification is not None and classification.status == "CONFIRMED"
            and classification.relevance != "WORK_RELATED"
        ):
            raise HTTPException(409, "This source was excluded from work content. Update its classification before using it here.")
    if sensitivity == "HIGHLY_SENSITIVE" and not acknowledge_sensitive:
        raise HTTPException(409, {"code": "SENSITIVE_ACK_REQUIRED",
            "message": "This source is marked highly sensitive. Confirm before using it for fact extraction."})

    scope = [str(user.organization_id), str(user.id)]
    locator: dict = {"kind": kind, "source_id": str(row.id), "text_format": "extracted_text_v1"}
    source_time = None
    metadata: dict = {}
    if kind in {"email", "calendar"}:
        connection = db.scalar(select(IntegrationConnection).where(
            IntegrationConnection.id == row.integration_connection_id,
            IntegrationConnection.organization_id == user.organization_id,
            IntegrationConnection.user_id == user.id,
        ).execution_options(populate_existing=True))
        if connection is None:
            raise HTTPException(404, "Source not found.")
        # The provider ID survives local deletion/reimport and reconnection.
        account = connection.account_email.strip().casefold()
        if not account:
            raise HTTPException(409, "This source has no stable provider account identity.")
        locator["integration_connection_id"] = str(connection.id)
        if kind == "email":
            title = row.subject
            text = source.content if source is not None else ""
            source_time = _time(row.received_at)
            origin = [*scope, "google_gmail", account, row.gmail_message_id]
            metadata = {"sender": row.sender, "recipients": sorted(row.recipients_json or []),
                "received_at": source_time.isoformat() if source_time else None}
            locator.update({"gmail_message_id": row.gmail_message_id,
                "source_item_id": str(source.id) if source is not None else None})
            # stored_raw=False denotes EXTRACTED_ONLY, not absent text. Read
            # the existing extracted SourceItem, never raw MIME/attachments.
        else:
            title = row.title
            text = f"Event: {title}\nDescription: {row.description}\nLocation: {row.location}\nStarts: {row.starts_at}\nEnds: {row.ends_at}"
            calendar_id = row.google_calendar_id or "primary"
            if calendar_id.casefold() == account:
                calendar_id = "primary"
            origin = [*scope, "google_calendar", account, calendar_id, row.google_event_id]
            metadata = {"starts_at": row.starts_at, "ends_at": row.ends_at}
            locator.update({"google_calendar_id": calendar_id, "google_event_id": row.google_event_id})
            # Event start is the asserted value, not when its source was authored.
            # updated_at is a local ORM timestamp, not Google's revision time.
    elif kind == "file":
        if row.processing_status != "INDEXED":
            raise HTTPException(409, "Process this file successfully before extracting facts.")
        title = row.filename
        chunks = list(db.scalars(select(DocumentChunk).where(
            DocumentChunk.document_asset_id == row.id,
            DocumentChunk.organization_id == user.organization_id,
        ).order_by(DocumentChunk.chunk_index, DocumentChunk.id)))
        text = "\n\n".join(chunk.text for chunk in chunks)
        offset = 0
        ranges = []
        for chunk in chunks:
            ranges.append({"chunk_id": str(chunk.id), "chunk_index": chunk.chunk_index,
                "page_number": chunk.page_number, "start": offset, "end": offset + len(chunk.text)})
            offset += len(chunk.text) + 2
        locator["chunks"] = ranges
        origin = [*scope, "document_asset", str(row.id)]
        metadata = {"checksum": row.checksum, "mime_type": row.mime_type,
            "chunks": [{"chunk_index": chunk.chunk_index, "page_number": chunk.page_number,
                "text": chunk.text} for chunk in chunks]}
    else:
        title, text = source.title, source.content
        # Manual/pasted sources use a server-default received_at. It does not
        # establish when the underlying statement was authored or revised.
        # Keep it in the revision metadata, never use it to order replacements.
        source_time = None
        origin = [*scope, "source_item", str(source.id)]
        metadata = {"source_type": source.source_type,
            "received_at": _time(source.received_at).isoformat() if source.received_at else None}

    if not text.strip():
        raise HTTPException(409, "This source has no readable text to analyse.")
    fingerprint = _hash({"format": "knowact_fact_source_v1", "kind": kind,
        "title": title, "text": text, "metadata": metadata})
    return FactSourceRead(kind=kind, source_id=row.id, origin_key=_hash(origin),
        fingerprint=fingerprint, title=title, text=text, source_time=source_time,
        locator=locator, source=source)
