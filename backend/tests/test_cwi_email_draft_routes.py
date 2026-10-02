"""Route tests for the CWI Gmail AI Draft endpoints (M6.6, Requirement 32).

Exercises the real FastAPI app against the ephemeral SQLite database with a FAKE
Gmail transport and FAKE Google OAuth transport injected (no network call, no
real email ever), ``AI_PROVIDER=mock``, and a test Fernet key so the TokenVault
can decrypt the connection's token. Covers the full lifecycle (request → edit →
approve → create Gmail draft → send), the separate explicit send confirmation,
idempotent send, backend-resolved recipients, grounded referenced facts, the
compose-scope gate, token safety, auth, and cross-org 404 isolation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.core import models as core_models
from app.core.models import KnowledgeItem, SuggestionStatus
from app.modules.cwi.dependencies import gmail_client, google_oauth_client
from app.modules.cwi.models import IntegrationConnection
from app.modules.cwi.models import ConnectionStatus, IntegrationConnection
from app.modules.cwi.services.gmail_client import FakeGmailClient, GmailClientError
from app.modules.cwi.services.google_oauth import (
    GMAIL_COMPOSE_SCOPE,
    GMAIL_READONLY_SCOPE,
    FakeGoogleOAuthClient,
    GoogleTokenGrant,
)

_TEST_KEY = Fernet.generate_key().decode()
_FORBIDDEN_FIELDS = ("access_token_encrypted", "refresh_token_encrypted")


def _compose_grant(scopes: list[str]) -> GoogleTokenGrant:
    return GoogleTokenGrant(
        access_token="access-compose",
        refresh_token="refresh-compose",
        expires_at=datetime.now(timezone.utc).replace(year=2999),
        granted_scopes=scopes,
        external_account_id="google-sub-compose",
        account_email="compose@example.com",
    )


@pytest.fixture()
def fake_gmail() -> FakeGmailClient:
    return FakeGmailClient()


@pytest.fixture()
def cwi_client(client: TestClient, fake_gmail: FakeGmailClient) -> TestClient:
    test_settings = Settings(token_encryption_key=_TEST_KEY, ai_provider="mock")
    client.app.dependency_overrides[get_settings] = lambda: test_settings
    # A Gmail connection incrementally authorized for both readonly + compose.
    client.app.dependency_overrides[google_oauth_client] = lambda: (
        FakeGoogleOAuthClient(
            grant=_compose_grant([GMAIL_READONLY_SCOPE, GMAIL_COMPOSE_SCOPE])
        )
    )
    client.app.dependency_overrides[gmail_client] = lambda: fake_gmail
    return client


def _login(client: TestClient, seeded_user: dict[str, Any]) -> None:
    resp = client.post(
        "/api/auth/login",
        json={"email": seeded_user["email"], "password": seeded_user["password"]},
    )
    assert resp.status_code == 200


def _connect_gmail(client: TestClient) -> str:
    begin = client.post("/api/integrations/GMAIL/connect")
    assert begin.status_code == 200
    state = begin.json()["state"]
    callback = client.get(
        "/api/integrations/callback",
        params={"code": "abc", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    listed = client.get("/api/integrations").json()
    return listed[0]["id"]


def _request_draft(
    client: TestClient, connection_id: str, **extra: Any
) -> dict[str, Any]:
    payload = {
        "connection_id": connection_id,
        "purpose": "follow up on the proposal",
        "tone": "professional",
        "to_recipients": ["client@example.com"],
        **extra,
    }
    resp = client.post("/api/email-drafts", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_disconnected_gmail_connection_cannot_start_email_draft(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    fake_gmail: FakeGmailClient,
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    assert cwi_client.post(
        f"/api/integrations/{connection_id}/disconnect"
    ).status_code == 200
    calls_before = fake_gmail.create_draft_calls

    response = cwi_client.post(
        "/api/email-drafts",
        json={
            "connection_id": connection_id,
            "purpose": "follow up",
            "tone": "professional",
            "to_recipients": ["client@example.com"],
        },
    )

    assert response.status_code == 403
    assert fake_gmail.create_draft_calls == calls_before


# ---------------------------------------------------------------------------
# Request draft
# ---------------------------------------------------------------------------


def test_request_draft_persists_ai_suggested(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)

    body = _request_draft(cwi_client, connection_id)
    assert body["status"] == "AI_SUGGESTED"
    assert body["subject"]
    assert body["body_text"]
    # Recipients are backend-resolved/validated (never invented by the LLM).
    assert body["to_recipients"] == ["client@example.com"]
    assert body["gmail_draft_id"] is None
    assert body["gmail_sent_message_id"] is None
    for field in _FORBIDDEN_FIELDS:
        assert field not in cwi_client.get(
            f"/api/email-drafts/{body['id']}"
        ).text


def test_request_draft_grounds_referenced_facts(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)

    knowledge = KnowledgeItem(
        organization_id=seeded_user["organization"].id,
        summary="Client prefers quarterly reviews.",
        key_points=["quarterly"],
        evidence_text="Client said quarterly reviews are best.",
        knowledge_type="FACT",
        status=SuggestionStatus.CONFIRMED,
    )
    db_session.add(knowledge)
    db_session.flush()

    body = _request_draft(cwi_client, connection_id)
    facts = body["referenced_facts"]
    assert facts, "expected at least one grounded referenced fact"
    ids = {f["source_id"] for f in facts}
    # Every referenced fact grounds in a supplied context id.
    assert str(knowledge.id) in ids


def test_recipient_addresses_are_validated(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)

    body = _request_draft(
        cwi_client,
        connection_id,
        to_recipients=["not-an-email", "ok@example.com", "ok@example.com"],
    )
    # Malformed / duplicate recipients are dropped by the backend resolver.
    assert body["to_recipients"] == ["ok@example.com"]


def test_edit_sets_valid_recipient(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    draft_id = _request_draft(cwi_client, connection_id)["id"]

    # A user may set/override recipients; the backend validates + stores them.
    edited = cwi_client.patch(
        f"/api/email-drafts/{draft_id}",
        json={"to_recipients": ["new@example.com", "New@example.com"]},
    )
    assert edited.status_code == 200, edited.text
    # De-duplicated case-insensitively, first-seen order preserved.
    assert edited.json()["to_recipients"] == ["new@example.com"]


def test_edit_rejects_invalid_recipient_without_partial_write(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    draft = _request_draft(cwi_client, connection_id)
    draft_id = draft["id"]
    original_recipients = draft["to_recipients"]
    original_subject = draft["subject"]

    # A malformed address rejects the whole edit with 422 (no partial write):
    # even though a valid subject is supplied, nothing is persisted.
    rejected = cwi_client.patch(
        f"/api/email-drafts/{draft_id}",
        json={"subject": "Should not persist", "to_recipients": ["not-an-email"]},
    )
    assert rejected.status_code == 422, rejected.text

    unchanged = cwi_client.get(f"/api/email-drafts/{draft_id}").json()
    assert unchanged["to_recipients"] == original_recipients
    assert unchanged["subject"] == original_subject


# ---------------------------------------------------------------------------
# Lifecycle: edit / approve / reject / create Gmail draft
# ---------------------------------------------------------------------------


def test_edit_then_approve(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    draft_id = _request_draft(cwi_client, connection_id)["id"]

    edited = cwi_client.patch(
        f"/api/email-drafts/{draft_id}",
        json={"subject": "Re: Proposal", "body_text": "Hi, here's the update."},
    )
    assert edited.status_code == 200
    assert edited.json()["subject"] == "Re: Proposal"

    approved = cwi_client.post(f"/api/email-drafts/{draft_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "USER_APPROVED"


def test_reject_draft(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    draft_id = _request_draft(cwi_client, connection_id)["id"]

    rejected = cwi_client.post(f"/api/email-drafts/{draft_id}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"


def test_create_gmail_draft_records_audit(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
    fake_gmail: FakeGmailClient,
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    draft_id = _request_draft(cwi_client, connection_id)["id"]
    cwi_client.post(f"/api/email-drafts/{draft_id}/approve")

    created = cwi_client.post(f"/api/email-drafts/{draft_id}/create-gmail-draft")
    assert created.status_code == 200
    assert created.json()["status"] == "GMAIL_DRAFT_CREATED"
    assert created.json()["gmail_draft_id"]
    assert fake_gmail.create_draft_calls == 1

    audits = (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == "CREATE_GMAIL_DRAFT")
        .all()
    )
    assert len(audits) == 1


def test_create_gmail_draft_before_approve_conflicts(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    draft_id = _request_draft(cwi_client, connection_id)["id"]

    resp = cwi_client.post(f"/api/email-drafts/{draft_id}/create-gmail-draft")
    assert resp.status_code == 409


def test_ambiguous_gmail_draft_creation_is_terminal_and_persists(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    fake_gmail: FakeGmailClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    draft_id = _request_draft(cwi_client, connection_id)["id"]
    cwi_client.post(f"/api/email-drafts/{draft_id}/approve")
    create_attempts = 0

    def ambiguous_create_failure(**_kwargs: Any) -> str:
        nonlocal create_attempts
        create_attempts += 1
        raise GmailClientError("Simulated ambiguous Gmail transport failure.")

    monkeypatch.setattr(fake_gmail, "create_draft", ambiguous_create_failure)

    failed = cwi_client.post(f"/api/email-drafts/{draft_id}/create-gmail-draft")
    assert failed.status_code == 502
    assert "Check Gmail Drafts" in failed.json()["detail"]
    assert create_attempts == 1

    detail = cwi_client.get(f"/api/email-drafts/{draft_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "FAILED"
    assert any("Check Gmail Drafts" in item for item in detail.json()["warnings"])

    # Gmail does not provide an idempotency guarantee for draft creation. Once
    # the result is ambiguous, a second blind create is refused locally.
    retry = cwi_client.post(f"/api/email-drafts/{draft_id}/create-gmail-draft")
    assert retry.status_code == 409
    assert create_attempts == 1


# ---------------------------------------------------------------------------
# Send: separate explicit confirmation + idempotency
# ---------------------------------------------------------------------------


def test_send_requires_explicit_confirmation(
    cwi_client: TestClient, seeded_user: dict[str, Any], fake_gmail: FakeGmailClient
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    draft_id = _request_draft(cwi_client, connection_id)["id"]
    cwi_client.post(f"/api/email-drafts/{draft_id}/approve")

    # Without an explicit confirm the send is rejected and nothing is sent.
    unconfirmed = cwi_client.post(
        f"/api/email-drafts/{draft_id}/send", json={"confirm": False}
    )
    assert unconfirmed.status_code == 400
    assert fake_gmail.send_calls == 0


def test_send_before_approve_conflicts(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    draft_id = _request_draft(cwi_client, connection_id)["id"]

    resp = cwi_client.post(
        f"/api/email-drafts/{draft_id}/send", json={"confirm": True}
    )
    assert resp.status_code == 409


def test_send_is_idempotent(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
    fake_gmail: FakeGmailClient,
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    draft_id = _request_draft(cwi_client, connection_id)["id"]
    cwi_client.post(f"/api/email-drafts/{draft_id}/approve")

    first = cwi_client.post(
        f"/api/email-drafts/{draft_id}/send", json={"confirm": True}
    )
    second = cwi_client.post(
        f"/api/email-drafts/{draft_id}/send", json={"confirm": True}
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == "SENT"
    assert second.json()["status"] == "SENT"
    # Exactly one send; the retried send returns the same message id.
    assert first.json()["gmail_sent_message_id"]
    assert (
        first.json()["gmail_sent_message_id"]
        == second.json()["gmail_sent_message_id"]
    )
    assert fake_gmail.send_calls == 1

    audits = (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == "SEND_EMAIL")
        .all()
    )
    assert len(audits) == 1


def test_confirmed_send_is_not_undone_by_local_snapshot_error(cwi_client, seeded_user, fake_gmail, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    from app.modules.cwi.services.gmail_sync_service import GmailSyncService
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    draft_id = _request_draft(cwi_client, connection_id)["id"]
    cwi_client.post(f"/api/email-drafts/{draft_id}/approve")
    def failed_snapshot(*args, **kwargs):
        raise SQLAlchemyError("snapshot failed")
    monkeypatch.setattr(GmailSyncService, "_ingest_message_race_safe", failed_snapshot)
    for _ in range(2):
        response = cwi_client.post(f"/api/email-drafts/{draft_id}/send", json={"confirm": True})
        assert response.status_code == 200
        assert response.json()["status"] == "SENT"
    assert fake_gmail.send_calls == 1


def test_send_failure_persists_failed_status(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    fake_gmail: FakeGmailClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    draft_id = _request_draft(cwi_client, connection_id)["id"]
    cwi_client.post(f"/api/email-drafts/{draft_id}/approve")
    send_attempts = 0

    def ambiguous_send_failure(**_kwargs: Any) -> str:
        nonlocal send_attempts
        send_attempts += 1
        raise GmailClientError("Simulated ambiguous Gmail transport failure.")

    monkeypatch.setattr(fake_gmail, "send_draft", ambiguous_send_failure)

    failed = cwi_client.post(
        f"/api/email-drafts/{draft_id}/send", json={"confirm": True}
    )
    assert failed.status_code == 502
    assert "Check Gmail Sent" in failed.json()["detail"]
    assert send_attempts == 1

    detail = cwi_client.get(f"/api/email-drafts/{draft_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "FAILED"
    assert any("Check Gmail Sent" in item for item in detail.json()["warnings"])

    # An ambiguous transport outcome is terminal locally: a blind retry could
    # duplicate a message that Gmail actually accepted before the response was
    # lost, so the backend refuses it without another external call.
    retry = cwi_client.post(
        f"/api/email-drafts/{draft_id}/send", json={"confirm": True}
    )
    assert retry.status_code == 409
    assert send_attempts == 1


def test_oauth_failure_before_send_restores_sendable_status(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
    fake_gmail: FakeGmailClient,
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    draft_id = _request_draft(cwi_client, connection_id)["id"]
    cwi_client.post(f"/api/email-drafts/{draft_id}/approve")

    # Force token acquisition to fail before GmailClient.send_draft is reached.
    connection = db_session.get(IntegrationConnection, UUID(connection_id))
    assert connection is not None
    connection.token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.add(connection)
    db_session.flush()
    failing_oauth = FakeGoogleOAuthClient(fail_refresh=True)
    cwi_client.app.dependency_overrides[google_oauth_client] = lambda: failing_oauth

    failed = cwi_client.post(
        f"/api/email-drafts/{draft_id}/send", json={"confirm": True}
    )
    assert failed.status_code == 502
    assert "reconnect Gmail and retry" in failed.json()["detail"]
    assert failing_oauth.refresh_calls == 1
    assert fake_gmail.send_calls == 0

    # The normal response commits both safe states across the request boundary:
    # the draft is still sendable and the integration records expiry.
    detail = cwi_client.get(f"/api/email-drafts/{draft_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "USER_APPROVED"
    db_session.refresh(connection)
    assert connection.status == ConnectionStatus.EXPIRED


def test_send_from_gmail_draft_created(
    cwi_client: TestClient, seeded_user: dict[str, Any], fake_gmail: FakeGmailClient
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    draft_id = _request_draft(cwi_client, connection_id)["id"]
    cwi_client.post(f"/api/email-drafts/{draft_id}/approve")
    cwi_client.post(f"/api/email-drafts/{draft_id}/create-gmail-draft")

    sent = cwi_client.post(
        f"/api/email-drafts/{draft_id}/send", json={"confirm": True}
    )
    assert sent.status_code == 200
    assert sent.json()["status"] == "SENT"
    assert fake_gmail.send_calls == 1


# ---------------------------------------------------------------------------
# Compose-scope gate (Requirement 32.3)
# ---------------------------------------------------------------------------


def test_compose_scope_required_for_gmail_draft(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    fake_gmail: FakeGmailClient,
    db_session: Any,
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    # Simulate a legacy/externally-reduced grant. New callbacks reject partial
    # grants, but every consuming service must still fail closed for existing
    # rows whose permissions have changed.
    connection = db_session.query(IntegrationConnection).one()
    connection.granted_scopes_json = [GMAIL_READONLY_SCOPE]
    db_session.flush()
    draft_id = _request_draft(cwi_client, connection_id)["id"]
    cwi_client.post(f"/api/email-drafts/{draft_id}/approve")

    # Creating a Gmail draft / sending is gated behind gmail.compose.
    created = cwi_client.post(f"/api/email-drafts/{draft_id}/create-gmail-draft")
    assert created.status_code == 403
    sent = cwi_client.post(
        f"/api/email-drafts/{draft_id}/send", json={"confirm": True}
    )
    assert sent.status_code == 403
    assert fake_gmail.create_draft_calls == 0
    assert fake_gmail.send_calls == 0


# ---------------------------------------------------------------------------
# Auth + cross-org isolation
# ---------------------------------------------------------------------------


def test_email_drafts_require_authentication(cwi_client: TestClient) -> None:
    assert cwi_client.get("/api/email-drafts").status_code == 401


def test_cross_org_draft_returns_404(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    draft_id = _request_draft(cwi_client, connection_id)["id"]

    from app.security import hash_password

    other_org = core_models.Organization(name="Other Org")
    db_session.add(other_org)
    db_session.flush()
    other_user = core_models.User(
        organization_id=other_org.id,
        email="other@example.com",
        full_name="Other",
        password_hash=hash_password("pw2"),
        role="ADMIN",
    )
    db_session.add(other_user)
    db_session.flush()

    cwi_client.cookies.clear()
    assert cwi_client.post(
        "/api/auth/login",
        json={"email": "other@example.com", "password": "pw2"},
    ).status_code == 200

    # The other org cannot see or act on the first org's draft.
    assert cwi_client.get(f"/api/email-drafts/{draft_id}").status_code == 404
    assert cwi_client.post(
        f"/api/email-drafts/{draft_id}/approve"
    ).status_code == 404
    assert cwi_client.post(
        f"/api/email-drafts/{draft_id}/send", json={"confirm": True}
    ).status_code == 404
