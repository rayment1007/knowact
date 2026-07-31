"""Initial schema: core engine.

Creates every core table, all native PostgreSQL enum types, the mixin
``organization_id`` indexes, the composite ``(organization_id, status)``
indexes, and the partial unique index enforcing at most one non-rejected
classification per source item.

Revision ID: 0001_initial_schema
Revises:
Create Date: initial
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Native PostgreSQL enum types (created once, shared across tables).
# ``create_type=False`` on the column references below prevents CREATE TABLE
# from trying to (re)create these types; we create/drop them explicitly.
# ---------------------------------------------------------------------------
business_entity_type = postgresql.ENUM(
    "CLIENT",
    "PROJECT",
    "DEPARTMENT",
    "VENDOR",
    "PARTNER",
    "ACCOUNT",
    "PROCESS",
    name="business_entity_type",
    create_type=False,
)
source_type = postgresql.ENUM(
    "EMAIL",
    "DOCUMENT",
    "MEETING_NOTE",
    "TASK_NOTE",
    "CHAT",
    "MANUAL",
    name="source_type",
    create_type=False,
)
source_status = postgresql.ENUM(
    "NEW",
    "CLASSIFIED",
    "PROCESSED",
    "ARCHIVED",
    "DISMISSED",
    name="source_status",
    create_type=False,
)
relevance = postgresql.ENUM(
    "WORK_RELATED",
    "PERSONAL",
    "IRRELEVANT",
    "SPAM",
    "SYSTEM_NOTIFICATION",
    name="relevance",
    create_type=False,
)
business_category = postgresql.ENUM(
    "CLIENT",
    "PROJECT",
    "DECISION",
    "TASK",
    "RISK",
    "MEETING",
    "PARTNER",
    "LEARNING",
    "OTHER",
    name="business_category",
    create_type=False,
)
sensitivity = postgresql.ENUM(
    "PUBLIC",
    "INTERNAL",
    "CONFIDENTIAL",
    "HIGHLY_SENSITIVE",
    name="sensitivity",
    create_type=False,
)
suggestion_status = postgresql.ENUM(
    "SUGGESTED",
    "CONFIRMED",
    "REJECTED",
    name="suggestion_status",
    create_type=False,
)
action_status = postgresql.ENUM(
    "OPEN",
    "IN_PROGRESS",
    "DONE",
    "CANCELLED",
    name="action_status",
    create_type=False,
)

_ALL_ENUMS = (
    business_entity_type,
    source_type,
    source_status,
    relevance,
    business_category,
    sensitivity,
    suggestion_status,
    action_status,
)


def upgrade() -> None:
    bind = op.get_bind()

    # Create all enum types first so tables can reference them.
    for enum in _ALL_ENUMS:
        enum.create(bind, checkfirst=True)

    # -- organizations (tenant root) ----------------------------------------
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # -- business_entities --------------------------------------------------
    op.create_table(
        "business_entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", business_entity_type, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
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
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_business_entities_organization_id",
        "business_entities",
        ["organization_id"],
    )

    # -- users --------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_users_organization_id", "users", ["organization_id"]
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # -- audit_logs ---------------------------------------------------------
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_logs_organization_id", "audit_logs", ["organization_id"]
    )

    # -- briefs -------------------------------------------------------------
    op.create_table(
        "briefs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("scope_ref_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "generated_for", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "content",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["generated_for"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_briefs_organization_id", "briefs", ["organization_id"]
    )

    # -- decision_records ---------------------------------------------------
    op.create_table(
        "decision_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "business_entity_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=False),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["business_entity_id"], ["business_entities.id"]
        ),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_decision_records_organization_id",
        "decision_records",
        ["organization_id"],
    )

    # -- source_items -------------------------------------------------------
    op.create_table(
        "source_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", source_type, nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", source_status, nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_source_items_org_status",
        "source_items",
        ["organization_id", "status"],
    )
    op.create_index(
        "ix_source_items_organization_id",
        "source_items",
        ["organization_id"],
    )

    # -- classification_results (tenant derived via parent source_item) -----
    op.create_table(
        "classification_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source_item_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("relevance", relevance, nullable=False),
        sa.Column("business_category", business_category, nullable=False),
        sa.Column("sensitivity", sensitivity, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "evidence_spans",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", suggestion_status, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_item_id"], ["source_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # Partial unique index: at most one non-rejected classification per source
    # item (Requirement 4.5); rejected rows are retained as negative signals.
    op.create_index(
        "uq_classification_active_per_source",
        "classification_results",
        ["source_item_id"],
        unique=True,
        postgresql_where=sa.text("status != 'REJECTED'"),
    )

    # -- knowledge_items ----------------------------------------------------
    op.create_table(
        "knowledge_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "business_entity_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "source_item_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "key_points",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("evidence_text", sa.Text(), nullable=False),
        sa.Column("knowledge_type", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["business_entity_id"], ["business_entities.id"]
        ),
        sa.ForeignKeyConstraint(["source_item_id"], ["source_items.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_items_org_status",
        "knowledge_items",
        ["organization_id", "status"],
    )
    op.create_index(
        "ix_knowledge_items_organization_id",
        "knowledge_items",
        ["organization_id"],
    )

    # -- action_items -------------------------------------------------------
    # ``source_meeting_id`` is a plain nullable UUID with no foreign key: it
    # records the meeting an action was promoted from without coupling the core
    # schema to any optional module that owns meeting records.
    op.create_table(
        "action_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "business_entity_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "knowledge_item_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "source_meeting_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", action_status, nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=True),
        sa.Column(
            "ai_generated",
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
        sa.ForeignKeyConstraint(
            ["business_entity_id"], ["business_entities.id"]
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_item_id"], ["knowledge_items.id"]
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_action_items_org_status",
        "action_items",
        ["organization_id", "status"],
    )
    op.create_index(
        "ix_action_items_organization_id",
        "action_items",
        ["organization_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Drop tables in reverse dependency order.
    op.drop_index("ix_action_items_organization_id", table_name="action_items")
    op.drop_index("ix_action_items_org_status", table_name="action_items")
    op.drop_table("action_items")

    op.drop_index(
        "ix_knowledge_items_organization_id", table_name="knowledge_items"
    )
    op.drop_index(
        "ix_knowledge_items_org_status", table_name="knowledge_items"
    )
    op.drop_table("knowledge_items")

    op.drop_index(
        "uq_classification_active_per_source",
        table_name="classification_results",
    )
    op.drop_table("classification_results")

    op.drop_index(
        "ix_source_items_organization_id", table_name="source_items"
    )
    op.drop_index("ix_source_items_org_status", table_name="source_items")
    op.drop_table("source_items")

    op.drop_index(
        "ix_decision_records_organization_id", table_name="decision_records"
    )
    op.drop_table("decision_records")

    op.drop_index("ix_briefs_organization_id", table_name="briefs")
    op.drop_table("briefs")

    op.drop_index("ix_audit_logs_organization_id", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_organization_id", table_name="users")
    op.drop_table("users")

    op.drop_index(
        "ix_business_entities_organization_id",
        table_name="business_entities",
    )
    op.drop_table("business_entities")

    op.drop_table("organizations")

    # Finally drop the enum types.
    for enum in reversed(_ALL_ENUMS):
        enum.drop(bind, checkfirst=True)
