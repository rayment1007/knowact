"""Privacy, control & deletion (M6.7, Requirement 33).

:class:`PrivacyService` gives users full control over their connected data:

* **Disconnect / revoke** — delegates to :class:`IntegrationService` so a
  connection is set to ``REVOKED`` (stopping all future sync while retaining
  imported data until explicitly deleted) or has its Google grant revoked and
  is disconnected locally. Each already writes exactly one audit row in the
  caller's transaction (Requirements 33.1, 33.2).
* **Delete imported email data** — removes the selected
  :class:`~app.modules.cwi.models.EmailMessageRecord`s (per chosen scope) and,
  in the **same transaction**, removes/invalidates the related
  :class:`~app.modules.cwi.models.DocumentChunk`s (with their embeddings/vector
  references) and any cached retrieval entries derived from that source, marks
  the provenance of any derived :class:`~app.modules.cwi.models.DocumentAsset`
  (``source_deleted = True``), retains confirmed business records by default,
  and writes exactly one ``DELETE_EMAIL_DATA`` audit row (Requirements 33.3,
  33.8, 33.9).
* **Delete uploaded documents** — delegates to
  :class:`DocumentService.delete`, which removes the binary from the
  :class:`StorageBackend`, cascades the asset's chunks/embeddings, and writes
  exactly one ``DELETE_DOCUMENT`` audit row (Requirements 33.4, 33.9).
* **Raw-email retention policy** — persists the user's choice
  (``RAW_AND_EXTRACTED`` vs ``EXTRACTED_ONLY`` + optional window) and applies it
  to whether raw message content is retained (Requirement 33.5).
* **Last-sync status** — surfaces each connection's ``last_sync_at``,
  ``last_error``, and ``status`` (Requirement 33.6).
* **What data was used for an AI answer** — returns the exact citations and
  evidence set that grounded a persisted Copilot answer (Requirement 33.7).

Conventions carried over from the other CWI services: every lookup is
org-scoped (a cross-org id is indistinguishable from a missing one and yields
``404``), and the service ``add``/``flush``es within the caller's transaction
and never commits, so every deletion and its single audit row commit atomically.

Deletion cascade & derived-record provenance
---------------------------------------------
Emails in this architecture are ingested as ``EMAIL`` ``SourceItem``s and are
never chunked/embedded (only uploaded documents produce
:class:`DocumentChunk`s), and retrieval is computed on demand rather than from a
persistent cache. So deleting imported email data:

1. deletes the derived AI artifacts that structurally depend on the records
   (:class:`~app.modules.cwi.models.EmailTaskSuggestion`s) — these are
   suggestions/negative-signals, not confirmed business memory;
2. deletes any :class:`DocumentChunk`s (and their embeddings/vector refs) whose
   metadata references the deleted email source items (defensive — normally
   none), which is also what invalidates any on-demand retrieval derived from
   that source;
3. marks any :class:`DocumentAsset` derived from those sources
   ``source_deleted = True`` for provenance (defensive);
4. **retains** confirmed business records (``KnowledgeItem`` / ``ActionItem`` /
   ``DecisionRecord``) derived from the deleted source — they are human-confirmed
   memory, not raw imported content — while the immutable ``DELETE_EMAIL_DATA``
   audit row records exactly which source items were deleted and how many
   derived records were retained, preserving the provenance that the source was
   deleted (Requirement 33.8);
5. deletes the selected :class:`EmailMessageRecord`s themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update as sa_update
from sqlalchemy.orm import Session

from app.core.models import ActionItem, KnowledgeItem
from app.core.services.audit_service import AuditService
from app.dependencies import not_found, scope_select
from app.modules.cwi.models import (
    CopilotAnswerLog,
    DocumentAsset,
    DocumentChunk,
    EmailMessageRecord,
    EmailTaskSuggestion,
    IntegrationConnection,
    RawEmailRetentionMode,
    RawEmailRetentionPolicy,
)
from app.modules.cwi.services.document_service import DocumentService
from app.modules.cwi.services.integration_service import IntegrationService

# Audit action types recorded per privacy deletion (Requirement 33.9 / P20).
DELETE_EMAIL_DATA = "DELETE_EMAIL_DATA"

_EMAIL_TARGET_TYPE = "EmailMessageData"


@dataclass
class EmailDeletionResult:
    """Counts describing an email-data deletion cascade (Requirement 33.3)."""

    deleted_records: int = 0
    deleted_task_suggestions: int = 0
    deleted_chunks: int = 0
    marked_documents_source_deleted: int = 0
    retained_derived_records: int = 0


class PrivacyService:
    """User-facing privacy, control & deletion operations (Requirement 33)."""

    def __init__(
        self,
        db: Session,
        *,
        integration_service: IntegrationService | None = None,
        document_service: DocumentService | None = None,
    ) -> None:
        """Bind the service to a session and (optionally) delegate services.

        Args:
            db: Request-scoped session (transaction owned by the caller/route).
            integration_service: The :class:`IntegrationService` used for
                disconnect/revoke. When omitted, disconnect/revoke are
                unavailable through this service (the route wires one).
            document_service: The :class:`DocumentService` used to delete
                uploaded documents. When omitted, document deletion is
                unavailable through this service (the route wires one).
        """

        self.db = db
        self.audit = AuditService(db)
        self._integration_service = integration_service
        self._document_service = document_service

    # -- Disconnect / revoke (Requirements 33.1, 33.2) ----------------------

    def disconnect(
        self, org_id: UUID, user_id: UUID, connection_id: UUID
    ) -> IntegrationConnection:
        """Disconnect a connection: stop future sync, retain data (Req 33.1).

        Delegates to :meth:`IntegrationService.disconnect`, which sets the
        connection to ``REVOKED`` and writes exactly one
        ``DISCONNECT_INTEGRATION`` audit row in the same transaction. Existing
        imported data is retained until explicitly deleted.
        """

        if self._integration_service is None:  # pragma: no cover - misuse guard
            raise RuntimeError("PrivacyService has no IntegrationService bound.")
        return self._integration_service.disconnect(org_id, user_id, connection_id)

    def revoke(
        self, org_id: UUID, user_id: UUID, connection_id: UUID
    ) -> IntegrationConnection:
        """Revoke Google access and disconnect locally (Requirement 33.2).

        Delegates to :meth:`IntegrationService.revoke_google_access`, which
        calls Google's revocation endpoint and then locally sets the connection
        to ``REVOKED``, writing exactly one ``REVOKE_INTEGRATION`` audit row in
        the same transaction.
        """

        if self._integration_service is None:  # pragma: no cover - misuse guard
            raise RuntimeError("PrivacyService has no IntegrationService bound.")
        return self._integration_service.revoke_google_access(
            org_id, user_id, connection_id
        )

    # -- Delete imported email data (Requirements 33.3, 33.8, 33.9) ---------

    def _target_email_records(
        self,
        org_id: UUID,
        *,
        scope: str,
        connection_id: UUID | None,
        record_ids: list[UUID] | None,
    ) -> list[EmailMessageRecord]:
        """Resolve the org-scoped records to delete for the chosen scope."""

        stmt = scope_select(
            select(EmailMessageRecord), EmailMessageRecord, org_id
        )
        normalized = (scope or "ALL").upper()
        if normalized == "CONNECTION":
            if connection_id is None:
                raise not_found("A connection id is required for this scope.")
            stmt = stmt.where(
                EmailMessageRecord.integration_connection_id == connection_id
            )
        elif normalized == "SELECTED":
            ids = record_ids or []
            if not ids:
                return []
            stmt = stmt.where(EmailMessageRecord.id.in_(ids))
        # ALL: no additional predicate (every org record).
        return list(self.db.execute(stmt).scalars().all())

    def delete_email_data(
        self,
        org_id: UUID,
        user_id: UUID,
        *,
        scope: str = "ALL",
        connection_id: UUID | None = None,
        record_ids: list[UUID] | None = None,
    ) -> EmailDeletionResult:
        """Delete imported email data and cascade in one transaction (33.3).

        Removes the selected :class:`EmailMessageRecord`s (per ``scope``) and,
        in the SAME transaction: their dependent
        :class:`EmailTaskSuggestion`s, any :class:`DocumentChunk`s/embeddings
        derived from the affected email source items (which also invalidates any
        on-demand retrieval over that source), and marks any derived
        :class:`DocumentAsset` ``source_deleted = True``. Confirmed business
        records derived from the source are **retained** and their count is
        recorded in the single ``DELETE_EMAIL_DATA`` audit row, preserving the
        provenance that the source was deleted (Requirement 33.8). Writes
        exactly one audit row (Requirement 33.9).
        """

        records = self._target_email_records(
            org_id,
            scope=scope,
            connection_id=connection_id,
            record_ids=record_ids,
        )
        result = EmailDeletionResult()
        record_ids_resolved = [record.id for record in records]
        source_item_ids = [
            record.source_item_id
            for record in records
            if record.source_item_id is not None
        ]

        if not record_ids_resolved:
            # Nothing matched; still record the (no-op) deletion request so the
            # action is auditable exactly once (Requirement 33.9).
            self.audit.record(
                org_id=org_id,
                actor_id=user_id,
                action_type=DELETE_EMAIL_DATA,
                target_type=_EMAIL_TARGET_TYPE,
                target_id=connection_id or user_id,
                detail={
                    "scope": (scope or "ALL").upper(),
                    "deleted_records": 0,
                },
            )
            self.db.flush()
            return result

        # 1) Delete dependent AI task suggestions (FK on email_message_record_id
        #    and source_item_id). These are suggestions/negative signals, not
        #    confirmed business memory.
        suggestion_result = self.db.execute(
            sa_delete(EmailTaskSuggestion)
            .where(EmailTaskSuggestion.organization_id == org_id)
            .where(
                EmailTaskSuggestion.email_message_record_id.in_(
                    record_ids_resolved
                )
            )
        )
        result.deleted_task_suggestions = int(suggestion_result.rowcount or 0)

        # 2) Invalidate derived chunks/embeddings/vector refs + on-demand
        #    retrieval (defensive: emails normally produce none).
        result.deleted_chunks = self._delete_derived_chunks(
            org_id, source_item_ids
        )

        # 3) Mark provenance on any derived DocumentAsset (defensive).
        result.marked_documents_source_deleted = (
            self._mark_derived_documents_source_deleted(org_id, source_item_ids)
        )

        # 4) Count the confirmed derived business records we RETAIN, so the
        #    audit row preserves the "source deleted" provenance (Req 33.8).
        result.retained_derived_records = self._count_retained_derived_records(
            org_id, source_item_ids
        )

        # 5) Delete the selected email records themselves.
        delete_result = self.db.execute(
            sa_delete(EmailMessageRecord)
            .where(EmailMessageRecord.organization_id == org_id)
            .where(EmailMessageRecord.id.in_(record_ids_resolved))
        )
        result.deleted_records = int(delete_result.rowcount or 0)
        self.db.flush()

        # Exactly one audit row for the whole cascade (Requirement 33.9).
        self.audit.record(
            org_id=org_id,
            actor_id=user_id,
            action_type=DELETE_EMAIL_DATA,
            target_type=_EMAIL_TARGET_TYPE,
            target_id=connection_id or user_id,
            detail={
                "scope": (scope or "ALL").upper(),
                "deleted_records": result.deleted_records,
                "deleted_task_suggestions": result.deleted_task_suggestions,
                "deleted_chunks": result.deleted_chunks,
                "marked_documents_source_deleted": (
                    result.marked_documents_source_deleted
                ),
                "retained_derived_records": result.retained_derived_records,
                "source_item_ids": [str(sid) for sid in source_item_ids],
            },
        )
        self.db.flush()
        return result

    def _delete_derived_chunks(
        self, org_id: UUID, source_item_ids: list[UUID]
    ) -> int:
        """Delete chunks/embeddings whose metadata references the sources.

        Emails are not chunked in this architecture, so this normally matches
        nothing; it is applied defensively so any future email-derived chunk (or
        one tagged with an email ``source_item_id`` in its ``metadata_json``) is
        removed together with its embedding/vector reference in the same
        transaction (Requirement 33.3).
        """

        if not source_item_ids:
            return 0
        wanted = {str(sid) for sid in source_item_ids}
        stmt = scope_select(select(DocumentChunk), DocumentChunk, org_id)
        deleted = 0
        for chunk in self.db.execute(stmt).scalars().all():
            metadata = chunk.metadata_json or {}
            ref = str(metadata.get("source_item_id", ""))
            if ref and ref in wanted:
                self.db.delete(chunk)
                deleted += 1
        if deleted:
            self.db.flush()
        return deleted

    def _mark_derived_documents_source_deleted(
        self, org_id: UUID, source_item_ids: list[UUID]
    ) -> int:
        """Mark any DocumentAsset derived from the sources ``source_deleted``.

        Provenance for a confirmed record derived from a now-deleted source
        (Requirement 33.8). Defensive: matches documents whose chunks carry the
        email ``source_item_id`` in their metadata (normally none).
        """

        if not source_item_ids:
            return 0
        wanted = {str(sid) for sid in source_item_ids}
        chunk_stmt = scope_select(select(DocumentChunk), DocumentChunk, org_id)
        asset_ids: set[UUID] = set()
        for chunk in self.db.execute(chunk_stmt).scalars().all():
            metadata = chunk.metadata_json or {}
            ref = str(metadata.get("source_item_id", ""))
            if ref and ref in wanted:
                asset_ids.add(chunk.document_asset_id)
        if not asset_ids:
            return 0
        update_result = self.db.execute(
            sa_update(DocumentAsset)
            .where(DocumentAsset.organization_id == org_id)
            .where(DocumentAsset.id.in_(asset_ids))
            .where(DocumentAsset.source_deleted.is_(False))
            .values(source_deleted=True)
        )
        self.db.flush()
        return int(update_result.rowcount or 0)

    def _count_retained_derived_records(
        self, org_id: UUID, source_item_ids: list[UUID]
    ) -> int:
        """Count confirmed business records derived from the deleted sources.

        These are retained by default (human-confirmed memory); the count feeds
        the audit row that preserves the "source deleted" provenance
        (Requirement 33.8). Covers ``KnowledgeItem``s linked by
        ``source_item_id`` and the ``ActionItem``s linked to those knowledge
        items; ``DecisionRecord``s carry no source link.
        """

        if not source_item_ids:
            return 0
        knowledge_ids = list(
            self.db.execute(
                scope_select(
                    select(KnowledgeItem.id), KnowledgeItem, org_id
                ).where(KnowledgeItem.source_item_id.in_(source_item_ids))
            ).scalars().all()
        )
        count = len(knowledge_ids)
        if knowledge_ids:
            action_count = self.db.execute(
                scope_select(
                    select(ActionItem.id), ActionItem, org_id
                ).where(ActionItem.knowledge_item_id.in_(knowledge_ids))
            ).scalars().all()
            count += len(list(action_count))
        return count

    # -- Delete uploaded documents (Requirements 33.4, 33.9) ----------------

    def delete_document(
        self, org_id: UUID, user_id: UUID, document_id: UUID
    ) -> None:
        """Delete an uploaded document via :class:`DocumentService` (33.4).

        Removes the binary from the :class:`StorageBackend`, cascades the
        asset's chunks/embeddings, and writes exactly one ``DELETE_DOCUMENT``
        audit row in the same transaction. A cross-org/missing id yields ``404``.
        """

        if self._document_service is None:  # pragma: no cover - misuse guard
            raise RuntimeError("PrivacyService has no DocumentService bound.")
        self._document_service.delete(org_id, user_id, document_id)

    # -- Raw-email retention policy (Requirement 33.5) ----------------------

    def get_retention_policy(
        self, org_id: UUID, user_id: UUID
    ) -> RawEmailRetentionPolicy | None:
        """Return the user's persisted retention policy, if any (Req 33.5)."""

        stmt = (
            scope_select(
                select(RawEmailRetentionPolicy), RawEmailRetentionPolicy, org_id
            )
            .where(RawEmailRetentionPolicy.user_id == user_id)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def set_retention_policy(
        self,
        org_id: UUID,
        user_id: UUID,
        *,
        mode: RawEmailRetentionMode,
        retention_window_days: int | None = None,
    ) -> RawEmailRetentionPolicy:
        """Persist the retention policy and apply it to raw content (33.5).

        Upserts the single ``(org, user)`` policy row. When the mode is
        ``EXTRACTED_ONLY`` the policy is applied immediately by clearing the
        ``stored_raw`` flag on the organization's ingested email records, so raw
        message content is no longer retained.
        """

        policy = self.get_retention_policy(org_id, user_id)
        if policy is None:
            policy = RawEmailRetentionPolicy(
                organization_id=org_id,
                user_id=user_id,
                mode=mode,
                retention_window_days=retention_window_days,
            )
            self.db.add(policy)
        else:
            policy.mode = mode
            policy.retention_window_days = retention_window_days
            self.db.add(policy)
        self.db.flush()

        # Apply the policy: EXTRACTED_ONLY drops retained raw content.
        if mode == RawEmailRetentionMode.EXTRACTED_ONLY:
            self.db.execute(
                sa_update(EmailMessageRecord)
                .where(EmailMessageRecord.organization_id == org_id)
                .where(EmailMessageRecord.stored_raw.is_(True))
                .values(stored_raw=False)
            )
            self.db.flush()
        return policy

    # -- Last-sync status (Requirement 33.6) --------------------------------

    def sync_status(
        self, org_id: UUID, user_id: UUID
    ) -> list[IntegrationConnection]:
        """Return the user's connections with their last-sync state (33.6).

        Each connection carries ``last_sync_at``, ``last_error``, and
        ``status`` — exactly what the Privacy page surfaces.
        """

        stmt = (
            scope_select(
                select(IntegrationConnection), IntegrationConnection, org_id
            )
            .where(IntegrationConnection.user_id == user_id)
            .order_by(
                IntegrationConnection.created_at.desc(),
                IntegrationConnection.id.desc(),
            )
        )
        return list(self.db.execute(stmt).scalars().all())

    # -- What data was used for an AI answer (Requirement 33.7) -------------

    def answer_provenance(
        self, org_id: UUID, answer_id: UUID
    ) -> CopilotAnswerLog:
        """Return the persisted answer + its citations/evidence set (33.7).

        Org-scoped: a cross-org/missing answer id is indistinguishable from a
        genuinely missing one and yields ``404``.
        """

        stmt = scope_select(
            select(CopilotAnswerLog), CopilotAnswerLog, org_id
        ).where(CopilotAnswerLog.id == answer_id)
        log = self.db.execute(stmt).scalar_one_or_none()
        if log is None:
            raise not_found("Answer not found.")
        return log


__all__ = [
    "PrivacyService",
    "EmailDeletionResult",
    "DELETE_EMAIL_DATA",
]
