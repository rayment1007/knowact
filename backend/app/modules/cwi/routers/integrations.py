"""Integrations management routes (Requirements 24, 25).

Exposes the incremental-OAuth integration lifecycle over HTTP. Every route is
protected by :func:`app.dependencies.get_current_user` (missing/invalid session
→ ``401``) and scoped to the caller's organization and user, so a connection
belonging to another tenant is indistinguishable from a missing one and yields
``404`` (Requirements 24.8, 25.5 / Property 13).

Responses use :class:`~app.modules.cwi.schemas.IntegrationConnectionView`, which
excludes every encrypted/secret token column, so no token, ciphertext, or key
can appear in any response body (Requirements 25.2, 25.3 / Property 14).

Routes:
* ``GET  /api/integrations`` — list the user's connections.
* ``POST /api/integrations/{service}/connect`` — begin incremental authorization.
* ``GET  /api/integrations/callback`` — complete authorization.
* ``POST /api/integrations/{id}/disconnect`` — stop future sync (``REVOKED``).
* ``POST /api/integrations/{id}/revoke`` — revoke Google access + disconnect.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.models import User
from app.database import get_db
from app.dependencies import get_current_user
from app.modules.cwi.dependencies import google_oauth_client
from app.modules.cwi.models import IntegrationService as IntegrationServiceEnum
from app.modules.cwi.schemas import (
    AuthorizationRedirectResponse,
    IntegrationConnectionView,
)
from app.modules.cwi.services.google_oauth import GoogleOAuthClient
from app.modules.cwi.services.integration_service import IntegrationService

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _service(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    oauth: GoogleOAuthClient = Depends(google_oauth_client),
) -> IntegrationService:
    """Build an :class:`IntegrationService` bound to the request transaction."""

    return IntegrationService(db, oauth, settings=settings)


@router.get("", response_model=list[IntegrationConnectionView])
def list_integrations(
    service: IntegrationService = Depends(_service),
    user: User = Depends(get_current_user),
) -> list[IntegrationConnectionView]:
    """List the user's connections, scoped to their organization (Req 24.1)."""

    connections = service.list_connections(user.organization_id, user.id)
    return [
        IntegrationConnectionView.from_connection(c) for c in connections
    ]


@router.post(
    "/{service_name}/connect", response_model=AuthorizationRedirectResponse
)
def connect_integration(
    service_name: IntegrationServiceEnum,
    service: IntegrationService = Depends(_service),
    user: User = Depends(get_current_user),
) -> AuthorizationRedirectResponse:
    """Begin incremental authorization for a service (Req 24.2)."""

    redirect = service.begin_authorization(
        user.organization_id, user.id, service_name
    )
    return AuthorizationRedirectResponse(
        authorization_url=redirect.authorization_url, state=redirect.state
    )


def _frontend_url(settings: Settings, path: str) -> str:
    """Build an absolute SPA URL under the configured frontend base."""

    base = settings.frontend_base_url.rstrip("/")
    return f"{base}{path}"


@router.get("/callback")
def integration_callback(
    code: str,
    state: str,
    service: IntegrationService = Depends(_service),
    settings: Settings = Depends(get_settings),
    user: User = Depends(get_current_user),
) -> RedirectResponse:
    """Complete incremental authorization and redirect back to the SPA (Req 24.3).

    On success the connection is persisted (``CONNECTED``) with a
    ``CONNECT_INTEGRATION`` audit row and the browser is redirected to
    ``/integrations?connected={service}``. If authorization cannot be completed
    the browser is redirected to ``/integrations?error=connect_failed`` and
    nothing is persisted.
    """

    try:
        connection = service.complete_authorization(
            user.organization_id, user.id, code, state
        )
    except HTTPException:
        return RedirectResponse(
            url=_frontend_url(settings, "/integrations?error=connect_failed"),
            status_code=302,
        )

    return RedirectResponse(
        url=_frontend_url(
            settings, f"/integrations?connected={connection.service.value}"
        ),
        status_code=302,
    )


@router.post("/{connection_id}/disconnect", response_model=IntegrationConnectionView)
def disconnect_integration(
    connection_id: UUID,
    service: IntegrationService = Depends(_service),
    user: User = Depends(get_current_user),
) -> IntegrationConnectionView:
    """Disconnect a connection (status ``REVOKED``, stop future sync) (Req 24.5)."""

    connection = service.disconnect(
        user.organization_id, user.id, connection_id
    )
    return IntegrationConnectionView.from_connection(connection)


@router.post("/{connection_id}/revoke", response_model=IntegrationConnectionView)
def revoke_integration(
    connection_id: UUID,
    service: IntegrationService = Depends(_service),
    user: User = Depends(get_current_user),
) -> IntegrationConnectionView:
    """Revoke Google access for a connection and disconnect it (Req 24.6)."""

    connection = service.revoke_google_access(
        user.organization_id, user.id, connection_id
    )
    return IntegrationConnectionView.from_connection(connection)
