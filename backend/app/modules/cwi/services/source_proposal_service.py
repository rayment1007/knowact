"""User-directed drafting. Only explicit approval creates a knowledge/task record."""
import hashlib
import json
from datetime import date
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.core.models import ActionItem, AuditLog, ClassificationResult, KnowledgeItem
from app.core.services.ai_provider import AIProviderError
from app.dependencies import get_ai_provider, not_found
from app.modules.cwi.models import DocumentChunk, SourceProposal
from app.modules.cwi.services.workspace_sources import _source

MAX_TEXT = 24000


class DraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    summary: str = Field(default="", max_length=12000)
    key_points: list[str] = Field(default_factory=list, max_length=20)
    title: str = Field(default="", max_length=512)
    description: str = Field(default="", max_length=12000)
    due_date: date | None = None


def clean_payload(target, payload):
    if target == "knowledge":
        if not payload.summary or any(len(point) > 2000 for point in payload.key_points):
            raise HTTPException(422, "Add a summary and keep each key point under 2,000 characters.")
        return {"summary": payload.summary, "key_points": [p.strip() for p in payload.key_points if p.strip()]}
    if not payload.title:
        raise HTTPException(422, "Add an action title.")
    return {"title": payload.title, "description": payload.description,
            "due_date": payload.due_date.isoformat() if payload.due_date else None}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def snapshot(db, user, kind, source_id, acknowledged=False, lock=False):
    row, source = _source(db, user, kind, source_id, lock)
    title = row.subject if kind == "email" else row.filename if kind == "file" else row.title
    sensitivity = getattr(row, "sensitivity", None)
    classification = None
    if source:
        classification = db.scalar(select(ClassificationResult).where(
            ClassificationResult.source_item_id == source.id, ClassificationResult.status != "REJECTED"))
        if classification:
            sensitivity = classification.sensitivity
        # A previous explicit exclusion is a user decision, not an AI eligibility gate.
        if source.status == "DISMISSED" or (classification and classification.status == "CONFIRMED"
                and classification.relevance != "WORK_RELATED"):
            raise HTTPException(409, "This source was excluded from work content. Update its classification before using it here.")
    if sensitivity == "HIGHLY_SENSITIVE" and not acknowledged:
        raise HTTPException(409, {"code": "SENSITIVE_ACK_REQUIRED",
            "message": "This source is marked highly sensitive. Confirm before sending its text to the configured AI provider."})
    if kind == "file":
        if row.processing_status != "INDEXED":
            raise HTTPException(409, "Process this file successfully before creating an AI draft.")
        chunks = select(DocumentChunk.text).where(DocumentChunk.document_asset_id == row.id,
            DocumentChunk.organization_id == user.organization_id)
        length = db.scalar(select(func.sum(func.length(DocumentChunk.text))).where(
            DocumentChunk.document_asset_id == row.id, DocumentChunk.organization_id == user.organization_id)) or 0
        parts, size = [], 0
        # Bounded input; never load a whole large document into memory for a draft.
        for chunk in db.scalars(chunks.order_by(DocumentChunk.chunk_index).execution_options(yield_per=20)):
            parts.append(chunk[:MAX_TEXT + 1 - size]); size += len(parts[-1]) + 2
            if size > MAX_TEXT:
                break
        text = "\n\n".join(parts)
        fingerprint_data = [row.checksum, row.processing_status, length, text]
        truncated = length > MAX_TEXT or len(text) > MAX_TEXT
    elif kind == "calendar":
        text = f"Event: {title}\nDescription: {row.description}\nLocation: {row.location}\nStarts: {row.starts_at}\nEnds: {row.ends_at}"
        fingerprint_data = text
        truncated = len(text) > MAX_TEXT
    else:
        text = source.content if source else ""
        fingerprint_data = text
        truncated = len(text) > MAX_TEXT
    if not text.strip():
        raise HTTPException(409, "This source has no readable text to analyse.")
    fingerprint = digest([title, fingerprint_data, sensitivity, source.status if source else None,
        classification.relevance if classification else None, classification.status if classification else None])
    return title, text[:MAX_TEXT], fingerprint, truncated, source


def proposal(db, user, proposal_id, lock=False):
    query = select(SourceProposal).where(SourceProposal.id == proposal_id,
        SourceProposal.organization_id == user.organization_id, SourceProposal.user_id == user.id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if not row:
        raise not_found("Draft not found.")
    return row


def audit(db, user, row, action, target_type="SourceProposal", target_id=None):
    db.add(AuditLog(organization_id=user.organization_id, actor_id=user.id, action_type=action,
        target_type=target_type, target_id=target_id or row.id,
        detail={"proposal_id": str(row.id), "source_kind": row.source_kind, "source_id": str(row.source_id), "version": row.version}))


def generate(db, user, kind, source_id, target: Literal["knowledge", "action"], acknowledged):
    title, text, fingerprint, truncated, _ = snapshot(db, user, kind, source_id, acknowledged)
    key = digest([user.id, kind, source_id, target, fingerprint])
    existing = db.scalar(select(SourceProposal).where(SourceProposal.active_key == key))
    if existing:
        return existing
    settings = get_settings()
    if settings.ai_provider == "llm" and not settings.llm_api_key:
        raise HTTPException(503, "AI drafting is not configured. Please try again after configuration is restored.")
    row = SourceProposal(organization_id=user.organization_id, user_id=user.id, source_kind=kind,
        source_id=source_id, source_title=title, source_fingerprint=fingerprint, target=target,
        active_key=key, provider="openai" if settings.ai_provider == "llm" else "mock",
        sensitivity_acknowledged=acknowledged, analysis_truncated=truncated)
    # Reserve the unique pending draft before the paid call. Concurrent requests
    # wait on the unique constraint and reuse the committed result.
    try:
        with db.begin_nested():
            db.add(row); db.flush()
    except IntegrityError:
        existing = db.scalar(select(SourceProposal).where(SourceProposal.active_key == key))
        if existing:
            return existing
        raise
    try:
        output = get_ai_provider(settings).prepare_source_draft(text, title, target)
        quote = " ".join(output.evidence_text.split())
        if output.insufficient_evidence or not quote or quote not in " ".join(text.split()):
            raise HTTPException(422, "The AI could not produce a draft supported by this source. Nothing was created; try again or add more source detail.")
        row.payload = clean_payload(target, DraftPayload.model_validate(output.model_dump(exclude={"insufficient_evidence", "evidence_text"})))
        row.evidence_text = output.evidence_text
    except (AIProviderError, ValidationError):
        db.delete(row); db.flush()
        raise HTTPException(503, "AI drafting failed. No knowledge or action was created. Please try again.") from None
    except HTTPException:
        db.delete(row); db.flush()
        raise
    audit(db, user, row, "GENERATE_SOURCE_PROPOSAL")
    db.flush()
    return row


def update(db, user, row, version, payload, approve=False):
    if approve and row.status == "APPROVED":
        return row  # A retry never creates a duplicate.
    if row.status != "SUGGESTED" or row.version != version:
        raise HTTPException(409, "This draft has changed. Reopen it to review the latest version.")
    values = clean_payload(row.target, payload)
    if approve:
        _, _, fingerprint, _, source = snapshot(db, user, row.source_kind, row.source_id, row.sensitivity_acknowledged, lock=True)
        if fingerprint != row.source_fingerprint:
            raise HTTPException(409, "The original source changed. Discard this draft and generate a new one before approving.")
        if row.target == "knowledge":
            result = KnowledgeItem(organization_id=user.organization_id, source_item_id=source.id if source else None,
                summary=values["summary"], key_points=values["key_points"], evidence_text=row.evidence_text,
                knowledge_type="CONTEXT", status="CONFIRMED")
        else:
            result = ActionItem(organization_id=user.organization_id, owner_id=user.id,
                title=values["title"], description=values["description"],
                due_date=date.fromisoformat(values["due_date"]) if values["due_date"] else None,
                status="OPEN", evidence_text=row.evidence_text, ai_generated=True)
        db.add(result); db.flush()
        row.result_id = result.id; row.status = "APPROVED"; row.active_key = None
    row.payload = values; row.version += 1
    audit(db, user, row, ("CONFIRM_KNOWLEDGE" if row.target == "knowledge" else "CREATE_ACTION") if approve else "EDIT_SOURCE_PROPOSAL",
        ("KnowledgeItem" if row.target == "knowledge" else "ActionItem") if approve else "SourceProposal", row.result_id if approve else None)
    db.flush()
    return row


def discard_unapproved(db, org_id, kind, ids):
    if ids:
        db.execute(delete(SourceProposal).where(SourceProposal.organization_id == org_id,
            SourceProposal.source_kind == kind, SourceProposal.source_id.in_(ids), SourceProposal.status != "APPROVED"))


def origin(db, user, kind, source_id, title="Original source"):
    try:
        row, source = _source(db, user, kind, source_id)
        available = kind != "email" or source is not None
        title = row.subject if kind == "email" else row.filename if kind == "file" else row.title
    except HTTPException as error:
        if error.status_code != 404:
            raise
        available = False
    return {"kind": kind, "id": str(source_id), "title": title, "available": available,
            "path": f"/workspace/sources?item={kind}:{source_id}" if available else None}


def result_origins(db, user, target, result_id, source_item_id=None):
    rows = db.scalars(select(SourceProposal).where(SourceProposal.organization_id == user.organization_id,
        SourceProposal.user_id == user.id, SourceProposal.target == target,
        SourceProposal.result_id == result_id, SourceProposal.status == "APPROVED"))
    origins = [origin(db, user, row.source_kind, row.source_id, row.source_title) for row in rows]
    if not origins and source_item_id:
        origins = [origin(db, user, "source", source_item_id)]
    return origins
