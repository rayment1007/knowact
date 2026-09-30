"""Manual destination choice, exact human approval, provenance, and fail-closed AI."""
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.core.models import ActionItem, ClassificationResult, KnowledgeItem, User
from app.core.services.ai_provider import AIProviderError, MockAIProvider, SourceDraftOutput
from app.modules.cwi.models import CalendarSource, DocumentAsset, DocumentChunk, IntegrationConnection, SourceProposal
from app.modules.cwi.services import source_proposal_service as service
from tests.test_cwi_calendar_routes import _login
from tests.test_workspace_browser import email, note


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def prepare(client, kind, item_id, target="knowledge", **extra):
    response = client.post(f"/api/workspace/source/{kind}/{item_id}/proposals", json={"target": target, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def approve(client, draft, **payload):
    return client.post(f"/api/workspace/proposals/{draft['id']}/approve", json={"version": draft["version"], "payload": payload or draft["payload"]})


def file_source(db, user):
    row = DocumentAsset(organization_id=user.organization_id, uploaded_by=user.id, filename="Specification.txt",
        mime_type="text/plain", storage_key="test", checksum="hash", processing_status="INDEXED")
    db.add(row); db.flush()
    db.add(DocumentChunk(organization_id=user.organization_id, document_asset_id=row.id, chunk_index=0,
        text="The delivery includes an editable review screen.")); db.flush()
    return row


def calendar_source(db, user):
    connection = IntegrationConnection(organization_id=user.organization_id, user_id=user.id, provider="GOOGLE",
        service="GOOGLE_CALENDAR", external_account_id="calendar", access_token_encrypted=b"test", account_email=user.email, status="CONNECTED")
    db.add(connection); db.flush()
    row = CalendarSource(organization_id=user.organization_id, integration_connection_id=connection.id,
        google_event_id="event", title="Review meeting", description="Discuss the delivery scope.",
        location="Meeting room", starts_at="2026-10-01T14:00:00+08:00", ends_at="2026-10-01T15:00:00+08:00")
    db.add(row); db.flush()
    return row


@pytest.mark.parametrize("kind", ["source", "email", "file", "calendar"])
@pytest.mark.parametrize("target", ["knowledge", "action"])
def test_manual_choice_edit_then_approve_has_bidirectional_provenance(client, db_session, seeded_user, kind, target):
    _login(client, seeded_user); user = seeded_user["user"]
    row = file_source(db_session, user) if kind == "file" else calendar_source(db_session, user) if kind == "calendar" else note(db_session, user)
    if kind == "email": row = email(db_session, user, row)
    draft = prepare(client, kind, row.id, target)
    assert count(db_session, KnowledgeItem) == count(db_session, ActionItem) == 0
    assert draft["status"] == "SUGGESTED" and draft["provider"] == "mock"
    assert client.get(f"/api/workspace/items?section={'knowledge' if target == 'knowledge' else 'actions'}&status=SUGGESTED").json()["items"][0]["kind"] == "proposal"
    links = client.get(f"/api/workspace/source/{kind}/{row.id}/links").json()
    assert links["items"][0]["id"] == draft["id"]
    edited = {"summary": "User reviewed summary", "key_points": ["User corrected point"]} if target == "knowledge" else {"title": "User reviewed task", "description": "Edited description", "due_date": "2026-10-05"}
    response = client.patch(f"/api/workspace/proposals/{draft['id']}", json={"version": draft["version"], "payload": edited})
    assert response.status_code == 200, response.text
    assert count(db_session, KnowledgeItem) == count(db_session, ActionItem) == 0
    assert approve(client, draft).status_code == 409
    saved = response.json()
    # Approval carries the latest text even when the user hasn't pressed Save first.
    edited["summary" if target == "knowledge" else "title"] = "Final reviewed text"
    response = approve(client, saved, **edited)
    assert response.status_code == 200, response.text
    result_id = response.json()["result_id"]
    model = KnowledgeItem if target == "knowledge" else ActionItem
    result = db_session.scalar(select(model))
    assert getattr(result, "summary" if target == "knowledge" else "title") == "Final reviewed text"
    assert result.status == ("CONFIRMED" if target == "knowledge" else "OPEN")
    assert result.evidence_text == draft["evidence_text"]
    assert approve(client, saved, **edited).json()["result_id"] == result_id
    assert count(db_session, model) == 1
    endpoint = f"knowledge/{result_id}/origin" if target == "knowledge" else f"actions/{result_id}/context"
    origins = client.get("/api/workspace/" + endpoint).json()
    if target == "action": origins = origins["origins"]
    assert origins[0]["kind"] == kind and origins[0]["id"] == str(row.id)
    assert origins[0]["path"] == f"/workspace/sources?item={kind}:{row.id}"
    links = client.get(f"/api/workspace/source/{kind}/{row.id}/links").json()["items"]
    assert len(links) == 1 and links[0]["id"] == result_id and links[0]["kind"] == target


def test_pending_reuse_rejection_and_source_change(client, db_session, seeded_user, monkeypatch):
    _login(client, seeded_user); row = note(db_session, seeded_user["user"])
    provider = Mock(wraps=MockAIProvider()); monkeypatch.setattr(service, "get_ai_provider", lambda _: provider)
    first = prepare(client, "source", row.id, "action")
    assert prepare(client, "source", row.id, "action")["id"] == first["id"]
    assert provider.prepare_source_draft.call_count == 1
    assert first["payload"]["due_date"] is None
    row.content = "The original source has changed."; db_session.flush()
    response = approve(client, first)
    assert response.status_code == 409 and "source changed" in response.text
    assert count(db_session, ActionItem) == 0
    assert client.post(f"/api/workspace/proposals/{first['id']}/reject", json={"version": 1}).status_code == 200
    assert count(db_session, ActionItem) == 0
    assert prepare(client, "source", row.id, "action")["id"] != first["id"]


def test_privacy_and_owner_checks_precede_ai(client, db_session, seeded_user, monkeypatch):
    _login(client, seeded_user); user = seeded_user["user"]
    row = note(db_session, user)
    provider = Mock(wraps=MockAIProvider()); monkeypatch.setattr(service, "get_ai_provider", lambda _: provider)
    classification = ClassificationResult(source_item_id=row.id, relevance="PERSONAL", business_category="OTHER",
        sensitivity="HIGHLY_SENSITIVE", confidence=.9, status="SUGGESTED")
    db_session.add(classification); db_session.flush()
    response = client.post(f"/api/workspace/source/source/{row.id}/proposals", json={"target": "knowledge"})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "SENSITIVE_ACK_REQUIRED"
    provider.prepare_source_draft.assert_not_called()
    # An AI's suggested classification is not a gate on manual destination choice.
    draft = prepare(client, "source", row.id, acknowledge_sensitive=True)
    other = User(organization_id=user.organization_id, email="private@example.com", full_name="Other", role="MEMBER", password_hash="unused")
    db_session.add(other); db_session.flush()
    db_session.get(SourceProposal, UUID(draft["id"])).user_id = other.id; db_session.flush()
    assert client.get(f"/api/workspace/proposals/{draft['id']}").status_code == 404
    assert approve(client, draft).status_code == 404
    assert client.get("/api/workspace/counts").json()["knowledge_reviews"] == 0
    classification.status = "CONFIRMED"; db_session.flush()
    response = client.post(f"/api/workspace/source/source/{row.id}/proposals", json={"target": "action", "acknowledge_sensitive": True})
    assert response.status_code == 409
    assert provider.prepare_source_draft.call_count == 1


@pytest.mark.parametrize("failure", ["provider", "quote", "empty"])
def test_ai_failures_do_not_leave_records_or_fallback_drafts(client, db_session, seeded_user, monkeypatch, failure):
    _login(client, seeded_user); row = note(db_session, seeded_user["user"])
    provider = Mock()
    if failure == "provider": provider.prepare_source_draft.side_effect = AIProviderError("test-only upstream failure")
    else: provider.prepare_source_draft.return_value = SourceDraftOutput(insufficient_evidence=failure == "empty",
        summary="Invalid summary", key_points=[], title="", description="", due_date=None, evidence_text="invented quote")
    monkeypatch.setattr(service, "get_ai_provider", lambda _: provider)
    response = client.post(f"/api/workspace/source/source/{row.id}/proposals", json={"target": "knowledge"})
    assert response.status_code in (422, 503) and "upstream failure" not in response.text
    assert count(db_session, SourceProposal) == count(db_session, KnowledgeItem) == count(db_session, ActionItem) == 0


def test_bounded_file_input_and_processing_prerequisite(client, db_session, seeded_user, monkeypatch):
    _login(client, seeded_user); row = file_source(db_session, seeded_user["user"])
    row.processing_status = "UPLOADED"; db_session.flush()
    provider = Mock(wraps=MockAIProvider()); monkeypatch.setattr(service, "get_ai_provider", lambda _: provider)
    path = f"/api/workspace/source/file/{row.id}/proposals"
    assert client.post(path, json={"target": "knowledge"}).status_code == 409
    provider.prepare_source_draft.assert_not_called()
    row.processing_status = "INDEXED"
    chunk = db_session.scalar(select(DocumentChunk)); chunk.text = "Source sentence. " * 3000; db_session.flush()
    draft = prepare(client, "file", row.id)
    assert draft["analysis_truncated"]
    assert len(provider.prepare_source_draft.call_args.args[0]) == service.MAX_TEXT


def test_deleting_raw_source_removes_pending_but_preserves_approved_origin(client, db_session, seeded_user):
    _login(client, seeded_user); row = note(db_session, seeded_user["user"])
    pending = prepare(client, "source", row.id, "action")
    accepted = approve(client, prepare(client, "source", row.id)).json()
    response = client.delete(f"/api/source-items/{row.id}")
    assert response.status_code in (200, 204), response.text
    assert client.get(f"/api/workspace/proposals/{pending['id']}").status_code == 404
    assert count(db_session, KnowledgeItem) == 1 and count(db_session, ActionItem) == 0
    origins = client.get(f"/api/workspace/knowledge/{accepted['result_id']}/origin").json()
    assert not origins[0]["available"] and origins[0]["path"] is None


def test_calendar_update_cannot_approve_old_snapshot(client, db_session, seeded_user):
    _login(client, seeded_user); row = calendar_source(db_session, seeded_user["user"])
    draft = prepare(client, "calendar", row.id, "action")
    assert draft["payload"]["due_date"] is None
    row.starts_at = "2026-10-02T14:00:00+08:00"; db_session.flush()
    assert approve(client, draft).status_code == 409
    assert count(db_session, ActionItem) == 0


def test_action_inherits_original_source_from_linked_knowledge(client, db_session, seeded_user):
    _login(client, seeded_user); user = seeded_user["user"]; row = file_source(db_session, user)
    accepted = approve(client, prepare(client, "file", row.id)).json()
    action = ActionItem(organization_id=user.organization_id, knowledge_item_id=UUID(accepted["result_id"]), title="Follow up")
    db_session.add(action); db_session.flush()
    context = client.get(f"/api/workspace/actions/{action.id}/context").json()
    assert context["origins"][0]["path"] == f"/workspace/sources?item=file:{row.id}"


def test_gmail_deletion_cleans_drafts_for_the_deleted_email(client, db_session, seeded_user):
    _login(client, seeded_user); user = seeded_user["user"]
    source = note(db_session, user); message = email(db_session, user, source)
    draft = prepare(client, "email", message.id, "action")
    response = client.delete(f"/api/gmail/messages/{message.id}")
    assert response.status_code in (200, 204), response.text
    assert client.get(f"/api/workspace/proposals/{draft['id']}").status_code == 404


def test_source_draft_real_adapter_uses_structured_output_and_user_selected_target():
    from tests.test_llm_provider import _provider
    from app.core.services.ai_provider import SourceActionDraftOutput
    output = SourceActionDraftOutput(insufficient_evidence=False, title="Review scope",
        description="Suggested follow-up: review the meeting scope.", due_date=None, evidence_text="Discuss the delivery scope.")
    provider = _provider(parsed=output)
    result = provider.prepare_source_draft("Discuss the delivery scope.", "Review meeting", "action")
    assert result.title == output.title and result.description == output.description
    call = provider._client.chat.completions.calls[0]
    assert call["response_format"] is SourceActionDraftOutput
    assert "summary" not in call["response_format"].model_fields
    assert "user has explicitly chosen" in call["messages"][0]["content"]
    assert "not merely a meeting date" in call["messages"][0]["content"]
    assert "Discuss the delivery scope." in call["messages"][1]["content"]
