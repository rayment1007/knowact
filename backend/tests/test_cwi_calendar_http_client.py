"""Unit tests for the production :class:`HttpCalendarClient`.

These tests exercise the REAL client but perform NO network I/O: every HTTP
call is served by an in-memory ``httpx.MockTransport`` injected via the
``transport`` param (mirroring the OAuth/Gmail HTTP client tests). They verify:

* ``list_calendars`` returns only writable (owner/writer) calendars mapped into
  :class:`CalendarInfo`.
* ``create_event`` builds the correct body for timed, all-day, reminder, and
  recurring events and returns the created event id.
* ``update_event`` PATCHes and ``delete_event`` DELETEs the right endpoint,
  treating 404/410 as already-deleted success.
* Any HTTP failure is surfaced as a secret-free :class:`CalendarClientError`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from app.config import Settings
from app.modules.cwi.services.calendar_client import (
    CalendarClientError,
    CalendarEventInput,
    HttpCalendarClient,
)

_ACCESS_TOKEN = "ya29.super-secret-calendar-token"

_SETTINGS = Settings(
    google_oauth_client_id="cid.apps.googleusercontent.com",
    google_oauth_client_secret="secret",
)


def _client(handler) -> HttpCalendarClient:
    return HttpCalendarClient(_SETTINGS, transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# list_calendars
# ---------------------------------------------------------------------------


def test_list_calendars_returns_only_writable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/users/me/calendarList")
        assert request.headers["Authorization"] == f"Bearer {_ACCESS_TOKEN}"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "primary",
                        "summary": "Primary",
                        "primary": True,
                        "accessRole": "owner",
                    },
                    {
                        "id": "team@group.calendar.google.com",
                        "summary": "Team",
                        "accessRole": "writer",
                    },
                    {
                        "id": "readonly@group.calendar.google.com",
                        "summary": "Holidays",
                        "accessRole": "reader",
                    },
                ]
            },
        )

    client = _client(handler)
    calendars = client.list_calendars(access_token=_ACCESS_TOKEN)

    ids = [c.calendar_id for c in calendars]
    assert ids == ["primary", "team@group.calendar.google.com"]
    assert calendars[0].primary is True
    assert calendars[0].access_role == "owner"
    assert calendars[1].summary == "Team"


def test_list_calendars_http_error_raises_secret_free_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _client(handler)
    with pytest.raises(CalendarClientError) as exc_info:
        client.list_calendars(access_token=_ACCESS_TOKEN)
    assert _ACCESS_TOKEN not in str(exc_info.value)


# ---------------------------------------------------------------------------
# create_event
# ---------------------------------------------------------------------------


def test_create_timed_event_with_reminders() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/calendars/primary/events")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "evt-1"})

    start = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    end = datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc)
    client = _client(handler)
    event_id = client.create_event(
        access_token=_ACCESS_TOKEN,
        calendar_id="primary",
        event=CalendarEventInput(
            summary="Meeting",
            description="Sync",
            start=start,
            end=end,
            reminder_minutes=[10, 30],
        ),
        idempotency_key="k1",
    )

    assert event_id == "evt-1"
    body = captured["body"]
    assert body["summary"] == "Meeting"
    assert body["id"]
    assert set(body["id"]) <= set("0123456789abcdefghijklmnopqrstuv")
    assert body["description"] == "Sync"
    assert body["start"]["dateTime"] == start.isoformat()
    assert body["end"]["dateTime"] == end.isoformat()
    assert body["reminders"]["useDefault"] is False
    assert body["reminders"]["overrides"] == [
        {"method": "popup", "minutes": 10},
        {"method": "popup", "minutes": 30},
    ]


def test_create_all_day_event_uses_date_fields() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "evt-allday"})

    client = _client(handler)
    event_id = client.create_event(
        access_token=_ACCESS_TOKEN,
        calendar_id="primary",
        event=CalendarEventInput(
            summary="Due date",
            all_day=True,
            all_day_date="2025-03-15",
        ),
        idempotency_key="k2",
    )

    assert event_id == "evt-allday"
    body = captured["body"]
    assert body["start"] == {"date": "2025-03-15"}
    assert body["end"] == {"date": "2025-03-16"}
    assert "dateTime" not in json.dumps(body)


def test_create_recurring_event_includes_recurrence() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "evt-rec"})

    client = _client(handler)
    client.create_event(
        access_token=_ACCESS_TOKEN,
        calendar_id="primary",
        event=CalendarEventInput(
            summary="Daily Brief",
            start=datetime(2025, 1, 1, 8, 0, tzinfo=timezone.utc),
            recurrence="RRULE:FREQ=DAILY",
        ),
        idempotency_key="k3",
    )

    assert captured["body"]["recurrence"] == ["RRULE:FREQ=DAILY"]


def test_create_event_http_error_raises_secret_free_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad"})

    client = _client(handler)
    with pytest.raises(CalendarClientError) as exc_info:
        client.create_event(
            access_token=_ACCESS_TOKEN,
            calendar_id="primary",
            event=CalendarEventInput(summary="X"),
            idempotency_key="k",
        )
    assert _ACCESS_TOKEN not in str(exc_info.value)


def test_create_event_reconciles_duplicate_idempotency_key() -> None:
    captured_ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            event_id = json.loads(request.content)["id"]
            captured_ids.append(event_id)
            return httpx.Response(409, json={"error": "duplicate"})
        assert request.method == "GET"
        assert request.url.path.endswith(f"/events/{captured_ids[0]}")
        return httpx.Response(200, json={"id": captured_ids[0]})

    client = _client(handler)
    event_id = client.create_event(
        access_token=_ACCESS_TOKEN,
        calendar_id="primary",
        event=CalendarEventInput(summary="Retry-safe event"),
        idempotency_key="stable-operation-key",
    )
    assert event_id == captured_ids[0]


# ---------------------------------------------------------------------------
# update_event / delete_event
# ---------------------------------------------------------------------------


def test_update_event_patches_endpoint() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert request.url.path.endswith("/calendars/primary/events/evt-9")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "evt-9"})

    client = _client(handler)
    client.update_event(
        access_token=_ACCESS_TOKEN,
        calendar_id="primary",
        event_id="evt-9",
        event=CalendarEventInput(summary="Renamed"),
    )
    assert captured["body"]["summary"] == "Renamed"


def test_delete_event_deletes_endpoint() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path.endswith("/calendars/primary/events/evt-9")
        captured["hit"] = True
        return httpx.Response(204)

    client = _client(handler)
    client.delete_event(
        access_token=_ACCESS_TOKEN, calendar_id="primary", event_id="evt-9"
    )
    assert captured["hit"] is True


def test_delete_event_treats_404_and_410_as_success() -> None:
    for code in (404, 410):
        def handler(request: httpx.Request, _code: int = code) -> httpx.Response:
            return httpx.Response(_code)

        client = _client(handler)
        # No exception raised: already-deleted is success.
        client.delete_event(
            access_token=_ACCESS_TOKEN,
            calendar_id="primary",
            event_id="gone",
        )


def test_delete_event_http_error_raises_secret_free_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _client(handler)
    with pytest.raises(CalendarClientError) as exc_info:
        client.delete_event(
            access_token=_ACCESS_TOKEN, calendar_id="primary", event_id="e"
        )
    assert _ACCESS_TOKEN not in str(exc_info.value)
