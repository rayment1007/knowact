"""Knowledge extraction service (Requirement 6).

The :class:`KnowledgeService` owns the *Connect + Summarize* steps of the
pipeline: it turns a source item that already carries a **CONFIRMED**
classification into a structured, evidence-backed
:class:`~app.core.models.KnowledgeItem`, connecting it to a
:class:`~app.core.models.BusinessEntity`. This task (10.1) implements the
extraction path and its two safety gates plus entity linking; confirmation,
rejection, and the hub/list operations land in task 10.2.

Two safety gates protect the knowledge base (Requirement 6, design "Key
Workflow 3: Classification with Safety Gating"). Both are evaluated against the
source item's **confirmed** classification and both refuse *before* any AI call
or persistence, so a refusal never leaves a partial :class:`KnowledgeItem`
behind:

* **Privacy gate (Requirement 6.3).** If the confirmed ``relevance`` is one of
  ``{PERSONAL, IRRELEVANT, SPAM, SYSTEM_NOTIFICATION}`` the content is excluded
  from the knowledge base and extraction is refused with ``409 Conflict``.
* **Sensitivity gate (Requirements 6.4, 6.5).** If the confirmed ``sensitivity``
  is ``HIGHLY_SENSITIVE`` the caller must pass ``acknowledged=True``; without an
  explicit acknowledgment extraction is refused with ``409 Conflict``. With the
  acknowledgment, extraction proceeds.

On success the service persists exactly one :class:`KnowledgeItem` in status
``SUGGESTED`` with **non-empty** ``evidence_text`` (Requirements 6.2, 6.6) and
links it to a business entity resolved from the AI's suggested entities
(Requirement 6.7). It never auto-confirms — a suggestion always waits for an
explicit human decision (task 10.2).

Conventions shared with the other Core Engine services:

* **Organization scoping is mandatory.** Every lookup is filtered by
  ``organization_id``; a missing or cross-tenant row is indistinguishable and
  yields ``404`` (Requirement 2.3).
* **The service never commits on its own.** It ``add``/``flush``es within the
  caller's (route's) transaction, which commits on success and rolls back on
  error.

Provider access is injected as an ``extractor`` callable
``(content, context) -> KnowledgeOutput`` rather than a hard dependency on a
concrete provider. This lets the extract route (task 10.2) route the call
through :func:`app.dependencies.call_with_fallback` (so the LLM→mock failure
policy applies) while the service still owns item resolution, the safety gates,
entity linking, and persistence. When no extractor is supplied the deterministic
:class:`MockAIProvider` is used so the service is usable and testable standalone.
"""

from __future__ import annotations

from typing import Callable
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from app.core.models import (
    ActionItem,
    BusinessEntity,
    ClassificationResult,
    DecisionRecord,
    KnowledgeItem,
    Relevance,
    Sensitivity,
    SourceItem,
    SuggestionStatus,
)
from app.core.schemas import KnowledgeListFilter
from app.core.services.ai_provider import (
    EntityContext,
    EntitySuggestion,
    KnowledgeOutput,
    MockAIProvider,
)
from app.core.services.audit_service import AuditService
from app.dependencies import not_found, scope_select

#: Audit action type recorded when a knowledge item is confirmed (Requirement 7.1).
CONFIRM_KNOWLEDGE = "CONFIRM_KNOWLEDGE"

#: Audit action type recorded when a knowledge item is permanently deleted.
DELETE_KNOWLEDGE = "DELETE_KNOWLEDGE"

# An extractor turns a source item's content and its entity context into a
# structured :class:`KnowledgeOutput`. Routes wrap the provider call in
# ``call_with_fallback`` and pass the wrapped callable here.
Extractor = Callable[[str, EntityContext], KnowledgeOutput]

#: Relevance values whose content is excluded from the knowledge base entirely
#: (Requirement 6.3). Extraction is refused for a confirmed classification whose
#: relevance is any of these.
_EXCLUDED_RELEVANCE: frozenset[Relevance] = frozenset(
    {
        Relevance.PERSONAL,
        Relevance.IRRELEVANT,
        Relevance.SPAM,
        Relevance.SYSTEM_NOTIFICATION,
    }
)


def _conflict(detail: str) -> HTTPException:
    """Build the ``409 Conflict`` used for safety-gate refusals.

    Extraction refusals (privacy gate, sensitivity gate, and the missing
    confirmed-classification precondition) surface as ``409 Conflict`` with a
    clear reason string (design "Safety Refusals"). Raising an
    :class:`HTTPException` here keeps the route thin — it simply lets the
    exception propagate.
    """

    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


class KnowledgeService:
    """Extract evidence-backed knowledge behind privacy/sensitivity gates."""

    def __init__(self, db: Session, audit: AuditService | None = None) -> None:
        """Bind the service to a request-scoped session.

        Args:
            db: The request-scoped session. Its transaction is owned by the
                caller (the route); this service adds/flushes rows but never
                commits, so a refused or failed request rolls back cleanly with
                no partial :class:`KnowledgeItem`, and the confirm mutation and
                its audit row commit (or roll back) together atomically.
            audit: The :class:`AuditService` used to record confirmations in the
                same transaction. Defaults to a fresh instance bound to ``db``.
        """

        self.db = db
        self.audit = audit or AuditService(db)

    # -- Internal helpers ---------------------------------------------------

    def _get_item(self, org_id: UUID, item_id: UUID) -> SourceItem:
        """Resolve an org-scoped source item or raise ``404`` (Requirement 2.3)."""

        stmt = scope_select(select(SourceItem), SourceItem, org_id).where(
            SourceItem.id == item_id
        )
        item = self.db.execute(stmt).scalar_one_or_none()
        if item is None:
            raise not_found("Source item not found.")
        return item

    def _get_knowledge(self, org_id: UUID, knowledge_id: UUID) -> KnowledgeItem:
        """Resolve an org-scoped knowledge item or raise ``404`` (Requirement 2.3).

        A missing row or one belonging to another tenant is indistinguishable
        and yields ``404`` so the system never reveals another org's data.
        """

        stmt = scope_select(select(KnowledgeItem), KnowledgeItem, org_id).where(
            KnowledgeItem.id == knowledge_id
        )
        knowledge = self.db.execute(stmt).scalar_one_or_none()
        if knowledge is None:
            raise not_found("Knowledge item not found.")
        return knowledge

    def _confirmed_classification(self, item_id: UUID) -> ClassificationResult | None:
        """Return the item's ``CONFIRMED`` classification, if any.

        The privacy and sensitivity gates key off the *confirmed* classification
        (design "Key Workflow 3"), so extraction operates on the human-approved
        axes rather than a raw AI suggestion. At most one non-rejected result
        exists per item (Requirement 4.5), so at most one can be ``CONFIRMED``.
        """

        stmt = (
            select(ClassificationResult)
            .where(ClassificationResult.source_item_id == item_id)
            .where(ClassificationResult.status == SuggestionStatus.CONFIRMED)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    # -- Entity linking (Requirements 6.2, 6.7) -----------------------------

    def link_or_create_entity(
        self,
        org_id: UUID,
        suggestion: EntitySuggestion,
    ) -> BusinessEntity:
        """Link to an existing matching :class:`BusinessEntity` or create one.

        Looks up an org-scoped business entity matching the suggestion's
        ``name`` and ``entity_type``; when a match exists it is reused, otherwise
        a new entity is created (Requirement 6.7). The lookup and any creation
        are always scoped to ``org_id`` so entities never leak or link across
        tenants (Requirement 2.1). The row is flushed (id populated) but not
        committed — the caller's transaction commits it.

        Args:
            org_id: The tenant the entity belongs to.
            suggestion: The AI-proposed entity (``name`` + ``entity_type``).

        Returns:
            The matched or newly created :class:`BusinessEntity`.
        """

        stmt = (
            scope_select(select(BusinessEntity), BusinessEntity, org_id)
            .where(BusinessEntity.name == suggestion.name)
            .where(BusinessEntity.entity_type == suggestion.entity_type)
        )
        existing = self.db.execute(stmt).scalars().first()
        if existing is not None:
            return existing

        entity = BusinessEntity(
            organization_id=org_id,
            entity_type=suggestion.entity_type,
            name=suggestion.name,
        )
        self.db.add(entity)
        self.db.flush()
        return entity

    # -- Extract ------------------------------------------------------------

    def extract(
        self,
        org_id: UUID,
        item_id: UUID,
        extractor: Extractor | None = None,
        acknowledged: bool = False,
    ) -> KnowledgeItem:
        """Extract a ``SUGGESTED`` knowledge item behind the safety gates.

        Resolves the org-scoped source item (``404`` if missing/cross-tenant)
        and its **confirmed** classification, evaluates the two safety gates, and
        only then invokes the AI provider and persists the result:

        1. **Confirmed-classification precondition (Requirement 6.1).** A source
           item without a ``CONFIRMED`` classification cannot be processed;
           extraction is refused with ``409``.
        2. **Privacy gate (Requirement 6.3).** A confirmed ``relevance`` in
           ``{PERSONAL, IRRELEVANT, SPAM, SYSTEM_NOTIFICATION}`` is excluded from
           the knowledge base; extraction is refused with ``409`` and **nothing
           is persisted**.
        3. **Sensitivity gate (Requirements 6.4, 6.5).** A confirmed
           ``sensitivity`` of ``HIGHLY_SENSITIVE`` requires ``acknowledged=True``;
           without it extraction is refused with ``409`` and **nothing is
           persisted**. With the acknowledgment, extraction proceeds.

        On success it invokes ``extractor(content, context)`` (Requirement 6.1),
        links/creates the business entity from the first suggested entity
        (Requirement 6.7), and persists exactly one :class:`KnowledgeItem` in
        status ``SUGGESTED`` with non-empty ``evidence_text`` (Requirements 6.2,
        6.6). It never auto-confirms.

        Args:
            org_id: The tenant the item must belong to.
            item_id: The source item to extract knowledge from.
            extractor: Optional ``(content, context) -> KnowledgeOutput`` callable
                (typically wrapped in :func:`app.dependencies.call_with_fallback`
                by the route). When omitted, the deterministic
                :class:`MockAIProvider` is used.
            acknowledged: Explicit acknowledgment for highly-sensitive content.

        Returns:
            The persisted (flushed) ``SUGGESTED`` :class:`KnowledgeItem`.

        Raises:
            HTTPException: ``404`` if the item does not exist for the
                organization; ``409`` if there is no confirmed classification or
                a safety gate refuses extraction.
        """

        item = self._get_item(org_id, item_id)

        classification = self._confirmed_classification(item.id)
        if classification is None:
            # Requirement 6.1: extraction operates on a CONFIRMED classification.
            raise _conflict(
                "Source item has no confirmed classification to extract from."
            )

        # Privacy gate (Requirement 6.3) — refuse before any AI call/persistence.
        if classification.relevance in _EXCLUDED_RELEVANCE:
            raise _conflict(
                f"Relevance {classification.relevance.value} is excluded from the "
                "knowledge base; extraction refused."
            )

        # Sensitivity gate (Requirements 6.4, 6.5).
        if classification.sensitivity == Sensitivity.HIGHLY_SENSITIVE and not acknowledged:
            raise _conflict(
                "Source item is highly sensitive; explicit acknowledgment is "
                "required before extraction."
            )

        # Gates passed — invoke the provider and persist a SUGGESTED item.
        context = EntityContext(organization_id=org_id)
        run = extractor or MockAIProvider().extract_knowledge
        output = run(item.content, context)

        entity: BusinessEntity | None = None
        if output.suggested_entities:
            # Link/create from the highest-signal (first) suggested entity.
            entity = self.link_or_create_entity(org_id, output.suggested_entities[0])

        # Requirement 6.6: evidence_text must be non-empty. Fall back to the
        # source content (guaranteed non-empty by the create-payload validation)
        # if the provider returned nothing usable.
        evidence_text = (output.evidence_text or "").strip() or (item.content or "").strip()

        knowledge = KnowledgeItem(
            organization_id=org_id,
            business_entity_id=entity.id if entity is not None else None,
            source_item_id=item.id,
            summary=output.summary,
            key_points=list(output.key_points),
            evidence_text=evidence_text,
            knowledge_type=output.knowledge_type,
            status=SuggestionStatus.SUGGESTED,
        )
        self.db.add(knowledge)
        self.db.flush()
        return knowledge

    # -- Confirm (Requirements 7.1, 13.1) -----------------------------------

    def confirm(
        self,
        org_id: UUID,
        knowledge_id: UUID,
        actor_id: UUID,
    ) -> KnowledgeItem:
        """Confirm a suggested knowledge item in one transaction.

        Resolves the org-scoped knowledge item (``404`` if missing/cross-tenant)
        and, atomically in the caller's transaction (Requirement 7.1):

        * sets the item status to ``CONFIRMED`` (Requirement 7.1);
        * records exactly one ``CONFIRM_KNOWLEDGE`` audit row via
          :class:`AuditService` (Requirements 7.1, 13.1).

        A ``CONFIRMED`` knowledge item must carry non-empty ``evidence_text``
        (Requirements 6.6, 7.5); ``extract`` already guarantees this, so no
        knowledge item can reach ``CONFIRMED`` without evidence.

        Args:
            org_id: The tenant the item must belong to.
            knowledge_id: The knowledge item to confirm.
            actor_id: The user confirming the item (audit actor).

        Returns:
            The updated (flushed) ``CONFIRMED`` :class:`KnowledgeItem`.

        Raises:
            HTTPException: ``404`` if the item does not exist for the
                organization.
        """

        knowledge = self._get_knowledge(org_id, knowledge_id)

        knowledge.status = SuggestionStatus.CONFIRMED
        self.db.add(knowledge)

        # Requirement 7.1 / 13.1: exactly one audit row in this transaction.
        self.audit.record(
            org_id=org_id,
            actor_id=actor_id,
            action_type=CONFIRM_KNOWLEDGE,
            target_type="KnowledgeItem",
            target_id=knowledge.id,
            detail={"business_entity_id": str(knowledge.business_entity_id)
                    if knowledge.business_entity_id is not None else None},
        )

        self.db.flush()
        return knowledge

    # -- Reject (Requirement 7.2) -------------------------------------------

    def reject(
        self,
        org_id: UUID,
        knowledge_id: UUID,
        actor_id: UUID,
    ) -> KnowledgeItem:
        """Reject a suggested knowledge item, retaining it as a negative signal.

        Resolves the org-scoped knowledge item (``404`` if missing/cross-tenant)
        and sets its status to ``REJECTED``. The row is **retained**, not
        deleted, so it stays available as a negative signal (Requirement 7.2).

        Args:
            org_id: The tenant the item must belong to.
            knowledge_id: The knowledge item to reject.
            actor_id: The user rejecting the item.

        Returns:
            The updated (flushed) ``REJECTED`` :class:`KnowledgeItem`.

        Raises:
            HTTPException: ``404`` if the item does not exist for the
                organization.
        """

        knowledge = self._get_knowledge(org_id, knowledge_id)

        knowledge.status = SuggestionStatus.REJECTED
        self.db.add(knowledge)
        self.db.flush()
        return knowledge

    # -- Delete -------------------------------------------------------------

    def delete(self, org_id: UUID, knowledge_id: UUID, actor_id: UUID) -> None:
        """Permanently delete a knowledge item, detaching dependent actions.

        Resolves the org-scoped knowledge item (``404`` if missing/cross-tenant)
        and, in one transaction, NULLs the ``knowledge_item_id`` FK on any
        :class:`ActionItem` referencing it (the derived action is **retained**,
        just detached), deletes the knowledge item, and records exactly one
        ``DELETE_KNOWLEDGE`` audit row.

        Args:
            org_id: The tenant the item must belong to.
            knowledge_id: The knowledge item to delete.
            actor_id: The user performing the delete (audit actor).

        Raises:
            HTTPException: ``404`` if the item does not exist for the org.
        """

        knowledge = self._get_knowledge(org_id, knowledge_id)

        self.db.execute(
            sa_update(ActionItem)
            .where(
                ActionItem.organization_id == org_id,
                ActionItem.knowledge_item_id == knowledge.id,
            )
            .values(knowledge_item_id=None)
        )

        deleted_id = knowledge.id
        self.db.delete(knowledge)
        self.db.flush()

        self.audit.record(
            org_id=org_id,
            actor_id=actor_id,
            action_type=DELETE_KNOWLEDGE,
            target_type="KnowledgeItem",
            target_id=deleted_id,
            detail={},
        )
        self.db.flush()

    # -- List (Requirement 7.3) ---------------------------------------------

    def list_for_entity(
        self,
        org_id: UUID,
        filters: KnowledgeListFilter | None = None,
    ) -> list[KnowledgeItem]:
        """Return knowledge items for an organization, newest first.

        Results are always constrained to ``org_id`` (Requirement 7.3) so one
        tenant can never see another's knowledge. Optional ``filters`` narrow
        the result by ``business_entity_id`` and/or ``status``.

        Args:
            org_id: The tenant whose knowledge items to return.
            filters: Optional :class:`KnowledgeListFilter`; when omitted, all of
                the organization's items are returned.

        Returns:
            The matching :class:`KnowledgeItem` rows ordered by ``created_at``
            descending (ties broken by ``id`` for a stable order).
        """

        filters = filters or KnowledgeListFilter()

        stmt = scope_select(select(KnowledgeItem), KnowledgeItem, org_id)
        if filters.business_entity_id is not None:
            stmt = stmt.where(
                KnowledgeItem.business_entity_id == filters.business_entity_id
            )
        if filters.status is not None:
            stmt = stmt.where(KnowledgeItem.status == filters.status)

        stmt = stmt.order_by(KnowledgeItem.created_at.desc(), KnowledgeItem.id.desc())
        return list(self.db.execute(stmt).scalars().all())

    # -- Detail with linked actions/decisions (Requirements 7.4, 7.5) -------

    def get_detail(
        self,
        org_id: UUID,
        knowledge_id: UUID,
    ) -> tuple[KnowledgeItem, list[ActionItem], list[DecisionRecord]]:
        """Return a knowledge item with its linked actions and decisions.

        Resolves the org-scoped knowledge item (``404`` if missing/cross-tenant)
        together with the actions and decisions connected to it (Requirement
        7.4). "Linked" means, for actions, either an :class:`ActionItem` whose
        ``knowledge_item_id`` points directly at this item **or** one that shares
        the same ``business_entity_id``; for decisions, a
        :class:`DecisionRecord` that shares the same ``business_entity_id``. All
        lookups are org-scoped so nothing links across tenants (Requirement
        2.1). The knowledge item is returned with its ``evidence_text`` intact
        (Requirement 7.5).

        Args:
            org_id: The tenant the item must belong to.
            knowledge_id: The knowledge item whose detail to assemble.

        Returns:
            A ``(knowledge_item, linked_actions, linked_decisions)`` tuple.

        Raises:
            HTTPException: ``404`` if the item does not exist for the
                organization.
        """

        knowledge = self._get_knowledge(org_id, knowledge_id)

        entity_id = knowledge.business_entity_id

        # Linked actions: directly via knowledge_item_id, or via the shared
        # business entity when this knowledge is connected to one.
        action_stmt = scope_select(select(ActionItem), ActionItem, org_id)
        if entity_id is not None:
            action_stmt = action_stmt.where(
                or_(
                    ActionItem.knowledge_item_id == knowledge.id,
                    ActionItem.business_entity_id == entity_id,
                )
            )
        else:
            action_stmt = action_stmt.where(
                ActionItem.knowledge_item_id == knowledge.id
            )
        action_stmt = action_stmt.order_by(
            ActionItem.created_at.desc(), ActionItem.id.desc()
        )
        actions = list(self.db.execute(action_stmt).scalars().all())

        # Linked decisions: share the same business entity (only when linked).
        decisions: list[DecisionRecord] = []
        if entity_id is not None:
            decision_stmt = (
                scope_select(select(DecisionRecord), DecisionRecord, org_id)
                .where(DecisionRecord.business_entity_id == entity_id)
                .order_by(DecisionRecord.decided_at.desc(), DecisionRecord.id.desc())
            )
            decisions = list(self.db.execute(decision_stmt).scalars().all())

        return knowledge, actions, decisions


__all__ = [
    "KnowledgeService",
    "Extractor",
    "CONFIRM_KNOWLEDGE",
    "DELETE_KNOWLEDGE",
]
