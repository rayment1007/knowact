"""Brief routes (Requirement 10).

Exposes the *Recommend + Learn feedback* step of the pipeline over HTTP:

* ``GET /api/brief/daily`` — generate and persist the organization-wide daily
  brief for the current user, assembled from the tenant's confirmed knowledge,
  open actions, and recent decisions (Requirements 10.1, 10.3).

The organization-scoped **entity** brief lives on the business-entity resource
(``GET /api/business-entities/{id}/brief``) in
:mod:`app.core.routers.business_entities` because it is keyed by an entity id.

Both endpoints route the provider call through
:func:`app.dependencies.call_with_fallback`: the primary provider is resolved
from configuration and any provider failure is handled by the mode-dependent
policy — fall back to the deterministic mock in ``DEVELOPMENT`` or surface a
retriable ``503`` in ``PRODUCTION`` (Requirement 11.6). The service owns context
assembly and persistence; the route only supplies a generator callable that
wraps the provider call. Because :class:`~app.core.services.brief_service.BriefService`
builds the context internally, the generator is defined as
``generator(context) -> BriefOutput`` and forwards ``context`` into the wrapped
provider call.

Access control and tenant scoping follow the Core Engine conventions: every
route is protected by :func:`app.dependencies.get_current_user` (missing/invalid
session → ``401``), and every query is scoped to the caller's ``organization_id``
(cross-tenant access → ``404``). All paths are mounted under the ``/api`` prefix
by :func:`app.main.create_app` via ``register_routers``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.models import User
from app.core.schemas import BriefResponse
from app.core.services.ai_provider import DailyBriefOutput, OrgContext
from app.core.services.brief_service import BriefService
from app.database import get_db
from app.dependencies import (
    call_with_fallback,
    get_ai_provider,
    get_current_organization_id,
    get_current_user,
)
from uuid import UUID

router = APIRouter(prefix="/brief", tags=["briefs"])


@router.get("/daily", response_model=BriefResponse)
def get_daily_brief(
    db: Session = Depends(get_db),
    organization_id: UUID = Depends(get_current_organization_id),
    settings: Settings = Depends(get_settings),
    user: User = Depends(get_current_user),
) -> BriefResponse:
    """Generate and persist the organization-wide daily brief (Requirements 10.1, 10.3).

    The service assembles the org context from *only* confirmed knowledge, open
    actions, and recent decisions (Requirements 10.1, 10.2) and persists a
    ``DAILY`` :class:`~app.core.models.Brief` carrying a headline, priorities,
    recommended actions, follow-ups, and risks — each line referencing its
    source evidence (Requirements 10.3, 10.4).

    The provider call is routed through
    :func:`app.dependencies.call_with_fallback`, so a failing ``LLMProvider``
    falls back to the deterministic mock in ``DEVELOPMENT`` and surfaces a
    retriable ``503`` in ``PRODUCTION`` (Requirement 11.6). Protected by
    :func:`app.dependencies.get_current_user` (``401`` without a valid session).
    """

    provider = get_ai_provider(settings)

    def generator(context: OrgContext) -> DailyBriefOutput:
        return call_with_fallback(
            provider,
            settings,
            lambda p: p.generate_daily_brief(context),
        )

    brief = BriefService(db).daily_brief(
        organization_id, user.id, generator=generator
    )
    return BriefResponse.model_validate(brief)
