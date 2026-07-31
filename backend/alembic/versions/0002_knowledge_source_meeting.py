"""Per-meeting client memory: knowledge_items.source_meeting_id + partial unique index.

Adds the nullable ``source_meeting_id`` column to ``knowledge_items`` (a plain
UUID with no hard FK to any meetings table, mirroring
``action_items.source_meeting_id`` to avoid a cross-module dependency) and a
*partial* unique index on ``(organization_id, source_meeting_id,
knowledge_type)`` restricted to rows where ``source_meeting_id IS NOT NULL``.

This makes meeting-confirm client memory per-meeting (model A): each confirmed
meeting owns exactly one ``MEMORY`` knowledge item, so history is preserved
across meetings, and the partial unique index is a DB-level backstop against
duplicate rows under concurrent confirms. Rows with a NULL ``source_meeting_id``
(all non-meeting knowledge) are unconstrained.

Revision ID: 0002_knowledge_source_meeting
Revises: 0001_initial_schema
Create Date: follow-on
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_knowledge_source_meeting"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_items",
        sa.Column(
            "source_meeting_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    # Partial unique index: at most one meeting-sourced knowledge item per
    # (organization, meeting, type). Only constrains rows with a non-null
    # source_meeting_id, leaving all non-meeting knowledge unconstrained.
    op.create_index(
        "uq_knowledge_items_meeting_memory",
        "knowledge_items",
        ["organization_id", "source_meeting_id", "knowledge_type"],
        unique=True,
        postgresql_where=sa.text("source_meeting_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_knowledge_items_meeting_memory", table_name="knowledge_items"
    )
    op.drop_column("knowledge_items", "source_meeting_id")
