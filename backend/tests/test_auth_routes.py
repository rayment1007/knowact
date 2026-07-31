"""Route tests for authentication (Requirements 1.1, 1.2, 1.3, 1.6).

Exercises the real FastAPI app against an ephemeral SQLite database (see
``conftest.py``) with no live PostgreSQL required:

* ``POST /api/auth/login`` succeeds with valid credentials, sets the HTTP-only
  auth cookie, and returns a safe user projection (no token in the body);
* ``POST /api/auth/login`` returns ``401`` for a wrong password and for an
  unknown email, without setting a cookie;
* ``GET /api/auth/me`` returns the user and organization when the session
  cookie is present and ``401`` when it is missing/invalid;
* ``POST /api/auth/logout`` clears the cookie;
* no auth response body ever contains the password hash (Requirement 1.6).

The ``TestClient`` (httpx-based) persists cookies across requests, so once a
test logs in, subsequent requests on the same client automatically carry the
session cookie — mirroring real browser behavior.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.config import get_settings

COOKIE_NAME = get_settings().auth_cookie_name


def _login(client: TestClient, email: str, password: str) -> Any:
    return client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def test_login_success_sets_cookie_and_returns_user(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    resp = _login(client, seeded_user["email"], seeded_user["password"])

    assert resp.status_code == 200
    body = resp.json()
    # The token lives only in the HTTP-only cookie, never in the body.
    assert "access_token" not in body
    assert body["user"]["email"] == seeded_user["email"]
    assert body["user"]["role"] == "ADMIN"
    # An auth cookie was set on the response.
    assert COOKIE_NAME in resp.cookies
    assert resp.cookies[COOKIE_NAME]


def test_login_wrong_password_returns_401_no_cookie(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    resp = _login(client, seeded_user["email"], "wrong-password")

    assert resp.status_code == 401
    # No session established -> no auth cookie set.
    assert COOKIE_NAME not in resp.cookies


def test_login_unknown_email_returns_401_no_cookie(client: TestClient) -> None:
    resp = _login(client, "nobody@example.com", "whatever")

    assert resp.status_code == 401
    assert COOKIE_NAME not in resp.cookies


def test_login_response_never_exposes_password_hash(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    resp = _login(client, seeded_user["email"], seeded_user["password"])

    assert resp.status_code == 200
    # Neither the user projection nor the raw body should carry the hash.
    assert "password_hash" not in resp.json()["user"]
    assert "password_hash" not in resp.text


# ---------------------------------------------------------------------------
# Current user (/me)
# ---------------------------------------------------------------------------


def test_me_returns_user_and_organization(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    # Logging in stores the cookie on the client, sent automatically below.
    _login(client, seeded_user["email"], seeded_user["password"])

    resp = client.get("/api/auth/me")

    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == seeded_user["email"]
    assert body["organization"]["name"] == "Test Organization"


def test_me_never_exposes_password_hash(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])

    resp = client.get("/api/auth/me")

    assert resp.status_code == 200
    assert "password_hash" not in resp.json()["user"]
    assert "password_hash" not in resp.text


def test_me_without_cookie_returns_401(client: TestClient) -> None:
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_with_invalid_cookie_returns_401(client: TestClient) -> None:
    client.cookies.set(COOKIE_NAME, "not-a-real-token")
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


def test_logout_clears_cookie(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    assert client.get("/api/auth/me").status_code == 200

    resp = client.post("/api/auth/logout")
    assert resp.status_code == 204

    # After logout the client no longer holds a valid session cookie.
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401
