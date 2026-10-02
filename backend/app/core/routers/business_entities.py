"""Business entity routes (Requirement 10.5).

A :class:`~app.core.models.BusinessEntity` is a domain object (client, project,
vendor, department, …) that accumulates knowledge, actions, and decisions and
can be the subject of an entity-scoped brief. This router exposes:

* ``GET /api/business-entities`` — list the organization's business entities,
  newest first.
* ``POST /api/business-entities`` — create a business entity for the caller's
  organization (``organization_id`` derived from the session, never the
  payload). An invalid payload yields ``422`` before the handler runs, so no
  partial entity is persisted.
* ``GET /api/business-entities/{id}/brief`` — generate and persist a brief
  scoped to that entity from *its* confirmed knowledge and open actions
  (Requirement 10.5). A non-existent or cross-tenant id yields ``404``
  (Requirement 2.3), enforced by :class:`~app.core.services.brief_service.BriefService`.

The entity-brief provider call is routed through
:func:`app.dependencies.call_with_fallback` (mode-dependent failure policy,
Requirement 11.6) exactly like the daily brief; the service owns context
assembly and persistence while the route supplies a generator callable that
wraps the provider call.

Access control and tenant scoping follow the Core Engine conventions: every
route is protected by :func:`app.dependencies.get_current_user` (missing/invalid
session → ``401``), and every query is scoped to the caller's ``organization_id``
(cross-tenant access → ``404``). All paths are mounted under the ``/api`` prefix
by :func:`app.main.create_app` via ``register_routers``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.models import BusinessEntity, User, KnowledgeItem, ActionItem, DecisionRecord, Brief
from app.modules.cwi.models import EmailTaskSuggestion, EmailDraft
from app.core.services.audit_service import AuditService
from app.dependencies import not_found
from app.core.schemas import (
    BriefResponse,
    BusinessEntityCreate,
    BusinessEntityResponse,
)
from app.core.services.ai_provider import EntityBriefOutput, EntityContext
from app.core.services.brief_service import BriefService
from app.database import get_db
from app.dependencies import (
    call_with_fallback,
    get_ai_provider,
    get_current_organization_id,
    get_current_user,
    scope_select,
)

router = APIRouter(prefix="/business-entities", tags=["business-entities"])


@router.get("", response_model=list[BusinessEntityResponse])
def list_business_entities(
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    _user: User = Depends(get_current_user),
) -> list[BusinessEntityResponse]:
    """Return the organization's business entities, newest first.

    Protected by :func:`app.dependencies.get_current_user` (``401`` without a
    valid session). Results are scoped to the caller's organization so one
    tenant can never see another's entities (Requirement 2.3).
    """

    stmt = scope_select(
        select(BusinessEntity), BusinessEntity, organization_id
    ).order_by(BusinessEntity.created_at.desc(), BusinessEntity.id.desc())
    entities = db.execute(stmt).scalars().all()
    return [BusinessEntityResponse.model_validate(e) for e in entities]


@router.post(
    "",
    response_model=BusinessEntityResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_business_entity(
    payload: BusinessEntityCreate,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    _user: User = Depends(get_current_user),
) -> BusinessEntityResponse:
    """Create a business entity for the caller's organization.

    The tenant is taken from the authenticated session, not the payload. An
    invalid payload is rejected with ``422`` by request validation before this
    handler runs, so no partial entity is written.
    """

    entity = BusinessEntity(
        organization_id=organization_id,
        name=payload.name,
        entity_type=payload.entity_type,
        description=payload.description,
        attributes=payload.attributes,
    )
    db.add(entity)
    db.flush()
    return BusinessEntityResponse.model_validate(entity)


@router.delete("/{entity_id}", status_code=204)
def delete_business_entity(entity_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    entity = db.scalar(select(BusinessEntity).where(BusinessEntity.id == entity_id, BusinessEntity.organization_id == user.organization_id))
    if entity is None:
        raise not_found("Project / entity not found.")
    for model in (KnowledgeItem, ActionItem, DecisionRecord, EmailTaskSuggestion, EmailDraft):
        db.execute(update(model).where(model.organization_id == user.organization_id,
            model.business_entity_id == entity_id).values(business_entity_id=None))
    db.execute(update(Brief).where(Brief.organization_id == user.organization_id,
        Brief.scope_ref_id == entity_id).values(scope_ref_id=None))
    AuditService(db).record(org_id=user.organization_id, actor_id=user.id,
        action_type="DELETE_BUSINESS_ENTITY", target_type="BusinessEntity", target_id=entity_id,
        detail={"name": entity.name, "records_retained": True})
    db.delete(entity)
    db.flush()


@router.get("/{entity_id}/brief", response_model=BriefResponse)
def get_entity_brief(
    entity_id: UUID,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    settings: Settings = Depends(get_settings),
    user: User = Depends(get_current_user),
) -> BriefResponse:
    """Generate and persist a brief scoped to one business entity (Requirement 10.5).

    Resolves the org-scoped entity (``404`` if missing/cross-tenant), assembles
    its context from *that entity's* confirmed knowledge, open actions, and
    recent decisions, and persists an ``ENTITY``
    :class:`~app.core.models.Brief` whose ``scope_ref_id`` is the entity and
    whose lines reference their source evidence (Requirements 10.4, 10.5).

    The provider call is routed through
    :func:`app.dependencies.call_with_fallback`, so a failing ``LLMProvider``
    falls back to the deterministic mock in ``DEVELOPMENT`` and surfaces a
    retriable ``503`` in ``PRODUCTION`` (Requirement 11.6). Protected by
    :func:`app.dependencies.get_current_user` (``401`` without a valid session).
    """

    provider = get_ai_provider(settings)

    def generator(context: EntityContext) -> EntityBriefOutput:
        return call_with_fallback(
            provider,
            settings,
            lambda p: p.generate_entity_brief(context),
        )

    brief = BriefService(db).entity_brief(
        organization_id, entity_id, user.id, generator=generator
    )
    return BriefResponse.model_validate(brief)
