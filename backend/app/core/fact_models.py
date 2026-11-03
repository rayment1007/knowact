"""Versioned fact-memory storage, separate from legacy narrative knowledge.

These tables hold reviewed atomic proposals and minimal evidence. They never
contain an additional copy of a full raw source. All cross-record links carry
the organization and owner, with identity-scoped version links where needed.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, DDL, ForeignKeyConstraint, Index,
    Integer, String, Text, UniqueConstraint, event, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, OrganizationScopedMixin


def _id():
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created():
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _owner_fk(name):
    return ForeignKeyConstraint(
        ["workspace_owner_id", "organization_id"], ["users.id", "users.organization_id"], name=name,
    )


def _scope_unique(name, *extra):
    return UniqueConstraint("id", *extra, "organization_id", "workspace_owner_id", name=name)


def _identity_fk(name):
    return ForeignKeyConstraint(
        ["identity_id", "organization_id", "workspace_owner_id"],
        ["fact_identities.id", "fact_identities.organization_id", "fact_identities.workspace_owner_id"], name=name,
    )


def _version_fk(column, name):
    return ForeignKeyConstraint(
        [column, "identity_id", "organization_id", "workspace_owner_id"],
        ["fact_versions.id", "fact_versions.identity_id", "fact_versions.organization_id", "fact_versions.workspace_owner_id"], name=name,
    )


def _evidence_fk(name):
    return ForeignKeyConstraint(
        ["evidence_id", "organization_id", "workspace_owner_id"],
        ["fact_evidence.id", "fact_evidence.organization_id", "fact_evidence.workspace_owner_id"], name=name,
    )


class FactIdentity(OrganizationScopedMixin, Base):
    __tablename__ = "fact_identities"
    id: Mapped[uuid.UUID] = _id()
    workspace_owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    fact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_key: Mapped[str] = mapped_column(String(160), nullable=False)
    occurrence_key: Mapped[str] = mapped_column(String(160), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        _owner_fk("fk_fact_identity_owner"),
        ForeignKeyConstraint(["project_id", "organization_id", "workspace_owner_id"],
            ["business_entities.id", "business_entities.organization_id", "business_entities.workspace_owner_id"],
            name="fk_fact_identity_project"),
        _scope_unique("uq_fact_identity_scope"),
        UniqueConstraint("organization_id", "workspace_owner_id", "project_id", "fact_type", "canonical_key", "occurrence_key", name="uq_fact_identity_key"),
        CheckConstraint("fact_type IN ('DEADLINE','MEETING_TIME','PROJECT_STATUS','DECISION','RESPONSIBLE_PERSON')", name="ck_fact_identity_type"),
        CheckConstraint("length(canonical_key) > 0", name="ck_fact_identity_key"),
    )


class FactSourceSnapshot(OrganizationScopedMixin, Base):
    __tablename__ = "fact_source_snapshots"
    id: Mapped[uuid.UUID] = _id()
    workspace_owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    origin_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    captured_at: Mapped[datetime] = _created()
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        _owner_fk("fk_fact_snapshot_owner"),
        _scope_unique("uq_fact_snapshot_scope"),
        UniqueConstraint("organization_id", "workspace_owner_id", "source_kind", "source_id", "source_fingerprint", name="uq_fact_snapshot_revision"),
        Index("ix_fact_snapshot_origin", "organization_id", "workspace_owner_id", "origin_key"),
        CheckConstraint("source_kind IN ('source','email','calendar','file')", name="ck_fact_snapshot_kind"),
        CheckConstraint("length(origin_key) = 64 AND length(source_fingerprint) = 64", name="ck_fact_snapshot_hash"),
        CheckConstraint("purged_at IS NULL OR (available = false AND (source_title IS NULL OR source_title = ''))", name="ck_fact_snapshot_purged"),
    )


class FactEvidence(OrganizationScopedMixin, Base):
    __tablename__ = "fact_evidence"
    id: Mapped[uuid.UUID] = _id()
    workspace_owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    excerpt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    locator_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        _owner_fk("fk_fact_evidence_owner"),
        ForeignKeyConstraint(["snapshot_id", "organization_id", "workspace_owner_id"],
            ["fact_source_snapshots.id", "fact_source_snapshots.organization_id", "fact_source_snapshots.workspace_owner_id"], name="fk_fact_evidence_snapshot"),
        _scope_unique("uq_fact_evidence_scope"),
        UniqueConstraint("snapshot_id", "excerpt_hash", name="uq_fact_evidence_excerpt"),
        CheckConstraint("(redacted_at IS NULL AND excerpt IS NOT NULL AND length(excerpt) > 0) OR (redacted_at IS NOT NULL AND excerpt IS NULL)", name="ck_fact_evidence_redaction"),
        CheckConstraint("length(excerpt_hash) = 64", name="ck_fact_evidence_hash"),
    )


class FactVersion(OrganizationScopedMixin, Base):
    __tablename__ = "fact_versions"
    id: Mapped[uuid.UUID] = _id()
    workspace_owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    identity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    value_json: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    normalization_version: Mapped[str] = mapped_column(String(32), nullable=False, default="M1_DATE_V1", server_default="M1_DATE_V1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="CURRENT", server_default="CURRENT")
    predecessor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    approved_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    approved_at: Mapped[datetime] = _created()
    redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        _owner_fk("fk_fact_version_owner"), _identity_fk("fk_fact_version_identity"),
        _scope_unique("uq_fact_version_scope", "identity_id"),
        _version_fk("predecessor_id", "fk_fact_version_predecessor"),
        UniqueConstraint("identity_id", "version_number", name="uq_fact_version_number"),
        Index("uq_fact_version_current", "identity_id", unique=True,
              postgresql_where=text("status = 'CURRENT'"), sqlite_where=text("status = 'CURRENT'")),
        CheckConstraint("status IN ('CURRENT','SUPERSEDED')", name="ck_fact_version_status"),
        CheckConstraint("version_number > 0", name="ck_fact_version_number"),
        CheckConstraint("approved_by = workspace_owner_id", name="ck_fact_version_reviewer"),
        CheckConstraint("predecessor_id IS NULL OR predecessor_id <> id", name="ck_fact_version_predecessor"),
        CheckConstraint("(redacted_at IS NULL AND value_json IS NOT NULL) OR (redacted_at IS NOT NULL AND value_json IS NULL)", name="ck_fact_version_redaction"),
    )


class FactProposal(OrganizationScopedMixin, Base):
    __tablename__ = "fact_proposals"
    id: Mapped[uuid.UUID] = _id()
    workspace_owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    identity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    evidence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    value_json: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    relationship: Mapped[str] = mapped_column(String(16), nullable=False, default="UNCLEAR", server_default="UNCLEAR")
    comparison_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    expected_head_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", server_default="PENDING")
    result_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        _owner_fk("fk_fact_proposal_owner"), _identity_fk("fk_fact_proposal_identity"),
        _evidence_fk("fk_fact_proposal_evidence"),
        _version_fk("expected_head_id", "fk_fact_proposal_head"),
        _version_fk("result_version_id", "fk_fact_proposal_result"),
        _scope_unique("uq_fact_proposal_scope", "identity_id"),
        CheckConstraint("revision > 0", name="ck_fact_proposal_revision"),
        CheckConstraint("status IN ('PENDING','APPROVED','REJECTED','UNRESOLVED')", name="ck_fact_proposal_status"),
        CheckConstraint("relationship IN ('NEW','SAME','REVISION','CONFLICT','UNCLEAR')", name="ck_fact_proposal_relationship"),
        CheckConstraint("(redacted_at IS NULL AND value_json IS NOT NULL) OR (redacted_at IS NOT NULL AND value_json IS NULL)", name="ck_fact_proposal_redaction"),
        CheckConstraint("(status = 'APPROVED' AND result_version_id IS NOT NULL) OR (status <> 'APPROVED' AND result_version_id IS NULL)", name="ck_fact_proposal_result"),
        Index("ix_fact_proposal_review_queue", "organization_id", "workspace_owner_id", "status"),
    )


class FactEvidenceLink(OrganizationScopedMixin, Base):
    __tablename__ = "fact_evidence_links"
    id: Mapped[uuid.UUID] = _id()
    workspace_owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    identity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    evidence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    evidence_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        _owner_fk("fk_fact_link_owner"), _identity_fk("fk_fact_link_identity"),
        _version_fk("version_id", "fk_fact_link_version"), _evidence_fk("fk_fact_link_evidence"),
        UniqueConstraint("version_id", "evidence_key", name="uq_fact_link_evidence_key"),
        CheckConstraint("length(evidence_key) = 64", name="ck_fact_link_hash"),
    )


class FactReview(OrganizationScopedMixin, Base):
    __tablename__ = "fact_reviews"
    id: Mapped[uuid.UUID] = _id()
    workspace_owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    identity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    proposal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    proposal_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        _owner_fk("fk_fact_review_owner"), _identity_fk("fk_fact_review_identity"),
        ForeignKeyConstraint(["proposal_id", "identity_id", "organization_id", "workspace_owner_id"],
            ["fact_proposals.id", "fact_proposals.identity_id", "fact_proposals.organization_id", "fact_proposals.workspace_owner_id"], name="fk_fact_review_proposal"),
        _version_fk("result_version_id", "fk_fact_review_result"),
        UniqueConstraint("organization_id", "workspace_owner_id", "request_key", name="uq_fact_review_request"),
        CheckConstraint("proposal_revision > 0", name="ck_fact_review_revision"),
        CheckConstraint("decision IN ('EDIT','APPROVE','REJECT','KEEP_UNRESOLVED')", name="ck_fact_review_decision"),
        CheckConstraint("length(request_key) > 0 AND length(request_hash) = 64", name="ck_fact_review_request"),
    )


# Identity keys are immutable: changing a project's key below an accepted
# version would silently reinterpret its history even if the version is frozen.
_IDENTITY_GUARD_PG = """
CREATE OR REPLACE FUNCTION knowact_guard_fact_identity() RETURNS trigger AS $$
BEGIN
 IF NEW IS DISTINCT FROM OLD THEN
   RAISE EXCEPTION 'Fact identity is immutable';
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql
"""
_IDENTITY_GUARD_SQLITE = """
CREATE TRIGGER guard_fact_identity_update BEFORE UPDATE ON fact_identities
WHEN NEW.id IS NOT OLD.id OR NEW.organization_id IS NOT OLD.organization_id
 OR NEW.workspace_owner_id IS NOT OLD.workspace_owner_id OR NEW.project_id IS NOT OLD.project_id
 OR NEW.fact_type IS NOT OLD.fact_type OR NEW.canonical_key IS NOT OLD.canonical_key
 OR NEW.occurrence_key IS NOT OLD.occurrence_key OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT, 'Fact identity is immutable'); END
"""
event.listen(FactIdentity.__table__, "after_create", DDL(_IDENTITY_GUARD_PG).execute_if(dialect="postgresql"))
event.listen(FactIdentity.__table__, "after_create", DDL("CREATE TRIGGER guard_fact_identity BEFORE UPDATE ON fact_identities FOR EACH ROW EXECUTE FUNCTION knowact_guard_fact_identity()").execute_if(dialect="postgresql"))
event.listen(FactIdentity.__table__, "after_create", DDL(_IDENTITY_GUARD_SQLITE).execute_if(dialect="sqlite"))


# Database-level history guard. PostgreSQL additionally requires an explicit
# transaction-local purge flag before accepting one-way value redaction.
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

_VERSION_GUARD_SQLITE = """
CREATE TRIGGER guard_fact_version_update BEFORE UPDATE ON fact_versions
WHEN NEW.id IS NOT OLD.id OR NEW.identity_id IS NOT OLD.identity_id
 OR NEW.organization_id IS NOT OLD.organization_id OR NEW.workspace_owner_id IS NOT OLD.workspace_owner_id
 OR NEW.version_number IS NOT OLD.version_number OR NEW.predecessor_id IS NOT OLD.predecessor_id
 OR NEW.approved_by IS NOT OLD.approved_by OR NEW.approved_at IS NOT OLD.approved_at
 OR NEW.normalization_version IS NOT OLD.normalization_version
 OR ((NEW.value_json IS NOT OLD.value_json OR NEW.redacted_at IS NOT OLD.redacted_at)
     AND NOT (OLD.redacted_at IS NULL AND NEW.redacted_at IS NOT NULL AND NEW.value_json IS NULL))
 OR (NEW.status IS NOT OLD.status AND NOT (OLD.status='CURRENT' AND NEW.status='SUPERSEDED'))
BEGIN SELECT RAISE(ABORT, 'Accepted fact versions are immutable'); END
"""

event.listen(FactVersion.__table__, "after_create", DDL(_VERSION_GUARD_PG).execute_if(dialect="postgresql"))
event.listen(FactVersion.__table__, "after_create", DDL("CREATE TRIGGER guard_fact_version BEFORE UPDATE OR DELETE ON fact_versions FOR EACH ROW EXECUTE FUNCTION knowact_guard_fact_version()").execute_if(dialect="postgresql"))
event.listen(FactVersion.__table__, "after_create", DDL(_VERSION_GUARD_SQLITE).execute_if(dialect="sqlite"))
event.listen(FactVersion.__table__, "after_create", DDL("CREATE TRIGGER guard_fact_version_delete BEFORE DELETE ON fact_versions BEGIN SELECT RAISE(ABORT, 'Accepted fact history cannot be deleted'); END").execute_if(dialect="sqlite"))


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

_REVIEW_GUARD_SQLITE = """
CREATE TRIGGER guard_fact_review_update BEFORE UPDATE ON fact_reviews
WHEN NEW.id IS NOT OLD.id OR NEW.identity_id IS NOT OLD.identity_id
 OR NEW.organization_id IS NOT OLD.organization_id OR NEW.workspace_owner_id IS NOT OLD.workspace_owner_id
 OR NEW.proposal_id IS NOT OLD.proposal_id OR NEW.proposal_revision IS NOT OLD.proposal_revision
 OR NEW.decision IS NOT OLD.decision OR NEW.request_key IS NOT OLD.request_key
 OR NEW.request_hash IS NOT OLD.request_hash OR NEW.result_version_id IS NOT OLD.result_version_id
 OR NEW.created_at IS NOT OLD.created_at
 OR ((NEW.detail IS NOT OLD.detail OR NEW.redacted_at IS NOT OLD.redacted_at)
     AND NOT (OLD.redacted_at IS NULL AND NEW.redacted_at IS NOT NULL AND NEW.detail = '{}'))
BEGIN SELECT RAISE(ABORT, 'Fact review history is immutable'); END
"""

event.listen(FactReview.__table__, "after_create", DDL(_REVIEW_GUARD_PG).execute_if(dialect="postgresql"))
event.listen(FactReview.__table__, "after_create", DDL("CREATE TRIGGER guard_fact_review BEFORE UPDATE OR DELETE ON fact_reviews FOR EACH ROW EXECUTE FUNCTION knowact_guard_fact_review()").execute_if(dialect="postgresql"))
event.listen(FactReview.__table__, "after_create", DDL(_REVIEW_GUARD_SQLITE).execute_if(dialect="sqlite"))
event.listen(FactReview.__table__, "after_create", DDL("CREATE TRIGGER guard_fact_review_delete BEFORE DELETE ON fact_reviews BEGIN SELECT RAISE(ABORT, 'Fact review history cannot be deleted'); END").execute_if(dialect="sqlite"))
