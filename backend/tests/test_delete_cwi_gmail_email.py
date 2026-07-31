"""Delete-endpoint tests for the CWI Gmail + email-draft resources.

Covers audit-row-written and org-isolation for:

* ``DELETE /api/gmail/messages/{id}`` — cascades the record's task suggestions;
* ``DELETE /api/gmail/suggestions/{id}``;
* ``DELETE /api/email-drafts/{id}`` — local delete only (never calls Gmail).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import models as core_models
from app.modules.cwi import models as cwi_models
from app.security import hash_password


def _login(client: TestClient, email: str, password: str) -> Any:
    return client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )


def _connection(db_session: Session, org_id: UUID, user_id: UUID) -> cwi_models.IntegrationConnection:
    conn = cwi_models.IntegrationConnection(
        organization_id=org_id,
        user_id=user_id,
        provider=cwi_models.IntegrationProvider.GOOGLE,
        service=cwi_models.IntegrationService.GMAIL,
        external_account_id="acct-1",
        account_email="mailbox@example.com",
        granted_scopes_json=[],
        access_token_encrypted=b"ciphertext",
        status=cwi_models.ConnectionStatus.CONNECTED,
    )
    db_session.add(conn)
    db_session.flush()
    return conn


def _second_org_login(client: TestClient, db_session: Session) -> None:
    org = core_models.Organization(name="Other Org")
    db_session.add(org)
    db_session.flush()
    user = core_models.User(
        organization_id=org.id,
        email="other-cwi@example.com",
        full_name="Other",
        password_hash=hash_password("pw2"),
        role="ADMIN",
    )
    db_session.add(user)
    db_session.flush()
    client.cookies.clear()
    assert _login(client, "other-cwi@example.com", "pw2").status_code == 200


def _audit_count(db_session: Session, action_type: str) -> int:
    return (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == action_type)
        .count()
    )


def test_delete_message_cascades_suggestions_and_audits(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    org_id = seeded_user["organization"].id
    conn = _connection(db_session, org_id, seeded_user["user"].id)

    record = cwi_models.EmailMessageRecord(
        organization_id=org_id,
        integration_connection_id=conn.id,
        gmail_message_id="m1",
        gmail_thread_id="t1",
        sender="a@b.com",
        content_hash="hash1",
    )
    db_session.add(record)
    db_session.flush()
    suggestion = cwi_models.EmailTaskSuggestion(
        organization_id=org_id,
        email_message_record_id=record.id,
        title="Task",
        gmail_message_id="m1",
        evidence_text="e",
        ai_provider="mock",
        ai_model="mock-1",
        status=core_models.SuggestionStatus.SUGGESTED,
    )
    db_session.add(suggestion)
    db_session.flush()
    record_id, suggestion_id = record.id, suggestion.id

    assert client.delete(f"/api/gmail/messages/{record_id}").status_code == 204

    db_session.expire_all()
    assert db_session.get(cwi_models.EmailMessageRecord, record_id) is None
    assert db_session.get(cwi_models.EmailTaskSuggestion, suggestion_id) is None
    assert _audit_count(db_session, "DELETE_EMAIL_MESSAGE") == 1


def test_delete_suggestion_audits(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    org_id = seeded_user["organization"].id
    conn = _connection(db_session, org_id, seeded_user["user"].id)
    record = cwi_models.EmailMessageRecord(
        organization_id=org_id,
        integration_connection_id=conn.id,
        gmail_message_id="m2",
        gmail_thread_id="t2",
        sender="a@b.com",
        content_hash="hash2",
    )
    db_session.add(record)
    db_session.flush()
    suggestion = cwi_models.EmailTaskSuggestion(
        organization_id=org_id,
        email_message_record_id=record.id,
        title="Task",
        gmail_message_id="m2",
        evidence_text="e",
        ai_provider="mock",
        ai_model="mock-1",
        status=core_models.SuggestionStatus.SUGGESTED,
    )
    db_session.add(suggestion)
    db_session.flush()
    suggestion_id = suggestion.id

    assert client.delete(f"/api/gmail/suggestions/{suggestion_id}").status_code == 204
    db_session.expire_all()
    assert db_session.get(cwi_models.EmailTaskSuggestion, suggestion_id) is None
    # The record is retained.
    assert db_session.get(cwi_models.EmailMessageRecord, record.id) is not None
    assert _audit_count(db_session, "DELETE_EMAIL_TASK_SUGGESTION") == 1


def test_delete_email_draft_local_only_and_audits(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    org_id = seeded_user["organization"].id
    conn = _connection(db_session, org_id, seeded_user["user"].id)

    draft = cwi_models.EmailDraft(
        organization_id=org_id,
        user_id=seeded_user["user"].id,
        integration_connection_id=conn.id,
        to_recipients_json=["a@b.com"],
        subject="Hi",
        body_text="Body",
        status=cwi_models.EmailDraftStatus.AI_SUGGESTED,
        send_idempotency_key="key-1",
    )
    db_session.add(draft)
    db_session.flush()
    draft_id = draft.id

    assert client.delete(f"/api/email-drafts/{draft_id}").status_code == 204
    db_session.expire_all()
    assert db_session.get(cwi_models.EmailDraft, draft_id) is None
    assert _audit_count(db_session, "DELETE_EMAIL_DRAFT") == 1


def test_cross_org_gmail_and_draft_delete_404(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    org_id = seeded_user["organization"].id
    conn = _connection(db_session, org_id, seeded_user["user"].id)
    record = cwi_models.EmailMessageRecord(
        organization_id=org_id,
        integration_connection_id=conn.id,
        gmail_message_id="m3",
        gmail_thread_id="t3",
        sender="a@b.com",
        content_hash="hash3",
    )
    draft = cwi_models.EmailDraft(
        organization_id=org_id,
        user_id=seeded_user["user"].id,
        integration_connection_id=conn.id,
        subject="Hi",
        body_text="Body",
        status=cwi_models.EmailDraftStatus.AI_SUGGESTED,
        send_idempotency_key="key-2",
    )
    db_session.add_all([record, draft])
    db_session.flush()
    record_id, draft_id = record.id, draft.id

    _second_org_login(client, db_session)
    assert client.delete(f"/api/gmail/messages/{record_id}").status_code == 404
    assert client.delete(f"/api/email-drafts/{draft_id}").status_code == 404
    assert db_session.get(cwi_models.EmailMessageRecord, record_id) is not None
    assert db_session.get(cwi_models.EmailDraft, draft_id) is not None
