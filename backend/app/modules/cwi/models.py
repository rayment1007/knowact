"""Connected Workspace Intelligence (CWI) data models.

This module defines the enumerations and SQLAlchemy ORM models for the CWI
extension (Requirements 23-34). Phase M6.1 introduces the
:class:`IntegrationConnection` — an authorized connection between one user
(within one organization) and one Google service — together with its
enumerations.

Security-critical conventions
-----------------------------
* **Tokens are encrypted at rest.** ``access_token_encrypted`` and
  ``refresh_token_encrypted`` store only Fernet ciphertext (``LargeBinary``);
  plaintext tokens never touch the database, logs, or any response schema
  (Requirements 25.1-25.4). These columns are excluded from every Pydantic
  response view (see :mod:`app.modules.cwi.schemas`).
* **Org + user scoping.** Every connection carries ``organization_id`` (via
  :class:`~app.database.OrganizationScopedMixin`) and ``user_id`` so isolation
  is enforced by the same service-layer helpers as the Core Engine
  (Requirement 25.5).

Conventions mirror :mod:`app.core.models`:
UUID primary keys, timezone-aware ``created_at`` / ``updated_at``, and JSONB for
flexible payloads. Foreign keys are declared against string table targets
(``"organizations.id"``, ``"users.id"``) so this module needs no import of the
core models, keeping model import order flexible for Alembic.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from datetime import date

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Integer, JSON
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.config import get_settings
from app.core.models import Sensitivity, SuggestionStatus
from app.database import Base, OrganizationScopedMixin

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class IntegrationProvider(str, enum.Enum):
    """The external identity/data provider a connection belongs to."""

    GOOGLE = "GOOGLE"


class IntegrationService(str, enum.Enum):
    """The specific Google service a connection authorizes."""

    GMAIL = "GMAIL"
    GOOGLE_CALENDAR = "GOOGLE_CALENDAR"


class ConnectionStatus(str, enum.Enum):
    """Lifecycle status of an :class:`IntegrationConnection`."""

    CONNECTED = "CONNECTED"  # active; tokens valid or refreshable
    EXPIRED = "EXPIRED"  # token refresh failed; needs re-authorization
    REVOKED = "REVOKED"  # user disconnected/revoked; future sync stopped
    ERROR = "ERROR"  # a non-recoverable error occurred


class SenderSignalType(str, enum.Enum):
    """The kind of negative signal a user attaches to a sender or domain.

    Marking a sender/domain ``PERSONAL`` or ``IRRELEVANT`` records a negative
    signal that future Gmail classification reuses to keep noise out of the
    knowledge base (Requirement 27.7).
    """

    PERSONAL = "PERSONAL"
    IRRELEVANT = "IRRELEVANT"


class CalendarSyncStatus(str, enum.Enum):
    """Lifecycle of a :class:`CalendarEventLink`'s sync with Google Calendar.

    A link is created ``PENDING`` and becomes ``SYNCED`` once the Google event
    exists. ``UPDATE_PENDING`` / ``CANCEL_PENDING`` mark an in-flight mutation,
    ``CANCELLED`` a successfully deleted event, and ``FAILED`` a sync error the
    user can retry (Requirements 28.5, 28.6).
    """

    PENDING = "PENDING"
    SYNCED = "SYNCED"
    UPDATE_PENDING = "UPDATE_PENDING"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class DocumentProcessingStatus(str, enum.Enum):
    """Lifecycle of a :class:`DocumentAsset` from upload to indexed (Req 29).

    A document is created ``UPLOADED`` (binary in the ``StorageBackend``, no
    chunks yet). :meth:`DocumentService.process` walks it through the parse →
    chunk → embed steps and lands it at ``INDEXED`` when its
    :class:`DocumentChunk` rows and embeddings are persisted (Requirement 29.3).
    ``FAILED`` records an unrecoverable processing error, leaving the upload
    retriable.
    """

    UPLOADED = "UPLOADED"
    PARSING = "PARSING"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    INDEXED = "INDEXED"
    FAILED = "FAILED"


class RawEmailRetentionMode(str, enum.Enum):
    """The raw-email retention policy a user chooses (M6.7, Requirement 33.5).

    ``RAW_AND_EXTRACTED`` keeps the raw imported message content alongside the
    extracted text; ``EXTRACTED_ONLY`` keeps only the extracted text and drops
    raw content. The choice controls whether the ``stored_raw`` flag on an
    ingested :class:`EmailMessageRecord` may remain set.
    """

    RAW_AND_EXTRACTED = "RAW_AND_EXTRACTED"
    EXTRACTED_ONLY = "EXTRACTED_ONLY"


class EmailDraftStatus(str, enum.Enum):
    """Lifecycle status of an AI-assisted Gmail draft (M6.6, Requirement 32.10).

    A draft is created ``AI_SUGGESTED`` from the provider's structured output,
    approved by a human (``USER_APPROVED``), optionally materialized as a real
    Gmail draft (``GMAIL_DRAFT_CREATED``), and — only after a *separate* explicit
    confirmation — sent. ``SENDING`` is the local in-flight guard: a single-row
    atomic compare-and-set moves the draft into ``SENDING`` before any Gmail
    send, so a concurrent local request does not issue another call. Because a
    transport failure can leave Gmail's outcome unknown, ``FAILED`` is terminal
    and requires checking Gmail Sent rather than blind retry. ``REJECTED`` is
    also terminal. Email is **never** sent automatically.
    """

    AI_SUGGESTED = "AI_SUGGESTED"
    USER_APPROVED = "USER_APPROVED"
    GMAIL_DRAFT_CREATED = "GMAIL_DRAFT_CREATED"
    SENDING = "SENDING"
    SENT = "SENT"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Portable vector column (SQLite tests / PostgreSQL production)
# ---------------------------------------------------------------------------


class PortableVector(TypeDecorator):
    """A dialect-portable embedding column: pgvector on PG, JSON elsewhere.

    Production runs on PostgreSQL where the column is a real ``pgvector``
    ``Vector(dim)`` — enabling ANN indexes and the ``<=>`` distance operators.
    The test suite runs on in-memory SQLite, which has no ``vector`` type, so on
    any non-PostgreSQL dialect the same column degrades to a ``JSON`` array of
    floats. This keeps the security-critical retrieval filtering (Property 18)
    fully exercisable on SQLite: :class:`RetrievalService` applies its filters as
    ordinary SQL ``WHERE`` predicates and ranks the surviving rows by cosine
    similarity in Python, so behavior is identical on both backends.

    The embedding is always represented in Python as a ``list[float]`` of length
    :attr:`dim`.
    """

    impl = JSON
    cache_ok = True

    def __init__(self, dim: int, *args, **kwargs) -> None:
        self.dim = dim
        super().__init__(*args, **kwargs)

    def load_dialect_impl(self, dialect):  # type: ignore[override]
        if dialect.name == "postgresql":
            # Import lazily so SQLite-only test runs never require the package
            # to be importable at module load time.
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):  # type: ignore[override]
        if value is None:
            return None
        # pgvector's Vector accepts a plain list; JSON stores the list as-is.
        return [float(component) for component in value]

    def process_result_value(self, value, dialect):  # type: ignore[override]
        if value is None:
            return None
        return [float(component) for component in value]


def _embedding_dimension() -> int:
    """Return the configured embedding dimension (default 1536)."""

    return get_settings().embedding_dimension


# A ``'{}'::jsonb`` server default captured at module scope. ``DocumentChunk``
# declares a column literally named ``text``, which shadows ``sqlalchemy.text``
# inside its class body, so the clause is built here instead.
_EMPTY_JSONB = text("'{}'::jsonb")


# ---------------------------------------------------------------------------
# Column helpers (mirror app/core/models.py conventions)
# ---------------------------------------------------------------------------


def _uuid_pk() -> Mapped[uuid.UUID]:
    """A UUID primary key column defaulting to a fresh ``uuid4``."""

    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


def _created_at() -> Mapped[datetime]:
    """A timezone-aware ``created_at`` column defaulting to ``now()``."""

    return mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


def _updated_at() -> Mapped[datetime]:
    """A timezone-aware ``updated_at`` column maintained on every update."""

    return mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ---------------------------------------------------------------------------
# IntegrationConnection
# ---------------------------------------------------------------------------


class IntegrationConnection(OrganizationScopedMixin, Base):
    """An authorized connection between one user and one Google service.

    Sign-in identity is deliberately **separate** from this: an
    ``IntegrationConnection`` is created only when a user *incrementally*
    authorizes a specific service scope (Gmail or Calendar), never at basic
    Google sign-in (Requirements 23.4, 24.3).

    The ``access_token_encrypted`` / ``refresh_token_encrypted`` columns hold
    only Fernet ciphertext and are never serialized into any API response
    (Requirements 25.1-25.3). ``granted_scopes_json`` records exactly the scopes
    Google returned (Requirement 24.3).
    """

    __tablename__ = "integration_connections"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # ``organization_id`` is contributed by OrganizationScopedMixin.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    provider: Mapped[IntegrationProvider] = mapped_column(
        SAEnum(IntegrationProvider, name="integration_provider"),
        nullable=False,
    )
    service: Mapped[IntegrationService] = mapped_column(
        SAEnum(IntegrationService, name="integration_service"),
        nullable=False,
    )
    # Google account identifier (the OIDC ``sub``) the connection authorizes.
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Connected mailbox / calendar account email.
    account_email: Mapped[str] = mapped_column(String(320), nullable=False)
    # list[str] scopes actually granted by Google (not merely requested).
    granted_scopes_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # Fernet ciphertext — NEVER serialized into any response schema.
    access_token_encrypted: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False
    )
    refresh_token_encrypted: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[ConnectionStatus] = mapped_column(
        SAEnum(ConnectionStatus, name="connection_status"),
        nullable=False,
        default=ConnectionStatus.CONNECTED,
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Gmail historyId / page token or Calendar sync token for incremental sync.
    sync_cursor: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Human-readable last error, recorded WITHOUT any secret content.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        # One connection per (org, user, provider, service, account).
        UniqueConstraint(
            "organization_id",
            "user_id",
            "provider",
            "service",
            "external_account_id",
            name="uq_integration_connection_identity",
        ),
    )


# ---------------------------------------------------------------------------
# EmailMessageRecord (M6.2)
# ---------------------------------------------------------------------------


class EmailMessageRecord(OrganizationScopedMixin, Base):
    """Metadata + provenance for an ingested Gmail message (Requirement 27.1).

    The raw binary / attachments are **never** stored in Postgres — only
    metadata and (optionally, per storage policy) extracted text. Each record
    links back to the originating :class:`IntegrationConnection` and forward to
    the :class:`~app.core.models.SourceItem` created in the existing pipeline.

    Two structural guards uphold dedup / idempotency (Requirement 27.2 /
    Property 15):

    * a unique constraint on ``(organization_id, integration_connection_id,
      gmail_message_id)`` so the same Gmail message never yields two records for
      one connection; and
    * a unique ``(integration_connection_id, content_hash)`` constraint that
      makes content dedup both mailbox-scoped and race-safe.
    """

    __tablename__ = "email_message_records"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # ``organization_id`` is contributed by OrganizationScopedMixin.
    integration_connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integration_connections.id"),
        nullable=False,
        index=True,
    )
    source_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_items.id"), nullable=True
    )
    gmail_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    gmail_thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sender: Mapped[str] = mapped_column(String(320), nullable=False)
    recipients_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    subject: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    labels_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    has_attachments: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    stored_raw: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "integration_connection_id",
            "gmail_message_id",
            name="uq_email_message_connection_gmail_id",
        ),
        UniqueConstraint(
            "integration_connection_id",
            "content_hash",
            name="uq_email_message_connection_content_hash",
        ),
    )


# ---------------------------------------------------------------------------
# EmailTaskSuggestion (M6.2)
# ---------------------------------------------------------------------------


class EmailTaskSuggestion(OrganizationScopedMixin, Base):
    """An AI-extracted task suggestion from an ingested Gmail message.

    Persisted in status ``SUGGESTED`` immediately after generation — never
    auto-confirmed (Requirement 27.6 / Property 20). It carries the full
    evidence-backed provenance a human needs to confirm/edit/reject/dismiss it:
    the title/description, a suggested due date and owner hint, the related
    entity, the source ``gmail_message_id``, the supporting evidence text, and
    the AI provider + model metadata that produced it.
    """

    __tablename__ = "email_task_suggestions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # ``organization_id`` is contributed by OrganizationScopedMixin.
    email_message_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("email_message_records.id"),
        nullable=False,
        index=True,
    )
    source_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_items.id"), nullable=True
    )
    business_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_entities.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Free-text owner hint (e.g. a name) as returned by the AI provider.
    suggested_owner: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # Human-readable related entity name, retained even when no entity is linked.
    related_entity_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    gmail_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    ai_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    ai_model: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[SuggestionStatus] = mapped_column(
        SAEnum(SuggestionStatus, name="suggestion_status"),
        nullable=False,
        default=SuggestionStatus.SUGGESTED,
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        Index(
            "ix_email_task_suggestions_org_status",
            "organization_id",
            "status",
        ),
    )


# ---------------------------------------------------------------------------
# EmailSenderSignal (M6.2)
# ---------------------------------------------------------------------------


class EmailSenderSignal(OrganizationScopedMixin, Base):
    """A negative signal marking a sender address or domain (Requirement 27.7).

    When a user marks a sender or domain ``PERSONAL`` or ``IRRELEVANT``, this
    row is recorded (one active signal per user and pattern) and reused only by
    that user's future Gmail ingestion to bias classification away from the
    knowledge base. ``pattern`` is a lower-cased email address (``a@b.com``) or
    a bare domain (``b.com``).
    """

    __tablename__ = "email_sender_signals"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # ``organization_id`` is contributed by OrganizationScopedMixin.
    integration_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integration_connections.id"),
        nullable=True,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    pattern: Mapped[str] = mapped_column(String(320), nullable=False)
    signal_type: Mapped[SenderSignalType] = mapped_column(
        SAEnum(SenderSignalType, name="sender_signal_type"),
        nullable=False,
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "created_by",
            "pattern",
            name="uq_email_sender_signal_org_user_pattern",
        ),
    )


# ---------------------------------------------------------------------------
# CalendarEventLink (M6.3)
# ---------------------------------------------------------------------------


class CalendarEventLink(OrganizationScopedMixin, Base):
    """The bridge between a confirmed :class:`ActionItem` and a Google event.

    The :class:`~app.core.models.ActionItem` remains the source of truth; this
    row records the externally-created Google Calendar event and its sync state
    (Requirement 28.5). Creation is **idempotent**: a stable ``idempotency_key``
    derived from ``(action_item_id, google_calendar_id)`` is used both as the
    Google request idempotency key and, via the unique constraint on
    ``(organization_id, action_item_id, integration_connection_id,
    google_calendar_id)``, as a structural guard — so repeatedly adding the same
    confirmed action to the same calendar yields **at most one** link and **at
    most one** ``google_event_id`` (Requirement 28.7 / Property 16).

    ``action_item_id`` is nullable so the same table can also anchor the single
    optional recurring **Daily Brief** calendar block (Requirement 28.9), which
    has no originating action; action-derived links always carry it.
    """

    __tablename__ = "calendar_event_links"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # ``organization_id`` is contributed by OrganizationScopedMixin.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    action_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("action_items.id"), nullable=True
    )
    integration_connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integration_connections.id"),
        nullable=False,
        index=True,
    )
    google_calendar_id: Mapped[str] = mapped_column(String(320), nullable=False)
    # Set only after a successful Google create (Requirement 28.5).
    google_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Stable per (action_item, calendar) — prevents duplicate create (Req 28.7).
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    sync_status: Mapped[CalendarSyncStatus] = mapped_column(
        SAEnum(CalendarSyncStatus, name="calendar_sync_status"),
        nullable=False,
        default=CalendarSyncStatus.PENDING,
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Human-readable last error, recorded WITHOUT any secret content.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "action_item_id",
            "integration_connection_id",
            "google_calendar_id",
            name="uq_calendar_event_link_identity",
        ),
    )


# ---------------------------------------------------------------------------
# DocumentAsset & DocumentChunk (M6.4)
# ---------------------------------------------------------------------------


class DocumentAsset(OrganizationScopedMixin, Base):
    """An uploaded document, parsed/chunked/embedded once and retrieved selectively.

    The binary itself lives behind the :class:`StorageBackend` (local filesystem
    now, S3/MinIO later) and is referenced only by an opaque ``storage_key`` —
    it is **never** stored in Postgres (Requirement 29.1, 29.2). This row holds
    the metadata and a ``checksum`` (sha256) for dedup/integrity, the
    ``processing_status`` lifecycle, and the ``sensitivity`` that gates
    retrieval (reusing the Core Engine :class:`~app.core.models.Sensitivity`).

    ``source_deleted`` is a provenance flag: when the originating source is
    deleted but a derived confirmed record is retained, the flag records that
    the original source is gone without losing the confirmed fact (see the
    design's *Deletion cascade & derived-record provenance*).
    """

    __tablename__ = "document_assets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # ``organization_id`` is contributed by OrganizationScopedMixin.
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    # Opaque key into the StorageBackend — NOT a Postgres blob (Req 29.1).
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    processing_status: Mapped[DocumentProcessingStatus] = mapped_column(
        SAEnum(DocumentProcessingStatus, name="document_processing_status"),
        nullable=False,
        default=DocumentProcessingStatus.UPLOADED,
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    sensitivity: Mapped[Sensitivity] = mapped_column(
        SAEnum(Sensitivity, name="sensitivity"),
        nullable=False,
        default=Sensitivity.INTERNAL,
    )
    source_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        Index(
            "ix_document_assets_org_checksum",
            "organization_id",
            "checksum",
        ),
    )


class DocumentChunk(OrganizationScopedMixin, Base):
    """One embedded slice of a :class:`DocumentAsset` (Requirement 29.3).

    Persisted once during processing with the chunk ``text``, its position
    (``chunk_index`` / ``page_number``), flexible ``metadata_json`` (section,
    heading, offsets, source refs), and its ``embedding`` — a dialect-portable
    vector (pgvector on PostgreSQL, JSON on SQLite). Retrieval filters org /
    permission / source-type / sensitivity / entity as SQL predicates *before*
    ranking these chunks by cosine similarity (Requirement 29.4 / Property 18).
    """

    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # ``organization_id`` is contributed by OrganizationScopedMixin.
    document_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # section, heading, offsets, source refs — mapped to the ``metadata_json``
    # column (``metadata`` is reserved on the declarative base). The server
    # default is referenced via the module-level ``_EMPTY_JSONB`` clause because
    # the ``text`` column below shadows ``sqlalchemy.text`` in this class body.
    metadata_json: Mapped[dict] = mapped_column(
        "metadata_json",
        JSONB,
        nullable=False,
        default=dict,
        server_default=_EMPTY_JSONB,
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        PortableVector(_embedding_dimension()), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint(
            "document_asset_id",
            "chunk_index",
            name="uq_document_chunks_asset_index",
        ),
        Index(
            "ix_document_chunks_org_asset",
            "organization_id",
            "document_asset_id",
        ),
    )


# ---------------------------------------------------------------------------
# EmailDraft (M6.6)
# ---------------------------------------------------------------------------


class EmailDraft(OrganizationScopedMixin, Base):
    """The full lifecycle state of an AI-assisted Gmail draft (Requirement 32).

    Holds a structured suggested reply grounded in permission-filtered confirmed
    context, its human-review status, and the external Gmail ids once a draft is
    created and sent. AI draft content is **never** treated as confirmed
    knowledge unless explicitly confirmed through the normal knowledge workflow
    (Requirement 32.11).

    Security / correctness conventions:

    * **Backend-resolved recipients only.** ``to_recipients_json`` holds the
      addresses the *backend* resolved and validated; the LLM never invents
      them (Requirement 32.2).
    * **Duplicate-send guard.** ``send_idempotency_key`` is a stable per-draft
      correlation key used together with an atomic status compare-and-set into
      ``SENDING`` so only one local request initiates a send. An ambiguous Gmail
      transport failure is terminal locally because the API does not provide an
      idempotency guarantee for this request. ``gmail_sent_message_id`` is set
      only after a confirmed response.
    * **Org + user scoping.** Every row carries ``organization_id`` (mixin) and
      ``user_id``; a cross-org draft id is indistinguishable from a missing one
      and yields ``404`` (Requirement 32.12 / Property 13).
    """

    __tablename__ = "email_drafts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # ``organization_id`` is contributed by OrganizationScopedMixin.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    integration_connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integration_connections.id"),
        nullable=False,
        index=True,
    )
    # The Gmail-derived SourceItem the draft replies to (optional).
    source_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_items.id"), nullable=True
    )
    # The client/business entity the draft concerns (optional).
    business_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_entities.id"), nullable=True
    )
    gmail_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Backend-resolved/validated recipient addresses ONLY (never from the LLM).
    to_recipients_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    subject: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tone: Mapped[str] = mapped_column(String(64), nullable=False, default="professional")
    purpose: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # list[{source_type, source_id, evidence, timestamp}] — each grounded id.
    referenced_facts_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    warnings_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    status: Mapped[EmailDraftStatus] = mapped_column(
        SAEnum(EmailDraftStatus, name="email_draft_status"),
        nullable=False,
        default=EmailDraftStatus.AI_SUGGESTED,
    )
    # Set by users.drafts.create; nullable until a Gmail draft is materialized.
    gmail_draft_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Set only after a confirmed users.drafts.send response.
    gmail_sent_message_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    # Stable per-draft correlation key used by the local duplicate-send guard.
    send_idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        Index(
            "ix_email_drafts_org_status",
            "organization_id",
            "status",
        ),
    )


# ---------------------------------------------------------------------------
# RawEmailRetentionPolicy (M6.7, Requirement 33.5)
# ---------------------------------------------------------------------------


class RawEmailRetentionPolicy(OrganizationScopedMixin, Base):
    """A user's raw-email retention choice, persisted and applied (Req 33.5).

    One active policy per ``(organization_id, user_id)`` records whether raw
    imported message content is retained (``RAW_AND_EXTRACTED``) or dropped in
    favor of only the extracted text (``EXTRACTED_ONLY``), plus an optional
    ``retention_window_days`` after which raw content should not be kept. The
    :class:`~app.modules.cwi.services.privacy_service.PrivacyService` persists
    the policy and applies it to the ``stored_raw`` flag of the org's ingested
    :class:`EmailMessageRecord`s.
    """

    __tablename__ = "raw_email_retention_policies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # ``organization_id`` is contributed by OrganizationScopedMixin.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    mode: Mapped[RawEmailRetentionMode] = mapped_column(
        SAEnum(RawEmailRetentionMode, name="raw_email_retention_mode"),
        nullable=False,
        default=RawEmailRetentionMode.EXTRACTED_ONLY,
    )
    # Optional window (days) after which raw content is not retained; ``None``
    # means no time-based expiry.
    retention_window_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_raw_email_retention_policy_org_user",
        ),
    )


# ---------------------------------------------------------------------------
# CopilotAnswerLog (M6.7, Requirement 33.7)
# ---------------------------------------------------------------------------


class CopilotAnswerLog(OrganizationScopedMixin, Base):
    """A persisted record of a Copilot answer and the evidence that grounded it.

    Backs "what data was used for an AI answer" (Requirement 33.7): each ``ask``
    persists the question, the returned answer, the intent, whether the evidence
    was insufficient, the exact citations returned, and the full bounded
    evidence set supplied to the model — so the Privacy_Service can later return
    the exact citations/evidence that grounded a given answer id. Org- and
    user-scoped; another user's or cross-org answer id is indistinguishable
    from a missing one.

    This is a transparency log, not a business mutation — it never writes an
    :class:`~app.core.models.AuditLog` row.
    """

    __tablename__ = "copilot_answer_logs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # ``organization_id`` is contributed by OrganizationScopedMixin.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent: Mapped[str] = mapped_column(String(16), nullable=False, default="ASK")
    insufficient_evidence: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # The exact citations returned to the user (list[{source_type, source_id,
    # title, evidence_excerpt, timestamp, deep_link}]).
    citations_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # The full bounded evidence set supplied to the model (same shape).
    evidence_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        Index(
            "ix_copilot_answer_logs_org_created",
            "organization_id",
            "created_at",
        ),
    )


__all__ = [
    "IntegrationProvider",
    "IntegrationService",
    "ConnectionStatus",
    "IntegrationConnection",
    "SenderSignalType",
    "EmailMessageRecord",
    "EmailTaskSuggestion",
    "EmailSenderSignal",
    "CalendarSyncStatus",
    "CalendarEventLink",
    "DocumentProcessingStatus",
    "PortableVector",
    "DocumentAsset",
    "DocumentChunk",
    "EmailDraftStatus",
    "EmailDraft",
    "RawEmailRetentionMode",
    "RawEmailRetentionPolicy",
    "CopilotAnswerLog",
]
