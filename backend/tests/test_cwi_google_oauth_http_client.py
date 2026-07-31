"""Unit tests for the production :class:`HttpGoogleOAuthClient`.

These tests exercise the REAL client but perform NO network I/O: every HTTP
call is served by an in-memory ``httpx.MockTransport``. They verify that:

* ``build_signin_url`` / ``build_service_url`` assemble the correct Google
  authorize endpoint, client id, scopes, redirect uri, state, and the
  online/offline + prompt parameters (pure string building — no network).
* ``exchange_signin_code`` / ``exchange_service_code`` / ``refresh_access_token``
  parse mocked token + userinfo responses into ``GoogleIdentity`` /
  ``GoogleTokenGrant``.
* Any HTTP failure is surfaced as a secret-free ``GoogleOAuthError``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.config import Settings
from app.modules.cwi.services.google_oauth import (
    CALENDAR_EVENTS_SCOPE,
    GMAIL_READONLY_SCOPE,
    GOOGLE_AUTHORIZE_ENDPOINT,
    GoogleIdentity,
    GoogleOAuthError,
    HttpGoogleOAuthClient,
)

_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
_CLIENT_SECRET = "super-secret-value"

_SETTINGS = Settings(
    google_oauth_client_id=_CLIENT_ID,
    google_oauth_client_secret=_CLIENT_SECRET,
)


def _client(handler) -> HttpGoogleOAuthClient:
    """A client whose outbound HTTP is served by an in-memory mock transport."""

    return HttpGoogleOAuthClient(
        _SETTINGS, transport=httpx.MockTransport(handler)
    )


# ---------------------------------------------------------------------------
# URL building (pure string assembly, no network)
# ---------------------------------------------------------------------------


def test_build_signin_url_contains_oidc_params() -> None:
    client = HttpGoogleOAuthClient(_SETTINGS)
    url = client.build_signin_url(
        redirect_uri="http://localhost:8000/api/auth/google/callback",
        state="state-123",
    )

    assert url.startswith(GOOGLE_AUTHORIZE_ENDPOINT + "?")
    assert f"client_id={_CLIENT_ID}" in url
    assert "response_type=code" in url
    # urlencode renders spaces as '+'.
    assert "scope=openid+email+profile" in url
    assert "access_type=online" in url
    assert "include_granted_scopes=true" in url
    assert "prompt=select_account" in url
    assert "state=state-123" in url
    assert (
        "redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fapi%2Fauth%2Fgoogle%2Fcallback"
        in url
    )
    # The secret is NEVER placed in a browser-facing URL.
    assert _CLIENT_SECRET not in url
    # Sign-in requests identity only — never a service scope.
    assert "gmail" not in url
    assert "calendar" not in url


def test_build_service_url_requests_offline_consent_and_scopes() -> None:
    client = HttpGoogleOAuthClient(_SETTINGS)
    url = client.build_service_url(
        scopes=[GMAIL_READONLY_SCOPE, CALENDAR_EVENTS_SCOPE],
        redirect_uri="http://localhost:8000/api/integrations/callback",
        state="svc-state",
    )

    assert url.startswith(GOOGLE_AUTHORIZE_ENDPOINT + "?")
    assert f"client_id={_CLIENT_ID}" in url
    assert "response_type=code" in url
    # A refresh token is guaranteed via offline access + a forced consent prompt.
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "include_granted_scopes=true" in url
    assert "state=svc-state" in url
    # Both requested scopes are present (space-joined then url-encoded).
    assert "gmail.readonly" in url
    assert "calendar.events" in url
    assert _CLIENT_SECRET not in url


# ---------------------------------------------------------------------------
# Code exchange + refresh (mocked HTTP)
# ---------------------------------------------------------------------------


def _token_and_userinfo_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/token":
        return httpx.Response(
            200,
            json={
                "access_token": "access-token-abc",
                "refresh_token": "refresh-token-xyz",
                "expires_in": 3600,
                "scope": GMAIL_READONLY_SCOPE,
            },
        )
    if path == "/v1/userinfo":
        assert request.headers["Authorization"] == "Bearer access-token-abc"
        return httpx.Response(
            200,
            json={
                "sub": "google-sub-42",
                "email": "person@example.com",
                "name": "Real Person",
            },
        )
    raise AssertionError(f"unexpected request path: {path}")


def test_exchange_signin_code_returns_identity() -> None:
    client = _client(_token_and_userinfo_handler)

    identity = client.exchange_signin_code(
        code="auth-code", redirect_uri="http://localhost:8000/cb"
    )

    assert identity == GoogleIdentity(
        external_account_id="google-sub-42",
        email="person@example.com",
        full_name="Real Person",
    )


def test_exchange_service_code_returns_grant() -> None:
    client = _client(_token_and_userinfo_handler)

    grant = client.exchange_service_code(
        code="auth-code", redirect_uri="http://localhost:8000/cb"
    )

    assert grant.access_token == "access-token-abc"
    assert grant.refresh_token == "refresh-token-xyz"
    assert grant.granted_scopes == [GMAIL_READONLY_SCOPE]
    assert grant.external_account_id == "google-sub-42"
    assert grant.account_email == "person@example.com"
    assert grant.expires_at > datetime.now(timezone.utc)


def test_refresh_access_token_returns_new_grant() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/token"
        return httpx.Response(
            200,
            json={
                "access_token": "access-token-refreshed",
                "expires_in": 1800,
                # A refresh response typically omits a new refresh token.
            },
        )

    client = _client(handler)
    grant = client.refresh_access_token(refresh_token="refresh-token-xyz")

    assert grant.access_token == "access-token-refreshed"
    assert grant.refresh_token is None
    assert grant.granted_scopes == []
    assert grant.expires_at > datetime.now(timezone.utc)


def test_revoke_token_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/revoke"
        return httpx.Response(200)

    client = _client(handler)
    # No exception means success.
    client.revoke_token(token="some-token")


# ---------------------------------------------------------------------------
# Error handling — every failure becomes a secret-free GoogleOAuthError
# ---------------------------------------------------------------------------


def test_exchange_signin_code_http_error_raises_oauth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    client = _client(handler)
    with pytest.raises(GoogleOAuthError) as exc_info:
        client.exchange_signin_code(
            code="bad", redirect_uri="http://localhost:8000/cb"
        )
    # The message must never leak the client secret.
    assert _CLIENT_SECRET not in str(exc_info.value)


def test_exchange_service_code_http_error_raises_oauth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _client(handler)
    with pytest.raises(GoogleOAuthError):
        client.exchange_service_code(
            code="bad", redirect_uri="http://localhost:8000/cb"
        )


def test_refresh_access_token_http_error_raises_oauth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    client = _client(handler)
    with pytest.raises(GoogleOAuthError):
        client.refresh_access_token(refresh_token="bad")


def test_revoke_token_http_error_raises_oauth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400)

    client = _client(handler)
    with pytest.raises(GoogleOAuthError):
        client.revoke_token(token="bad")


def test_exchange_signin_code_missing_field_raises_oauth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            # Malformed: no access_token in the token response.
            return httpx.Response(200, json={"token_type": "Bearer"})
        raise AssertionError("userinfo should not be reached")

    client = _client(handler)
    with pytest.raises(GoogleOAuthError):
        client.exchange_signin_code(
            code="c", redirect_uri="http://localhost:8000/cb"
        )
