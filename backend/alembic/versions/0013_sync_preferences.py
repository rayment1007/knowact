"""User sync windows and persistent email deletion markers."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013_sync_preferences"
down_revision = "0012_source_proposals"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("calendar_event_links", sa.Column("pending_event_json", postgresql.JSONB(), nullable=True))
    op.add_column("calendar_sources", sa.Column("google_calendar_id", sa.String(320), nullable=False, server_default="primary"))
    op.create_table("sync_preferences",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("email_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("calendar_past_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("calendar_future_days", sa.Integer(), nullable=False, server_default="90"))
    op.create_index("ix_sync_preferences_organization_id", "sync_preferences", ["organization_id"])
    op.create_table("sync_exclusions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("account_email", sa.String(320), nullable=False),
        sa.Column("external_id_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("user_id", "account_email", "external_id_hash", name="uq_sync_exclusion_identity"))
    op.create_index("ix_sync_exclusions_organization_id", "sync_exclusions", ["organization_id"])


def downgrade():
    op.drop_column("calendar_event_links", "pending_event_json")
    op.drop_column("calendar_sources", "google_calendar_id")
    op.drop_table("sync_exclusions")
    op.drop_table("sync_preferences")
