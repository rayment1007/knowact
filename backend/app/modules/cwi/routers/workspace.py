"""Authenticated keyword search over captured originals and workspace records.

Search is deterministic and does not send private content to an AI provider.
Original sources do not need a classification or knowledge approval to appear.
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import String, cast, delete, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from app.core.models import ActionItem, AuditLog, ClassificationResult, KnowledgeItem, SourceItem, SourceType, User
from app.database import get_db
from app.dependencies import get_current_user, not_found
from app.modules.cwi.models import (
    CalendarSource, DocumentAsset, DocumentChunk, EmailDraft,
    EmailMessageRecord, IntegrationConnection, SourceProposal,
    SyncPreference, SyncExclusion,
)
from app.modules.cwi.services.sync_preferences import SyncPreferenceView, preferences

router = APIRouter(prefix="/workspace", tags=["workspace"])


@router.get("/sync-preferences", response_model=SyncPreferenceView)
def get_sync_preferences(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return preferences(db, user.organization_id, user.id)


@router.put("/sync-preferences", response_model=SyncPreferenceView)
def save_sync_preferences(payload: SyncPreferenceView, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.scalar(select(SyncPreference).where(SyncPreference.user_id == user.id, SyncPreference.organization_id == user.organization_id))
    if row is None:
        row = SyncPreference(user_id=user.id, organization_id=user.organization_id)
        db.add(row)
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    db.flush()
    return row


@router.get("/sync-exclusions")
def sync_exclusions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return {"count": db.scalar(select(func.count()).select_from(SyncExclusion).where(
        SyncExclusion.organization_id == user.organization_id, SyncExclusion.user_id == user.id)) or 0}


@router.delete("/sync-exclusions")
def restore_sync_exclusions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    db.execute(delete(SyncExclusion).where(SyncExclusion.organization_id == user.organization_id, SyncExclusion.user_id == user.id))
    return {"restored": True}

SearchKind = Literal["all", "email", "file", "note", "knowledge", "action", "draft", "calendar"]


class SearchResult(BaseModel):
    id: str
    kind: str
    title: str
    excerpt: str
    status: str
    updated_at: datetime
    path: str


class SearchPage(BaseModel):
    items: list[SearchResult]
    total: int
    has_more: bool


class CalendarSourceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    description: str
    location: str
    starts_at: str
    ends_at: str
    html_link: str
    updated_at: datetime


def _calendar_query(user: User):
    return select(CalendarSource).join(IntegrationConnection).where(
        CalendarSource.organization_id == user.organization_id,
        IntegrationConnection.organization_id == user.organization_id,
        IntegrationConnection.user_id == user.id,
    )


@router.get("/calendar", response_model=list[CalendarSourceView])
def calendar_sources(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.scalars(_calendar_query(user).order_by(CalendarSource.starts_at)).all()


@router.get("/calendar/{source_id}", response_model=CalendarSourceView)
def calendar_source(source_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.scalar(_calendar_query(user).where(CalendarSource.id == source_id))
    if row is None:
        raise not_found("Calendar source not found.")
    return row


@router.get("/summary")
def summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.modules.cwi.routers.workspace_browser import counts
    review_counts = counts(db, user)
    def count(model, *conditions):
        return db.scalar(select(func.count()).select_from(model).where(
            model.organization_id == user.organization_id, *conditions
        )) or 0
    return {
        "documents": count(DocumentAsset, DocumentAsset.uploaded_by == user.id, DocumentAsset.source_deleted.is_(False)),
        "documents_pending": count(DocumentAsset, DocumentAsset.uploaded_by == user.id, DocumentAsset.source_deleted.is_(False), DocumentAsset.processing_status.in_(["UPLOADED", "PARSING", "CHUNKING", "EMBEDDING"])),
        "documents_failed": count(DocumentAsset, DocumentAsset.uploaded_by == user.id, DocumentAsset.source_deleted.is_(False), DocumentAsset.processing_status == "FAILED"),
        "notes": count(SourceItem, SourceItem.created_by == user.id, SourceItem.source_type.notin_([SourceType.EMAIL, SourceType.DOCUMENT])),
        "open_actions": count(ActionItem, ActionItem.status.in_(["OPEN", "IN_PROGRESS"])),
        "knowledge": count(KnowledgeItem, KnowledgeItem.status == "CONFIRMED"),
        "knowledge_reviews": review_counts["knowledge_reviews"],
        "action_reviews": review_counts["actions_reviews"],
        "draft_reviews": count(EmailDraft, EmailDraft.user_id == user.id, EmailDraft.status == "AI_SUGGESTED"),
        "source_reviews": review_counts["sources_reviews"],
    }


# Only expose known workspace operations, never raw audit detail (which can
# contain privacy-sensitive payloads). Deleted targets intentionally have no link.
_ACTIVITY_LABELS = {
    "DELETE_CALENDAR_SOURCE": "Calendar source removed",
    "CREATE_SOURCE_ITEM": "Source added", "DELETE_SOURCE_ITEM": "Source deleted",
    "CONFIRM_CLASSIFICATION": "Source classification confirmed",
    "CONFIRM_KNOWLEDGE": "Knowledge confirmed", "DELETE_KNOWLEDGE": "Knowledge deleted",
    "CREATE_ACTION": "Action created", "UPDATE_ACTION": "Action updated", "DELETE_ACTION": "Action deleted",
    "UPLOAD_DOCUMENT": "Document uploaded", "PROCESS_DOCUMENT": "Document processed", "DELETE_DOCUMENT": "Document deleted",
}
_ACTIVITY_TARGETS = {
    "CalendarSource": (CalendarSource, "title", "/calendar-sources/"),
    "SourceItem": (SourceItem, "title", "/source-inbox/"),
    "KnowledgeItem": (KnowledgeItem, "summary", "/knowledge/"),
    "ActionItem": (ActionItem, "title", "/actions/"),
    "DocumentAsset": (DocumentAsset, "filename", "/documents/"),
}


@router.get("/activity")
def activity(
    limit: int = Query(default=5, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=100000),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    query = select(AuditLog).where(
        AuditLog.organization_id == user.organization_id,
        AuditLog.actor_id == user.id,
        AuditLog.action_type.in_(_ACTIVITY_LABELS),
        AuditLog.target_type.in_(_ACTIVITY_TARGETS),
    ).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).offset(offset).limit(limit + 1)
    rows = list(db.scalars(query))
    items = []
    for row in rows[:limit]:
        model, title_field, path_root = _ACTIVITY_TARGETS[row.target_type]
        target_query = select(model).where(model.id == row.target_id, model.organization_id == user.organization_id)
        if model is SourceItem:
            target_query = target_query.where(SourceItem.created_by == user.id)
        elif model is DocumentAsset:
            target_query = target_query.where(DocumentAsset.uploaded_by == user.id, DocumentAsset.source_deleted.is_(False))
        elif model is CalendarSource:
            target_query = target_query.join(IntegrationConnection).where(IntegrationConnection.user_id == user.id)
        target = db.scalar(target_query)
        items.append({
            "id": row.id, "label": _ACTIVITY_LABELS[row.action_type],
            "title": getattr(target, title_field) if target else ("Item deleted. Activity record only." if row.action_type.startswith("DELETE_") else "Item no longer available"),
            "path": path_root + str(target.id) if target else None,
            "created_at": row.created_at,
        })
    return {"items": items, "has_more": len(rows) > limit}


@router.get("/search", response_model=SearchPage)
def search(
    q: str = Query(default="", max_length=200),
    kind: SearchKind = "all",
    offset: int = Query(default=0, ge=0, le=100000),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    term = q.strip()
    # LIKE metacharacters are literal user text, not wildcard instructions.
    pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

    def matches(*columns):
        return or_(*(cast(column, String).ilike(pattern, escape="\\") for column in columns))

    def fields(model, category, title, content, state, date, path):
        return select(
            cast(model.id, String).label("id"), literal(category).label("kind"),
            title.label("title"), func.coalesce(content, "").label("content"),
            cast(state, String).label("status"), date.label("updated_at"),
            literal(path).label("path_root"),
        ).where(model.organization_id == user.organization_id)

    queries = {}
    email_body = func.coalesce(SourceItem.content, "")
    queries["email"] = fields(EmailMessageRecord, "email", EmailMessageRecord.subject, email_body,
        literal("Original email"), EmailMessageRecord.received_at, "/emails/").join(
        IntegrationConnection, EmailMessageRecord.integration_connection_id == IntegrationConnection.id
    ).outerjoin(SourceItem, (SourceItem.id == EmailMessageRecord.source_item_id) &
        (SourceItem.organization_id == user.organization_id) & (SourceItem.created_by == user.id)).where(
        IntegrationConnection.user_id == user.id, IntegrationConnection.organization_id == user.organization_id,
        matches(EmailMessageRecord.subject, EmailMessageRecord.sender, EmailMessageRecord.recipients_json, email_body),
    )
    chunk_text = select(DocumentChunk.text).where(
        DocumentChunk.document_asset_id == DocumentAsset.id,
        DocumentChunk.organization_id == user.organization_id, matches(DocumentChunk.text),
    ).order_by(DocumentChunk.chunk_index).limit(1).correlate(DocumentAsset).scalar_subquery()
    queries["file"] = fields(DocumentAsset, "file", DocumentAsset.filename, chunk_text,
        DocumentAsset.processing_status, DocumentAsset.created_at, "/documents/").where(
        DocumentAsset.uploaded_by == user.id, DocumentAsset.source_deleted.is_(False),
        or_(matches(DocumentAsset.filename), chunk_text.is_not(None)),
    )
    queries["note"] = fields(SourceItem, "note", SourceItem.title, SourceItem.content,
        SourceItem.status, SourceItem.created_at, "/source-inbox/").where(
        SourceItem.created_by == user.id,
        ~select(EmailMessageRecord.id).where(EmailMessageRecord.source_item_id == SourceItem.id).exists(),
        matches(SourceItem.title, SourceItem.content),
    )
    queries["knowledge"] = fields(KnowledgeItem, "knowledge", KnowledgeItem.summary, KnowledgeItem.evidence_text,
        KnowledgeItem.status, KnowledgeItem.updated_at, "/knowledge/").where(
        matches(KnowledgeItem.summary, KnowledgeItem.evidence_text, KnowledgeItem.key_points))
    queries["action"] = fields(ActionItem, "action", ActionItem.title, ActionItem.description,
        ActionItem.status, ActionItem.created_at, "/actions/").where(
        matches(ActionItem.title, ActionItem.description, ActionItem.evidence_text))
    queries["draft"] = fields(EmailDraft, "draft", EmailDraft.subject, EmailDraft.body_text,
        EmailDraft.status, EmailDraft.created_at, "/email-drafts/").where(
        EmailDraft.user_id == user.id, matches(EmailDraft.subject, EmailDraft.body_text, EmailDraft.to_recipients_json))
    queries["calendar"] = fields(CalendarSource, "calendar", CalendarSource.title, CalendarSource.description,
        literal("Original event"), CalendarSource.updated_at, "/calendar-sources/").join(IntegrationConnection).where(
        IntegrationConnection.user_id == user.id, IntegrationConnection.organization_id == user.organization_id,
        matches(CalendarSource.title, CalendarSource.description, CalendarSource.location, CalendarSource.starts_at))

    combined = union_all(*(queries.values() if kind == "all" else [queries[kind]])).subquery()
    total = db.scalar(select(func.count()).select_from(combined)) or 0
    rows = db.execute(select(combined).order_by(combined.c.updated_at.desc(), combined.c.kind, combined.c.id).offset(offset).limit(limit)).mappings()
    items = []
    for row in rows:
        content = row["content"]
        position = content.lower().find(term.lower()) if term else 0
        start = max(0, position - 70)
        excerpt = ("…" if start else "") + content[start:start + 280]
        if start + 280 < len(content):
            excerpt += "…"
        # PostgreSQL UUID casts use hyphens; SQLite's UUID storage does not.
        record_id = str(UUID(row["id"]))
        items.append(SearchResult(id=record_id, kind=row["kind"], title=row["title"] or "Untitled",
            excerpt=excerpt, status=row["status"], updated_at=row["updated_at"], path=row["path_root"] + record_id))
    return SearchPage(items=items, total=total, has_more=offset + len(items) < total)
