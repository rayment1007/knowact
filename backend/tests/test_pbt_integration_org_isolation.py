"""Property-based test P13: organization isolation of integration connections.

Property 13 (org isolation of integrations): a user can read or write an
:class:`~app.modules.cwi.models.IntegrationConnection` only when
``row.organization_id == user.organization_id``. A request for a connection
belonging to a *different* organization must fail with ``404 Not Found`` and
reveal nothing about its existence (never a ``403``) — Requirements 24.8, 25.5.

Each Hypothesis example seeds **two** fully independent organizations, each with
its own user and an ``IntegrationConnection`` whose tokens are encrypted via the
:class:`~app.modules.cwi.services.token_vault.TokenVault`, then asserts isolation
in **both** directions across the :class:`IntegrationService` read/write surface:

* read — ``get_connection`` and ``get_valid_access_token``;
* write — ``disconnect`` and ``revoke_google_access``.

All Google I/O is performed through a FAKE transport, so no network/OAuth call
ever occurs. Per Requirement 21.3 the property runs a minimum of 100 examples.

**Validates: Requirements 24.8, 25.5 / Property 13**
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

from app.config import Settings
from app.core import models as core_models
from app.modules.cwi.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationService as IntegrationServiceEnum,
)
from app.modules.cwi.services.google_oauth import FakeGoogleOAuthClient
from app.modules.cwi.services.integration_service import IntegrationService
from app.modules.cwi.services.token_vault import TokenVault
from app.security import hash_password

# bcrypt is slow; hash the throwaway password once per module.
_PW_HASH = hash_password("pw")

# A stable Fernet key so the vault can encrypt/decrypt within the test process.
_TEST_SETTINGS = Settings(token_encryption_key=Fernet.generate_key().decode())
_VAULT = TokenVault(_TEST_SETTINGS)


class _OrgFixture:
    """A seeded org + user + one IntegrationConnection scoped to that org."""

    def __init__(
        self,
        org: core_models.Organization,
        user: core_models.User,
        connection: IntegrationConnection,
    ) -> None:
        self.org = org
        self.user = user
        self.connection = connection


def _seed_org(db: Session, name: str) -> _OrgFixture:
    """Create an org + user and one connected IntegrationConnection."""

    suffix = uuid4().hex
    org = core_models.Organization(name=f"{name}-{suffix}")
    db.add(org)
    db.flush()

    user = core_models.User(
        organization_id=org.id,
        email=f"user-{suffix}@example.com",
        full_name="Actor",
        password_hash=_PW_HASH,
        role="ADMIN",
    )
    db.add(user)
    db.flush()

    # Expired-in-the-future token so get_valid_access_token does NOT refresh.
    connection = IntegrationConnection(
        organization_id=org.id,
        user_id=user.id,
        provider=IntegrationProvider.GOOGLE,
        service=IntegrationServiceEnum.GMAIL,
        external_account_id=f"sub-{suffix}",
        account_email=f"mailbox-{suffix}@example.com",
        granted_scopes_json=["https://www.googleapis.com/auth/gmail.readonly"],
        access_token_encrypted=_VAULT.encrypt(f"access-{suffix}"),
        refresh_token_encrypted=_VAULT.encrypt(f"refresh-{suffix}"),
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        status=ConnectionStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()

    return _OrgFixture(org, user, connection)


def _assert_cross_org_404(call) -> None:
    """A cross-tenant access must raise ``404`` and reveal nothing (Req 24.8)."""

    with pytest.raises(HTTPException) as exc:
        call()
    assert exc.value.status_code == 404


def _check_isolation(
    db: Session, owner: _OrgFixture, other: _OrgFixture
) -> None:
    """Assert ``owner``'s connection is unreachable from ``other`` (both dirs)."""

    service = IntegrationService(
        db, FakeGoogleOAuthClient(), token_vault=_VAULT, settings=_TEST_SETTINGS
    )

    owner_org = owner.org.id
    other_org = other.org.id
    conn_id = owner.connection.id

    # -- Read: same-org succeeds, cross-org 404 -----------------------------
    assert service.get_connection(owner_org, conn_id).id == conn_id
    _assert_cross_org_404(lambda: service.get_connection(other_org, conn_id))

    # get_valid_access_token is org-scoped too (read of the secret material).
    assert service.get_valid_access_token(owner_org, owner.user.id, conn_id)
    _assert_cross_org_404(
        lambda: service.get_valid_access_token(
            other_org, other.user.id, conn_id
        )
    )

    # -- Write: cross-org disconnect/revoke refused (404) -------------------
    _assert_cross_org_404(
        lambda: service.disconnect(other_org, other.user.id, conn_id)
    )
    _assert_cross_org_404(
        lambda: service.revoke_google_access(
            other_org, other.user.id, conn_id
        )
    )

    # Same-org write succeeds.
    disconnected = service.disconnect(owner_org, owner.user.id, conn_id)
    assert disconnected.status == ConnectionStatus.REVOKED


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    name_a=st.text(min_size=0, max_size=40),
    name_b=st.text(min_size=0, max_size=40),
)
def test_integration_org_isolation(
    db_session: Session, name_a: str, name_b: str
) -> None:
    """Connections are readable/writable only within their own org; else 404.

    **Validates: Requirements 24.8, 25.5 / Property 13**
    """

    org_a = _seed_org(db_session, name_a or "OrgA")
    org_b = _seed_org(db_session, name_b or "OrgB")

    _check_isolation(db_session, owner=org_a, other=org_b)
    _check_isolation(db_session, owner=org_b, other=org_a)
