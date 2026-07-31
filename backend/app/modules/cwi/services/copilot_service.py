"""Enterprise Copilot grounded-RAG service (M6.5, Requirements 30, 31).

The Copilot is **not a generic chatbot**. It is a grounded reader over the
organization's own *confirmed* records and permission-filtered content, bounded
by an approved tool allow-list and the human-in-the-loop model. This service
implements the exact per-request pipeline from the design:

    authenticate (done by the route dependency) → resolve org + permissions →
    determine intent (ASK / DRAFT / ACT) → retrieve structured records +
    semantic chunks under org/permission/sensitivity filters → build a BOUNDED
    evidence context → call the LLM (via the injected :class:`AIProvider` and
    :func:`app.dependencies.call_with_fallback`) with structured outputs →
    validate with Pydantic AND verify every citation ``source_id`` is present in
    the bounded evidence set → return an answer + citations OR a ``SUGGESTED``
    artifact.

Security / grounding invariants (Requirements 30.1, 30.2, 30.7, 31.2-31.5):

* **Approved tool allow-list only.** Evidence is gathered exclusively through
  the ASK-tier tools in :data:`COPILOT_TOOL_ALLOWLIST`; there is no code path to
  execute arbitrary SQL or bypass org scoping. Every query is org-scoped, and
  document retrieval is permission/sensitivity filtered by
  :class:`RetrievalService`.
* **No fabricated citations.** The backend holds the full citation record for
  every evidence item and rebuilds each citation from the ``source_id`` the LLM
  returned. A ``source_id`` that is not in the bounded evidence set is dropped —
  the LLM can only *reference* evidence, never invent it.
* **Honest insufficiency.** When the bounded evidence set is empty, the service
  refuses to guess: it returns ``insufficient_evidence=True`` and a message that
  it cannot find enough confirmed evidence — without calling the model.
* **No mutation in ``ask``.** DRAFT/ACT intents only ever produce a ``SUGGESTED``
  artifact. Mutations happen exclusively in :meth:`confirm`, which applies the
  change and writes exactly one :class:`AuditLog` row in the same transaction.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.models import (
    ActionItem,
    ActionStatus,
    BusinessEntity,
    BusinessEntityType,
    DecisionRecord,
    KnowledgeItem,
    SourceType,
    SuggestionStatus,
)
from app.core.schemas import ActionCreate
from app.core.services.ai_provider import (
    AIProvider,
    CopilotAnswerOutput,
    CopilotEvidenceItem,
    CopilotQueryContext,
)
from app.core.services.action_service import ActionService
from app.core.services.audit_service import AuditService
from app.dependencies import call_with_fallback, scope_select
from app.modules.cwi.models import (
    CalendarEventLink,
    CalendarSyncStatus,
    CopilotAnswerLog,
    EmailMessageRecord,
)
from app.modules.cwi.services.embedding import EmbeddingProvider
from app.modules.cwi.services.retrieval_service import (
    RetrievalFilters,
    RetrievalService,
)

# ---------------------------------------------------------------------------
# Intents, tool tiers, and the approved tool allow-list
# ---------------------------------------------------------------------------


class CopilotIntent(str, enum.Enum):
    """The three capability tiers a Copilot request can resolve to."""

    ASK = "ASK"
    DRAFT = "DRAFT"
    ACT = "ACT"


class CopilotToolTier(str, enum.Enum):
    """Capability tier of an approved tool (read / draft / propose-mutation)."""

    ASK = "ASK"
    DRAFT = "DRAFT"
    ACT = "ACT"


#: The Copilot may call **only** these tools (design *Approved Tool Allow-List*).
#: ASK tools are read-only grounded reads; DRAFT/ACT tools produce ``SUGGESTED``
#: artifacts and never mutate. Anything not in this map is forbidden — there is
#: deliberately no tool for arbitrary SQL, scope bypass, or direct mutation.
COPILOT_TOOL_ALLOWLIST: dict[str, CopilotToolTier] = {
    "search_confirmed_knowledge": CopilotToolTier.ASK,
    "search_email_sources": CopilotToolTier.ASK,
    "search_document_chunks": CopilotToolTier.ASK,
    "list_open_actions": CopilotToolTier.ASK,
    "list_recent_decisions": CopilotToolTier.ASK,
    "get_client_context": CopilotToolTier.ASK,
    "get_upcoming_calendar_events": CopilotToolTier.ASK,
    "draft_email": CopilotToolTier.DRAFT,
    "propose_action_item": CopilotToolTier.ACT,
    "propose_calendar_event": CopilotToolTier.ACT,
    "propose_entity_link": CopilotToolTier.ACT,
}

#: Citation source-type labels (design *Citation Contract*).
SOURCE_KNOWLEDGE = "KNOWLEDGE"
SOURCE_EMAIL = "EMAIL"
SOURCE_DOCUMENT = "DOCUMENT"
SOURCE_ACTION = "ACTION"
SOURCE_DECISION = "DECISION"
SOURCE_CLIENT = "CLIENT"
SOURCE_CALENDAR = "CALENDAR"

#: The standard, honest message returned when evidence is insufficient.
INSUFFICIENT_EVIDENCE_MESSAGE = (
    "I can't find enough confirmed evidence in your organization's data to "
    "answer that. Try confirming related knowledge, or ask about something "
    "already captured."
)

#: Audit action type recorded when a Copilot-proposed artifact is confirmed.
CONFIRM_COPILOT_ACTION = "CREATE_ACTION"

# Per-category caps keep the evidence context BOUNDED (Requirement 31.1); the
# model never sees every historical record.
_MAX_PER_CATEGORY = 5
_MAX_CHUNKS = 5
_EXCERPT_MAX = 300

# Keyword hints for lightweight intent detection when the caller does not pass
# an explicit intent.
_DRAFT_HINTS = ("draft", "compose", "write an email", "write a reply", "reply to")
_ACT_HINTS = (
    "create a task",
    "add a task",
    "remind me",
    "schedule",
    "add to calendar",
    "create an action",
    "propose a task",
    "link this",
)


@dataclass
class _Evidence:
    """A backend-held citation record for one bounded evidence item.

    The service keeps the *full* citation fields here and hands the LLM only a
    slim :class:`CopilotEvidenceItem`. Citations returned by the model are
    rebuilt from this record by ``source_id``, so the model can never fabricate
    a citation's title/excerpt/deep-link.
    """

    source_type: str
    source_id: UUID
    title: str
    evidence_excerpt: str
    timestamp: datetime | None
    deep_link: str

    def to_provider_item(self) -> CopilotEvidenceItem:
        return CopilotEvidenceItem(
            source_type=self.source_type,
            source_id=self.source_id,
            title=self.title,
            excerpt=self.evidence_excerpt,
            timestamp=self.timestamp,
        )


def _excerpt(text: str | None) -> str:
    """Trim and bound an excerpt for a citation."""

    clean = " ".join((text or "").split())
    if len(clean) <= _EXCERPT_MAX:
        return clean
    return clean[: _EXCERPT_MAX - 1].rstrip() + "\u2026"


class CopilotService:
    """Grounded, tool-bounded Enterprise Copilot (Requirements 30, 31)."""

    def __init__(
        self,
        db: Session,
        ai_provider: AIProvider,
        embedding_provider: EmbeddingProvider,
        settings: Settings | None = None,
        audit: AuditService | None = None,
    ) -> None:
        """Bind the service to the request transaction and injected providers.

        Args:
            db: Request-scoped session (transaction owned by the caller/route).
            ai_provider: The resolved :class:`AIProvider` (mock or LLM). Tests
                inject a fake-transport ``LLMProvider`` or the deterministic
                ``MockAIProvider`` — never a real network client.
            embedding_provider: The injected embedding provider used by
                :class:`RetrievalService` for semantic chunk retrieval.
            settings: Application settings (for the provider fallback policy).
            audit: The :class:`AuditService` for confirm-time audit writes.
        """

        self.db = db
        self.ai_provider = ai_provider
        self.settings = settings or get_settings()
        self.audit = audit or AuditService(db)
        self.actions = ActionService(db, self.audit)
        self.retrieval = RetrievalService(db, embedding_provider)

    # -- Intent -------------------------------------------------------------

    def _determine_intent(
        self, question: str, explicit: str | None
    ) -> CopilotIntent:
        """Resolve the request intent (explicit wins, else keyword heuristic)."""

        if explicit:
            try:
                return CopilotIntent(explicit.upper())
            except ValueError:
                pass
        text = (question or "").lower()
        if any(hint in text for hint in _DRAFT_HINTS):
            return CopilotIntent.DRAFT
        if any(hint in text for hint in _ACT_HINTS):
            return CopilotIntent.ACT
        return CopilotIntent.ASK

    # -- ASK-tier tools (read-only, org/permission scoped) ------------------

    def _assert_ask_tool(self, name: str) -> None:
        """Guard: only ASK-tier allow-list tools may gather evidence."""

        tier = COPILOT_TOOL_ALLOWLIST.get(name)
        if tier is not CopilotToolTier.ASK:  # pragma: no cover - defensive
            raise RuntimeError(f"Tool {name!r} is not an approved ASK tool.")

    def _search_confirmed_knowledge(self, org_id: UUID) -> list[_Evidence]:
        self._assert_ask_tool("search_confirmed_knowledge")
        stmt = (
            scope_select(select(KnowledgeItem), KnowledgeItem, org_id)
            .where(KnowledgeItem.status == SuggestionStatus.CONFIRMED)
            .order_by(KnowledgeItem.created_at.desc(), KnowledgeItem.id.desc())
            .limit(_MAX_PER_CATEGORY)
        )
        rows = self.db.execute(stmt).scalars().all()
        return [
            _Evidence(
                source_type=SOURCE_KNOWLEDGE,
                source_id=row.id,
                title=_excerpt(row.summary)[:120] or "Knowledge",
                evidence_excerpt=_excerpt(row.evidence_text or row.summary),
                timestamp=row.created_at,
                deep_link=f"/knowledge/{row.id}",
            )
            for row in rows
        ]

    def _search_email_sources(self, org_id: UUID) -> list[_Evidence]:
        self._assert_ask_tool("search_email_sources")
        stmt = (
            scope_select(select(EmailMessageRecord), EmailMessageRecord, org_id)
            .order_by(
                EmailMessageRecord.received_at.desc(),
                EmailMessageRecord.id.desc(),
            )
            .limit(_MAX_PER_CATEGORY)
        )
        rows = self.db.execute(stmt).scalars().all()
        return [
            _Evidence(
                source_type=SOURCE_EMAIL,
                source_id=row.id,
                title=_excerpt(row.subject)[:120] or "(no subject)",
                evidence_excerpt=_excerpt(f"From {row.sender}: {row.subject}"),
                timestamp=row.received_at,
                deep_link=f"/emails/{row.id}",
            )
            for row in rows
        ]

    def _search_document_chunks(
        self, org_id: UUID, user_id: UUID, query: str, business_entity_id: UUID | None
    ) -> list[_Evidence]:
        self._assert_ask_tool("search_document_chunks")
        filters = RetrievalFilters(
            source_types=frozenset({SourceType.DOCUMENT}),
            business_entity_id=business_entity_id,
        )
        chunks = self.retrieval.retrieve(
            org_id, user_id, query, filters=filters, k=_MAX_CHUNKS
        )
        return [
            _Evidence(
                source_type=SOURCE_DOCUMENT,
                source_id=chunk.source_id,
                title=chunk.title,
                evidence_excerpt=_excerpt(chunk.evidence_excerpt),
                timestamp=chunk.timestamp,
                deep_link=chunk.deep_link,
            )
            for chunk in chunks
        ]

    def _list_open_actions(self, org_id: UUID) -> list[_Evidence]:
        self._assert_ask_tool("list_open_actions")
        stmt = (
            scope_select(select(ActionItem), ActionItem, org_id)
            .where(ActionItem.status == ActionStatus.OPEN)
            .order_by(ActionItem.created_at.desc(), ActionItem.id.desc())
            .limit(_MAX_PER_CATEGORY)
        )
        rows = self.db.execute(stmt).scalars().all()
        return [
            _Evidence(
                source_type=SOURCE_ACTION,
                source_id=row.id,
                title=_excerpt(row.title)[:120] or "Action",
                evidence_excerpt=_excerpt(row.evidence_text or row.title),
                timestamp=row.created_at,
                deep_link=f"/actions/{row.id}",
            )
            for row in rows
        ]

    def _list_recent_decisions(self, org_id: UUID) -> list[_Evidence]:
        self._assert_ask_tool("list_recent_decisions")
        stmt = (
            scope_select(select(DecisionRecord), DecisionRecord, org_id)
            .order_by(DecisionRecord.decided_at.desc(), DecisionRecord.id.desc())
            .limit(_MAX_PER_CATEGORY)
        )
        rows = self.db.execute(stmt).scalars().all()
        return [
            _Evidence(
                source_type=SOURCE_DECISION,
                source_id=row.id,
                title=_excerpt(row.title)[:120] or "Decision",
                evidence_excerpt=_excerpt(f"{row.decision} — {row.rationale}"),
                timestamp=row.decided_at,
                deep_link=f"/decisions/{row.id}",
            )
            for row in rows
        ]

    def _get_client_context(
        self, org_id: UUID, business_entity_id: UUID | None
    ) -> list[_Evidence]:
        """Retrieve client-facing context from CLIENT business entities.

        Clients are modelled as :class:`~app.core.models.BusinessEntity` rows of
        type ``CLIENT``, so this tool reads the same organization-scoped core
        table every other ASK tool reads. Only the entity's own description and
        attributes are surfaced — nothing is inferred by the model.
        """

        self._assert_ask_tool("get_client_context")
        stmt = scope_select(select(BusinessEntity), BusinessEntity, org_id).where(
            BusinessEntity.entity_type == BusinessEntityType.CLIENT
        )
        if business_entity_id is not None:
            stmt = stmt.where(BusinessEntity.id == business_entity_id)
        stmt = stmt.order_by(
            BusinessEntity.updated_at.desc(), BusinessEntity.id.desc()
        ).limit(_MAX_PER_CATEGORY)
        rows = self.db.execute(stmt).scalars().all()
        evidence: list[_Evidence] = []
        for row in rows:
            attributes = row.attributes if isinstance(row.attributes, dict) else {}
            tags = attributes.get("tags") or attributes.get("needs") or []
            excerpt = f"Client {row.name}."
            if row.description:
                excerpt = f"{excerpt} {row.description}"
            if isinstance(tags, list) and tags:
                excerpt = f"{excerpt} Tags: {', '.join(str(t) for t in tags)}."
            evidence.append(
                _Evidence(
                    source_type=SOURCE_CLIENT,
                    source_id=row.id,
                    title=_excerpt(row.name)[:120] or "Client",
                    evidence_excerpt=_excerpt(excerpt),
                    timestamp=row.updated_at,
                    deep_link=f"/knowledge?business_entity_id={row.id}",
                )
            )
        return evidence

    def _get_upcoming_calendar_events(
        self, org_id: UUID, user_id: UUID
    ) -> list[_Evidence]:
        self._assert_ask_tool("get_upcoming_calendar_events")
        stmt = (
            scope_select(select(CalendarEventLink), CalendarEventLink, org_id)
            .where(CalendarEventLink.user_id == user_id)
            .where(
                CalendarEventLink.sync_status.in_(
                    (
                        CalendarSyncStatus.PENDING,
                        CalendarSyncStatus.SYNCED,
                        CalendarSyncStatus.UPDATE_PENDING,
                    )
                )
            )
            .order_by(
                CalendarEventLink.created_at.desc(), CalendarEventLink.id.desc()
            )
            .limit(_MAX_PER_CATEGORY)
        )
        rows = self.db.execute(stmt).scalars().all()
        evidence: list[_Evidence] = []
        for row in rows:
            title = "Calendar event"
            excerpt = f"Calendar event on {row.google_calendar_id}."
            if row.action_item_id is not None:
                action = self.db.get(ActionItem, row.action_item_id)
                if action is not None and action.organization_id == org_id:
                    title = _excerpt(action.title)[:120] or title
                    excerpt = _excerpt(f"Scheduled: {action.title}")
            evidence.append(
                _Evidence(
                    source_type=SOURCE_CALENDAR,
                    source_id=row.id,
                    title=title,
                    evidence_excerpt=excerpt,
                    timestamp=row.last_synced_at or row.created_at,
                    deep_link=f"/calendar/{row.id}",
                )
            )
        return evidence

    # -- Evidence assembly --------------------------------------------------

    def _gather_evidence(
        self,
        org_id: UUID,
        user_id: UUID,
        question: str,
        business_entity_id: UUID | None,
    ) -> list[_Evidence]:
        """Collect the BOUNDED evidence set via ASK-tier tools only (Req 31.1).

        Deduplicates by ``source_id`` (a record can surface via more than one
        tool) so the evidence set — and therefore the citable set — is a clean,
        bounded slice.
        """

        collected: list[_Evidence] = []
        collected += self._search_confirmed_knowledge(org_id)
        collected += self._list_open_actions(org_id)
        collected += self._list_recent_decisions(org_id)
        collected += self._get_client_context(org_id, business_entity_id)
        collected += self._get_upcoming_calendar_events(org_id, user_id)
        collected += self._search_email_sources(org_id)
        collected += self._search_document_chunks(
            org_id, user_id, question, business_entity_id
        )

        seen: set[UUID] = set()
        bounded: list[_Evidence] = []
        for item in collected:
            if item.source_id in seen:
                continue
            seen.add(item.source_id)
            bounded.append(item)
        return bounded

    # -- ask (no mutation) --------------------------------------------------

    def ask(self, org_id: UUID, user_id: UUID, req) -> "object":
        """Answer a grounded question or return a ``SUGGESTED`` artifact.

        Executes the full pipeline and returns a
        :class:`~app.modules.cwi.schemas.CopilotResponse`. No mutation ever
        happens here (Requirement 31.3): DRAFT/ACT intents yield a ``SUGGESTED``
        artifact that requires an explicit :meth:`confirm` step.
        """

        # Imported here to avoid a circular import at module load time.
        from app.modules.cwi.schemas import (
            Citation,
            CopilotResponse,
            SuggestedArtifact,
        )

        intent = self._determine_intent(req.question, getattr(req, "intent", None))
        business_entity_id = getattr(req, "business_entity_id", None)

        evidence = self._gather_evidence(
            org_id, user_id, req.question, business_entity_id
        )
        evidence_by_id: dict[UUID, _Evidence] = {e.source_id: e for e in evidence}

        # Honest refusal when there is nothing confirmed to ground on: never
        # call the model, never guess (Requirement 30.5 / Property 19).
        if not evidence_by_id:
            response = CopilotResponse(
                answer=INSUFFICIENT_EVIDENCE_MESSAGE,
                citations=[],
                insufficient_evidence=True,
                suggested_artifact=None,
                intent=intent.value,
            )
            self._log_answer(org_id, user_id, req.question, response, evidence)
            return response

        context = CopilotQueryContext(
            organization_id=org_id,
            question=req.question,
            intent=intent.value,
            evidence=[e.to_provider_item() for e in evidence],
        )

        def _run(provider: AIProvider) -> CopilotAnswerOutput:
            return provider.answer_copilot_query(context)

        output = call_with_fallback(self.ai_provider, self.settings, _run)

        # Grounding validation (Requirement 30.7, 31.2 / Property 19): rebuild
        # each citation from the backend-held record; drop any source_id the
        # model returned that is not in the bounded evidence set.
        citations: list[Citation] = []
        seen: set[UUID] = set()
        for source_id in output.cited_source_ids:
            if source_id not in evidence_by_id or source_id in seen:
                continue
            seen.add(source_id)
            record = evidence_by_id[source_id]
            citations.append(
                Citation(
                    source_type=record.source_type,
                    source_id=record.source_id,
                    title=record.title,
                    evidence_excerpt=record.evidence_excerpt,
                    timestamp=record.timestamp,
                    deep_link=record.deep_link,
                )
            )

        if output.insufficient_evidence:
            response = CopilotResponse(
                answer=INSUFFICIENT_EVIDENCE_MESSAGE,
                citations=[],
                insufficient_evidence=True,
                suggested_artifact=None,
                intent=intent.value,
            )
            self._log_answer(org_id, user_id, req.question, response, evidence)
            return response

        suggested_artifact: SuggestedArtifact | None = None
        answer: str | None = output.answer
        if intent in (CopilotIntent.DRAFT, CopilotIntent.ACT) and (
            output.proposed_artifact is not None
        ):
            artifact = output.proposed_artifact
            suggested_artifact = SuggestedArtifact(
                status="SUGGESTED",
                tier=intent.value,
                kind=artifact.kind,
                title=artifact.title,
                body=artifact.body,
                details=dict(artifact.details or {}),
            )
            # A SUGGESTED artifact replaces a prose answer (design pipeline).
            answer = None

        response = CopilotResponse(
            answer=answer,
            citations=citations,
            insufficient_evidence=False,
            suggested_artifact=suggested_artifact,
            intent=intent.value,
        )
        self._log_answer(org_id, user_id, req.question, response, evidence)
        return response

    # -- answer provenance log (Requirement 33.7) ---------------------------

    @staticmethod
    def _evidence_to_dict(item: "_Evidence") -> dict:
        """Serialize one backend-held evidence record for the answer log."""

        return {
            "source_type": item.source_type,
            "source_id": str(item.source_id),
            "title": item.title,
            "evidence_excerpt": item.evidence_excerpt,
            "timestamp": (
                item.timestamp.isoformat() if item.timestamp is not None else None
            ),
            "deep_link": item.deep_link,
        }

    @staticmethod
    def _citation_to_dict(citation) -> dict:
        """Serialize one returned citation for the answer log."""

        return {
            "source_type": citation.source_type,
            "source_id": str(citation.source_id),
            "title": citation.title,
            "evidence_excerpt": citation.evidence_excerpt,
            "timestamp": (
                citation.timestamp.isoformat()
                if citation.timestamp is not None
                else None
            ),
            "deep_link": citation.deep_link,
        }

    def _log_answer(
        self,
        org_id: UUID,
        user_id: UUID,
        question: str,
        response,
        evidence: list["_Evidence"],
    ) -> None:
        """Persist the answer + its citations/evidence and stamp ``answer_id``.

        Backs "what data was used for an AI answer" (Requirement 33.7). This is
        a transparency log only — it never writes an ``AuditLog`` row, so it
        does not affect the audit-completeness property (P20).
        """

        log = CopilotAnswerLog(
            organization_id=org_id,
            user_id=user_id,
            question=question,
            answer=response.answer,
            intent=response.intent,
            insufficient_evidence=response.insufficient_evidence,
            citations_json=[
                self._citation_to_dict(c) for c in response.citations
            ],
            evidence_json=[self._evidence_to_dict(e) for e in evidence],
        )
        self.db.add(log)
        self.db.flush()
        response.answer_id = log.id

    # -- suggested questions ------------------------------------------------

    def suggested_questions(self, org_id: UUID, user_id: UUID) -> "object":
        """Return a fixed question set plus a dynamic, org-scoped set (Req 30.6).

        The dynamic questions are themselves grounded — they are derived only
        from current confirmed, organization-scoped context (recent confirmed
        knowledge, open actions, active clients), never from the model.
        """

        from app.modules.cwi.schemas import SuggestedQuestionsResponse

        fixed = [
            "What needs my attention today?",
            "Show my overdue tasks.",
            "Which important emails still need a reply?",
            "What AI suggestions are waiting for confirmation?",
            "Summarize recent risks and decisions.",
        ]

        dynamic: list[str] = []

        open_count = self.db.execute(
            scope_select(select(ActionItem), ActionItem, org_id).where(
                ActionItem.status == ActionStatus.OPEN
            )
        ).scalars().all()
        if open_count:
            dynamic.append(
                f"What are my {len(open_count)} open action(s) about?"
            )

        recent_decisions = self.db.execute(
            scope_select(select(DecisionRecord), DecisionRecord, org_id)
            .order_by(DecisionRecord.decided_at.desc())
            .limit(1)
        ).scalars().first()
        if recent_decisions is not None:
            dynamic.append(
                f"Why did we decide: {_excerpt(recent_decisions.title)[:80]}?"
            )

        clients = self.db.execute(
            scope_select(select(BusinessEntity), BusinessEntity, org_id)
            .where(BusinessEntity.entity_type == BusinessEntityType.CLIENT)
            .order_by(BusinessEntity.updated_at.desc())
            .limit(3)
        ).scalars().all()
        for client in clients:
            dynamic.append(f"What's the latest on {client.name}?")

        # Keep the dynamic set bounded.
        dynamic = dynamic[:_MAX_PER_CATEGORY]

        return SuggestedQuestionsResponse(
            fixed=fixed,
            dynamic=dynamic,
            questions=fixed + dynamic,
        )

    # -- confirm (the ONLY mutation path) -----------------------------------

    def confirm(self, org_id: UUID, user_id: UUID, req) -> "object":
        """Apply a previously-``SUGGESTED`` Copilot artifact (Requirement 31.3).

        This is the *only* place a Copilot-originated mutation is applied, and it
        happens only on an explicit human confirmation. Applying an
        ``ACTION_ITEM`` creates a real, evidence-backed ``ActionItem`` and writes
        exactly one ``CREATE_ACTION`` audit row in the *same* transaction
        (Requirement 31.4 / Property 20). Other artifact kinds are applied
        through their dedicated services in later milestones and are rejected
        here with ``422``.
        """

        from app.modules.cwi.schemas import CopilotConfirmResponse

        kind = (req.kind or "").upper()
        if kind == "ACTION_ITEM":
            payload = ActionCreate(
                title=req.title or "Follow up",
                description=req.body,
                business_entity_id=getattr(req, "business_entity_id", None),
                due_date=getattr(req, "due_date", None),
                evidence_text=getattr(req, "evidence_text", None) or req.body,
            )
            action = self.actions.create(org_id, user_id, payload)
            return CopilotConfirmResponse(
                applied=True,
                kind=kind,
                created_id=action.id,
                message="Action item created from the Copilot suggestion.",
            )

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Copilot artifact kind {kind!r} cannot be confirmed here; use "
                "its dedicated confirm flow."
            ),
        )


__all__ = [
    "CopilotService",
    "CopilotIntent",
    "CopilotToolTier",
    "COPILOT_TOOL_ALLOWLIST",
    "INSUFFICIENT_EVIDENCE_MESSAGE",
]
