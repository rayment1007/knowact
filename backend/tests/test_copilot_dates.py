from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.core.models import ActionItem, User
from app.core.services.ai_provider import CopilotAnswerOutput
from app.modules.cwi.models import CalendarSource, IntegrationConnection
from app.modules.cwi.services import copilot_service as module
from tests.test_cwi_copilot_routes import cwi_client, _login, _seed_confirmed_knowledge


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 9, 30, 17, 0, tzinfo=timezone.utc).astimezone(tz)


def test_today_uses_user_day_and_real_due_dates_without_promoting_old_email(cwi_client, db_session, seeded_user, monkeypatch):
    monkeypatch.setattr(module, "datetime", FixedDatetime)
    _login(cwi_client, seeded_user)
    org = seeded_user["organization"].id
    _seed_confirmed_knowledge(db_session, org, "A meeting was planned for September 24. Follow up this Sunday.")
    for title, due, status in [("Overdue task", date(2026, 9, 30), "OPEN"),
            ("Due today task", date(2026, 10, 1), "IN_PROGRESS"), ("Future task", date(2026, 10, 2), "OPEN"),
            ("Undated task", None, "OPEN"), ("Completed task", date(2026, 10, 1), "DONE")]:
        db_session.add(ActionItem(organization_id=org, title=title, due_date=due, status=status))
    db_session.flush()
    def no_rag(*args):
        raise AssertionError("An exact date-list request must not rely on the LLM to infer dates.")
    monkeypatch.setattr(module.CopilotService, "_gather_evidence", no_rag)
    response = cwi_client.post("/api/copilot/ask", json={"question": "what need my attention today", "utc_offset_minutes": 480})
    assert response.status_code == 200, response.text
    body = response.json(); answer = body["answer"]
    assert "01 Oct 2026 (UTC+08:00)" in answer
    assert "Overdue tasks: 1" in answer and "Due today: 1" in answer
    assert "1 unfinished action(s) have no due date" in answer
    assert "Future task" not in answer and "Completed task" not in answer and "this Sunday" not in answer
    assert {c["title"] for c in body["citations"]} == {"Overdue task", "Due today task"}
    assert all(c["source_id"] not in answer for c in body["citations"])
    assert body["suggested_artifact"] is None
    # With UTC as the default, the very same instant is still September 30.
    response = cwi_client.post("/api/copilot/ask", json={"question": "what needs my attention today"})
    assert "30 Sep 2026 (UTC+00:00)" in response.json()["answer"]
    response = cwi_client.post("/api/copilot/ask", json={"question": "今天有什么任务？", "utc_offset_minutes": 480})
    assert "01 Oct 2026 (UTC+08:00)" in response.json()["answer"]


def test_calendar_today_uses_actual_event_times_and_owner_scope(cwi_client, db_session, seeded_user, monkeypatch):
    monkeypatch.setattr(module, "datetime", FixedDatetime)
    _login(cwi_client, seeded_user); user = seeded_user["user"]
    other = User(organization_id=user.organization_id, email="other-calendar@example.com", full_name="Other", role="MEMBER", password_hash="test")
    db_session.add(other); db_session.flush()
    for owner in [user, other]:
        conn = IntegrationConnection(organization_id=user.organization_id, user_id=owner.id, provider="GOOGLE",
            service="GOOGLE_CALENDAR", external_account_id=str(owner.id), account_email=owner.email,
            access_token_encrypted=b"test", status="CONNECTED")
        db_session.add(conn); db_session.flush()
        for title, start, end in [("Cross-zone today", "2026-09-30T19:00:00Z", "2026-09-30T20:00:00Z"),
                ("Old meeting", "2026-09-24T14:00:00+08:00", "2026-09-24T15:00:00+08:00"),
                ("Yesterday all-day", "2026-09-30", "2026-10-01"),
                ("Tomorrow event", "2026-10-02", "2026-10-03")]:
            db_session.add(CalendarSource(organization_id=user.organization_id, integration_connection_id=conn.id,
                google_event_id=title, title=title if owner == user else "Private " + title,
                starts_at=start, ends_at=end, description="Calendar source"))
    db_session.flush()
    body = cwi_client.post("/api/copilot/ask", json={"question": "What needs my attention today?", "utc_offset_minutes": 480}).json()
    assert "Cross-zone today" in body["answer"]
    assert "01 Oct, 03:00" in body["answer"]
    assert all(title not in body["answer"] for title in ["Old meeting", "Yesterday all-day", "Tomorrow event", "Private"])
    assert body["citations"][0]["deep_link"].startswith("/workspace/sources?item=calendar:")


def test_general_answer_receives_clock_and_action_metadata_and_hides_ids(cwi_client, db_session, seeded_user, monkeypatch):
    monkeypatch.setattr(module, "datetime", FixedDatetime)
    _login(cwi_client, seeded_user)
    action = ActionItem(organization_id=seeded_user["organization"].id, title="Review draft", status="IN_PROGRESS", due_date=date(2026, 9, 24))
    db_session.add(action); db_session.flush()
    def answer(context):
        assert context.current_datetime.isoformat().startswith("2026-10-01T01:00:00+08:00")
        found = next(e for e in context.evidence if e.source_id == action.id)
        assert found.status == "IN_PROGRESS" and found.due_date == date(2026, 9, 24)
        return CopilotAnswerOutput(answer=f"Review draft is overdue (id={action.id}).", cited_source_ids=[action.id])
    monkeypatch.setattr(module, "call_with_fallback", lambda provider, settings, operation: operation(SimpleNamespace(answer_copilot_query=answer)))
    body = cwi_client.post("/api/copilot/ask", json={"question": "Tell me about the review draft", "utc_offset_minutes": 480}).json()
    assert body["answer"] == "Review draft is overdue [1]."
    assert body["citations"][0]["source_id"] == str(action.id)


def test_no_valid_citation_cannot_be_presented_as_a_grounded_answer(cwi_client, db_session, seeded_user, monkeypatch):
    _login(cwi_client, seeded_user); _seed_confirmed_knowledge(db_session, seeded_user["organization"].id)
    monkeypatch.setattr(module, "call_with_fallback", lambda *args: CopilotAnswerOutput(answer="Unsupported claim", cited_source_ids=[uuid4()]))
    body = cwi_client.post("/api/copilot/ask", json={"question": "What changed?"}).json()
    assert body["insufficient_evidence"] and "Unsupported claim" not in body["answer"]


def test_offset_is_bounded(cwi_client, seeded_user):
    _login(cwi_client, seeded_user)
    assert cwi_client.post("/api/copilot/ask", json={"question": "Today?", "utc_offset_minutes": 9999}).status_code == 422
