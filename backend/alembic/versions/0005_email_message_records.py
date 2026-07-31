"""Connected Workspace Intelligence — Gmail ingestion tables (M6.2).

Creates the M6.2 Gmail-ingestion tables:

* ``email_message_records`` — metadata + provenance for an ingested Gmail
  message (raw binaries are never stored). Unique
  ``(organization_id, integration_connection_id, gmail_message_id)`` and a
  ``(organization_id, content_hash)`` dedup index back Property 15.
* ``email_task_suggestions`` — AI-extracted task suggestions in status
  ``SUGGESTED`` carrying evidence + AI provider/model metadata (Req 27.6).
* ``email_sender_signals`` — negative signals marking a sender/domain
  ``PERSONAL``/``IRRELEVANT`` (Req 27.7), one active per ``(org, pattern)``.

Migration ordering note
------------------------
The design's provisional "0006" label chains off a not-yet-existent revision.
This revision therefore chains off the ACTUAL current head,
``0004_integration_connections``, taking the next real number ``0005``.

The ``suggestion_status`` enum already exists (created in ``0001``), so it is
referenced with ``create_type=False`` and is neither created nor dropped here.
Only the new ``sender_signal_type`` enum is created/dropped by this revision.

Revision ID: 0005_email_message_records
Revises: 0004_integration_connections
Create Date: M6.2
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_email_message_records"
down_revision: str | None = "0004_integration_connections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Existing enum (created in 0001) — referenced only, never (re)created here.
suggestion_status = postgresql.ENUM(
    "SUGGESTED",
    "CONFIRMED",
    "REJECTED",
    name="suggestion_status",
    create_type=False,
)

# New enum for sender/domain negative signals.
sender_signal_type = postgresql.ENUM(
    "PERSONAL",
    "IRRELEVANT",
    name="sender_signal_type",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    sender_signal_type.create(bind, checkfirst=True)

    # -- email_message_records ---------------------------------------------
    op.create_table(
        "email_message_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "integration_connection_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("source_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("gmail_message_id", sa.String(length=255), nullable=False),
        sa.Column("gmail_thread_id", sa.String(length=255), nullable=False),
        sa.Column("sender", sa.String(length=320), nullable=False),
        sa.Column(
            "recipients_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("subject", sa.String(length=1024), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "labels_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "has_attachments",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "stored_raw",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["integration_connection_id"], ["integration_connections.id"]
        ),
        sa.ForeignKeyConstraint(["source_item_id"], ["source_items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "integration_connection_id",
            "gmail_message_id",
            name="uq_email_message_connection_gmail_id",
        ),
    )
    op.create_index(
        "ix_email_message_records_organization_id",
        "email_message_records",
        ["organization_id"],
    )
    op.create_index(
        "ix_email_message_records_integration_connection_id",
        "email_message_records",
        ["integration_connection_id"],
    )
    op.create_index(
        "ix_email_message_records_org_content_hash",
        "email_message_records",
        ["organization_id", "content_hash"],
    )

    # -- email_task_suggestions --------------------------------------------
    op.create_table(
        "email_task_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "email_message_record_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("source_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("business_entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("suggested_due_date", sa.Date(), nullable=True),
        sa.Column("suggested_owner", sa.String(length=320), nullable=True),
        sa.Column("related_entity_name", sa.String(length=255), nullable=True),
        sa.Column("gmail_message_id", sa.String(length=255), nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=False),
        sa.Column("ai_provider", sa.String(length=64), nullable=False),
        sa.Column("ai_model", sa.String(length=128), nullable=False),
        sa.Column("status", suggestion_status, nullable=False),
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
        sa.ForeignKeyConstraint(
            ["email_message_record_id"], ["email_message_records.id"]
        ),
        sa.ForeignKeyConstraint(["source_item_id"], ["source_items.id"]),
        sa.ForeignKeyConstraint(["business_entity_id"], ["business_entities.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_email_task_suggestions_organization_id",
        "email_task_suggestions",
        ["organization_id"],
    )
    op.create_index(
        "ix_email_task_suggestions_email_message_record_id",
        "email_task_suggestions",
        ["email_message_record_id"],
    )
    op.create_index(
        "ix_email_task_suggestions_org_status",
        "email_task_suggestions",
        ["organization_id", "status"],
    )

    # -- email_sender_signals ----------------------------------------------
    op.create_table(
        "email_sender_signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "integration_connection_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pattern", sa.String(length=320), nullable=False),
        sa.Column("signal_type", sender_signal_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["integration_connection_id"], ["integration_connections.id"]
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "pattern",
            name="uq_email_sender_signal_org_pattern",
        ),
    )
    op.create_index(
        "ix_email_sender_signals_organization_id",
        "email_sender_signals",
        ["organization_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index(
        "ix_email_sender_signals_organization_id",
        table_name="email_sender_signals",
    )
    op.drop_table("email_sender_signals")

    op.drop_index(
        "ix_email_task_suggestions_org_status",
        table_name="email_task_suggestions",
    )
    op.drop_index(
        "ix_email_task_suggestions_email_message_record_id",
        table_name="email_task_suggestions",
    )
    op.drop_index(
        "ix_email_task_suggestions_organization_id",
        table_name="email_task_suggestions",
    )
    op.drop_table("email_task_suggestions")

    op.drop_index(
        "ix_email_message_records_org_content_hash",
        table_name="email_message_records",
    )
    op.drop_index(
        "ix_email_message_records_integration_connection_id",
        table_name="email_message_records",
    )
    op.drop_index(
        "ix_email_message_records_organization_id",
        table_name="email_message_records",
    )
    op.drop_table("email_message_records")

    sender_signal_type.drop(bind, checkfirst=True)
