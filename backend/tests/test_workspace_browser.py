"""Pagination, provenance, review semantics, and private source access."""
from uuid import uuid4
import pytest
from app.core.models import ActionItem, AuditLog, ClassificationResult, KnowledgeItem, SourceItem, User
from app.modules.cwi.models import DocumentAsset, DocumentChunk, EmailMessageRecord, EmailTaskSuggestion, IntegrationConnection
from tests.test_cwi_calendar_routes import _login


def note(db, user, title="A note", **fields):
    row = SourceItem(organization_id=user.organization_id, created_by=user.id, source_type="MANUAL", title=title, content="Original " * 100, **fields)
    db.add(row); db.flush()
    return row


def email(db, user, source):
    connection = IntegrationConnection(organization_id=user.organization_id, user_id=user.id, provider="GOOGLE", service="GMAIL", external_account_id="test-account", access_token_encrypted=b"test-only", account_email=user.email, status="CONNECTED")
    db.add(connection); db.flush()
    row = EmailMessageRecord(organization_id=user.organization_id, integration_connection_id=connection.id,
        source_item_id=source.id, gmail_message_id="m1", gmail_thread_id="t1", sender="test@example.com", subject="Email source", content_hash="abc")
    db.add(row); db.flush()
    return row


def test_real_pagination_and_bounded_previews(client, db_session, seeded_user):
    _login(client, seeded_user)
    user = seeded_user["user"]
    for i in range(23):
        note(db_session, user, f"Note {i}")
    first = client.get("/api/workspace/items").json()
    second = client.get("/api/workspace/items?offset=20").json()
    assert first["total"] == second["total"] == 23
    assert len(first["items"]) == 20 and len(second["items"]) == 3
    assert first["has_more"] and not second["has_more"]
    assert {x["id"] for x in first["items"]}.isdisjoint(x["id"] for x in second["items"])
    assert all(len(x["excerpt"]) <= 240 and "content" not in x for x in first["items"])
    assert client.get("/api/workspace/items?limit=0").status_code == 422
    assert client.get("/api/workspace/items?offset=-1").status_code == 422


def test_source_types_deduplicate_email_and_review_does_not_mean_raw(client, db_session, seeded_user):
    _login(client, seeded_user); user = seeded_user["user"]
    raw = note(db_session, user, "100% raw")
    reviewed = note(db_session, user, "review me")
    message = email(db_session, user, reviewed)
    db_session.add(ClassificationResult(source_item_id=reviewed.id, relevance="WORK_RELATED", business_category="PROJECT", sensitivity="INTERNAL", confidence=.8, status="SUGGESTED")); db_session.flush()
    response = client.get("/api/workspace/items")
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 2
    assert page["type_counts"] == {"note": 1, "email": 1}
    assert {row["status"] for row in page["items"]} == {"RAW", "NEEDS_REVIEW"}
    assert client.get("/api/workspace/items?source_type=email&status=NEEDS_REVIEW").json()["items"][0]["id"] == str(message.id)
    assert client.get("/api/workspace/items?q=%25").json()["total"] == 1
    assert client.get("/api/workspace/counts").json()["sources_reviews"] == 1
    assert client.get(f"/api/workspace/source/email/{message.id}").json()["source"]["id"] == str(reviewed.id)
    assert client.get(f"/api/workspace/source/source/{raw.id}").json()["raw_content"].startswith("Original")


def test_source_and_file_detail_access_and_paged_chunks(client, db_session, seeded_user):
    _login(client, seeded_user); user = seeded_user["user"]
    other = User(organization_id=user.organization_id, email="browser-other@example.com", full_name="Other", password_hash="unused", role="MEMBER")
    db_session.add(other); db_session.flush()
    foreign = note(db_session, other)
    asset = DocumentAsset(organization_id=user.organization_id, uploaded_by=user.id, filename="A.txt", mime_type="text/plain", storage_key="private-never-return", checksum="abc")
    private = DocumentAsset(organization_id=user.organization_id, uploaded_by=other.id, filename="Secret.txt", mime_type="text/plain", storage_key="secret", checksum="def")
    db_session.add_all([asset, private]); db_session.flush()
    for i in range(12):
        db_session.add(DocumentChunk(organization_id=user.organization_id, document_asset_id=asset.id, chunk_index=i, text=f"Passage {i}"))
    db_session.flush()
    assert client.get(f"/api/workspace/source/source/{foreign.id}").status_code == 404
    for suffix in [f"source/file/{private.id}", f"files/{private.id}/chunks", f"source/file/{private.id}/links"]:
        assert client.get("/api/workspace/" + suffix).status_code == 404
    assert client.get("/api/workspace/items?source_type=file").json()["total"] == 1
    detail = client.get(f"/api/workspace/source/file/{asset.id}")
    assert detail.status_code == 200, detail.text
    assert "storage_key" not in detail.text and "Passage" not in detail.text
    chunks = client.get(f"/api/workspace/files/{asset.id}/chunks?offset=10").json()
    assert chunks["total"] == 12 and len(chunks["items"]) == 2
    assert chunks["items"][0]["chunk_index"] == 10


def test_links_use_real_provenance_and_confirmed_email_actions(client, db_session, seeded_user):
    _login(client, seeded_user); user = seeded_user["user"]
    source = note(db_session, user); message = email(db_session, user, source)
    knowledge = KnowledgeItem(organization_id=user.organization_id, source_item_id=source.id, summary="Extracted fact", knowledge_type="FACT", evidence_text="Original")
    suggestion = EmailTaskSuggestion(organization_id=user.organization_id, email_message_record_id=message.id, source_item_id=source.id,
        title="Proposed task", gmail_message_id="m1", evidence_text="Original", ai_provider="mock", ai_model="mock")
    db_session.add_all([knowledge, suggestion]); db_session.flush()
    linked = ActionItem(organization_id=user.organization_id, knowledge_item_id=knowledge.id, title="Linked task", ai_generated=False)
    unrelated = ActionItem(organization_id=user.organization_id, title="Linked task", evidence_text="Original", ai_generated=False)
    db_session.add_all([linked, unrelated]); db_session.flush()
    response = client.get(f"/api/workspace/source/email/{message.id}/links")
    assert response.status_code == 200, response.text
    assert {x["kind"] for x in response.json()["items"]} == {"action", "knowledge", "suggestion"}
    assert str(unrelated.id) not in {x["id"] for x in response.json()["items"]}
    assert client.get("/api/workspace/counts").json()["actions_reviews"] == 1
    confirmed = client.post(f"/api/gmail/suggestions/{suggestion.id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    rows = client.get(f"/api/workspace/source/email/{message.id}/links").json()["items"]
    actions = [x for x in rows if x["kind"] == "action"]
    assert len(actions) == 2 and not any(x["kind"] == "suggestion" for x in rows)
    confirmed_action = next(x for x in actions if x["id"] != str(linked.id))
    context = client.get(f"/api/workspace/actions/{confirmed_action['id']}/context")
    assert context.status_code == 200, context.text
    assert context.json()["email_id"] == str(message.id)
    assert client.get("/api/workspace/counts").json()["actions_reviews"] == 0
    feed = client.get("/api/workspace/items?section=actions").json()
    assert feed["total"] == 3 and all(x["kind"] == "action" for x in feed["items"])


@pytest.mark.parametrize("section,model,fields", [
    ("knowledge", KnowledgeItem, {"summary": "Fact", "knowledge_type": "FACT", "evidence_text": "evidence", "status": "CONFIRMED"}),
    ("actions", ActionItem, {"title": "Task", "ai_generated": False, "status": "DONE"}),
])
def test_knowledge_actions_are_paged_and_status_filtered(client, db_session, seeded_user, section, model, fields):
    _login(client, seeded_user); user = seeded_user["user"]
    db_session.add_all([model(organization_id=user.organization_id, **fields) for _ in range(22)]); db_session.flush()
    page = client.get(f"/api/workspace/items?section={section}&offset=20&status={fields['status']}").json()
    assert page["total"] == 22 and len(page["items"]) == 2
    assert client.get(f"/api/workspace/items?section={section}&status=SUGGESTED").json()["total"] == 0


def test_private_workspace_endpoints_require_session(client):
    item_id = uuid4()
    for path in ["items", "counts", f"source/source/{item_id}", f"source/email/{item_id}/links", f"files/{item_id}/chunks", f"suggestions/{item_id}", f"actions/{item_id}/context"]:
        assert client.get("/api/workspace/" + path).status_code == 401
