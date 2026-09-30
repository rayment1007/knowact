"""Source item ingestion service (Requirement 3).

The :class:`IngestionService` owns the collect side of the pipeline: it takes
raw organizational content, persists it as a :class:`~app.core.models.SourceItem`
in status ``NEW``, lists the organization's inbox, fetches a single item
together with its current classification, and dismisses items as irrelevant.

Two conventions run through every method:

* **Organization scoping is mandatory.** Every query is filtered by
  ``organization_id`` via :func:`app.dependencies.scope_select`. A row that
  belongs to another tenant is indistinguishable from a missing row: scoped
  lookups that return nothing raise :func:`app.dependencies.not_found` (a
  ``404``), never a ``403`` (Requirement 2.3).
* **The service never commits on its own.** Writes ``add``/``flush`` within the
  caller's (route's) transaction; the route's ``get_db`` dependency commits at
  the end of a successful request and rolls back on error. Combined with
  Pydantic request validation — which rejects invalid payloads with ``422``
  before the service ever runs — this guarantees that a failed request leaves
  no partial source item behind (Requirement 3.5).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from app.core.models import (
    ClassificationResult,
    KnowledgeItem,
    SourceItem,
    SourceStatus,
    SuggestionStatus,
)
from app.core.schemas import InboxFilter, SourceItemCreate
from app.core.services.audit_service import AuditService
from app.dependencies import not_found, scope_select

#: Audit action type recorded when a source item is permanently deleted.
DELETE_SOURCE_ITEM = "DELETE_SOURCE_ITEM"


class IngestionService:
    """Collect, list, fetch, dismiss, and delete :class:`SourceItem` rows."""

    def __init__(self, db: Session, audit: AuditService | None = None) -> None:
        """Bind the service to a request-scoped session.

        The session's transaction is owned by the caller (the route); this
        service adds/flushes rows but never commits, so a failed request rolls
        back cleanly with no partial writes.

        Args:
            db: The request-scoped session.
            audit: The :class:`AuditService` used to record the single
                ``DELETE_SOURCE_ITEM`` audit row in the same transaction as a
                delete. Defaults to a fresh instance bound to ``db``.
        """

        self.db = db
        self.audit = audit or AuditService(db)

    def ingest(
        self,
        org_id: UUID,
        user_id: UUID,
        payload: SourceItemCreate,
    ) -> SourceItem:
        """Persist a new source item in status ``NEW`` (Requirement 3.1).

        Records the creating user (``created_by``) and organization
        (``organization_id``) from the authenticated session rather than the
        client payload. ``received_at`` defaults to the database ``now()`` when
        omitted. The row is flushed (so its id/timestamps are populated) but not
        committed — the route's transaction commits it on success.

        Args:
            org_id: The tenant the item belongs to.
            user_id: The user collecting the item.
            payload: The validated create payload.

        Returns:
            The persisted (flushed) :class:`SourceItem` with status ``NEW``.
        """

        item = SourceItem(
            organization_id=org_id,
            created_by=user_id,
            source_type=payload.source_type,
            title=payload.title,
            content=payload.content,
            status=SourceStatus.NEW,
        )
        if payload.received_at is not None:
            item.received_at = payload.received_at

        self.db.add(item)
        self.db.flush()
        self.audit.record(org_id=org_id, actor_id=user_id, action_type="CREATE_SOURCE_ITEM",
            target_type="SourceItem", target_id=item.id, detail={})
        return item

    def list_inbox(
        self,
        org_id: UUID,
        filters: InboxFilter | None = None,
        review_user_id: UUID | None = None,
    ) -> list[SourceItem]:
        """Return the organization's source items, newest first.

        Results are always constrained to ``org_id`` (Requirement 3.2) and may
        be narrowed by status and business category. Filtering by category joins
        the item's current non-rejected classification, so only items whose
        active classification matches the requested category are returned;
        items without a classification are excluded when a category filter is
        supplied.

        Args:
            org_id: The tenant whose inbox to return.
            filters: Optional :class:`InboxFilter`; when omitted, all of the
                organization's items are returned.

        Returns:
            The matching :class:`SourceItem` rows ordered by ``received_at``
            descending (ties broken by ``id`` for a stable order).
        """

        filters = filters or InboxFilter()

        stmt = scope_select(select(SourceItem), SourceItem, org_id)
        if filters.review_pending:
            stmt = stmt.where(
                SourceItem.status.notin_([SourceStatus.ARCHIVED, SourceStatus.DISMISSED]),
                select(ClassificationResult.id).where(
                    ClassificationResult.source_item_id == SourceItem.id,
                    ClassificationResult.status == SuggestionStatus.SUGGESTED,
                ).exists(),
            )
            if review_user_id is not None:
                stmt = stmt.where(SourceItem.created_by == review_user_id)
        if filters.status is not None:
            stmt = stmt.where(SourceItem.status == filters.status)
        if filters.business_category is not None:
            # Category lives on the classification, not the source item. Only
            # items with a matching non-rejected classification qualify.
            stmt = stmt.join(
                ClassificationResult,
                ClassificationResult.source_item_id == SourceItem.id,
            ).where(
                ClassificationResult.business_category == filters.business_category,
                ClassificationResult.status != SuggestionStatus.REJECTED,
            )

        stmt = stmt.order_by(SourceItem.received_at.desc(), SourceItem.id.desc())
        return list(self.db.execute(stmt).scalars().all())

    def get(self, org_id: UUID, item_id: UUID) -> SourceItem:
        """Return a single source item, scoped to the organization.

        Args:
            org_id: The tenant the item must belong to.
            item_id: The id of the item to fetch.

        Returns:
            The matching :class:`SourceItem`.

        Raises:
            HTTPException: ``404 Not Found`` if the item does not exist or
                belongs to another organization (Requirement 2.3).
        """

        stmt = scope_select(select(SourceItem), SourceItem, org_id).where(
            SourceItem.id == item_id
        )
        item = self.db.execute(stmt).scalar_one_or_none()
        if item is None:
            raise not_found("Source item not found.")
        return item

    def get_current_classification(
        self, item_id: UUID
    ) -> ClassificationResult | None:
        """Return the item's current (non-rejected) classification, if any.

        The system maintains at most one non-rejected classification per item
        (Requirement 4.5), so this returns that single active result or ``None``
        when the item has not been classified yet (Requirement 3.3). The lookup
        is by ``source_item_id`` only; tenant scoping is enforced by the caller
        having already resolved the org-scoped source item via :meth:`get`.
        """

        stmt = (
            select(ClassificationResult)
            .where(ClassificationResult.source_item_id == item_id)
            .where(ClassificationResult.status != SuggestionStatus.REJECTED)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def dismiss(
        self,
        org_id: UUID,
        item_id: UUID,
        actor_id: UUID,
    ) -> SourceItem:
        """Transition a source item to ``DISMISSED`` (Requirement 3.4).

        The item is resolved with organization scoping first, so dismissing a
        non-existent or cross-tenant item yields ``404`` (Requirement 2.3). The
        status change is flushed but not committed; the route's transaction
        commits it on success.

        Args:
            org_id: The tenant the item must belong to.
            item_id: The id of the item to dismiss.
            actor_id: The user performing the dismissal.

        Returns:
            The updated :class:`SourceItem` with status ``DISMISSED``.
        """

        item = self.get(org_id, item_id)
        item.status = SourceStatus.DISMISSED
        self.db.add(item)
        self.db.flush()
        return item

    def delete(self, org_id: UUID, item_id: UUID, actor_id: UUID) -> None:
        """Permanently delete a source item and cascade its dependents.

        This is a HARD delete (distinct from :meth:`dismiss`). The item is
        resolved with organization scoping first, so deleting a non-existent or
        cross-tenant item yields ``404`` (Requirement 2.3). In one transaction
        it:

        * deletes the item's :class:`ClassificationResult` rows (they derive
          their tenant from, and only exist for, this source item); and
        * **NULLs** the ``source_item_id`` FK on the derived business rows that
          reference it — :class:`KnowledgeItem`, :class:`ActionItem`, and the
          CWI ``EmailMessageRecord`` / ``EmailTaskSuggestion`` rows — so the
          confirmed memory/actions those represent are **retained**, just
          detached from the now-deleted raw source; and
        * records exactly one ``DELETE_SOURCE_ITEM`` audit row.

        The CWI models are imported lazily so this core service carries no
        module-load dependency on the CWI layer.

        Args:
            org_id: The tenant the item must belong to.
            item_id: The id of the item to delete.
            actor_id: The user performing the delete (audit actor).

        Raises:
            HTTPException: ``404`` if the item does not exist for the org.
        """

        item = self.get(org_id, item_id)

        from app.modules.cwi.models import EmailMessageRecord
        from app.modules.cwi.services.source_proposal_service import discard_unapproved
        discard_unapproved(self.db, org_id, "source", [item.id])
        email_ids = list(self.db.scalars(select(EmailMessageRecord.id).where(
            EmailMessageRecord.organization_id == org_id, EmailMessageRecord.source_item_id == item.id)))
        discard_unapproved(self.db, org_id, "email", email_ids)

        # Classification results only exist for this source item; delete them.
        self.db.execute(
            sa_delete(ClassificationResult).where(
                ClassificationResult.source_item_id == item.id
            )
        )

        # NULL the FK on the derived KnowledgeItem rows so the confirmed
        # business memory is retained, detached from the deleted source.
        # (ActionItem has no source_item_id column — it links to source only via
        # its knowledge item — so no action FK needs nulling here.)
        self.db.execute(
            sa_update(KnowledgeItem)
            .where(
                KnowledgeItem.organization_id == org_id,
                KnowledgeItem.source_item_id == item.id,
            )
            .values(source_item_id=None)
        )

        # NULL the FK on derived CWI rows too (lazy import keeps core free of a
        # module-load dependency on the CWI layer): the Gmail provenance record,
        # its extracted task suggestion, and any AI email draft replying to it.
        from app.modules.cwi.models import (
            EmailDraft,
            EmailMessageRecord,
            EmailTaskSuggestion,
        )

        self.db.execute(
            sa_update(EmailMessageRecord)
            .where(
                EmailMessageRecord.organization_id == org_id,
                EmailMessageRecord.source_item_id == item.id,
            )
            .values(source_item_id=None)
        )
        self.db.execute(
            sa_update(EmailTaskSuggestion)
            .where(
                EmailTaskSuggestion.organization_id == org_id,
                EmailTaskSuggestion.source_item_id == item.id,
            )
            .values(source_item_id=None)
        )
        self.db.execute(
            sa_update(EmailDraft)
            .where(
                EmailDraft.organization_id == org_id,
                EmailDraft.source_item_id == item.id,
            )
            .values(source_item_id=None)
        )

        deleted_id = item.id
        self.db.delete(item)
        self.db.flush()

        self.audit.record(
            org_id=org_id,
            actor_id=actor_id,
            action_type=DELETE_SOURCE_ITEM,
            target_type="SourceItem",
            target_id=deleted_id,
            detail={},
        )
        self.db.flush()


__all__ = ["IngestionService", "DELETE_SOURCE_ITEM"]
