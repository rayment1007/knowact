"""Focused route tests for Phase 1 record deep links.

The frontend detail routes must resolve one record directly instead of loading
an unbounded list and searching it in the browser. These tests cover the four
single-record backend endpoints introduced for that contract, including the
existing session and organization-isolation rules.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import models as core_models
from app.modules.cwi import models as cwi_models
from app.security import hash_password


def _login(client: TestClient, email: str, password: str) -> None:
    response = client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200


def _connection(
    db: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    service: cwi_models.IntegrationService,
) -> cwi_models.IntegrationConnection:
    connection = cwi_models.IntegrationConnection(
        organization_id=organization_id,
        user_id=user_id,
        provider=cwi_models.IntegrationProvider.GOOGLE,
        service=service,
        external_account_id=f"account-{service.value.lower()}",
        account_email="owner@example.com",
        granted_scopes_json=[],
        access_token_encrypted=b"test-ciphertext",
        status=cwi_models.ConnectionStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    return connection


def _seed_records(
    db: Session, organization_id: UUID, user_id: UUID
) -> dict[str, Any]:
    action = core_models.ActionItem(
        organization_id=organization_id,
        title="Follow up with the client",
        description="Send the requested summary.",
        owner_id=user_id,
        status=core_models.ActionStatus.OPEN,
        evidence_text="The client requested a written summary.",
        ai_generated=False,
    )
    decision = core_models.DecisionRecord(
        organization_id=organization_id,
        title="Use the phased rollout",
        decision="Start with the individual workflow.",
        rationale="It matches the approved proposal scope.",
        evidence_text="Proposal scope review.",
        decided_by=user_id,
        decided_at=datetime.now(timezone.utc),
    )
    db.add_all([action, decision])
    db.flush()

    gmail_connection = _connection(
        db,
        organization_id=organization_id,
        user_id=user_id,
        service=cwi_models.IntegrationService.GMAIL,
    )
    calendar_connection = _connection(
        db,
        organization_id=organization_id,
        user_id=user_id,
        service=cwi_models.IntegrationService.GOOGLE_CALENDAR,
    )

    email = cwi_models.EmailMessageRecord(
        organization_id=organization_id,
        integration_connection_id=gmail_connection.id,
        gmail_message_id="gmail-message-1",
        gmail_thread_id="gmail-thread-1",
        sender="client@example.com",
        recipients_json=["owner@example.com"],
        subject="Requested summary",
        received_at=datetime.now(timezone.utc),
        labels_json=["INBOX"],
        content_hash="a" * 64,
        has_attachments=False,
        stored_raw=False,
    )
    calendar_link = cwi_models.CalendarEventLink(
        organization_id=organization_id,
        user_id=user_id,
        action_item_id=action.id,
        integration_connection_id=calendar_connection.id,
        google_calendar_id="primary",
        google_event_id="event-1",
        idempotency_key=f"detail-{uuid4()}",
        sync_status=cwi_models.CalendarSyncStatus.SYNCED,
    )
    db.add_all([email, calendar_link])
    db.flush()

    return {
        "action": action,
        "decision": decision,
        "email": email,
        "calendar": calendar_link,
    }


def test_get_action_detail(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    records = _seed_records(
        db_session, seeded_user["organization"].id, seeded_user["user"].id
    )
    _login(client, seeded_user["email"], seeded_user["password"])

    response = client.get(f"/api/actions/{records['action'].id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(records["action"].id)
    assert response.json()["title"] == "Follow up with the client"


def test_get_decision_detail(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    records = _seed_records(
        db_session, seeded_user["organization"].id, seeded_user["user"].id
    )
    _login(client, seeded_user["email"], seeded_user["password"])

    response = client.get(f"/api/decisions/{records['decision'].id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(records["decision"].id)
    assert response.json()["decision"] == "Start with the individual workflow."


def test_get_email_detail(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    records = _seed_records(
        db_session, seeded_user["organization"].id, seeded_user["user"].id
    )
    _login(client, seeded_user["email"], seeded_user["password"])

    response = client.get(f"/api/gmail/messages/{records['email'].id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(records["email"].id)
    assert response.json()["subject"] == "Requested summary"
    assert response.json()["recipients"] == ["owner@example.com"]


def test_get_calendar_link_detail(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    records = _seed_records(
        db_session, seeded_user["organization"].id, seeded_user["user"].id
    )
    _login(client, seeded_user["email"], seeded_user["password"])

    response = client.get(f"/api/calendar/links/{records['calendar'].id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(records["calendar"].id)
    assert response.json()["google_event_id"] == "event-1"


def test_detail_routes_do_not_fall_back_to_another_org_record(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    """An unknown ID stays 404 even when the organization has matching rows."""

    _seed_records(
        db_session, seeded_user["organization"].id, seeded_user["user"].id
    )
    _login(client, seeded_user["email"], seeded_user["password"])

    paths = [
        "/api/actions/{id}",
        "/api/decisions/{id}",
        "/api/gmail/messages/{id}",
        "/api/calendar/links/{id}",
    ]
    for path in paths:
        assert client.get(path.format(id=uuid4())).status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/actions/{id}",
        "/api/decisions/{id}",
        "/api/gmail/messages/{id}",
        "/api/calendar/links/{id}",
    ],
)
def test_detail_routes_require_authentication(
    client: TestClient, path: str
) -> None:
    response = client.get(path.format(id=uuid4()))

    assert response.status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/actions/{id}",
        "/api/decisions/{id}",
        "/api/gmail/messages/{id}",
        "/api/calendar/links/{id}",
    ],
)
def test_detail_routes_return_404_for_missing_records(
    client: TestClient, seeded_user: dict[str, Any], path: str
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])

    response = client.get(path.format(id=uuid4()))

    assert response.status_code == 404


def test_detail_routes_hide_cross_org_records(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    records = _seed_records(
        db_session, seeded_user["organization"].id, seeded_user["user"].id
    )

    other_org = core_models.Organization(name="Other Organization")
    db_session.add(other_org)
    db_session.flush()
    other_user = core_models.User(
        organization_id=other_org.id,
        email="other-detail-user@example.com",
        full_name="Other Detail User",
        password_hash=hash_password("other-detail-password"),
        role="ADMIN",
    )
    db_session.add(other_user)
    db_session.flush()
    _login(client, other_user.email, "other-detail-password")

    paths = [
        f"/api/actions/{records['action'].id}",
        f"/api/decisions/{records['decision'].id}",
        f"/api/gmail/messages/{records['email'].id}",
        f"/api/calendar/links/{records['calendar'].id}",
    ]
    for path in paths:
        assert client.get(path).status_code == 404
