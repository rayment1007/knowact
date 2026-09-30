"""Paged, read-only workspace views. Opening a record never invokes an AI/provider."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, and_, case, cast, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from app.core.models import ActionItem, AuditLog, ClassificationResult, KnowledgeItem, SourceItem, User
from app.core.schemas import SourceItemResponse
from app.database import get_db
from app.dependencies import get_current_user, not_found
from app.modules.cwi.models import CalendarEventLink, CalendarSource, DocumentAsset, DocumentChunk, EmailMessageRecord, EmailTaskSuggestion, IntegrationConnection, SourceProposal
from app.modules.cwi.schemas import DocumentAssetView, EmailTaskSuggestionView, CalendarEventLinkView

router = APIRouter(prefix="/workspace", tags=["workspace"])
Section = Literal["sources", "knowledge", "actions"]
SourceKind = Literal["source", "email", "file", "calendar"]


from app.modules.cwi.services.workspace_sources import _owned_email, _source
from app.modules.cwi.services.source_proposal_service import origin, result_origins


def _classification_state():
    pending = select(ClassificationResult.id).where(ClassificationResult.source_item_id == SourceItem.id,
        ClassificationResult.status == "SUGGESTED").exists()
    confirmed = select(ClassificationResult.id).where(ClassificationResult.source_item_id == SourceItem.id,
        ClassificationResult.status == "CONFIRMED").exists()
    return case((SourceItem.status == "DISMISSED", "DISMISSED"), (SourceItem.status == "ARCHIVED", "ARCHIVED"),
        (pending, "NEEDS_REVIEW"), (confirmed, "REVIEWED"), else_="RAW")


def _rows(model, kind, source_type, title, content, state, date, user):
    return select(cast(model.id, String).label("id"), literal(kind).label("kind"),
        source_type.label("source_type"), title.label("title"),
        func.substr(func.coalesce(content, ""), 1, 240).label("excerpt"),
        cast(state, String).label("status"), date.label("updated_at")).where(model.organization_id == user.organization_id)


def _proposal_rows(user):
    title = case((SourceProposal.target == "knowledge", SourceProposal.payload["summary"].as_string()),
                 else_=SourceProposal.payload["title"].as_string())
    return _rows(SourceProposal, "proposal", SourceProposal.target, title, SourceProposal.evidence_text,
        SourceProposal.status, SourceProposal.updated_at, user).where(
        SourceProposal.user_id == user.id, SourceProposal.status == "SUGGESTED")


def _feed(section, user, q="", entity_id=None):
    pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    def matches(*fields):
        if not q.strip():
            return literal(True)
        return or_(*(cast(field, String).ilike(pattern, escape="\\") for field in fields))
    if section == "sources":
        source_type = case((SourceItem.source_type == "EMAIL", "email"),
            (SourceItem.source_type == "DOCUMENT", "file"), else_="note")
        notes = _rows(SourceItem, "source", source_type, SourceItem.title, SourceItem.content,
            _classification_state(), SourceItem.created_at, user).where(SourceItem.created_by == user.id,
            ~select(EmailMessageRecord.id).where(EmailMessageRecord.source_item_id == SourceItem.id).exists(),
            matches(SourceItem.title, SourceItem.content))
        emails = _rows(EmailMessageRecord, "email", literal("email"), EmailMessageRecord.subject,
            SourceItem.content, _classification_state(), EmailMessageRecord.received_at, user).outerjoin(SourceItem,
            and_(SourceItem.id == EmailMessageRecord.source_item_id, SourceItem.created_by == user.id,
                 SourceItem.organization_id == user.organization_id)).where(EmailMessageRecord.id.in_(_owned_email(user)),
            matches(EmailMessageRecord.subject, EmailMessageRecord.sender, SourceItem.content))
        chunk = select(DocumentChunk.text).where(DocumentChunk.document_asset_id == DocumentAsset.id,
            DocumentChunk.organization_id == user.organization_id, matches(DocumentChunk.text)
        ).order_by(DocumentChunk.chunk_index).limit(1).correlate(DocumentAsset).scalar_subquery()
        file_state = case((DocumentAsset.processing_status == "FAILED", "FAILED"),
            (DocumentAsset.processing_status.in_(["PARSING", "CHUNKING", "EMBEDDING"]), "PROCESSING"),
            (DocumentAsset.processing_status == "UPLOADED", "UPLOADED"), else_="INDEXED")
        files = _rows(DocumentAsset, "file", literal("file"), DocumentAsset.filename, chunk,
            file_state, DocumentAsset.created_at, user).where(DocumentAsset.uploaded_by == user.id,
            DocumentAsset.source_deleted.is_(False), or_(matches(DocumentAsset.filename), chunk.is_not(None)))
        calendar = _rows(CalendarSource, "calendar", literal("calendar"), CalendarSource.title, CalendarSource.description,
            literal("RAW"), CalendarSource.updated_at, user).join(IntegrationConnection).where(
            IntegrationConnection.user_id == user.id, IntegrationConnection.organization_id == user.organization_id,
            matches(CalendarSource.title, CalendarSource.description, CalendarSource.location))
        return union_all(notes, emails, files, calendar).subquery()
    proposals = _proposal_rows(user).where(SourceProposal.target == ("knowledge" if section == "knowledge" else "action"),
        matches(SourceProposal.source_title, SourceProposal.payload), literal(not entity_id))
    if section == "knowledge":
        knowledge = _rows(KnowledgeItem, "knowledge", literal("knowledge"), KnowledgeItem.summary, KnowledgeItem.evidence_text,
            KnowledgeItem.status, KnowledgeItem.updated_at, user).where(
            matches(KnowledgeItem.summary, KnowledgeItem.key_points, KnowledgeItem.evidence_text),
            KnowledgeItem.business_entity_id == entity_id if entity_id else literal(True))
        return union_all(knowledge, proposals).subquery()
    actions = _rows(ActionItem, "action", literal("action"), ActionItem.title, ActionItem.description,
        ActionItem.status, ActionItem.created_at, user).where(matches(ActionItem.title, ActionItem.description, ActionItem.evidence_text),
            ActionItem.business_entity_id == entity_id if entity_id else literal(True))
    # A proposal is not an accepted action. Once confirmed it appears only as the
    # resulting ActionItem, avoiding two copies in the user's action list.
    suggestions = _rows(EmailTaskSuggestion, "suggestion", literal("action"), EmailTaskSuggestion.title,
        EmailTaskSuggestion.description, EmailTaskSuggestion.status, EmailTaskSuggestion.created_at, user).where(
        EmailTaskSuggestion.email_message_record_id.in_(_owned_email(user)), EmailTaskSuggestion.status == "SUGGESTED",
        matches(EmailTaskSuggestion.title, EmailTaskSuggestion.description, EmailTaskSuggestion.evidence_text),
        EmailTaskSuggestion.business_entity_id == entity_id if entity_id else literal(True))
    return union_all(actions, suggestions, proposals).subquery()


def _page(db, query, limit, offset):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    items = [dict(row) for row in db.execute(query.limit(limit).offset(offset)).mappings()]
    for row in items:
        row["id"] = str(UUID(str(row["id"])))
    return {"items": items, "total": total, "has_more": offset + len(items) < total}


@router.get("/items")
def items(section: Section = "sources", q: str = Query("", max_length=200),
          source_type: Literal["all", "email", "calendar", "file", "note"] = "all",
          status: str = Query("", max_length=30), order: Literal["newest", "oldest"] = "newest",
          entity_id: UUID | None = None,
          limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=100000),
          db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = _feed(section, user, q, entity_id)
    query = select(rows)
    if source_type != "all" and section == "sources":
        query = query.where(rows.c.source_type == source_type)
    # Status counts reflect the selected source type and search, before status.
    facet_rows = query.subquery()
    status_counts = dict(db.execute(select(facet_rows.c.status, func.count()).group_by(facet_rows.c.status)).all())
    type_counts = dict(db.execute(select(rows.c.source_type, func.count()).group_by(rows.c.source_type)).all())
    if status:
        query = query.where(rows.c.status == status)
    query = query.order_by(rows.c.updated_at.desc() if order == "newest" else rows.c.updated_at.asc(), rows.c.kind, rows.c.id)
    return {**_page(db, query, limit, offset), "status_counts": status_counts, "type_counts": type_counts}


@router.get("/counts")
def counts(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    result = {}
    for section in ("sources", "knowledge", "actions"):
        rows = _feed(section, user)
        # Counts never materialize source bodies or all list records in Python.
        result[section] = db.scalar(select(func.count()).select_from(rows)) or 0
        result[section + "_reviews"] = db.scalar(select(func.count()).select_from(rows).where(
            rows.c.status == ("NEEDS_REVIEW" if section == "sources" else "SUGGESTED"))) or 0
    return result




@router.get("/source/{kind}/{item_id}")
def source_detail(kind: SourceKind, item_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row, source = _source(db, user, kind, item_id)
    metadata = {}
    if kind == "email":
        metadata = {"sender": row.sender, "recipients": row.recipients_json, "received_at": row.received_at,
                    "gmail_message_id": row.gmail_message_id}
    elif kind == "calendar":
        metadata = {"starts_at": row.starts_at, "ends_at": row.ends_at, "location": row.location, "html_link": row.html_link}
    return {"id": row.id, "kind": kind,
        "title": row.subject if kind == "email" else row.filename if kind == "file" else row.title,
        "source": SourceItemResponse.model_validate(source) if source else None,
        "document": DocumentAssetView.model_validate(row) if kind == "file" else None,
        "raw_content": source.content if source else row.description if kind == "calendar" else None,
        "metadata": metadata}


def _confirmed_email_action(user):
    # The existing confirmation transaction records the exact resulting action ID
    # in its audit record. Never infer relationships from matching title/evidence.
    return select(AuditLog.target_id).join(EmailTaskSuggestion, EmailTaskSuggestion.id == AuditLog.target_id).where(
        AuditLog.organization_id == user.organization_id, AuditLog.actor_id == user.id,
        AuditLog.action_type == "CONFIRM_EMAIL_TASK", AuditLog.target_type == "EmailTaskSuggestion",
        EmailTaskSuggestion.email_message_record_id.in_(_owned_email(user)),
        func.replace(AuditLog.detail["action_item_id"].as_string(), "-", "") == func.replace(cast(ActionItem.id, String), "-", ""))


@router.get("/source/{kind}/{item_id}/links")
def source_links(kind: SourceKind, item_id: UUID, limit: int = Query(10, ge=1, le=50), offset: int = Query(0, ge=0, le=100000),
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row, source = _source(db, user, kind, item_id)
    source_id = source.id if source else None
    # Email raw-source bookmarks and the email view resolve the same provenance.
    source_match = (SourceProposal.source_kind == kind) & (SourceProposal.source_id == item_id)
    if source:
        source_match = or_(source_match, (SourceProposal.source_kind == "source") & (SourceProposal.source_id == source.id),
            (SourceProposal.source_kind == "email") & SourceProposal.source_id.in_(
                select(EmailMessageRecord.id).where(EmailMessageRecord.source_item_id == source.id,
                    EmailMessageRecord.id.in_(_owned_email(user)))))
    approved = select(SourceProposal.result_id).where(SourceProposal.organization_id == user.organization_id,
        SourceProposal.user_id == user.id, SourceProposal.status == "APPROVED", source_match)
    knowledge_ids = select(KnowledgeItem.id).where(KnowledgeItem.organization_id == user.organization_id,
        or_(KnowledgeItem.source_item_id == source_id if source_id else literal(False),
            KnowledgeItem.id.in_(approved.where(SourceProposal.target == "knowledge"))))
    knowledge = _rows(KnowledgeItem, "knowledge", literal("knowledge"), KnowledgeItem.summary,
        KnowledgeItem.evidence_text, KnowledgeItem.status, KnowledgeItem.updated_at, user).where(KnowledgeItem.id.in_(knowledge_ids))
    email_condition = EmailTaskSuggestion.email_message_record_id == row.id if kind == "email" else (EmailTaskSuggestion.source_item_id == source_id if source_id else literal(False))
    suggestions = _rows(EmailTaskSuggestion, "suggestion", literal("action"), EmailTaskSuggestion.title,
        EmailTaskSuggestion.evidence_text, EmailTaskSuggestion.status, EmailTaskSuggestion.created_at, user).where(
        EmailTaskSuggestion.email_message_record_id.in_(_owned_email(user)), email_condition, EmailTaskSuggestion.status == "SUGGESTED")
    actions = _rows(ActionItem, "action", literal("action"), ActionItem.title, ActionItem.evidence_text,
        ActionItem.status, ActionItem.created_at, user).where(or_(ActionItem.knowledge_item_id.in_(knowledge_ids),
            _confirmed_email_action(user).where(email_condition).exists(),
            ActionItem.id.in_(approved.where(SourceProposal.target == "action"))))
    rows = union_all(knowledge, suggestions, actions, _proposal_rows(user).where(source_match)).subquery()
    return _page(db, select(rows).order_by(rows.c.updated_at.desc(), rows.c.kind, rows.c.id), limit, offset)


@router.get("/files/{item_id}/chunks")
def file_chunks(item_id: UUID, limit: int = Query(10, ge=1, le=30), offset: int = Query(0, ge=0, le=100000),
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _source(db, user, "file", item_id)
    query = select(DocumentChunk.id, DocumentChunk.chunk_index, DocumentChunk.text).where(
        DocumentChunk.document_asset_id == item_id, DocumentChunk.organization_id == user.organization_id).order_by(DocumentChunk.chunk_index)
    return _page(db, query, limit, offset)


@router.get("/suggestions/{item_id}", response_model=EmailTaskSuggestionView)
def suggestion(item_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.scalar(select(EmailTaskSuggestion).where(EmailTaskSuggestion.id == item_id,
        EmailTaskSuggestion.organization_id == user.organization_id, EmailTaskSuggestion.email_message_record_id.in_(_owned_email(user))))
    if row is None:
        raise not_found("Suggestion not found.")
    return row


@router.get("/actions/{item_id}/context")
def action_context(item_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    action = db.scalar(select(ActionItem).where(ActionItem.id == item_id, ActionItem.organization_id == user.organization_id))
    if action is None:
        raise not_found("Action not found.")
    links = db.scalars(select(CalendarEventLink).join(IntegrationConnection).where(CalendarEventLink.action_item_id == item_id,
        CalendarEventLink.organization_id == user.organization_id, IntegrationConnection.user_id == user.id,
        IntegrationConnection.organization_id == user.organization_id).order_by(CalendarEventLink.created_at).limit(20)).all()
    email_id = db.scalar(select(EmailTaskSuggestion.email_message_record_id).join(AuditLog, AuditLog.target_id == EmailTaskSuggestion.id).where(
        AuditLog.organization_id == user.organization_id, AuditLog.actor_id == user.id,
        AuditLog.action_type == "CONFIRM_EMAIL_TASK", AuditLog.target_type == "EmailTaskSuggestion",
        AuditLog.detail["action_item_id"].as_string() == str(item_id),
        EmailTaskSuggestion.email_message_record_id.in_(_owned_email(user))))
    origins = result_origins(db, user, "action", item_id)
    if not origins and action.knowledge_item_id:
        knowledge = db.scalar(select(KnowledgeItem).where(KnowledgeItem.id == action.knowledge_item_id,
            KnowledgeItem.organization_id == user.organization_id))
        if knowledge:
            origins = result_origins(db, user, "knowledge", knowledge.id, knowledge.source_item_id)
    if not origins and email_id:
        origins = [origin(db, user, "email", email_id)]
    return {"origins": origins, "email_id": email_id, "calendar_links": [CalendarEventLinkView.model_validate(link) for link in links]}


@router.get("/knowledge/{item_id}/origin")
def knowledge_origin(item_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    item = db.scalar(select(KnowledgeItem).where(KnowledgeItem.id == item_id, KnowledgeItem.organization_id == user.organization_id))
    if not item:
        raise not_found("Knowledge not found.")
    return result_origins(db, user, "knowledge", item.id, item.source_item_id)
