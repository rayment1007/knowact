"""Source item routes (Requirement 3).

Exposes the collect side of the pipeline over HTTP:

* ``GET /api/source-items`` — list the organization's inbox, optionally
  filtered by ``status`` and ``category`` (Requirement 3.2).
* ``POST /api/source-items`` — collect a new source item; persisted in status
  ``NEW`` (Requirement 3.1). Invalid payloads are rejected by request
  validation with ``422`` and nothing is persisted (Requirement 3.5).
* ``GET /api/source-items/{id}`` — fetch a single item together with its
  current classification if one exists (Requirement 3.3).
* ``POST /api/source-items/{id}/dismiss`` — mark an item ``DISMISSED``
  (Requirement 3.4).

The classification endpoints (Requirements 4 and 5) are mounted on the same
``/source-items/{id}`` resource because they operate on a single item:

* ``POST /api/source-items/{id}/classify`` — run AI classification and persist a
  ``SUGGESTED`` result (Requirement 4.1). The provider call is routed through
  :func:`app.dependencies.call_with_fallback`, so a failing ``LLMProvider``
  falls back to the deterministic mock in ``DEVELOPMENT`` and surfaces a
  retriable ``503`` in ``PRODUCTION`` (Requirement 11.6) — never a silent
  downgrade or a partial write.
* ``POST /api/source-items/{id}/confirm-classification`` — confirm the
  suggestion (with an optional human override), setting the result to
  ``CONFIRMED`` and the item to ``CLASSIFIED`` in one audited transaction
  (Requirements 5.1, 5.2, 5.4).
* ``POST /api/source-items/{id}/reject-classification`` — reject the suggestion,
  retaining it as a negative signal (Requirement 5.3).

Access control and tenant scoping follow the Core Engine conventions: every
route is protected by :func:`app.dependencies.get_current_user` (missing/invalid
session → ``401``), and every query is scoped to the caller's ``organization_id``
so one tenant can never read or mutate another's items (cross-tenant access →
``404``). All paths are mounted under the ``/api`` prefix by
:func:`app.main.create_app` via ``register_routers``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.models import BusinessCategory, SourceStatus, User
from app.core.schemas import (
    ClassificationOverride,
    ClassificationResultResponse,
    InboxFilter,
    KnowledgeExtractRequest,
    KnowledgeItemResponse,
    SourceItemCreate,
    SourceItemDetailResponse,
    SourceItemResponse,
)
from app.core.services.ai_provider import (
    ClassificationOutput,
    EntityContext,
    KnowledgeOutput,
)
from app.core.services.classification_service import ClassificationService
from app.core.services.ingestion_service import IngestionService
from app.core.services.knowledge_service import KnowledgeService
from app.database import get_db
from app.dependencies import (
    call_with_fallback,
    get_ai_provider,
    get_current_organization_id,
    get_current_user,
)

router = APIRouter(prefix="/source-items", tags=["source-items"])


@router.get("", response_model=list[SourceItemResponse])
def list_source_items(
    status: SourceStatus | None = None,
    category: BusinessCategory | None = None,
    review_pending: bool = False,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    _user: User = Depends(get_current_user),
) -> list[SourceItemResponse]:
    """Return the organization's inbox, newest first (Requirement 3.2).

    Protected by :func:`app.dependencies.get_current_user` (``401`` without a
    valid session). Results are scoped to the caller's organization and can be
    narrowed by ``status`` and ``category`` query parameters.
    """

    filters = InboxFilter(status=status, business_category=category, review_pending=review_pending)
    items = IngestionService(db).list_inbox(organization_id, filters, review_user_id=_user.id)
    return [SourceItemResponse.model_validate(item) for item in items]


@router.post(
    "",
    response_model=SourceItemResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_source_item(
    payload: SourceItemCreate,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    user: User = Depends(get_current_user),
) -> SourceItemResponse:
    """Collect a new source item in status ``NEW`` (Requirement 3.1).

    The tenant and creating user are taken from the authenticated session, not
    the payload. An invalid payload is rejected with ``422`` by request
    validation before this handler runs, so no partial item is written
    (Requirement 3.5).
    """

    item = IngestionService(db).ingest(organization_id, user.id, payload)
    return SourceItemResponse.model_validate(item)


@router.get("/{item_id}", response_model=SourceItemDetailResponse)
def get_source_item(
    item_id: UUID,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    _user: User = Depends(get_current_user),
) -> SourceItemDetailResponse:
    """Return a single item plus its current classification (Requirement 3.3).

    ``classification`` is ``None`` for items that have not been classified yet.
    A non-existent or cross-tenant id yields ``404`` (Requirement 2.3).
    """

    service = IngestionService(db)
    item = service.get(organization_id, item_id)
    classification = service.get_current_classification(item.id)
    return SourceItemDetailResponse(
        source_item=SourceItemResponse.model_validate(item),
        classification=(
            ClassificationResultResponse.model_validate(classification)
            if classification is not None
            else None
        ),
    )


@router.post("/{item_id}/dismiss", response_model=SourceItemResponse)
def dismiss_source_item(
    item_id: UUID,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    user: User = Depends(get_current_user),
) -> SourceItemResponse:
    """Mark a source item ``DISMISSED`` (Requirement 3.4).

    A non-existent or cross-tenant id yields ``404`` (Requirement 2.3).
    """

    item = IngestionService(db).dismiss(organization_id, item_id, user.id)
    return SourceItemResponse.model_validate(item)


@router.delete(
    "/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_source_item(
    item_id: UUID,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    user: User = Depends(get_current_user),
) -> Response:
    """Permanently delete a source item and cascade its dependents.

    A HARD delete (distinct from dismiss): also deletes the item's
    classification results and NULLs the ``source_item_id`` on derived
    knowledge/action/email rows so confirmed memory is retained. Writes one
    ``DELETE_SOURCE_ITEM`` audit row in the same transaction. A non-existent or
    cross-tenant id yields ``404`` (Requirement 2.3).
    """

    IngestionService(db).delete(organization_id, item_id, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Classification (Requirements 4 and 5)
# ---------------------------------------------------------------------------


@router.post(
    "/{item_id}/classify",
    response_model=ClassificationResultResponse,
    status_code=status.HTTP_201_CREATED,
)
def classify_source_item(
    item_id: UUID,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    settings: Settings = Depends(get_settings),
    _user: User = Depends(get_current_user),
) -> ClassificationResultResponse:
    """Run AI classification, persisting a ``SUGGESTED`` result (Requirement 4.1).

    The provider call is routed through
    :func:`app.dependencies.call_with_fallback`: the primary provider is
    resolved from configuration, and any provider failure is handled by the
    mode-dependent policy — fall back to the deterministic mock in
    ``DEVELOPMENT`` or surface a retriable ``503`` in ``PRODUCTION``
    (Requirement 11.6). The result is always persisted in status ``SUGGESTED``
    and never auto-confirmed (Requirements 4.2, 5.5). A non-existent or
    cross-tenant id yields ``404`` (Requirement 2.3).
    """

    provider = get_ai_provider(settings)

    def classifier(content: str, title: str) -> ClassificationOutput:
        return call_with_fallback(
            provider,
            settings,
            lambda p: p.classify_source_item(content, title),
        )

    result = ClassificationService(db).classify(
        organization_id, item_id, classifier=classifier
    )
    return ClassificationResultResponse.model_validate(result)


@router.post(
    "/{item_id}/confirm-classification",
    response_model=ClassificationResultResponse,
)
def confirm_classification(
    item_id: UUID,
    override: ClassificationOverride | None = None,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    user: User = Depends(get_current_user),
) -> ClassificationResultResponse:
    """Confirm a suggested classification (Requirements 5.1, 5.2, 5.4).

    Applies the optional human ``override`` (any omitted axis keeps the AI
    value), sets the result to ``CONFIRMED`` and the source item to
    ``CLASSIFIED``, and records exactly one ``CONFIRM_CLASSIFICATION`` audit row
    — all in one transaction. A missing item or active classification, or a
    cross-tenant id, yields ``404`` (Requirements 2.3, 5.4).
    """

    result = ClassificationService(db).confirm(
        organization_id, item_id, user.id, override=override
    )
    return ClassificationResultResponse.model_validate(result)


@router.post(
    "/{item_id}/reject-classification",
    response_model=ClassificationResultResponse,
)
def reject_classification(
    item_id: UUID,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    user: User = Depends(get_current_user),
) -> ClassificationResultResponse:
    """Reject a suggested classification, retaining it as a negative signal.

    Sets the current active classification to ``REJECTED`` without deleting it,
    so it remains available as a negative signal (Requirement 5.3). A missing
    item or active classification, or a cross-tenant id, yields ``404``
    (Requirement 2.3).
    """

    result = ClassificationService(db).reject(organization_id, item_id, user.id)
    return ClassificationResultResponse.model_validate(result)


# ---------------------------------------------------------------------------
# Knowledge extraction (Requirement 6)
# ---------------------------------------------------------------------------


@router.post(
    "/{item_id}/extract",
    response_model=KnowledgeItemResponse,
    status_code=status.HTTP_201_CREATED,
)
def extract_knowledge(
    item_id: UUID,
    payload: KnowledgeExtractRequest | None = None,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    settings: Settings = Depends(get_settings),
    _user: User = Depends(get_current_user),
) -> KnowledgeItemResponse:
    """Extract a ``SUGGESTED`` knowledge item from a source item (Requirement 6).

    Requires the source item to carry a ``CONFIRMED`` classification and to pass
    the privacy and sensitivity safety gates; a gate refusal (or the missing
    confirmed-classification precondition) surfaces unchanged as ``409 Conflict``
    (Requirements 6.1, 6.3, 6.4, 6.5). The optional body carries the explicit
    sensitivity ``acknowledged`` flag needed to process ``HIGHLY_SENSITIVE``
    content.

    The provider call is routed through
    :func:`app.dependencies.call_with_fallback`, so a failing ``LLMProvider``
    falls back to the deterministic mock in ``DEVELOPMENT`` and surfaces a
    retriable ``503`` in ``PRODUCTION`` (Requirement 11.6) — never a silent
    downgrade or a partial write. The result is always persisted in status
    ``SUGGESTED`` and never auto-confirmed (Requirement 6.2). A non-existent or
    cross-tenant id yields ``404`` (Requirement 2.3).
    """

    acknowledged = payload.acknowledged if payload is not None else False
    provider = get_ai_provider(settings)

    def extractor(content: str, context: EntityContext) -> KnowledgeOutput:
        return call_with_fallback(
            provider,
            settings,
            lambda p: p.extract_knowledge(content, context),
        )

    knowledge = KnowledgeService(db).extract(
        organization_id, item_id, extractor=extractor, acknowledged=acknowledged
    )
    return KnowledgeItemResponse.model_validate(knowledge)
