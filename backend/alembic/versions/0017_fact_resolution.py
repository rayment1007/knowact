"""Add scoped candidate lineage and comparison replay receipts.

No prior candidate identity, review, or accepted version is rewritten.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0017_fact_resolution"
down_revision = "0016_fact_extraction_runs"
branch_labels = None
depends_on = None


def _common():
    return [sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("workspace_owner_id", sa.UUID(), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def _proposal_fk(column, name):
    return sa.ForeignKeyConstraint([column, "organization_id", "workspace_owner_id"],
        ["fact_proposals.id", "fact_proposals.organization_id", "fact_proposals.workspace_owner_id"], name=name)


def upgrade():
    op.add_column("fact_proposals", sa.Column("comparison_json", postgresql.JSONB(),
        nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.create_unique_constraint("uq_fact_proposal_owner_scope", "fact_proposals",
        ["id", "organization_id", "workspace_owner_id"])
    op.create_table("fact_resolutions", *_common(),
        sa.Column("candidate_id", sa.UUID(), nullable=False),
        sa.Column("candidate_revision", sa.Integer(), nullable=False),
        sa.Column("resolved_proposal_id", sa.UUID(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["workspace_owner_id", "organization_id"], ["users.id", "users.organization_id"], name="fk_fact_resolution_owner"),
        _proposal_fk("candidate_id", "fk_fact_resolution_candidate"),
        _proposal_fk("resolved_proposal_id", "fk_fact_resolution_proposal"),
        sa.UniqueConstraint("organization_id", "workspace_owner_id", "request_key", name="uq_fact_resolution_request"),
        sa.UniqueConstraint("resolved_proposal_id", name="uq_fact_resolution_proposal"),
        sa.CheckConstraint("candidate_revision > 0 AND candidate_id <> resolved_proposal_id", name="ck_fact_resolution_candidate"),
        sa.CheckConstraint("length(request_key) > 0 AND length(request_hash) = 64", name="ck_fact_resolution_request"))
    op.create_index("ix_fact_resolutions_organization_id", "fact_resolutions", ["organization_id"])
    op.create_index("uq_fact_resolution_active", "fact_resolutions", ["candidate_id"], unique=True,
        postgresql_where=sa.text("active = true"))
    op.create_table("fact_comparison_requests", *_common(),
        sa.Column("proposal_id", sa.UUID(), nullable=False),
        sa.Column("proposal_revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_owner_id", "organization_id"], ["users.id", "users.organization_id"], name="fk_fact_comparison_owner"),
        _proposal_fk("proposal_id", "fk_fact_comparison_proposal"),
        sa.UniqueConstraint("organization_id", "workspace_owner_id", "request_key", name="uq_fact_comparison_request"),
        sa.CheckConstraint("proposal_revision > 0", name="ck_fact_comparison_revision"),
        sa.CheckConstraint("length(request_key) > 0 AND length(request_hash) = 64", name="ck_fact_comparison_request"))
    op.create_index("ix_fact_comparison_requests_organization_id", "fact_comparison_requests", ["organization_id"])
    op.execute("""CREATE FUNCTION knowact_guard_fact_resolution() RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Fact resolution history cannot be deleted'; END IF;
      IF (to_jsonb(NEW) - 'active') IS DISTINCT FROM (to_jsonb(OLD) - 'active')
         OR (OLD.active = false AND NEW.active = true) THEN
        RAISE EXCEPTION 'Fact resolution lineage is immutable';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql""")
    op.execute("CREATE TRIGGER guard_fact_resolution BEFORE UPDATE OR DELETE ON fact_resolutions FOR EACH ROW EXECUTE FUNCTION knowact_guard_fact_resolution()")


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS guard_fact_resolution ON fact_resolutions")
    op.execute("DROP FUNCTION IF EXISTS knowact_guard_fact_resolution()")
    op.drop_table("fact_comparison_requests")
    op.drop_table("fact_resolutions")
    op.drop_constraint("uq_fact_proposal_owner_scope", "fact_proposals", type_="unique")
    op.drop_column("fact_proposals", "comparison_json")
