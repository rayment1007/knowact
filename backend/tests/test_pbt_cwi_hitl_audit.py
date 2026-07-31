"""Property-based test P20: CWI human-in-the-loop & audit completeness.

Property 20 (CWI human-in-the-loop & audit completeness): for all CWI-produced
artifacts, immediately after generation their status is ``SUGGESTED`` /
``AI_SUGGESTED`` (never applied without an explicit confirm), AND every CWI
mutation — integration connect / disconnect / revoke, calendar create / update /
cancel, gmail draft create, email send, and document / email deletion — writes
**exactly one** :class:`~app.core.models.AuditLog` row in the **same
transaction** as the mutation.

Each Hypothesis example seeds an isolated org + user and drives the real CWI
services (backed by FAKE Gmail / Calendar / OpenAI transports and a
deterministic :class:`FakeEmbeddingProvider` — no network, no real email) over
the full set of mutations, asserting after each one that the organization's
audit-log count grew by exactly one, and that freshly-generated artifacts are
suggested (not applied). Per Requirement 21.3 the property runs a minimum of
100 examples.

No inline lambdas are used in Hypothesis ``.map``/``.filter`` (they crash on
Python 3.13); all helpers are named functions.

**Validates: Requirements 33.9 / Property 20**
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.core import models as core_models
from app.core.models import ActionItem, ActionStatus, AuditLog, SuggestionStatus
from app.core.services.ai_provider import MockAIProvider
from app.modules.cwi.models import (
    ConnectionStatus,
    EmailDraftStatus,
    EmailMessageRecord,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationService as IntegrationServiceEnum,
)
from app.modules.cwi.schemas import (
    CalendarAddRequest,
    CalendarUpdateRequest,
    CopilotAskRequest,
    EmailDraftRequest,
)
from app.modules.cwi.services.calendar_client import CalendarInfo, FakeCalendarClient
from app.modules.cwi.services.calendar_service import CalendarService
from app.modules.cwi.services.copilot_service import CopilotService
from app.modules.cwi.services.document_service import DocumentService
from app.modules.cwi.services.email_draft_service import EmailDraftService
from app.modules.cwi.services.embedding import FakeEmbeddingProvider
from app.modules.cwi.services.gmail_client import FakeGmailClient
from app.modules.cwi.services.google_oauth import (
    CALENDAR_EVENTS_SCOPE,
    GMAIL_COMPOSE_SCOPE,
    GMAIL_READONLY_SCOPE,
    FakeGoogleOAuthClient,
)
from app.modules.cwi.services.integration_service import IntegrationService
from app.modules.cwi.services.privacy_service import PrivacyService
from app.modules.cwi.services.token_vault import TokenVault
from app.security import hash_password

_PW_HASH = hash_password("pw")
_DIM = 16
_KEY = Fernet.generate_key().decode()
_TEST_SETTINGS = Settings(
    ai_provider="mock",
    embedding_provider="mock",
    embedding_dimension=_DIM,
    token_encryption_key=_KEY,
)
_VAULT = TokenVault(_TEST_SETTINGS)
# A single sandbox for document binaries across all examples.
_STORAGE_ROOT = tempfile.mkdtemp(prefix="p20-storage-")

# Constrained printable text so generators stay in the input space. Named (not
# inline) helpers only — inline lambdas in Hypothesis .map/.filter crash on 3.13.
_TEXT = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=0,
    max_size=40,
)

_CALENDARS = [
    CalendarInfo(
        calendar_id="primary",
        summary="Primary",
        primary=True,
        access_role="owner",
    ),
]


def _audit_count(db: Session, org_id: UUID) -> int:
    """Return the number of audit rows currently scoped to the organization."""

    return int(
        db.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.organization_id == org_id)
        ).scalar_one()
    )


def _seed_org_user(db: Session) -> tuple[UUID, UUID]:
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
    return org.id, user.id


def _seed_connection(
    db: Session,
    org_id: UUID,
    user_id: UUID,
    service: IntegrationServiceEnum,
    scopes: list[str],
) -> IntegrationConnection:
    suffix = uuid4().hex
    connection = IntegrationConnection(
        organization_id=org_id,
        user_id=user_id,
        provider=IntegrationProvider.GOOGLE,
        service=service,
        external_account_id=f"sub-{suffix}",
        account_email=f"acct-{suffix}@example.com",
        granted_scopes_json=list(scopes),
        access_token_encrypted=_VAULT.encrypt("access-token"),
        refresh_token_encrypted=_VAULT.encrypt("refresh-token"),
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        status=ConnectionStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    return connection


def _seed_email_records(
    db: Session, org_id: UUID, connection_id: UUID, n: int
) -> None:
    for _ in range(n):
        suffix = uuid4().hex
        db.add(
            EmailMessageRecord(
                organization_id=org_id,
                integration_connection_id=connection_id,
                source_item_id=None,
                gmail_message_id=f"msg-{suffix}",
                gmail_thread_id=f"thread-{suffix}",
                sender="sender@example.com",
                recipients_json=["to@example.com"],
                subject="Subject",
                received_at=datetime.now(timezone.utc),
                labels_json=["INBOX"],
                content_hash=suffix,
                has_attachments=False,
                stored_raw=True,
            )
        )
    db.flush()


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    n_emails=st.integers(min_value=1, max_value=3),
    n_docs=st.integers(min_value=1, max_value=2),
    subject=_TEXT,
    summary=_TEXT,
)
def test_cwi_hitl_and_audit_completeness(
    db_session: Session,
    n_emails: int,
    n_docs: int,
    subject: str,
    summary: str,
) -> None:
    """Every CWI mutation writes exactly one audit; artifacts start suggested.

    **Validates: Requirements 33.9 / Property 20**
    """

    org_id, user_id = _seed_org_user(db_session)

    oauth = FakeGoogleOAuthClient()
    integration_service = IntegrationService(
        db_session, oauth, token_vault=_VAULT, settings=_TEST_SETTINGS
    )
    privacy = PrivacyService(
        db_session, integration_service=integration_service
    )

    # -- connect (CONNECT_INTEGRATION) --------------------------------------
    begin = integration_service.begin_authorization(
        org_id, user_id, IntegrationServiceEnum.GMAIL
    )
    before = _audit_count(db_session, org_id)
    connected = integration_service.complete_authorization(
        org_id, user_id, code="code", state=begin.state
    )
    assert _audit_count(db_session, org_id) - before == 1

    # -- calendar add / update / cancel -------------------------------------
    calendar_connection = _seed_connection(
        db_session,
        org_id,
        user_id,
        IntegrationServiceEnum.GOOGLE_CALENDAR,
        [CALENDAR_EVENTS_SCOPE],
    )
    action = ActionItem(
        organization_id=org_id,
        title="Confirmed action",
        description="Do the thing",
        status=ActionStatus.OPEN,
        ai_generated=False,
    )
    db_session.add(action)
    db_session.flush()
    calendar = CalendarService(
        db_session,
        FakeCalendarClient(list(_CALENDARS)),
        integration_service=None,
        settings=_TEST_SETTINGS,
    )

    before = _audit_count(db_session, org_id)
    link = calendar.add_action_to_calendar(
        org_id,
        user_id,
        action.id,
        CalendarAddRequest(
            connection_id=calendar_connection.id,
            google_calendar_id="primary",
            summary=summary or None,
        ),
    )
    assert _audit_count(db_session, org_id) - before == 1

    before = _audit_count(db_session, org_id)
    calendar.update_event(
        org_id,
        user_id,
        link.id,
        CalendarUpdateRequest(summary=(summary or "Updated")[:60] or None),
    )
    assert _audit_count(db_session, org_id) - before == 1

    before = _audit_count(db_session, org_id)
    calendar.cancel_event(org_id, user_id, link.id)
    assert _audit_count(db_session, org_id) - before == 1

    # -- gmail AI draft: suggested → create → send --------------------------
    gmail_connection = _seed_connection(
        db_session,
        org_id,
        user_id,
        IntegrationServiceEnum.GMAIL,
        [GMAIL_READONLY_SCOPE, GMAIL_COMPOSE_SCOPE],
    )
    draft_service = EmailDraftService(
        db_session,
        MockAIProvider(),
        FakeGmailClient(),
        integration_service=None,
        settings=_TEST_SETTINGS,
    )
    draft = draft_service.request_draft(
        org_id,
        user_id,
        EmailDraftRequest(
            connection_id=gmail_connection.id,
            to_recipients=["reply@example.com"],
            purpose=(subject or "follow up")[:120],
            tone="professional",
        ),
    )
    # HITL: a freshly generated draft is AI_SUGGESTED, never sent.
    assert draft.status == EmailDraftStatus.AI_SUGGESTED
    assert draft.gmail_sent_message_id is None

    draft_service.approve_draft(org_id, user_id, draft.id)  # no audit

    before = _audit_count(db_session, org_id)
    draft_service.create_gmail_draft(org_id, user_id, draft.id)
    assert _audit_count(db_session, org_id) - before == 1

    before = _audit_count(db_session, org_id)
    sent = draft_service.send_draft(org_id, user_id, draft.id, confirm=True)
    assert _audit_count(db_session, org_id) - before == 1
    assert sent.status == EmailDraftStatus.SENT

    # -- copilot ACT: suggested artifact, no mutation, no audit -------------
    db_session.add(
        core_models.KnowledgeItem(
            organization_id=org_id,
            summary="Acme renewed the annual retainer.",
            evidence_text="Acme renewed the annual retainer.",
            knowledge_type="FACT",
            status=SuggestionStatus.CONFIRMED,
        )
    )
    db_session.flush()
    copilot = CopilotService(
        db_session,
        MockAIProvider(),
        FakeEmbeddingProvider(dimension=_DIM),
        settings=_TEST_SETTINGS,
    )
    before = _audit_count(db_session, org_id)
    response = copilot.ask(
        org_id,
        user_id,
        CopilotAskRequest(
            question="Create a task to follow up with Acme", intent="ACT"
        ),
    )
    # HITL: ask never mutates and never writes an audit row.
    assert _audit_count(db_session, org_id) - before == 0
    if response.suggested_artifact is not None:
        assert response.suggested_artifact.status == "SUGGESTED"

    # -- document deletion (DELETE_DOCUMENT) --------------------------------
    documents = DocumentService(
        db_session,
        _local_storage(),
        FakeEmbeddingProvider(dimension=_DIM),
    )
    for _ in range(n_docs):
        asset = documents.upload(
            org_id,
            user_id,
            filename="notes.txt",
            mime_type="text/plain",
            data=b"some indexed content for chunking",
        )
        documents.process(org_id, user_id, asset.id)
        before = _audit_count(db_session, org_id)
        documents.delete(org_id, user_id, asset.id)
        assert _audit_count(db_session, org_id) - before == 1

    # -- email data deletion (DELETE_EMAIL_DATA) ----------------------------
    _seed_email_records(db_session, org_id, connected.id, n_emails)
    before = _audit_count(db_session, org_id)
    result = privacy.delete_email_data(org_id, user_id, scope="ALL")
    assert _audit_count(db_session, org_id) - before == 1
    assert result.deleted_records == n_emails

    # -- disconnect / revoke ------------------------------------------------
    before = _audit_count(db_session, org_id)
    privacy.disconnect(org_id, user_id, connected.id)
    assert _audit_count(db_session, org_id) - before == 1

    before = _audit_count(db_session, org_id)
    privacy.revoke(org_id, user_id, gmail_connection.id)
    assert _audit_count(db_session, org_id) - before == 1


def _local_storage():
    """Build the temp-dir-backed document storage used across examples."""

    from app.modules.cwi.services.storage import LocalFilesystemStorage

    return LocalFilesystemStorage(_STORAGE_ROOT)
