"""Phase 1 regressions: materialized drafts send reviewed content, offline only."""

from uuid import UUID

import pytest

from app.core import models
from app.core.services.ai_provider import MockAIProvider
from app.modules.cwi.models import EmailDraft, EmailDraftStatus
from app.modules.cwi.services.email_draft_service import EmailDraftService
from app.modules.cwi.services.gmail_client import GmailClientError
from tests.test_cwi_email_draft_routes import (
    cwi_client, fake_gmail, _connect_gmail, _login, _request_draft,
)


def _materialize(client, seeded_user):
    _login(client, seeded_user)
    connection_id = _connect_gmail(client)
    draft_id = _request_draft(client, connection_id)["id"]
    assert client.post(f"/api/email-drafts/{draft_id}/approve").status_code == 200
    response = client.post(f"/api/email-drafts/{draft_id}/create-gmail-draft")
    assert response.status_code == 200
    return draft_id, response.json()


def test_materialized_edit_updates_provider_and_requires_fresh_approval(
    cwi_client, seeded_user, fake_gmail, monkeypatch,
):
    draft_id, original = _materialize(cwi_client, seeded_user)
    provider_messages = {}
    sent = []
    real_send = fake_gmail.send_draft

    def update(**kwargs):
        provider_messages[kwargs["draft_id"]] = kwargs["message"]

    def send(**kwargs):
        # Match the real Gmail behavior: sending by ID uses provider content,
        # not the `message` parameter passed by the service.
        sent.append(provider_messages[kwargs["draft_id"]])
        return real_send(**kwargs)

    monkeypatch.setattr(fake_gmail, "update_draft", update)
    monkeypatch.setattr(fake_gmail, "send_draft", send)
    edited = cwi_client.patch(f"/api/email-drafts/{draft_id}", json={
        "subject": "Reviewed new subject", "body_text": "Reviewed new body",
        "to_recipients": ["reviewed@example.com"],
    })
    assert edited.status_code == 200
    assert edited.json()["status"] == "AI_SUGGESTED"
    assert edited.json()["gmail_draft_id"] == original["gmail_draft_id"]
    assert provider_messages[original["gmail_draft_id"]].body_text == "Reviewed new body"
    assert cwi_client.post(f"/api/email-drafts/{draft_id}/send", json={"confirm": True}).status_code == 409
    assert fake_gmail.send_calls == 0
    assert cwi_client.post(f"/api/email-drafts/{draft_id}/approve").status_code == 200
    # Re-materializing a newly approved revision must not create a duplicate.
    assert cwi_client.post(f"/api/email-drafts/{draft_id}/create-gmail-draft").status_code == 200
    assert fake_gmail.create_draft_calls == 1
    response = cwi_client.post(f"/api/email-drafts/{draft_id}/send", json={"confirm": True})
    assert response.status_code == 200
    assert sent[0].subject == "Reviewed new subject"
    assert sent[0].body_text == "Reviewed new body"
    assert sent[0].to_recipients == ["reviewed@example.com"]
    assert fake_gmail.send_calls == 1


def test_failed_materialized_edit_preserves_local_reviewed_content(
    cwi_client, seeded_user, fake_gmail, monkeypatch,
):
    draft_id, original = _materialize(cwi_client, seeded_user)

    def fail(**kwargs):
        raise GmailClientError("provider update unavailable")

    monkeypatch.setattr(fake_gmail, "update_draft", fail)
    result = cwi_client.patch(f"/api/email-drafts/{draft_id}", json={"body_text": "unsaved edit"})
    assert result.status_code == 502
    current = cwi_client.get(f"/api/email-drafts/{draft_id}").json()
    assert current["body_text"] == original["body_text"]
    assert current["status"] == original["status"]
    assert fake_gmail.send_calls == 0


def test_pre_send_sync_failure_never_sends_and_allows_safe_retry(
    cwi_client, seeded_user, fake_gmail, monkeypatch,
):
    draft_id, _ = _materialize(cwi_client, seeded_user)
    real_update = fake_gmail.update_draft

    def fail(**kwargs):
        raise GmailClientError("provider update unavailable")

    monkeypatch.setattr(fake_gmail, "update_draft", fail)
    result = cwi_client.post(f"/api/email-drafts/{draft_id}/send", json={"confirm": True})
    assert result.status_code == 502
    assert "Nothing was sent" in result.json()["detail"]
    assert fake_gmail.send_calls == 0
    assert cwi_client.get(f"/api/email-drafts/{draft_id}").json()["status"] == "GMAIL_DRAFT_CREATED"
    monkeypatch.setattr(fake_gmail, "update_draft", real_update)
    assert cwi_client.post(f"/api/email-drafts/{draft_id}/send", json={"confirm": True}).status_code == 200
    assert fake_gmail.send_calls == 1


def test_send_resynchronizes_legacy_local_only_edits(
    cwi_client, seeded_user, fake_gmail, db_session, monkeypatch,
):
    draft_id, _ = _materialize(cwi_client, seeded_user)
    draft = db_session.get(EmailDraft, UUID(draft_id))
    draft.body_text = "Previously approved local content"
    db_session.flush()
    order = []
    real_send = fake_gmail.send_draft

    def update(**kwargs):
        order.append(("update", kwargs["message"].body_text))

    def send(**kwargs):
        order.append(("send", kwargs["message"].body_text))
        return real_send(**kwargs)

    monkeypatch.setattr(fake_gmail, "update_draft", update)
    monkeypatch.setattr(fake_gmail, "send_draft", send)
    assert cwi_client.post(f"/api/email-drafts/{draft_id}/send", json={"confirm": True}).status_code == 200
    assert order == [("update", draft.body_text), ("send", draft.body_text)]


def _other_user(db, seeded_user):
    user = models.User(organization_id=seeded_user["organization"].id,
        email="other-draft-owner@example.com", full_name="Other owner",
        password_hash=seeded_user["user"].password_hash, role="ADMIN")
    db.add(user)
    db.flush()
    return user


@pytest.mark.parametrize("kind", ["source", "project"])
def test_draft_request_rejects_same_org_foreign_reference(
    cwi_client, seeded_user, db_session, fake_gmail, kind,
):
    _login(cwi_client, seeded_user)
    connection = _connect_gmail(cwi_client)
    other = _other_user(db_session, seeded_user)
    if kind == "source":
        record = models.SourceItem(organization_id=other.organization_id, created_by=other.id,
            source_type=models.SourceType.MANUAL, title="private", content="private source")
        key = "source_item_id"
    else:
        record = models.BusinessEntity(organization_id=other.organization_id,
            workspace_owner_id=other.id, name="private project", entity_type=models.BusinessEntityType.PROJECT)
        key = "business_entity_id"
    db_session.add(record)
    db_session.flush()
    response = cwi_client.post("/api/email-drafts", json={
        "connection_id": connection, key: str(record.id), "purpose": "follow up",
        "to_recipients": ["test@example.com"],
    })
    assert response.status_code == 404
    assert db_session.query(EmailDraft).count() == 0
    assert fake_gmail.send_calls == 0


def test_email_context_excludes_other_owner_and_unknown_legacy_owner(
    db_session, seeded_user, fake_gmail,
):
    org = seeded_user["organization"].id
    owner = seeded_user["user"].id
    other = _other_user(db_session, seeded_user).id
    for person in (owner, other, None):
        db_session.add(models.KnowledgeItem(organization_id=org, workspace_owner_id=person,
            summary=str(person), key_points=[], evidence_text="evidence", knowledge_type="FACT",
            status=models.SuggestionStatus.CONFIRMED))
        db_session.add(models.ActionItem(organization_id=org, workspace_owner_id=person,
            title=str(person), description="description", status=models.ActionStatus.OPEN))
    for person in (owner, other):
        db_session.add(models.DecisionRecord(organization_id=org, decided_by=person,
            title=str(person), decision="decision", rationale="reason", evidence_text="evidence"))
    db_session.flush()
    service = EmailDraftService(db_session, MockAIProvider(), fake_gmail)
    knowledge, memories = service._confirmed_knowledge(org, owner, None)
    assert [row.summary for row in knowledge] == [str(owner)]
    assert memories == []
    assert [row.title for row in service._open_actions(org, owner, None)] == [str(owner)]
    assert [row.title for row in service._confirmed_decisions(org, owner, None)] == [str(owner)]
