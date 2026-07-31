"""Connected Workspace Intelligence — privacy retention & answer provenance (M6.7).

Creates the two M6.7 tables that back Privacy, Control & Deletion (Requirement
33):

* ``raw_email_retention_policies`` — a user's persisted raw-email retention
  choice (``RAW_AND_EXTRACTED`` vs ``EXTRACTED_ONLY`` plus an optional
  retention window), applied to whether raw message content is retained
  (Requirement 33.5).
* ``copilot_answer_logs`` — a persisted Copilot answer plus the exact citations
  returned and the full bounded evidence set that grounded it, so the
  Privacy_Service can answer "what data was used for an AI answer"
  (Requirement 33.7).

Migration ordering note
------------------------
Chains off the ACTUAL current head, ``0008_email_drafts``, taking the next real
number ``0009``.

Portability
-----------
The automated tests run on in-memory SQLite via ``Base.metadata.create_all``
(never through this migration); production runs on PostgreSQL. The JSONB columns
and the new ``raw_email_retention_mode`` enum are created here for PostgreSQL.

The reversible downgrade drops both tables and the new enum.

Revision ID: 0009_privacy_retention
Revises: 0008_email_drafts
Create Date: M6.7
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic. Kept <= 32 chars to fit the
# ``alembic_version.version_num`` column.
revision: str = "0009_privacy_retention"
down_revision: str | None = "0008_email_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# New enum for the raw-email retention policy mode.
raw_email_retention_mode = postgresql.ENUM(
    "RAW_AND_EXTRACTED",
    "EXTRACTED_ONLY",
    name="raw_email_retention_mode",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    raw_email_retention_mode.create(bind, checkfirst=True)

    op.create_table(
        "raw_email_retention_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", raw_email_retention_mode, nullable=False),
        sa.Column("retention_window_days", sa.Integer(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_raw_email_retention_policy_org_user",
        ),
    )
    op.create_index(
        "ix_raw_email_retention_policies_organization_id",
        "raw_email_retention_policies",
        ["organization_id"],
    )
    op.create_index(
        "ix_raw_email_retention_policies_user_id",
        "raw_email_retention_policies",
        ["user_id"],
    )

    op.create_table(
        "copilot_answer_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("intent", sa.String(length=16), nullable=False),
        sa.Column(
            "insufficient_evidence",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "citations_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "evidence_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_copilot_answer_logs_organization_id",
        "copilot_answer_logs",
        ["organization_id"],
    )
    op.create_index(
        "ix_copilot_answer_logs_user_id",
        "copilot_answer_logs",
        ["user_id"],
    )
    op.create_index(
        "ix_copilot_answer_logs_org_created",
        "copilot_answer_logs",
        ["organization_id", "created_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index(
        "ix_copilot_answer_logs_org_created", table_name="copilot_answer_logs"
    )
    op.drop_index(
        "ix_copilot_answer_logs_user_id", table_name="copilot_answer_logs"
    )
    op.drop_index(
        "ix_copilot_answer_logs_organization_id",
        table_name="copilot_answer_logs",
    )
    op.drop_table("copilot_answer_logs")

    op.drop_index(
        "ix_raw_email_retention_policies_user_id",
        table_name="raw_email_retention_policies",
    )
    op.drop_index(
        "ix_raw_email_retention_policies_organization_id",
        table_name="raw_email_retention_policies",
    )
    op.drop_table("raw_email_retention_policies")

    raw_email_retention_mode.drop(bind, checkfirst=True)
