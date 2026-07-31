"""End-to-end Gmail ingestion test (Requirements 27.3, 27.4, 27.5).

Drives a fake Gmail message through the whole pipeline:

    sync (collect) -> SourceItem (EMAIL) -> classify (SUGGESTED)
      -> confirm classification -> extract knowledge (SUGGESTED)
      -> confirm knowledge (CONFIRMED)

and asserts statuses, evidence presence, dedup on re-sync, and audit rows. All
Gmail I/O uses a FAKE client, so no network call occurs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.core import models as core_models
from app.core.models import (
    KnowledgeItem,
    SourceItem,
    SourceStatus,
    SourceType,
    SuggestionStatus,
)
from app.core.services.classification_service import ClassificationService
from app.core.services.knowledge_service import KnowledgeService
from app.modules.cwi.models import (
    ConnectionStatus,
    EmailMessageRecord,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationService as IntegrationServiceEnum,
)
from app.modules.cwi.services.gmail_client import FakeGmailClient, GmailMessage
from app.modules.cwi.services.gmail_sync_service import GmailSyncService
from app.security import hash_password

_TEST_SETTINGS = Settings(ai_provider="mock")


def _seed(db: Session) -> IntegrationConnection:
    suffix = uuid4().hex
    org = core_models.Organization(name=f"Org-{suffix}")
    db.add(org)
    db.flush()
    user = core_models.User(
        organization_id=org.id,
        email=f"user-{suffix}@example.com",
        full_name="Actor",
        password_hash=hash_password("pw"),
        role="ADMIN",
    )
    db.add(user)
    db.flush()
    connection = IntegrationConnection(
        organization_id=org.id,
        user_id=user.id,
        provider=IntegrationProvider.GOOGLE,
        service=IntegrationServiceEnum.GMAIL,
        external_account_id=f"sub-{suffix}",
        account_email=f"mailbox-{suffix}@example.com",
        granted_scopes_json=["https://www.googleapis.com/auth/gmail.readonly"],
        access_token_encrypted=b"ciphertext",
        refresh_token_encrypted=b"ciphertext",
        token_expires_at=datetime.now(timezone.utc),
        status=ConnectionStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    return connection


def test_gmail_message_flows_through_pipeline_to_knowledge(
    db_session: Session,
) -> None:
    connection = _seed(db_session)
    org_id = connection.organization_id
    user_id = connection.user_id

    message = GmailMessage(
        gmail_message_id="gmail-e2e-1",
        gmail_thread_id="thread-1",
        sender="client@acme.com",
        subject="Project kickoff decision",
        body_text=(
            "We agreed to proceed with the project. The client approved the "
            "milestone plan for the account."
        ),
        received_at=datetime.now(timezone.utc),
        recipients=["me@example.com"],
        labels=["INBOX"],
    )
    fake = FakeGmailClient([message])
    service = GmailSyncService(
        db_session, fake, integration_service=None, settings=_TEST_SETTINGS
    )

    # 1) Sync (collect).
    run = service.sync_now(org_id, user_id, connection.id)
    assert run.records_created == 1
    assert run.source_items_created == 1

    record = db_session.execute(
        select(EmailMessageRecord).where(
            EmailMessageRecord.organization_id == org_id
        )
    ).scalar_one()
    # Provenance preserved (Requirement 27.1).
    assert record.gmail_message_id == "gmail-e2e-1"
    assert record.gmail_thread_id == "thread-1"
    assert record.sender == "client@acme.com"
    assert record.recipients_json == ["me@example.com"]
    assert record.source_item_id is not None

    # SourceItem is an EMAIL item with a SUGGESTED classification.
    source_item = db_session.get(SourceItem, record.source_item_id)
    assert source_item.source_type == SourceType.EMAIL
    assert source_item.status == SourceStatus.NEW

    classification = ClassificationService(db_session)._active_result(
        source_item.id
    )
    assert classification is not None
    assert classification.status == SuggestionStatus.SUGGESTED

    # 2) Confirm classification (human-in-the-loop gate).
    confirmed_class = ClassificationService(db_session).confirm(
        org_id, source_item.id, user_id
    )
    assert confirmed_class.status == SuggestionStatus.CONFIRMED
    db_session.refresh(source_item)
    assert source_item.status == SourceStatus.CLASSIFIED

    # 3) Extract knowledge (passes the privacy gate: work-related, PUBLIC).
    knowledge = KnowledgeService(db_session).extract(org_id, source_item.id)
    assert knowledge.status == SuggestionStatus.SUGGESTED
    assert knowledge.evidence_text  # evidence present (Requirement 6.6)

    # 4) Confirm knowledge → CONFIRMED with an audit row.
    confirmed_knowledge = KnowledgeService(db_session).confirm(
        org_id, knowledge.id, user_id
    )
    assert confirmed_knowledge.status == SuggestionStatus.CONFIRMED

    knowledge_audits = (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == "CONFIRM_KNOWLEDGE")
        .all()
    )
    assert len(knowledge_audits) == 1

    # 5) Re-sync is a no-op (dedup on re-sync, Requirement 27.2).
    run2 = service.sync_now(org_id, user_id, connection.id)
    assert run2.records_created == 0
    assert run2.skipped_duplicates == 1
    records = list(
        db_session.execute(
            select(EmailMessageRecord).where(
                EmailMessageRecord.organization_id == org_id
            )
        ).scalars()
    )
    assert len(records) == 1
    # Still exactly one knowledge item — re-sync created no parallel pipeline.
    kitems = list(
        db_session.execute(
            select(KnowledgeItem).where(KnowledgeItem.organization_id == org_id)
        ).scalars()
    )
    assert len(kitems) == 1
