"""Decision Memory service (Requirement 9).

The :class:`DecisionService` owns the *Decision Memory* of the Core Engine: it
records and lists :class:`~app.core.models.DecisionRecord` rows so that
organizational decisions stay traceable, each carrying its rationale and the
evidence that backs it (Requirement 9.1). It follows the same conventions as
the other Core Engine services:

* **Organization scoping is mandatory.** Every lookup is filtered by
  ``organization_id``; a row belonging to another tenant is indistinguishable
  from one that does not exist (Requirement 2.3).
* **The service never commits on its own.** It ``add``/``flush``es within the
  caller's (route's) transaction, which commits on success and rolls back on
  error. This is what lets a decision and its audit row commit atomically.

**The decision record is immutable.** The design's ``DecisionRecord`` captures a
decision *already made* — a statement, its rationale, the evidence behind it,
the owner who made it (``decided_by``), and when (``decided_at``). There is no
separate mutable "status" column: the recorded ``decision`` statement is itself
the outcome/status, so this service records decisions rather than transitioning
them (Requirement 9.1).

**Auditing.** Recording a decision mutates business memory, so — following the
design's rule that *every* memory-mutating change writes exactly one
:class:`~app.core.models.AuditLog` row in the same transaction (Requirements
9.2, 13.1) — :meth:`create` records exactly one ``CREATE_DECISION`` audit row
in the same transaction as the decision.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import DecisionRecord
from app.core.schemas import DecisionCreate, DecisionListFilter
from app.core.services.audit_service import AuditService
from app.dependencies import not_found, scope_select

#: Audit action type recorded when a decision is created (Requirements 9.2, 13.1).
CREATE_DECISION = "CREATE_DECISION"

#: Audit action type recorded when a decision is permanently deleted.
DELETE_DECISION = "DELETE_DECISION"


class DecisionService:
    """Record and list decision records for an organization (Requirement 9)."""

    def __init__(self, db: Session, audit: AuditService | None = None) -> None:
        """Bind the service to a request-scoped session.

        Args:
            db: The request-scoped session. Its transaction is owned by the
                caller (the route); this service adds/flushes rows but never
                commits, so a failed request rolls back cleanly and the decision
                and its audit row commit (or roll back) together atomically.
            audit: The :class:`AuditService` used to record creations in the
                same transaction. Defaults to a fresh instance bound to ``db``.
        """

        self.db = db
        self.audit = audit or AuditService(db)

    # -- Read ---------------------------------------------------------------

    def get(self, org_id: UUID, decision_id: UUID) -> DecisionRecord:
        """Return one org-scoped decision record or raise ``404``.

        The organization predicate is applied in SQL so a missing identifier
        and a record owned by another organization are indistinguishable.
        """

        stmt = scope_select(select(DecisionRecord), DecisionRecord, org_id).where(
            DecisionRecord.id == decision_id
        )
        decision = self.db.execute(stmt).scalar_one_or_none()
        if decision is None:
            raise not_found("Decision record not found.")
        return decision

    # -- Create (Requirements 9.1, 9.2, 9.4) --------------------------------

    def create(
        self,
        org_id: UUID,
        actor_id: UUID,
        payload: DecisionCreate,
    ) -> DecisionRecord:
        """Record a decision and its ``CREATE_DECISION`` audit row.

        Persists a :class:`DecisionRecord` with the decision statement,
        rationale, evidence text, owner (``decided_by`` = the acting user), and
        decided-at date, optionally linked to a business entity (Requirements
        9.1, 9.4). The originating evidence text is stored verbatim so a
        decision made from an AI output stays traceable (Requirement 9.4).

        Exactly one ``CREATE_DECISION`` audit row is written in the same
        transaction (Requirements 9.2, 13.1). Both rows are flushed (ids /
        timestamps populated) but not committed — the caller's transaction
        commits them together atomically.

        Args:
            org_id: The tenant the decision belongs to.
            actor_id: The user recording the decision (audit actor and owner).
            payload: The decision fields.

        Returns:
            The persisted (flushed) :class:`DecisionRecord`.
        """

        decided_at = payload.decided_at or datetime.now(timezone.utc)

        decision = DecisionRecord(
            organization_id=org_id,
            business_entity_id=payload.business_entity_id,
            title=payload.title,
            decision=payload.decision,
            rationale=payload.rationale,
            evidence_text=payload.evidence_text,
            decided_by=actor_id,
            decided_at=decided_at,
        )
        self.db.add(decision)
        self.db.flush()

        # Requirement 9.2 / 13.1: exactly one audit row in this transaction.
        self.audit.record(
            org_id=org_id,
            actor_id=actor_id,
            action_type=CREATE_DECISION,
            target_type="DecisionRecord",
            target_id=decision.id,
            detail={
                "business_entity_id": (
                    str(payload.business_entity_id)
                    if payload.business_entity_id is not None
                    else None
                ),
            },
        )

        self.db.flush()
        return decision

    # -- Delete -------------------------------------------------------------

    def delete(self, org_id: UUID, decision_id: UUID, actor_id: UUID) -> None:
        """Permanently delete a decision record (Requirement 2.3).

        Resolves the org-scoped decision (``404`` if missing/cross-tenant) and,
        in one transaction, deletes it and records exactly one
        ``DELETE_DECISION`` audit row.

        Args:
            org_id: The tenant the decision must belong to.
            decision_id: The decision to delete.
            actor_id: The user performing the delete (audit actor).

        Raises:
            HTTPException: ``404`` if the decision does not exist for the org.
        """

        stmt = scope_select(select(DecisionRecord), DecisionRecord, org_id).where(
            DecisionRecord.id == decision_id
        )
        decision = self.db.execute(stmt).scalar_one_or_none()
        if decision is None:
            raise not_found("Decision record not found.")

        deleted_id = decision.id
        self.db.delete(decision)
        self.db.flush()

        self.audit.record(
            org_id=org_id,
            actor_id=actor_id,
            action_type=DELETE_DECISION,
            target_type="DecisionRecord",
            target_id=deleted_id,
            detail={},
        )
        self.db.flush()

    # -- List (Requirement 9.3) ---------------------------------------------

    def list(
        self,
        org_id: UUID,
        filters: DecisionListFilter | None = None,
    ) -> list[DecisionRecord]:
        """Return an organization's decision records, newest first (Requirement 9.3).

        Results are always constrained to ``org_id`` (Requirement 9.3) so one
        tenant never sees another's decisions. An optional ``business_entity_id``
        filter narrows the result to the decisions connected to a single entity.
        Each record carries its statement, rationale, evidence, owner, and date
        (Requirement 9.3).

        Args:
            org_id: The tenant whose decisions to return.
            filters: Optional :class:`DecisionListFilter`; when omitted, all of
                the organization's decisions are returned.

        Returns:
            The matching :class:`DecisionRecord` rows ordered by ``decided_at``
            descending (ties broken by ``id`` for a stable order).
        """

        filters = filters or DecisionListFilter()

        stmt = scope_select(select(DecisionRecord), DecisionRecord, org_id)
        if filters.business_entity_id is not None:
            stmt = stmt.where(
                DecisionRecord.business_entity_id == filters.business_entity_id
            )

        stmt = stmt.order_by(
            DecisionRecord.decided_at.desc(), DecisionRecord.id.desc()
        )
        return list(self.db.execute(stmt).scalars().all())


__all__ = ["DecisionService", "CREATE_DECISION", "DELETE_DECISION"]
