"""Pydantic schemas for the Core Engine API.

This module defines the request and response models used by the API routers.
It currently covers authentication (Requirement 1); further resource schemas
are added alongside their routers in later tasks.

Two conventions are applied throughout:

* **ORM serialization.** Response models set ``model_config =
  ConfigDict(from_attributes=True)`` (Pydantic v2) so they can be constructed
  directly from SQLAlchemy ORM instances via ``Model.model_validate(orm_obj)``.
* **Password hashes are never exposed.** The :class:`UserResponse` schema
  deliberately omits ``password_hash``. Because responses are declared with
  explicit schemas, the hash stored on the ``User`` ORM model can never leak
  into a response body (Requirements 1.3, 1.6).
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.models import (
    ActionStatus,
    BusinessCategory,
    BusinessEntityType,
    Relevance,
    Sensitivity,
    SourceStatus,
    SourceType,
    SuggestionStatus,
)


# ---------------------------------------------------------------------------
# Auth requests
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    """Request body of ``POST /api/auth/login``."""

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=72)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("password")
    @classmethod
    def enforce_bcrypt_byte_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes.")
        return value


# ---------------------------------------------------------------------------
# Auth responses
# ---------------------------------------------------------------------------


class UserResponse(BaseModel):
    """The authenticated user, safe for API responses.

    Mirrors :class:`app.core.models.User` but **never** includes
    ``password_hash`` (Requirements 1.3, 1.6).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    email: str
    full_name: str
    role: str
    created_at: datetime


class OrganizationResponse(BaseModel):
    """An organization (tenant), safe for API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    created_at: datetime


class LoginResponse(BaseModel):
    """Response body of ``POST /api/auth/login``.

    Authentication is cookie-based: on success the signed JWT is set as an
    HTTP-only cookie on the response and is **never** included in this body.
    The body carries only the safe user projection so the client can populate
    its session immediately without a follow-up request.
    """

    user: UserResponse


class CurrentUserResponse(BaseModel):
    """Response body of ``GET /api/auth/me``: the current user + organization."""

    user: UserResponse
    organization: OrganizationResponse


# ---------------------------------------------------------------------------
# Source items (Requirement 3)
# ---------------------------------------------------------------------------


class SourceItemCreate(BaseModel):
    """Request body of ``POST /api/source-items``.

    The payload carries only user-supplied content; ``organization_id`` and
    ``created_by`` are derived from the authenticated session, never trusted
    from the client. ``title`` and ``content`` must be non-empty — an invalid
    payload is rejected by Pydantic with a ``422`` before the service runs, so
    no partial source item is ever persisted (Requirement 3.5).
    """

    source_type: SourceType
    title: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1)
    received_at: datetime | None = None


class InboxFilter(BaseModel):
    """Optional filters for the inbox list (``GET /api/source-items``).

    Both fields are optional; an empty filter returns every source item for the
    caller's organization. Filtering is applied *in addition to* the mandatory
    organization scoping (Requirement 3.2).
    """

    status: SourceStatus | None = None
    business_category: BusinessCategory | None = None


class SourceItemResponse(BaseModel):
    """A source item, safe for API responses.

    Mirrors :class:`app.core.models.SourceItem` (Requirement 3.1).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    created_by: UUID
    source_type: SourceType
    title: str
    content: str
    status: SourceStatus
    received_at: datetime
    created_at: datetime


class ClassificationResultResponse(BaseModel):
    """A classification result, safe for API responses.

    Mirrors :class:`app.core.models.ClassificationResult`. Returned as part of
    a source item's detail view when a classification exists (Requirement 3.3);
    classifications themselves are produced in a later task.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_item_id: UUID
    relevance: Relevance
    business_category: BusinessCategory
    sensitivity: Sensitivity
    confidence: float
    reasons: list
    evidence_spans: list
    status: SuggestionStatus
    created_at: datetime


class SourceItemDetailResponse(BaseModel):
    """Response body of ``GET /api/source-items/{id}``.

    Carries the source item together with its current classification if one
    exists; ``classification`` is ``None`` for items that have not been
    classified yet (Requirement 3.3).
    """

    source_item: SourceItemResponse
    classification: ClassificationResultResponse | None = None


class ClassificationOverride(BaseModel):
    """Optional human edits applied when confirming a classification.

    Carries an override for any of the three classification axes; a field left
    ``None`` keeps the AI-suggested value (Requirement 5.2). An empty override
    (all fields ``None``) confirms the AI suggestion verbatim.
    """

    relevance: Relevance | None = None
    business_category: BusinessCategory | None = None
    sensitivity: Sensitivity | None = None


# ---------------------------------------------------------------------------
# Knowledge extraction (Requirement 6)
# ---------------------------------------------------------------------------


class KnowledgeExtractRequest(BaseModel):
    """Request body of ``POST /api/source-items/{id}/extract``.

    Carries only the explicit sensitivity acknowledgment. Extraction of a
    ``HIGHLY_SENSITIVE`` source item requires ``acknowledged=True`` (Requirements
    6.4, 6.5); it defaults to ``False`` so unacknowledged highly-sensitive
    content is refused by the sensitivity gate. The field is ignored for content
    that is not highly sensitive.
    """

    acknowledged: bool = False


# ---------------------------------------------------------------------------
# Knowledge hub (Requirement 7)
# ---------------------------------------------------------------------------


class KnowledgeListFilter(BaseModel):
    """Optional filters for the knowledge list (``GET /api/knowledge``).

    Both fields are optional; an empty filter returns every knowledge item for
    the caller's organization. Filtering is applied *in addition to* the
    mandatory organization scoping (Requirement 7.3). ``business_entity_id``
    narrows to a single connected entity; ``status`` narrows by suggestion
    status (e.g. only ``CONFIRMED`` items).
    """

    business_entity_id: UUID | None = None
    status: SuggestionStatus | None = None


class KnowledgeItemResponse(BaseModel):
    """A knowledge item, safe for API responses.

    Mirrors :class:`app.core.models.KnowledgeItem` and always carries its
    ``evidence_text`` (Requirement 7.5).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    business_entity_id: UUID | None
    source_item_id: UUID | None
    summary: str
    key_points: list
    evidence_text: str
    knowledge_type: str
    status: SuggestionStatus
    created_at: datetime
    updated_at: datetime


class ActionItemResponse(BaseModel):
    """An action item, safe for API responses.

    Mirrors :class:`app.core.models.ActionItem`. Used here to surface the
    actions linked to a knowledge item in its detail view (Requirement 7.4).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    business_entity_id: UUID | None
    knowledge_item_id: UUID | None
    source_meeting_id: UUID | None
    title: str
    description: str | None
    owner_id: UUID | None
    due_date: date | None
    status: ActionStatus
    evidence_text: str | None
    ai_generated: bool
    created_at: datetime


class DecisionRecordResponse(BaseModel):
    """A decision record, safe for API responses.

    Mirrors :class:`app.core.models.DecisionRecord`. Used here to surface the
    decisions linked to a knowledge item in its detail view (Requirement 7.4).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    business_entity_id: UUID | None
    title: str
    decision: str
    rationale: str
    evidence_text: str
    decided_by: UUID
    decided_at: datetime


class KnowledgeDetailResponse(BaseModel):
    """Response body of ``GET /api/knowledge/{id}``.

    Carries the knowledge item together with the actions and decisions linked to
    it — either directly (an :class:`ActionItem` whose ``knowledge_item_id``
    points at this item) or through the shared business entity — so the hub can
    show a knowledge item's full context with its evidence (Requirements 7.4,
    7.5).
    """

    knowledge_item: KnowledgeItemResponse
    linked_actions: list[ActionItemResponse] = Field(default_factory=list)
    linked_decisions: list[DecisionRecordResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Action Center (Requirement 8)
# ---------------------------------------------------------------------------


class ActionCreate(BaseModel):
    """Request body of ``POST /api/actions``.

    Creates a single action item. The origin is inferred from the payload
    (Requirements 8.1, 8.2, 8.5):

    * When ``knowledge_item_id`` references a **confirmed** knowledge item, the
      action is created *from that suggestion* — it is marked AI-generated and
      inherits the suggestion's evidence and business entity unless overridden
      here (Requirement 8.1).
    * Otherwise the action is **manually created** and marked human-originated
      (Requirement 8.2).

    ``title`` must be non-empty; an invalid payload is rejected with ``422``
    before the service runs, so no partial action is persisted.
    """

    title: str = Field(min_length=1, max_length=512)
    description: str | None = None
    business_entity_id: UUID | None = None
    knowledge_item_id: UUID | None = None
    owner_id: UUID | None = None
    due_date: date | None = None
    evidence_text: str | None = None


class ActionUpdate(BaseModel):
    """Request body of ``PATCH /api/actions/{id}``.

    A partial update: every field is optional and only the fields explicitly
    provided are applied (unset fields are left untouched). Setting ``status`` to
    ``DONE`` marks an action complete (Requirement 8.4); other fields allow
    lightweight edits to an existing action.
    """

    status: ActionStatus | None = None
    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = None
    owner_id: UUID | None = None
    due_date: date | None = None
    business_entity_id: UUID | None = None


class ActionListFilter(BaseModel):
    """Optional filters for the action list (``GET /api/actions``).

    Both fields are optional; an empty filter returns every action for the
    caller's organization. Filtering is applied *in addition to* the mandatory
    organization scoping (Requirement 8.3). ``status`` narrows by lifecycle state
    (e.g. only ``OPEN`` work); ``business_entity_id`` narrows to a single entity.
    """

    status: ActionStatus | None = None
    business_entity_id: UUID | None = None


# ---------------------------------------------------------------------------
# Decision Memory (Requirement 9)
# ---------------------------------------------------------------------------


class DecisionCreate(BaseModel):
    """Request body of ``POST /api/decisions``.

    Records a decision with its statement, rationale, and evidence
    (Requirement 9.1). The *owner* of the decision (``decided_by``) is derived
    from the authenticated session — never trusted from the client — and the
    ``organization_id`` is likewise taken from the caller's tenant.

    The design's :class:`~app.core.models.DecisionRecord` is an immutable
    record of a decision *already made*: there is no separate mutable "status"
    column. The recorded ``decision`` statement itself is the outcome/status,
    so no status field is accepted or stored here (Requirement 9.1).

    ``title``, ``decision``, ``rationale``, and ``evidence_text`` must all be
    non-empty; an invalid payload is rejected by Pydantic with a ``422`` before
    the service runs, so no partial decision record is ever persisted. When a
    decision originates from an AI output the caller passes the originating
    text as ``evidence_text`` so it is persisted with the record (Requirement
    9.4). ``decided_at`` is optional and defaults to the time of recording.
    """

    title: str = Field(min_length=1, max_length=512)
    decision: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence_text: str = Field(min_length=1)
    business_entity_id: UUID | None = None
    decided_at: datetime | None = None


class DecisionListFilter(BaseModel):
    """Optional filters for the decision list (``GET /api/decisions``).

    The single field is optional; an empty filter returns every decision record
    for the caller's organization. Filtering is applied *in addition to* the
    mandatory organization scoping (Requirement 9.3). ``business_entity_id``
    narrows to the decisions connected to a single business entity.
    """

    business_entity_id: UUID | None = None


# ---------------------------------------------------------------------------
# Business entities (Requirement 10.5)
# ---------------------------------------------------------------------------


class BusinessEntityCreate(BaseModel):
    """Request body of ``POST /api/business-entities``.

    Creates a business entity — a domain object (client, project, vendor, …)
    that accumulates knowledge, actions, and decisions and can be the subject of
    an entity brief (Requirement 10.5). ``organization_id`` is derived from the
    authenticated session, never trusted from the client. ``name`` must be
    non-empty; an invalid payload is rejected by Pydantic with a ``422`` before
    the handler runs, so no partial entity is persisted. ``attributes`` carries
    flexible per-type fields and defaults to an empty object.
    """

    name: str = Field(min_length=1, max_length=255)
    entity_type: BusinessEntityType
    description: str | None = None
    attributes: dict = Field(default_factory=dict)


class BusinessEntityResponse(BaseModel):
    """A business entity, safe for API responses.

    Mirrors :class:`app.core.models.BusinessEntity`.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    entity_type: BusinessEntityType
    name: str
    description: str | None
    attributes: dict
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Briefs (Requirement 10)
# ---------------------------------------------------------------------------


class BriefResponse(BaseModel):
    """A generated brief, safe for API responses.

    Mirrors :class:`app.core.models.Brief` (Requirement 10.3). The ``content``
    is the structured brief produced by the AI provider — for a ``DAILY`` brief
    a headline with priorities, recommended actions, follow-ups, and risks; for
    an ``ENTITY`` brief a summary with recent knowledge, open actions, and
    recommended next steps — where each line references its source evidence
    (Requirement 10.4). It is typed loosely as a ``dict`` because its exact
    shape depends on the brief ``scope``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    scope: str
    scope_ref_id: UUID | None
    generated_for: UUID
    content: dict
    created_at: datetime


# ---------------------------------------------------------------------------
# Audit (Requirement 13)
# ---------------------------------------------------------------------------


class AuditFilter(BaseModel):
    """Optional filters for listing audit log entries.

    All fields are optional; an empty filter returns every audit entry for the
    caller's organization. Filtering is always applied *in addition to* the
    mandatory organization scoping enforced by
    :class:`~app.core.services.audit_service.AuditService`.
    """

    actor_id: UUID | None = None
    action_type: str | None = None
    target_type: str | None = None
    target_id: UUID | None = None


class AuditLogResponse(BaseModel):
    """A single audit log entry, safe for API responses.

    Mirrors :class:`app.core.models.AuditLog`: who did what to which object,
    when, with free-form ``detail`` (Requirement 13.2).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    actor_id: UUID
    action_type: str
    target_type: str
    target_id: UUID
    detail: dict
    created_at: datetime
