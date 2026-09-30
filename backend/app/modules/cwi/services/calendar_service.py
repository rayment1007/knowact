"""Google Calendar event creation from confirmed actions (Requirement 28).

:class:`CalendarService` turns a **confirmed** :class:`~app.core.models.ActionItem`
into a Google Calendar event through an explicit, auditable, idempotent flow.
The ``ActionItem`` remains the source of truth; a
:class:`~app.modules.cwi.models.CalendarEventLink` records the externally-created
event and its sync state (Requirement 28.5).

Design invariants carried from the Core Engine services:

* **Explicit request only, confirmed action only.** An event is never
  auto-added (Requirement 28.4); the caller must make an explicit
  "Add to Google Calendar" request, and the referenced action must be confirmed
  — a non-confirmed (withdrawn/``CANCELLED``) action is rejected with ``409``
  and no event is created (Requirement 28.2).
* **Idempotent creation (Requirement 28.7 / Property 16).** A stable
  ``idempotency_key`` derived from ``(action_item_id, google_calendar_id)`` is
  used *both* as the Google request idempotency key *and*, via the unique
  constraint on ``(organization_id, action_item_id, integration_connection_id,
  google_calendar_id)``, as a structural guard. Repeatedly adding the same
  confirmed action to the same calendar yields **at most one**
  ``CalendarEventLink`` and **at most one** ``google_event_id``.
* **One audit per mutation, same transaction (Requirement 28.8 / Property 20).**
  Each successful create/update/cancel writes exactly one ``AuditLog``
  (``ADD_CALENDAR_EVENT`` / ``UPDATE_CALENDAR_EVENT`` / ``CANCEL_CALENDAR_EVENT``)
  in the caller's transaction and updates ``sync_status``.
* **Org + user scoping.** Every lookup is org-scoped; a cross-tenant
  ``CalendarEventLink`` or ``ActionItem`` id is indistinguishable from a missing
  one and yields ``404`` (Requirement 28.10 / Property 13).
* **No self-commit.** The service ``add``/``flush``es within the caller's
  transaction and never commits, so the event mutation and its audit row commit
  atomically.

Google I/O is performed exclusively through an injected
:class:`~app.modules.cwi.services.calendar_client.CalendarClient`, so tests use a
deterministic fake and no real Calendar call ever occurs (Requirement 34).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.models import ActionItem, ActionStatus
from app.core.services.audit_service import AuditService
from app.dependencies import not_found, scope_select
from app.modules.cwi.models import (
    CalendarSource,
    CalendarEventLink,
    CalendarSyncStatus,
    ConnectionStatus,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationService as IntegrationServiceEnum,
)
from app.modules.cwi.schemas import (
    CalendarAddRequest,
    CalendarDailyBriefBlockRequest,
    CalendarUpdateRequest,
)
from app.modules.cwi.services.calendar_client import (
    CalendarClient,
    CalendarClientError,
    CalendarEventInput,
    CalendarInfo,
)
from app.modules.cwi.services.google_oauth import (
    CALENDAR_CALENDARLIST_READONLY_SCOPE,
    CALENDAR_EVENTS_SCOPE,
    GoogleOAuthError,
)
from app.modules.cwi.services.integration_service import IntegrationService

# Audit action types recorded per calendar mutation (Requirement 28.8 / P20).
ADD_CALENDAR_EVENT = "ADD_CALENDAR_EVENT"
UPDATE_CALENDAR_EVENT = "UPDATE_CALENDAR_EVENT"
CANCEL_CALENDAR_EVENT = "CANCEL_CALENDAR_EVENT"

_TARGET_TYPE = "CalendarEventLink"

# An ActionItem may be scheduled only while it is an active, confirmed
# commitment. A ``CANCELLED`` action has been withdrawn and is treated as "not
# confirmed" for the purpose of adding it to a calendar (Requirement 28.2).
_ADDABLE_ACTION_STATUSES: frozenset[ActionStatus] = frozenset(
    {ActionStatus.OPEN, ActionStatus.IN_PROGRESS, ActionStatus.DONE}
)


def _conflict(detail: str) -> HTTPException:
    """Build the ``409 Conflict`` used when a precondition fails."""

    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def derive_idempotency_key(action_item_id: UUID, google_calendar_id: str) -> str:
    """Return a stable idempotency key for ``(action, calendar)`` (Req 28.7).

    The key is deterministic in its inputs, so repeated "Add to Calendar"
    requests for the same confirmed action and calendar resolve to the same key
    — the basis for both the Google request-idempotency guarantee and the local
    unique constraint. A ``sha256`` digest keeps the key stable and bounded
    (<= 128 chars) even when ``google_calendar_id`` is a long address.
    """

    basis = f"action|{action_item_id}|{google_calendar_id}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _brief_idempotency_key(user_id: UUID, google_calendar_id: str) -> str:
    """Return a stable idempotency key for a user's Daily Brief block."""

    basis = f"daily-brief|{user_id}|{google_calendar_id}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


class CalendarService:
    """Create, update, cancel, and retry Google Calendar events (Req 28)."""

    def __init__(
        self,
        db: Session,
        calendar_client: CalendarClient,
        *,
        integration_service: IntegrationService | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Bind the service to a session and an injected Calendar transport.

        Args:
            db: Request-scoped session (transaction owned by the caller).
            calendar_client: The Calendar transport (a fake in tests).
            integration_service: Used to obtain a valid access token for the
                connection; when omitted a transient empty token is used (the
                fake client ignores it, so tests need no OAuth wiring).
            settings: Optional settings; process settings are used if omitted.
        """

        self.db = db
        self.calendar = calendar_client
        self.settings = settings or get_settings()
        self._integration_service = integration_service
        self.audit = AuditService(db)

    # -- Scoped lookups -----------------------------------------------------

    def _get_connection(
        self,
        org_id: UUID,
        connection_id: UUID,
        user_id: UUID | None = None,
        *,
        required_scopes: tuple[str, ...] = (CALENDAR_EVENTS_SCOPE,),
    ) -> IntegrationConnection:
        stmt = scope_select(
            select(IntegrationConnection), IntegrationConnection, org_id
        ).where(IntegrationConnection.id == connection_id)
        if user_id is not None:
            stmt = stmt.where(IntegrationConnection.user_id == user_id)
        connection = self.db.execute(stmt).scalar_one_or_none()
        if connection is None:
            raise not_found("Integration connection not found.")
        missing_scopes = set(required_scopes).difference(
            connection.granted_scopes_json or []
        )
        if (
            connection.provider != IntegrationProvider.GOOGLE
            or connection.service != IntegrationServiceEnum.GOOGLE_CALENDAR
            or connection.status != ConnectionStatus.CONNECTED
            or missing_scopes
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "A connected Google Calendar account with the required "
                    "authorization is needed. Reconnect Calendar and try again."
                ),
            )
        return connection

    def _get_action(self, org_id: UUID, action_id: UUID) -> ActionItem:
        stmt = scope_select(select(ActionItem), ActionItem, org_id).where(
            ActionItem.id == action_id
        )
        action = self.db.execute(stmt).scalar_one_or_none()
        if action is None:
            raise not_found("Action item not found.")
        return action

    def _get_link(
        self, org_id: UUID, user_id: UUID, link_id: UUID
    ) -> CalendarEventLink:
        """Return an org-and-user-scoped link or ``404``."""

        stmt = scope_select(
            select(CalendarEventLink), CalendarEventLink, org_id
        ).where(
            CalendarEventLink.id == link_id,
            CalendarEventLink.user_id == user_id,
        )
        link = self.db.execute(stmt).scalar_one_or_none()
        if link is None:
            raise not_found("Calendar event link not found.")
        return link

    def _find_link_identity(
        self,
        org_id: UUID,
        action_item_id: UUID | None,
        connection_id: UUID,
        google_calendar_id: str,
    ) -> CalendarEventLink | None:
        stmt = scope_select(
            select(CalendarEventLink), CalendarEventLink, org_id
        ).where(
            CalendarEventLink.integration_connection_id == connection_id,
            CalendarEventLink.google_calendar_id == google_calendar_id,
        )
        if action_item_id is None:
            stmt = stmt.where(CalendarEventLink.action_item_id.is_(None))
        else:
            stmt = stmt.where(
                CalendarEventLink.action_item_id == action_item_id
            )
        return self.db.execute(stmt).scalars().first()

    def _access_token(
        self,
        org_id: UUID,
        user_id: UUID,
        connection_id: UUID,
        *,
        required_scopes: tuple[str, ...] = (CALENDAR_EVENTS_SCOPE,),
    ) -> str:
        """Obtain a valid (refreshed) access token, or a transient empty one.

        The token exists only transiently in memory for the outbound Calendar
        call and is never persisted or logged. When no
        :class:`IntegrationService` is injected (tests with the fake client),
        an empty string is returned — the fake ignores it.
        """

        if self._integration_service is not None:
            return self._integration_service.get_valid_access_token(
                org_id,
                user_id,
                connection_id,
                expected_service=IntegrationServiceEnum.GOOGLE_CALENDAR,
                required_scopes=required_scopes,
            )
        return ""

    # -- Event payload building ---------------------------------------------

    def sync_sources(self, org_id: UUID, user_id: UUID, connection_id: UUID) -> int:
        connection = self._get_connection(org_id, connection_id, user_id)
        # Serialize snapshots per connection, including requests from other tabs.
        self.db.execute(select(IntegrationConnection).where(
            IntegrationConnection.id == connection.id
        ).with_for_update()).scalar_one()
        try:
            token = self._access_token(org_id, user_id, connection_id)
            events = self.calendar.list_events(access_token=token)
        except (CalendarClientError, GoogleOAuthError):
            connection.last_error = "Calendar sync failed. Please retry or reconnect."
            self.db.flush()
            raise

        existing = {row.google_event_id: row for row in self.db.scalars(
            select(CalendarSource).where(
                CalendarSource.organization_id == org_id,
                CalendarSource.integration_connection_id == connection_id,
            )
        )}
        seen = set()
        now = datetime.now(timezone.utc)
        for event in events:
            if event.get("status") == "cancelled":
                continue
            event_id = str(event["id"])
            seen.add(event_id)
            row = existing.get(event_id)
            if row is None:
                row = CalendarSource(organization_id=org_id,
                    integration_connection_id=connection_id, google_event_id=event_id)
                existing[event_id] = row
                self.db.add(row)
            values = {
                "title": str(event.get("summary") or "Untitled event"),
                "description": str(event.get("description") or ""),
                "location": str(event.get("location") or ""),
                "starts_at": str((event.get("start") or {}).get("dateTime") or (event.get("start") or {}).get("date") or ""),
                "ends_at": str((event.get("end") or {}).get("dateTime") or (event.get("end") or {}).get("date") or ""),
                "html_link": str(event.get("htmlLink") or ""),
            }
            if any(getattr(row, field) != value for field, value in values.items()):
                for field, value in values.items():
                    setattr(row, field, value)
                row.updated_at = now
        # Remove local snapshots only after the entire provider read succeeds.
        for event_id, row in existing.items():
            if event_id not in seen:
                from app.modules.cwi.services.source_proposal_service import discard_unapproved
                discard_unapproved(self.db, org_id, "calendar", [row.id])
                self.db.delete(row)
        connection.last_sync_at = now
        connection.last_error = None
        self.db.flush()
        return len(seen)

    def _event_from_action(
        self, action: ActionItem, req: CalendarAddRequest
    ) -> CalendarEventInput:
        """Build the create payload from the action and the (edited) request.

        The request carries the user's edit-before-confirm choices (summary,
        description, times, all-day, reminders); anything omitted falls back to
        the action's own fields, so a due-date-only action can be scheduled as
        an all-day event (Requirement 28.3).
        """

        summary = (req.summary or action.title or "Action").strip() or "Action"
        description = (
            req.description
            if req.description is not None
            else (action.description or "")
        )
        all_day_date = req.all_day_date
        if req.all_day and all_day_date is None and action.due_date is not None:
            all_day_date = action.due_date.isoformat()
        return CalendarEventInput(
            summary=summary,
            description=description or "",
            start=req.start,
            end=req.end,
            all_day=req.all_day,
            all_day_date=all_day_date,
            reminder_minutes=list(req.reminder_minutes or []),
        )

    def _event_from_update(
        self, link: CalendarEventLink, action: ActionItem | None, req: CalendarUpdateRequest
    ) -> CalendarEventInput:
        summary = (req.summary or (action.title if action else None) or "Action").strip()
        description = req.description if req.description is not None else (
            action.description if action and action.description else ""
        )
        all_day_date = req.all_day_date
        if (
            req.all_day
            and all_day_date is None
            and action is not None
            and action.due_date is not None
        ):
            all_day_date = action.due_date.isoformat()
        return CalendarEventInput(
            summary=summary or "Action",
            description=description or "",
            start=req.start,
            end=req.end,
            all_day=bool(req.all_day),
            all_day_date=all_day_date,
            reminder_minutes=list(req.reminder_minutes or []),
        )

    # -- Public API ---------------------------------------------------------

    def list_writable_calendars(
        self, org_id: UUID, user_id: UUID, connection_id: UUID
    ) -> list[CalendarInfo]:
        """Return the writable calendars for a connection (Requirement 28.3).

        Only calendars the user can write to are surfaced, since an event can be
        created only on a writable calendar. A cross-org/missing connection
        yields ``404``.
        """

        list_scope = (CALENDAR_CALENDARLIST_READONLY_SCOPE,)
        self._get_connection(
            org_id,
            connection_id,
            user_id,
            required_scopes=list_scope,
        )
        token = self._access_token(
            org_id,
            user_id,
            connection_id,
            required_scopes=list_scope,
        )
        return self.calendar.list_calendars(access_token=token)

    def add_action_to_calendar(
        self,
        org_id: UUID,
        user_id: UUID,
        action_item_id: UUID,
        req: CalendarAddRequest,
    ) -> CalendarEventLink:
        """Add a confirmed action to Google Calendar (Requirements 28.1-28.8).

        Rejects a non-confirmed (withdrawn) action with ``409`` (Requirement
        28.2). Idempotent: a stable ``idempotency_key`` from
        ``(action_item_id, google_calendar_id)`` plus the unique constraint mean
        repeated calls return the same link and create at most one Google event
        (Requirement 28.7 / Property 16). On the first successful create it sets
        ``google_event_id``, marks the link ``SYNCED``, and writes exactly one
        ``ADD_CALENDAR_EVENT`` audit row in the same transaction (Requirement
        28.8).
        """

        action = self._get_action(org_id, action_item_id)
        if action.status not in _ADDABLE_ACTION_STATUSES:
            raise _conflict(
                "Cannot add an action that is not confirmed to a calendar."
            )

        connection = self._get_connection(org_id, req.connection_id, user_id)
        idempotency_key = derive_idempotency_key(
            action_item_id, req.google_calendar_id
        )

        # Idempotency guard: an existing link for this identity is reused. A
        # link already SYNCED short-circuits — no second event, no second audit.
        link = self._find_link_identity(
            org_id, action_item_id, connection.id, req.google_calendar_id
        )
        if link is not None and link.sync_status == CalendarSyncStatus.SYNCED:
            return link
        if link is None:
            link = CalendarEventLink(
                organization_id=org_id,
                user_id=user_id,
                action_item_id=action_item_id,
                integration_connection_id=connection.id,
                google_calendar_id=req.google_calendar_id,
                google_event_id=None,
                idempotency_key=idempotency_key,
                sync_status=CalendarSyncStatus.PENDING,
            )
            self.db.add(link)
            self.db.flush()

        event = self._event_from_action(action, req)
        return self._perform_create(org_id, user_id, link, connection.id, event)

    def _perform_create(
        self,
        org_id: UUID,
        user_id: UUID,
        link: CalendarEventLink,
        connection_id: UUID,
        event: CalendarEventInput,
    ) -> CalendarEventLink:
        """Call the transport to create the event and record the outcome.

        On success sets ``google_event_id`` + ``SYNCED`` and writes exactly one
        ``ADD_CALENDAR_EVENT`` audit row. On a transport error marks the link
        ``FAILED`` with a secret-free ``last_error`` and writes no audit (no
        external event was created), leaving it retriable.
        """

        try:
            token = self._access_token(org_id, user_id, connection_id)
            event_id = self.calendar.create_event(
                access_token=token,
                calendar_id=link.google_calendar_id,
                event=event,
                idempotency_key=link.idempotency_key,
            )
        except (CalendarClientError, GoogleOAuthError):
            link.sync_status = CalendarSyncStatus.FAILED
            link.last_error = "Calendar sync failed. Please retry or reconnect."
            self.db.add(link)
            self.db.flush()
            return link

        link.google_event_id = event_id
        link.sync_status = CalendarSyncStatus.SYNCED
        link.last_synced_at = datetime.now(timezone.utc)
        link.last_error = None
        self.db.add(link)
        self.db.flush()

        self.audit.record(
            org_id=org_id,
            actor_id=user_id,
            action_type=ADD_CALENDAR_EVENT,
            target_type=_TARGET_TYPE,
            target_id=link.id,
            detail={
                "google_calendar_id": link.google_calendar_id,
                "google_event_id": event_id,
                "action_item_id": (
                    str(link.action_item_id)
                    if link.action_item_id is not None
                    else None
                ),
            },
        )
        self.db.flush()
        return link

    def update_event(
        self,
        org_id: UUID,
        user_id: UUID,
        link_id: UUID,
        req: CalendarUpdateRequest,
    ) -> CalendarEventLink:
        """Update an existing calendar event in place (Requirement 28.6).

        Requires a created event (``google_event_id`` set); otherwise ``409``.
        On success marks the link ``SYNCED`` and writes exactly one
        ``UPDATE_CALENDAR_EVENT`` audit row in the same transaction. A transport
        failure marks the link ``FAILED`` (retriable) with a secret-free error.
        """

        link = self._get_link(org_id, user_id, link_id)
        if link.google_event_id is None:
            raise _conflict("Cannot update an event that has not been created.")

        action = (
            self._get_action(org_id, link.action_item_id)
            if link.action_item_id is not None
            else None
        )
        event = self._event_from_update(link, action, req)

        link.sync_status = CalendarSyncStatus.UPDATE_PENDING
        self.db.add(link)
        self.db.flush()

        try:
            token = self._access_token(
                org_id, user_id, link.integration_connection_id
            )
            self.calendar.update_event(
                access_token=token,
                calendar_id=link.google_calendar_id,
                event_id=link.google_event_id,
                event=event,
            )
        except (CalendarClientError, GoogleOAuthError):
            link.sync_status = CalendarSyncStatus.FAILED
            link.last_error = "Calendar sync failed. Please retry or reconnect."
            self.db.add(link)
            self.db.flush()
            return link

        link.sync_status = CalendarSyncStatus.SYNCED
        link.last_synced_at = datetime.now(timezone.utc)
        link.last_error = None
        self.db.add(link)
        self.db.flush()

        self.audit.record(
            org_id=org_id,
            actor_id=user_id,
            action_type=UPDATE_CALENDAR_EVENT,
            target_type=_TARGET_TYPE,
            target_id=link.id,
            detail={
                "google_calendar_id": link.google_calendar_id,
                "google_event_id": link.google_event_id,
            },
        )
        self.db.flush()
        return link

    def cancel_event(
        self, org_id: UUID, user_id: UUID, link_id: UUID
    ) -> CalendarEventLink:
        """Cancel/delete a calendar event on Google (Requirement 28.6).

        Deletes the event (when one was created) and marks the link
        ``CANCELLED``, writing exactly one ``CANCEL_CALENDAR_EVENT`` audit row in
        the same transaction. If Google does not confirm the deletion, the link
        remains ``CANCEL_PENDING`` with a secret-free error. This preserves the
        operation being retried: a retry must delete again, never update the
        event merely because a ``google_event_id`` is present.
        """

        link = self._get_link(org_id, user_id, link_id)

        # A completed cancellation is locally idempotent. Do not issue a
        # second external delete or write a duplicate audit row.
        if link.sync_status == CalendarSyncStatus.CANCELLED:
            return link

        link.sync_status = CalendarSyncStatus.CANCEL_PENDING
        self.db.add(link)
        self.db.flush()

        try:
            token = self._access_token(
                org_id, user_id, link.integration_connection_id
            )
            if link.google_event_id is not None:
                self.calendar.delete_event(
                    access_token=token,
                    calendar_id=link.google_calendar_id,
                    event_id=link.google_event_id,
                )
        except (CalendarClientError, GoogleOAuthError):
            # Keep the intended operation explicit. A generic FAILED state plus
            # an existing event id is indistinguishable from a failed update and
            # previously caused retry() to call update_event instead of delete.
            link.sync_status = CalendarSyncStatus.CANCEL_PENDING
            link.last_error = (
                "Calendar cancellation was not confirmed. "
                "Please retry or reconnect."
            )
            self.db.add(link)
            self.db.flush()
            return link

        link.sync_status = CalendarSyncStatus.CANCELLED
        link.last_synced_at = datetime.now(timezone.utc)
        link.last_error = None
        self.db.add(link)
        self.db.flush()

        self.audit.record(
            org_id=org_id,
            actor_id=user_id,
            action_type=CANCEL_CALENDAR_EVENT,
            target_type=_TARGET_TYPE,
            target_id=link.id,
            detail={
                "google_calendar_id": link.google_calendar_id,
                "google_event_id": link.google_event_id,
            },
        )
        self.db.flush()
        return link

    def retry(
        self, org_id: UUID, user_id: UUID, link_id: UUID
    ) -> CalendarEventLink:
        """Retry a failed sync (Requirement 28.6).

        Re-attempts the operation that failed: a pending cancellation is always
        retried as a delete; otherwise a ``FAILED`` link is retried as a create
        when no ``google_event_id`` exists yet, or an update when one exists. A
        link in any other state is returned unchanged. A successful retry writes
        exactly one audit row matching the operation performed.
        """

        link = self._get_link(org_id, user_id, link_id)

        # Preserve cancellation intent across an ambiguous transport failure.
        # HttpCalendarClient treats 404/410 as a successful delete, making this
        # retry safe when Google deleted the event but the first response was
        # lost.
        if link.sync_status == CalendarSyncStatus.CANCEL_PENDING:
            return self.cancel_event(org_id, user_id, link_id)

        if link.sync_status != CalendarSyncStatus.FAILED:
            return link

        if link.google_event_id is None:
            # Retry the create using the same stable idempotency key.
            action = (
                self._get_action(org_id, link.action_item_id)
                if link.action_item_id is not None
                else None
            )
            event = CalendarEventInput(
                summary=(action.title if action else "Daily Brief") or "Action",
                description=(action.description if action and action.description else ""),
            )
            link.sync_status = CalendarSyncStatus.PENDING
            self.db.add(link)
            self.db.flush()
            return self._perform_create(
                org_id, user_id, link, link.integration_connection_id, event
            )

        # An event exists: retry as an update using the current action fields.
        return self.update_event(
            org_id, user_id, link_id, CalendarUpdateRequest()
        )

    def create_daily_brief_block(
        self,
        org_id: UUID,
        user_id: UUID,
        req: CalendarDailyBriefBlockRequest,
    ) -> CalendarEventLink:
        """Create the optional single recurring Daily Brief block (Req 28.9).

        The event's description carries **only** a deep link and a minimal label
        — never large or sensitive brief content. Idempotent per
        ``(user, calendar)``: an existing block is returned rather than
        duplicated. On create it writes exactly one ``ADD_CALENDAR_EVENT`` audit
        row in the same transaction.
        """

        connection = self._get_connection(org_id, req.connection_id, user_id)

        existing = self._find_link_identity(
            org_id, None, connection.id, req.google_calendar_id
        )
        if existing is not None and existing.sync_status == CalendarSyncStatus.SYNCED:
            return existing

        idempotency_key = _brief_idempotency_key(user_id, req.google_calendar_id)
        link = existing
        if link is None:
            link = CalendarEventLink(
                organization_id=org_id,
                user_id=user_id,
                action_item_id=None,
                integration_connection_id=connection.id,
                google_calendar_id=req.google_calendar_id,
                google_event_id=None,
                idempotency_key=idempotency_key,
                sync_status=CalendarSyncStatus.PENDING,
            )
            self.db.add(link)
            self.db.flush()

        # Description carries ONLY a deep link + minimal label (Requirement 28.9).
        label = (req.label or "Daily Brief").strip() or "Daily Brief"
        description = f"{label} — {req.deep_link}"
        event = CalendarEventInput(
            summary=label,
            description=description,
            start=req.start,
            recurrence=req.recurrence or "RRULE:FREQ=DAILY",
        )

        try:
            token = self._access_token(org_id, user_id, connection.id)
            event_id = self.calendar.create_event(
                access_token=token,
                calendar_id=link.google_calendar_id,
                event=event,
                idempotency_key=link.idempotency_key,
            )
        except (CalendarClientError, GoogleOAuthError):
            link.sync_status = CalendarSyncStatus.FAILED
            link.last_error = "Calendar sync failed. Please retry or reconnect."
            self.db.add(link)
            self.db.flush()
            return link

        link.google_event_id = event_id
        link.sync_status = CalendarSyncStatus.SYNCED
        link.last_synced_at = datetime.now(timezone.utc)
        link.last_error = None
        self.db.add(link)
        self.db.flush()

        self.audit.record(
            org_id=org_id,
            actor_id=user_id,
            action_type=ADD_CALENDAR_EVENT,
            target_type=_TARGET_TYPE,
            target_id=link.id,
            detail={
                "google_calendar_id": link.google_calendar_id,
                "google_event_id": event_id,
                "daily_brief_block": True,
            },
        )
        self.db.flush()
        return link

    # -- Reads --------------------------------------------------------------

    def list_links(
        self, org_id: UUID, user_id: UUID
    ) -> list[CalendarEventLink]:
        """Return the user's calendar event links, newest first."""

        stmt = scope_select(
            select(CalendarEventLink), CalendarEventLink, org_id
        ).where(CalendarEventLink.user_id == user_id)
        stmt = stmt.order_by(
            CalendarEventLink.created_at.desc(),
            CalendarEventLink.id.desc(),
        )
        return list(self.db.execute(stmt).scalars().all())

    def get_link(
        self, org_id: UUID, user_id: UUID, link_id: UUID
    ) -> CalendarEventLink:
        """Return a single org-and-user-scoped link or ``404``."""

        return self._get_link(org_id, user_id, link_id)


__all__ = [
    "CalendarService",
    "derive_idempotency_key",
    "ADD_CALENDAR_EVENT",
    "UPDATE_CALENDAR_EVENT",
    "CANCEL_CALENDAR_EVENT",
]
