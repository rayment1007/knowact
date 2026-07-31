"""Knowledge hub routes (Requirement 7).

Exposes the review side of the knowledge base — the "Knowledge Hub" — over
HTTP:

* ``GET /api/knowledge`` — list the organization's knowledge items, optionally
  filtered by ``business_entity_id`` and ``status`` (Requirement 7.3).
* ``GET /api/knowledge/{id}`` — fetch a single knowledge item together with its
  ``evidence_text`` and the actions and decisions linked to it (Requirements
  7.4, 7.5).
* ``POST /api/knowledge/{id}/confirm`` — confirm a suggested item, setting it to
  ``CONFIRMED`` and recording exactly one ``CONFIRM_KNOWLEDGE`` audit row in one
  transaction (Requirements 7.1, 13.1).
* ``POST /api/knowledge/{id}/reject`` — reject a suggested item, setting it to
  ``REJECTED`` while retaining it as a negative signal (Requirement 7.2).

The extraction endpoint that *creates* a knowledge item is mounted on the
source-item resource (``POST /api/source-items/{id}/extract``) because it
operates on a single source item; see :mod:`app.core.routers.source_items`.

Access control and tenant scoping follow the Core Engine conventions: every
route is protected by :func:`app.dependencies.get_current_user` (missing/invalid
session → ``401``), and every query is scoped to the caller's ``organization_id``
so one tenant can never read or mutate another's knowledge (cross-tenant access
→ ``404``). All paths are mounted under the ``/api`` prefix by
:func:`app.main.create_app` via ``register_routers``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.models import SuggestionStatus, User
from app.core.schemas import (
    ActionItemResponse,
    DecisionRecordResponse,
    KnowledgeDetailResponse,
    KnowledgeItemResponse,
    KnowledgeListFilter,
)
from app.core.services.knowledge_service import KnowledgeService
from app.database import get_db
from app.dependencies import get_current_organization_id, get_current_user

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("", response_model=list[KnowledgeItemResponse])
def list_knowledge(
    business_entity_id: UUID | None = None,
    status: SuggestionStatus | None = None,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    _user: User = Depends(get_current_user),
) -> list[KnowledgeItemResponse]:
    """Return the organization's knowledge items, newest first (Requirement 7.3).

    Protected by :func:`app.dependencies.get_current_user` (``401`` without a
    valid session). Results are scoped to the caller's organization and can be
    narrowed by ``business_entity_id`` and ``status`` query parameters.
    """

    filters = KnowledgeListFilter(
        business_entity_id=business_entity_id, status=status
    )
    items = KnowledgeService(db).list_for_entity(organization_id, filters)
    return [KnowledgeItemResponse.model_validate(item) for item in items]


@router.get("/{knowledge_id}", response_model=KnowledgeDetailResponse)
def get_knowledge_detail(
    knowledge_id: UUID,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    _user: User = Depends(get_current_user),
) -> KnowledgeDetailResponse:
    """Return a knowledge item plus its linked actions and decisions.

    Carries the item's summary, key points, and ``evidence_text`` together with
    the actions and decisions connected to it (Requirements 7.4, 7.5). A
    non-existent or cross-tenant id yields ``404`` (Requirement 2.3).
    """

    knowledge, actions, decisions = KnowledgeService(db).get_detail(
        organization_id, knowledge_id
    )
    return KnowledgeDetailResponse(
        knowledge_item=KnowledgeItemResponse.model_validate(knowledge),
        linked_actions=[ActionItemResponse.model_validate(a) for a in actions],
        linked_decisions=[
            DecisionRecordResponse.model_validate(d) for d in decisions
        ],
    )


@router.post("/{knowledge_id}/confirm", response_model=KnowledgeItemResponse)
def confirm_knowledge(
    knowledge_id: UUID,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    user: User = Depends(get_current_user),
) -> KnowledgeItemResponse:
    """Confirm a suggested knowledge item (Requirements 7.1, 13.1).

    Sets the item to ``CONFIRMED`` and records exactly one ``CONFIRM_KNOWLEDGE``
    audit row — both in one transaction. A non-existent or cross-tenant id
    yields ``404`` (Requirement 2.3).
    """

    knowledge = KnowledgeService(db).confirm(organization_id, knowledge_id, user.id)
    return KnowledgeItemResponse.model_validate(knowledge)


@router.post("/{knowledge_id}/reject", response_model=KnowledgeItemResponse)
def reject_knowledge(
    knowledge_id: UUID,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    user: User = Depends(get_current_user),
) -> KnowledgeItemResponse:
    """Reject a suggested knowledge item, retaining it as a negative signal.

    Sets the item to ``REJECTED`` without deleting it, so it remains available
    as a negative signal (Requirement 7.2). A non-existent or cross-tenant id
    yields ``404`` (Requirement 2.3).
    """

    knowledge = KnowledgeService(db).reject(organization_id, knowledge_id, user.id)
    return KnowledgeItemResponse.model_validate(knowledge)


@router.delete(
    "/{knowledge_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_knowledge(
    knowledge_id: UUID,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    user: User = Depends(get_current_user),
) -> Response:
    """Permanently delete a knowledge item (Requirement 2.3).

    NULLs the ``knowledge_item_id`` on any action referencing it (retaining the
    action) and writes one ``DELETE_KNOWLEDGE`` audit row in the same
    transaction. A non-existent or cross-tenant id yields ``404``.
    """

    KnowledgeService(db).delete(organization_id, knowledge_id, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
