"""Connected Workspace Intelligence — Calendar event links (M6.3).

Creates the M6.3 calendar table:

* ``calendar_event_links`` — the bridge between a confirmed
  :class:`~app.core.models.ActionItem` and a Google Calendar event, recording
  the Google calendar/event ids, a stable ``idempotency_key`` and the current
  ``sync_status``. The unique constraint on ``(organization_id,
  action_item_id, integration_connection_id, google_calendar_id)`` is the
  structural guard behind calendar-create idempotency (Requirement 28.7 /
  Property 16).

Migration ordering note
------------------------
The design's provisional label chains off a not-yet-existent revision. This
revision therefore chains off the ACTUAL current head,
``0005_email_message_records``, taking the next real number ``0006``.

Only the new ``calendar_sync_status`` enum is created/dropped by this revision.

Revision ID: 0006_calendar_event_links
Revises: 0005_email_message_records
Create Date: M6.3
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_calendar_event_links"
down_revision: str | None = "0005_email_message_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# New enum for calendar event link sync state.
calendar_sync_status = postgresql.ENUM(
    "PENDING",
    "SYNCED",
    "UPDATE_PENDING",
    "CANCEL_PENDING",
    "CANCELLED",
    "FAILED",
    name="calendar_sync_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    calendar_sync_status.create(bind, checkfirst=True)

    op.create_table(
        "calendar_event_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "integration_connection_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("google_calendar_id", sa.String(length=320), nullable=False),
        sa.Column("google_event_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("sync_status", calendar_sync_status, nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["action_item_id"], ["action_items.id"]),
        sa.ForeignKeyConstraint(
            ["integration_connection_id"], ["integration_connections.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "action_item_id",
            "integration_connection_id",
            "google_calendar_id",
            name="uq_calendar_event_link_identity",
        ),
    )
    op.create_index(
        "ix_calendar_event_links_organization_id",
        "calendar_event_links",
        ["organization_id"],
    )
    op.create_index(
        "ix_calendar_event_links_user_id",
        "calendar_event_links",
        ["user_id"],
    )
    op.create_index(
        "ix_calendar_event_links_integration_connection_id",
        "calendar_event_links",
        ["integration_connection_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index(
        "ix_calendar_event_links_integration_connection_id",
        table_name="calendar_event_links",
    )
    op.drop_index(
        "ix_calendar_event_links_user_id",
        table_name="calendar_event_links",
    )
    op.drop_index(
        "ix_calendar_event_links_organization_id",
        table_name="calendar_event_links",
    )
    op.drop_table("calendar_event_links")

    calendar_sync_status.drop(bind, checkfirst=True)
