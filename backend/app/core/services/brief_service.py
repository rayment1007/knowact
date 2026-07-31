"""Brief service (Requirement 10).

The :class:`BriefService` owns the *Recommend + Learn feedback* step of the
pipeline: it assembles the organization's **persisted, human-confirmed context**
and turns it into a structured brief that is persisted as a
:class:`~app.core.models.Brief`.

The **Learn loop** is the important design constraint here. A brief is *only*
grounded in memory a human has validated:

* **Confirmed knowledge** — :class:`~app.core.models.KnowledgeItem` rows in
  status ``CONFIRMED`` (never ``SUGGESTED`` / ``REJECTED``).
* **Open actions** — :class:`~app.core.models.ActionItem` rows that are still
  active (``OPEN`` or ``IN_PROGRESS``), i.e. not ``DONE`` / ``CANCELLED``.
* **Recent decisions** — the newest :class:`~app.core.models.DecisionRecord`
  rows (a decision record is an immutable record of a decision already made).

Assembling context solely from that confirmed memory (Requirement 10.2) is the
mechanism by which the brief reflects reality rather than un-vetted AI output:
as more knowledge/actions/decisions are confirmed, later briefs improve.

Two operations are exposed (design "BriefService"):

* :meth:`daily_brief` — the organization-wide brief. Assembles an
  :class:`~app.core.services.ai_provider.OrgContext`, invokes the provider's
  ``generate_daily_brief``, and persists a ``DAILY`` :class:`Brief` carrying a
  headline, priorities, recommended actions, follow-ups, and risks — each line
  referencing its source evidence (Requirements 10.1, 10.3, 10.4).
* :meth:`entity_brief` — a brief scoped to one :class:`BusinessEntity`. Resolves
  the org-scoped entity (``404`` if missing/cross-tenant), assembles an
  :class:`~app.core.services.ai_provider.EntityContext` from *that entity's*
  confirmed knowledge and open actions (plus its recent decisions), invokes
  ``generate_entity_brief``, and persists an ``ENTITY`` :class:`Brief` whose
  ``scope_ref_id`` is the entity (Requirements 10.4, 10.5).

Conventions shared with the other Core Engine services:

* **Organization scoping is mandatory.** Every lookup is filtered by
  ``organization_id``; a missing or cross-tenant row is indistinguishable and
  yields ``404`` (Requirement 2.3).
* **The service never commits on its own.** It ``add``/``flush``es within the
  caller's (route's) transaction, which commits on success and rolls back on
  error.

Provider access is injected as a ``generator`` callable rather than a hard
dependency on a concrete provider. This lets the brief route (task 12.2) route
the call through :func:`app.dependencies.call_with_fallback` (so the LLM→mock
failure policy applies) while the service still owns context assembly and
persistence. When no generator is supplied the deterministic
:class:`MockAIProvider` is used so the service is usable and testable standalone.
"""

from __future__ import annotations

from typing import Callable, Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import (
    ActionItem,
    ActionStatus,
    BusinessEntity,
    DecisionRecord,
    KnowledgeItem,
    Brief,
    SuggestionStatus,
)
from app.core.services.ai_provider import (
    BriefLine,
    ContextActionItem,
    ContextDecision,
    ContextKnowledgeItem,
    DailyBriefOutput,
    EntityBriefOutput,
    EntityContext,
    MockAIProvider,
    OrgContext,
)
from app.dependencies import not_found, scope_select

# A daily-brief generator turns an assembled :class:`OrgContext` into a
# structured :class:`DailyBriefOutput`. Routes wrap the provider call in
# ``call_with_fallback`` and pass the wrapped callable here.
DailyBriefGenerator = Callable[[OrgContext], DailyBriefOutput]

# An entity-brief generator turns an assembled :class:`EntityContext` into a
# structured :class:`EntityBriefOutput`.
EntityBriefGenerator = Callable[[EntityContext], EntityBriefOutput]

#: Action statuses considered "open" (still active) for brief context. Actions
#: that are ``DONE`` or ``CANCELLED`` are closed and never feed a brief.
_OPEN_ACTION_STATUSES: frozenset[ActionStatus] = frozenset(
    {ActionStatus.OPEN, ActionStatus.IN_PROGRESS}
)

#: How many of the newest decisions to include as "recent decisions" context.
_RECENT_DECISIONS_LIMIT = 10

#: Max length of a human-readable evidence snippet surfaced on a brief line.
_EVIDENCE_SNIPPET_LIMIT = 200


def _truncate(text: str, limit: int = _EVIDENCE_SNIPPET_LIMIT) -> str:
    """Trim ``text`` to ``limit`` chars with an ellipsis, collapsing whitespace."""

    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 1)].rstrip() + "…"


def _looks_like_uuid(value: str) -> bool:
    """Return ``True`` if ``value`` parses as a UUID (i.e. is an opaque id)."""

    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


class BriefService:
    """Assemble confirmed context and produce daily / entity briefs (Req 10)."""

    def __init__(self, db: Session) -> None:
        """Bind the service to a request-scoped session.

        Args:
            db: The request-scoped session. Its transaction is owned by the
                caller (the route); this service adds/flushes rows but never
                commits, so a failed request rolls back cleanly with no partial
                :class:`Brief`.
        """

        self.db = db

    # -- Context assembly (the Learn loop, Requirement 10.2) ----------------

    def _confirmed_knowledge(
        self, org_id: UUID, entity_id: UUID | None = None
    ) -> list[KnowledgeItem]:
        """Return the org's ``CONFIRMED`` knowledge items, newest first.

        Only ``CONFIRMED`` knowledge feeds a brief (Requirement 10.2); suggested
        and rejected items are excluded. When ``entity_id`` is supplied the
        result is further narrowed to that business entity (Requirement 10.5).
        """

        stmt = scope_select(select(KnowledgeItem), KnowledgeItem, org_id).where(
            KnowledgeItem.status == SuggestionStatus.CONFIRMED
        )
        if entity_id is not None:
            stmt = stmt.where(KnowledgeItem.business_entity_id == entity_id)
        stmt = stmt.order_by(KnowledgeItem.created_at.desc(), KnowledgeItem.id.desc())
        return list(self.db.execute(stmt).scalars().all())

    def _open_actions(
        self, org_id: UUID, entity_id: UUID | None = None
    ) -> list[ActionItem]:
        """Return the org's open (active) action items, newest first.

        Only actions that are still ``OPEN`` or ``IN_PROGRESS`` are included;
        ``DONE`` / ``CANCELLED`` actions are closed and never feed a brief
        (Requirements 10.1, 10.5). Optionally narrowed to one business entity.
        """

        stmt = scope_select(select(ActionItem), ActionItem, org_id).where(
            ActionItem.status.in_(_OPEN_ACTION_STATUSES)
        )
        if entity_id is not None:
            stmt = stmt.where(ActionItem.business_entity_id == entity_id)
        stmt = stmt.order_by(ActionItem.created_at.desc(), ActionItem.id.desc())
        return list(self.db.execute(stmt).scalars().all())

    def _recent_decisions(
        self, org_id: UUID, entity_id: UUID | None = None
    ) -> list[DecisionRecord]:
        """Return the org's most recent decision records, newest first.

        Decision records are immutable records of decisions already made, so all
        of them are "confirmed" by construction; the newest
        ``_RECENT_DECISIONS_LIMIT`` provide recency-bounded context
        (Requirement 10.1). Optionally narrowed to one business entity.
        """

        stmt = scope_select(select(DecisionRecord), DecisionRecord, org_id)
        if entity_id is not None:
            stmt = stmt.where(DecisionRecord.business_entity_id == entity_id)
        stmt = stmt.order_by(
            DecisionRecord.decided_at.desc(), DecisionRecord.id.desc()
        ).limit(_RECENT_DECISIONS_LIMIT)
        return list(self.db.execute(stmt).scalars().all())

    @staticmethod
    def _to_context_knowledge(item: KnowledgeItem) -> ContextKnowledgeItem:
        """Project a persisted knowledge item into provider context."""

        return ContextKnowledgeItem(
            id=item.id,
            summary=item.summary,
            key_points=list(item.key_points or []),
            evidence_text=item.evidence_text,
            knowledge_type=item.knowledge_type,
            business_entity_id=item.business_entity_id,
        )

    @staticmethod
    def _to_context_action(action: ActionItem) -> ContextActionItem:
        """Project a persisted action item into provider context."""

        return ContextActionItem(
            id=action.id,
            title=action.title,
            status=action.status.value,
            due_date=action.due_date,
            business_entity_id=action.business_entity_id,
        )

    @staticmethod
    def _to_context_decision(decision: DecisionRecord) -> ContextDecision:
        """Project a persisted decision record into provider context."""

        return ContextDecision(
            id=decision.id,
            title=decision.title,
            decision=decision.decision,
            rationale=decision.rationale,
            evidence_text=decision.evidence_text,
            business_entity_id=decision.business_entity_id,
            decided_at=decision.decided_at,
        )

    def _build_org_context(self, org_id: UUID) -> OrgContext:
        """Assemble the organization-wide context from confirmed memory.

        Gathers *only* confirmed knowledge, open actions, and recent decisions
        for the tenant (Requirements 10.1, 10.2) — the Learn loop's confirmed
        context that grounds the daily brief.
        """

        return OrgContext(
            organization_id=org_id,
            confirmed_knowledge=[
                self._to_context_knowledge(k) for k in self._confirmed_knowledge(org_id)
            ],
            open_actions=[
                self._to_context_action(a) for a in self._open_actions(org_id)
            ],
            recent_decisions=[
                self._to_context_decision(d) for d in self._recent_decisions(org_id)
            ],
        )

    def _build_entity_context(
        self, org_id: UUID, entity: BusinessEntity
    ) -> EntityContext:
        """Assemble a single entity's context from its confirmed memory.

        Gathers the entity's confirmed knowledge, open actions, and recent
        decisions (Requirement 10.5), all org-scoped, so an entity brief is
        grounded only in that entity's human-validated context.
        """

        return EntityContext(
            organization_id=org_id,
            entity_id=entity.id,
            entity_type=entity.entity_type,
            entity_name=entity.name,
            description=entity.description,
            confirmed_knowledge=[
                self._to_context_knowledge(k)
                for k in self._confirmed_knowledge(org_id, entity.id)
            ],
            open_actions=[
                self._to_context_action(a)
                for a in self._open_actions(org_id, entity.id)
            ],
            recent_decisions=[
                self._to_context_decision(d)
                for d in self._recent_decisions(org_id, entity.id)
            ],
        )

    # -- Human-readable evidence resolution (Requirement 10.4) --------------

    def _evidence_snippets(
        self, org_id: UUID, entity_id: UUID | None = None
    ) -> dict[str, str]:
        """Map each confirmed-context id to a short human-readable snippet.

        A brief line's ``evidence_ref`` is often a bare knowledge/action/decision
        id (opaque to a human). This resolves those ids back to the readable text
        that produced them so the UI can show evidence instead of a raw id
        (Requirement 10.4). Built from the same confirmed context that grounds
        the brief, so nothing outside that context is ever surfaced.
        """

        snippets: dict[str, str] = {}
        for k in self._confirmed_knowledge(org_id, entity_id):
            text = (k.evidence_text or "").strip() or (k.summary or "").strip()
            if text:
                snippets[str(k.id)] = _truncate(text)
        for a in self._open_actions(org_id, entity_id):
            if a.title:
                snippets[str(a.id)] = _truncate(a.title)
        for d in self._recent_decisions(org_id, entity_id):
            text = (d.decision or "").strip() or (d.title or "").strip()
            if text:
                snippets[str(d.id)] = _truncate(text)
        return snippets

    @staticmethod
    def _humanize_lines(
        lines: Iterable[BriefLine], snippets: dict[str, str]
    ) -> None:
        """Populate each line's ``evidence_text`` with readable evidence.

        Resolves an id-shaped ``evidence_ref`` via ``snippets``; leaves already
        human-readable free-text refs as-is; and, so no bare id is ever shown,
        surfaces no ``evidence_text`` for an unresolvable id (Requirement 10.4).
        ``evidence_ref`` itself is preserved for traceability.
        """

        for line in lines:
            ref = (line.evidence_ref or "").strip()
            if not ref:
                continue
            snippet = snippets.get(ref)
            if snippet:
                line.evidence_text = snippet
            elif _looks_like_uuid(ref):
                # A bare id with no resolvable snippet — never surface it as text.
                line.evidence_text = None
            else:
                # ``evidence_ref`` is already human-readable free text.
                line.evidence_text = ref

    # -- Entity resolution --------------------------------------------------

    def _get_entity(self, org_id: UUID, entity_id: UUID) -> BusinessEntity:
        """Resolve an org-scoped business entity or raise ``404``.

        A missing row or one belonging to another tenant is indistinguishable
        and yields ``404`` so the system never reveals another org's data
        (Requirement 2.3).
        """

        stmt = scope_select(select(BusinessEntity), BusinessEntity, org_id).where(
            BusinessEntity.id == entity_id
        )
        entity = self.db.execute(stmt).scalar_one_or_none()
        if entity is None:
            raise not_found("Business entity not found.")
        return entity

    # -- Daily brief (Requirements 10.1, 10.2, 10.3, 10.4) ------------------

    def daily_brief(
        self,
        org_id: UUID,
        user_id: UUID,
        generator: DailyBriefGenerator | None = None,
    ) -> Brief:
        """Produce and persist the organization-wide daily brief.

        Assembles the org context from *only* confirmed knowledge, open actions,
        and recent decisions (Requirements 10.1, 10.2), invokes the provider's
        ``generate_daily_brief`` (Requirement 10.1), and persists a ``DAILY``
        :class:`Brief` whose ``content`` carries the headline, priorities,
        recommended actions, follow-ups, and risks — each line referencing its
        source evidence (Requirements 10.3, 10.4).

        Args:
            org_id: The tenant to build the brief for.
            user_id: The user the brief is generated for (``generated_for``).
            generator: Optional ``(OrgContext) -> DailyBriefOutput`` callable
                (typically wrapped in :func:`app.dependencies.call_with_fallback`
                by the route). When omitted, the deterministic
                :class:`MockAIProvider` is used.

        Returns:
            The persisted (flushed) ``DAILY`` :class:`Brief`.
        """

        context = self._build_org_context(org_id)
        run = generator or MockAIProvider().generate_daily_brief
        output = run(context)

        # Resolve id-shaped evidence references into readable snippets so the UI
        # never shows a bare id (Requirement 10.4).
        snippets = self._evidence_snippets(org_id)
        self._humanize_lines(output.priorities, snippets)
        self._humanize_lines(output.risks, snippets)

        brief = Brief(
            organization_id=org_id,
            scope="DAILY",
            scope_ref_id=None,
            generated_for=user_id,
            content=output.model_dump(mode="json"),
        )
        self.db.add(brief)
        self.db.flush()
        return brief

    # -- Entity brief (Requirements 10.4, 10.5) -----------------------------

    def entity_brief(
        self,
        org_id: UUID,
        entity_id: UUID,
        user_id: UUID,
        generator: EntityBriefGenerator | None = None,
    ) -> Brief:
        """Produce and persist a brief scoped to a single business entity.

        Resolves the org-scoped entity (``404`` if missing/cross-tenant),
        assembles its context from *that entity's* confirmed knowledge, open
        actions, and recent decisions (Requirement 10.5), invokes the provider's
        ``generate_entity_brief``, and persists an ``ENTITY`` :class:`Brief`
        whose ``scope_ref_id`` is the entity and whose ``content`` references its
        source evidence (Requirement 10.4).

        Args:
            org_id: The tenant the entity must belong to.
            entity_id: The business entity to build the brief about.
            user_id: The user the brief is generated for (``generated_for``).
            generator: Optional ``(EntityContext) -> EntityBriefOutput`` callable
                (typically wrapped in :func:`app.dependencies.call_with_fallback`
                by the route). When omitted, the deterministic
                :class:`MockAIProvider` is used.

        Returns:
            The persisted (flushed) ``ENTITY`` :class:`Brief`.

        Raises:
            HTTPException: ``404`` if the entity does not exist for the
                organization.
        """

        entity = self._get_entity(org_id, entity_id)

        context = self._build_entity_context(org_id, entity)
        run = generator or MockAIProvider().generate_entity_brief
        output = run(context)

        # Resolve id-shaped evidence references into readable snippets so the UI
        # never shows a bare id (Requirement 10.4).
        snippets = self._evidence_snippets(org_id, entity.id)
        self._humanize_lines(output.recent_knowledge, snippets)
        self._humanize_lines(output.open_actions, snippets)

        brief = Brief(
            organization_id=org_id,
            scope="ENTITY",
            scope_ref_id=entity.id,
            generated_for=user_id,
            content=output.model_dump(mode="json"),
        )
        self.db.add(brief)
        self.db.flush()
        return brief


__all__ = ["BriefService", "DailyBriefGenerator", "EntityBriefGenerator"]
