"""Same-organization user isolation for personal CWI resources.

Core Source, Knowledge, Action, and Decision records intentionally retain their
organization-shared semantics.  Gmail-derived records, email drafts, calendar
links, privacy controls, and Copilot email evidence are personal because they
are anchored to a user's own external connection.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.core import models as core_models
from app.core.models import ActionItem, SuggestionStatus
from app.modules.cwi.dependencies import (
    calendar_client,
    embedding_provider,
    gmail_client,
    google_oauth_client,
    storage_backend,
)
from app.modules.cwi.models import (
    CalendarEventLink,
    CalendarSyncStatus,
    ConnectionStatus,
    CopilotAnswerLog,
    EmailDraft,
    EmailDraftStatus,
    EmailMessageRecord,
    EmailSenderSignal,
    EmailTaskSuggestion,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationService as IntegrationServiceEnum,
    SenderSignalType,
)
from app.modules.cwi.services.calendar_client import CalendarClientError, FakeCalendarClient
from app.modules.cwi.services.embedding import FakeEmbeddingProvider
from app.modules.cwi.services.gmail_client import FakeGmailClient
from app.modules.cwi.services.gmail_sync_service import GmailSyncService
from app.modules.cwi.services.google_oauth import (
    GMAIL_COMPOSE_SCOPE,
    GMAIL_READONLY_SCOPE,
    FakeGoogleOAuthClient,
)
from app.modules.cwi.services.storage import LocalFilesystemStorage
from app.modules.cwi.services.token_vault import TokenVault

_DIMENSION = 32
_TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture()
def fake_gmail() -> FakeGmailClient:
    return FakeGmailClient()


@pytest.fixture()
def fake_calendar() -> FakeCalendarClient:
    return FakeCalendarClient()


@pytest.fixture()
def isolated_client(
    client: TestClient,
    fake_gmail: FakeGmailClient,
    fake_calendar: FakeCalendarClient,
    tmp_path: Any,
) -> TestClient:
    settings = Settings(
        token_encryption_key=_TEST_KEY,
        ai_provider="mock",
        embedding_provider="mock",
        embedding_dimension=_DIMENSION,
    )
    storage = LocalFilesystemStorage(tmp_path)
    client.app.dependency_overrides[get_settings] = lambda: settings
    client.app.dependency_overrides[google_oauth_client] = (
        lambda: FakeGoogleOAuthClient()
    )
    client.app.dependency_overrides[gmail_client] = lambda: fake_gmail
    client.app.dependency_overrides[calendar_client] = lambda: fake_calendar
    client.app.dependency_overrides[embedding_provider] = (
        lambda: FakeEmbeddingProvider(dimension=_DIMENSION)
    )
    client.app.dependency_overrides[storage_backend] = lambda: storage
    return client


def _make_second_user(db: Any, seeded_user: dict[str, Any]) -> core_models.User:
    """Create a second user in the seeded user's organization."""

    user = core_models.User(
        organization_id=seeded_user["organization"].id,
        email=f"same-org-{uuid4().hex}@example.com",
        full_name="Same Org User",
        password_hash=seeded_user["user"].password_hash,
        role="MEMBER",
    )
    db.add(user)
    db.flush()
    return user


def _login(
    client: TestClient,
    user: core_models.User,
    password: str,
) -> None:
    client.cookies.clear()
    response = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": password},
    )
    assert response.status_code == 200, response.text


def _seed_connection(
    db: Any,
    org_id: Any,
    user_id: Any,
    *,
    service: IntegrationServiceEnum = IntegrationServiceEnum.GMAIL,
) -> IntegrationConnection:
    suffix = uuid4().hex
    scopes = (
        [GMAIL_READONLY_SCOPE, GMAIL_COMPOSE_SCOPE]
        if service == IntegrationServiceEnum.GMAIL
        else ["https://www.googleapis.com/auth/calendar.events"]
    )
    connection = IntegrationConnection(
        organization_id=org_id,
        user_id=user_id,
        provider=IntegrationProvider.GOOGLE,
        service=service,
        external_account_id=f"sub-{suffix}",
        account_email=f"mailbox-{suffix}@example.com",
        granted_scopes_json=scopes,
        access_token_encrypted=b"test-ciphertext",
        refresh_token_encrypted=b"test-ciphertext",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        status=ConnectionStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    return connection


def _seed_email_record(
    db: Any,
    org_id: Any,
    connection_id: Any,
    *,
    subject: str = "Private mailbox subject",
    stored_raw: bool = True,
) -> EmailMessageRecord:
    suffix = uuid4().hex
    record = EmailMessageRecord(
        organization_id=org_id,
        integration_connection_id=connection_id,
        source_item_id=None,
        gmail_message_id=f"message-{suffix}",
        gmail_thread_id=f"thread-{suffix}",
        sender="private-sender@example.com",
        recipients_json=["owner@example.com"],
        subject=subject,
        received_at=datetime.now(timezone.utc),
        labels_json=["INBOX"],
        content_hash=suffix,
        has_attachments=False,
        stored_raw=stored_raw,
    )
    db.add(record)
    db.flush()
    return record


def _seed_suggestion(
    db: Any,
    org_id: Any,
    record: EmailMessageRecord,
) -> EmailTaskSuggestion:
    suggestion = EmailTaskSuggestion(
        organization_id=org_id,
        email_message_record_id=record.id,
        source_item_id=None,
        title="Private follow-up",
        description="Only the mailbox owner may review this.",
        gmail_message_id=record.gmail_message_id,
        evidence_text="Please follow up tomorrow.",
        ai_provider="mock",
        ai_model="mock",
        status=SuggestionStatus.SUGGESTED,
    )
    db.add(suggestion)
    db.flush()
    return suggestion


def _seed_draft(
    db: Any,
    org_id: Any,
    user_id: Any,
    connection_id: Any,
) -> EmailDraft:
    draft = EmailDraft(
        organization_id=org_id,
        user_id=user_id,
        integration_connection_id=connection_id,
        source_item_id=None,
        business_entity_id=None,
        gmail_thread_id="private-thread",
        to_recipients_json=["client@example.com"],
        subject="Owner draft",
        body_text="Private draft content.",
        tone="professional",
        purpose="Follow up",
        referenced_facts_json=[],
        warnings_json=[],
        status=EmailDraftStatus.USER_APPROVED,
        gmail_draft_id=None,
        gmail_sent_message_id=None,
        send_idempotency_key=f"send-{uuid4().hex}",
    )
    db.add(draft)
    db.flush()
    return draft


def _seed_calendar_link(
    db: Any,
    org_id: Any,
    user_id: Any,
    connection_id: Any,
    *,
    sync_status: CalendarSyncStatus,
) -> CalendarEventLink:
    action = ActionItem(
        organization_id=org_id,
        title="Owner calendar action",
        description="Private calendar details",
        status="OPEN",
        ai_generated=False,
    )
    db.add(action)
    db.flush()

    link = CalendarEventLink(
        organization_id=org_id,
        user_id=user_id,
        action_item_id=action.id,
        integration_connection_id=connection_id,
        google_calendar_id="primary",
        google_event_id=(
            None if sync_status == CalendarSyncStatus.FAILED else "owner-event"
        ),
        idempotency_key=f"calendar-{uuid4().hex}",
        sync_status=sync_status,
        last_synced_at=datetime.now(timezone.utc),
        last_error=(
            "previous safe failure"
            if sync_status == CalendarSyncStatus.FAILED
            else None
        ),
    )
    db.add(link)
    db.flush()
    return link


def test_same_org_user_cannot_read_or_list_another_users_gmail_data(
    isolated_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
) -> None:
    org_id = seeded_user["organization"].id
    owner = seeded_user["user"]
    other = _make_second_user(db_session, seeded_user)
    connection = _seed_connection(db_session, org_id, owner.id)
    record = _seed_email_record(db_session, org_id, connection.id)
    suggestion = _seed_suggestion(db_session, org_id, record)

    _login(isolated_client, other, seeded_user["password"])

    messages = isolated_client.get("/api/gmail/messages")
    suggestions = isolated_client.get("/api/gmail/suggestions")
    filtered = isolated_client.get(
        "/api/gmail/suggestions", params={"record_id": str(record.id)}
    )
    detail = isolated_client.get(f"/api/gmail/messages/{record.id}")

    assert messages.status_code == 200
    assert messages.json() == []
    assert suggestions.status_code == 200
    assert suggestions.json() == []
    assert filtered.status_code == 200
    assert filtered.json() == []
    assert detail.status_code == 404
    assert db_session.get(EmailMessageRecord, record.id) is not None
    assert db_session.get(EmailTaskSuggestion, suggestion.id) is not None


def test_same_org_user_cannot_delete_another_users_gmail_message(
    isolated_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
) -> None:
    org_id = seeded_user["organization"].id
    owner = seeded_user["user"]
    other = _make_second_user(db_session, seeded_user)
    connection = _seed_connection(db_session, org_id, owner.id)
    record = _seed_email_record(db_session, org_id, connection.id)
    suggestion = _seed_suggestion(db_session, org_id, record)

    _login(isolated_client, other, seeded_user["password"])
    response = isolated_client.delete(f"/api/gmail/messages/{record.id}")

    assert response.status_code == 404
    db_session.expire_all()
    assert db_session.get(EmailMessageRecord, record.id) is not None
    assert db_session.get(EmailTaskSuggestion, suggestion.id) is not None


@pytest.mark.parametrize(
    ("method", "suffix", "payload"),
    [
        ("PATCH", "", {"title": "Hijacked title"}),
        ("POST", "/confirm", None),
        ("POST", "/reject", None),
        ("POST", "/dismiss", None),
        ("DELETE", "", None),
    ],
)
def test_same_org_user_cannot_review_another_users_gmail_suggestion(
    isolated_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
    method: str,
    suffix: str,
    payload: dict[str, Any] | None,
) -> None:
    org_id = seeded_user["organization"].id
    owner = seeded_user["user"]
    other = _make_second_user(db_session, seeded_user)
    connection = _seed_connection(db_session, org_id, owner.id)
    record = _seed_email_record(db_session, org_id, connection.id)
    suggestion = _seed_suggestion(db_session, org_id, record)

    _login(isolated_client, other, seeded_user["password"])
    response = isolated_client.request(
        method,
        f"/api/gmail/suggestions/{suggestion.id}{suffix}",
        json=payload,
    )

    assert response.status_code == 404
    db_session.expire_all()
    unchanged = db_session.get(EmailTaskSuggestion, suggestion.id)
    assert unchanged is not None
    assert unchanged.title == "Private follow-up"
    assert unchanged.status == SuggestionStatus.SUGGESTED
    assert db_session.query(ActionItem).count() == 0


@pytest.mark.parametrize(
    ("method", "suffix", "payload"),
    [
        ("GET", "", None),
        ("PATCH", "", {"subject": "Hijacked subject"}),
        ("POST", "/approve", None),
        ("POST", "/reject", None),
        ("POST", "/create-gmail-draft", None),
        ("POST", "/send", {"confirm": True}),
        ("DELETE", "", None),
    ],
)
def test_same_org_user_cannot_access_another_users_email_draft(
    isolated_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
    fake_gmail: FakeGmailClient,
    method: str,
    suffix: str,
    payload: dict[str, Any] | None,
) -> None:
    org_id = seeded_user["organization"].id
    owner = seeded_user["user"]
    other = _make_second_user(db_session, seeded_user)
    connection = _seed_connection(db_session, org_id, owner.id)
    draft = _seed_draft(db_session, org_id, owner.id, connection.id)

    _login(isolated_client, other, seeded_user["password"])
    assert isolated_client.get("/api/email-drafts").json() == []
    response = isolated_client.request(
        method,
        f"/api/email-drafts/{draft.id}{suffix}",
        json=payload,
    )

    assert response.status_code == 404
    db_session.expire_all()
    unchanged = db_session.get(EmailDraft, draft.id)
    assert unchanged is not None
    assert unchanged.subject == "Owner draft"
    assert unchanged.status == EmailDraftStatus.USER_APPROVED
    assert unchanged.gmail_draft_id is None
    assert unchanged.gmail_sent_message_id is None
    assert fake_gmail.create_draft_calls == 0
    assert fake_gmail.update_draft_calls == 0
    assert fake_gmail.send_calls == 0


@pytest.mark.parametrize(
    ("method", "suffix", "payload", "initial_status"),
    [
        ("GET", "", None, CalendarSyncStatus.SYNCED),
        (
            "PATCH",
            "",
            {"summary": "Hijacked calendar title"},
            CalendarSyncStatus.SYNCED,
        ),
        ("POST", "/cancel", None, CalendarSyncStatus.SYNCED),
        ("POST", "/retry", None, CalendarSyncStatus.FAILED),
    ],
)
def test_same_org_user_cannot_access_another_users_calendar_link(
    isolated_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
    fake_calendar: FakeCalendarClient,
    method: str,
    suffix: str,
    payload: dict[str, Any] | None,
    initial_status: CalendarSyncStatus,
) -> None:
    org_id = seeded_user["organization"].id
    owner = seeded_user["user"]
    other = _make_second_user(db_session, seeded_user)
    connection = _seed_connection(
        db_session,
        org_id,
        owner.id,
        service=IntegrationServiceEnum.GOOGLE_CALENDAR,
    )
    link = _seed_calendar_link(
        db_session,
        org_id,
        owner.id,
        connection.id,
        sync_status=initial_status,
    )

    _login(isolated_client, other, seeded_user["password"])
    assert isolated_client.get("/api/calendar/links").json() == []
    response = isolated_client.request(
        method,
        f"/api/calendar/links/{link.id}{suffix}",
        json=payload,
    )

    assert response.status_code == 404
    db_session.expire_all()
    unchanged = db_session.get(CalendarEventLink, link.id)
    assert unchanged is not None
    assert unchanged.sync_status == initial_status
    assert fake_calendar.create_calls == 0
    assert fake_calendar.update_calls == 0
    assert fake_calendar.delete_calls == 0


def test_failed_calendar_cancellation_retries_delete_not_update(
    isolated_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
    fake_calendar: FakeCalendarClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = seeded_user["organization"].id
    owner = seeded_user["user"]
    connection = _seed_connection(
        db_session,
        org_id,
        owner.id,
        service=IntegrationServiceEnum.GOOGLE_CALENDAR,
    )
    vault = TokenVault(Settings(token_encryption_key=_TEST_KEY))
    connection.access_token_encrypted = vault.encrypt("calendar-access")
    connection.refresh_token_encrypted = vault.encrypt("calendar-refresh")
    db_session.add(connection)
    link = _seed_calendar_link(
        db_session,
        org_id,
        owner.id,
        connection.id,
        sync_status=CalendarSyncStatus.SYNCED,
    )
    _login(isolated_client, owner, seeded_user["password"])

    delete_attempts = 0
    real_delete = fake_calendar.delete_event

    def fail_first_delete(**kwargs: Any) -> None:
        nonlocal delete_attempts
        delete_attempts += 1
        if delete_attempts == 1:
            raise CalendarClientError("Simulated ambiguous delete failure.")
        real_delete(**kwargs)

    monkeypatch.setattr(fake_calendar, "delete_event", fail_first_delete)

    failed = isolated_client.post(f"/api/calendar/links/{link.id}/cancel")
    assert failed.status_code == 200
    assert failed.json()["sync_status"] == "CANCEL_PENDING"
    assert "cancellation was not confirmed" in failed.json()["last_error"]
    assert delete_attempts == 1
    assert fake_calendar.update_calls == 0

    retried = isolated_client.post(f"/api/calendar/links/{link.id}/retry")
    assert retried.status_code == 200
    assert retried.json()["sync_status"] == "CANCELLED"
    assert delete_attempts == 2
    assert fake_calendar.update_calls == 0

    # Once cancelled, retry is locally idempotent and creates no second audit.
    again = isolated_client.post(f"/api/calendar/links/{link.id}/retry")
    assert again.status_code == 200
    assert again.json()["sync_status"] == "CANCELLED"
    assert delete_attempts == 2
    audits = (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == "CANCEL_CALENDAR_EVENT")
        .all()
    )
    assert len(audits) == 1


def test_privacy_email_delete_only_targets_the_current_users_connections(
    isolated_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
) -> None:
    org_id = seeded_user["organization"].id
    owner = seeded_user["user"]
    other = _make_second_user(db_session, seeded_user)
    owner_connection = _seed_connection(db_session, org_id, owner.id)
    other_connection = _seed_connection(db_session, org_id, other.id)
    owner_record = _seed_email_record(
        db_session, org_id, owner_connection.id, subject="Owner email"
    )
    other_record = _seed_email_record(
        db_session, org_id, other_connection.id, subject="Other email"
    )
    owner_suggestion = _seed_suggestion(db_session, org_id, owner_record)
    other_suggestion = _seed_suggestion(db_session, org_id, other_record)

    _login(isolated_client, other, seeded_user["password"])

    selected = isolated_client.request(
        "DELETE",
        "/api/privacy/email-data",
        json={"scope": "SELECTED", "record_ids": [str(owner_record.id)]},
    )
    connection = isolated_client.request(
        "DELETE",
        "/api/privacy/email-data",
        json={
            "scope": "CONNECTION",
            "connection_id": str(owner_connection.id),
        },
    )
    assert selected.status_code == 200
    assert selected.json()["deleted_records"] == 0
    assert connection.status_code == 200
    assert connection.json()["deleted_records"] == 0
    assert db_session.get(EmailMessageRecord, owner_record.id) is not None

    own_all = isolated_client.request(
        "DELETE", "/api/privacy/email-data", json={"scope": "ALL"}
    )
    assert own_all.status_code == 200
    assert own_all.json()["deleted_records"] == 1
    assert own_all.json()["deleted_task_suggestions"] == 1

    db_session.expire_all()
    assert db_session.get(EmailMessageRecord, owner_record.id) is not None
    assert db_session.get(EmailTaskSuggestion, owner_suggestion.id) is not None
    assert db_session.get(EmailMessageRecord, other_record.id) is None
    assert db_session.get(EmailTaskSuggestion, other_suggestion.id) is None


def test_retention_update_does_not_change_another_users_email_records(
    isolated_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
) -> None:
    org_id = seeded_user["organization"].id
    owner = seeded_user["user"]
    other = _make_second_user(db_session, seeded_user)
    owner_connection = _seed_connection(db_session, org_id, owner.id)
    other_connection = _seed_connection(db_session, org_id, other.id)
    owner_record = _seed_email_record(db_session, org_id, owner_connection.id)
    other_record = _seed_email_record(db_session, org_id, other_connection.id)

    _login(isolated_client, other, seeded_user["password"])
    response = isolated_client.put(
        "/api/privacy/retention-policy",
        json={"mode": "EXTRACTED_ONLY", "retention_window_days": 30},
    )

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(EmailMessageRecord, owner_record.id).stored_raw is True
    assert db_session.get(EmailMessageRecord, other_record.id).stored_raw is False


def test_copilot_does_not_use_another_same_org_users_email_as_evidence(
    isolated_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
) -> None:
    org_id = seeded_user["organization"].id
    owner = seeded_user["user"]
    other = _make_second_user(db_session, seeded_user)
    owner_connection = _seed_connection(db_session, org_id, owner.id)
    private_subject = "Project Nightfall confidential renewal"
    _seed_email_record(
        db_session,
        org_id,
        owner_connection.id,
        subject=private_subject,
    )

    _login(isolated_client, other, seeded_user["password"])
    response = isolated_client.post(
        "/api/copilot/ask",
        json={"question": "What happened with Project Nightfall?"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["insufficient_evidence"] is True
    assert body["citations"] == []
    assert private_subject not in response.text


def test_same_org_user_cannot_read_another_users_answer_provenance(
    isolated_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
) -> None:
    org_id = seeded_user["organization"].id
    owner = seeded_user["user"]
    other = _make_second_user(db_session, seeded_user)
    log = CopilotAnswerLog(
        organization_id=org_id,
        user_id=owner.id,
        question="What private evidence was used?",
        answer="Private answer",
        intent="ASK",
        insufficient_evidence=False,
        citations_json=[],
        evidence_json=[],
    )
    db_session.add(log)
    db_session.flush()

    _login(isolated_client, other, seeded_user["password"])
    denied = isolated_client.get(
        f"/api/privacy/answer-provenance/{log.id}"
    )
    assert denied.status_code == 404

    # The id is valid and remains available to its owner.
    _login(isolated_client, owner, seeded_user["password"])
    allowed = isolated_client.get(
        f"/api/privacy/answer-provenance/{log.id}"
    )
    assert allowed.status_code == 200
    assert allowed.json()["answer_id"] == str(log.id)


def test_same_org_users_have_independent_sender_signals(
    isolated_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
    fake_gmail: FakeGmailClient,
) -> None:
    org_id = seeded_user["organization"].id
    owner = seeded_user["user"]
    other = _make_second_user(db_session, seeded_user)
    no_signal_user = _make_second_user(db_session, seeded_user)

    _login(isolated_client, owner, seeded_user["password"])
    owner_response = isolated_client.post(
        "/api/gmail/sender-signals",
        json={"pattern": "partner.com", "signal_type": "IRRELEVANT"},
    )
    assert owner_response.status_code == 200

    _login(isolated_client, other, seeded_user["password"])
    other_response = isolated_client.post(
        "/api/gmail/sender-signals",
        json={"pattern": "partner.com", "signal_type": "PERSONAL"},
    )
    assert other_response.status_code == 200
    assert other_response.json()["id"] != owner_response.json()["id"]

    signals = (
        db_session.query(EmailSenderSignal)
        .filter(
            EmailSenderSignal.organization_id == org_id,
            EmailSenderSignal.pattern == "partner.com",
        )
        .all()
    )
    assert len(signals) == 2
    assert {signal.created_by for signal in signals} == {owner.id, other.id}

    service = GmailSyncService(db_session, fake_gmail)
    sender = "person@partner.com"
    assert service._matching_sender_signal(
        org_id, owner.id, sender
    ) == SenderSignalType.IRRELEVANT
    assert service._matching_sender_signal(
        org_id, other.id, sender
    ) == SenderSignalType.PERSONAL
    assert service._matching_sender_signal(
        org_id, no_signal_user.id, sender
    ) is None
