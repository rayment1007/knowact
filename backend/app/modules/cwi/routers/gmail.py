"""Gmail manual sync & ingestion routes (Requirements 26, 27).

Exposes the Gmail collector over HTTP. Every route is protected by
:func:`app.dependencies.get_current_user` (missing/invalid session → ``401``)
and scoped to the caller's organization, so an :class:`IntegrationConnection`
or :class:`EmailMessageRecord` belonging to another tenant is indistinguishable
from a missing one and yields ``404`` (Requirements 27.9, 25.5 / Property 13).

Ingested messages flow through the **existing** pipeline; these routes only
kick off sync, list provenance records, and drive the human-in-the-loop review
(confirm/edit/reject/dismiss task suggestions, mark a sender/domain as a
negative signal). No token is ever serialized into a response body.

Routes:
* ``POST /api/gmail/{connection_id}/initial-sync`` — first import with options.
* ``POST /api/gmail/{connection_id}/sync-now`` — manual incremental sync.
* ``GET  /api/gmail/messages`` — list ingested email records.
* ``GET  /api/gmail/suggestions`` — list extracted task suggestions.
* ``POST /api/gmail/suggestions/{id}/confirm|reject|dismiss`` — lifecycle.
* ``PATCH /api/gmail/suggestions/{id}`` — edit a suggestion.
* ``POST /api/gmail/sender-signals`` — mark a sender/domain personal/irrelevant.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.models import User
from app.core.services.ai_provider import ClassificationOutput
from app.database import get_db
from app.dependencies import (
    call_with_fallback,
    get_ai_provider,
    get_current_user,
)
from app.modules.cwi.dependencies import gmail_client, google_oauth_client
from app.modules.cwi.schemas import (
    EmailMessageRecordView,
    EmailTaskSuggestionEdit,
    EmailTaskSuggestionView,
    InitialSyncOptions,
    SenderSignalRequest,
    SenderSignalView,
    SyncRunResponse,
)
from app.modules.cwi.services.gmail_client import GmailClient, GmailClientError
from app.modules.cwi.services.gmail_sync_service import GmailSyncService, SyncRun
from app.modules.cwi.services.google_oauth import (
    GoogleOAuthClient,
    GoogleOAuthError,
)
from app.modules.cwi.services.integration_service import IntegrationService

router = APIRouter(prefix="/gmail", tags=["gmail"])


def _service(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    gmail: GmailClient = Depends(gmail_client),
    oauth: GoogleOAuthClient = Depends(google_oauth_client),
) -> GmailSyncService:
    """Build a :class:`GmailSyncService` bound to the request transaction.

    The classifier routes the AI call through
    :func:`app.dependencies.call_with_fallback`, so a failing ``LLMProvider``
    falls back to the deterministic mock in ``DEVELOPMENT`` or surfaces a
    retriable ``503`` in ``PRODUCTION`` — never a silent downgrade.
    """

    provider = get_ai_provider(settings)

    def classifier(content: str, title: str) -> ClassificationOutput:
        return call_with_fallback(
            provider,
            settings,
            lambda p: p.classify_source_item(content, title),
        )

    integration_service = IntegrationService(db, oauth, settings=settings)
    return GmailSyncService(
        db,
        gmail,
        integration_service=integration_service,
        classifier=classifier,
        settings=settings,
    )


def _run_response(run: SyncRun) -> SyncRunResponse:
    return SyncRunResponse(
        messages_seen=run.messages_seen,
        records_created=run.records_created,
        source_items_created=run.source_items_created,
        skipped_duplicates=run.skipped_duplicates,
        skipped_ineligible=run.skipped_ineligible,
        suggestions_created=run.suggestions_created,
        record_ids=list(run.record_ids),
    )


@router.post("/{connection_id}/initial-sync", response_model=SyncRunResponse)
def initial_sync(
    connection_id: UUID,
    options: InitialSyncOptions,
    service: GmailSyncService = Depends(_service),
    user: User = Depends(get_current_user),
) -> SyncRunResponse | JSONResponse:
    """Start the first Gmail import with the given options (Req 26.1-26.6).

    An invalid options payload is rejected with ``422`` by request validation
    before the service runs, so no partial sync begins (Requirement 26.6). A
    cross-org/missing connection yields ``404``.
    """

    try:
        run = service.start_initial_sync(
            user.organization_id, user.id, connection_id, options
        )
    except (GoogleOAuthError, GmailClientError):
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": "Gmail sync failed. Please retry or reconnect."},
        )
    return _run_response(run)


@router.post("/{connection_id}/sync-now", response_model=SyncRunResponse)
def sync_now(
    connection_id: UUID,
    service: GmailSyncService = Depends(_service),
    user: User = Depends(get_current_user),
) -> SyncRunResponse | JSONResponse:
    """Run a manual incremental sync; dedup keeps it idempotent (Req 27.8)."""

    try:
        run = service.sync_now(user.organization_id, user.id, connection_id)
    except (GoogleOAuthError, GmailClientError):
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": "Gmail sync failed. Please retry or reconnect."},
        )
    return _run_response(run)


@router.get("/messages", response_model=list[EmailMessageRecordView])
def list_messages(
    connection_id: UUID | None = None,
    service: GmailSyncService = Depends(_service),
    user: User = Depends(get_current_user),
) -> list[EmailMessageRecordView]:
    """List the organization's ingested email records (Requirement 27.9)."""

    records = service.list_message_records(
        user.organization_id, user.id, connection_id
    )
    return [EmailMessageRecordView.from_record(r) for r in records]


@router.get("/messages/{record_id}", response_model=EmailMessageRecordView)
def get_message(
    record_id: UUID,
    service: GmailSyncService = Depends(_service),
    user: User = Depends(get_current_user),
) -> EmailMessageRecordView:
    """Return one org-scoped Gmail record for an email deep link."""

    record = service.get_message_record(
        user.organization_id, user.id, record_id
    )
    return EmailMessageRecordView.from_record(record)


@router.delete(
    "/messages/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_message(
    record_id: UUID,
    service: GmailSyncService = Depends(_service),
    user: User = Depends(get_current_user),
) -> Response:
    """Permanently delete an ingested email record and its task suggestions.

    Org-scoped: a cross-org/missing id yields ``404``. Writes one
    ``DELETE_EMAIL_MESSAGE`` audit row in the same transaction.
    """

    service.delete_message_record(user.organization_id, user.id, record_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/suggestions", response_model=list[EmailTaskSuggestionView])
def list_suggestions(
    record_id: UUID | None = None,
    service: GmailSyncService = Depends(_service),
    user: User = Depends(get_current_user),
) -> list[EmailTaskSuggestionView]:
    """List extracted task suggestions for the organization (Requirement 27.6)."""

    suggestions = service.list_task_suggestions(
        user.organization_id, user.id, record_id
    )
    return [EmailTaskSuggestionView.model_validate(s) for s in suggestions]


@router.post(
    "/suggestions/{suggestion_id}/confirm",
    response_model=EmailTaskSuggestionView,
)
def confirm_suggestion(
    suggestion_id: UUID,
    service: GmailSyncService = Depends(_service),
    user: User = Depends(get_current_user),
) -> EmailTaskSuggestionView:
    """Confirm a task suggestion into an ``OPEN`` action (Requirement 27.7)."""

    suggestion, _action = service.confirm_task_suggestion(
        user.organization_id, user.id, suggestion_id
    )
    return EmailTaskSuggestionView.model_validate(suggestion)


@router.patch(
    "/suggestions/{suggestion_id}", response_model=EmailTaskSuggestionView
)
def edit_suggestion(
    suggestion_id: UUID,
    payload: EmailTaskSuggestionEdit,
    service: GmailSyncService = Depends(_service),
    user: User = Depends(get_current_user),
) -> EmailTaskSuggestionView:
    """Apply a partial edit to a task suggestion (Requirement 27.7)."""

    fields = set(payload.model_dump(exclude_unset=True).keys())
    suggestion = service.edit_task_suggestion(
        user.organization_id,
        user.id,
        suggestion_id,
        title=payload.title,
        description=payload.description,
        suggested_due_date=payload.suggested_due_date,
        suggested_owner=payload.suggested_owner,
        related_entity_name=payload.related_entity_name,
        business_entity_id=payload.business_entity_id,
        _fields_set=fields,
    )
    return EmailTaskSuggestionView.model_validate(suggestion)


@router.post(
    "/suggestions/{suggestion_id}/reject",
    response_model=EmailTaskSuggestionView,
)
def reject_suggestion(
    suggestion_id: UUID,
    service: GmailSyncService = Depends(_service),
    user: User = Depends(get_current_user),
) -> EmailTaskSuggestionView:
    """Reject a task suggestion, retaining it as a negative signal (27.7)."""

    suggestion = service.reject_task_suggestion(
        user.organization_id, user.id, suggestion_id
    )
    return EmailTaskSuggestionView.model_validate(suggestion)


@router.post(
    "/suggestions/{suggestion_id}/dismiss",
    response_model=EmailTaskSuggestionView,
)
def dismiss_suggestion(
    suggestion_id: UUID,
    service: GmailSyncService = Depends(_service),
    user: User = Depends(get_current_user),
) -> EmailTaskSuggestionView:
    """Dismiss a task suggestion (Requirement 27.7)."""

    suggestion = service.dismiss_task_suggestion(
        user.organization_id, user.id, suggestion_id
    )
    return EmailTaskSuggestionView.model_validate(suggestion)


@router.delete(
    "/suggestions/{suggestion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_suggestion(
    suggestion_id: UUID,
    service: GmailSyncService = Depends(_service),
    user: User = Depends(get_current_user),
) -> Response:
    """Permanently delete an extracted task suggestion (Requirement 27.9).

    Org-scoped: a cross-org/missing id yields ``404``. Writes one
    ``DELETE_EMAIL_TASK_SUGGESTION`` audit row in the same transaction.
    """

    service.delete_task_suggestion(user.organization_id, user.id, suggestion_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sender-signals", response_model=SenderSignalView)
def mark_sender_signal(
    payload: SenderSignalRequest,
    service: GmailSyncService = Depends(_service),
    user: User = Depends(get_current_user),
) -> SenderSignalView:
    """Mark a sender address or domain personal/irrelevant (Requirement 27.7)."""

    signal = service.mark_sender_signal(
        user.organization_id,
        user.id,
        payload.pattern,
        payload.signal_type,
    )
    return SenderSignalView.model_validate(signal)
