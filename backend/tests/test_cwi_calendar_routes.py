"""Route tests for the CWI Calendar endpoints (M6.3, Requirement 28).

Exercises the real FastAPI app against the ephemeral SQLite database with a FAKE
Calendar transport and FAKE Google OAuth transport injected (no network call
ever occurs) and a test Fernet key configured so the TokenVault can decrypt the
connection's token. Covers listing writable calendars, adding a confirmed
action (idempotently), rejecting a non-confirmed action, update/cancel/retry,
the Daily Brief block, token-safety, auth, and cross-org 404 isolation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.core import models as core_models
from app.core.models import ActionItem, ActionStatus
from app.modules.cwi.dependencies import calendar_client, google_oauth_client
from app.modules.cwi.services.calendar_client import CalendarClientError, FakeCalendarClient
from app.modules.cwi.services.google_oauth import (
    CALENDAR_CALENDARLIST_READONLY_SCOPE,
    CALENDAR_EVENTS_SCOPE,
    FakeGoogleOAuthClient,
    GoogleTokenGrant,
)

_TEST_KEY = Fernet.generate_key().decode()
_FORBIDDEN_FIELDS = ("access_token_encrypted", "refresh_token_encrypted")


@pytest.fixture()
def fake_calendar() -> FakeCalendarClient:
    return FakeCalendarClient()


def _calendar_grant() -> GoogleTokenGrant:
    return GoogleTokenGrant(
        access_token="access-calendar",
        refresh_token="refresh-calendar",
        expires_at=datetime.now(timezone.utc).replace(year=2999),
        granted_scopes=[
            CALENDAR_EVENTS_SCOPE,
            CALENDAR_CALENDARLIST_READONLY_SCOPE,
        ],
        external_account_id="google-sub-calendar",
        account_email="calendar@example.com",
    )


@pytest.fixture()
def cwi_client(
    client: TestClient,
    fake_calendar: FakeCalendarClient,
) -> TestClient:
    test_settings = Settings(token_encryption_key=_TEST_KEY, ai_provider="mock")
    client.app.dependency_overrides[get_settings] = lambda: test_settings
    client.app.dependency_overrides[google_oauth_client] = (
        lambda: FakeGoogleOAuthClient(grant=_calendar_grant())
    )
    client.app.dependency_overrides[calendar_client] = lambda: fake_calendar
    return client


def _login(client: TestClient, seeded_user: dict[str, Any]) -> None:
    resp = client.post(
        "/api/auth/login",
        json={"email": seeded_user["email"], "password": seeded_user["password"]},
    )
    assert resp.status_code == 200


def _connect_calendar(client: TestClient) -> str:
    begin = client.post("/api/integrations/GOOGLE_CALENDAR/connect")
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


def _make_action(
    db_session: Any,
    org_id: Any,
    *,
    status: ActionStatus = ActionStatus.OPEN,
    title: str = "Follow up with client",
) -> ActionItem:
    action = ActionItem(
        organization_id=org_id,
        title=title,
        description="Details",
        status=status,
        ai_generated=False,
    )
    db_session.add(action)
    db_session.flush()
    return action


def test_disconnected_calendar_connection_is_rejected_before_external_call(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    fake_calendar: FakeCalendarClient,
    monkeypatch: Any,
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_calendar(cwi_client)
    assert cwi_client.post(
        f"/api/integrations/{connection_id}/disconnect"
    ).status_code == 200

    def _unexpected_call(*, access_token: str) -> list[Any]:
        raise AssertionError("Calendar transport must not be called")

    monkeypatch.setattr(fake_calendar, "list_calendars", _unexpected_call)
    response = cwi_client.get(f"/api/calendar/{connection_id}/calendars")

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# List writable calendars
# ---------------------------------------------------------------------------


def test_list_writable_calendars(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_calendar(cwi_client)

    resp = cwi_client.get(f"/api/calendar/{connection_id}/calendars")
    assert resp.status_code == 200
    calendars = resp.json()
    ids = {c["calendar_id"] for c in calendars}
    assert "primary" in ids
    # Only writable (owner/writer) calendars are surfaced.
    assert all(c["access_role"] in ("owner", "writer") for c in calendars)


# ---------------------------------------------------------------------------
# Add confirmed action + idempotency
# ---------------------------------------------------------------------------


def test_add_confirmed_action_creates_link_and_audit(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_calendar(cwi_client)
    action = _make_action(db_session, seeded_user["organization"].id)

    payload = {"connection_id": connection_id, "google_calendar_id": "primary", "all_day": True, "all_day_date": "2026-10-09"}
    resp = cwi_client.post(
        f"/api/actions/{action.id}/add-to-calendar", json=payload
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sync_status"] == "SYNCED"
    assert body["google_event_id"]
    assert body["action_item_id"] == str(action.id)
    # No token ever surfaces.
    for field in _FORBIDDEN_FIELDS:
        assert field not in resp.text

    audits = (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == "ADD_CALENDAR_EVENT")
        .all()
    )
    assert len(audits) == 1


def test_add_confirmed_action_is_idempotent(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
    fake_calendar: FakeCalendarClient,
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_calendar(cwi_client)
    action = _make_action(db_session, seeded_user["organization"].id)

    payload = {"connection_id": connection_id, "google_calendar_id": "primary", "all_day": True, "all_day_date": "2026-10-09"}
    first = cwi_client.post(
        f"/api/actions/{action.id}/add-to-calendar", json=payload
    )
    second = cwi_client.post(
        f"/api/actions/{action.id}/add-to-calendar", json=payload
    )
    assert first.status_code == second.status_code == 200
    # Same link, same Google event id; no duplicate created.
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["google_event_id"] == second.json()["google_event_id"]
    assert fake_calendar.create_calls == 1

    from app.modules.cwi.models import CalendarEventLink

    links = db_session.query(CalendarEventLink).all()
    assert len(links) == 1
    # Exactly one audit row despite two add requests.
    audits = (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == "ADD_CALENDAR_EVENT")
        .all()
    )
    assert len(audits) == 1


def test_add_non_confirmed_action_rejected(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_calendar(cwi_client)
    # A withdrawn (CANCELLED) action is treated as not confirmed (Req 28.2).
    action = _make_action(
        db_session, seeded_user["organization"].id, status=ActionStatus.CANCELLED
    )

    payload = {"connection_id": connection_id, "google_calendar_id": "primary", "all_day": True, "all_day_date": "2026-10-09"}
    resp = cwi_client.post(
        f"/api/actions/{action.id}/add-to-calendar", json=payload
    )
    assert resp.status_code == 409

    from app.modules.cwi.models import CalendarEventLink

    assert db_session.query(CalendarEventLink).count() == 0


# ---------------------------------------------------------------------------
# Update / cancel / retry
# ---------------------------------------------------------------------------


def test_update_and_cancel_event(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_calendar(cwi_client)
    action = _make_action(db_session, seeded_user["organization"].id)

    payload = {"connection_id": connection_id, "google_calendar_id": "primary", "all_day": True, "all_day_date": "2026-10-09"}
    link_id = cwi_client.post(
        f"/api/actions/{action.id}/add-to-calendar", json=payload
    ).json()["id"]

    updated = cwi_client.patch(
        f"/api/calendar/links/{link_id}",
        json={"summary": "Rescheduled follow up"},
    )
    assert updated.status_code == 200
    assert updated.json()["sync_status"] == "SYNCED"

    cancelled = cwi_client.post(f"/api/calendar/links/{link_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["sync_status"] == "CANCELLED"

    for action_type in ("UPDATE_CALENDAR_EVENT", "CANCEL_CALENDAR_EVENT"):
        audits = (
            db_session.query(core_models.AuditLog)
            .filter(core_models.AuditLog.action_type == action_type)
            .all()
        )
        assert len(audits) == 1


def test_retry_after_failed_create(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
    fake_calendar: FakeCalendarClient,
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_calendar(cwi_client)
    action = _make_action(db_session, seeded_user["organization"].id)

    # First create fails at the transport; the link is persisted as FAILED.
    fake_calendar.fail_next_create = True
    payload = {"connection_id": connection_id, "google_calendar_id": "primary", "all_day": True, "all_day_date": "2026-10-09"}
    failed = cwi_client.post(
        f"/api/actions/{action.id}/add-to-calendar", json=payload
    )
    assert failed.status_code == 200
    assert failed.json()["sync_status"] == "FAILED"
    assert failed.json()["google_event_id"] is None
    link_id = failed.json()["id"]

    # No audit for the failed create.
    assert (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == "ADD_CALENDAR_EVENT")
        .count()
        == 0
    )

    # Retry now succeeds and records exactly one audit row.
    retried = cwi_client.post(f"/api/calendar/links/{link_id}/retry")
    assert retried.status_code == 200
    assert retried.json()["sync_status"] == "SYNCED"
    assert retried.json()["google_event_id"]
    assert (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == "ADD_CALENDAR_EVENT")
        .count()
        == 1
    )


# ---------------------------------------------------------------------------
# Daily Brief block (Requirement 28.9)
# ---------------------------------------------------------------------------


def test_daily_brief_block_no_sensitive_content(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_calendar(cwi_client)

    payload = {
        "connection_id": connection_id,
        "google_calendar_id": "primary", "all_day": True, "all_day_date": "2026-10-09",
        "deep_link": "https://app.example.com/dashboard",
        "label": "Daily Brief",
    }
    first = cwi_client.post("/api/calendar/daily-brief-block", json=payload)
    assert first.status_code == 200
    assert first.json()["sync_status"] == "SYNCED"
    assert first.json()["action_item_id"] is None

    # Idempotent: a second request returns the same block.
    second = cwi_client.post("/api/calendar/daily-brief-block", json=payload)
    assert second.json()["id"] == first.json()["id"]

    from app.modules.cwi.models import CalendarEventLink

    assert db_session.query(CalendarEventLink).count() == 1


# ---------------------------------------------------------------------------
# Auth + cross-org isolation
# ---------------------------------------------------------------------------


def test_calendar_requires_authentication(cwi_client: TestClient) -> None:
    assert cwi_client.get("/api/calendar/links").status_code == 401


def test_cross_org_link_returns_404(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    connection_id = _connect_calendar(cwi_client)
    action = _make_action(db_session, seeded_user["organization"].id)
    payload = {"connection_id": connection_id, "google_calendar_id": "primary", "all_day": True, "all_day_date": "2026-10-09"}
    link_id = cwi_client.post(
        f"/api/actions/{action.id}/add-to-calendar", json=payload
    ).json()["id"]

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

    # The other org cannot see/act on the first org's link.
    assert cwi_client.post(
        f"/api/calendar/links/{link_id}/cancel"
    ).status_code == 404
    assert cwi_client.patch(
        f"/api/calendar/links/{link_id}", json={"summary": "x"}
    ).status_code == 404
