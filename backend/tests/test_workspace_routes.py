"""Workspace search and read-only Calendar sync against local fake providers."""
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.models import ActionItem, AuditLog, ClassificationResult, KnowledgeItem, SourceItem, SourceType, User
from app.modules.cwi.models import CalendarSource, DocumentAsset, DocumentChunk, IntegrationConnection
from app.modules.cwi.services.calendar_client import CalendarClientError, HttpCalendarClient
from tests.test_cwi_calendar_routes import cwi_client, fake_calendar, _login, _connect_calendar


def test_search_original_text_actions_files_and_literal_wildcards(client, db_session, seeded_user):
    _login(client, seeded_user)
    user = seeded_user["user"]
    source = SourceItem(organization_id=user.organization_id, created_by=user.id,
        source_type=SourceType.MANUAL, title="Project note", content="UAT marker 100% complete")
    action = ActionItem(organization_id=user.organization_id, title="Prepare UAT", description="Follow up", ai_generated=False)
    knowledge = KnowledgeItem(organization_id=user.organization_id, summary="UAT fact",
        evidence_text="UAT evidence", knowledge_type="FACT")
    asset = DocumentAsset(organization_id=user.organization_id, uploaded_by=user.id,
        filename="Project.txt", mime_type="text/plain", storage_key="test", checksum="abc")
    db_session.add_all([source, action, knowledge, asset]); db_session.flush()
    db_session.add(DocumentChunk(organization_id=user.organization_id, document_asset_id=asset.id,
        chunk_index=0, text="Hidden UAT phrase inside raw file")); db_session.flush()
    result = client.get("/api/workspace/search", params={"q": "uat"})
    assert result.status_code == 200, result.text
    data = result.json()
    assert {row["kind"] for row in data["items"]} == {"note", "action", "knowledge", "file"}
    assert data["total"] == 4
    assert any(row["status"] == "NEW" for row in data["items"])
    assert client.get("/api/workspace/search", params={"q": "%"}).json()["total"] == 1
    assert client.get("/api/workspace/search", params={"q": "_"}).json()["total"] == 0
    page = client.get("/api/workspace/search", params={"q": "uat", "limit": 2}).json()
    next_page = client.get("/api/workspace/search", params={"q": "uat", "limit": 2, "offset": 2}).json()
    assert page["has_more"] and not next_page["has_more"]
    assert set(row["id"] for row in page["items"]).isdisjoint(row["id"] for row in next_page["items"])
    filtered = client.get("/api/workspace/search", params={"q": "uat", "kind": "action"}).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["path"] == f"/actions/{action.id}"


def test_search_does_not_expose_another_users_sources(client, db_session, seeded_user):
    _login(client, seeded_user)
    user = seeded_user["user"]
    other = User(organization_id=user.organization_id, email="other-workspace@example.com",
        full_name="Other", password_hash="unused", role="MEMBER")
    db_session.add(other); db_session.flush()
    db_session.add(SourceItem(organization_id=user.organization_id, created_by=other.id,
        source_type=SourceType.MANUAL, title="Private marker", content="secret marker"))
    db_session.add(DocumentAsset(organization_id=user.organization_id, uploaded_by=other.id,
        filename="marker.txt", mime_type="text/plain", storage_key="test2", checksum="def"))
    db_session.flush()
    assert client.get("/api/workspace/search", params={"q": "marker"}).json()["total"] == 0


def test_workspace_requires_login(client):
    for path in ["/api/workspace/search", "/api/workspace/summary", "/api/workspace/calendar", "/api/workspace/activity"]:
        assert client.get(path).status_code == 401


def test_calendar_sync_is_read_only_idempotent_and_searchable(cwi_client, fake_calendar, db_session, seeded_user):
    _login(cwi_client, seeded_user)
    connection_id = _connect_calendar(cwi_client)
    fake_calendar.source_events = [{"id": "event-1", "summary": "UAT review", "description": "Bring checklist",
        "start": {"date": "2026-09-29"}, "end": {"date": "2026-09-30"}}]
    for _ in range(2):
        response = cwi_client.post(f"/api/calendar/{connection_id}/sync-now")
        assert response.status_code == 200, response.text
        assert response.json()["events_synced"] == 1
    rows = db_session.scalars(select(CalendarSource)).all()
    assert len(rows) == 1
    record_id = rows[0].id
    assert fake_calendar.create_calls == fake_calendar.update_calls == fake_calendar.delete_calls == 0
    results = cwi_client.get("/api/workspace/search", params={"q": "checklist"}).json()
    assert results["items"][0]["path"] == f"/calendar-sources/{record_id}"
    fake_calendar.source_events[0]["summary"] = "Moved UAT"
    cwi_client.post(f"/api/calendar/{connection_id}/sync-now")
    assert cwi_client.get(f"/api/workspace/calendar/{record_id}").json()["title"] == "Moved UAT"
    fake_calendar.source_events = []
    cwi_client.post(f"/api/calendar/{connection_id}/sync-now")
    assert cwi_client.get("/api/workspace/calendar").json() == []


def test_calendar_failure_preserves_snapshot_and_success_time(cwi_client, fake_calendar, db_session, seeded_user, monkeypatch):
    _login(cwi_client, seeded_user)
    connection_id = _connect_calendar(cwi_client)
    fake_calendar.source_events = [{"id": "original", "summary": "Keep me"}]
    assert cwi_client.post(f"/api/calendar/{connection_id}/sync-now").status_code == 200
    connection = db_session.get(IntegrationConnection, UUID(connection_id))
    last_success = connection.last_sync_at
    def fail(**kwargs):
        raise CalendarClientError("offline")
    monkeypatch.setattr(fake_calendar, "list_events", fail)
    assert cwi_client.post(f"/api/calendar/{connection_id}/sync-now").status_code == 502
    assert connection.last_sync_at == last_success
    assert connection.last_error
    assert len(cwi_client.get("/api/workspace/calendar").json()) == 1
    assert cwi_client.post(f"/api/calendar/{uuid4()}/sync-now").status_code == 404


def test_disconnected_calendar_does_not_read_provider(cwi_client, fake_calendar, seeded_user, monkeypatch):
    _login(cwi_client, seeded_user)
    connection_id = _connect_calendar(cwi_client)
    cwi_client.post(f"/api/integrations/{connection_id}/disconnect")
    def unexpected(**kwargs):
        raise AssertionError("Disconnected calendar was read")
    monkeypatch.setattr(fake_calendar, "list_events", unexpected)
    assert cwi_client.post(f"/api/calendar/{connection_id}/sync-now").status_code == 403


def test_calendar_reader_follows_pages_and_only_uses_get():
    import httpx
    from app.config import Settings
    seen = []
    def transport(request):
        assert request.method == "GET"
        assert request.url.path.endswith("/calendars/primary/events")
        seen.append(request.url.params.get("pageToken"))
        if len(seen) == 1:
            return httpx.Response(200, json={"items": [{"id": "first"}], "nextPageToken": "page-2"})
        return httpx.Response(200, json={"items": [{"id": "second"}]})
    calendar = HttpCalendarClient(Settings(), transport=httpx.MockTransport(transport))
    assert [item["id"] for item in calendar.list_events(access_token="test")] == ["first", "second"]
    assert seen == [None, "page-2"]


def test_calendar_reader_does_not_return_partial_snapshot_after_page_failure():
    import httpx
    import pytest
    from app.config import Settings
    def transport(request):
        if not request.url.params.get("pageToken"):
            return httpx.Response(200, json={"items": [{"id": "first"}], "nextPageToken": "page-2"})
        return httpx.Response(503)
    calendar = HttpCalendarClient(Settings(), transport=httpx.MockTransport(transport))
    with pytest.raises(CalendarClientError):
        calendar.list_events(access_token="test")


def test_review_counts_match_review_links_and_only_count_pending_ai_suggestions(client, db_session, seeded_user):
    _login(client, seeded_user)
    user = seeded_user["user"]
    other = User(organization_id=user.organization_id, email="review-other@example.com", full_name="Other", password_hash="unused", role="MEMBER")
    db_session.add(other); db_session.flush()
    sources = []
    for name, state, owner, suggestion in [
        ("raw only", "NEW", user.id, None),
        ("review this", "NEW", user.id, "SUGGESTED"),
        ("already confirmed", "CLASSIFIED", user.id, "CONFIRMED"),
        ("dismissed", "DISMISSED", user.id, "SUGGESTED"),
        ("someone else's", "NEW", other.id, "SUGGESTED"),
    ]:
        source = SourceItem(organization_id=user.organization_id, created_by=owner, source_type=SourceType.MANUAL, title=name, content=name, status=state)
        db_session.add(source); db_session.flush(); sources.append(source)
        if suggestion:
            db_session.add(ClassificationResult(source_item_id=source.id, relevance="WORK_RELATED", business_category="PROJECT", sensitivity="INTERNAL", confidence=0.9, status=suggestion))
    for state in ["SUGGESTED", "CONFIRMED", "REJECTED"]:
        db_session.add(KnowledgeItem(organization_id=user.organization_id, summary=state, evidence_text="evidence", knowledge_type="FACT", status=state))
    db_session.flush()
    result = client.get("/api/workspace/summary")
    assert result.status_code == 200, result.text
    assert result.json()["source_reviews"] == 1
    assert result.json()["knowledge_reviews"] == 1
    assert result.json()["knowledge"] == 1
    items = client.get("/api/source-items", params={"review_pending": "true"}).json()
    assert [item["title"] for item in items] == ["review this"]
    client.post(f"/api/source-items/{sources[1].id}/reject-classification")
    assert client.get("/api/workspace/summary").json()["source_reviews"] == 0
    assert client.get("/api/source-items", params={"review_pending": "true"}).json() == []


def test_activity_records_local_edits_and_excludes_other_actors_and_raw_details(client, db_session, seeded_user):
    _login(client, seeded_user)
    user = seeded_user["user"]
    source = client.post("/api/source-items", json={"source_type": "MANUAL", "title": "My source", "content": "Source content"}).json()
    action = client.post("/api/actions", json={"title": "My action"}).json()
    response = client.patch(f"/api/actions/{action['id']}", json={"status": "DONE"})
    assert response.status_code == 200, response.text
    # A repeated/no-op save must not manufacture another recent change.
    client.patch(f"/api/actions/{action['id']}", json={"status": "DONE"})
    other = User(organization_id=user.organization_id, email="activity-other@example.com", full_name="Other", password_hash="unused", role="MEMBER")
    db_session.add(other); db_session.flush()
    db_session.add(AuditLog(organization_id=user.organization_id, actor_id=other.id, action_type="CREATE_ACTION", target_type="ActionItem", target_id=UUID(action["id"]), detail={"private": "never disclose"}))
    db_session.flush()
    response = client.get("/api/workspace/activity")
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["items"]) == 3
    assert [item["label"] for item in data["items"]].count("Action updated") == 1
    assert {item["path"] for item in data["items"]} == {f"/actions/{action['id']}", f"/source-inbox/{source['id']}"}
    assert "never disclose" not in response.text
    first = client.get("/api/workspace/activity", params={"limit": 2}).json()
    second = client.get("/api/workspace/activity", params={"limit": 2, "offset": 2}).json()
    assert first["has_more"] and not second["has_more"]
    assert not {item["id"] for item in first["items"]} & {item["id"] for item in second["items"]}
    client.delete(f"/api/actions/{action['id']}")
    deleted = client.get("/api/workspace/activity").json()["items"]
    assert all(item["path"] is None for item in deleted if item["label"].startswith("Action"))


def test_unchanged_calendar_sync_does_not_appear_as_a_new_change(cwi_client, fake_calendar, db_session, seeded_user):
    from datetime import datetime, timezone
    _login(cwi_client, seeded_user)
    connection_id = _connect_calendar(cwi_client)
    fake_calendar.source_events = [{"id": "stable-event", "summary": "Same event", "start": {"date": "2026-09-29"}}]
    cwi_client.post(f"/api/calendar/{connection_id}/sync-now")
    row = db_session.scalar(select(CalendarSource))
    original = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row.updated_at = original
    db_session.flush()
    cwi_client.post(f"/api/calendar/{connection_id}/sync-now")
    assert row.updated_at == original
    fake_calendar.source_events[0]["summary"] = "Actually changed"
    cwi_client.post(f"/api/calendar/{connection_id}/sync-now")
    assert row.updated_at > original
