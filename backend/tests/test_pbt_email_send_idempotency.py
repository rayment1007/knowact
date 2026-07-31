"""Property-based test P17: Email send idempotency (no duplicate sends).

Property 17 (Email send idempotency): for an approved
:class:`~app.modules.cwi.models.EmailDraft` and any number of send attempts
``n >= 1`` (including retried/concurrent attempts), **exactly one** message is
sent and every later attempt returns the existing ``gmail_sent_message_id``
without sending again.

Each Hypothesis example seeds an isolated org + user + Gmail connection (with the
incremental ``gmail.compose`` scope) and an ``USER_APPROVED`` draft, then drives
the :class:`~app.modules.cwi.services.email_draft_service.EmailDraftService`
(backed by a FAKE Gmail client — no network, no real email) over ``n`` explicit
send confirmations and asserts the invariant. Per Requirement 21.3 the property
runs a minimum of 100 examples.

**Validates: Requirements 32.8 / Property 17**
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
from app.core.services.ai_provider import MockAIProvider
from app.modules.cwi.models import (
    ConnectionStatus,
    EmailDraft,
    EmailDraftStatus,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationService as IntegrationServiceEnum,
)
from app.modules.cwi.services.email_draft_service import EmailDraftService
from app.modules.cwi.services.gmail_client import FakeGmailClient
from app.modules.cwi.services.google_oauth import (
    GMAIL_COMPOSE_SCOPE,
    GMAIL_READONLY_SCOPE,
)
from app.security import hash_password

_PW_HASH = hash_password("pw")
_TEST_SETTINGS = Settings(ai_provider="mock")

# Constrained, printable, bounded subject/body text so generators stay in the
# input space. Named (not inline) helpers are used everywhere below because
# inline lambdas in Hypothesis .map/.filter crash on Python 3.13.
_TEXT = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=0,
    max_size=80,
)


def _seed_approved_draft(db: Session) -> EmailDraft:
    """Seed an isolated org + user + Gmail connection + USER_APPROVED draft."""

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
        account_email=f"gmail-{suffix}@example.com",
        granted_scopes_json=[GMAIL_READONLY_SCOPE, GMAIL_COMPOSE_SCOPE],
        access_token_encrypted=b"ciphertext",
        refresh_token_encrypted=b"ciphertext",
        token_expires_at=datetime.now(timezone.utc),
        status=ConnectionStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    draft = EmailDraft(
        organization_id=org.id,
        user_id=user.id,
        integration_connection_id=connection.id,
        source_item_id=None,
        business_entity_id=None,
        gmail_thread_id=f"thread-{suffix}",
        to_recipients_json=[f"recipient-{suffix}@example.com"],
        subject="Re: Proposal",
        body_text="Approved body.",
        tone="professional",
        purpose="follow_up",
        referenced_facts_json=[],
        warnings_json=[],
        status=EmailDraftStatus.USER_APPROVED,
        gmail_draft_id=None,
        gmail_sent_message_id=None,
        send_idempotency_key=uuid4().hex,
    )
    db.add(draft)
    db.flush()
    return draft


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    attempts=st.integers(min_value=1, max_value=8),
    edits=st.lists(_TEXT, min_size=0, max_size=4),
)
def test_email_send_idempotency(
    db_session: Session,
    attempts: int,
    edits: list[str],
) -> None:
    """N send attempts on one approved draft send exactly one message.

    **Validates: Requirements 32.8 / Property 17**
    """

    draft = _seed_approved_draft(db_session)
    org_id = draft.organization_id
    user_id = draft.user_id
    draft_id = draft.id

    fake_gmail = FakeGmailClient()
    # No IntegrationService: the fake Gmail client ignores the access token, so
    # no OAuth wiring (and no network) is needed.
    service = EmailDraftService(
        db_session,
        MockAIProvider(),
        fake_gmail,
        integration_service=None,
        settings=_TEST_SETTINGS,
    )

    sent_ids: list[str | None] = []
    for _ in range(attempts):
        result = service.send_draft(org_id, user_id, draft_id, confirm=True)
        sent_ids.append(result.gmail_sent_message_id)

    # Exactly one message was ever sent through the transport (Property 17).
    assert fake_gmail.send_calls == 1

    # Every attempt observed the SAME sent message id (the first minted it; each
    # retry returned it without sending again).
    distinct = {sid for sid in sent_ids if sid is not None}
    assert len(distinct) == 1
    assert all(sid == sent_ids[0] for sid in sent_ids)

    # The persisted draft is SENT with exactly one sent message id.
    persisted = db_session.execute(
        select(EmailDraft).where(EmailDraft.id == draft_id)
    ).scalar_one()
    assert persisted.status == EmailDraftStatus.SENT
    assert persisted.gmail_sent_message_id == sent_ids[0]
