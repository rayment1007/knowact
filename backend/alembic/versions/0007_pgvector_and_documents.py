"""Connected Workspace Intelligence — pgvector + document knowledge (M6.4).

Creates the M6.4 document tables that back document upload and filtered
retrieval (Requirement 29):

* ``document_assets`` — uploaded-document metadata + checksum + processing
  status + sensitivity + ``source_deleted`` provenance flag. The binary lives
  behind the ``StorageBackend`` (never in Postgres); only the opaque
  ``storage_key`` is persisted.
* ``document_chunks`` — one embedded slice per row. The ``embedding`` column is
  a real ``pgvector`` ``Vector(dim)`` on PostgreSQL (with an approximate-NN
  index) and a portable JSON array on any other dialect. A btree index on
  ``(organization_id, document_asset_id)`` supports scoped lookups.

Portability
-----------
Production runs on PostgreSQL; the automated tests run on in-memory SQLite via
``Base.metadata.create_all`` (never through this migration). Every
pgvector-specific step here — ``CREATE EXTENSION vector``, the ``Vector(dim)``
column type, and the ANN index — is therefore **guarded to the PostgreSQL
dialect** so the migration never breaks on another backend. The embedding
dimension is pinned from ``Settings.embedding_dimension`` so the deployed schema
is explicit even if the model changes later.

Migration ordering note
------------------------
Chains off the ACTUAL current head, ``0006_calendar_event_links``, taking the
next real number ``0007`` (the design's provisional ``0004`` label predates the
integration/email/calendar revisions that landed first).

The reversible downgrade drops both tables and the ``document_processing_status``
enum, but deliberately leaves the ``vector`` extension in place: dropping a
shared-schema extension is a destructive change and is out of scope for an
automatic downgrade.

Revision ID: 0007_pgvector_and_documents
Revises: 0006_calendar_event_links
Create Date: M6.4
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.config import get_settings

# revision identifiers, used by Alembic.
revision: str = "0007_pgvector_and_documents"
down_revision: str | None = "0006_calendar_event_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# New enum for the document processing lifecycle.
document_processing_status = postgresql.ENUM(
    "UPLOADED",
    "PARSING",
    "CHUNKING",
    "EMBEDDING",
    "INDEXED",
    "FAILED",
    name="document_processing_status",
    create_type=False,
)

# The existing ``sensitivity`` enum (created in 0001) is reused, not recreated.
sensitivity = postgresql.ENUM(
    "PUBLIC",
    "INTERNAL",
    "CONFIDENTIAL",
    "HIGHLY_SENSITIVE",
    name="sensitivity",
    create_type=False,
)

# ivfflat fallback list count when hnsw is unavailable. 100 is a sensible
# default for small/medium corpora (documented per design guidance).
_IVFFLAT_LISTS = 100


def _embedding_dimension() -> int:
    """Pin the deployed embedding dimension from application settings."""

    return get_settings().embedding_dimension


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    dim = _embedding_dimension()

    # pgvector is a PostgreSQL-only extension; enable it FIRST so the
    # ``document_chunks.embedding`` column type resolves. Guarded to Postgres so
    # the migration is a no-op-safe on other dialects.
    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    document_processing_status.create(bind, checkfirst=True)

    op.create_table(
        "document_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column(
            "processing_status", document_processing_status, nullable=False
        ),
        sa.Column("sensitivity", sensitivity, nullable=False),
        sa.Column(
            "source_deleted",
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
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_document_assets_organization_id",
        "document_assets",
        ["organization_id"],
    )
    op.create_index(
        "ix_document_assets_uploaded_by",
        "document_assets",
        ["uploaded_by"],
    )
    op.create_index(
        "ix_document_assets_org_checksum",
        "document_assets",
        ["organization_id", "checksum"],
    )

    # The embedding column type is dialect-dependent: a real ``Vector(dim)`` on
    # PostgreSQL, a portable JSON array elsewhere.
    if is_postgres:
        from pgvector.sqlalchemy import Vector

        embedding_column = sa.Column("embedding", Vector(dim), nullable=True)
    else:
        embedding_column = sa.Column("embedding", sa.JSON(), nullable=True)

    op.create_table(
        "document_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "document_asset_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        embedding_column,
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["document_asset_id"],
            ["document_assets.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_document_chunks_organization_id",
        "document_chunks",
        ["organization_id"],
    )
    op.create_index(
        "ix_document_chunks_org_asset",
        "document_chunks",
        ["organization_id", "document_asset_id"],
    )

    # Approximate-NN index on the embedding — PostgreSQL/pgvector only. Prefer
    # hnsw (better recall/latency, no training); fall back to ivfflat with a
    # documented ``lists`` value if hnsw is unavailable on the server build.
    if is_postgres:
        try:
            op.execute(
                "CREATE INDEX ix_document_chunks_embedding_hnsw "
                "ON document_chunks USING hnsw (embedding vector_cosine_ops)"
            )
        except Exception:  # pragma: no cover - depends on server build
            op.execute(
                "CREATE INDEX ix_document_chunks_embedding_ivfflat "
                "ON document_chunks USING ivfflat (embedding vector_cosine_ops) "
                f"WITH (lists = {_IVFFLAT_LISTS})"
            )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw")
        op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_ivfflat")

    op.drop_index(
        "ix_document_chunks_org_asset", table_name="document_chunks"
    )
    op.drop_index(
        "ix_document_chunks_organization_id", table_name="document_chunks"
    )
    op.drop_table("document_chunks")

    op.drop_index(
        "ix_document_assets_org_checksum", table_name="document_assets"
    )
    op.drop_index(
        "ix_document_assets_uploaded_by", table_name="document_assets"
    )
    op.drop_index(
        "ix_document_assets_organization_id", table_name="document_assets"
    )
    op.drop_table("document_assets")

    document_processing_status.drop(bind, checkfirst=True)
    # The ``vector`` extension is intentionally left in place (destructive
    # shared-schema change; out of scope for automatic downgrade).
