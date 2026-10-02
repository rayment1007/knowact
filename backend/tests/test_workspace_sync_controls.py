from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
import json
import httpx
from sqlalchemy import select

from app.config import Settings
from app.core.models import ActionItem, BusinessEntity, ClassificationResult, KnowledgeItem, SourceItem, User
from app.modules.cwi.models import CalendarSource, EmailDraft, EmailMessageRecord, EmailTaskSuggestion, IntegrationConnection, SyncExclusion
from app.modules.cwi.services.gmail_client import FakeGmailClient, GmailMessage, HttpGmailClient
from app.modules.cwi.services.gmail_sync_service import GmailSyncService
from app.modules.cwi.services.google_oauth import GMAIL_READONLY_SCOPE
from app.modules.cwi.services.calendar_client import HttpCalendarClient
from app.modules.cwi.dependencies import calendar_client
from tests.test_cwi_calendar_routes import cwi_client, fake_calendar, _login, _connect_calendar, _make_action


def connection(db, user, account="work@example.com"):
    row = IntegrationConnection(organization_id=user.organization_id, user_id=user.id,
        service="GMAIL", provider="GOOGLE", external_account_id=account, account_email=account,
        access_token_encrypted=b"test", status="CONNECTED", granted_scopes_json=[GMAIL_READONLY_SCOPE])
    db.add(row); db.flush()
    return row


def message(key, days=0):
    return GmailMessage(gmail_message_id=key, gmail_thread_id=key, sender="sender@example.com",
        subject=f"Please follow up {key}", body_text=f"Please send the project update {key}",
        received_at=datetime.now(timezone.utc) - timedelta(days=days), labels=["INBOX"])


def test_default_and_custom_window_deletion_resync_restore(client, db_session, seeded_user):
    _login(client, seeded_user); user = seeded_user["user"]
    assert client.get("/api/workspace/sync-preferences").json() == {"email_days": 7, "calendar_past_days": 7, "calendar_future_days": 90}
    conn = connection(db_session, user)
    gmail = FakeGmailClient([message("recent", 2), message("older", 10)])
    service = GmailSyncService(db_session, gmail)
    assert service.sync_now(user.organization_id, user.id, conn.id).records_created == 1
    record = db_session.scalar(select(EmailMessageRecord))
    source_id, record_id = record.source_item_id, record.id
    kept = KnowledgeItem(organization_id=user.organization_id, source_item_id=source_id, knowledge_type="FACT", summary="Reviewed fact", evidence_text="Evidence", status="CONFIRMED")
    db_session.add(kept); db_session.flush()
    assert client.delete(f"/api/gmail/messages/{record_id}").status_code == 204
    db_session.expire_all()
    assert db_session.get(SourceItem, source_id) is None
    assert db_session.get(KnowledgeItem, kept.id).source_item_id is None
    assert service.sync_now(user.organization_id, user.id, conn.id).records_created == 0
    assert client.get("/api/workspace/sync-exclusions").json() == {"count": 1}
    changed = client.put("/api/workspace/sync-preferences", json={"email_days": 30, "calendar_past_days": 3, "calendar_future_days": 120})
    assert changed.status_code == 200
    assert service.sync_now(user.organization_id, user.id, conn.id).records_created == 1  # older only
    assert client.delete("/api/workspace/sync-exclusions").status_code == 200
    assert service.sync_now(user.organization_id, user.id, conn.id).records_created == 1  # restored recent
    assert client.put("/api/workspace/sync-preferences", json={"email_days": 0}).status_code == 422
    assert client.put("/api/workspace/sync-preferences", json={"email_days": 366}).status_code == 422


def test_exclusions_and_settings_are_owner_scoped(client, db_session, seeded_user):
    _login(client, seeded_user); user = seeded_user["user"]
    other = User(organization_id=user.organization_id, email="sync-other@example.com", full_name="Other", password_hash="unused", role="MEMBER")
    db_session.add(other); db_session.flush()
    conn = connection(db_session, other)
    from app.modules.cwi.services.sync_preferences import exclude_email, preferences
    exclude_email(db_session, conn, "private")
    client.put("/api/workspace/sync-preferences", json={"email_days": 60})
    assert preferences(db_session, user.organization_id, other.id).email_days == 7
    assert client.get("/api/workspace/sync-exclusions").json()["count"] == 0
    client.delete("/api/workspace/sync-exclusions")
    assert db_session.scalar(select(SyncExclusion)) is not None


def test_sent_in_knowact_sync_does_not_create_ai_reviews(client, db_session, seeded_user):
    _login(client, seeded_user); user = seeded_user["user"]
    conn = connection(db_session, user)
    draft = EmailDraft(organization_id=user.organization_id, user_id=user.id, integration_connection_id=conn.id,
        subject="Sent", body_text="Approved text", status="SENT", gmail_sent_message_id="sent-id", send_idempotency_key="sent-test")
    db_session.add(draft); db_session.flush()
    service = GmailSyncService(db_session, FakeGmailClient([message("sent-id")]))
    assert service.sync_now(user.organization_id, user.id, conn.id).records_created == 1
    assert service.sync_now(user.organization_id, user.id, conn.id).records_created == 0
    assert db_session.scalar(select(ClassificationResult)) is None
    assert db_session.scalar(select(EmailTaskSuggestion)) is None
    assert client.get("/api/workspace/items?source_type=email").json()["items"][0]["status"] == "CREATED_HERE"
    # Legacy suggestions on a recognized outbound source must not make the
    # dashboard disagree with the source-review list.
    record = db_session.scalar(select(EmailMessageRecord))
    db_session.add(ClassificationResult(source_item_id=record.source_item_id, relevance="WORK_RELATED",
        business_category="PROJECT", sensitivity="INTERNAL", confidence=.8, status="SUGGESTED"))
    db_session.flush()
    assert client.get("/api/workspace/summary").json()["source_reviews"] == 0


def test_all_day_real_http_retry_keeps_dates_and_sync_links_action(cwi_client, db_session, seeded_user, fake_calendar):
    _login(cwi_client, seeded_user); user = seeded_user["user"]
    conn_id = _connect_calendar(cwi_client)
    action = _make_action(db_session, user.organization_id)
    bodies = []
    def transport(request):
        if request.method == "GET":
            assert request.url.params["singleEvents"] == "true"
            assert request.url.params["timeMin"] and request.url.params["timeMax"]
            return httpx.Response(200, json={"items": [{**bodies[-1], "id": "event-confirmed"}]})
        bodies.append(json.loads(request.content))
        return httpx.Response(503 if len(bodies) == 1 else 200, json={"id": "event-confirmed"})
    real = HttpCalendarClient(Settings(), transport=httpx.MockTransport(transport))
    cwi_client.app.dependency_overrides[calendar_client] = lambda: real
    payload = {"connection_id": conn_id, "google_calendar_id": "primary", "summary": "My approved title", "all_day": True, "all_day_date": "2026-12-31"}
    result = cwi_client.post(f"/api/actions/{action.id}/add-to-calendar", json=payload)
    assert result.status_code == 200, result.text
    assert result.json()["sync_status"] == "FAILED"
    retry = cwi_client.post(f"/api/calendar/links/{result.json()['id']}/retry")
    assert retry.status_code == 200, retry.text
    assert bodies[0] == bodies[1]
    assert bodies[1]["start"] == {"date": "2026-12-31"}
    assert bodies[1]["end"] == {"date": "2027-01-01"}
    snapshot = db_session.scalar(select(CalendarSource))
    snapshot_id = snapshot.id
    assert snapshot.title == "My approved title"
    assert cwi_client.post(f"/api/calendar/{conn_id}/sync-now").status_code == 200
    assert [row.id for row in db_session.scalars(select(CalendarSource))] == [snapshot_id]
    linked = cwi_client.get(f"/api/workspace/source/calendar/{snapshot_id}/links").json()
    assert linked["items"][0]["id"] == str(action.id)
    assert cwi_client.get("/api/workspace/counts").json()["actions_reviews"] == 0


def test_invalid_calendar_times_make_no_external_call(cwi_client, db_session, seeded_user, fake_calendar):
    _login(cwi_client, seeded_user); user = seeded_user["user"]
    conn_id = _connect_calendar(cwi_client); action = _make_action(db_session, user.organization_id)
    for bad in [{"all_day": True}, {"start": "2026-10-02T14:00:00+08:00", "end": "2026-10-02T13:00:00+08:00"}]:
        response = cwi_client.post(f"/api/actions/{action.id}/add-to-calendar", json={"connection_id": conn_id, "google_calendar_id": "primary", **bad})
        assert response.status_code == 422
    assert fake_calendar.create_calls == 0


def test_token_key_mismatch_is_actionable_without_expiring_shared_connection(cwi_client, db_session, seeded_user, monkeypatch):
    from uuid import UUID
    from app.modules.cwi.services.token_vault import TokenVault, TokenVaultError
    _login(cwi_client, seeded_user)
    conn_id = _connect_calendar(cwi_client)
    def fail(*args):
        raise TokenVaultError("internal-secret-details")
    monkeypatch.setattr(TokenVault, "decrypt", fail)
    response = cwi_client.post(f"/api/calendar/{conn_id}/sync-now")
    assert response.status_code == 503
    assert "encryption configuration" in response.json()["detail"]
    assert "internal-secret-details" not in response.text
    db_session.expire_all()
    row = db_session.get(IntegrationConnection, UUID(conn_id))
    assert row.status == "CONNECTED" and row.last_error is None


def test_bounded_calendar_sync_keeps_outside_range_and_other_calendars(cwi_client, db_session, seeded_user, fake_calendar):
    _login(cwi_client, seeded_user); user = seeded_user["user"]
    from uuid import UUID
    conn_id = _connect_calendar(cwi_client)
    now = datetime.now(timezone.utc)
    for key, days, calendar in [("old", -40, "primary"), ("missing-current", 1, "primary"), ("other-calendar", 1, "other@example.com")]:
        db_session.add(CalendarSource(organization_id=user.organization_id, integration_connection_id=UUID(conn_id),
            google_event_id=key, google_calendar_id=calendar, title=key,
            starts_at=(now + timedelta(days=days)).isoformat(), ends_at=(now + timedelta(days=days, hours=1)).isoformat()))
    db_session.flush()
    assert cwi_client.post(f"/api/calendar/{conn_id}/sync-now").status_code == 200
    assert {row.google_event_id for row in db_session.scalars(select(CalendarSource))} == {"old", "other-calendar"}


def test_delete_project_unlinks_records_and_today_filter(client, db_session, seeded_user):
    _login(client, seeded_user); user = seeded_user["user"]
    entity = BusinessEntity(organization_id=user.organization_id, name="Project A", entity_type="PROJECT")
    db_session.add(entity); db_session.flush()
    knowledge = KnowledgeItem(organization_id=user.organization_id, business_entity_id=entity.id, knowledge_type="FACT", summary="Keep me", evidence_text="Evidence", status="CONFIRMED")
    today = ActionItem(organization_id=user.organization_id, business_entity_id=entity.id, title="Due today", due_date=date(2026, 10, 2), status="OPEN")
    tomorrow = ActionItem(organization_id=user.organization_id, business_entity_id=entity.id, title="Tomorrow", due_date=date(2026, 10, 3), status="OPEN")
    db_session.add_all([knowledge, today, tomorrow]); db_session.flush()
    assert client.delete(f"/api/business-entities/{entity.id}").status_code == 204
    db_session.expire_all()
    assert db_session.get(KnowledgeItem, knowledge.id).business_entity_id is None
    assert db_session.get(ActionItem, today.id).business_entity_id is None
    assert client.get("/api/workspace/items?section=actions&due=2026-10-02").json()["items"][0]["title"] == "Due today"
    assert client.get("/api/workspace/items?section=actions&due=2026-10-02").json()["total"] == 1
    assert client.delete(f"/api/business-entities/{uuid4()}").status_code == 404


def test_gmail_query_uses_epoch_and_follows_bounded_pages():
    calls = []
    after = datetime(2026, 10, 1, tzinfo=timezone(timedelta(hours=8)))
    def transport(request):
        calls.append(request)
        if request.url.path.endswith("/messages"):
            assert request.url.params["q"] == f"after:{int(after.timestamp())}"
            if request.url.params.get("pageToken"):
                return httpx.Response(200, json={"messages": [{"id": "second"}]})
            return httpx.Response(200, json={"messages": [{"id": "first"}], "nextPageToken": "next"})
        return httpx.Response(200, json={"id": request.url.path.rsplit("/", 1)[-1], "internalDate": str(int(after.timestamp()) * 1000)})
    gmail = HttpGmailClient(Settings(), transport=httpx.MockTransport(transport))
    assert {m.gmail_message_id for m in gmail.list_messages(access_token="test", after=after)} == {"first", "second"}
    assert len(calls) == 4
