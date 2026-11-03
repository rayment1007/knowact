"""Add scoped versioned fact memory without converting legacy knowledge.

The new tables start empty. A reviewed version is immutable except for its
CURRENT -> SUPERSEDED transition and an explicit transaction-local privacy
redaction. Source snapshots store identity/metadata, never another full body.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015_structured_fact_memory"
down_revision = "0014_personal_workspace_owners"
branch_labels = None
depends_on = None


_IDENTITY_GUARD_PG = """
CREATE OR REPLACE FUNCTION knowact_guard_fact_identity() RETURNS trigger AS $$
BEGIN
 IF NEW IS DISTINCT FROM OLD THEN
   RAISE EXCEPTION 'Fact identity is immutable';
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql
"""

_VERSION_GUARD_PG = """
CREATE OR REPLACE FUNCTION knowact_guard_fact_version() RETURNS trigger AS $$
BEGIN
 IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Accepted fact history cannot be deleted'; END IF;
 IF NEW.id IS DISTINCT FROM OLD.id OR NEW.identity_id IS DISTINCT FROM OLD.identity_id
 OR NEW.organization_id IS DISTINCT FROM OLD.organization_id OR NEW.workspace_owner_id IS DISTINCT FROM OLD.workspace_owner_id
 OR NEW.version_number IS DISTINCT FROM OLD.version_number OR NEW.predecessor_id IS DISTINCT FROM OLD.predecessor_id
 OR NEW.approved_by IS DISTINCT FROM OLD.approved_by OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
 OR NEW.normalization_version IS DISTINCT FROM OLD.normalization_version THEN
   RAISE EXCEPTION 'Accepted fact identity and review metadata are immutable';
 END IF;
 IF NEW.value_json IS DISTINCT FROM OLD.value_json OR NEW.redacted_at IS DISTINCT FROM OLD.redacted_at THEN
   IF NOT (OLD.redacted_at IS NULL AND NEW.redacted_at IS NOT NULL AND NEW.value_json IS NULL
      AND COALESCE(current_setting('knowact.allow_fact_redaction', true), '') = 'on') THEN
     RAISE EXCEPTION 'Accepted fact values are immutable';
   END IF;
 END IF;
 IF NEW.status IS DISTINCT FROM OLD.status AND NOT (OLD.status='CURRENT' AND NEW.status='SUPERSEDED') THEN
   RAISE EXCEPTION 'Accepted fact status cannot be restored';
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql
"""

_REVIEW_GUARD_PG = """
CREATE OR REPLACE FUNCTION knowact_guard_fact_review() RETURNS trigger AS $$
BEGIN
 IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Fact review history cannot be deleted'; END IF;
 IF NEW.id IS DISTINCT FROM OLD.id OR NEW.identity_id IS DISTINCT FROM OLD.identity_id
 OR NEW.organization_id IS DISTINCT FROM OLD.organization_id OR NEW.workspace_owner_id IS DISTINCT FROM OLD.workspace_owner_id
 OR NEW.proposal_id IS DISTINCT FROM OLD.proposal_id OR NEW.proposal_revision IS DISTINCT FROM OLD.proposal_revision
 OR NEW.decision IS DISTINCT FROM OLD.decision OR NEW.request_key IS DISTINCT FROM OLD.request_key
 OR NEW.request_hash IS DISTINCT FROM OLD.request_hash OR NEW.result_version_id IS DISTINCT FROM OLD.result_version_id
 OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
   RAISE EXCEPTION 'Fact review history is immutable';
 END IF;
 IF NEW.detail IS DISTINCT FROM OLD.detail OR NEW.redacted_at IS DISTINCT FROM OLD.redacted_at THEN
   IF NOT (OLD.redacted_at IS NULL AND NEW.redacted_at IS NOT NULL AND NEW.detail = '{}'::jsonb
      AND COALESCE(current_setting('knowact.allow_fact_redaction', true), '') = 'on') THEN
     RAISE EXCEPTION 'Fact review content can only be explicitly redacted';
   END IF;
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql
"""


def upgrade():
    op.create_unique_constraint('uq_users_id_org', 'users', ['id', 'organization_id'])
    op.create_unique_constraint('uq_entities_id_org_owner', 'business_entities', ['id', 'organization_id', 'workspace_owner_id'])
    op.create_table('fact_identities',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_owner_id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('fact_type', sa.String(length=32), nullable=False),
    sa.Column('canonical_key', sa.String(length=160), nullable=False),
    sa.Column('occurrence_key', sa.String(length=160), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.CheckConstraint("fact_type IN ('DEADLINE','MEETING_TIME','PROJECT_STATUS','DECISION','RESPONSIBLE_PERSON')", name='ck_fact_identity_type'),
    sa.CheckConstraint('length(canonical_key) > 0', name='ck_fact_identity_key'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['project_id', 'organization_id', 'workspace_owner_id'], ['business_entities.id', 'business_entities.organization_id', 'business_entities.workspace_owner_id'], name='fk_fact_identity_project'),
    sa.ForeignKeyConstraint(['workspace_owner_id', 'organization_id'], ['users.id', 'users.organization_id'], name='fk_fact_identity_owner'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'organization_id', 'workspace_owner_id', name='uq_fact_identity_scope'),
    sa.UniqueConstraint('organization_id', 'workspace_owner_id', 'project_id', 'fact_type', 'canonical_key', 'occurrence_key', name='uq_fact_identity_key')
    )
    op.create_index(op.f('ix_fact_identities_organization_id'), 'fact_identities', ['organization_id'], unique=False)
    op.create_table('fact_source_snapshots',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_owner_id', sa.UUID(), nullable=False),
    sa.Column('source_kind', sa.String(length=16), nullable=False),
    sa.Column('source_id', sa.UUID(), nullable=False),
    sa.Column('origin_key', sa.String(length=64), nullable=False),
    sa.Column('source_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('source_title', sa.Text(), nullable=True),
    sa.Column('source_time', sa.DateTime(timezone=True), nullable=True),
    sa.Column('captured_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('available', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('purged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.CheckConstraint("purged_at IS NULL OR (available = false AND (source_title IS NULL OR source_title = ''))", name='ck_fact_snapshot_purged'),
    sa.CheckConstraint("source_kind IN ('source','email','calendar','file')", name='ck_fact_snapshot_kind'),
    sa.CheckConstraint('length(origin_key) = 64 AND length(source_fingerprint) = 64', name='ck_fact_snapshot_hash'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['workspace_owner_id', 'organization_id'], ['users.id', 'users.organization_id'], name='fk_fact_snapshot_owner'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'organization_id', 'workspace_owner_id', name='uq_fact_snapshot_scope'),
    sa.UniqueConstraint('organization_id', 'workspace_owner_id', 'source_kind', 'source_id', 'source_fingerprint', name='uq_fact_snapshot_revision')
    )
    op.create_index('ix_fact_snapshot_origin', 'fact_source_snapshots', ['organization_id', 'workspace_owner_id', 'origin_key'], unique=False)
    op.create_index(op.f('ix_fact_source_snapshots_organization_id'), 'fact_source_snapshots', ['organization_id'], unique=False)
    op.create_table('fact_evidence',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_owner_id', sa.UUID(), nullable=False),
    sa.Column('snapshot_id', sa.UUID(), nullable=False),
    sa.Column('excerpt', sa.Text(), nullable=True),
    sa.Column('excerpt_hash', sa.String(length=64), nullable=False),
    sa.Column('locator_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('redacted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.CheckConstraint('(redacted_at IS NULL AND excerpt IS NOT NULL AND length(excerpt) > 0) OR (redacted_at IS NOT NULL AND excerpt IS NULL)', name='ck_fact_evidence_redaction'),
    sa.CheckConstraint('length(excerpt_hash) = 64', name='ck_fact_evidence_hash'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['snapshot_id', 'organization_id', 'workspace_owner_id'], ['fact_source_snapshots.id', 'fact_source_snapshots.organization_id', 'fact_source_snapshots.workspace_owner_id'], name='fk_fact_evidence_snapshot'),
    sa.ForeignKeyConstraint(['workspace_owner_id', 'organization_id'], ['users.id', 'users.organization_id'], name='fk_fact_evidence_owner'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'organization_id', 'workspace_owner_id', name='uq_fact_evidence_scope'),
    sa.UniqueConstraint('snapshot_id', 'excerpt_hash', name='uq_fact_evidence_excerpt')
    )
    op.create_index(op.f('ix_fact_evidence_organization_id'), 'fact_evidence', ['organization_id'], unique=False)
    op.create_table('fact_versions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_owner_id', sa.UUID(), nullable=False),
    sa.Column('identity_id', sa.UUID(), nullable=False),
    sa.Column('version_number', sa.Integer(), nullable=False),
    sa.Column('value_json', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
    sa.Column('normalization_version', sa.String(length=32), server_default='M1_DATE_V1', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='CURRENT', nullable=False),
    sa.Column('predecessor_id', sa.UUID(), nullable=True),
    sa.Column('approved_by', sa.UUID(), nullable=False),
    sa.Column('approved_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('redacted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.CheckConstraint("status IN ('CURRENT','SUPERSEDED')", name='ck_fact_version_status'),
    sa.CheckConstraint('(redacted_at IS NULL AND value_json IS NOT NULL) OR (redacted_at IS NOT NULL AND value_json IS NULL)', name='ck_fact_version_redaction'),
    sa.CheckConstraint('approved_by = workspace_owner_id', name='ck_fact_version_reviewer'),
    sa.CheckConstraint('predecessor_id IS NULL OR predecessor_id <> id', name='ck_fact_version_predecessor'),
    sa.CheckConstraint('version_number > 0', name='ck_fact_version_number'),
    sa.ForeignKeyConstraint(['identity_id', 'organization_id', 'workspace_owner_id'], ['fact_identities.id', 'fact_identities.organization_id', 'fact_identities.workspace_owner_id'], name='fk_fact_version_identity'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['predecessor_id', 'identity_id', 'organization_id', 'workspace_owner_id'], ['fact_versions.id', 'fact_versions.identity_id', 'fact_versions.organization_id', 'fact_versions.workspace_owner_id'], name='fk_fact_version_predecessor'),
    sa.ForeignKeyConstraint(['workspace_owner_id', 'organization_id'], ['users.id', 'users.organization_id'], name='fk_fact_version_owner'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'identity_id', 'organization_id', 'workspace_owner_id', name='uq_fact_version_scope'),
    sa.UniqueConstraint('identity_id', 'version_number', name='uq_fact_version_number')
    )
    op.create_index(op.f('ix_fact_versions_organization_id'), 'fact_versions', ['organization_id'], unique=False)
    op.create_index('uq_fact_version_current', 'fact_versions', ['identity_id'], unique=True, postgresql_where=sa.text("status = 'CURRENT'"), sqlite_where=sa.text("status = 'CURRENT'"))
    op.create_table('fact_proposals',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_owner_id', sa.UUID(), nullable=False),
    sa.Column('identity_id', sa.UUID(), nullable=False),
    sa.Column('evidence_id', sa.UUID(), nullable=False),
    sa.Column('revision', sa.Integer(), server_default='1', nullable=False),
    sa.Column('value_json', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
    sa.Column('relationship', sa.String(length=16), server_default='UNCLEAR', nullable=False),
    sa.Column('comparison_ready', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('expected_head_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='PENDING', nullable=False),
    sa.Column('result_version_id', sa.UUID(), nullable=True),
    sa.Column('redacted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.CheckConstraint("(status = 'APPROVED' AND result_version_id IS NOT NULL) OR (status <> 'APPROVED' AND result_version_id IS NULL)", name='ck_fact_proposal_result'),
    sa.CheckConstraint("relationship IN ('NEW','SAME','REVISION','CONFLICT','UNCLEAR')", name='ck_fact_proposal_relationship'),
    sa.CheckConstraint("status IN ('PENDING','APPROVED','REJECTED','UNRESOLVED')", name='ck_fact_proposal_status'),
    sa.CheckConstraint('(redacted_at IS NULL AND value_json IS NOT NULL) OR (redacted_at IS NOT NULL AND value_json IS NULL)', name='ck_fact_proposal_redaction'),
    sa.CheckConstraint('revision > 0', name='ck_fact_proposal_revision'),
    sa.ForeignKeyConstraint(['evidence_id', 'organization_id', 'workspace_owner_id'], ['fact_evidence.id', 'fact_evidence.organization_id', 'fact_evidence.workspace_owner_id'], name='fk_fact_proposal_evidence'),
    sa.ForeignKeyConstraint(['expected_head_id', 'identity_id', 'organization_id', 'workspace_owner_id'], ['fact_versions.id', 'fact_versions.identity_id', 'fact_versions.organization_id', 'fact_versions.workspace_owner_id'], name='fk_fact_proposal_head'),
    sa.ForeignKeyConstraint(['identity_id', 'organization_id', 'workspace_owner_id'], ['fact_identities.id', 'fact_identities.organization_id', 'fact_identities.workspace_owner_id'], name='fk_fact_proposal_identity'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['result_version_id', 'identity_id', 'organization_id', 'workspace_owner_id'], ['fact_versions.id', 'fact_versions.identity_id', 'fact_versions.organization_id', 'fact_versions.workspace_owner_id'], name='fk_fact_proposal_result'),
    sa.ForeignKeyConstraint(['workspace_owner_id', 'organization_id'], ['users.id', 'users.organization_id'], name='fk_fact_proposal_owner'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'identity_id', 'organization_id', 'workspace_owner_id', name='uq_fact_proposal_scope')
    )
    op.create_index('ix_fact_proposal_review_queue', 'fact_proposals', ['organization_id', 'workspace_owner_id', 'status'], unique=False)
    op.create_index(op.f('ix_fact_proposals_organization_id'), 'fact_proposals', ['organization_id'], unique=False)
    op.create_table('fact_evidence_links',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_owner_id', sa.UUID(), nullable=False),
    sa.Column('identity_id', sa.UUID(), nullable=False),
    sa.Column('version_id', sa.UUID(), nullable=False),
    sa.Column('evidence_id', sa.UUID(), nullable=False),
    sa.Column('evidence_key', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.CheckConstraint('length(evidence_key) = 64', name='ck_fact_link_hash'),
    sa.ForeignKeyConstraint(['evidence_id', 'organization_id', 'workspace_owner_id'], ['fact_evidence.id', 'fact_evidence.organization_id', 'fact_evidence.workspace_owner_id'], name='fk_fact_link_evidence'),
    sa.ForeignKeyConstraint(['identity_id', 'organization_id', 'workspace_owner_id'], ['fact_identities.id', 'fact_identities.organization_id', 'fact_identities.workspace_owner_id'], name='fk_fact_link_identity'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['version_id', 'identity_id', 'organization_id', 'workspace_owner_id'], ['fact_versions.id', 'fact_versions.identity_id', 'fact_versions.organization_id', 'fact_versions.workspace_owner_id'], name='fk_fact_link_version'),
    sa.ForeignKeyConstraint(['workspace_owner_id', 'organization_id'], ['users.id', 'users.organization_id'], name='fk_fact_link_owner'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('version_id', 'evidence_key', name='uq_fact_link_evidence_key')
    )
    op.create_index(op.f('ix_fact_evidence_links_organization_id'), 'fact_evidence_links', ['organization_id'], unique=False)
    op.create_table('fact_reviews',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_owner_id', sa.UUID(), nullable=False),
    sa.Column('identity_id', sa.UUID(), nullable=False),
    sa.Column('proposal_id', sa.UUID(), nullable=False),
    sa.Column('proposal_revision', sa.Integer(), nullable=False),
    sa.Column('decision', sa.String(length=24), nullable=False),
    sa.Column('request_key', sa.String(length=128), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('result_version_id', sa.UUID(), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('redacted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.CheckConstraint("decision IN ('EDIT','APPROVE','REJECT','KEEP_UNRESOLVED')", name='ck_fact_review_decision'),
    sa.CheckConstraint('length(request_key) > 0 AND length(request_hash) = 64', name='ck_fact_review_request'),
    sa.CheckConstraint('proposal_revision > 0', name='ck_fact_review_revision'),
    sa.ForeignKeyConstraint(['identity_id', 'organization_id', 'workspace_owner_id'], ['fact_identities.id', 'fact_identities.organization_id', 'fact_identities.workspace_owner_id'], name='fk_fact_review_identity'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['proposal_id', 'identity_id', 'organization_id', 'workspace_owner_id'], ['fact_proposals.id', 'fact_proposals.identity_id', 'fact_proposals.organization_id', 'fact_proposals.workspace_owner_id'], name='fk_fact_review_proposal'),
    sa.ForeignKeyConstraint(['result_version_id', 'identity_id', 'organization_id', 'workspace_owner_id'], ['fact_versions.id', 'fact_versions.identity_id', 'fact_versions.organization_id', 'fact_versions.workspace_owner_id'], name='fk_fact_review_result'),
    sa.ForeignKeyConstraint(['workspace_owner_id', 'organization_id'], ['users.id', 'users.organization_id'], name='fk_fact_review_owner'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'workspace_owner_id', 'request_key', name='uq_fact_review_request')
    )
    op.create_index(op.f('ix_fact_reviews_organization_id'), 'fact_reviews', ['organization_id'], unique=False)
    op.execute(_IDENTITY_GUARD_PG)
    op.execute("CREATE TRIGGER guard_fact_identity BEFORE UPDATE ON fact_identities FOR EACH ROW EXECUTE FUNCTION knowact_guard_fact_identity()")
    op.execute(_VERSION_GUARD_PG)
    op.execute("CREATE TRIGGER guard_fact_version BEFORE UPDATE OR DELETE ON fact_versions FOR EACH ROW EXECUTE FUNCTION knowact_guard_fact_version()")
    op.execute(_REVIEW_GUARD_PG)
    op.execute("CREATE TRIGGER guard_fact_review BEFORE UPDATE OR DELETE ON fact_reviews FOR EACH ROW EXECUTE FUNCTION knowact_guard_fact_review()")


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS guard_fact_identity ON fact_identities")
    op.execute("DROP FUNCTION IF EXISTS knowact_guard_fact_identity()")
    op.execute("DROP TRIGGER IF EXISTS guard_fact_review ON fact_reviews")
    op.execute("DROP TRIGGER IF EXISTS guard_fact_version ON fact_versions")
    op.execute("DROP FUNCTION IF EXISTS knowact_guard_fact_review()")
    op.execute("DROP FUNCTION IF EXISTS knowact_guard_fact_version()")
    op.drop_table("fact_reviews")
    op.drop_table("fact_evidence_links")
    op.drop_table("fact_proposals")
    op.drop_table("fact_versions")
    op.drop_table("fact_evidence")
    op.drop_table("fact_source_snapshots")
    op.drop_table("fact_identities")
    op.drop_constraint("uq_entities_id_org_owner", "business_entities", type_="unique")
    op.drop_constraint("uq_users_id_org", "users", type_="unique")

