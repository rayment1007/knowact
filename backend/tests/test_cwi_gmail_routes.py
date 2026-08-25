"""Route tests for the CWI Gmail sync & ingestion endpoints (Requirements 26, 27).

Exercises the real FastAPI app against the ephemeral SQLite database with FAKE
Google OAuth and FAKE Gmail transports injected (no network call ever occurs)
and a test Fernet key configured so the TokenVault can decrypt the connection's
token during sync. Covers initial-sync options, manual sync-now, content-hash
dedup, message listing, task-suggestion review, sender-signal marking, and
cross-org 404 isolation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.core import models as core_models
from app.modules.cwi.dependencies import gmail_client, google_oauth_client
from app.modules.cwi.models import (
    IntegrationConnection,
    IntegrationService as IntegrationServiceEnum,
)
from app.modules.cwi.services.gmail_client import FakeGmailClient, GmailMessage
from app.modules.cwi.services.google_oauth import FakeGoogleOAuthClient

_TEST_KEY = Fernet.generate_key().decode()
_FORBIDDEN_FIELDS = ("access_token_encrypted", "refresh_token_encrypted")


def _msg(
    mid: str,
    *,
    subject: str,
    body: str,
    sender: str = "alice@partner.com",
    labels: list[str] | None = None,
    has_attachments: bool = False,
) -> GmailMessage:
    return GmailMessage(
        gmail_message_id=mid,
        gmail_thread_id=f"thread-{mid}",
        sender=sender,
        subject=subject,
        body_text=body,
        received_at=datetime.now(timezone.utc),
        recipients=["me@example.com"],
        labels=labels or ["INBOX"],
        has_attachments=has_attachments,
    )


@pytest.fixture()
def fake_gmail() -> FakeGmailClient:
    return FakeGmailClient(
        [
            # A task-like message (matches the TASK lexicon: "please send").
            _msg(
                "m-task",
                subject="Please send the Q3 report",
                body="Please send the Q3 report and follow up with the client.",
            ),
            # A plain informational message.
            _msg(
                "m-info",
                subject="Project milestone reached",
                body="The project milestone was reached this week.",
            ),
        ]
    )


@pytest.fixture()
def cwi_client(
    client: TestClient,
    fake_gmail: FakeGmailClient,
) -> TestClient:
    test_settings = Settings(token_encryption_key=_TEST_KEY, ai_provider="mock")
    client.app.dependency_overrides[get_settings] = lambda: test_settings
    client.app.dependency_overrides[google_oauth_client] = (
        lambda: FakeGoogleOAuthClient()
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


def test_disconnected_connection_cannot_sync_or_call_gmail(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    fake_gmail: FakeGmailClient,
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    assert cwi_client.post(
        f"/api/integrations/{connection_id}/disconnect"
    ).status_code == 200
    calls_before = fake_gmail.list_calls

    response = cwi_client.post(f"/api/gmail/{connection_id}/sync-now")

    assert response.status_code == 403
    assert fake_gmail.list_calls == calls_before


def test_gmail_sync_rejects_connection_missing_read_scope_before_external_call(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    fake_gmail: FakeGmailClient,
    db_session: Any,
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    connection = db_session.query(IntegrationConnection).one()
    connection.granted_scopes_json = []
    db_session.flush()
    calls_before = fake_gmail.list_calls

    response = cwi_client.post(f"/api/gmail/{connection_id}/sync-now")

    assert response.status_code == 403
    assert fake_gmail.list_calls == calls_before


def test_gmail_sync_rejects_calendar_connection_before_external_call(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    fake_gmail: FakeGmailClient,
    db_session: Any,
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    connection = db_session.query(IntegrationConnection).one()
    connection.service = IntegrationServiceEnum.GOOGLE_CALENDAR
    db_session.flush()
    calls_before = fake_gmail.list_calls

    response = cwi_client.post(f"/api/gmail/{connection_id}/sync-now")

    assert response.status_code == 403
    assert fake_gmail.list_calls == calls_before


# ---------------------------------------------------------------------------
# Initial sync + dedup
# ---------------------------------------------------------------------------


def test_initial_sync_ingests_and_dedups(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)

    payload = {
        "date_range_days": 30,
        "labels": None,
        "include_sent": False,
        "attachment_handling": "METADATA_ONLY",
        "storage_policy": "EXTRACTED_ONLY",
    }
    first = cwi_client.post(
        f"/api/gmail/{connection_id}/initial-sync", json=payload
    )
    assert first.status_code == 200
    body = first.json()
    assert body["records_created"] == 2
    assert body["source_items_created"] == 2
    assert body["suggestions_created"] == 1  # only the task-like message

    # Re-running produces no new records (content-hash dedup / Property 15).
    second = cwi_client.post(
        f"/api/gmail/{connection_id}/initial-sync", json=payload
    )
    assert second.status_code == 200
    assert second.json()["records_created"] == 0
    assert second.json()["skipped_duplicates"] == 2

    messages = cwi_client.get("/api/gmail/messages").json()
    assert len(messages) == 2
    # No token ever surfaces.
    for field in _FORBIDDEN_FIELDS:
        assert field not in cwi_client.get("/api/gmail/messages").text


def test_initial_sync_rejects_invalid_options_with_422(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)

    bad = {
        "date_range_days": 45,  # not in {7, 30, 90}
        "include_sent": False,
        "attachment_handling": "METADATA_ONLY",
        "storage_policy": "EXTRACTED_ONLY",
    }
    resp = cwi_client.post(f"/api/gmail/{connection_id}/initial-sync", json=bad)
    assert resp.status_code == 422
    # No partial sync: nothing was ingested.
    assert cwi_client.get("/api/gmail/messages").json() == []


def test_sync_now_is_idempotent(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)

    r1 = cwi_client.post(f"/api/gmail/{connection_id}/sync-now")
    assert r1.status_code == 200
    assert r1.json()["records_created"] == 2

    r2 = cwi_client.post(f"/api/gmail/{connection_id}/sync-now")
    assert r2.status_code == 200
    assert r2.json()["records_created"] == 0
    assert len(cwi_client.get("/api/gmail/messages").json()) == 2


# ---------------------------------------------------------------------------
# Task-suggestion review lifecycle
# ---------------------------------------------------------------------------


def test_task_suggestion_confirm_creates_action(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    cwi_client.post(f"/api/gmail/{connection_id}/sync-now")

    suggestions = cwi_client.get("/api/gmail/suggestions").json()
    assert len(suggestions) == 1
    assert suggestions[0]["status"] == "SUGGESTED"
    assert suggestions[0]["ai_provider"] == "mock"
    assert suggestions[0]["evidence_text"]

    sid = suggestions[0]["id"]
    confirmed = cwi_client.post(f"/api/gmail/suggestions/{sid}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"

    # An AI-generated action was created and one audit row recorded.
    actions = db_session.query(core_models.ActionItem).all()
    assert any(a.ai_generated for a in actions)
    audits = (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == "CONFIRM_EMAIL_TASK")
        .all()
    )
    assert len(audits) == 1


def test_task_suggestion_edit_and_reject(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)
    cwi_client.post(f"/api/gmail/{connection_id}/sync-now")

    sid = cwi_client.get("/api/gmail/suggestions").json()[0]["id"]

    edited = cwi_client.patch(
        f"/api/gmail/suggestions/{sid}",
        json={"title": "Edited task title"},
    )
    assert edited.status_code == 200
    assert edited.json()["title"] == "Edited task title"

    rejected = cwi_client.post(f"/api/gmail/suggestions/{sid}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"


def test_sender_signal_marking(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    _connect_gmail(cwi_client)

    resp = cwi_client.post(
        "/api/gmail/sender-signals",
        json={"pattern": "spam.com", "signal_type": "IRRELEVANT"},
    )
    assert resp.status_code == 200
    assert resp.json()["pattern"] == "spam.com"
    assert resp.json()["signal_type"] == "IRRELEVANT"


# ---------------------------------------------------------------------------
# Auth + cross-org isolation
# ---------------------------------------------------------------------------


def test_gmail_requires_authentication(cwi_client: TestClient) -> None:
    assert cwi_client.get("/api/gmail/messages").status_code == 401


def test_cross_org_sync_returns_404(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_gmail(cwi_client)

    # A second org/user.
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

    # The other org cannot sync the first org's connection.
    resp = cwi_client.post(
        f"/api/gmail/{connection_id}/sync-now",
    )
    assert resp.status_code == 404
