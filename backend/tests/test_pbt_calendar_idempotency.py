"""Property-based test P16: Calendar create idempotency.

Property 16 (Calendar create idempotency): for all confirmed actions ``a``, all
target calendars ``c``, and any number of "Add to Calendar" requests ``n >= 1``,
adding ``a`` to ``c`` ``n`` times produces **at most one**
:class:`~app.modules.cwi.models.CalendarEventLink` and **at most one**
``google_event_id`` for the identity ``(organization_id, action_item_id,
integration_connection_id, google_calendar_id)``.

Each Hypothesis example seeds an isolated org + user + connection + a confirmed
:class:`~app.core.models.ActionItem`, then drives the
:class:`~app.modules.cwi.services.calendar_service.CalendarService` (backed by a
FAKE Calendar client — no network) over ``n`` add requests (each request may
carry different edited summary/description/times, exercising that only the
stable ``(action, calendar)`` identity — not the payload — governs
idempotency), and asserts the invariant holds. Per Requirement 21.3 the property
runs a minimum of 100 examples.

**Validates: Requirements 28.7 / Property 16**
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.core import models as core_models
from app.core.models import ActionItem, ActionStatus
from app.modules.cwi.models import (
    CalendarEventLink,
    ConnectionStatus,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationService as IntegrationServiceEnum,
)
from app.modules.cwi.schemas import CalendarAddRequest
from app.modules.cwi.services.calendar_client import CalendarInfo, FakeCalendarClient
from app.modules.cwi.services.calendar_service import CalendarService
from app.security import hash_password

_PW_HASH = hash_password("pw")
_TEST_SETTINGS = Settings(ai_provider="mock")

# Constrained text: printable, bounded, so generators stay in the input space.
_TEXT = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=0,
    max_size=60,
)

# The fake client's two canned writable calendars (owner/writer access).
_CALENDAR_IDS = ["primary", "team@group.calendar.google.com"]


def _seed_confirmed_action(
    db: Session,
) -> tuple[IntegrationConnection, ActionItem]:
    """Seed an isolated org + user + Calendar connection + confirmed action."""

    suffix = uuid4().hex
    org = core_models.Organization(name=f"Org-{suffix}")
    db.add(org)
    db.flush()
    user = core_models.User(
        organization_id=org.id,
        email=f"user-{suffix}@example.com",
        full_name="Actor",
        password_hash=_PW_HASH,
        role="ADMIN",
    )
    db.add(user)
    db.flush()
    connection = IntegrationConnection(
        organization_id=org.id,
        user_id=user.id,
        provider=IntegrationProvider.GOOGLE,
        service=IntegrationServiceEnum.GOOGLE_CALENDAR,
        external_account_id=f"sub-{suffix}",
        account_email=f"cal-{suffix}@example.com",
        granted_scopes_json=["https://www.googleapis.com/auth/calendar.events"],
        access_token_encrypted=b"ciphertext",
        refresh_token_encrypted=b"ciphertext",
        token_expires_at=datetime.now(timezone.utc),
        status=ConnectionStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    action = ActionItem(
        organization_id=org.id,
        title=f"Task {suffix}",
        description="Do the thing",
        status=ActionStatus.OPEN,
        ai_generated=False,
    )
    db.add(action)
    db.flush()
    return connection, action


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    calendar_id=st.sampled_from(_CALENDAR_IDS),
    attempts=st.integers(min_value=1, max_value=6),
    summaries=st.lists(_TEXT, min_size=1, max_size=6),
    all_day=st.booleans(),
)
def test_calendar_create_idempotency(
    db_session: Session,
    calendar_id: str,
    attempts: int,
    summaries: list[str],
    all_day: bool,
) -> None:
    """Repeatedly adding one confirmed action to one calendar yields <=1 link.

    **Validates: Requirements 28.7 / Property 16**
    """

    connection, action = _seed_confirmed_action(db_session)
    org_id = connection.organization_id
    user_id = connection.user_id

    fake = FakeCalendarClient(
        [
            CalendarInfo(
                calendar_id="primary",
                summary="Primary",
                primary=True,
                access_role="owner",
            ),
            CalendarInfo(
                calendar_id="team@group.calendar.google.com",
                summary="Team",
                access_role="writer",
            ),
        ]
    )
    service = CalendarService(
        db_session, fake, integration_service=None, settings=_TEST_SETTINGS
    )

    for i in range(attempts):
        # Each attempt may carry a different edited summary — payload variation
        # must not defeat identity-based idempotency.
        summary = summaries[i % len(summaries)]
        req = CalendarAddRequest(
            connection_id=connection.id,
            google_calendar_id=calendar_id,
            summary=summary or None,
            all_day=all_day,
            all_day_date="2026-10-09",
            start=datetime(2026, 10, 9, 9, tzinfo=timezone.utc),
            end=datetime(2026, 10, 9, 10, tzinfo=timezone.utc),
        )
        service.add_action_to_calendar(org_id, user_id, action.id, req)

    links = list(
        db_session.execute(
            select(CalendarEventLink).where(
                CalendarEventLink.organization_id == org_id,
                CalendarEventLink.action_item_id == action.id,
                CalendarEventLink.integration_connection_id == connection.id,
                CalendarEventLink.google_calendar_id == calendar_id,
            )
        ).scalars()
    )
    # At most one link for the (action, calendar) identity (Property 16).
    assert len(links) <= 1

    # And at most one distinct Google event id was ever minted.
    event_ids = {
        link.google_event_id for link in links if link.google_event_id is not None
    }
    assert len(event_ids) <= 1
    # The fake client created at most one event for this identity.
    assert fake.create_calls <= 1
