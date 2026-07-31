"""AI-assisted Gmail draft lifecycle with idempotent send (M6.6, Req 32).

:class:`EmailDraftService` owns the full lifecycle of an AI-assisted Gmail
draft: assembling permission-filtered confirmed context, calling the provider's
``generate_email_draft``, persisting the suggested draft, editing/approving/
rejecting it, materializing a real Gmail draft, and — only after a *separate*
explicit confirmation — sending it exactly once.

Design invariants (carried from the Core Engine / CWI services):

* **The backend resolves and validates recipients; the LLM never invents them.**
  ``generate_email_draft`` returns no recipient address at all. The service
  resolves recipients from the thread (or the explicitly-provided, validated
  addresses) and stores them in ``to_recipients_json`` (Requirement 32.2).
* **Every referenced fact is grounded.** The provider's ``referenced_facts`` are
  re-validated against the ids present in the supplied context; any fabricated
  id is dropped before persistence (Requirement 32.1, mirroring Property 19).
* **``gmail.compose`` is incremental, never at sign-in.** Compose/send actions
  require the connection to carry the incrementally-granted ``gmail.compose``
  scope; otherwise the service refuses with ``403`` and directs the user to the
  incremental-authorization flow (Requirement 32.3).
* **Send is a separate explicit confirmation and is idempotent.** Sending
  requires ``confirm=True`` (distinct from approving content, Requirement 32.7)
  and performs a single-row atomic status compare-and-set into ``SENDING``
  guarded by ``send_idempotency_key``. Any attempt that observes ``SENDING`` or
  ``SENT`` returns the existing ``gmail_sent_message_id`` **without** calling
  Gmail again, so a retried/concurrent send never produces a duplicate
  (Requirement 32.8 / Property 17). Email is never sent automatically.
* **Org + user scoping.** Every lookup is org-scoped; a cross-org draft id is
  indistinguishable from a missing one and yields ``404`` (Requirement 32.12 /
  Property 13).
* **One audit per external effect, same transaction.** Creating a Gmail draft
  and sending each write exactly one ``AuditLog`` (``CREATE_GMAIL_DRAFT`` /
  ``SEND_EMAIL``) in the caller's transaction (Requirement 32.9 / Property 20).
* **AI content is never confirmed knowledge.** A draft is content the user
  reviews and sends; it is never promoted into business memory here
  (Requirement 32.11).

Gmail I/O is performed exclusively through an injected
:class:`~app.modules.cwi.services.gmail_client.GmailClient`, so tests use a
deterministic fake and no real Gmail call — and no real email — ever occurs
(Requirement 34).
"""

from __future__ import annotations

import re
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.models import (
    ActionItem,
    ActionStatus,
    DecisionRecord,
    KnowledgeItem,
    SuggestionStatus,
)
from app.core.services.ai_provider import (
    AIProvider,
    ActionView,
    DecisionView,
    EmailDraftContext,
    EmailDraftOutput,
    GmailMessageView,
    KnowledgeView,
    RecipientIdentity,
)
from app.core.services.audit_service import AuditService
from app.dependencies import call_with_fallback, not_found, scope_select
from app.modules.cwi.models import (
    EmailDraft,
    EmailDraftStatus,
    EmailMessageRecord,
    IntegrationConnection,
    IntegrationService as IntegrationServiceEnum,
)
from app.modules.cwi.schemas import EmailDraftEdit, EmailDraftRequest
from app.modules.cwi.services.gmail_client import (
    GmailClient,
    GmailClientError,
    GmailDraftMessage,
)
from app.modules.cwi.services.google_oauth import GMAIL_COMPOSE_SCOPE
from app.modules.cwi.services.integration_service import IntegrationService

# Audit action types recorded per external effect (Requirement 32.9 / P20).
CREATE_GMAIL_DRAFT = "CREATE_GMAIL_DRAFT"
SEND_EMAIL = "SEND_EMAIL"

#: Audit action type recorded when a local email draft is permanently deleted.
DELETE_EMAIL_DRAFT = "DELETE_EMAIL_DRAFT"

_TARGET_TYPE = "EmailDraft"

# Bounded context caps so the provider never sees every historical record.
_MAX_CONTEXT_ITEMS = 8
_MAX_THREAD_MESSAGES = 20

# A pragmatic email-address validator: recipients are only ever accepted when
# they look like an address, so the backend never stores (nor the LLM ever
# supplies) a malformed recipient.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# The statuses from which an atomic compare-and-set into SENDING may win.
_SENDABLE_STATUSES = (
    EmailDraftStatus.USER_APPROVED,
    EmailDraftStatus.GMAIL_DRAFT_CREATED,
)


def _conflict(detail: str) -> HTTPException:
    """Build the ``409 Conflict`` used when a lifecycle precondition fails."""

    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _forbidden(detail: str) -> HTTPException:
    """Build the ``403`` used when an incremental scope is missing."""

    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _bad_request(detail: str) -> HTTPException:
    """Build the ``400`` used when explicit send confirmation is missing."""

    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _unprocessable(detail: str) -> HTTPException:
    """Build the ``422`` used when a user-supplied recipient is malformed."""

    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail
    )


def _validate_recipients(candidates: list[str]) -> list[str]:
    """Validate + de-duplicate user-supplied recipient addresses (Req 32.2).

    The user may set/override recipients, but the *backend* is the validator:
    each address must match the basic email regex or the whole edit is rejected
    with ``422`` (no partial write). Order is preserved and case-insensitive
    duplicates are collapsed. The LLM never supplies addresses; this only ever
    validates what a human explicitly typed.
    """

    seen: set[str] = set()
    valid: list[str] = []
    for raw in candidates:
        address = (raw or "").strip()
        if not address:
            raise _unprocessable("A recipient address must not be empty.")
        if _EMAIL_RE.match(address) is None:
            raise _unprocessable(f"'{address}' is not a valid email address.")
        key = address.lower()
        if key in seen:
            continue
        seen.add(key)
        valid.append(address)
    return valid


def _valid_recipients(candidates: list[str]) -> list[str]:
    """Return the well-formed, de-duplicated recipient addresses (Req 32.2).

    Deterministic: preserves first-seen order and drops anything that does not
    look like an address, so the backend never stores a malformed recipient.
    """

    seen: set[str] = set()
    valid: list[str] = []
    for raw in candidates:
        address = (raw or "").strip()
        key = address.lower()
        if not address or key in seen:
            continue
        if _EMAIL_RE.match(address) is None:
            continue
        seen.add(key)
        valid.append(address)
    return valid


class EmailDraftService:
    """Manage the AI-assisted Gmail draft lifecycle (Requirement 32)."""

    def __init__(
        self,
        db: Session,
        ai_provider: AIProvider,
        gmail_client: GmailClient,
        *,
        integration_service: IntegrationService | None = None,
        settings: Settings | None = None,
        audit: AuditService | None = None,
    ) -> None:
        """Bind the service to the request transaction and injected providers.

        Args:
            db: Request-scoped session (transaction owned by the caller/route).
            ai_provider: The resolved :class:`AIProvider` (mock or LLM).
            gmail_client: The Gmail transport (a fake in tests — no network).
            integration_service: Used to obtain a valid access token; when
                omitted a transient empty token is used (the fake ignores it).
            settings: Application settings (for the provider fallback policy).
            audit: The :class:`AuditService` for one-audit-per-effect writes.
        """

        self.db = db
        self.ai_provider = ai_provider
        self.gmail = gmail_client
        self.settings = settings or get_settings()
        self._integration_service = integration_service
        self.audit = audit or AuditService(db)

    # -- Scoped lookups -----------------------------------------------------

    def _get_scoped(self, org_id: UUID, draft_id: UUID) -> EmailDraft:
        """Return an org-scoped draft or ``404`` (Requirement 32.12 / P13)."""

        stmt = scope_select(select(EmailDraft), EmailDraft, org_id).where(
            EmailDraft.id == draft_id
        )
        draft = self.db.execute(stmt).scalar_one_or_none()
        if draft is None:
            raise not_found("Email draft not found.")
        return draft

    def _get_connection(
        self, org_id: UUID, connection_id: UUID, user_id: UUID | None = None
    ) -> IntegrationConnection:
        stmt = scope_select(
            select(IntegrationConnection), IntegrationConnection, org_id
        ).where(IntegrationConnection.id == connection_id)
        if user_id is not None:
            stmt = stmt.where(IntegrationConnection.user_id == user_id)
        connection = self.db.execute(stmt).scalar_one_or_none()
        if connection is None:
            raise not_found("Integration connection not found.")
        return connection

    def _access_token(self, org_id: UUID, connection_id: UUID) -> str:
        """Obtain a valid (refreshed) access token, or a transient empty one.

        The token exists only transiently in memory for the outbound Gmail call
        and is never persisted or logged. When no :class:`IntegrationService`
        is injected (tests with the fake client), an empty string is returned.
        """

        if self._integration_service is not None:
            return self._integration_service.get_valid_access_token(
                org_id, connection_id
            )
        return ""

    def _require_compose_scope(self, connection: IntegrationConnection) -> None:
        """Ensure the connection carries the incremental ``gmail.compose`` scope.

        Compose/send is gated on the scope the user grants *incrementally* (via
        the integrations authorization flow), never at basic sign-in
        (Requirement 32.3). A connection without it is refused with ``403`` so
        the frontend can direct the user to authorize compose.
        """

        granted = set(connection.granted_scopes_json or [])
        if GMAIL_COMPOSE_SCOPE not in granted:
            raise _forbidden(
                "Gmail compose authorization is required. Grant the gmail.compose "
                "scope from Integrations before creating or sending a draft."
            )

    # -- Context assembly ---------------------------------------------------

    def _thread_messages(
        self, org_id: UUID, source_item_id: UUID | None
    ) -> tuple[list[GmailMessageView], str | None]:
        """Build the Gmail thread view + thread id for a source item (bounded).

        Returns the thread messages (each grounded by ``source_id`` = the
        derived ``SourceItem`` id, so the provider may cite them) and the shared
        ``gmail_thread_id``. When no source item is supplied, returns an empty
        thread.
        """

        if source_item_id is None:
            return [], None

        stmt = (
            scope_select(select(EmailMessageRecord), EmailMessageRecord, org_id)
            .where(EmailMessageRecord.source_item_id == source_item_id)
            .order_by(
                EmailMessageRecord.received_at.asc(),
                EmailMessageRecord.id.asc(),
            )
            .limit(_MAX_THREAD_MESSAGES)
        )
        records = list(self.db.execute(stmt).scalars().all())
        views: list[GmailMessageView] = []
        thread_id: str | None = None
        for record in records:
            thread_id = record.gmail_thread_id or thread_id
            views.append(
                GmailMessageView(
                    source_id=record.source_item_id,
                    gmail_message_id=record.gmail_message_id,
                    sender=record.sender,
                    recipients=list(record.recipients_json or []),
                    subject=record.subject,
                    body_text=record.subject,
                    timestamp=record.received_at,
                )
            )
        return views, thread_id

    def _confirmed_knowledge(
        self, org_id: UUID, business_entity_id: UUID | None
    ) -> tuple[list[KnowledgeView], list[KnowledgeView]]:
        """Return (client knowledge, meeting memories) — confirmed only, bounded."""

        stmt = (
            scope_select(select(KnowledgeItem), KnowledgeItem, org_id)
            .where(KnowledgeItem.status == SuggestionStatus.CONFIRMED)
            .order_by(KnowledgeItem.created_at.desc(), KnowledgeItem.id.desc())
            .limit(_MAX_CONTEXT_ITEMS * 2)
        )
        if business_entity_id is not None:
            stmt = stmt.where(
                KnowledgeItem.business_entity_id == business_entity_id
            )
        rows = list(self.db.execute(stmt).scalars().all())

        knowledge: list[KnowledgeView] = []
        memories: list[KnowledgeView] = []
        for row in rows:
            view = KnowledgeView(
                id=row.id,
                summary=row.summary,
                evidence_text=row.evidence_text or "",
                knowledge_type=row.knowledge_type,
                timestamp=row.created_at,
            )
            if (row.knowledge_type or "").upper() == "MEMORY":
                if len(memories) < _MAX_CONTEXT_ITEMS:
                    memories.append(view)
            elif len(knowledge) < _MAX_CONTEXT_ITEMS:
                knowledge.append(view)
        return knowledge, memories

    def _open_actions(
        self, org_id: UUID, business_entity_id: UUID | None
    ) -> list[ActionView]:
        stmt = (
            scope_select(select(ActionItem), ActionItem, org_id)
            .where(ActionItem.status == ActionStatus.OPEN)
            .order_by(ActionItem.created_at.desc(), ActionItem.id.desc())
            .limit(_MAX_CONTEXT_ITEMS)
        )
        if business_entity_id is not None:
            stmt = stmt.where(ActionItem.business_entity_id == business_entity_id)
        rows = list(self.db.execute(stmt).scalars().all())
        return [
            ActionView(
                id=row.id,
                title=row.title,
                status=row.status.value if hasattr(row.status, "value") else str(row.status),
                due_date=row.due_date,
            )
            for row in rows
        ]

    def _confirmed_decisions(
        self, org_id: UUID, business_entity_id: UUID | None
    ) -> list[DecisionView]:
        stmt = (
            scope_select(select(DecisionRecord), DecisionRecord, org_id)
            .order_by(DecisionRecord.decided_at.desc(), DecisionRecord.id.desc())
            .limit(_MAX_CONTEXT_ITEMS)
        )
        if business_entity_id is not None:
            stmt = stmt.where(
                DecisionRecord.business_entity_id == business_entity_id
            )
        rows = list(self.db.execute(stmt).scalars().all())
        return [
            DecisionView(
                id=row.id,
                title=row.title,
                decision=row.decision,
                rationale=row.rationale,
                timestamp=row.decided_at,
            )
            for row in rows
        ]

    def _resolve_recipients(
        self,
        req_recipients: list[str],
        thread_messages: list[GmailMessageView],
    ) -> tuple[list[str], RecipientIdentity]:
        """Resolve + validate recipients on the backend (Requirement 32.2).

        Prefers explicitly-provided, validated addresses; otherwise replies to
        the sender of the latest inbound thread message. The returned
        :class:`RecipientIdentity` is what the provider sees (name only), while
        the validated address list is what the backend stores. The provider is
        never asked for — and never returns — an address.
        """

        recipients = _valid_recipients(req_recipients)
        if not recipients and thread_messages:
            sender = (thread_messages[-1].sender or "").strip()
            recipients = _valid_recipients([sender])

        primary = recipients[0] if recipients else None
        display_name = None
        if primary is not None:
            display_name = primary.split("@", 1)[0]
        identity = RecipientIdentity(display_name=display_name, email=primary)
        return recipients, identity

    def _ground_referenced_facts(
        self, output: EmailDraftOutput, context: EmailDraftContext
    ) -> list[dict]:
        """Drop any referenced fact whose id is not present in the context.

        The provider must ground every fact in a supplied ``source_id``; this
        re-validates each returned id against the ids the backend actually
        supplied, so a fabricated id never gets persisted (Requirement 32.1).
        """

        valid_ids: set[UUID] = set()
        for msg in context.thread_messages:
            if msg.source_id is not None:
                valid_ids.add(msg.source_id)
        for item in context.confirmed_client_knowledge:
            valid_ids.add(item.id)
        for memory in context.confirmed_meeting_memories:
            valid_ids.add(memory.id)
        for action in context.open_action_items:
            valid_ids.add(action.id)
        for decision in context.relevant_confirmed_decisions:
            valid_ids.add(decision.id)

        grounded: list[dict] = []
        for fact in output.referenced_facts:
            if fact.source_id not in valid_ids:
                continue
            grounded.append(
                {
                    "source_type": fact.source_type,
                    "source_id": str(fact.source_id),
                    "evidence": fact.evidence,
                    "timestamp": (
                        fact.timestamp.isoformat()
                        if fact.timestamp is not None
                        else None
                    ),
                }
            )
        return grounded

    # -- Public API ---------------------------------------------------------

    def request_draft(
        self, org_id: UUID, user_id: UUID, req: EmailDraftRequest
    ) -> EmailDraft:
        """Retrieve context, call the provider, and persist ``AI_SUGGESTED`` (32.4).

        Assembles ONLY permission-filtered confirmed context, resolves and
        validates recipients on the backend, calls ``generate_email_draft``, and
        persists a new :class:`EmailDraft` with status ``AI_SUGGESTED`` whose
        referenced facts are all grounded in the supplied context. No external
        Gmail call happens here.
        """

        connection = self._get_connection(org_id, req.connection_id, user_id)
        if connection.service != IntegrationServiceEnum.GMAIL:
            raise _conflict("A Gmail connection is required to draft email.")

        thread_messages, thread_id = self._thread_messages(
            org_id, req.source_item_id
        )
        knowledge, memories = self._confirmed_knowledge(
            org_id, req.business_entity_id
        )
        open_actions = self._open_actions(org_id, req.business_entity_id)
        decisions = self._confirmed_decisions(org_id, req.business_entity_id)
        recipients, identity = self._resolve_recipients(
            req.to_recipients, thread_messages
        )

        context = EmailDraftContext(
            thread_messages=thread_messages,
            recipient_identity=identity,
            confirmed_client_knowledge=knowledge,
            confirmed_meeting_memories=memories,
            open_action_items=open_actions,
            relevant_confirmed_decisions=decisions,
            requested_purpose=req.purpose or "",
            requested_tone=req.tone or "professional",
        )

        def _run(provider: AIProvider) -> EmailDraftOutput:
            return provider.generate_email_draft(context)

        output = call_with_fallback(self.ai_provider, self.settings, _run)
        grounded_facts = self._ground_referenced_facts(output, context)

        draft = EmailDraft(
            organization_id=org_id,
            user_id=user_id,
            integration_connection_id=connection.id,
            source_item_id=req.source_item_id,
            business_entity_id=req.business_entity_id,
            gmail_thread_id=thread_id,
            to_recipients_json=recipients,
            subject=output.subject,
            body_text=output.body_text,
            tone=output.tone,
            purpose=output.purpose,
            referenced_facts_json=grounded_facts,
            warnings_json=list(output.warnings),
            status=EmailDraftStatus.AI_SUGGESTED,
            gmail_draft_id=None,
            gmail_sent_message_id=None,
            send_idempotency_key=uuid4().hex,
        )
        self.db.add(draft)
        self.db.flush()
        return draft

    def edit_draft(
        self, org_id: UUID, user_id: UUID, draft_id: UUID, edits: EmailDraftEdit
    ) -> EmailDraft:
        """Apply human edits to a draft before it is sent (Requirement 32.5).

        Only explicitly-provided fields are applied; ``to_recipients`` is
        re-validated on the backend. Editing is refused once the draft is in a
        terminal or in-flight send state.
        """

        draft = self._get_scoped(org_id, draft_id)
        if draft.status in (
            EmailDraftStatus.SENDING,
            EmailDraftStatus.SENT,
            EmailDraftStatus.REJECTED,
            EmailDraftStatus.FAILED,
        ):
            raise _conflict(
                f"A draft in status {draft.status.value} can no longer be edited."
            )

        # Validate any user-supplied recipients *before* mutating the row so a
        # malformed address rejects the whole edit with 422 and never leaves a
        # partial write (Requirement 32.2).
        new_recipients: list[str] | None = None
        if edits.to_recipients is not None:
            new_recipients = _validate_recipients(edits.to_recipients)

        if edits.subject is not None:
            draft.subject = edits.subject
        if edits.body_text is not None:
            draft.body_text = edits.body_text
        if edits.tone is not None:
            draft.tone = edits.tone
        if edits.purpose is not None:
            draft.purpose = edits.purpose
        if new_recipients is not None:
            draft.to_recipients_json = new_recipients
        self.db.add(draft)
        self.db.flush()
        return draft

    def approve_draft(
        self, org_id: UUID, user_id: UUID, draft_id: UUID
    ) -> EmailDraft:
        """Approve the draft content → ``USER_APPROVED`` (Requirement 32.5).

        Idempotent on an already-approved draft. Approving content is distinct
        from the separate explicit send confirmation (Requirement 32.7).
        """

        draft = self._get_scoped(org_id, draft_id)
        if draft.status in (
            EmailDraftStatus.USER_APPROVED,
            EmailDraftStatus.GMAIL_DRAFT_CREATED,
        ):
            return draft
        if draft.status != EmailDraftStatus.AI_SUGGESTED:
            raise _conflict(
                f"A draft in status {draft.status.value} cannot be approved."
            )
        draft.status = EmailDraftStatus.USER_APPROVED
        self.db.add(draft)
        self.db.flush()
        return draft

    def reject_draft(
        self, org_id: UUID, user_id: UUID, draft_id: UUID
    ) -> EmailDraft:
        """Reject the draft → ``REJECTED`` (Requirement 32.5).

        A rejected draft is never created in Gmail or sent. Refused once the
        draft is in-flight or already sent.
        """

        draft = self._get_scoped(org_id, draft_id)
        if draft.status == EmailDraftStatus.REJECTED:
            return draft
        if draft.status in (EmailDraftStatus.SENDING, EmailDraftStatus.SENT):
            raise _conflict(
                f"A draft in status {draft.status.value} cannot be rejected."
            )
        draft.status = EmailDraftStatus.REJECTED
        self.db.add(draft)
        self.db.flush()
        return draft

    def create_gmail_draft(
        self, org_id: UUID, user_id: UUID, draft_id: UUID
    ) -> EmailDraft:
        """Materialize a real Gmail draft → ``GMAIL_DRAFT_CREATED`` (Req 32.6).

        Requires an approved draft and the incremental ``gmail.compose`` scope.
        Calls ``users.drafts.create``, stores ``gmail_draft_id``, and writes
        exactly one ``CREATE_GMAIL_DRAFT`` audit row in the same transaction
        (Requirement 32.9). Idempotent: an already-created Gmail draft is
        returned without a second create.
        """

        draft = self._get_scoped(org_id, draft_id)
        if (
            draft.status == EmailDraftStatus.GMAIL_DRAFT_CREATED
            and draft.gmail_draft_id is not None
        ):
            return draft
        if draft.status != EmailDraftStatus.USER_APPROVED:
            raise _conflict(
                "The draft must be approved before a Gmail draft is created."
            )

        connection = self._get_connection(
            org_id, draft.integration_connection_id, user_id
        )
        self._require_compose_scope(connection)

        token = self._access_token(org_id, connection.id)
        message = self._to_gmail_message(draft)
        try:
            gmail_draft_id = self.gmail.create_draft(
                access_token=token,
                message=message,
                idempotency_key=f"draft-{draft.send_idempotency_key}",
            )
        except GmailClientError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not create the Gmail draft: {exc}",
            ) from exc

        draft.gmail_draft_id = gmail_draft_id
        draft.status = EmailDraftStatus.GMAIL_DRAFT_CREATED
        self.db.add(draft)
        self.db.flush()

        self.audit.record(
            org_id=org_id,
            actor_id=user_id,
            action_type=CREATE_GMAIL_DRAFT,
            target_type=_TARGET_TYPE,
            target_id=draft.id,
            detail={"gmail_draft_id": gmail_draft_id},
        )
        self.db.flush()
        return draft

    def update_gmail_draft(
        self, org_id: UUID, user_id: UUID, draft_id: UUID, edits: EmailDraftEdit
    ) -> EmailDraft:
        """Apply edits and update the materialized Gmail draft (Req 32.6).

        Requires an existing ``gmail_draft_id``. Applies the edits locally and
        calls ``users.drafts.update`` so the Gmail draft reflects the change.
        """

        draft = self.edit_draft(org_id, user_id, draft_id, edits)
        if draft.gmail_draft_id is None:
            raise _conflict("No Gmail draft exists to update.")

        connection = self._get_connection(
            org_id, draft.integration_connection_id, user_id
        )
        self._require_compose_scope(connection)
        token = self._access_token(org_id, connection.id)
        try:
            self.gmail.update_draft(
                access_token=token,
                draft_id=draft.gmail_draft_id,
                message=self._to_gmail_message(draft),
            )
        except GmailClientError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not update the Gmail draft: {exc}",
            ) from exc
        return draft

    def send_draft(
        self,
        org_id: UUID,
        user_id: UUID,
        draft_id: UUID,
        *,
        confirm: bool,
    ) -> EmailDraft:
        """Send the draft exactly once after a separate explicit confirm (32.7/8).

        ``confirm`` must be ``True`` (the separate confirmation distinct from
        approving content). The send is idempotent: a single-row atomic
        compare-and-set moves the draft into ``SENDING`` before any Gmail call;
        any attempt that observes ``SENDING``/``SENT`` returns the existing
        ``gmail_sent_message_id`` without sending again (Property 17). On the
        winning send the draft becomes ``SENT`` with its
        ``gmail_sent_message_id`` set and exactly one ``SEND_EMAIL`` audit row is
        written in the same transaction (Requirement 32.9). Email is never sent
        automatically (Requirement 32.10).
        """

        if not confirm:
            raise _bad_request(
                "Sending requires a separate explicit confirmation (confirm=true)."
            )

        draft = self._get_scoped(org_id, draft_id)

        # Any attempt observing an in-flight or completed send returns the
        # existing sent message id WITHOUT calling Gmail again (Property 17).
        if draft.status in (EmailDraftStatus.SENDING, EmailDraftStatus.SENT):
            return draft
        if draft.status not in _SENDABLE_STATUSES:
            raise _conflict(
                "The draft must be approved before it can be sent."
            )

        connection = self._get_connection(
            org_id, draft.integration_connection_id, user_id
        )
        self._require_compose_scope(connection)

        # Atomic status compare-and-set into SENDING: only one caller can win
        # the transition out of USER_APPROVED/GMAIL_DRAFT_CREATED (Req 32.8).
        result = self.db.execute(
            update(EmailDraft)
            .where(
                EmailDraft.id == draft.id,
                EmailDraft.organization_id == org_id,
                EmailDraft.status.in_(_SENDABLE_STATUSES),
            )
            .values(status=EmailDraftStatus.SENDING)
        )
        self.db.flush()
        if result.rowcount != 1:
            # Someone else won the transition; return the current row (which is
            # SENDING/SENT) with the existing sent message id — no second send.
            self.db.refresh(draft)
            return draft

        self.db.refresh(draft)  # draft.status is now SENDING

        token = self._access_token(org_id, connection.id)
        message = self._to_gmail_message(draft)
        try:
            sent_message_id = self.gmail.send_draft(
                access_token=token,
                message=message,
                idempotency_key=draft.send_idempotency_key,
                draft_id=draft.gmail_draft_id,
            )
        except GmailClientError as exc:
            draft.status = EmailDraftStatus.FAILED
            self.db.add(draft)
            self.db.flush()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not send the email: {exc}",
            ) from exc

        draft.status = EmailDraftStatus.SENT
        draft.gmail_sent_message_id = sent_message_id
        self.db.add(draft)
        self.db.flush()

        self.audit.record(
            org_id=org_id,
            actor_id=user_id,
            action_type=SEND_EMAIL,
            target_type=_TARGET_TYPE,
            target_id=draft.id,
            detail={
                "gmail_sent_message_id": sent_message_id,
                "gmail_thread_id": draft.gmail_thread_id,
            },
        )
        self.db.flush()
        return draft

    # -- Reads --------------------------------------------------------------

    def list_drafts(self, org_id: UUID, user_id: UUID) -> list[EmailDraft]:
        """Return the user's email drafts, newest first (org + user scoped)."""

        stmt = (
            scope_select(select(EmailDraft), EmailDraft, org_id)
            .where(EmailDraft.user_id == user_id)
            .order_by(EmailDraft.created_at.desc(), EmailDraft.id.desc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def get_draft(self, org_id: UUID, draft_id: UUID) -> EmailDraft:
        """Return a single org-scoped draft or ``404`` (Requirement 32.12)."""

        return self._get_scoped(org_id, draft_id)

    # -- Delete -------------------------------------------------------------

    def delete_draft(
        self, org_id: UUID, user_id: UUID, draft_id: UUID
    ) -> None:
        """Permanently delete the LOCAL email draft row (Requirement 32.12).

        This deletes only the local :class:`EmailDraft` record — it does **not**
        call Gmail, so any materialized Gmail draft or sent message is left
        untouched. Org-scoped: a cross-org/missing id is indistinguishable from
        a missing one and yields ``404``. Records exactly one
        ``DELETE_EMAIL_DRAFT`` audit row in the same transaction.

        Args:
            org_id: The tenant the draft must belong to.
            user_id: The user performing the delete (audit actor).
            draft_id: The draft to delete.

        Raises:
            HTTPException: ``404`` if the draft does not exist for the org.
        """

        draft = self._get_scoped(org_id, draft_id)
        deleted_id = draft.id
        self.db.delete(draft)
        self.db.flush()

        self.audit.record(
            org_id=org_id,
            actor_id=user_id,
            action_type=DELETE_EMAIL_DRAFT,
            target_type=_TARGET_TYPE,
            target_id=deleted_id,
            detail={},
        )
        self.db.flush()

    # -- Helpers ------------------------------------------------------------

    def _to_gmail_message(self, draft: EmailDraft) -> GmailDraftMessage:
        """Build the outbound Gmail payload from the persisted draft.

        Recipients come ONLY from the backend-resolved ``to_recipients_json``
        (Requirement 32.2); the LLM never contributes an address.
        """

        return GmailDraftMessage(
            to_recipients=list(draft.to_recipients_json or []),
            subject=draft.subject,
            body_text=draft.body_text,
            thread_id=draft.gmail_thread_id,
        )


__all__ = [
    "EmailDraftService",
    "DELETE_EMAIL_DRAFT",
    "CREATE_GMAIL_DRAFT",
    "SEND_EMAIL",
]
