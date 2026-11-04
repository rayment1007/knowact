"""Owner-scoped, bounded extraction attempts and their content-bearing cache.

These records are generation receipts, never approvals. Content copies remain
explicitly attributable to an origin so fact-layer redaction can clear them.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, OrganizationScopedMixin


class FactExtractionRun(OrganizationScopedMixin, Base):
    __tablename__ = "fact_extraction_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    origin_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PROCESSING", server_default="PROCESSING")
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    coverage_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    output_json: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The legacy summary can be removed independently; this is a durable
    # provenance reference, not a cascade/restrict relationship.
    source_proposal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    proposal_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(["workspace_owner_id", "organization_id"],
            ["users.id", "users.organization_id"], name="fk_fact_extraction_owner"),
        ForeignKeyConstraint(["project_id", "organization_id", "workspace_owner_id"],
            ["business_entities.id", "business_entities.organization_id", "business_entities.workspace_owner_id"],
            name="fk_fact_extraction_project"),
        UniqueConstraint("organization_id", "workspace_owner_id", "request_key", name="uq_fact_extraction_request"),
        UniqueConstraint("id", "organization_id", "workspace_owner_id", name="uq_fact_extraction_scope"),
        UniqueConstraint("cache_key", "generation", name="uq_fact_extraction_generation"),
        CheckConstraint("source_kind IN ('source','email','calendar','file')", name="ck_fact_extraction_kind"),
        CheckConstraint("status IN ('PROCESSING','SUCCEEDED','FAILED')", name="ck_fact_extraction_status"),
        CheckConstraint("generation > 0", name="ck_fact_extraction_generation"),
        CheckConstraint("length(origin_key) = 64 AND length(source_fingerprint) = 64 AND length(cache_key) = 64", name="ck_fact_extraction_hashes"),
        CheckConstraint("length(request_key) > 0 AND length(request_hash) = 64", name="ck_fact_extraction_request"),
        CheckConstraint("redacted_at IS NULL OR output_json IS NULL", name="ck_fact_extraction_redaction"),
        CheckConstraint("status <> 'FAILED' OR error_code IS NOT NULL", name="ck_fact_extraction_error"),
        Index("ix_fact_extraction_origin", "organization_id", "workspace_owner_id", "origin_key"),
        Index("ix_fact_extraction_cache", "organization_id", "workspace_owner_id", "cache_key", "status"),
    )


class FactExtractionRequest(OrganizationScopedMixin, Base):
    """Content-free replay receipt, including requests which reuse a cache."""
    __tablename__ = "fact_extraction_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(["workspace_owner_id", "organization_id"],
            ["users.id", "users.organization_id"], name="fk_fact_extraction_receipt_owner"),
        ForeignKeyConstraint(["extraction_run_id", "organization_id", "workspace_owner_id"],
            ["fact_extraction_runs.id", "fact_extraction_runs.organization_id", "fact_extraction_runs.workspace_owner_id"],
            name="fk_fact_extraction_receipt_run"),
        UniqueConstraint("organization_id", "workspace_owner_id", "request_key", name="uq_fact_extraction_receipt_request"),
        CheckConstraint("length(request_key) > 0 AND length(request_hash) = 64", name="ck_fact_extraction_receipt_request"),
    )
