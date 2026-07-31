"""Connected Workspace Intelligence — AI-assisted Gmail drafts (M6.6).

Creates the M6.6 ``email_drafts`` table that backs the AI-assisted Gmail draft
lifecycle (Requirement 32): a structured suggested reply grounded in
permission-filtered confirmed context, its human-review status, backend-resolved
recipients, and the external Gmail draft/sent ids once materialized and sent.

Idempotent send (Requirement 32.8 / Property 17)
------------------------------------------------
``send_idempotency_key`` is a stable per-draft key passed to Gmail and used
together with an atomic status compare-and-set into ``SENDING`` so a
retried/concurrent send never produces a second sent message.
``gmail_sent_message_id`` is written exactly once, on the winning send.

Migration ordering note
------------------------
Chains off the ACTUAL current head, ``0007_pgvector_and_documents``, taking the
next real number ``0008``.

Portability
-----------
The automated tests run on in-memory SQLite via ``Base.metadata.create_all``
(never through this migration); production runs on PostgreSQL. The JSONB columns
and the new ``email_draft_status`` enum are created here for PostgreSQL.

The reversible downgrade drops the table and the ``email_draft_status`` enum.

Revision ID: 0008_email_drafts
Revises: 0007_pgvector_and_documents
Create Date: M6.6
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_email_drafts"
down_revision: str | None = "0007_pgvector_and_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# New enum for the email-draft lifecycle.
email_draft_status = postgresql.ENUM(
    "AI_SUGGESTED",
    "USER_APPROVED",
    "GMAIL_DRAFT_CREATED",
    "SENDING",
    "SENT",
    "REJECTED",
    "FAILED",
    name="email_draft_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    email_draft_status.create(bind, checkfirst=True)

    op.create_table(
        "email_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "integration_connection_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "source_item_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "business_entity_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("gmail_thread_id", sa.String(length=255), nullable=True),
        sa.Column(
            "to_recipients_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("subject", sa.String(length=1024), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("tone", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=255), nullable=False),
        sa.Column(
            "referenced_facts_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "warnings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", email_draft_status, nullable=False),
        sa.Column("gmail_draft_id", sa.String(length=255), nullable=True),
        sa.Column(
            "gmail_sent_message_id", sa.String(length=255), nullable=True
        ),
        sa.Column(
            "send_idempotency_key", sa.String(length=128), nullable=False
        ),
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
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["integration_connection_id"], ["integration_connections.id"]
        ),
        sa.ForeignKeyConstraint(["source_item_id"], ["source_items.id"]),
        sa.ForeignKeyConstraint(
            ["business_entity_id"], ["business_entities.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_email_drafts_organization_id",
        "email_drafts",
        ["organization_id"],
    )
    op.create_index(
        "ix_email_drafts_user_id",
        "email_drafts",
        ["user_id"],
    )
    op.create_index(
        "ix_email_drafts_integration_connection_id",
        "email_drafts",
        ["integration_connection_id"],
    )
    op.create_index(
        "ix_email_drafts_org_status",
        "email_drafts",
        ["organization_id", "status"],
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index("ix_email_drafts_org_status", table_name="email_drafts")
    op.drop_index(
        "ix_email_drafts_integration_connection_id", table_name="email_drafts"
    )
    op.drop_index("ix_email_drafts_user_id", table_name="email_drafts")
    op.drop_index(
        "ix_email_drafts_organization_id", table_name="email_drafts"
    )
    op.drop_table("email_drafts")

    email_draft_status.drop(bind, checkfirst=True)
