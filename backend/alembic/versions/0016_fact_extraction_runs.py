"""Track bounded source extraction receipts and editable-candidate context.

No legacy knowledge or approvals are converted. Extraction runs are content-
bearing caches, explicitly scoped and attributable to a source origin.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0016_fact_extraction_runs"
down_revision = "0015_structured_fact_memory"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("fact_proposals", sa.Column("extraction_context", postgresql.JSONB(),
        nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.create_table("fact_extraction_runs",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("workspace_owner_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("origin_key", sa.String(64), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="PROCESSING"),
        sa.Column("extractor_version", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("coverage_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("output_json", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("source_proposal_id", sa.UUID(), nullable=True),
        sa.Column("proposal_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("redacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["workspace_owner_id", "organization_id"],
            ["users.id", "users.organization_id"], name="fk_fact_extraction_owner"),
        sa.ForeignKeyConstraint(["project_id", "organization_id", "workspace_owner_id"],
            ["business_entities.id", "business_entities.organization_id", "business_entities.workspace_owner_id"], name="fk_fact_extraction_project"),
        sa.UniqueConstraint("organization_id", "workspace_owner_id", "request_key", name="uq_fact_extraction_request"),
        sa.UniqueConstraint("id", "organization_id", "workspace_owner_id", name="uq_fact_extraction_scope"),
        sa.UniqueConstraint("cache_key", "generation", name="uq_fact_extraction_generation"),
        sa.CheckConstraint("source_kind IN ('source','email','calendar','file')", name="ck_fact_extraction_kind"),
        sa.CheckConstraint("status IN ('PROCESSING','SUCCEEDED','FAILED')", name="ck_fact_extraction_status"),
        sa.CheckConstraint("generation > 0", name="ck_fact_extraction_generation"),
        sa.CheckConstraint("length(origin_key) = 64 AND length(source_fingerprint) = 64 AND length(cache_key) = 64", name="ck_fact_extraction_hashes"),
        sa.CheckConstraint("length(request_key) > 0 AND length(request_hash) = 64", name="ck_fact_extraction_request"),
        sa.CheckConstraint("redacted_at IS NULL OR output_json IS NULL", name="ck_fact_extraction_redaction"),
        sa.CheckConstraint("status <> 'FAILED' OR error_code IS NOT NULL", name="ck_fact_extraction_error"),
    )
    op.create_index("ix_fact_extraction_runs_organization_id", "fact_extraction_runs", ["organization_id"])
    op.create_index("ix_fact_extraction_origin", "fact_extraction_runs", ["organization_id", "workspace_owner_id", "origin_key"])
    op.create_index("ix_fact_extraction_cache", "fact_extraction_runs", ["organization_id", "workspace_owner_id", "cache_key", "status"])
    op.create_table("fact_extraction_requests",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("workspace_owner_id", sa.UUID(), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("extraction_run_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["workspace_owner_id", "organization_id"],
            ["users.id", "users.organization_id"], name="fk_fact_extraction_receipt_owner"),
        sa.ForeignKeyConstraint(["extraction_run_id", "organization_id", "workspace_owner_id"],
            ["fact_extraction_runs.id", "fact_extraction_runs.organization_id", "fact_extraction_runs.workspace_owner_id"],
            name="fk_fact_extraction_receipt_run"),
        sa.UniqueConstraint("organization_id", "workspace_owner_id", "request_key", name="uq_fact_extraction_receipt_request"),
        sa.CheckConstraint("length(request_key) > 0 AND length(request_hash) = 64", name="ck_fact_extraction_receipt_request"),
    )
    op.create_index("ix_fact_extraction_requests_organization_id", "fact_extraction_requests", ["organization_id"])


def downgrade():
    op.drop_table("fact_extraction_requests")
    op.drop_table("fact_extraction_runs")
    op.drop_column("fact_proposals", "extraction_context")
