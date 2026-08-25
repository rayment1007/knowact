"""Privacy, control & deletion routes (M6.7, Requirement 33).

Exposes the user-facing privacy controls over HTTP. Every route is protected by
:func:`app.dependencies.get_current_user` (missing/invalid session → ``401``)
and scoped to the caller's organization, so another tenant's connection,
document, or answer is indistinguishable from a missing one and yields ``404``
(Requirement 33 / Property 13). No token or secret is ever serialized.

Routes:
* ``DELETE /api/privacy/email-data`` — delete imported email data (choose scope).
* ``DELETE /api/privacy/documents/{document_id}`` — delete an uploaded document.
* ``PUT    /api/privacy/retention-policy`` — choose the raw-email retention policy.
* ``GET    /api/privacy/sync-status`` — last successful sync, last error, status.
* ``GET    /api/privacy/answer-provenance/{answer_id}`` — what data grounded an
  AI answer (a.k.a. data-usage).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.models import User
from app.database import get_db
from app.dependencies import get_current_user
from app.modules.cwi.dependencies import (
    embedding_provider,
    google_oauth_client,
    storage_backend,
)
from app.modules.cwi.schemas import (
    AnswerCitationView,
    AnswerProvenanceView,
    EmailDataDeleteRequest,
    EmailDataDeleteResponse,
    RetentionPolicyUpdate,
    RetentionPolicyView,
    SyncStatusView,
)
from app.modules.cwi.services.document_service import DocumentService
from app.modules.cwi.services.embedding import EmbeddingProvider
from app.modules.cwi.services.google_oauth import GoogleOAuthClient
from app.modules.cwi.services.integration_service import IntegrationService
from app.modules.cwi.services.privacy_service import PrivacyService
from app.modules.cwi.services.storage import StorageBackend

router = APIRouter(prefix="/privacy", tags=["privacy"])


def _service(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    oauth: GoogleOAuthClient = Depends(google_oauth_client),
    storage: StorageBackend = Depends(storage_backend),
    embeddings: EmbeddingProvider = Depends(embedding_provider),
) -> PrivacyService:
    """Build a :class:`PrivacyService` bound to the request transaction."""

    integration_service = IntegrationService(db, oauth, settings=settings)
    document_service = DocumentService(db, storage, embeddings)
    return PrivacyService(
        db,
        integration_service=integration_service,
        document_service=document_service,
    )


@router.delete("/email-data", response_model=EmailDataDeleteResponse)
def delete_email_data(
    payload: EmailDataDeleteRequest,
    service: PrivacyService = Depends(_service),
    user: User = Depends(get_current_user),
) -> EmailDataDeleteResponse:
    """Delete imported email data with the cascade (Requirements 33.3, 33.9).

    Removes the selected email records and, in the same transaction, their
    dependent suggestions, any derived chunks/embeddings, and marks derived
    document provenance — retaining confirmed business records — and writes
    exactly one ``DELETE_EMAIL_DATA`` audit row.
    """

    result = service.delete_email_data(
        user.organization_id,
        user.id,
        scope=payload.scope,
        connection_id=payload.connection_id,
        record_ids=payload.record_ids,
    )
    return EmailDataDeleteResponse(
        deleted_records=result.deleted_records,
        deleted_task_suggestions=result.deleted_task_suggestions,
        deleted_chunks=result.deleted_chunks,
        marked_documents_source_deleted=result.marked_documents_source_deleted,
        retained_derived_records=result.retained_derived_records,
    )


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_document(
    document_id: UUID,
    service: PrivacyService = Depends(_service),
    user: User = Depends(get_current_user),
) -> Response:
    """Delete an uploaded document: binary + chunks + embeddings (33.4, 33.9).

    Org-scoped: a cross-org/missing id yields ``404``. Writes exactly one
    ``DELETE_DOCUMENT`` audit row in the same transaction.
    """

    service.delete_document(user.organization_id, user.id, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/retention-policy", response_model=RetentionPolicyView)
def set_retention_policy(
    payload: RetentionPolicyUpdate,
    service: PrivacyService = Depends(_service),
    user: User = Depends(get_current_user),
) -> RetentionPolicyView:
    """Choose the raw-email retention policy and apply it (Requirement 33.5)."""

    policy = service.set_retention_policy(
        user.organization_id,
        user.id,
        mode=payload.mode,
        retention_window_days=payload.retention_window_days,
    )
    return RetentionPolicyView.model_validate(policy)


@router.get("/sync-status", response_model=list[SyncStatusView])
def sync_status(
    service: PrivacyService = Depends(_service),
    user: User = Depends(get_current_user),
) -> list[SyncStatusView]:
    """Return each connection's last-sync time, last error, and status (33.6)."""

    connections = service.sync_status(user.organization_id, user.id)
    return [
        SyncStatusView(
            connection_id=c.id,
            service=c.service,
            account_email=c.account_email,
            status=c.status,
            last_sync_at=c.last_sync_at,
            last_error=c.last_error,
        )
        for c in connections
    ]


@router.get(
    "/answer-provenance/{answer_id}", response_model=AnswerProvenanceView
)
def answer_provenance(
    answer_id: UUID,
    service: PrivacyService = Depends(_service),
    user: User = Depends(get_current_user),
) -> AnswerProvenanceView:
    """Return the exact citations/evidence that grounded an AI answer (33.7).

    User-scoped: another user's, cross-org, or missing answer id yields ``404``.
    """

    log = service.answer_provenance(
        user.organization_id,
        user.id,
        answer_id,
    )
    return AnswerProvenanceView(
        answer_id=log.id,
        question=log.question,
        answer=log.answer,
        intent=log.intent,
        insufficient_evidence=log.insufficient_evidence,
        citations=[
            AnswerCitationView(**_citation_payload(c))
            for c in (log.citations_json or [])
        ],
        evidence=[
            AnswerCitationView(**_citation_payload(e))
            for e in (log.evidence_json or [])
        ],
        created_at=log.created_at,
    )


def _citation_payload(raw: dict) -> dict:
    """Coerce a persisted citation/evidence dict into the view's fields."""

    return {
        "source_type": raw.get("source_type", ""),
        "source_id": raw.get("source_id"),
        "title": raw.get("title", ""),
        "evidence_excerpt": raw.get("evidence_excerpt", ""),
        "timestamp": raw.get("timestamp"),
        "deep_link": raw.get("deep_link", ""),
    }
