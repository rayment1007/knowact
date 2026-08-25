"""Pydantic schemas for the Connected Workspace Intelligence API.

The response models here are **token-safe by construction**: every encrypted or
secret column of :class:`~app.modules.cwi.models.IntegrationConnection`
(``access_token_encrypted``, ``refresh_token_encrypted``) is deliberately
omitted. Because API responses are declared with these explicit schemas, a
token can never leak into a response body — exposing only ``status``,
``account_email``, ``granted_scopes``, ``last_sync_at``, and ``last_error``
alongside identifying metadata (Requirements 25.2, 25.3 / Property 14).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.models import SuggestionStatus
from app.core.models import Sensitivity
from app.modules.cwi.models import (
    CalendarSyncStatus,
    ConnectionStatus,
    DocumentProcessingStatus,
    EmailDraftStatus,
    IntegrationProvider,
    IntegrationService,
    RawEmailRetentionMode,
    SenderSignalType,
)


class IntegrationConnectionView(BaseModel):
    """A token-safe projection of an :class:`IntegrationConnection`.

    Exposes only the fields a user needs to manage a connection. The encrypted
    token columns are intentionally absent, so no token, ciphertext, or key can
    appear in any response built from this schema (Requirements 25.2, 25.3).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    user_id: UUID
    provider: IntegrationProvider
    service: IntegrationService
    account_email: str
    status: ConnectionStatus
    granted_scopes: list[str]
    last_sync_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_connection(cls, connection: object) -> "IntegrationConnectionView":
        """Build a view from an ORM connection, mapping ``granted_scopes_json``.

        The ORM column is ``granted_scopes_json``; the public field is
        ``granted_scopes``. This adapter copies only safe fields, so the
        encrypted token columns are never read into the view.
        """

        return cls(
            id=connection.id,
            organization_id=connection.organization_id,
            user_id=connection.user_id,
            provider=connection.provider,
            service=connection.service,
            account_email=connection.account_email,
            status=connection.status,
            granted_scopes=list(connection.granted_scopes_json or []),
            last_sync_at=connection.last_sync_at,
            last_error=connection.last_error,
            created_at=connection.created_at,
            updated_at=connection.updated_at,
        )


class AuthorizationRedirectResponse(BaseModel):
    """The redirect a client follows to begin a Google authorization flow.

    Carries only the provider ``authorization_url`` and an opaque ``state``
    value; no secret is included.
    """

    authorization_url: str
    state: str


# ---------------------------------------------------------------------------
# Gmail sync & ingestion (M6.2, Requirements 26, 27)
# ---------------------------------------------------------------------------


class InitialSyncOptions(BaseModel):
    """Options controlling the first Gmail import (Requirements 26.1-26.5).

    An invalid payload is rejected by Pydantic with ``422`` before any sync
    begins, so a bad configuration never starts a partial import (Requirement
    26.6). ``date_range_days`` is constrained to the allowed {7, 30, 90} window;
    ``attachment_handling`` and ``storage_policy`` to their enumerated choices.
    """

    date_range_days: Literal[7, 30, 90] = 30
    labels: list[str] | None = None
    include_sent: bool = False
    attachment_handling: Literal["IGNORE", "METADATA_ONLY", "STORE"] = (
        "METADATA_ONLY"
    )
    storage_policy: Literal["RAW_AND_EXTRACTED", "EXTRACTED_ONLY"] = (
        "EXTRACTED_ONLY"
    )


class SyncRunResponse(BaseModel):
    """A token-safe summary of a completed sync pass."""

    messages_seen: int
    records_created: int
    source_items_created: int
    skipped_duplicates: int
    skipped_ineligible: int
    suggestions_created: int
    record_ids: list[UUID] = Field(default_factory=list)


class EmailMessageRecordView(BaseModel):
    """A provenance-only projection of an :class:`EmailMessageRecord`.

    Carries the metadata a reviewer needs; raw content/attachments are never
    stored and never surfaced.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    integration_connection_id: UUID
    source_item_id: UUID | None
    gmail_message_id: str
    gmail_thread_id: str
    sender: str
    recipients: list[str]
    subject: str
    received_at: datetime
    labels: list[str]
    content_hash: str
    has_attachments: bool
    stored_raw: bool
    created_at: datetime

    @classmethod
    def from_record(cls, record: object) -> "EmailMessageRecordView":
        """Build a view mapping the ``*_json`` columns to public list fields."""

        return cls(
            id=record.id,
            organization_id=record.organization_id,
            integration_connection_id=record.integration_connection_id,
            source_item_id=record.source_item_id,
            gmail_message_id=record.gmail_message_id,
            gmail_thread_id=record.gmail_thread_id,
            sender=record.sender,
            recipients=list(record.recipients_json or []),
            subject=record.subject,
            received_at=record.received_at,
            labels=list(record.labels_json or []),
            content_hash=record.content_hash,
            has_attachments=record.has_attachments,
            stored_raw=record.stored_raw,
            created_at=record.created_at,
        )


class EmailTaskSuggestionView(BaseModel):
    """A view of an :class:`EmailTaskSuggestion` for the review UI."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    email_message_record_id: UUID
    source_item_id: UUID | None
    business_entity_id: UUID | None
    title: str
    description: str | None
    suggested_due_date: date | None
    suggested_owner: str | None
    related_entity_name: str | None
    gmail_message_id: str
    evidence_text: str
    ai_provider: str
    ai_model: str
    status: SuggestionStatus
    created_at: datetime
    updated_at: datetime


class EmailTaskSuggestionEdit(BaseModel):
    """Editable fields applied when a user edits a task suggestion (Req 27.7).

    Every field is optional; only explicitly-provided fields are applied.
    """

    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = None
    suggested_due_date: date | None = None
    suggested_owner: str | None = None
    related_entity_name: str | None = None
    business_entity_id: UUID | None = None


class SenderSignalRequest(BaseModel):
    """Request to mark a sender address or domain as a negative signal (27.7)."""

    pattern: str = Field(min_length=1, max_length=320)
    signal_type: SenderSignalType


class SenderSignalView(BaseModel):
    """A view of a persisted :class:`EmailSenderSignal`."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    integration_connection_id: UUID | None
    pattern: str
    signal_type: SenderSignalType
    created_at: datetime


# ---------------------------------------------------------------------------
# Calendar events from confirmed actions (M6.3, Requirement 28)
# ---------------------------------------------------------------------------


class CalendarView(BaseModel):
    """A single writable Google Calendar the user can add events to (28.3)."""

    calendar_id: str
    summary: str
    primary: bool = False
    access_role: str = "writer"


class CalendarAddRequest(BaseModel):
    """The explicit "Add to Google Calendar" request for a confirmed action.

    Carries the user's edit-before-confirm choices (Requirement 28.3): the
    target calendar, start/end times (or an all-day date for a due-date-only
    action), reminders, and optional summary/description overrides. Omitted
    fields fall back to the action's own values server-side.
    """

    connection_id: UUID
    google_calendar_id: str = Field(min_length=1, max_length=320)
    summary: str | None = Field(default=None, max_length=512)
    description: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    all_day: bool = False
    all_day_date: date | None = None
    reminder_minutes: list[int] = Field(default_factory=list)


class CalendarUpdateRequest(BaseModel):
    """Editable fields applied when updating an existing calendar event (28.6).

    Every field is optional; the service merges them with the source action's
    fields to build the updated event payload.
    """

    summary: str | None = Field(default=None, max_length=512)
    description: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    all_day: bool = False
    all_day_date: date | None = None
    reminder_minutes: list[int] = Field(default_factory=list)


class CalendarDailyBriefBlockRequest(BaseModel):
    """Request to create the optional recurring Daily Brief block (Req 28.9).

    The event's description is built server-side to contain **only** the deep
    link and a minimal label — no large or sensitive brief content is ever
    written into the event.
    """

    connection_id: UUID
    google_calendar_id: str = Field(min_length=1, max_length=320)
    deep_link: str = Field(min_length=1, max_length=1024)
    label: str = Field(default="Daily Brief", max_length=120)
    start: datetime | None = None
    recurrence: str = Field(default="RRULE:FREQ=DAILY", max_length=255)


class CalendarEventLinkView(BaseModel):
    """A view of a :class:`CalendarEventLink` with its sync state (28.5, 28.6)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    user_id: UUID
    action_item_id: UUID | None
    integration_connection_id: UUID
    google_calendar_id: str
    google_event_id: str | None
    idempotency_key: str
    sync_status: CalendarSyncStatus
    last_synced_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Documents & retrieval (M6.4, Requirement 29)
# ---------------------------------------------------------------------------


class DocumentAssetView(BaseModel):
    """A view of a :class:`DocumentAsset` for the document list / detail UI.

    Carries the metadata a user needs to manage a document and track its
    processing status. The binary itself is never exposed — only the metadata
    and the opaque ``storage_key`` is deliberately omitted from this view.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    uploaded_by: UUID
    filename: str
    mime_type: str
    checksum: str
    processing_status: DocumentProcessingStatus
    failure_reason: str | None
    sensitivity: Sensitivity
    source_deleted: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# Enterprise Copilot grounded chat (M6.5, Requirements 30, 31)
# ---------------------------------------------------------------------------


class CopilotAskRequest(BaseModel):
    """Request body of ``POST /api/copilot/ask``.

    ``question`` is the user's grounded query. ``intent`` optionally forces the
    ASK/DRAFT/ACT tier (otherwise it is inferred). ``business_entity_id`` narrows
    retrieval (e.g. a specific client) when relevant.
    """

    question: str = Field(min_length=1, max_length=2000)
    intent: Literal["ASK", "DRAFT", "ACT"] | None = None
    business_entity_id: UUID | None = None


class Citation(BaseModel):
    """A single grounded citation (design *Citation Contract*).

    Every field is populated by the backend from the record that was actually
    placed in the bounded evidence context — never by the model — so a citation
    can never be fabricated (Requirement 30.4, 30.7 / Property 19).
    """

    source_type: str
    source_id: UUID
    title: str
    evidence_excerpt: str
    timestamp: datetime | None = None
    deep_link: str


class SuggestedArtifact(BaseModel):
    """A DRAFT/ACT artifact awaiting explicit confirmation (Requirement 30.3).

    Its ``status`` is always ``SUGGESTED`` on return from ``ask`` — the Copilot
    never applies it. Confirming it is a separate, explicit step
    (``POST /api/copilot/confirm``).
    """

    status: Literal["SUGGESTED"] = "SUGGESTED"
    tier: Literal["DRAFT", "ACT"]
    kind: str  # ACTION_ITEM | CALENDAR_EVENT | ENTITY_LINK | EMAIL_DRAFT
    title: str | None = None
    body: str | None = None
    details: dict = Field(default_factory=dict)


class CopilotResponse(BaseModel):
    """Response of ``POST /api/copilot/ask`` (design *Citation Contract*).

    Either ``answer`` + ``citations`` (an ASK result) or a ``suggested_artifact``
    (a DRAFT/ACT result). ``insufficient_evidence`` is set when the Copilot
    cannot find enough confirmed evidence to answer.
    """

    answer: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    insufficient_evidence: bool = False
    suggested_artifact: SuggestedArtifact | None = None
    intent: str = "ASK"
    # Id of the persisted answer log, so the client can later ask the
    # Privacy_Service "what data was used for this answer" (Requirement 33.7).
    answer_id: UUID | None = None


class SuggestedQuestionsResponse(BaseModel):
    """Response of ``GET /api/copilot/suggested-questions`` (Requirement 30.6).

    ``fixed`` is a small constant set; ``dynamic`` is derived from current
    confirmed, org-scoped context. ``questions`` is the combined convenience
    list the UI renders.
    """

    fixed: list[str] = Field(default_factory=list)
    dynamic: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)


class CopilotConfirmRequest(BaseModel):
    """Request body of ``POST /api/copilot/confirm`` (confirm-before-mutate).

    Echoes back the previously-``SUGGESTED`` artifact the user approved. Only
    after this explicit step does any mutation occur, and exactly one audit row
    is written in the same transaction (Requirement 31.3, 31.4).
    """

    kind: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=512)
    body: str | None = None
    business_entity_id: UUID | None = None
    due_date: date | None = None
    evidence_text: str | None = None


class CopilotConfirmResponse(BaseModel):
    """Response of ``POST /api/copilot/confirm``."""

    applied: bool
    kind: str
    created_id: UUID | None = None
    message: str


# ---------------------------------------------------------------------------
# Gmail AI Draft (M6.6, Requirement 32)
# ---------------------------------------------------------------------------


class EmailDraftRequest(BaseModel):
    """Request body of ``POST /api/email-drafts`` (Requirement 32.4).

    Identifies the Gmail connection to draft from, optionally the Gmail-derived
    ``source_item_id`` the draft replies to and the ``business_entity_id`` the
    draft concerns, the user's requested ``purpose`` and ``tone``, and any
    explicit ``to_recipients``. Recipients are always **resolved and validated
    on the backend** (Requirement 32.2); any provided here are validated, and
    when omitted the backend resolves them from the thread.
    """

    connection_id: UUID
    source_item_id: UUID | None = None
    business_entity_id: UUID | None = None
    to_recipients: list[str] = Field(default_factory=list)
    purpose: str = Field(default="", max_length=255)
    tone: str = Field(default="professional", max_length=64)


class EmailDraftEdit(BaseModel):
    """Editable fields applied when a user edits a draft (Requirement 32.5).

    Every field is optional; only explicitly-provided fields are applied.
    ``to_recipients``, when provided, is re-validated on the backend.
    """

    subject: str | None = Field(default=None, max_length=1024)
    body_text: str | None = None
    tone: str | None = Field(default=None, max_length=64)
    purpose: str | None = Field(default=None, max_length=255)
    to_recipients: list[str] | None = None


class EmailDraftSendRequest(BaseModel):
    """Request body of ``POST /api/email-drafts/{id}/send`` (Requirement 32.7).

    Sending requires a **separate explicit confirmation** distinct from
    approving the draft content: ``confirm`` must be ``true`` or the send is
    rejected and no Gmail send occurs. Email is never sent automatically
    (Requirement 32.10).
    """

    confirm: bool = False


class ReferencedFactView(BaseModel):
    """A grounded fact the draft leans on (Requirement 32.1).

    Each carries a ``source_id`` present in the context the backend supplied to
    the provider; the service drops any fabricated id before persisting.
    """

    source_type: str
    source_id: UUID
    evidence: str
    timestamp: datetime | None = None


class EmailDraftView(BaseModel):
    """A token-safe projection of an :class:`EmailDraft` (Requirement 32).

    Exposes the full lifecycle state a user needs to review, edit, approve,
    create, and send a draft — including backend-resolved recipients, grounded
    referenced facts, warnings, status, and the external Gmail ids. No token or
    secret is ever part of this schema.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    user_id: UUID
    integration_connection_id: UUID
    source_item_id: UUID | None
    business_entity_id: UUID | None
    gmail_thread_id: str | None
    to_recipients: list[str]
    subject: str
    body_text: str
    tone: str
    purpose: str
    referenced_facts: list[ReferencedFactView]
    warnings: list[str]
    status: EmailDraftStatus
    gmail_draft_id: str | None
    gmail_sent_message_id: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_draft(cls, draft: object) -> "EmailDraftView":
        """Build a view mapping the ``*_json`` columns to public list fields."""

        return cls(
            id=draft.id,
            organization_id=draft.organization_id,
            user_id=draft.user_id,
            integration_connection_id=draft.integration_connection_id,
            source_item_id=draft.source_item_id,
            business_entity_id=draft.business_entity_id,
            gmail_thread_id=draft.gmail_thread_id,
            to_recipients=list(draft.to_recipients_json or []),
            subject=draft.subject,
            body_text=draft.body_text,
            tone=draft.tone,
            purpose=draft.purpose,
            referenced_facts=[
                ReferencedFactView(**fact)
                for fact in (draft.referenced_facts_json or [])
            ],
            warnings=list(draft.warnings_json or []),
            status=draft.status,
            gmail_draft_id=draft.gmail_draft_id,
            gmail_sent_message_id=draft.gmail_sent_message_id,
            created_at=draft.created_at,
            updated_at=draft.updated_at,
        )


# ---------------------------------------------------------------------------
# Privacy, control & deletion (M6.7, Requirement 33)
# ---------------------------------------------------------------------------


class EmailDataDeleteRequest(BaseModel):
    """Request body of ``DELETE /api/privacy/email-data`` (Requirement 33.3).

    ``scope`` chooses what to delete:

    * ``ALL`` — every ingested :class:`EmailMessageRecord` in the organization.
    * ``CONNECTION`` — only records for ``connection_id`` (required for this
      scope).
    * ``SELECTED`` — only the records in ``record_ids`` (required for this
      scope).
    """

    scope: Literal["ALL", "CONNECTION", "SELECTED"] = "ALL"
    connection_id: UUID | None = None
    record_ids: list[UUID] = Field(default_factory=list)


class EmailDataDeleteResponse(BaseModel):
    """Summary of an email-data deletion cascade (Requirement 33.3, 33.8)."""

    deleted_records: int
    deleted_task_suggestions: int
    deleted_chunks: int
    marked_documents_source_deleted: int
    retained_derived_records: int


class RetentionPolicyUpdate(BaseModel):
    """Request body of ``PUT /api/privacy/retention-policy`` (Requirement 33.5).

    ``mode`` selects whether raw message content is retained alongside the
    extracted text; ``retention_window_days`` optionally bounds how long raw
    content is kept.
    """

    mode: RawEmailRetentionMode
    retention_window_days: int | None = Field(default=None, ge=1, le=3650)


class RetentionPolicyView(BaseModel):
    """A view of the persisted raw-email retention policy (Requirement 33.5)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    user_id: UUID
    mode: RawEmailRetentionMode
    retention_window_days: int | None
    created_at: datetime
    updated_at: datetime


class SyncStatusView(BaseModel):
    """A connection's last-sync status surfaced by the Privacy page (33.6)."""

    connection_id: UUID
    service: IntegrationService
    account_email: str
    status: ConnectionStatus
    last_sync_at: datetime | None
    last_error: str | None


class AnswerCitationView(BaseModel):
    """One citation/evidence item recorded for a Copilot answer (33.7)."""

    source_type: str
    source_id: UUID
    title: str
    evidence_excerpt: str
    timestamp: datetime | None = None
    deep_link: str


class AnswerProvenanceView(BaseModel):
    """The exact data that grounded a Copilot answer (Requirement 33.7).

    Returns the persisted question, answer, intent, and both the citations
    returned to the user and the full bounded evidence set supplied to the
    model — so a user can see precisely what data was used for an AI answer.
    """

    answer_id: UUID
    question: str
    answer: str | None
    intent: str
    insufficient_evidence: bool
    citations: list[AnswerCitationView]
    evidence: list[AnswerCitationView]
    created_at: datetime


__all__ = [
    "IntegrationConnectionView",
    "AuthorizationRedirectResponse",
    "InitialSyncOptions",
    "SyncRunResponse",
    "EmailMessageRecordView",
    "EmailTaskSuggestionView",
    "EmailTaskSuggestionEdit",
    "SenderSignalRequest",
    "SenderSignalView",
    "CalendarView",
    "CalendarAddRequest",
    "CalendarUpdateRequest",
    "CalendarDailyBriefBlockRequest",
    "CalendarEventLinkView",
    "DocumentAssetView",
    "CopilotAskRequest",
    "Citation",
    "SuggestedArtifact",
    "CopilotResponse",
    "SuggestedQuestionsResponse",
    "CopilotConfirmRequest",
    "CopilotConfirmResponse",
    "EmailDraftRequest",
    "EmailDraftEdit",
    "EmailDraftSendRequest",
    "ReferencedFactView",
    "EmailDraftView",
    "EmailDataDeleteRequest",
    "EmailDataDeleteResponse",
    "RetentionPolicyUpdate",
    "RetentionPolicyView",
    "SyncStatusView",
    "AnswerCitationView",
    "AnswerProvenanceView",
]
