"""Read-only primary-calendar source snapshots.

Revision ID: 0011_calendar_sources
Revises: 0010_document_hardening
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0011_calendar_sources"
down_revision = "0010_document_hardening"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "calendar_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("integration_connection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("integration_connections.id"), nullable=False),
        sa.Column("google_event_id", sa.String(1024), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.String(80), nullable=False),
        sa.Column("ends_at", sa.String(80), nullable=False),
        sa.Column("html_link", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("integration_connection_id", "google_event_id", name="uq_calendar_source_event"),
    )
    op.create_index("ix_calendar_sources_organization_id", "calendar_sources", ["organization_id"])
    op.create_index("ix_calendar_sources_org_connection", "calendar_sources", ["organization_id", "integration_connection_id"])


def downgrade():
    op.drop_table("calendar_sources")
