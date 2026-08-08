"""Decision Memory routes (Requirement 9).

Exposes decision recording and review over HTTP:

* ``GET /api/decisions`` — list the organization's decision records, optionally
  filtered by ``business_entity_id``, each carrying its statement, rationale,
  evidence, owner, and date (Requirement 9.3).
* ``POST /api/decisions`` — record a decision with its rationale and evidence,
  owned by the authenticated user and dated now (or as supplied), writing
  exactly one ``CREATE_DECISION`` audit row in the same transaction
  (Requirements 9.1, 9.2, 9.4).

Access control and tenant scoping follow the Core Engine conventions: every
route is protected by :func:`app.dependencies.get_current_user` (missing/invalid
session → ``401``), and every query is scoped to the caller's ``organization_id``
so one tenant can never read another's decisions (cross-tenant access → ``404``).
All paths are mounted under the ``/api`` prefix by :func:`app.main.create_app`
via ``register_routers``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.models import User
from app.core.schemas import (
    DecisionCreate,
    DecisionListFilter,
    DecisionRecordResponse,
)
from app.core.services.decision_service import DecisionService
from app.database import get_db
from app.dependencies import get_current_organization_id, get_current_user

router = APIRouter(prefix="/decisions", tags=["decisions"])


@router.get("", response_model=list[DecisionRecordResponse])
def list_decisions(
    business_entity_id: UUID | None = None,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    _user: User = Depends(get_current_user),
) -> list[DecisionRecordResponse]:
    """Return the organization's decision records, newest first (Requirement 9.3).

    Protected by :func:`app.dependencies.get_current_user` (``401`` without a
    valid session). Results are scoped to the caller's organization and can be
    narrowed by the ``business_entity_id`` query parameter. Each record includes
    its statement, rationale, evidence, owner, and date (Requirement 9.3).
    """

    filters = DecisionListFilter(business_entity_id=business_entity_id)
    decisions = DecisionService(db).list(organization_id, filters)
    return [DecisionRecordResponse.model_validate(d) for d in decisions]


@router.get("/{decision_id}", response_model=DecisionRecordResponse)
def get_decision(
    decision_id: UUID,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    _user: User = Depends(get_current_user),
) -> DecisionRecordResponse:
    """Return one decision record for a direct Decision Memory deep link.

    A missing record and a record owned by another organization both return
    ``404``.
    """

    decision = DecisionService(db).get(organization_id, decision_id)
    return DecisionRecordResponse.model_validate(decision)


@router.post(
    "",
    response_model=DecisionRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_decision(
    payload: DecisionCreate,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    user: User = Depends(get_current_user),
) -> DecisionRecordResponse:
    """Record a decision (Requirements 9.1, 9.2, 9.4).

    Persists a decision record with its statement, rationale, evidence, owner
    (the authenticated user), and decided-at date, optionally linked to a
    business entity, and writes exactly one ``CREATE_DECISION`` audit row in the
    same transaction (Requirements 9.1, 9.2, 9.4, 13.1). An invalid payload
    yields ``422`` before this handler runs, so no partial decision is written.
    """

    decision = DecisionService(db).create(organization_id, user.id, payload)
    return DecisionRecordResponse.model_validate(decision)


@router.delete(
    "/{decision_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_decision(
    decision_id: UUID,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    user: User = Depends(get_current_user),
) -> Response:
    """Permanently delete a decision record (Requirement 2.3).

    Writes one ``DELETE_DECISION`` audit row in the same transaction. A
    non-existent or cross-tenant id yields ``404``.
    """

    DecisionService(db).delete(organization_id, decision_id, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
