"""Property-based test P15: Gmail dedup idempotency.

Property 15 (Gmail dedup idempotency): for all messages ``m`` and any number of
sync passes ``n >= 1``, ingesting ``m`` ``n`` times produces **at most one**
:class:`~app.modules.cwi.models.EmailMessageRecord` and **at most one**
downstream :class:`~app.core.models.SourceItem` for a given
``(organization_id, content_hash)``.

Each Hypothesis example seeds an isolated org + user + connection, then drives
the :class:`GmailSyncService` (backed by a FAKE Gmail client — no network) over
``n`` sync passes where the fetched batch even contains the *same content under
different Gmail message ids*, and asserts the dedup invariant holds. Per
Requirement 21.3 the property runs a minimum of 100 examples.

**Validates: Requirements 27.2 / Property 15**
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.core import models as core_models
from app.core.models import SourceItem, SourceType
from app.modules.cwi.models import (
    ConnectionStatus,
    EmailMessageRecord,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationService as IntegrationServiceEnum,
)
from app.modules.cwi.services.gmail_client import FakeGmailClient, GmailMessage
from app.modules.cwi.services.gmail_sync_service import (
    GmailSyncService,
    compute_content_hash,
)
from app.security import hash_password

_PW_HASH = hash_password("pw")
_TEST_SETTINGS = Settings(ai_provider="mock")

# Constrained text: printable, bounded, so generators stay in the input space.
_TEXT = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=0,
    max_size=60,
)


def _seed_connection(db: Session) -> IntegrationConnection:
    suffix = uuid4().hex
    org = core_models.Organization(name=f"Org-{suffix}")
    db.add(org)
    db.flush()
    user = core_models.User(
        organization_id=org.id,
        email=f"user-{suffix}@example.com",
        full_name="Actor",
        password_hash=_PW_HASH,
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


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    sender=_TEXT,
    subject=_TEXT,
    body=_TEXT,
    passes=st.integers(min_value=1, max_value=5),
    duplicate_ids=st.integers(min_value=1, max_value=3),
)
def test_gmail_dedup_idempotency(
    db_session: Session,
    sender: str,
    subject: str,
    body: str,
    passes: int,
    duplicate_ids: int,
) -> None:
    """Ingesting one message across n passes yields at most one record + item.

    **Validates: Requirements 27.2 / Property 15**
    """

    connection = _seed_connection(db_session)
    org_id = connection.organization_id
    user_id = connection.user_id

    received = datetime.now(timezone.utc)
    # The same logical content under several distinct Gmail message ids, so the
    # content-hash dedup (not merely the message-id unique constraint) is what
    # keeps a single record.
    batch = [
        GmailMessage(
            gmail_message_id=f"gmail-{i}",
            gmail_thread_id="thread",
            sender=sender,
            subject=subject,
            body_text=body,
            received_at=received,
            recipients=["me@example.com"],
            labels=["INBOX"],
        )
        for i in range(duplicate_ids)
    ]
    content_hash = compute_content_hash(batch[0])

    fake = FakeGmailClient(batch)
    service = GmailSyncService(
        db_session, fake, integration_service=None, settings=_TEST_SETTINGS
    )

    for _ in range(passes):
        service.sync_now(org_id, user_id, connection.id)

    records = list(
        db_session.execute(
            select(EmailMessageRecord).where(
                EmailMessageRecord.organization_id == org_id,
                EmailMessageRecord.content_hash == content_hash,
            )
        ).scalars()
    )
    assert len(records) <= 1

    source_items = list(
        db_session.execute(
            select(SourceItem).where(
                SourceItem.organization_id == org_id,
                SourceItem.source_type == SourceType.EMAIL,
            )
        ).scalars()
    )
    assert len(source_items) <= 1
