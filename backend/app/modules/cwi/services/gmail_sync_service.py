"""Gmail manual sync & ingestion into the existing pipeline (Requirements 26, 27).

:class:`GmailSyncService` is a **collector, not a parallel pipeline**. It reads
messages through an injected :class:`~app.modules.cwi.services.gmail_client.GmailClient`
and, for each *eligible, not-yet-seen* message, converts it into a
``source_type=EMAIL`` :class:`~app.core.models.SourceItem` via the **existing**
:class:`~app.core.services.ingestion_service.IngestionService`. From that point
the message flows through the unchanged Collect → Classify → gate → Confirm →
Knowledge → Confirm → Act pipeline (Requirement 27.3).

Key guarantees
--------------
* **Dedup / idempotency (Requirement 27.2 / Property 15).** Before ingesting,
  a ``sha256`` ``content_hash`` of the message's normalized content is computed;
  if an :class:`~app.modules.cwi.models.EmailMessageRecord` with the same
  ``(organization_id, content_hash)`` already exists the message is **skipped**,
  so any number of sync passes produce at most one record — and at most one
  downstream ``SourceItem`` — per message.
* **Gates honored (Requirements 27.4, 27.5).** The service only ever produces
  ``SUGGESTED`` artifacts (a suggested classification and, when eligible, a
  ``SUGGESTED`` task suggestion). It never auto-confirms a classification and
  never auto-extracts knowledge, so a ``PERSONAL/SPAM/IRRELEVANT/
  SYSTEM_NOTIFICATION`` message is never auto-converted to knowledge and a
  ``HIGHLY_SENSITIVE`` message is never auto-processed without explicit human
  acknowledgment — those steps require a human confirmation downstream.
* **Negative signals (Requirement 27.7).** A sender/domain a user marked
  ``PERSONAL``/``IRRELEVANT`` biases the suggested classification so future
  ingestion keeps that noise out of the knowledge base.
* **Org scoping & no self-commit.** Every query is org-scoped; the service
  ``add``/``flush``es within the caller's transaction and never commits.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete as sa_delete
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.models import (
    ActionItem,
    ActionStatus,
    BusinessCategory,
    ClassificationResult,
    Relevance,
    Sensitivity,
    SourceItem,
    SourceType,
    SuggestionStatus,
)
from app.core.schemas import SourceItemCreate
from app.core.services.ai_provider import ClassificationOutput, MockAIProvider
from app.core.services.audit_service import AuditService
from app.core.services.classification_service import ClassificationService
from app.core.services.ingestion_service import IngestionService
from app.dependencies import not_found, scope_select
from app.modules.cwi.models import (
    EmailMessageRecord,
    EmailSenderSignal,
    EmailTaskSuggestion,
    ConnectionStatus,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationService as IntegrationServiceEnum,
    SenderSignalType,
)
from app.modules.cwi.schemas import InitialSyncOptions
from app.modules.cwi.services.gmail_client import GmailClient, GmailMessage, GmailClientError
from app.modules.cwi.services.google_oauth import GMAIL_READONLY_SCOPE, GoogleOAuthError
from app.modules.cwi.services.integration_service import IntegrationService

# Relevance values whose content is noise: excluded from knowledge and from
# task-suggestion extraction (mirrors the KnowledgeService privacy gate).
_EXCLUDED_RELEVANCE: frozenset[Relevance] = frozenset(
    {
        Relevance.PERSONAL,
        Relevance.IRRELEVANT,
        Relevance.SPAM,
        Relevance.SYSTEM_NOTIFICATION,
    }
)

_SENT_LABEL = "SENT"

# Audit action types recorded for task-suggestion lifecycle mutations (Req 27.7).
CONFIRM_EMAIL_TASK = "CONFIRM_EMAIL_TASK"
MARK_SENDER_SIGNAL = "MARK_SENDER_SIGNAL"

#: Audit action types recorded when Gmail-derived rows are permanently deleted.
DELETE_EMAIL_MESSAGE = "DELETE_EMAIL_MESSAGE"
DELETE_EMAIL_TASK_SUGGESTION = "DELETE_EMAIL_TASK_SUGGESTION"

# A classifier turns ``(content, title)`` into a structured ClassificationOutput
# (routes wrap the provider call in ``call_with_fallback``).
Classifier = Callable[[str, str], ClassificationOutput]


@dataclass
class SyncRun:
    """Outcome of a sync pass — counts plus the ids of the records touched."""

    messages_seen: int = 0
    records_created: int = 0
    source_items_created: int = 0
    skipped_duplicates: int = 0
    skipped_ineligible: int = 0
    suggestions_created: int = 0
    record_ids: list[UUID] = field(default_factory=list)


def compute_content_hash(message: GmailMessage) -> str:
    """Return the ``sha256`` of the message's normalized content.

    Normalization lower-cases and whitespace-collapses the sender, subject, and
    body so trivially-different renderings of the same message hash identically,
    giving stable dedup across sync passes (Requirement 27.2 / Property 15).
    """

    def _norm(value: str) -> str:
        return " ".join((value or "").split()).lower()

    basis = "\n".join(
        [_norm(message.sender), _norm(message.subject), _norm(message.body_text)]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


class GmailSyncService:
    """Ingest Gmail messages into the existing pipeline with content dedup."""

    def __init__(
        self,
        db: Session,
        gmail_client: GmailClient,
        *,
        integration_service: IntegrationService | None = None,
        classifier: Classifier | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Bind the service to a session and an injected Gmail transport.

        Args:
            db: Request-scoped session (transaction owned by the caller).
            gmail_client: The Gmail transport (a fake in tests).
            integration_service: Used to obtain a valid access token for the
                connection; one is built from ``db`` + a fake OAuth client is
                *not* assumed — callers pass a configured service. When omitted
                the token is fetched lazily via a service built on ``db``.
            classifier: Optional ``(content, title) -> ClassificationOutput``;
                defaults to the deterministic :class:`MockAIProvider`.
            settings: Optional settings; process settings are used if omitted.
        """

        self.db = db
        self.gmail = gmail_client
        self.settings = settings or get_settings()
        self._integration_service = integration_service
        self._classifier = classifier or MockAIProvider().classify_source_item
        self.ingestion = IngestionService(db)
        self.classification = ClassificationService(db)
        self.audit = AuditService(db)

    # -- Scoped lookups -----------------------------------------------------

    def _get_connection(
        self, org_id: UUID, connection_id: UUID, user_id: UUID | None = None
    ) -> IntegrationConnection:
        stmt = scope_select(
            select(IntegrationConnection), IntegrationConnection, org_id
        ).where(IntegrationConnection.id == connection_id)
        if user_id is not None:
            stmt = stmt.where(IntegrationConnection.user_id == user_id)
        connection = self.db.execute(stmt).scalar_one_or_none()
        if connection is None:
            raise not_found("Integration connection not found.")
        if (
            connection.provider != IntegrationProvider.GOOGLE
            or connection.service != IntegrationServiceEnum.GMAIL
            or connection.status != ConnectionStatus.CONNECTED
            or GMAIL_READONLY_SCOPE not in (connection.granted_scopes_json or [])
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "A connected Gmail account with gmail.readonly authorization "
                    "is required. Reconnect Gmail and try again."
                ),
            )
        return connection

    def _access_token(
        self, org_id: UUID, user_id: UUID, connection_id: UUID
    ) -> str:
        """Obtain a valid (refreshed if needed) access token for the connection.

        Uses the injected :class:`IntegrationService` when provided. The token
        exists only transiently in memory for the outbound Gmail call and is
        never persisted or logged.
        """

        if self._integration_service is not None:
            return self._integration_service.get_valid_access_token(
                org_id,
                user_id,
                connection_id,
                expected_service=IntegrationServiceEnum.GMAIL,
                required_scopes=(GMAIL_READONLY_SCOPE,),
            )
        # Fallback: no OAuth client available; the fake Gmail client ignores the
        # token anyway, so return an empty transient value.
        return ""

    def _matching_sender_signal(
        self, org_id: UUID, user_id: UUID, sender: str
    ) -> SenderSignalType | None:
        """Return this user's signal for ``sender``'s address/domain, if any."""

        address = (sender or "").strip().lower()
        if not address:
            return None
        patterns = [address]
        if "@" in address:
            patterns.append(address.split("@", 1)[1])
        stmt = scope_select(
            select(EmailSenderSignal), EmailSenderSignal, org_id
        ).where(
            EmailSenderSignal.created_by == user_id,
            EmailSenderSignal.pattern.in_(patterns),
        )
        signal = self.db.execute(stmt).scalars().first()
        return signal.signal_type if signal is not None else None

    def _find_existing(
        self, org_id: UUID, connection_id: UUID, content_hash: str
    ) -> EmailMessageRecord | None:
        """Return an existing record with the same connection-scoped hash."""

        stmt = scope_select(
            select(EmailMessageRecord), EmailMessageRecord, org_id
        ).where(
            EmailMessageRecord.integration_connection_id == connection_id,
            EmailMessageRecord.content_hash == content_hash,
        )
        return self.db.execute(stmt).scalars().first()

    # -- Eligibility (Requirements 26.1, 26.2, 26.3) ------------------------

    def _is_eligible(
        self, message: GmailMessage, opts: InitialSyncOptions, now: datetime
    ) -> bool:
        # Date range: only messages received within the selected window.
        received = message.received_at
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
        cutoff = now - timedelta(days=opts.date_range_days)
        if received < cutoff:
            return False
        # Sent mail include/exclude choice.
        if not opts.include_sent and _SENT_LABEL in set(message.labels):
            return False
        # Label filter: only messages carrying a selected label.
        if opts.labels:
            if not set(opts.labels).intersection(set(message.labels)):
                return False
        return True

    # -- Ingestion of a single message --------------------------------------

    def _ingest_message(
        self,
        org_id: UUID,
        connection: IntegrationConnection,
        message: GmailMessage,
        opts: InitialSyncOptions,
        run: SyncRun,
    ) -> EmailMessageRecord | None:
        """Convert one eligible message into a SourceItem + EmailMessageRecord.

        Skips (returns ``None``) when a record with the same
        ``(organization_id, content_hash)`` already exists, so re-syncing a
        message never creates a second record or SourceItem (Requirement 27.2 /
        Property 15). On a fresh message it:

        1. persists a ``source_type=EMAIL`` SourceItem via the existing
           IngestionService (Requirement 27.3);
        2. persists the provenance-preserving EmailMessageRecord (Req 27.1);
        3. produces a ``SUGGESTED`` classification (biased by any sender/domain
           negative signal, Req 27.7) — never auto-confirmed;
        4. when the suggested classification is work-relevant, not
           highly-sensitive, and looks like a task, persists a ``SUGGESTED``
           :class:`EmailTaskSuggestion` (Requirement 27.6).
        """

        content_hash = compute_content_hash(message)
        if self._find_existing(org_id, connection.id, content_hash) is not None:
            run.skipped_duplicates += 1
            return None

        # 1) Collect: create the EMAIL SourceItem through the existing service.
        title = message.subject.strip() or "(no subject)"
        body = message.body_text.strip() or title
        received = message.received_at
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
        source_item = self.ingestion.ingest(
            org_id,
            connection.user_id,
            SourceItemCreate(
                source_type=SourceType.EMAIL,
                title=title[:512],
                content=body,
                received_at=received,
            ),
        )
        run.source_items_created += 1

        # 2) Provenance record linking connection <-> pipeline.
        record = EmailMessageRecord(
            organization_id=org_id,
            integration_connection_id=connection.id,
            source_item_id=source_item.id,
            gmail_message_id=message.gmail_message_id,
            gmail_thread_id=message.gmail_thread_id,
            sender=message.sender,
            recipients_json=list(message.recipients),
            subject=message.subject[:1024],
            received_at=received,
            labels_json=list(message.labels),
            content_hash=content_hash,
            has_attachments=bool(message.has_attachments),
            stored_raw=(opts.storage_policy == "RAW_AND_EXTRACTED"),
        )
        self.db.add(record)
        self.db.flush()
        run.records_created += 1
        run.record_ids.append(record.id)

        # 3) Classify (SUGGESTED only) — biased by a sender/domain signal.
        signal = self._matching_sender_signal(
            org_id,
            connection.user_id,
            message.sender,
        )
        classifier = self._classifier
        if signal is not None:
            forced = (
                Relevance.PERSONAL
                if signal == SenderSignalType.PERSONAL
                else Relevance.IRRELEVANT
            )

            def signalled_classifier(
                content: str, item_title: str
            ) -> ClassificationOutput:
                base = self._classifier(content, item_title)
                return base.model_copy(update={"relevance": forced})

            classifier = signalled_classifier

        classification = self.classification.classify(
            org_id, source_item.id, classifier=classifier
        )

        # 4) Task suggestion — only for eligible (non-noise, non-highly-sensitive)
        #    messages that look like a task. Never auto-confirmed.
        self._maybe_create_task_suggestion(
            org_id, connection, message, source_item, record, classification
        )
        return record

    def _ingest_message_race_safe(
        self,
        org_id: UUID,
        connection: IntegrationConnection,
        message: GmailMessage,
        opts: InitialSyncOptions,
        run: SyncRun,
    ) -> EmailMessageRecord | None:
        """Ingest inside a savepoint and reconcile a concurrent duplicate."""

        source_count = run.source_items_created
        record_count = run.records_created
        record_ids_count = len(run.record_ids)
        try:
            with self.db.begin_nested():
                return self._ingest_message(
                    org_id, connection, message, opts, run
                )
        except IntegrityError:
            # The database uniqueness constraints are the final race guard.
            # Roll back only this message's savepoint, then verify the winner
            # really is the same Gmail message/content before treating it as a
            # harmless duplicate.
            content_hash = compute_content_hash(message)
            existing = self.db.execute(
                scope_select(
                    select(EmailMessageRecord), EmailMessageRecord, org_id
                ).where(
                    EmailMessageRecord.integration_connection_id
                    == connection.id,
                    or_(
                        EmailMessageRecord.content_hash == content_hash,
                        EmailMessageRecord.gmail_message_id
                        == message.gmail_message_id,
                    ),
                )
            ).scalars().first()
            if existing is None:
                raise
            run.source_items_created = source_count
            run.records_created = record_count
            del run.record_ids[record_ids_count:]
            run.skipped_duplicates += 1
            return None

    def _maybe_create_task_suggestion(
        self,
        org_id: UUID,
        connection: IntegrationConnection,
        message: GmailMessage,
        source_item: SourceItem,
        record: EmailMessageRecord,
        classification: ClassificationResult,
    ) -> EmailTaskSuggestion | None:
        """Persist a ``SUGGESTED`` task suggestion when the message warrants one.

        Gating mirrors the pipeline's privacy/sensitivity gates: a noise
        relevance or a ``HIGHLY_SENSITIVE`` sensitivity yields no suggestion, so
        nothing is auto-produced from content that must not become knowledge
        without explicit human handling (Requirements 27.4, 27.5).
        """

        if classification.relevance in _EXCLUDED_RELEVANCE:
            return None
        if classification.sensitivity == Sensitivity.HIGHLY_SENSITIVE:
            return None
        if classification.business_category != BusinessCategory.TASK:
            return None

        provider = self.settings.ai_provider
        model = (
            self.settings.llm_model
            if self.settings.ai_provider == "llm"
            else "mock"
        )
        evidence = message.body_text.strip() or message.subject.strip() or "(no content)"
        suggestion = EmailTaskSuggestion(
            organization_id=org_id,
            email_message_record_id=record.id,
            source_item_id=source_item.id,
            business_entity_id=None,
            title=(message.subject.strip() or "(no subject)")[:512],
            description=message.body_text.strip() or None,
            suggested_due_date=None,
            suggested_owner=message.sender or None,
            related_entity_name=None,
            gmail_message_id=message.gmail_message_id,
            evidence_text=evidence,
            ai_provider=provider,
            ai_model=model,
            status=SuggestionStatus.SUGGESTED,
        )
        self.db.add(suggestion)
        self.db.flush()
        return suggestion

    # -- Public API ---------------------------------------------------------

    def start_initial_sync(
        self,
        org_id: UUID,
        user_id: UUID,
        connection_id: UUID,
        opts: InitialSyncOptions,
    ) -> SyncRun:
        """Run the first import applying the user's :class:`InitialSyncOptions`.

        Fetches messages via the injected Gmail client and ingests every
        eligible, not-yet-seen message (Requirements 26.1-26.5). Invalid options
        are rejected by Pydantic with ``422`` before this runs, so a bad payload
        never starts a partial sync (Requirement 26.6). Updates the connection's
        ``last_sync_at`` on success.
        """

        connection = self._get_connection(org_id, connection_id, user_id)
        label_ids = list(opts.labels) if opts.labels else None
        messages = self._read_messages(org_id, user_id, connection, label_ids)

        now = datetime.now(timezone.utc)
        run = SyncRun()
        for message in messages:
            run.messages_seen += 1
            if not self._is_eligible(message, opts, now):
                run.skipped_ineligible += 1
                continue
            self._ingest_message_race_safe(
                org_id, connection, message, opts, run
            )

        run.suggestions_created = self._count_suggestions(org_id, run.record_ids)
        connection.last_sync_at = now
        connection.last_error = None
        self.db.add(connection)
        self.db.flush()
        return run

    def sync_now(
        self, org_id: UUID, user_id: UUID, connection_id: UUID
    ) -> SyncRun:
        """Run a manual incremental sync (Requirement 27.8).

        Ingests every not-yet-seen message the client currently returns; the
        content-hash dedup guarantees re-syncing already-ingested messages
        creates nothing new (Requirement 27.2 / Property 15). Uses a permissive
        default option set (no date/label narrowing, include sent).
        """

        connection = self._get_connection(org_id, connection_id, user_id)
        messages = self._read_messages(org_id, user_id, connection)

        # Permissive defaults for an incremental pull; dedup does the rest.
        opts = InitialSyncOptions(
            date_range_days=90,
            labels=None,
            include_sent=True,
            attachment_handling="METADATA_ONLY",
            storage_policy="EXTRACTED_ONLY",
        )
        now = datetime.now(timezone.utc)
        run = SyncRun()
        for message in messages:
            run.messages_seen += 1
            # sync_now does not apply the initial date window — ingest anything
            # not yet seen (dedup keeps it idempotent).
            if not opts.include_sent and _SENT_LABEL in set(message.labels):
                run.skipped_ineligible += 1
                continue
            self._ingest_message_race_safe(
                org_id, connection, message, opts, run
            )

        run.suggestions_created = self._count_suggestions(org_id, run.record_ids)
        connection.last_sync_at = now
        connection.last_error = None
        self.db.add(connection)
        self.db.flush()
        return run

    def _read_messages(self, org_id, user_id, connection, label_ids=None):
        try:
            token = self._access_token(org_id, user_id, connection.id)
            return self.gmail.list_messages(access_token=token, label_ids=label_ids)
        except (GmailClientError, GoogleOAuthError):
            connection.last_error = "Gmail sync failed. Please retry or reconnect."
            self.db.flush()
            raise

    def _count_suggestions(
        self, org_id: UUID, record_ids: list[UUID]
    ) -> int:
        if not record_ids:
            return 0
        stmt = scope_select(
            select(EmailTaskSuggestion), EmailTaskSuggestion, org_id
        ).where(EmailTaskSuggestion.email_message_record_id.in_(record_ids))
        return len(list(self.db.execute(stmt).scalars().all()))

    # -- Message records & task suggestions (read + lifecycle) --------------

    def list_message_records(
        self,
        org_id: UUID,
        user_id: UUID,
        connection_id: UUID | None = None,
    ) -> list[EmailMessageRecord]:
        """Return the user's ingested email records, newest first (Req 27.9)."""

        stmt = (
            scope_select(select(EmailMessageRecord), EmailMessageRecord, org_id)
            .join(
                IntegrationConnection,
                IntegrationConnection.id
                == EmailMessageRecord.integration_connection_id,
            )
            .where(
                IntegrationConnection.organization_id == org_id,
                IntegrationConnection.user_id == user_id,
            )
        )
        if connection_id is not None:
            stmt = stmt.where(
                EmailMessageRecord.integration_connection_id == connection_id
            )
        stmt = stmt.order_by(
            EmailMessageRecord.received_at.desc(),
            EmailMessageRecord.id.desc(),
        )
        return list(self.db.execute(stmt).scalars().all())

    def get_message_record(
        self, org_id: UUID, user_id: UUID, record_id: UUID
    ) -> EmailMessageRecord:
        """Return one org-and-user-scoped record or ``404``."""

        stmt = (
            scope_select(select(EmailMessageRecord), EmailMessageRecord, org_id)
            .join(
                IntegrationConnection,
                IntegrationConnection.id
                == EmailMessageRecord.integration_connection_id,
            )
            .where(
                EmailMessageRecord.id == record_id,
                IntegrationConnection.organization_id == org_id,
                IntegrationConnection.user_id == user_id,
            )
        )
        record = self.db.execute(stmt).scalar_one_or_none()
        if record is None:
            raise not_found("Email message record not found.")
        return record

    def list_task_suggestions(
        self,
        org_id: UUID,
        user_id: UUID,
        record_id: UUID | None = None,
    ) -> list[EmailTaskSuggestion]:
        """Return the user's task suggestions, newest first (Requirement 27.6)."""

        stmt = (
            scope_select(select(EmailTaskSuggestion), EmailTaskSuggestion, org_id)
            .join(
                EmailMessageRecord,
                EmailMessageRecord.id
                == EmailTaskSuggestion.email_message_record_id,
            )
            .join(
                IntegrationConnection,
                IntegrationConnection.id
                == EmailMessageRecord.integration_connection_id,
            )
            .where(
                EmailMessageRecord.organization_id == org_id,
                IntegrationConnection.organization_id == org_id,
                IntegrationConnection.user_id == user_id,
            )
        )
        if record_id is not None:
            stmt = stmt.where(
                EmailTaskSuggestion.email_message_record_id == record_id
            )
        stmt = stmt.order_by(
            EmailTaskSuggestion.created_at.desc(),
            EmailTaskSuggestion.id.desc(),
        )
        return list(self.db.execute(stmt).scalars().all())

    def _get_task_suggestion(
        self, org_id: UUID, user_id: UUID, suggestion_id: UUID
    ) -> EmailTaskSuggestion:
        stmt = (
            scope_select(select(EmailTaskSuggestion), EmailTaskSuggestion, org_id)
            .join(
                EmailMessageRecord,
                EmailMessageRecord.id
                == EmailTaskSuggestion.email_message_record_id,
            )
            .join(
                IntegrationConnection,
                IntegrationConnection.id
                == EmailMessageRecord.integration_connection_id,
            )
            .where(
                EmailTaskSuggestion.id == suggestion_id,
                EmailMessageRecord.organization_id == org_id,
                IntegrationConnection.organization_id == org_id,
                IntegrationConnection.user_id == user_id,
            )
        )
        suggestion = self.db.execute(stmt).scalar_one_or_none()
        if suggestion is None:
            raise not_found("Email task suggestion not found.")
        return suggestion

    def confirm_task_suggestion(
        self, org_id: UUID, user_id: UUID, suggestion_id: UUID
    ) -> tuple[EmailTaskSuggestion, ActionItem]:
        """Confirm a task suggestion into an ``OPEN`` action (Requirement 27.7).

        Sets the suggestion to ``CONFIRMED`` and creates one AI-generated
        :class:`~app.core.models.ActionItem` carrying the suggestion's title,
        description, due date, related entity, and evidence — recording exactly
        one ``CONFIRM_EMAIL_TASK`` audit row in the same transaction (Req 13.1 /
        Property 20).
        """

        suggestion = self._get_task_suggestion(org_id, user_id, suggestion_id)
        suggestion.status = SuggestionStatus.CONFIRMED

        action = ActionItem(
            organization_id=org_id,
            business_entity_id=suggestion.business_entity_id,
            title=suggestion.title,
            description=suggestion.description,
            owner_id=None,
            due_date=suggestion.suggested_due_date,
            status=ActionStatus.OPEN,
            evidence_text=suggestion.evidence_text,
            ai_generated=True,
        )
        self.db.add_all([suggestion, action])
        self.db.flush()

        self.audit.record(
            org_id=org_id,
            actor_id=user_id,
            action_type=CONFIRM_EMAIL_TASK,
            target_type="EmailTaskSuggestion",
            target_id=suggestion.id,
            detail={
                "action_item_id": str(action.id),
                "gmail_message_id": suggestion.gmail_message_id,
            },
        )
        self.db.flush()
        return suggestion, action

    def edit_task_suggestion(
        self,
        org_id: UUID,
        user_id: UUID,
        suggestion_id: UUID,
        *,
        title: str | None = None,
        description: str | None = None,
        suggested_due_date=None,
        suggested_owner: str | None = None,
        related_entity_name: str | None = None,
        business_entity_id: UUID | None = None,
        _fields_set: set[str] | None = None,
    ) -> EmailTaskSuggestion:
        """Apply a partial edit to a ``SUGGESTED`` task suggestion (Req 27.7).

        Only fields present in ``_fields_set`` are applied so an omitted field is
        left untouched; the suggestion remains ``SUGGESTED`` for later
        confirm/reject.
        """

        suggestion = self._get_task_suggestion(org_id, user_id, suggestion_id)
        fields = _fields_set or set()
        if "title" in fields and title is not None:
            suggestion.title = title
        if "description" in fields:
            suggestion.description = description
        if "suggested_due_date" in fields:
            suggestion.suggested_due_date = suggested_due_date
        if "suggested_owner" in fields:
            suggestion.suggested_owner = suggested_owner
        if "related_entity_name" in fields:
            suggestion.related_entity_name = related_entity_name
        if "business_entity_id" in fields:
            suggestion.business_entity_id = business_entity_id
        self.db.add(suggestion)
        self.db.flush()
        return suggestion

    def reject_task_suggestion(
        self, org_id: UUID, user_id: UUID, suggestion_id: UUID
    ) -> EmailTaskSuggestion:
        """Reject a task suggestion, retaining it as a negative signal (27.7)."""

        suggestion = self._get_task_suggestion(org_id, user_id, suggestion_id)
        suggestion.status = SuggestionStatus.REJECTED
        self.db.add(suggestion)
        self.db.flush()
        return suggestion

    def dismiss_task_suggestion(
        self, org_id: UUID, user_id: UUID, suggestion_id: UUID
    ) -> EmailTaskSuggestion:
        """Dismiss a task suggestion (retained as ``REJECTED``) (Req 27.7)."""

        return self.reject_task_suggestion(org_id, user_id, suggestion_id)

    # -- Delete (permanent) -------------------------------------------------

    def delete_message_record(
        self, org_id: UUID, user_id: UUID, record_id: UUID
    ) -> None:
        """Permanently delete an ingested email record and its suggestions.

        Resolves the org-scoped record (``404`` if missing/cross-tenant) and, in
        one transaction, deletes the dependent :class:`EmailTaskSuggestion` rows
        that reference it (their ``email_message_record_id`` FK is non-nullable,
        so they must go first), deletes the record, and records exactly one
        ``DELETE_EMAIL_MESSAGE`` audit row. The derived ``SourceItem`` (if any)
        is retained — this only removes the Gmail provenance record.

        Args:
            org_id: The tenant the record must belong to.
            user_id: The user performing the delete (audit actor).
            record_id: The email message record to delete.

        Raises:
            HTTPException: ``404`` if the record does not exist for the org.
        """

        record = self.get_message_record(org_id, user_id, record_id)

        from app.modules.cwi.services.source_proposal_service import discard_unapproved
        discard_unapproved(self.db, org_id, "email", [record.id])

        self.db.execute(
            sa_delete(EmailTaskSuggestion).where(
                EmailTaskSuggestion.organization_id == org_id,
                EmailTaskSuggestion.email_message_record_id == record.id,
            )
        )

        deleted_id = record.id
        self.db.delete(record)
        self.db.flush()

        self.audit.record(
            org_id=org_id,
            actor_id=user_id,
            action_type=DELETE_EMAIL_MESSAGE,
            target_type="EmailMessageRecord",
            target_id=deleted_id,
            detail={},
        )
        self.db.flush()

    def delete_task_suggestion(
        self, org_id: UUID, user_id: UUID, suggestion_id: UUID
    ) -> None:
        """Permanently delete an extracted task suggestion (Requirement 27.9).

        Resolves the org-scoped suggestion (``404`` if missing/cross-tenant) and,
        in one transaction, deletes it and records exactly one
        ``DELETE_EMAIL_TASK_SUGGESTION`` audit row.

        Args:
            org_id: The tenant the suggestion must belong to.
            user_id: The user performing the delete (audit actor).
            suggestion_id: The task suggestion to delete.

        Raises:
            HTTPException: ``404`` if the suggestion does not exist for the org.
        """

        suggestion = self._get_task_suggestion(org_id, user_id, suggestion_id)

        deleted_id = suggestion.id
        self.db.delete(suggestion)
        self.db.flush()

        self.audit.record(
            org_id=org_id,
            actor_id=user_id,
            action_type=DELETE_EMAIL_TASK_SUGGESTION,
            target_type="EmailTaskSuggestion",
            target_id=deleted_id,
            detail={},
        )
        self.db.flush()

    # -- Sender/domain negative signal (Requirement 27.7) -------------------

    def mark_sender_signal(
        self,
        org_id: UUID,
        user_id: UUID,
        pattern: str,
        signal_type: SenderSignalType,
        connection_id: UUID | None = None,
    ) -> EmailSenderSignal:
        """Mark a sender address or domain ``PERSONAL``/``IRRELEVANT``.

        Records (or updates) a single negative signal per user and pattern,
        reused only by that user's future classification (Requirement 27.7).
        ``pattern`` is lower-cased for stable matching.
        """

        normalized = (pattern or "").strip().lower()
        if not normalized:
            raise not_found("Sender pattern is required.")

        stmt = scope_select(
            select(EmailSenderSignal), EmailSenderSignal, org_id
        ).where(
            EmailSenderSignal.created_by == user_id,
            EmailSenderSignal.pattern == normalized,
        )
        signal = self.db.execute(stmt).scalar_one_or_none()
        if signal is None:
            signal = EmailSenderSignal(
                organization_id=org_id,
                integration_connection_id=connection_id,
                created_by=user_id,
                pattern=normalized,
                signal_type=signal_type,
            )
            self.db.add(signal)
        else:
            signal.signal_type = signal_type
            self.db.add(signal)
        self.db.flush()

        self.audit.record(
            org_id=org_id,
            actor_id=user_id,
            action_type=MARK_SENDER_SIGNAL,
            target_type="EmailSenderSignal",
            target_id=signal.id,
            detail={"pattern": normalized, "signal_type": signal_type.value},
        )
        self.db.flush()
        return signal


__all__ = ["GmailSyncService", "SyncRun", "compute_content_hash"]
