"""Property-based test P14: tokens are never serialized to API responses.

Property 14 (tokens never serialized): no integration API response body ever
contains ``access_token_encrypted``, ``refresh_token_encrypted``, decrypted
token text, or the encryption key (Requirements 25.2, 25.3).

Each Hypothesis example persists an :class:`IntegrationConnection` whose access
and refresh tokens are arbitrary generated secrets (encrypted with the same
Fernet key the app is configured with), then hits every integration response
surface — the ``GET /api/integrations`` list, the ``connect`` redirect, the
``callback`` view, and the ``disconnect``/``revoke`` views — asserting that none
of the response bodies contain the token plaintext, the encrypted-column names,
or the encryption key. A FAKE Google transport is used, so no real OAuth call
occurs. Per Requirement 21.3 the property runs a minimum of 100 examples.

**Validates: Requirements 25.2, 25.3 / Property 14**
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.config import Settings, get_settings
from app.core import models as core_models
from app.modules.cwi.dependencies import google_oauth_client
from app.modules.cwi.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationService as IntegrationServiceEnum,
)
from app.modules.cwi.services.google_oauth import FakeGoogleOAuthClient
from app.modules.cwi.services.token_vault import TokenVault

_TEST_KEY = Fernet.generate_key().decode()
_TEST_SETTINGS = Settings(token_encryption_key=_TEST_KEY)
_VAULT = TokenVault(_TEST_SETTINGS)

# The encrypted-column names must never appear in a response body.
_FORBIDDEN_FIELD_NAMES = ("access_token_encrypted", "refresh_token_encrypted")


@pytest.fixture()
def authed_client(
    client: TestClient, seeded_user: dict[str, Any]
) -> TestClient:
    """A logged-in client wired with the test key + fake Google transport."""

    client.app.dependency_overrides[get_settings] = lambda: _TEST_SETTINGS
    client.app.dependency_overrides[google_oauth_client] = (
        lambda: FakeGoogleOAuthClient()
    )
    resp = client.post(
        "/api/auth/login",
        json={
            "email": seeded_user["email"],
            "password": seeded_user["password"],
        },
    )
    assert resp.status_code == 200
    return client


def _persist_connection(
    db: Any, org_id: Any, user_id: Any, access_tok: str, refresh_tok: str
) -> IntegrationConnection:
    """Create a CONNECTED connection whose tokens are the given secrets."""

    connection = IntegrationConnection(
        organization_id=org_id,
        user_id=user_id,
        provider=IntegrationProvider.GOOGLE,
        service=IntegrationServiceEnum.GMAIL,
        external_account_id=f"sub-{uuid4().hex}",
        account_email=f"acct-{uuid4().hex}@example.com",
        granted_scopes_json=["https://www.googleapis.com/auth/gmail.readonly"],
        access_token_encrypted=_VAULT.encrypt(access_tok),
        refresh_token_encrypted=_VAULT.encrypt(refresh_tok),
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        status=ConnectionStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    return connection


def _assert_token_safe(text: str, access_tok: str, refresh_tok: str) -> None:
    """No token plaintext, column name, or encryption key is in ``text``."""

    assert access_tok not in text
    assert refresh_tok not in text
    assert _TEST_KEY not in text
    for name in _FORBIDDEN_FIELD_NAMES:
        assert name not in text


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    access_suffix=st.text(min_size=0, max_size=40),
    refresh_suffix=st.text(min_size=0, max_size=40),
)
def test_tokens_never_serialized(
    authed_client: TestClient,
    db_session: Any,
    seeded_user: dict[str, Any],
    access_suffix: str,
    refresh_suffix: str,
) -> None:
    """No integration response body leaks tokens, ciphertext columns, or the key.

    **Validates: Requirements 25.2, 25.3 / Property 14**
    """

    # Distinctive secrets so a match is unambiguous (never coincidental).
    access_tok = f"ACCESS-SECRET-{uuid4().hex}-{access_suffix}"
    refresh_tok = f"REFRESH-SECRET-{uuid4().hex}-{refresh_suffix}"

    org_id = seeded_user["organization"].id
    user_id = seeded_user["user"].id
    connection = _persist_connection(
        db_session, org_id, user_id, access_tok, refresh_tok
    )

    # -- GET /api/integrations (list view) ----------------------------------
    listed = authed_client.get("/api/integrations")
    assert listed.status_code == 200
    _assert_token_safe(listed.text, access_tok, refresh_tok)

    # -- connect redirect ---------------------------------------------------
    begin = authed_client.post("/api/integrations/GMAIL/connect")
    assert begin.status_code == 200
    _assert_token_safe(begin.text, access_tok, refresh_tok)

    # -- callback redirect (fresh connection via fake transport) ------------
    callback = authed_client.get(
        "/api/integrations/callback",
        params={"code": "abc", "state": begin.json()["state"]},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    _assert_token_safe(callback.text, access_tok, refresh_tok)
    _assert_token_safe(callback.headers["location"], access_tok, refresh_tok)

    # -- disconnect + revoke views ------------------------------------------
    disc = authed_client.post(f"/api/integrations/{connection.id}/disconnect")
    assert disc.status_code == 200
    _assert_token_safe(disc.text, access_tok, refresh_tok)

    revoke = authed_client.post(f"/api/integrations/{connection.id}/revoke")
    assert revoke.status_code == 200
    _assert_token_safe(revoke.text, access_tok, refresh_tok)
