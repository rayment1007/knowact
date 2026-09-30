"""Action Center service (Requirement 8).

The :class:`ActionService` owns the *Act* step of the pipeline: it creates and
manages :class:`~app.core.models.ActionItem` rows, whether those originate from
a confirmed AI recommendation or are entered by a human. It follows the same
conventions as the other Core Engine services:

* **Organization scoping is mandatory.** Every lookup is filtered by
  ``organization_id``; a missing or cross-tenant row is indistinguishable and
  yields ``404`` (Requirement 2.3).
* **The service never commits on its own.** It ``add``/``flush``es within the
  caller's (route's) transaction, which commits on success and rolls back on
  error. This is what lets an action and its audit row commit atomically.

Origin (AI vs human) is recorded on every action via the ``ai_generated`` flag
(Requirement 8.5): actions created from a confirmed suggestion carry
``ai_generated=True`` and inherit the suggestion's ``evidence_text``, while
manually created actions carry ``ai_generated=False``.

**Auditing.** Creating an action mutates business memory, so — following the
design's rule that *every* memory-mutating change writes exactly one
:class:`~app.core.models.AuditLog` row in the same transaction (Requirement
13.1) — **both** AI-originated and manual creations record a single
``CREATE_ACTION`` audit row. Requirement 8.1 makes this explicit for the
confirmed-suggestion path; applying it uniformly to manual creation keeps the
audit trail complete and consistent. Changed status/field updates record one
``UPDATE_ACTION`` row for the workspace activity feed; no-op updates add none.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import (
    ActionItem,
    ActionStatus,
    KnowledgeItem,
    SuggestionStatus,
)
from app.core.schemas import ActionCreate, ActionListFilter, ActionUpdate
from app.core.services.audit_service import AuditService
from app.dependencies import not_found, scope_select

#: Audit action type recorded when an action item is created (Requirements 8.1, 13.1).
CREATE_ACTION = "CREATE_ACTION"

#: Audit action type recorded when an action item is permanently deleted.
DELETE_ACTION = "DELETE_ACTION"


def _conflict(detail: str) -> HTTPException:
    """Build the ``409 Conflict`` used when an origin precondition fails.

    Creating an action *from a confirmed suggestion* requires the referenced
    knowledge item to actually be ``CONFIRMED`` (Requirement 8.1). A reference
    to a non-confirmed suggestion surfaces as ``409 Conflict`` so the caller
    knows the suggestion has not been human-approved yet.
    """

    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


class ActionService:
    """Create and manage action items for an organization (Requirement 8)."""

    def __init__(self, db: Session, audit: AuditService | None = None) -> None:
        """Bind the service to a request-scoped session.

        Args:
            db: The request-scoped session. Its transaction is owned by the
                caller (the route); this service adds/flushes rows but never
                commits, so a failed request rolls back cleanly and the action
                and its audit row commit (or roll back) together atomically.
            audit: The :class:`AuditService` used to record creations in the
                same transaction. Defaults to a fresh instance bound to ``db``.
        """

        self.db = db
        self.audit = audit or AuditService(db)

    # -- Internal helpers ---------------------------------------------------

    def _get_action(self, org_id: UUID, action_id: UUID) -> ActionItem:
        """Resolve an org-scoped action item or raise ``404`` (Requirement 2.3).

        A missing row or one belonging to another tenant is indistinguishable
        and yields ``404`` so the system never reveals another org's data.
        """

        stmt = scope_select(select(ActionItem), ActionItem, org_id).where(
            ActionItem.id == action_id
        )
        action = self.db.execute(stmt).scalar_one_or_none()
        if action is None:
            raise not_found("Action item not found.")
        return action

    def _get_confirmed_knowledge(
        self, org_id: UUID, knowledge_id: UUID
    ) -> KnowledgeItem:
        """Resolve an org-scoped, **confirmed** knowledge item for a suggestion.

        The confirmed-suggestion path (Requirement 8.1) creates an action from a
        recommendation that a human has already approved. A missing or
        cross-tenant knowledge item yields ``404`` (Requirement 2.3); an existing
        but not-yet-``CONFIRMED`` item yields ``409`` because an action must not
        be spawned from an unconfirmed suggestion.
        """

        stmt = scope_select(select(KnowledgeItem), KnowledgeItem, org_id).where(
            KnowledgeItem.id == knowledge_id
        )
        knowledge = self.db.execute(stmt).scalar_one_or_none()
        if knowledge is None:
            raise not_found("Knowledge item not found.")
        if knowledge.status != SuggestionStatus.CONFIRMED:
            raise _conflict(
                "Cannot create an action from a suggestion that is not confirmed."
            )
        return knowledge

    def get(self, org_id: UUID, action_id: UUID) -> ActionItem:
        """Return one org-scoped action item or raise ``404``.

        This public read path supports direct application deep links without
        loading the action list and searching it client-side. Missing and
        cross-organization identifiers intentionally share the same response.
        """

        return self._get_action(org_id, action_id)

    def _persist(
        self,
        org_id: UUID,
        actor_id: UUID,
        *,
        title: str,
        description: str | None,
        business_entity_id: UUID | None,
        knowledge_item_id: UUID | None,
        owner_id: UUID | None,
        due_date,
        evidence_text: str | None,
        ai_generated: bool,
    ) -> ActionItem:
        """Persist one ``OPEN`` action and its ``CREATE_ACTION`` audit row.

        Creates the :class:`ActionItem` in status ``OPEN`` (Requirements 8.1,
        8.2) with the given origin flag (Requirement 8.5), then records exactly
        one ``CREATE_ACTION`` audit row in the same transaction (Requirements
        8.1, 13.1). The row is flushed (id/created_at populated) but not
        committed — the caller's transaction commits both together.
        """

        action = ActionItem(
            organization_id=org_id,
            business_entity_id=business_entity_id,
            knowledge_item_id=knowledge_item_id,
            title=title,
            description=description,
            owner_id=owner_id,
            due_date=due_date,
            status=ActionStatus.OPEN,
            evidence_text=evidence_text,
            ai_generated=ai_generated,
        )
        self.db.add(action)
        self.db.flush()

        # Requirement 8.1 / 13.1: exactly one audit row in this transaction.
        self.audit.record(
            org_id=org_id,
            actor_id=actor_id,
            action_type=CREATE_ACTION,
            target_type="ActionItem",
            target_id=action.id,
            detail={
                "ai_generated": ai_generated,
                "business_entity_id": (
                    str(business_entity_id) if business_entity_id is not None else None
                ),
                "knowledge_item_id": (
                    str(knowledge_item_id) if knowledge_item_id is not None else None
                ),
            },
        )

        self.db.flush()
        return action

    # -- Create -------------------------------------------------------------

    def create(
        self,
        org_id: UUID,
        actor_id: UUID,
        payload: ActionCreate,
    ) -> ActionItem:
        """Create an action item in status ``OPEN`` (Requirements 8.1, 8.2, 8.5).

        Dispatches on the payload's origin:

        * When ``payload.knowledge_item_id`` is set, the action is created **from
          a confirmed suggestion** — see :meth:`create_from_suggestion`
          (Requirement 8.1): ``ai_generated=True`` and the evidence is inherited
          from the confirmed knowledge item.
        * Otherwise the action is **manually created** (Requirement 8.2):
          ``ai_generated=False`` (Requirement 8.5).

        Either way exactly one ``CREATE_ACTION`` audit row is written in the same
        transaction (Requirements 8.1, 13.1).

        Args:
            org_id: The tenant the action belongs to.
            actor_id: The user creating the action (audit actor / default owner).
            payload: The action fields.

        Returns:
            The persisted (flushed) ``OPEN`` :class:`ActionItem`.

        Raises:
            HTTPException: ``404`` if a referenced knowledge item does not exist
                for the organization; ``409`` if it exists but is not confirmed.
        """

        if payload.knowledge_item_id is not None:
            return self.create_from_suggestion(org_id, actor_id, payload)

        return self._persist(
            org_id,
            actor_id,
            title=payload.title,
            description=payload.description,
            business_entity_id=payload.business_entity_id,
            knowledge_item_id=None,
            owner_id=payload.owner_id,
            due_date=payload.due_date,
            evidence_text=payload.evidence_text,
            ai_generated=False,
        )

    def create_from_suggestion(
        self,
        org_id: UUID,
        actor_id: UUID,
        payload: ActionCreate,
    ) -> ActionItem:
        """Create an ``OPEN`` action from a confirmed suggestion (Requirement 8.1).

        Resolves the org-scoped, **confirmed** knowledge item referenced by
        ``payload.knowledge_item_id`` (``404`` if missing/cross-tenant, ``409`` if
        not confirmed) and creates an action that:

        * carries ``ai_generated=True`` (Requirement 8.5);
        * inherits the suggestion's ``evidence_text`` when the payload does not
          supply its own, so the action stays evidence-backed;
        * inherits the suggestion's ``business_entity_id`` when the payload does
          not specify one, keeping the action connected to the same entity;
        * links back to the originating knowledge item via ``knowledge_item_id``.

        Exactly one ``CREATE_ACTION`` audit row is written in the same
        transaction (Requirements 8.1, 13.1).

        Args:
            org_id: The tenant the action belongs to.
            actor_id: The user creating the action (audit actor).
            payload: The action fields; ``knowledge_item_id`` must be set.

        Returns:
            The persisted (flushed) ``OPEN`` :class:`ActionItem`.
        """

        knowledge = self._get_confirmed_knowledge(org_id, payload.knowledge_item_id)

        evidence_text = (
            payload.evidence_text
            if payload.evidence_text is not None
            else knowledge.evidence_text
        )
        business_entity_id = (
            payload.business_entity_id
            if payload.business_entity_id is not None
            else knowledge.business_entity_id
        )

        return self._persist(
            org_id,
            actor_id,
            title=payload.title,
            description=payload.description,
            business_entity_id=business_entity_id,
            knowledge_item_id=knowledge.id,
            owner_id=payload.owner_id,
            due_date=payload.due_date,
            evidence_text=evidence_text,
            ai_generated=True,
        )

    # -- Update (status / fields) -------------------------------------------

    def update(
        self,
        org_id: UUID,
        action_id: UUID,
        actor_id: UUID,
        payload: ActionUpdate,
    ) -> ActionItem:
        """Apply a partial update to an action (status and/or editable fields).

        Resolves the org-scoped action (``404`` if missing/cross-tenant) and
        applies only the fields explicitly provided in ``payload`` (unset fields
        are left untouched), so a client can, e.g., mark an action ``DONE``
        without disturbing its other fields (Requirements 8.3, 8.4). A changed
        value records one ``UPDATE_ACTION`` row in the same transaction.

        Args:
            org_id: The tenant the action must belong to.
            action_id: The action to update.
            actor_id: The user performing the update.
            payload: The fields to change; only explicitly-set fields are applied.

        Returns:
            The updated (flushed) :class:`ActionItem`.

        Raises:
            HTTPException: ``404`` if the action does not exist for the org.
        """

        action = self._get_action(org_id, action_id)

        changes = {field: value for field, value in payload.model_dump(exclude_unset=True).items()
            if getattr(action, field) != value}
        for field, value in changes.items():
            setattr(action, field, value)

        self.db.add(action)
        self.db.flush()
        if changes:
            self.audit.record(org_id=org_id, actor_id=actor_id, action_type="UPDATE_ACTION",
                target_type="ActionItem", target_id=action.id, detail={"fields": sorted(changes)})
        return action

    def update_status(
        self,
        org_id: UUID,
        action_id: UUID,
        actor_id: UUID,
        status: ActionStatus,
    ) -> ActionItem:
        """Transition an action to a new status (Requirements 8.3, 8.4).

        Resolves the org-scoped action (``404`` if missing/cross-tenant) and sets
        its ``status`` — e.g. to ``DONE`` when a user marks an action complete
        (Requirement 8.4). A thin wrapper over :meth:`update` for the common
        status-only case.

        Args:
            org_id: The tenant the action must belong to.
            action_id: The action to transition.
            actor_id: The user performing the transition.
            status: The new :class:`ActionStatus`.

        Returns:
            The updated (flushed) :class:`ActionItem`.

        Raises:
            HTTPException: ``404`` if the action does not exist for the org.
        """

        return self.update(
            org_id, action_id, actor_id, ActionUpdate(status=status)
        )

    # -- Delete -------------------------------------------------------------

    def delete(self, org_id: UUID, action_id: UUID, actor_id: UUID) -> None:
        """Permanently delete an action item and its calendar links.

        Resolves the org-scoped action (``404`` if missing/cross-tenant) and, in
        one transaction, deletes any CWI ``CalendarEventLink`` rows for the
        action (so the FK from the link to the action never blocks the delete),
        deletes the action, and records exactly one ``DELETE_ACTION`` audit row.
        The CWI model is imported lazily so this core service carries no
        module-load dependency on the CWI layer.

        Args:
            org_id: The tenant the action must belong to.
            action_id: The action to delete.
            actor_id: The user performing the delete (audit actor).

        Raises:
            HTTPException: ``404`` if the action does not exist for the org.
        """

        action = self._get_action(org_id, action_id)

        from app.modules.cwi.models import CalendarEventLink

        self.db.execute(
            sa_delete(CalendarEventLink).where(
                CalendarEventLink.organization_id == org_id,
                CalendarEventLink.action_item_id == action.id,
            )
        )

        deleted_id = action.id
        self.db.delete(action)
        self.db.flush()

        self.audit.record(
            org_id=org_id,
            actor_id=actor_id,
            action_type=DELETE_ACTION,
            target_type="ActionItem",
            target_id=deleted_id,
            detail={},
        )
        self.db.flush()

    # -- List (Requirement 8.3) ---------------------------------------------

    def list_open(
        self,
        org_id: UUID,
        filters: ActionListFilter | None = None,
    ) -> list[ActionItem]:
        """Return an organization's action items, newest first (Requirement 8.3).

        Results are always constrained to ``org_id`` (Requirement 8.3) so one
        tenant never sees another's actions. Optional ``filters`` narrow the
        result by ``status`` (e.g. only ``OPEN`` work) and/or
        ``business_entity_id``. With no ``status`` filter, actions in every
        status are returned so the Action Center can show the full picture.

        Args:
            org_id: The tenant whose actions to return.
            filters: Optional :class:`ActionListFilter`; when omitted, all of the
                organization's actions are returned.

        Returns:
            The matching :class:`ActionItem` rows ordered by ``created_at``
            descending (ties broken by ``id`` for a stable order).
        """

        filters = filters or ActionListFilter()

        stmt = scope_select(select(ActionItem), ActionItem, org_id)
        if filters.status is not None:
            stmt = stmt.where(ActionItem.status == filters.status)
        if filters.business_entity_id is not None:
            stmt = stmt.where(
                ActionItem.business_entity_id == filters.business_entity_id
            )

        stmt = stmt.order_by(ActionItem.created_at.desc(), ActionItem.id.desc())
        return list(self.db.execute(stmt).scalars().all())


__all__ = ["ActionService", "CREATE_ACTION", "DELETE_ACTION"]
