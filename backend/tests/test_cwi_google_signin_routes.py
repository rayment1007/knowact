"""Route tests for Google Sign-In via OpenID Connect (Requirement 23).

Exercises the real FastAPI app with a FAKE Google identity transport (no
network call): sign-in requests only ``openid email profile``, a valid identity
establishes the existing cookie session, verification failure yields 401 with no
session, and signing in creates NO IntegrationConnection and stores no token.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.core import models as core_models
from app.modules.cwi.dependencies import google_oauth_client
from app.modules.cwi.services.google_oauth import (
    FakeGoogleOAuthClient,
    GoogleIdentity,
)


def _override(client: TestClient, fake: FakeGoogleOAuthClient) -> None:
    client.app.dependency_overrides[google_oauth_client] = lambda: fake


def test_start_requests_only_openid_email_profile(client: TestClient) -> None:
    _override(client, FakeGoogleOAuthClient())
    resp = client.get("/api/auth/google/start")

    assert resp.status_code == 200
    body = resp.json()
    url = body["authorization_url"]
    assert body["state"]
    # Identity scopes only — never a Gmail/Calendar scope (Requirements 23.1, 23.3).
    assert "openid" in url and "email" in url and "profile" in url
    assert "gmail" not in url
    assert "calendar" not in url
    assert "httponly" in resp.headers["set-cookie"].lower()


def test_callback_establishes_session_for_existing_user(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    identity = GoogleIdentity(
        external_account_id="google-sub-1",
        email=seeded_user["email"],
        full_name="Test User",
        email_verified=True,
    )
    _override(client, FakeGoogleOAuthClient(identity=identity))

    state = client.get("/api/auth/google/start").json()["state"]
    resp = client.get(
        "/api/auth/google/callback",
        params={"code": "xyz", "state": state},
        follow_redirects=False,
    )

    # On success the browser is redirected back to the SPA home with the auth
    # cookie set (Requirement 23.2).
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("/")
    # Session established: /me now succeeds using the cookie.
    assert client.get("/api/auth/me").status_code == 200


def test_callback_verification_failure_redirects_no_session(
    client: TestClient,
) -> None:
    _override(client, FakeGoogleOAuthClient(fail_signin=True))

    state = client.get("/api/auth/google/start").json()["state"]
    resp = client.get(
        "/api/auth/google/callback",
        params={"code": "xyz", "state": state},
        follow_redirects=False,
    )

    # Verification failure redirects to the login page with an error and
    # establishes NO session (Requirement 23.6).
    assert resp.status_code == 302
    assert "/login?error=google_signin_failed" in resp.headers["location"]
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401


def test_callback_invalid_state_redirects_no_session(client: TestClient) -> None:
    _override(client, FakeGoogleOAuthClient())
    resp = client.get(
        "/api/auth/google/callback",
        params={"code": "xyz", "state": "not-a-valid-state"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/login?error=google_signin_failed" in resp.headers["location"]
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401


def test_signin_creates_no_integration_connection(
    client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    identity = GoogleIdentity(
        external_account_id="google-sub-1",
        email=seeded_user["email"],
        full_name="Test User",
        email_verified=True,
    )
    _override(client, FakeGoogleOAuthClient(identity=identity))

    state = client.get("/api/auth/google/start").json()["state"]
    resp = client.get(
        "/api/auth/google/callback",
        params={"code": "xyz", "state": state},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # Requirement 23.4: no IntegrationConnection is created by signing in.
    from app.modules.cwi.models import IntegrationConnection

    assert db_session.query(IntegrationConnection).count() == 0


def test_callback_upserts_new_user(client: TestClient, db_session: Any) -> None:
    identity = GoogleIdentity(
        external_account_id="google-sub-new",
        email="brand-new@example.com",
        full_name="Brand New",
        email_verified=True,
    )
    _override(client, FakeGoogleOAuthClient(identity=identity))

    state = client.get("/api/auth/google/start").json()["state"]
    resp = client.get(
        "/api/auth/google/callback",
        params={"code": "xyz", "state": state},
        follow_redirects=False,
    )

    assert resp.status_code == 302
    user = (
        db_session.query(core_models.User)
        .filter(core_models.User.email == "brand-new@example.com")
        .one()
    )
    assert user.full_name == "Brand New"


def test_callback_rejects_unverified_email_without_creating_account(
    client: TestClient, db_session: Any
) -> None:
    identity = GoogleIdentity(
        external_account_id="google-sub-unverified",
        email="owner@example.com",
        full_name="Owner",
        email_verified=False,
    )
    _override(client, FakeGoogleOAuthClient(identity=identity))
    client.app.dependency_overrides[get_settings] = lambda: Settings(
        auth_allowed_emails=["owner@example.com"]
    )
    users_before = db_session.query(core_models.User).count()
    orgs_before = db_session.query(core_models.Organization).count()

    state = client.get("/api/auth/google/start").json()["state"]
    response = client.get(
        "/api/auth/google/callback",
        params={"code": "xyz", "state": state},
        follow_redirects=False,
    )

    assert "/login?error=google_signin_failed" in response.headers["location"]
    assert db_session.query(core_models.User).count() == users_before
    assert db_session.query(core_models.Organization).count() == orgs_before
    assert client.get("/api/auth/me").status_code == 401


def test_callback_rejects_email_outside_allowlist_without_creating_account(
    client: TestClient, db_session: Any
) -> None:
    identity = GoogleIdentity(
        external_account_id="google-sub-unlisted",
        email="unlisted@example.com",
        full_name="Unlisted",
        email_verified=True,
    )
    _override(client, FakeGoogleOAuthClient(identity=identity))
    client.app.dependency_overrides[get_settings] = lambda: Settings(
        auth_allowed_emails=["owner@example.com"]
    )
    users_before = db_session.query(core_models.User).count()
    orgs_before = db_session.query(core_models.Organization).count()

    state = client.get("/api/auth/google/start").json()["state"]
    response = client.get(
        "/api/auth/google/callback",
        params={"code": "xyz", "state": state},
        follow_redirects=False,
    )

    assert "/login?error=google_signin_failed" in response.headers["location"]
    assert db_session.query(core_models.User).count() == users_before
    assert db_session.query(core_models.Organization).count() == orgs_before
    assert client.get("/api/auth/me").status_code == 401


def test_signin_state_is_bound_to_starting_browser(client: TestClient) -> None:
    fake = FakeGoogleOAuthClient()
    _override(client, fake)
    state = client.get("/api/auth/google/start").json()["state"]

    with TestClient(client.app) as other_browser:
        response = other_browser.get(
            "/api/auth/google/callback",
            params={"code": "xyz", "state": state},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert "/login?error=google_signin_failed" in response.headers["location"]


def test_signin_state_is_single_use(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    identity = GoogleIdentity(
        external_account_id="google-sub-replay",
        email=seeded_user["email"],
        full_name="Test User",
        email_verified=True,
    )
    _override(client, FakeGoogleOAuthClient(identity=identity))
    state = client.get("/api/auth/google/start").json()["state"]

    first = client.get(
        "/api/auth/google/callback",
        params={"code": "xyz", "state": state},
        follow_redirects=False,
    )
    replay = client.get(
        "/api/auth/google/callback",
        params={"code": "xyz", "state": state},
        follow_redirects=False,
    )

    assert first.headers["location"].endswith("/")
    assert "/login?error=google_signin_failed" in replay.headers["location"]


def test_expired_signin_state_is_rejected(client: TestClient) -> None:
    _override(client, FakeGoogleOAuthClient())
    client.get("/api/auth/google/start")
    nonce = client.cookies.get("knowact_google_signin_state")
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expired = jwt.encode(
        {
            "purpose": "cwi_signin",
            "nonce": nonce,
            "iat": now - timedelta(minutes=20),
            "exp": now - timedelta(minutes=10),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    response = client.get(
        "/api/auth/google/callback",
        params={"code": "xyz", "state": expired},
        follow_redirects=False,
    )
    assert "/login?error=google_signin_failed" in response.headers["location"]
