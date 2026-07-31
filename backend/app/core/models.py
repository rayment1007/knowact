"""Core Engine data models.

This module defines the enumerations and SQLAlchemy ORM models that make up the
Core Engine's persistent domain (Requirements 2.1, 3.1, 4.5, 6.6, 8.5, 9.1,
13.2). It implements the "Data Models" section of the design document.

Conventions
-----------
* **UUID primary keys.** Every table uses a server-agnostic ``uuid4`` primary
  key, stored using PostgreSQL's native ``UUID`` type.
* **Timestamps.** ``created_at`` (and ``updated_at`` where the design shows it)
  are timezone-aware and default to the database ``now()``.
* **Flexible payloads.** Free-form structured fields (reasons, evidence spans,
  key points, attributes, content, detail) use PostgreSQL ``JSONB`` columns.
* **Multi-tenancy.** Org-scoped models inherit ``OrganizationScopedMixin`` from
  :mod:`app.database`, which contributes an indexed ``organization_id`` FK.
  ``Organization`` itself is the tenant root and is therefore not scoped, and
  ``ClassificationResult`` derives its tenant via its parent ``SourceItem`` and
  so also omits its own ``organization_id`` (matching the design).
* **Enumerations.** Classification is split into three orthogonal axes —
  ``relevance``, ``business_category``, and ``sensitivity`` — each persisted as
  a native PostgreSQL enum.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, OrganizationScopedMixin

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SourceType(str, enum.Enum):
    """The origin format of a collected :class:`SourceItem`."""

    EMAIL = "EMAIL"
    DOCUMENT = "DOCUMENT"
    MEETING_NOTE = "MEETING_NOTE"
    TASK_NOTE = "TASK_NOTE"
    CHAT = "CHAT"
    MANUAL = "MANUAL"


class SourceStatus(str, enum.Enum):
    """Lifecycle status of a :class:`SourceItem`."""

    NEW = "NEW"  # collected, not classified
    CLASSIFIED = "CLASSIFIED"  # classification confirmed
    PROCESSED = "PROCESSED"  # knowledge extracted & confirmed
    ARCHIVED = "ARCHIVED"
    DISMISSED = "DISMISSED"  # rejected as irrelevant


class Relevance(str, enum.Enum):
    """Axis 1 of classification: is this work content or noise?"""

    WORK_RELATED = "WORK_RELATED"
    PERSONAL = "PERSONAL"
    IRRELEVANT = "IRRELEVANT"
    SPAM = "SPAM"
    SYSTEM_NOTIFICATION = "SYSTEM_NOTIFICATION"


class BusinessCategory(str, enum.Enum):
    """Axis 2 of classification: what kind of business content is it?"""

    CLIENT = "CLIENT"
    PROJECT = "PROJECT"
    DECISION = "DECISION"
    TASK = "TASK"
    RISK = "RISK"
    MEETING = "MEETING"
    PARTNER = "PARTNER"
    LEARNING = "LEARNING"
    OTHER = "OTHER"


class Sensitivity(str, enum.Enum):
    """Axis 3 of classification: how protected is this content?"""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    HIGHLY_SENSITIVE = "HIGHLY_SENSITIVE"


class SuggestionStatus(str, enum.Enum):
    """Human-in-the-loop status shared by AI-produced artifacts."""

    SUGGESTED = "SUGGESTED"  # AI produced, awaiting human
    CONFIRMED = "CONFIRMED"  # human accepted
    REJECTED = "REJECTED"  # human declined (kept as signal)


class ActionStatus(str, enum.Enum):
    """Lifecycle status of an :class:`ActionItem`."""

    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class BusinessEntityType(str, enum.Enum):
    """The kind of domain object a :class:`BusinessEntity` represents."""

    CLIENT = "CLIENT"
    PROJECT = "PROJECT"
    DEPARTMENT = "DEPARTMENT"
    VENDOR = "VENDOR"
    PARTNER = "PARTNER"
    ACCOUNT = "ACCOUNT"
    PROCESS = "PROCESS"


# ---------------------------------------------------------------------------
# Column helpers
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
# Core models
# ---------------------------------------------------------------------------


class Organization(Base):
    """The tenant root. Every other domain row is scoped to one of these."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class User(OrganizationScopedMixin, Base):
    """An authenticated member of an organization."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(
        String(320), nullable=False, unique=True, index=True
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Never exposed by any response schema (Requirement 1.6).
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # "ADMIN" | "ADVISOR" | "MEMBER"
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class BusinessEntity(OrganizationScopedMixin, Base):
    """A domain object that accumulates knowledge, actions, and decisions."""

    __tablename__ = "business_entities"

    id: Mapped[uuid.UUID] = _uuid_pk()
    entity_type: Mapped[BusinessEntityType] = mapped_column(
        SAEnum(BusinessEntityType, name="business_entity_type"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Flexible per-type fields.
    attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class SourceItem(OrganizationScopedMixin, Base):
    """A collected unit of raw organizational content."""

    __tablename__ = "source_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    source_type: Mapped[SourceType] = mapped_column(
        SAEnum(SourceType, name="source_type"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # raw text
    status: Mapped[SourceStatus] = mapped_column(
        SAEnum(SourceStatus, name="source_status"),
        nullable=False,
        default=SourceStatus.NEW,
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        # Inbox queries filter by organization and status (Requirement 3.2).
        Index("ix_source_items_org_status", "organization_id", "status"),
    )


class ClassificationResult(Base):
    """AI classification of a source item across three orthogonal axes.

    Not org-scoped directly: it inherits its tenant from the parent
    ``SourceItem`` (matching the design, which gives it only ``source_item_id``).

    Rather than a global unique constraint on ``source_item_id``, we enforce
    Requirement 4.5 ("at most one non-rejected result per item") with a
    *partial* unique index that ignores ``REJECTED`` rows. This lets rejected
    classifications be retained as negative signals (Requirement 5.3) while
    still guaranteeing a single active classification at any time.
    """

    __tablename__ = "classification_results"

    id: Mapped[uuid.UUID] = _uuid_pk()
    source_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_items.id"), nullable=False
    )
    relevance: Mapped[Relevance] = mapped_column(
        SAEnum(Relevance, name="relevance"), nullable=False
    )
    business_category: Mapped[BusinessCategory] = mapped_column(
        SAEnum(BusinessCategory, name="business_category"), nullable=False
    )
    sensitivity: Mapped[Sensitivity] = mapped_column(
        SAEnum(Sensitivity, name="sensitivity"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)  # 0.0 - 1.0
    # list[str] evidence-backed reasons.
    reasons: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # list[{text, start, end}].
    evidence_spans: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    status: Mapped[SuggestionStatus] = mapped_column(
        SAEnum(SuggestionStatus, name="suggestion_status"),
        nullable=False,
        default=SuggestionStatus.SUGGESTED,
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        Index(
            "uq_classification_active_per_source",
            "source_item_id",
            unique=True,
            postgresql_where=text("status != 'REJECTED'"),
            # SQLite also supports partial indexes; mirroring the predicate here
            # keeps the "at most one non-rejected result per item" invariant
            # (Requirement 4.5) identical on the SQLite-backed test database,
            # where rejected rows must be able to coexist with a new active one.
            sqlite_where=text("status != 'REJECTED'"),
        ),
    )


class KnowledgeItem(OrganizationScopedMixin, Base):
    """A structured, evidence-backed piece of knowledge."""

    __tablename__ = "knowledge_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_entities.id"), nullable=True
    )
    source_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_items.id"), nullable=True
    )
    # If promoted from a confirmed advisor meeting; a plain nullable UUID (no
    # hard FK to any meetings table, mirroring ``ActionItem.source_meeting_id``,
    # to avoid a cross-module import cycle). Client-memory items are keyed by
    # this so each meeting preserves its own memory row (per-meeting history).
    source_meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    # list[str].
    key_points: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    # "FACT" | "CONTEXT" | "MEMORY".
    knowledge_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[SuggestionStatus] = mapped_column(
        SAEnum(SuggestionStatus, name="suggestion_status"),
        nullable=False,
        default=SuggestionStatus.SUGGESTED,
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        Index("ix_knowledge_items_org_status", "organization_id", "status"),
        # At most one knowledge item per (org, meeting, type) among meeting-
        # sourced rows: this makes the per-meeting client-memory upsert
        # (model A) a DB-level backstop against duplicate rows even under
        # concurrent confirms. A *partial* unique index (only rows where
        # ``source_meeting_id IS NOT NULL``) so the many rows with a NULL
        # ``source_meeting_id`` (all non-meeting knowledge) are unconstrained.
        # Mirrors ClassificationResult's partial index: both ``postgresql_where``
        # and ``sqlite_where`` are supplied so it behaves identically on the
        # SQLite-backed test database.
        Index(
            "uq_knowledge_items_meeting_memory",
            "organization_id",
            "source_meeting_id",
            "knowledge_type",
            unique=True,
            postgresql_where=text("source_meeting_id IS NOT NULL"),
            sqlite_where=text("source_meeting_id IS NOT NULL"),
        ),
    )


class ActionItem(OrganizationScopedMixin, Base):
    """A task derived from a confirmed recommendation or created manually."""

    __tablename__ = "action_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_entities.id"), nullable=True
    )
    knowledge_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id"), nullable=True
    )
    # If produced by a meeting (advisor module); FK added with that table.
    source_meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[ActionStatus] = mapped_column(
        SAEnum(ActionStatus, name="action_status"),
        nullable=False,
        default=ActionStatus.OPEN,
    )
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Whether this action originated from an AI suggestion (Requirement 8.5).
    ai_generated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        Index("ix_action_items_org_status", "organization_id", "status"),
    )


class DecisionRecord(OrganizationScopedMixin, Base):
    """A recorded decision with rationale, evidence, owner, and date."""

    __tablename__ = "decision_records"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_entities.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Brief(OrganizationScopedMixin, Base):
    """A generated structured summary (daily, entity, or meeting scope)."""

    __tablename__ = "briefs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # "DAILY" | "ENTITY" | "MEETING".
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    # entity/meeting the brief is about.
    scope_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    generated_for: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    # Structured brief content (headline, priorities, actions, ...).
    content: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = _created_at()


class AuditLog(OrganizationScopedMixin, Base):
    """An immutable record of a state-changing confirmation."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    # e.g. CONFIRM_CLASSIFICATION.
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # e.g. SourceItem.
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    detail: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = _created_at()
