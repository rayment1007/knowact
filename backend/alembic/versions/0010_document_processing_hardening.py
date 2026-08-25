"""Persist document failures, strengthen dedup, and isolate sender signals.

Revision ID: 0010_document_hardening
Revises: 0009_privacy_retention
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0010_document_hardening"
down_revision: str | None = "0009_privacy_retention"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_assets",
        sa.Column("failure_reason", sa.Text(), nullable=True),
    )

    # Older code could create duplicate positions under concurrent processing.
    # Keep the oldest row at each position before enforcing the invariant.
    op.execute(
        sa.text(
            """
            DELETE FROM document_chunks AS duplicate
            USING document_chunks AS keeper
            WHERE duplicate.document_asset_id = keeper.document_asset_id
              AND duplicate.chunk_index = keeper.chunk_index
              AND duplicate.id::text > keeper.id::text
            """
        )
    )
    op.create_unique_constraint(
        "uq_document_chunks_asset_index",
        "document_chunks",
        ["document_asset_id", "chunk_index"],
    )

    # Gmail content dedup belongs to one authorized mailbox, not the whole
    # organization. Constraint creation deliberately fails if historical
    # duplicates exist so operators can review them instead of silently
    # deleting imported user data during a deployment.
    op.drop_index(
        "ix_email_message_records_org_content_hash",
        table_name="email_message_records",
    )
    op.create_unique_constraint(
        "uq_email_message_connection_content_hash",
        "email_message_records",
        ["integration_connection_id", "content_hash"],
    )

    # Sender preferences are personal. Keep nullable legacy ``created_by``
    # rows dormant rather than guessing an owner; current application writes
    # always set the authenticated user's id.
    op.drop_constraint(
        "uq_email_sender_signal_org_pattern",
        "email_sender_signals",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_email_sender_signal_org_user_pattern",
        "email_sender_signals",
        ["organization_id", "created_by", "pattern"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_email_sender_signal_org_user_pattern",
        "email_sender_signals",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_email_sender_signal_org_pattern",
        "email_sender_signals",
        ["organization_id", "pattern"],
    )
    op.drop_constraint(
        "uq_email_message_connection_content_hash",
        "email_message_records",
        type_="unique",
    )
    op.create_index(
        "ix_email_message_records_org_content_hash",
        "email_message_records",
        ["organization_id", "content_hash"],
    )
    op.drop_constraint(
        "uq_document_chunks_asset_index",
        "document_chunks",
        type_="unique",
    )
    op.drop_column("document_assets", "failure_reason")
