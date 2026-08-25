"""Google OAuth / OpenID Connect transport abstraction.

Identity verification and OAuth token exchange are expressed behind the
:class:`GoogleOAuthClient` protocol so the rest of the CWI code never talks to
Google directly. This is the seam that keeps the system **default-safe and
fully testable**:

* Tests inject :class:`FakeGoogleOAuthClient`, which performs **no network I/O**
  whatsoever — it returns deterministic identities and tokens. No CWI test ever
  makes a real OAuth call (Requirement 34).
* Production uses :class:`HttpGoogleOAuthClient`, a thin wrapper over Google's
  real HTTP endpoints, selected only when real client credentials are present.

The concrete client is resolved via :func:`get_google_oauth_client`, a FastAPI
dependency that routes override in tests. Sign-in (OpenID Connect) requests
**only** ``openid email profile`` and is kept separate from incremental service
authorization (Gmail/Calendar), which requests one capability scope at a time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable
from urllib.parse import urlencode

import httpx

from app.config import Settings, get_settings

# ---------------------------------------------------------------------------
# Google HTTP endpoints (production transport)
# ---------------------------------------------------------------------------

#: OAuth 2.0 / OpenID Connect authorization endpoint (browser redirect target).
GOOGLE_AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
#: Token endpoint for authorization-code exchange and refresh.
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
#: OpenID Connect UserInfo endpoint (sub / email / name claims).
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
#: Token revocation endpoint.
GOOGLE_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"

#: Short, fixed timeout for every outbound Google call.
_HTTP_TIMEOUT_SECONDS = 15

# ---------------------------------------------------------------------------
# Scope constants
# ---------------------------------------------------------------------------

#: Sign-in requests ONLY these OpenID Connect scopes (Requirements 23.1, 23.3).
OIDC_SIGNIN_SCOPES: tuple[str, ...] = ("openid", "email", "profile")

#: Per-service incremental authorization scopes (Requirement 24.2). One
#: capability is requested at a time; ``gmail.compose`` is added in a later
#: phase (only when the user first uses AI draft), never here.
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
#: Read-only access to the user's calendar LIST (needed to populate the
#: "choose a calendar" dropdown via ``calendarList.list``). The narrower
#: ``calendar.events`` scope alone does NOT permit listing calendars, so this
#: read-only companion scope is requested alongside it.
CALENDAR_CALENDARLIST_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
)
#: Gmail compose/send scope (M6.6). Requested incrementally ONLY when the user
#: first uses AI draft — never at basic sign-in (Requirement 32.3).
GMAIL_COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"


# ---------------------------------------------------------------------------
# Transport data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoogleIdentity:
    """The verified identity claims returned by an OpenID Connect sign-in."""

    external_account_id: str  # the OIDC ``sub``
    email: str
    full_name: str
    email_verified: bool


@dataclass(frozen=True)
class GoogleTokenGrant:
    """The result of exchanging (or refreshing) an OAuth authorization.

    ``granted_scopes`` are the scopes Google actually granted (not merely those
    requested), so the connection records what was truly authorized
    (Requirement 24.3). Token strings exist only transiently in memory and are
    encrypted before persistence.
    """

    access_token: str
    refresh_token: str | None
    expires_at: datetime
    granted_scopes: list[str] = field(default_factory=list)
    external_account_id: str = ""
    account_email: str = ""


class GoogleOAuthError(Exception):
    """Raised when a Google OAuth/identity operation fails.

    The message is safe to surface to users / store in ``last_error``: it must
    never contain a token or key.
    """


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class GoogleOAuthClient(Protocol):
    """The seam every CWI service uses to reach Google.

    A production implementation performs real HTTP; the test implementation is
    a deterministic fake. No CWI code depends on the concrete type.
    """

    def build_signin_url(self, *, redirect_uri: str, state: str) -> str:
        """Return the OIDC authorize URL for sign-in (openid email profile)."""

    def exchange_signin_code(
        self, *, code: str, redirect_uri: str
    ) -> GoogleIdentity:
        """Verify the identity token for ``code`` and return its claims.

        Raises:
            GoogleOAuthError: if verification fails (yields a 401 upstream).
        """

    def build_service_url(
        self, *, scopes: list[str], redirect_uri: str, state: str
    ) -> str:
        """Return the incremental authorization URL for a service scope."""

    def exchange_service_code(
        self, *, code: str, redirect_uri: str
    ) -> GoogleTokenGrant:
        """Exchange an authorization ``code`` for access/refresh tokens."""

    def refresh_access_token(self, *, refresh_token: str) -> GoogleTokenGrant:
        """Refresh an access token using a stored refresh token.

        Raises:
            GoogleOAuthError: if the refresh fails.
        """

    def revoke_token(self, *, token: str) -> None:
        """Revoke a token at Google's revocation endpoint."""


# ---------------------------------------------------------------------------
# Fake implementation (tests / local mock) — NO network I/O
# ---------------------------------------------------------------------------


class FakeGoogleOAuthClient:
    """A deterministic, offline :class:`GoogleOAuthClient` for tests/local dev.

    Every method returns canned values derived from its inputs; nothing touches
    the network. Callers can pre-seed identities/grants or rely on the defaults.
    A ``fail_*`` toggle lets tests exercise verification/refresh failure paths.
    """

    def __init__(
        self,
        *,
        identity: GoogleIdentity | None = None,
        grant: GoogleTokenGrant | None = None,
        fail_signin: bool = False,
        fail_refresh: bool = False,
        access_token_ttl_seconds: int = 3600,
    ) -> None:
        self._identity = identity
        self._grant = grant
        self.fail_signin = fail_signin
        self.fail_refresh = fail_refresh
        self._ttl = access_token_ttl_seconds
        # Recorded calls, useful for assertions in tests.
        self.revoked_tokens: list[str] = []
        self.refresh_calls: int = 0

    # -- Sign-in (OpenID Connect) -------------------------------------------

    def build_signin_url(self, *, redirect_uri: str, state: str) -> str:
        scope = "+".join(OIDC_SIGNIN_SCOPES)
        return (
            "https://accounts.google.com/o/oauth2/v2/auth"
            f"?response_type=code&scope={scope}"
            f"&redirect_uri={redirect_uri}&state={state}"
        )

    def exchange_signin_code(
        self, *, code: str, redirect_uri: str
    ) -> GoogleIdentity:
        if self.fail_signin:
            raise GoogleOAuthError("Identity token verification failed.")
        if self._identity is not None:
            return self._identity
        # Deterministically derive an identity from the code.
        return GoogleIdentity(
            external_account_id=f"google-sub-{code}",
            email=f"{code}@example.com",
            full_name=f"User {code}",
            email_verified=True,
        )

    # -- Incremental service authorization ----------------------------------

    def build_service_url(
        self, *, scopes: list[str], redirect_uri: str, state: str
    ) -> str:
        scope = "+".join(scopes)
        return (
            "https://accounts.google.com/o/oauth2/v2/auth"
            f"?response_type=code&access_type=offline&scope={scope}"
            f"&redirect_uri={redirect_uri}&state={state}"
        )

    def exchange_service_code(
        self, *, code: str, redirect_uri: str
    ) -> GoogleTokenGrant:
        if self._grant is not None:
            return self._grant
        now = datetime.now(timezone.utc)
        return GoogleTokenGrant(
            access_token=f"access-{code}",
            refresh_token=f"refresh-{code}",
            expires_at=now + timedelta(seconds=self._ttl),
            granted_scopes=[GMAIL_READONLY_SCOPE, GMAIL_COMPOSE_SCOPE],
            external_account_id=f"google-sub-{code}",
            account_email=f"{code}@example.com",
        )

    def refresh_access_token(self, *, refresh_token: str) -> GoogleTokenGrant:
        self.refresh_calls += 1
        if self.fail_refresh:
            raise GoogleOAuthError("Token refresh failed.")
        now = datetime.now(timezone.utc)
        return GoogleTokenGrant(
            access_token=f"access-refreshed-{self.refresh_calls}",
            refresh_token=refresh_token,
            expires_at=now + timedelta(seconds=self._ttl),
            granted_scopes=[GMAIL_READONLY_SCOPE],
        )

    def revoke_token(self, *, token: str) -> None:
        self.revoked_tokens.append(token)


# ---------------------------------------------------------------------------
# HTTP implementation (production) — thin wrapper, injectable & default-safe
# ---------------------------------------------------------------------------


class HttpGoogleOAuthClient:
    """A production :class:`GoogleOAuthClient` over Google's real HTTP endpoints.

    Selected only when Google client credentials are configured (see
    :func:`get_google_oauth_client`); tests never construct it against the live
    network. Authorization URLs are built by pure string assembly (no network);
    code exchange, refresh, and revocation perform short-timeout HTTP calls.

    Secrets discipline: the client id/secret and access/refresh tokens are used
    only as request parameters/headers. They are **never** placed in exception
    messages or logs — every failure raises :class:`GoogleOAuthError` with a
    fixed, secret-free message.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        # Tests inject an ``httpx.MockTransport`` so no real network call occurs.
        self._transport = transport

    # -- Internal helpers ---------------------------------------------------

    def _client(self) -> httpx.Client:
        """Build a short-timeout HTTP client (test-injectable transport)."""

        return httpx.Client(
            timeout=_HTTP_TIMEOUT_SECONDS, transport=self._transport
        )

    def _fetch_userinfo(
        self, http: httpx.Client, access_token: str
    ) -> dict[str, object]:
        """Return the OIDC UserInfo claims for ``access_token``."""

        resp = http.get(
            GOOGLE_USERINFO_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()

    # -- Sign-in (OpenID Connect) -------------------------------------------

    def build_signin_url(self, *, redirect_uri: str, state: str) -> str:
        params = {
            "client_id": self._settings.google_oauth_client_id or "",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
            "include_granted_scopes": "true",
            "prompt": "select_account",
        }
        return f"{GOOGLE_AUTHORIZE_ENDPOINT}?{urlencode(params)}"

    def exchange_signin_code(
        self, *, code: str, redirect_uri: str
    ) -> GoogleIdentity:
        data = {
            "code": code,
            "client_id": self._settings.google_oauth_client_id or "",
            "client_secret": self._settings.google_oauth_client_secret or "",
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        try:
            with self._client() as http:
                token_resp = http.post(GOOGLE_TOKEN_ENDPOINT, data=data)
                token_resp.raise_for_status()
                access_token = token_resp.json()["access_token"]
                userinfo = self._fetch_userinfo(http, access_token)
                if userinfo.get("email_verified") is not True:
                    raise GoogleOAuthError(
                        "Google sign-in email address is not verified."
                    )
                return GoogleIdentity(
                    external_account_id=str(userinfo["sub"]),
                    email=str(userinfo["email"]),
                    full_name=str(userinfo.get("name", "")),
                    email_verified=True,
                )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise GoogleOAuthError(
                "Google sign-in could not be verified."
            ) from exc

    # -- Incremental service authorization ----------------------------------

    def build_service_url(
        self, *, scopes: list[str], redirect_uri: str, state: str
    ) -> str:
        params = {
            "client_id": self._settings.google_oauth_client_id or "",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
        }
        return f"{GOOGLE_AUTHORIZE_ENDPOINT}?{urlencode(params)}"

    def exchange_service_code(
        self, *, code: str, redirect_uri: str
    ) -> GoogleTokenGrant:
        data = {
            "code": code,
            "client_id": self._settings.google_oauth_client_id or "",
            "client_secret": self._settings.google_oauth_client_secret or "",
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        try:
            with self._client() as http:
                token_resp = http.post(GOOGLE_TOKEN_ENDPOINT, data=data)
                token_resp.raise_for_status()
                payload = token_resp.json()
                access_token = payload["access_token"]
                userinfo = self._fetch_userinfo(http, access_token)
                if userinfo.get("email_verified") is not True:
                    raise GoogleOAuthError(
                        "Google account email address is not verified."
                    )
                return GoogleTokenGrant(
                    access_token=access_token,
                    refresh_token=payload.get("refresh_token"),
                    expires_at=self._expires_at(payload),
                    granted_scopes=self._granted_scopes(payload),
                    external_account_id=str(userinfo.get("sub", "")),
                    account_email=str(userinfo.get("email", "")),
                )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise GoogleOAuthError(
                "Google authorization could not be completed."
            ) from exc

    def refresh_access_token(self, *, refresh_token: str) -> GoogleTokenGrant:
        data = {
            "refresh_token": refresh_token,
            "client_id": self._settings.google_oauth_client_id or "",
            "client_secret": self._settings.google_oauth_client_secret or "",
            "grant_type": "refresh_token",
        }
        try:
            with self._client() as http:
                resp = http.post(GOOGLE_TOKEN_ENDPOINT, data=data)
                resp.raise_for_status()
                payload = resp.json()
                return GoogleTokenGrant(
                    access_token=payload["access_token"],
                    # A refresh response usually omits a new refresh token.
                    refresh_token=payload.get("refresh_token"),
                    expires_at=self._expires_at(payload),
                    granted_scopes=self._granted_scopes(payload),
                )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise GoogleOAuthError("Google token refresh failed.") from exc

    def revoke_token(self, *, token: str) -> None:
        try:
            with self._client() as http:
                resp = http.post(
                    GOOGLE_REVOKE_ENDPOINT, data={"token": token}
                )
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise GoogleOAuthError("Google token revocation failed.") from exc

    # -- Parsing helpers ----------------------------------------------------

    @staticmethod
    def _expires_at(payload: dict[str, object]) -> datetime:
        expires_in = int(payload.get("expires_in", 0) or 0)
        return datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    @staticmethod
    def _granted_scopes(payload: dict[str, object]) -> list[str]:
        scope = str(payload.get("scope", "") or "")
        return scope.split() if scope else []


# ---------------------------------------------------------------------------
# Provider / FastAPI dependency
# ---------------------------------------------------------------------------


def get_google_oauth_client(
    settings: Settings | None = None,
) -> GoogleOAuthClient:
    """Resolve the :class:`GoogleOAuthClient` for the current configuration.

    Returns the production :class:`HttpGoogleOAuthClient` when real Google
    client credentials are configured; otherwise a deterministic
    :class:`FakeGoogleOAuthClient` so local/mock development runs with no network
    dependency and no real OAuth call. Route handlers depend on this function
    and tests override it with a pre-seeded fake, so no network I/O ever occurs
    under test.
    """

    settings = settings or get_settings()
    if settings.google_oauth_client_id and settings.google_oauth_client_secret:
        return HttpGoogleOAuthClient(settings)
    return FakeGoogleOAuthClient()


__all__ = [
    "OIDC_SIGNIN_SCOPES",
    "GMAIL_READONLY_SCOPE",
    "CALENDAR_EVENTS_SCOPE",
    "CALENDAR_CALENDARLIST_READONLY_SCOPE",
    "GMAIL_COMPOSE_SCOPE",
    "GoogleIdentity",
    "GoogleTokenGrant",
    "GoogleOAuthError",
    "GoogleOAuthClient",
    "FakeGoogleOAuthClient",
    "HttpGoogleOAuthClient",
    "get_google_oauth_client",
]
