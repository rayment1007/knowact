"""Owner-requested drafts and approved source provenance.

Revision ID: 0012_source_proposals
Revises: 0011_calendar_sources
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012_source_proposals"
down_revision = "0011_calendar_sources"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("source_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_title", sa.Text(), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("target", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=False),
        sa.Column("analysis_truncated", sa.Boolean(), nullable=False),
        sa.Column("sensitivity_acknowledged", sa.Boolean(), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("result_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("active_key", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("active_key", name="uq_source_proposals_active_key"),
    )
    op.create_index("ix_source_proposals_organization_id", "source_proposals", ["organization_id"])
    op.create_index("ix_source_proposals_owner_status", "source_proposals", ["organization_id", "user_id", "status"])
    op.create_index("ix_source_proposals_origin", "source_proposals", ["source_kind", "source_id"])
    op.create_index("ix_source_proposals_result", "source_proposals", ["target", "result_id"])


def downgrade():
    op.drop_table("source_proposals")
