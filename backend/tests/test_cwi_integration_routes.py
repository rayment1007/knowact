"""Route tests for the CWI integration endpoints (Requirements 24, 25).

Exercises the real FastAPI app against the ephemeral SQLite database with a
FAKE Google OAuth transport injected (no network/OAuth call ever occurs) and a
test Fernet key configured so the TokenVault can encrypt tokens. Covers the
connect → callback → list → disconnect/revoke lifecycle plus cross-org 404 and
token-safety of every response.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.core import models as core_models
from app.modules.cwi.dependencies import google_oauth_client
from app.modules.cwi.models import ConnectionStatus
from app.modules.cwi.services.google_oauth import (
    GMAIL_COMPOSE_SCOPE,
    GMAIL_READONLY_SCOPE,
    FakeGoogleOAuthClient,
    GoogleTokenGrant,
)

_TEST_KEY = Fernet.generate_key().decode()
# Token columns/fields that must never surface in a response body.
_FORBIDDEN_FIELDS = ("access_token_encrypted", "refresh_token_encrypted")


@pytest.fixture()
def fake_google() -> FakeGoogleOAuthClient:
    return FakeGoogleOAuthClient()


@pytest.fixture()
def cwi_client(
    client: TestClient,
    fake_google: FakeGoogleOAuthClient,
) -> TestClient:
    """The shared test client with token key + fake Google transport wired in."""

    test_settings = Settings(token_encryption_key=_TEST_KEY)
    client.app.dependency_overrides[get_settings] = lambda: test_settings
    client.app.dependency_overrides[google_oauth_client] = lambda: fake_google
    return client


def _login(client: TestClient, seeded_user: dict[str, Any]) -> None:
    resp = client.post(
        "/api/auth/login",
        json={"email": seeded_user["email"], "password": seeded_user["password"]},
    )
    assert resp.status_code == 200


def _connect_gmail(client: TestClient) -> dict[str, Any]:
    """Run connect → callback and return the created connection view.

    The callback now issues a browser redirect (302) back to the SPA rather than
    returning JSON, so we assert the redirect + its side effects and then read
    the persisted connection back via the token-safe list view.
    """

    begin = client.post("/api/integrations/GMAIL/connect")
    assert begin.status_code == 200
    state = begin.json()["state"]
    assert begin.json()["authorization_url"]

    callback = client.get(
        "/api/integrations/callback",
        params={"code": "abc", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert "/integrations?connected=GMAIL" in callback.headers["location"]

    listed = client.get("/api/integrations").json()
    assert listed
    return listed[0]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_connect_callback_creates_connected_connection(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    view = _connect_gmail(cwi_client)

    assert view["status"] == ConnectionStatus.CONNECTED.value
    assert view["service"] == "GMAIL"
    assert view["account_email"] == "abc@example.com"
    assert view["granted_scopes"] == [
        GMAIL_READONLY_SCOPE,
        GMAIL_COMPOSE_SCOPE,
    ]
    # No token field is ever present.
    for field in _FORBIDDEN_FIELDS:
        assert field not in view


def test_service_authorization_requests_identity_and_capability_scopes(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)

    response = cwi_client.post("/api/integrations/GMAIL/connect")
    url = response.json()["authorization_url"]

    assert response.status_code == 200
    assert "openid" in url and "email" in url and "profile" in url
    assert "gmail.readonly" in url and "gmail.compose" in url


def test_callback_rejects_partial_scope_grant_without_persisting_connection(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
) -> None:
    _login(cwi_client, seeded_user)
    begin = cwi_client.post("/api/integrations/GMAIL/connect")
    partial = GoogleTokenGrant(
        access_token="access-partial",
        refresh_token="refresh-partial",
        expires_at=datetime.now(timezone.utc).replace(year=2999),
        granted_scopes=[GMAIL_READONLY_SCOPE],
        external_account_id="google-sub-partial",
        account_email="partial@example.com",
    )
    cwi_client.app.dependency_overrides[google_oauth_client] = lambda: (
        FakeGoogleOAuthClient(grant=partial)
    )

    callback = cwi_client.get(
        "/api/integrations/callback",
        params={"code": "abc", "state": begin.json()["state"]},
        follow_redirects=False,
    )

    assert "/integrations?error=connect_failed" in callback.headers["location"]
    from app.modules.cwi.models import IntegrationConnection

    assert db_session.query(IntegrationConnection).count() == 0


def test_connect_writes_connect_integration_audit(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    _connect_gmail(cwi_client)

    audits = (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == "CONNECT_INTEGRATION")
        .all()
    )
    assert len(audits) == 1
    assert audits[0].target_type == "IntegrationConnection"


def test_list_integrations_returns_connection_without_tokens(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    _connect_gmail(cwi_client)

    resp = cwi_client.get("/api/integrations")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    for field in _FORBIDDEN_FIELDS:
        assert field not in resp.text
        assert field not in body[0]


def test_disconnect_sets_revoked(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    view = _connect_gmail(cwi_client)

    resp = cwi_client.post(f"/api/integrations/{view['id']}/disconnect")
    assert resp.status_code == 200
    assert resp.json()["status"] == ConnectionStatus.REVOKED.value


def test_revoke_calls_google_and_disconnects(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    fake_google: FakeGoogleOAuthClient,
) -> None:
    _login(cwi_client, seeded_user)
    view = _connect_gmail(cwi_client)

    resp = cwi_client.post(f"/api/integrations/{view['id']}/revoke")
    assert resp.status_code == 200
    assert resp.json()["status"] == ConnectionStatus.REVOKED.value
    # The fake transport recorded exactly one revoked token (decrypted only
    # transiently for the outbound call).
    assert len(fake_google.revoked_tokens) == 1


# ---------------------------------------------------------------------------
# Auth + cross-org isolation
# ---------------------------------------------------------------------------


def test_integrations_require_authentication(cwi_client: TestClient) -> None:
    assert cwi_client.get("/api/integrations").status_code == 401


def test_cross_org_connection_returns_404(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
) -> None:
    _login(cwi_client, seeded_user)
    view = _connect_gmail(cwi_client)
    connection_id = view["id"]

    # A second organization with its own user logs in on a fresh client sharing
    # the same DB session/app (via the same dependency overrides).
    other_org = core_models.Organization(name="Other Org")
    db_session.add(other_org)
    db_session.flush()
    from app.security import hash_password

    other_user = core_models.User(
        organization_id=other_org.id,
        email="other@example.com",
        full_name="Other",
        password_hash=hash_password("pw2"),
        role="ADMIN",
    )
    db_session.add(other_user)
    db_session.flush()

    # Re-login as the other user (clears prior cookie).
    cwi_client.cookies.clear()
    resp = cwi_client.post(
        "/api/auth/login", json={"email": "other@example.com", "password": "pw2"}
    )
    assert resp.status_code == 200

    # The other org must not see or mutate the first org's connection.
    assert cwi_client.post(
        f"/api/integrations/{connection_id}/disconnect"
    ).status_code == 404
    assert cwi_client.post(
        f"/api/integrations/{connection_id}/revoke"
    ).status_code == 404
    # And it is absent from their list.
    listed = cwi_client.get("/api/integrations").json()
    assert listed == []
