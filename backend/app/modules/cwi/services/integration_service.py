"""Integration authorization and secure token lifecycle (Requirements 24, 25).

:class:`IntegrationService` owns the incremental-OAuth lifecycle for Google
service connections (Gmail, Calendar): beginning an authorization, completing
it (persisting an encrypted :class:`~app.modules.cwi.models.IntegrationConnection`
and writing the ``CONNECT_INTEGRATION`` audit in the same transaction), listing
connections, transparently refreshing an expired access token, disconnecting,
and revoking Google access.

Conventions carried over from the Core Engine services:

* **Org + user scoping is mandatory.** Every lookup is filtered by
  ``organization_id`` (and ``user_id``) via :func:`app.dependencies.scope_select`.
  A connection belonging to another tenant is indistinguishable from a missing
  one: a scoped lookup that returns nothing raises
  :func:`app.dependencies.not_found` (``404``), never ``403`` (Requirements
  24.8, 25.5 / Property 13).
* **The service never commits.** It ``add``/``flush``es within the caller's
  (route's) transaction so the connection mutation and its audit row commit
  atomically (Requirement 24.4); the route's ``get_db`` dependency commits on
  success and rolls back on error.
* **Tokens are encrypted at rest and never logged.** Plaintext tokens exist
  only transiently in memory during an outbound Google call; ``last_error``
  records user-facing reasons without any secret content (Requirements 25.1,
  25.4, 25.7).

Google I/O is performed exclusively through an injected
:class:`~app.modules.cwi.services.google_oauth.GoogleOAuthClient`, so tests use a
deterministic fake and no real OAuth call ever occurs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.services.audit_service import AuditService
from app.dependencies import not_found, scope_select
from app.modules.cwi.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationService as IntegrationServiceEnum,
)
from app.modules.cwi.services.google_oauth import (
    CALENDAR_CALENDARLIST_READONLY_SCOPE,
    CALENDAR_EVENTS_SCOPE,
    GMAIL_COMPOSE_SCOPE,
    GMAIL_READONLY_SCOPE,
    GoogleOAuthClient,
    GoogleOAuthError,
)
from app.modules.cwi.services.token_vault import TokenVault

# Audit action types (Requirements 24.4, 24.7).
CONNECT_INTEGRATION = "CONNECT_INTEGRATION"
DISCONNECT_INTEGRATION = "DISCONNECT_INTEGRATION"
REVOKE_INTEGRATION = "REVOKE_INTEGRATION"

_TARGET_TYPE = "IntegrationConnection"

# The capability scopes requested per service (Requirement 24.2). Gmail requests
# both read and compose in a single connect so one Gmail authorization also
# grants AI drafting/sending; compose is still NEVER requested at basic sign-in
# (Requirement 32.3). Calendar requests only its events scope.
_SERVICE_SCOPES: dict[IntegrationServiceEnum, list[str]] = {
    IntegrationServiceEnum.GMAIL: [GMAIL_READONLY_SCOPE, GMAIL_COMPOSE_SCOPE],
    IntegrationServiceEnum.GOOGLE_CALENDAR: [
        CALENDAR_EVENTS_SCOPE,
        CALENDAR_CALENDARLIST_READONLY_SCOPE,
    ],
}

# The signed-state ``purpose`` claim so a sign-in state can never be replayed as
# an integration-authorization state (or vice versa).
_STATE_PURPOSE = "cwi_integration_authz"


@dataclass(frozen=True)
class AuthorizationRedirect:
    """Where to send the user's browser to authorize a service, plus state."""

    authorization_url: str
    state: str


class IntegrationService:
    """Manage the OAuth lifecycle of Google service connections."""

    def __init__(
        self,
        db: Session,
        oauth_client: GoogleOAuthClient,
        *,
        token_vault: TokenVault | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Bind the service to a session and an injected Google transport.

        Args:
            db: Request-scoped session (transaction owned by the caller).
            oauth_client: The Google OAuth transport (a fake in tests).
            token_vault: Optional vault; one is built from settings if omitted.
            settings: Optional settings; process settings are used if omitted.
        """

        self.db = db
        self.oauth = oauth_client
        self.settings = settings or get_settings()
        self.vault = token_vault or TokenVault(self.settings)

    # -- Callback URIs ------------------------------------------------------

    def _service_redirect_uri(self) -> str:
        base = self.settings.google_oauth_redirect_base.rstrip("/")
        return f"{base}/api/integrations/callback"

    # -- Signed state -------------------------------------------------------

    def _encode_state(
        self, org_id: UUID, user_id: UUID, service: IntegrationServiceEnum
    ) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "purpose": _STATE_PURPOSE,
            "org_id": str(org_id),
            "user_id": str(user_id),
            "service": service.value,
            "nonce": uuid4().hex,
            "iat": now,
        }
        return jwt.encode(
            payload,
            self.settings.jwt_secret,
            algorithm=self.settings.jwt_algorithm,
        )

    def _decode_state(
        self, state: str, org_id: UUID, user_id: UUID
    ) -> IntegrationServiceEnum:
        try:
            claims = jwt.decode(
                state,
                self.settings.jwt_secret,
                algorithms=[self.settings.jwt_algorithm],
            )
        except jwt.InvalidTokenError as exc:
            raise not_found("Integration connection not found.") from exc

        # The state must be an integration-authz state issued for THIS user in
        # THIS organization; otherwise treat it as if it never existed (404).
        if (
            claims.get("purpose") != _STATE_PURPOSE
            or claims.get("org_id") != str(org_id)
            or claims.get("user_id") != str(user_id)
        ):
            raise not_found("Integration connection not found.")

        try:
            return IntegrationServiceEnum(claims.get("service"))
        except ValueError as exc:
            raise not_found("Integration connection not found.") from exc

    # -- Scoped lookup ------------------------------------------------------

    def _get_scoped(
        self, org_id: UUID, connection_id: UUID, user_id: UUID | None = None
    ) -> IntegrationConnection:
        """Return an org- (and optionally user-) scoped connection or ``404``.

        Cross-organization access is indistinguishable from a missing row
        (Requirements 24.8, 25.5 / Property 13).
        """

        stmt = scope_select(
            select(IntegrationConnection), IntegrationConnection, org_id
        ).where(IntegrationConnection.id == connection_id)
        if user_id is not None:
            stmt = stmt.where(IntegrationConnection.user_id == user_id)
        connection = self.db.execute(stmt).scalar_one_or_none()
        if connection is None:
            raise not_found("Integration connection not found.")
        return connection

    # -- Public API ---------------------------------------------------------

    def begin_authorization(
        self, org_id: UUID, user_id: UUID, service: IntegrationServiceEnum
    ) -> AuthorizationRedirect:
        """Begin incremental authorization for one service capability.

        Requests exactly one capability scope for ``service`` (Requirement
        24.2) and returns the Google authorization URL plus a signed ``state``
        that binds the flow to this org+user and records which service is being
        connected. No connection is created yet.
        """

        scopes = _SERVICE_SCOPES[service]
        state = self._encode_state(org_id, user_id, service)
        url = self.oauth.build_service_url(
            scopes=scopes,
            redirect_uri=self._service_redirect_uri(),
            state=state,
        )
        return AuthorizationRedirect(authorization_url=url, state=state)

    def complete_authorization(
        self, org_id: UUID, user_id: UUID, code: str, state: str
    ) -> IntegrationConnection:
        """Complete an incremental authorization and persist the connection.

        Exchanges ``code`` for access/refresh tokens via the injected transport,
        encrypts them, and creates or updates the
        :class:`IntegrationConnection` with status ``CONNECTED`` and
        ``granted_scopes`` set to exactly what Google returned (Requirement
        24.3). Records exactly one ``CONNECT_INTEGRATION`` audit row in the same
        transaction (Requirement 24.4). Tokens are never logged or returned.
        """

        service = self._decode_state(state, org_id, user_id)

        try:
            grant = self.oauth.exchange_service_code(
                code=code, redirect_uri=self._service_redirect_uri()
            )
        except GoogleOAuthError as exc:
            # Authorization could not be completed; nothing is persisted.
            raise not_found("Integration connection not found.") from exc

        access_encrypted = self.vault.encrypt(grant.access_token)
        refresh_encrypted = (
            self.vault.encrypt(grant.refresh_token)
            if grant.refresh_token
            else None
        )

        # Upsert on the connection identity.
        stmt = scope_select(
            select(IntegrationConnection), IntegrationConnection, org_id
        ).where(
            IntegrationConnection.user_id == user_id,
            IntegrationConnection.provider == IntegrationProvider.GOOGLE,
            IntegrationConnection.service == service,
            IntegrationConnection.external_account_id
            == grant.external_account_id,
        )
        connection = self.db.execute(stmt).scalar_one_or_none()

        if connection is None:
            connection = IntegrationConnection(
                organization_id=org_id,
                user_id=user_id,
                provider=IntegrationProvider.GOOGLE,
                service=service,
                external_account_id=grant.external_account_id,
                account_email=grant.account_email,
                granted_scopes_json=list(grant.granted_scopes),
                access_token_encrypted=access_encrypted,
                refresh_token_encrypted=refresh_encrypted,
                token_expires_at=grant.expires_at,
                status=ConnectionStatus.CONNECTED,
                last_error=None,
            )
            self.db.add(connection)
        else:
            connection.account_email = grant.account_email
            connection.granted_scopes_json = list(grant.granted_scopes)
            connection.access_token_encrypted = access_encrypted
            if refresh_encrypted is not None:
                connection.refresh_token_encrypted = refresh_encrypted
            connection.token_expires_at = grant.expires_at
            connection.status = ConnectionStatus.CONNECTED
            connection.last_error = None
            self.db.add(connection)

        self.db.flush()

        AuditService(self.db).record(
            org_id=org_id,
            actor_id=user_id,
            action_type=CONNECT_INTEGRATION,
            target_type=_TARGET_TYPE,
            target_id=connection.id,
            detail={
                "service": service.value,
                "granted_scopes": list(grant.granted_scopes),
            },
        )
        return connection

    def list_connections(
        self, org_id: UUID, user_id: UUID
    ) -> list[IntegrationConnection]:
        """Return the user's connections in the org, newest first (Req 24.1)."""

        stmt = scope_select(
            select(IntegrationConnection), IntegrationConnection, org_id
        ).where(IntegrationConnection.user_id == user_id)
        stmt = stmt.order_by(
            IntegrationConnection.created_at.desc(),
            IntegrationConnection.id.desc(),
        )
        return list(self.db.execute(stmt).scalars().all())

    def get_connection(
        self, org_id: UUID, connection_id: UUID, user_id: UUID | None = None
    ) -> IntegrationConnection:
        """Return a single org-scoped connection or raise ``404`` (Req 24.8)."""

        return self._get_scoped(org_id, connection_id, user_id)

    def get_valid_access_token(
        self, org_id: UUID, connection_id: UUID
    ) -> str:
        """Return a valid (refreshed if needed) decrypted access token.

        If the stored access token is expired, it is refreshed using the
        encrypted refresh token; the new token is re-encrypted and
        ``token_expires_at`` updated (Requirement 25.6). If the refresh fails,
        the connection is set to ``EXPIRED`` and ``last_error`` records a
        secret-free reason before the failure is raised (Requirement 25.7).

        The plaintext token is returned for a transient outbound call only; it
        is never logged or persisted in the clear.
        """

        connection = self._get_scoped(org_id, connection_id)

        now = datetime.now(timezone.utc)
        expires_at = connection.token_expires_at
        # Treat a naive datetime (SQLite round-trip) as UTC for comparison.
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        is_expired = expires_at is not None and expires_at <= now
        if not is_expired:
            return self.vault.decrypt(connection.access_token_encrypted)

        # Expired: refresh using the stored refresh token.
        if connection.refresh_token_encrypted is None:
            connection.status = ConnectionStatus.EXPIRED
            connection.last_error = "Access token expired and no refresh token is available."
            self.db.add(connection)
            self.db.flush()
            raise GoogleOAuthError(connection.last_error)

        refresh_token = self.vault.decrypt(connection.refresh_token_encrypted)
        try:
            grant = self.oauth.refresh_access_token(refresh_token=refresh_token)
        except GoogleOAuthError as exc:
            connection.status = ConnectionStatus.EXPIRED
            # Store a user-facing reason with NO secret content.
            connection.last_error = "Token refresh failed; please reconnect this integration."
            self.db.add(connection)
            self.db.flush()
            raise exc

        connection.access_token_encrypted = self.vault.encrypt(grant.access_token)
        if grant.refresh_token:
            connection.refresh_token_encrypted = self.vault.encrypt(
                grant.refresh_token
            )
        connection.token_expires_at = grant.expires_at
        connection.status = ConnectionStatus.CONNECTED
        connection.last_error = None
        self.db.add(connection)
        self.db.flush()
        return grant.access_token

    def disconnect(
        self, org_id: UUID, user_id: UUID, connection_id: UUID
    ) -> IntegrationConnection:
        """Set a connection to ``REVOKED`` and stop future sync (Req 24.5).

        Records exactly one audit row in the same transaction (Requirement
        24.7). Existing imported data is retained; only future sync stops.
        """

        connection = self._get_scoped(org_id, connection_id, user_id)
        connection.status = ConnectionStatus.REVOKED
        self.db.add(connection)
        self.db.flush()

        AuditService(self.db).record(
            org_id=org_id,
            actor_id=user_id,
            action_type=DISCONNECT_INTEGRATION,
            target_type=_TARGET_TYPE,
            target_id=connection.id,
            detail={"service": connection.service.value},
        )
        return connection

    def revoke_google_access(
        self, org_id: UUID, user_id: UUID, connection_id: UUID
    ) -> IntegrationConnection:
        """Revoke Google access for a connection and disconnect it locally.

        Calls Google's token revocation endpoint (via the injected transport)
        and then locally sets the connection to ``REVOKED`` (Requirement 24.6),
        recording exactly one audit row in the same transaction (Requirement
        24.7). A revocation-call failure never leaks secret content; the local
        disconnect still proceeds so the platform stops using the tokens.
        """

        connection = self._get_scoped(org_id, connection_id, user_id)

        # Prefer revoking the refresh token (revokes the whole grant); fall back
        # to the access token. Decrypted only transiently for the outbound call.
        token_ciphertext = (
            connection.refresh_token_encrypted
            or connection.access_token_encrypted
        )
        try:
            token = self.vault.decrypt(token_ciphertext)
            self.oauth.revoke_token(token=token)
        except GoogleOAuthError:
            # Record a secret-free note but still disconnect locally.
            connection.last_error = "Google revocation call did not complete; connection disconnected locally."

        connection.status = ConnectionStatus.REVOKED
        self.db.add(connection)
        self.db.flush()

        AuditService(self.db).record(
            org_id=org_id,
            actor_id=user_id,
            action_type=REVOKE_INTEGRATION,
            target_type=_TARGET_TYPE,
            target_id=connection.id,
            detail={"service": connection.service.value},
        )
        return connection


__all__ = [
    "IntegrationService",
    "AuthorizationRedirect",
    "CONNECT_INTEGRATION",
    "DISCONNECT_INTEGRATION",
    "REVOKE_INTEGRATION",
]
