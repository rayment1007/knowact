"""Calendar event creation routes (M6.3, Requirement 28).

Exposes the explicit, auditable, idempotent "Add to Google Calendar" flow over
HTTP. Every route is protected by :func:`app.dependencies.get_current_user`
(missing/invalid session → ``401``) and scoped to the caller's organization, so
a :class:`~app.modules.cwi.models.CalendarEventLink`, ``ActionItem``, or
:class:`~app.modules.cwi.models.IntegrationConnection` belonging to another
tenant is indistinguishable from a missing one and yields ``404`` (Requirement
28.10 / Property 13). No token is ever serialized into a response body.

Routes:
* ``GET  /api/calendar/{connection_id}/calendars`` — writable calendars.
* ``POST /api/actions/{action_id}/add-to-calendar`` — add a confirmed action.
* ``PATCH /api/calendar/links/{link_id}`` — update the event.
* ``POST /api/calendar/links/{link_id}/cancel`` — cancel/delete the event.
* ``POST /api/calendar/links/{link_id}/retry`` — retry a failed sync.
* ``GET  /api/calendar/links`` — list the user's calendar event links.
* ``POST /api/calendar/daily-brief-block`` — create the recurring brief block.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.models import User
from app.database import get_db
from app.dependencies import get_current_user
from app.modules.cwi.dependencies import calendar_client, google_oauth_client
from app.modules.cwi.schemas import (
    CalendarAddRequest,
    CalendarDailyBriefBlockRequest,
    CalendarEventLinkView,
    CalendarUpdateRequest,
    CalendarView,
)
from app.modules.cwi.services.calendar_client import CalendarClient, CalendarInfo, CalendarClientError
from app.modules.cwi.services.calendar_service import CalendarService
from app.modules.cwi.services.google_oauth import (
    GoogleOAuthClient,
    GoogleOAuthError,
)
from app.modules.cwi.services.integration_service import IntegrationService

router = APIRouter(tags=["calendar"])


@router.delete("/calendar/sources/{source_id}", status_code=204)
def remove_calendar_source(source_id: UUID, db: Session = Depends(get_db),
                           user: User = Depends(get_current_user)):
    """Remove only the captured source; a later sync may import it again."""
    from sqlalchemy import select
    from app.modules.cwi.models import IntegrationConnection
    from app.modules.cwi.services.workspace_sources import _source
    from app.modules.cwi.services.source_proposal_service import discard_unapproved
    from app.core.services.audit_service import AuditService

    row, _ = _source(db, user, "calendar", source_id)
    db.execute(select(IntegrationConnection).where(
        IntegrationConnection.id == row.integration_connection_id,
        IntegrationConnection.organization_id == user.organization_id,
        IntegrationConnection.user_id == user.id).with_for_update()).scalar_one()
    discard_unapproved(db, user.organization_id, "calendar", [row.id])
    db.delete(row)
    AuditService(db).record(org_id=user.organization_id, actor_id=user.id,
        action_type="DELETE_CALENDAR_SOURCE", target_type="CalendarSource",
        target_id=source_id, detail={})
    db.flush()


def _service(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    calendar: CalendarClient = Depends(calendar_client),
    oauth: GoogleOAuthClient = Depends(google_oauth_client),
) -> CalendarService:
    """Build a :class:`CalendarService` bound to the request transaction."""

    integration_service = IntegrationService(db, oauth, settings=settings)
    return CalendarService(
        db,
        calendar,
        integration_service=integration_service,
        settings=settings,
    )


@router.post("/calendar/{connection_id}/sync-now")
def sync_sources(
    connection_id: UUID,
    service: CalendarService = Depends(_service),
    user: User = Depends(get_current_user),
):
    try:
        count = service.sync_sources(user.organization_id, user.id, connection_id)
    except (CalendarClientError, GoogleOAuthError):
        # Return normally so get_db persists the failure status, not a success timestamp.
        return JSONResponse(status_code=502, content={"detail": "Calendar sync failed. Please retry or reconnect."})
    return {"events_synced": count}


def _calendar_view(info: CalendarInfo) -> CalendarView:
    return CalendarView(
        calendar_id=info.calendar_id,
        summary=info.summary,
        primary=info.primary,
        access_role=info.access_role,
    )


@router.get(
    "/calendar/{connection_id}/calendars",
    response_model=list[CalendarView],
)
def list_calendars(
    connection_id: UUID,
    service: CalendarService = Depends(_service),
    user: User = Depends(get_current_user),
) -> list[CalendarView] | JSONResponse:
    """List the writable calendars for a connection (Requirement 28.3)."""

    try:
        calendars = service.list_writable_calendars(
            user.organization_id, user.id, connection_id
        )
    except GoogleOAuthError:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": "Calendar authorization expired. Please reconnect."},
        )
    return [_calendar_view(c) for c in calendars]


@router.post(
    "/actions/{action_id}/add-to-calendar",
    response_model=CalendarEventLinkView,
)
def add_action_to_calendar(
    action_id: UUID,
    payload: CalendarAddRequest,
    service: CalendarService = Depends(_service),
    user: User = Depends(get_current_user),
) -> CalendarEventLinkView:
    """Add a confirmed action to Google Calendar (Requirements 28.1-28.8).

    A non-confirmed action yields ``409``; a cross-org/missing action or
    connection yields ``404``. Idempotent: repeated calls return the same link
    and create at most one Google event (Requirement 28.7 / Property 16).
    """

    link = service.add_action_to_calendar(
        user.organization_id, user.id, action_id, payload
    )
    return CalendarEventLinkView.model_validate(link)


@router.get("/calendar/links", response_model=list[CalendarEventLinkView])
def list_links(
    service: CalendarService = Depends(_service),
    user: User = Depends(get_current_user),
) -> list[CalendarEventLinkView]:
    """List the user's calendar event links with their sync state (28.6)."""

    links = service.list_links(user.organization_id, user.id)
    return [CalendarEventLinkView.model_validate(link) for link in links]


@router.get(
    "/calendar/links/{link_id}", response_model=CalendarEventLinkView
)
def get_link(
    link_id: UUID,
    service: CalendarService = Depends(_service),
    user: User = Depends(get_current_user),
) -> CalendarEventLinkView:
    """Return one org-scoped Calendar link for a direct deep link."""

    link = service.get_link(user.organization_id, user.id, link_id)
    return CalendarEventLinkView.model_validate(link)


@router.patch(
    "/calendar/links/{link_id}", response_model=CalendarEventLinkView
)
def update_event(
    link_id: UUID,
    payload: CalendarUpdateRequest,
    service: CalendarService = Depends(_service),
    user: User = Depends(get_current_user),
) -> CalendarEventLinkView:
    """Update an existing calendar event in place (Requirement 28.6)."""

    link = service.update_event(
        user.organization_id, user.id, link_id, payload
    )
    return CalendarEventLinkView.model_validate(link)


@router.post(
    "/calendar/links/{link_id}/cancel", response_model=CalendarEventLinkView
)
def cancel_event(
    link_id: UUID,
    service: CalendarService = Depends(_service),
    user: User = Depends(get_current_user),
) -> CalendarEventLinkView:
    """Cancel/delete a calendar event on Google (Requirement 28.6)."""

    link = service.cancel_event(user.organization_id, user.id, link_id)
    return CalendarEventLinkView.model_validate(link)


@router.post(
    "/calendar/links/{link_id}/retry", response_model=CalendarEventLinkView
)
def retry_sync(
    link_id: UUID,
    service: CalendarService = Depends(_service),
    user: User = Depends(get_current_user),
) -> CalendarEventLinkView:
    """Retry a failed calendar sync (Requirement 28.6)."""

    link = service.retry(user.organization_id, user.id, link_id)
    return CalendarEventLinkView.model_validate(link)


@router.post(
    "/calendar/daily-brief-block", response_model=CalendarEventLinkView
)
def create_daily_brief_block(
    payload: CalendarDailyBriefBlockRequest,
    service: CalendarService = Depends(_service),
    user: User = Depends(get_current_user),
) -> CalendarEventLinkView:
    """Create the optional recurring Daily Brief calendar block (Req 28.9)."""

    link = service.create_daily_brief_block(
        user.organization_id, user.id, payload
    )
    return CalendarEventLinkView.model_validate(link)
