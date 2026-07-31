"""Audit log routes (Requirement 13).

Exposes the read side of the audit trail:

* ``GET /api/audit`` — list audit log entries for the authenticated user's
  organization, most-recent first, with optional filtering by actor, action
  type, and target (Requirement 13.3).

The write side (:meth:`~app.core.services.audit_service.AuditService.record`)
has no route of its own: audit rows are written *by other services* inside the
transaction of the state change they record, never through a standalone HTTP
call.

Access control and tenant scoping follow the Core Engine conventions: the route
is protected by :func:`app.dependencies.get_current_user` (missing/invalid
session → ``401``), and the query is scoped to the caller's ``organization_id``
so one tenant can never read another's audit trail.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.schemas import AuditFilter, AuditLogResponse
from app.core.services.audit_service import AuditService
from app.database import get_db
from app.dependencies import get_current_organization_id, get_current_user

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditLogResponse])
def list_audit(
    actor_id: UUID | None = None,
    action_type: str | None = None,
    target_type: str | None = None,
    target_id: UUID | None = None,
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    _user=Depends(get_current_user),
) -> list[AuditLogResponse]:
    """Return the organization's audit entries, newest first.

    Protected by :func:`app.dependencies.get_current_user` (``401`` without a
    valid session). Results are scoped to the caller's organization
    (Requirement 13.3); the optional query parameters narrow the result by
    actor, action type, or target.
    """

    filters = AuditFilter(
        actor_id=actor_id,
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
    )
    entries = AuditService(db).list(organization_id, filters)
    return [AuditLogResponse.model_validate(entry) for entry in entries]
