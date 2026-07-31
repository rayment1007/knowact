"""Connected Workspace Intelligence service layer.

Services here follow the Core Engine conventions: every query is filtered by the
caller's ``organization_id`` (cross-tenant access surfaces as ``404``), and
services ``add``/``flush`` within the request transaction but never commit — the
route's ``get_db`` dependency commits on success and rolls back on error.
"""

from __future__ import annotations

from app.modules.cwi.services.google_oauth import (
    FakeGoogleOAuthClient,
    GoogleIdentity,
    GoogleOAuthClient,
    GoogleOAuthError,
    GoogleTokenGrant,
    HttpGoogleOAuthClient,
    get_google_oauth_client,
)
from app.modules.cwi.services.integration_service import (
    CONNECT_INTEGRATION,
    DISCONNECT_INTEGRATION,
    REVOKE_INTEGRATION,
    AuthorizationRedirect,
    IntegrationService,
)
from app.modules.cwi.services.token_vault import TokenVault, TokenVaultError

__all__ = [
    "GoogleOAuthClient",
    "GoogleIdentity",
    "GoogleTokenGrant",
    "GoogleOAuthError",
    "FakeGoogleOAuthClient",
    "HttpGoogleOAuthClient",
    "get_google_oauth_client",
    "TokenVault",
    "TokenVaultError",
    "IntegrationService",
    "AuthorizationRedirect",
    "CONNECT_INTEGRATION",
    "DISCONNECT_INTEGRATION",
    "REVOKE_INTEGRATION",
]
