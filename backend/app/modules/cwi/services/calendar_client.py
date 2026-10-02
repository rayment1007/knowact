"""Google Calendar transport abstraction (the injectable Calendar client).

Calendar writes are expressed behind the :class:`CalendarClient` protocol so
the rest of the CWI code never talks to Google Calendar directly. This is the
seam that keeps calendar event creation **default-safe and fully testable**,
exactly mirroring the Gmail/OAuth client pattern:

* Tests inject :class:`FakeCalendarClient`, which performs **no network I/O**
  whatsoever — it returns deterministic calendar and event ids. No CWI test
  ever makes a real Calendar call (Requirement 34).
* Production uses :class:`HttpCalendarClient`, a thin wrapper over Google
  Calendar's real HTTP endpoints, selected only when live Google credentials
  are present. Until the phase that first needs live Calendar traffic it raises
  a clear error rather than making a partial/unsafe request, so no accidental
  network call happens in local/mock runs.

Idempotency is a first-class part of the seam: :meth:`CalendarClient.create_event`
accepts an ``idempotency_key`` so a retried create resolves to the **same**
event id rather than a duplicate (Requirement 28.7 / Property 16). The
:class:`FakeCalendarClient` honors this deterministically.

The concrete client is resolved via :func:`get_calendar_client`, a FastAPI
dependency that routes override in tests with a pre-seeded fake.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Protocol, runtime_checkable

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


def _log_calendar_error(operation: str, exc: Exception) -> None:
    """Log the underlying Calendar API failure for diagnosis (secret-free).

    Provider response bodies can contain event or attendee data, so only a
    status code and exception class are recorded.
    """

    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        logger.error(
            "Calendar %s failed: HTTP %s",
            operation,
            exc.response.status_code,
        )
    else:
        logger.error("Calendar %s failed: %s", operation, type(exc).__name__)


#: Base URL for the Google Calendar API v3.
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"

#: Short, fixed timeout for every outbound Calendar call.
_HTTP_TIMEOUT_SECONDS = 15

#: Access roles that permit writing events to a calendar (Requirement 28.3).
_WRITABLE_ROLES = frozenset({"owner", "writer"})


def _event_id_for_idempotency_key(idempotency_key: str) -> str:
    """Map any internal key to Google's base32hex-compatible event id."""

    digest = hashlib.sha256(idempotency_key.encode("utf-8")).digest()
    return base64.b32hexencode(digest).decode("ascii").lower().rstrip("=")


@dataclass(frozen=True)
class CalendarInfo:
    """A single writable Google Calendar the user can add events to.

    Only calendars the user can write to (``accessRole`` owner/writer) are
    surfaced, since an event can only be created on a writable calendar
    (Requirement 28.3).
    """

    calendar_id: str
    summary: str
    primary: bool = False
    access_role: str = "writer"


@dataclass(frozen=True)
class CalendarEventInput:
    """The event payload handed to the transport for create/update.

    Deliberately minimal and provider-neutral: it carries only the fields the
    "Add to Google Calendar" flow supports — summary/description, start/end (or
    an all-day date for a due-date-only action), reminder minutes, and an
    optional recurrence rule for the recurring Daily Brief block (Requirement
    28.3, 28.9).
    """

    summary: str
    description: str = ""
    start: datetime | None = None
    end: datetime | None = None
    all_day: bool = False
    # All-day events carry a plain date (YYYY-MM-DD) rather than a timestamp.
    all_day_date: str | None = None
    reminder_minutes: list[int] = field(default_factory=list)
    # RFC 5545 RRULE (e.g. "RRULE:FREQ=DAILY") for the recurring brief block.
    recurrence: str | None = None


@runtime_checkable
class CalendarClient(Protocol):
    """The seam every Google Calendar write goes through.

    A production implementation performs real HTTP; the test implementation is
    a deterministic fake. No CWI code depends on the concrete type.
    """

    def list_events(self, *, access_token: str, time_min: datetime | None = None, time_max: datetime | None = None) -> list[dict]:
        """Read the complete primary-calendar event snapshot, following pages."""

    def list_calendars(self, *, access_token: str) -> list[CalendarInfo]:
        """Return the writable calendars for the authorized account."""

    def create_event(
        self,
        *,
        access_token: str,
        calendar_id: str,
        event: CalendarEventInput,
        idempotency_key: str,
    ) -> str:
        """Create an event and return its Google event id.

        The ``idempotency_key`` makes creation safe under retry: repeating a
        create with the same key resolves to the same event id rather than
        creating a duplicate (Requirement 28.7 / Property 16).
        """

    def update_event(
        self,
        *,
        access_token: str,
        calendar_id: str,
        event_id: str,
        event: CalendarEventInput,
    ) -> None:
        """Update an existing event in place."""

    def delete_event(
        self,
        *,
        access_token: str,
        calendar_id: str,
        event_id: str,
    ) -> None:
        """Delete/cancel an existing event."""


class FakeCalendarClient:
    """A deterministic, offline :class:`CalendarClient` for tests / local dev.

    Nothing touches the network. It returns a canned set of writable calendars
    and mints deterministic event ids. Creation is **idempotent by key**: a
    repeated :meth:`create_event` with the same ``idempotency_key`` returns the
    same event id (mirroring Google's request-idempotency guarantee), so the
    fake never fabricates a duplicate event even if the service layer were to
    call it twice (Requirement 28.7 / Property 16). The ``access_token`` is
    accepted and ignored so tests drive the full flow with no real call.
    """

    def __init__(self, calendars: list[CalendarInfo] | None = None) -> None:
        self._calendars = list(calendars) if calendars is not None else [
            CalendarInfo(
                calendar_id="primary",
                summary="Primary",
                primary=True,
                access_role="owner",
            ),
            CalendarInfo(
                calendar_id="team@group.calendar.google.com",
                summary="Team",
                primary=False,
                access_role="writer",
            ),
        ]
        # idempotency_key -> event_id, so repeated creates resolve identically.
        self._events_by_key: dict[str, str] = {}
        self._counter = 0
        # Recorded for assertions in tests.
        self.create_calls: int = 0
        self.update_calls: int = 0
        self.delete_calls: int = 0
        self.fail_next_create: bool = False
        self.source_events: list[dict] = []

    def list_events(self, *, access_token: str, time_min: datetime | None = None, time_max: datetime | None = None) -> list[dict]:
        return list(self.source_events)

    def set_calendars(self, calendars: list[CalendarInfo]) -> None:
        """Replace the canned writable-calendar set."""

        self._calendars = list(calendars)

    def list_calendars(self, *, access_token: str) -> list[CalendarInfo]:
        # Only writable calendars are surfaced (owner/writer access role).
        return [
            c
            for c in self._calendars
            if c.access_role in ("owner", "writer")
        ]

    def create_event(
        self,
        *,
        access_token: str,
        calendar_id: str,
        event: CalendarEventInput,
        idempotency_key: str,
    ) -> str:
        # A repeated create with the same key resolves to the same event id —
        # the fake never fabricates a duplicate (Property 16).
        existing = self._events_by_key.get(idempotency_key)
        if existing is not None:
            return existing
        if self.fail_next_create:
            self.fail_next_create = False
            raise CalendarClientError("Simulated Google Calendar create failure.")
        self.create_calls += 1
        self._counter += 1
        event_id = f"evt-{idempotency_key[:12]}-{self._counter}"
        self._events_by_key[idempotency_key] = event_id
        return event_id

    def update_event(
        self,
        *,
        access_token: str,
        calendar_id: str,
        event_id: str,
        event: CalendarEventInput,
    ) -> None:
        self.update_calls += 1

    def delete_event(
        self,
        *,
        access_token: str,
        calendar_id: str,
        event_id: str,
    ) -> None:
        self.delete_calls += 1


class CalendarClientError(Exception):
    """Raised when a Google Calendar operation fails.

    The message is safe to surface to users / store in ``last_error``: it must
    never contain a token or key.
    """


def _event_body(event: CalendarEventInput) -> dict:
    """Build a Calendar API event resource body from a :class:`CalendarEventInput`.

    Timed events use ``start.dateTime``/``end.dateTime`` (ISO 8601, tz included);
    an all-day event uses ``start.date``/``end.date`` (Requirement 28.3). Popup
    reminder overrides are attached (with ``useDefault=false``) only when
    reminder minutes are supplied, and a recurrence rule is included when set.
    """

    body: dict[str, object] = {}
    if event.summary:
        body["summary"] = event.summary
    if event.description:
        body["description"] = event.description

    if event.all_day and event.all_day_date:
        day = date.fromisoformat(event.all_day_date) if isinstance(event.all_day_date, str) else event.all_day_date
        body["start"] = {"date": day.isoformat()}
        body["end"] = {"date": (day + timedelta(days=1)).isoformat()}
    else:
        if event.start is not None:
            body["start"] = {"dateTime": event.start.isoformat()}
        if event.end is not None:
            body["end"] = {"dateTime": event.end.isoformat()}

    if event.reminder_minutes:
        body["reminders"] = {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": minutes}
                for minutes in event.reminder_minutes
            ],
        }

    if event.recurrence:
        body["recurrence"] = [event.recurrence]

    return body


class HttpCalendarClient:
    """A production :class:`CalendarClient` over Google Calendar's real API v3.

    Selected only when Google client credentials are configured (see
    :func:`get_calendar_client`); tests inject an ``httpx.MockTransport`` so no
    real network call ever occurs. The passed ``access_token`` is used solely as
    the Bearer credential and is **never** placed in an exception message or log
    — every failure raises :class:`CalendarClientError` with a fixed, secret-free
    message (mirroring :class:`HttpGoogleOAuthClient`).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        # Tests inject an ``httpx.MockTransport`` so no real network call occurs.
        self._transport = transport

    # -- Internal helpers ---------------------------------------------------

    def _client(self, access_token: str) -> httpx.Client:
        """Build a short-timeout Bearer-authorized client (test-injectable)."""

        return httpx.Client(
            base_url=CALENDAR_API_BASE,
            timeout=_HTTP_TIMEOUT_SECONDS,
            transport=self._transport,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    # -- Public API ---------------------------------------------------------

    def list_events(self, *, access_token: str, time_min: datetime | None = None, time_max: datetime | None = None) -> list[dict]:
        # Keep recurring masters (no unbounded recurrence expansion). A full
        # snapshot also observes deletions without persisting expired tokens.
        events: list[dict] = []
        params: dict[str, str | int] = {"maxResults": 2500, "singleEvents": "false"}
        if time_min is not None:
            params["timeMin"] = time_min.isoformat()
        if time_max is not None:
            params["timeMax"] = time_max.isoformat()
        if time_min is not None and time_max is not None:
            params["singleEvents"] = "true"
        seen_tokens: set[str] = set()
        try:
            with self._client(access_token) as http:
                while True:
                    response = http.get("/calendars/primary/events", params=params)
                    response.raise_for_status()
                    payload = response.json()
                    items = payload.get("items", [])
                    if not isinstance(items, list) or any(not isinstance(item, dict) or not item.get("id") for item in items):
                        raise ValueError("Invalid event response")
                    events.extend(items)
                    token = payload.get("nextPageToken")
                    if not token:
                        return events
                    if token in seen_tokens:
                        raise ValueError("Repeated page token")
                    seen_tokens.add(token)
                    params["pageToken"] = token
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            _log_calendar_error("list_events", exc)
            raise CalendarClientError("Calendar events could not be read. Please retry or reconnect.") from exc

    def list_calendars(self, *, access_token: str) -> list[CalendarInfo]:
        try:
            with self._client(access_token) as http:
                resp = http.get("/users/me/calendarList")
                resp.raise_for_status()
                items = resp.json().get("items") or []
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            _log_calendar_error("list_calendars", exc)
            raise CalendarClientError(
                "Google calendars could not be listed."
            ) from exc

        calendars: list[CalendarInfo] = []
        for item in items:
            access_role = str(item.get("accessRole", ""))
            if access_role not in _WRITABLE_ROLES:
                continue
            calendars.append(
                CalendarInfo(
                    calendar_id=str(item.get("id", "")),
                    summary=str(item.get("summary", "")),
                    primary=bool(item.get("primary", False)),
                    access_role=access_role,
                )
            )
        return calendars

    def create_event(
        self,
        *,
        access_token: str,
        calendar_id: str,
        event: CalendarEventInput,
        idempotency_key: str,
    ) -> str:
        event_id = _event_id_for_idempotency_key(idempotency_key)
        body = _event_body(event)
        body["id"] = event_id
        try:
            with self._client(access_token) as http:
                resp = http.post(
                    f"/calendars/{calendar_id}/events",
                    json=body,
                )
                if resp.status_code == 409:
                    existing = http.get(
                        f"/calendars/{calendar_id}/events/{event_id}"
                    )
                    existing.raise_for_status()
                    return str(existing.json()["id"])
                resp.raise_for_status()
                return str(resp.json()["id"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            _log_calendar_error("create_event", exc)
            raise CalendarClientError(
                "The Google Calendar event could not be created."
            ) from exc

    def update_event(
        self,
        *,
        access_token: str,
        calendar_id: str,
        event_id: str,
        event: CalendarEventInput,
    ) -> None:
        try:
            with self._client(access_token) as http:
                resp = http.patch(
                    f"/calendars/{calendar_id}/events/{event_id}",
                    json=_event_body(event),
                )
                resp.raise_for_status()
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            _log_calendar_error("update_event", exc)
            raise CalendarClientError(
                "The Google Calendar event could not be updated."
            ) from exc

    def delete_event(
        self,
        *,
        access_token: str,
        calendar_id: str,
        event_id: str,
    ) -> None:
        try:
            with self._client(access_token) as http:
                resp = http.delete(
                    f"/calendars/{calendar_id}/events/{event_id}"
                )
                # A 404/410 means the event is already gone — treat as success.
                if resp.status_code in (404, 410):
                    return
                resp.raise_for_status()
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            _log_calendar_error("delete_event", exc)
            raise CalendarClientError(
                "The Google Calendar event could not be deleted."
            ) from exc


def get_calendar_client(settings: Settings | None = None) -> CalendarClient:
    """Resolve the :class:`CalendarClient` for the current configuration.

    Returns the production :class:`HttpCalendarClient` when real Google client
    credentials are configured; otherwise a :class:`FakeCalendarClient` so local
    / mock development runs with no network dependency. Route handlers depend on
    this function and tests override it with a pre-seeded fake, so no network
    I/O ever occurs under test.
    """

    settings = settings or get_settings()
    if settings.google_oauth_client_id and settings.google_oauth_client_secret:
        return HttpCalendarClient(settings)
    return FakeCalendarClient()


__all__ = [
    "CALENDAR_API_BASE",
    "CalendarInfo",
    "CalendarEventInput",
    "CalendarClient",
    "CalendarClientError",
    "FakeCalendarClient",
    "HttpCalendarClient",
    "get_calendar_client",
]
