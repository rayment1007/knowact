"""Connected Workspace Intelligence — integration_connections (M6.1).

Creates the ``integration_connections`` table plus its native PostgreSQL enum
types (``integration_provider``, ``integration_service``, ``connection_status``)
for the CWI identity/connection phase (Requirements 24.3, 25.1, 25.5). OAuth
access/refresh tokens are stored ONLY as encrypted ``LargeBinary`` ciphertext;
plaintext is never persisted.

Migration ordering note
------------------------
CWI phase M6.1 is implemented BEFORE M6.4 (pgvector & documents), so the design's
provisional "0005" label is not usable yet — its stated ``down_revision``
(``0004_pgvector``) does not exist in the chain. This revision therefore chains
off the ACTUAL current head, ``0003_referral_match_evidence``, taking the next
real number ``0004``. Later CWI phases chain after it.

Revision ID: 0004_integration_connections
Revises: 0002_knowledge_source_meeting
Create Date: M6.1
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_integration_connections"
down_revision: str | None = "0002_knowledge_source_meeting"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Native PostgreSQL enum types. ``create_type=False`` keeps CREATE TABLE from
# trying to (re)create them; we create/drop them explicitly.
# ---------------------------------------------------------------------------
integration_provider = postgresql.ENUM(
    "GOOGLE",
    name="integration_provider",
    create_type=False,
)
integration_service = postgresql.ENUM(
    "GMAIL",
    "GOOGLE_CALENDAR",
    name="integration_service",
    create_type=False,
)
connection_status = postgresql.ENUM(
    "CONNECTED",
    "EXPIRED",
    "REVOKED",
    "ERROR",
    name="connection_status",
    create_type=False,
)

_ALL_ENUMS = (
    integration_provider,
    integration_service,
    connection_status,
)


def upgrade() -> None:
    bind = op.get_bind()

    for enum in _ALL_ENUMS:
        enum.create(bind, checkfirst=True)

    op.create_table(
        "integration_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", integration_provider, nullable=False),
        sa.Column("service", integration_service, nullable=False),
        sa.Column("external_account_id", sa.String(length=255), nullable=False),
        sa.Column("account_email", sa.String(length=320), nullable=False),
        sa.Column(
            "granted_scopes_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("access_token_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", connection_status, nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_cursor", sa.String(length=512), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            "provider",
            "service",
            "external_account_id",
            name="uq_integration_connection_identity",
        ),
    )
    op.create_index(
        "ix_integration_connections_organization_id",
        "integration_connections",
        ["organization_id"],
    )
    op.create_index(
        "ix_integration_connections_user_id",
        "integration_connections",
        ["user_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index(
        "ix_integration_connections_user_id",
        table_name="integration_connections",
    )
    op.drop_index(
        "ix_integration_connections_organization_id",
        table_name="integration_connections",
    )
    op.drop_table("integration_connections")

    for enum in reversed(_ALL_ENUMS):
        enum.drop(bind, checkfirst=True)
