"""Action Center routes (Requirement 8).

Exposes the *Act* step of the pipeline over HTTP:

* ``GET /api/actions`` — list the organization's action items, optionally
  filtered by ``status`` and ``business_entity_id`` (Requirement 8.3).
* ``POST /api/actions`` — create an action item in status ``OPEN``. When the
  body references a **confirmed** knowledge item (``knowledge_item_id``) the
  action is created *from that suggestion*: marked AI-generated, inheriting its
  evidence, and recorded with exactly one ``CREATE_ACTION`` audit row in one
  transaction (Requirements 8.1, 13.1). Otherwise it is a manual, human-created
  action (Requirements 8.2, 8.5).
* ``PATCH /api/actions/{id}`` — update an action's status and/or editable
  fields; setting ``status`` to ``DONE`` marks it complete (Requirements 8.3,
  8.4).

Access control and tenant scoping follow the Core Engine conventions: every
route is protected by :func:`app.dependencies.get_current_user` (missing/invalid
session → ``401``), and every query is scoped to the caller's ``organization_id``
so one tenant can never read or mutate another's actions (cross-tenant access →
``404``). All paths are mounted under the ``/api`` prefix by
:func:`app.main.create_app` via ``register_routers``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.models import ActionStatus, User
from app.core.schemas import (
    ActionCreate,
    ActionItemResponse,
    ActionListFilter,
    ActionUpdate,
)
from app.core.services.action_service import ActionService
from app.database import get_db
from app.dependencies import get_current_organization_id, get_current_user

router = APIRouter(prefix="/actions", tags=["actions"])


@router.get("", response_model=list[ActionItemResponse])
def list_actions(
    status: ActionStatus | None = None,
    business_entity_id: UUID | None = None,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    _user: User = Depends(get_current_user),
) -> list[ActionItemResponse]:
    """Return the organization's action items, newest first (Requirement 8.3).

    Protected by :func:`app.dependencies.get_current_user` (``401`` without a
    valid session). Results are scoped to the caller's organization and can be
    narrowed by ``status`` and ``business_entity_id`` query parameters. Each item
    carries its ``ai_generated`` origin flag (Requirement 8.5).
    """

    filters = ActionListFilter(status=status, business_entity_id=business_entity_id)
    items = ActionService(db).list_open(organization_id, filters)
    return [ActionItemResponse.model_validate(item) for item in items]


@router.post(
    "",
    response_model=ActionItemResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_action(
    payload: ActionCreate,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    user: User = Depends(get_current_user),
) -> ActionItemResponse:
    """Create an action item in status ``OPEN`` (Requirements 8.1, 8.2, 8.5).

    When ``payload.knowledge_item_id`` references a confirmed knowledge item the
    action is created from that suggestion (AI-generated, evidence inherited)
    and a single ``CREATE_ACTION`` audit row is written in the same transaction
    (Requirements 8.1, 13.1); otherwise the action is manually created and
    marked human-originated (Requirements 8.2, 8.5). An invalid payload yields
    ``422`` before this handler runs, so no partial action is written. A
    referenced knowledge item that is missing/cross-tenant yields ``404``; one
    that exists but is not confirmed yields ``409``.
    """

    action = ActionService(db).create(organization_id, user.id, payload)
    return ActionItemResponse.model_validate(action)


@router.patch("/{action_id}", response_model=ActionItemResponse)
def update_action(
    action_id: UUID,
    payload: ActionUpdate,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    user: User = Depends(get_current_user),
) -> ActionItemResponse:
    """Update an action's status and/or fields (Requirements 8.3, 8.4).

    Applies only the explicitly-provided fields (unset fields are untouched);
    setting ``status`` to ``DONE`` marks the action complete (Requirement 8.4).
    A non-existent or cross-tenant id yields ``404`` (Requirement 2.3).
    """

    action = ActionService(db).update(organization_id, action_id, user.id, payload)
    return ActionItemResponse.model_validate(action)


@router.delete(
    "/{action_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_action(
    action_id: UUID,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    user: User = Depends(get_current_user),
) -> Response:
    """Permanently delete an action item and its calendar links (Requirement 2.3).

    Deletes any Google Calendar event links for the action and writes one
    ``DELETE_ACTION`` audit row in the same transaction. A non-existent or
    cross-tenant id yields ``404``.
    """

    ActionService(db).delete(organization_id, action_id, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
