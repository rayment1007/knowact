"""Owner-scoped original source lookup shared by browsing and explicit drafting."""
from sqlalchemy import select
from app.core.models import SourceItem
from app.dependencies import not_found
from app.modules.cwi.models import CalendarSource, DocumentAsset, EmailMessageRecord, IntegrationConnection


def _owned_email(user):
    return select(EmailMessageRecord.id).join(IntegrationConnection).where(
        EmailMessageRecord.organization_id == user.organization_id,
        IntegrationConnection.organization_id == user.organization_id,
        IntegrationConnection.user_id == user.id)


def _source(db, user, kind, item_id, lock=False):
    model = {"source": SourceItem, "email": EmailMessageRecord, "file": DocumentAsset, "calendar": CalendarSource}[kind]
    query = select(model).where(model.id == item_id, model.organization_id == user.organization_id)
    if kind == "source":
        query = query.where(SourceItem.created_by == user.id)
    elif kind == "email":
        query = query.where(EmailMessageRecord.id.in_(_owned_email(user)))
    elif kind == "file":
        query = query.where(DocumentAsset.uploaded_by == user.id, DocumentAsset.source_deleted.is_(False))
    else:
        query = query.join(IntegrationConnection).where(IntegrationConnection.user_id == user.id,
            IntegrationConnection.organization_id == user.organization_id)
    if lock:
        query = query.with_for_update(of=model).execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None:
        raise not_found("Source not found.")
    source = row if kind == "source" else None
    if kind == "email" and row.source_item_id:
        source_query = select(SourceItem).where(SourceItem.id == row.source_item_id,
            SourceItem.organization_id == user.organization_id, SourceItem.created_by == user.id)
        if lock:
            source_query = source_query.with_for_update().execution_options(populate_existing=True)
        source = db.scalar(source_query)
    return row, source
