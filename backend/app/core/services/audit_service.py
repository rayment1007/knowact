"""Audit logging service (Requirement 13).

The :class:`AuditService` is the single, shared way every state-changing
confirmation records an immutable :class:`~app.core.models.AuditLog` row. Its
design is deliberately small and has one crucial contract that the rest of the
Core Engine depends on:

    **``record`` writes exactly one ``AuditLog`` row inside the caller's
    existing transaction and never commits on its own.**

Callers (ClassificationService, KnowledgeService, ActionService,
DecisionService, MeetingService, …) mutate business memory and, in the *same*
transaction, call :meth:`AuditService.record`. Because ``record`` only
``add``/``flush``es — it does not ``commit`` — the audit row and the state
change it audits are committed (or rolled back) together atomically
(Requirements 13.1, 13.2). If the caller's transaction later fails and rolls
back, the audit row disappears with it, so an audit entry can never outlive the
change it claims to describe.

:meth:`AuditService.list` returns audit entries scoped to the caller's
organization (Requirement 13.3), most-recent first, with optional filtering.

Action-type naming follows the design's convention (e.g.
``CONFIRM_CLASSIFICATION``, ``CONFIRM_KNOWLEDGE``, ``CREATE_ACTION``,
``CREATE_DECISION``, ``CONFIRM_MEETING``, ``CREATE_REFERRAL``); the service
itself treats ``action_type`` as an opaque string so new action types can be
added without changing this code.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import AuditLog
from app.core.schemas import AuditFilter
from app.dependencies import scope_select


class AuditService:
    """Records and lists :class:`AuditLog` entries for an organization."""

    def __init__(self, db: Session) -> None:
        """Bind the service to a request-scoped session.

        The session's transaction is owned by the caller; this service adds
        rows to it but never commits, so audit writes stay atomic with the
        state change being audited.
        """

        self.db = db

    def record(
        self,
        org_id: UUID,
        actor_id: UUID,
        action_type: str,
        target_type: str,
        target_id: UUID,
        detail: dict | None = None,
    ) -> AuditLog:
        """Write exactly one ``AuditLog`` row in the caller's transaction.

        Persists a single immutable audit entry capturing *who* (``actor_id``)
        did *what* (``action_type``) to *which* object (``target_type`` +
        ``target_id``), plus free-form ``detail`` (Requirement 13.2). The row is
        scoped to ``org_id``.

        This method ``add``s the row and ``flush``es so the generated id and
        ``created_at`` are populated and any integrity error surfaces
        immediately — but it deliberately **does not commit**. The caller's
        surrounding transaction commits the audit row together with the state
        change it records, guaranteeing exactly one audit row per confirmation
        in the same transaction (Requirement 13.1). If the caller rolls back,
        the audit row is rolled back with it.

        Args:
            org_id: The tenant the audit entry belongs to.
            actor_id: The user who performed the action.
            action_type: The action performed (e.g. ``CONFIRM_CLASSIFICATION``).
            target_type: The kind of object affected (e.g. ``SourceItem``).
            target_id: The id of the affected object.
            detail: Optional structured context; stored as ``{}`` when omitted.

        Returns:
            The persisted (flushed) :class:`AuditLog`.
        """

        entry = AuditLog(
            organization_id=org_id,
            actor_id=actor_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            detail=detail if detail is not None else {},
        )
        self.db.add(entry)
        # Flush (not commit): assign the PK/created_at and enforce constraints
        # while leaving the transaction open for the caller to commit/rollback.
        self.db.flush()
        return entry

    def list(
        self,
        org_id: UUID,
        filters: AuditFilter | None = None,
    ) -> list[AuditLog]:
        """Return audit entries for an organization, most-recent first.

        Results are always constrained to ``org_id`` (Requirement 13.3) so one
        tenant can never see another's audit trail. Optional ``filters`` narrow
        the result by actor, action type, or target.

        Args:
            org_id: The tenant whose audit entries to return.
            filters: Optional :class:`AuditFilter`; when omitted, all of the
                organization's entries are returned.

        Returns:
            The matching :class:`AuditLog` rows ordered by ``created_at``
            descending (ties broken by ``id`` for a stable order).
        """

        filters = filters or AuditFilter()

        stmt = scope_select(select(AuditLog), AuditLog, org_id)
        if filters.actor_id is not None:
            stmt = stmt.where(AuditLog.actor_id == filters.actor_id)
        if filters.action_type is not None:
            stmt = stmt.where(AuditLog.action_type == filters.action_type)
        if filters.target_type is not None:
            stmt = stmt.where(AuditLog.target_type == filters.target_type)
        if filters.target_id is not None:
            stmt = stmt.where(AuditLog.target_id == filters.target_id)

        stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        return list(self.db.execute(stmt).scalars().all())


__all__ = ["AuditService"]
