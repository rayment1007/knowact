"""AI-assisted Gmail draft routes (M6.6, Requirement 32).

Exposes the AI draft lifecycle over HTTP: request an AI draft, edit it, approve
or reject it, materialize a real Gmail draft, and — via a *separate* explicit
confirmation — send it with local duplicate-send guards.

Every route is protected by :func:`app.dependencies.get_current_user`
(missing/invalid session → ``401``) and scoped to the caller's organization, so
a draft belonging to another tenant is indistinguishable from a missing one and
yields ``404`` (Requirement 32.12 / Property 13). No token or secret is ever
serialized into a response body.

Routes:
* ``POST  /api/email-drafts`` — request an AI draft (``AI_SUGGESTED``).
* ``GET   /api/email-drafts`` — list the user's drafts.
* ``GET   /api/email-drafts/{id}`` — a single draft.
* ``PATCH /api/email-drafts/{id}`` — edit draft content.
* ``POST  /api/email-drafts/{id}/approve`` — approve content (``USER_APPROVED``).
* ``POST  /api/email-drafts/{id}/reject`` — reject the draft (``REJECTED``).
* ``POST  /api/email-drafts/{id}/create-gmail-draft`` — create the Gmail draft.
* ``POST  /api/email-drafts/{id}/send`` — separate explicit confirm → send.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.models import User
from app.core.services.ai_provider import AIProvider
from app.database import get_db
from app.dependencies import get_ai_provider, get_current_user
from app.modules.cwi.dependencies import gmail_client, google_oauth_client
from app.modules.cwi.schemas import (
    EmailDraftEdit,
    EmailDraftRequest,
    EmailDraftSendRequest,
    EmailDraftView,
)
from app.modules.cwi.services.email_draft_service import (
    EmailDraftCreateError,
    EmailDraftSendError,
    EmailDraftService,
)
from app.modules.cwi.services.gmail_client import GmailClient
from app.modules.cwi.services.google_oauth import (
    GoogleOAuthClient,
    GoogleOAuthError,
)
from app.modules.cwi.services.integration_service import IntegrationService

router = APIRouter(prefix="/email-drafts", tags=["email-drafts"])


def _ai_provider(settings: Settings = Depends(get_settings)) -> AIProvider:
    """Resolve the :class:`AIProvider` (mock by default; never a real network)."""

    return get_ai_provider(settings)


def _service(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    provider: AIProvider = Depends(_ai_provider),
    gmail: GmailClient = Depends(gmail_client),
    oauth: GoogleOAuthClient = Depends(google_oauth_client),
) -> EmailDraftService:
    """Build an :class:`EmailDraftService` bound to the request transaction."""

    integration_service = IntegrationService(db, oauth, settings=settings)
    return EmailDraftService(
        db,
        provider,
        gmail,
        integration_service=integration_service,
        settings=settings,
    )


@router.post("", response_model=EmailDraftView)
def request_draft(
    payload: EmailDraftRequest,
    service: EmailDraftService = Depends(_service),
    user: User = Depends(get_current_user),
) -> EmailDraftView:
    """Request an AI draft grounded in permitted context (``AI_SUGGESTED``)."""

    draft = service.request_draft(user.organization_id, user.id, payload)
    return EmailDraftView.from_draft(draft)


@router.get("", response_model=list[EmailDraftView])
def list_drafts(
    service: EmailDraftService = Depends(_service),
    user: User = Depends(get_current_user),
) -> list[EmailDraftView]:
    """List the current user's email drafts, newest first."""

    drafts = service.list_drafts(user.organization_id, user.id)
    return [EmailDraftView.from_draft(d) for d in drafts]


@router.get("/{draft_id}", response_model=EmailDraftView)
def get_draft(
    draft_id: UUID,
    service: EmailDraftService = Depends(_service),
    user: User = Depends(get_current_user),
) -> EmailDraftView:
    """Return a single draft; a cross-org/missing id yields ``404``."""

    draft = service.get_draft(user.organization_id, user.id, draft_id)
    return EmailDraftView.from_draft(draft)


@router.delete(
    "/{draft_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_draft(
    draft_id: UUID,
    service: EmailDraftService = Depends(_service),
    user: User = Depends(get_current_user),
) -> Response:
    """Permanently delete the local draft (never calls Gmail).

    Org-scoped: a cross-org/missing id yields ``404``. Writes one
    ``DELETE_EMAIL_DRAFT`` audit row in the same transaction.
    """

    service.delete_draft(user.organization_id, user.id, draft_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{draft_id}", response_model=EmailDraftView)
def edit_draft(
    draft_id: UUID,
    payload: EmailDraftEdit,
    service: EmailDraftService = Depends(_service),
    user: User = Depends(get_current_user),
) -> EmailDraftView:
    """Edit draft content before it is sent (Requirement 32.5)."""

    draft = service.edit_draft(user.organization_id, user.id, draft_id, payload)
    return EmailDraftView.from_draft(draft)


@router.post("/{draft_id}/approve", response_model=EmailDraftView)
def approve_draft(
    draft_id: UUID,
    service: EmailDraftService = Depends(_service),
    user: User = Depends(get_current_user),
) -> EmailDraftView:
    """Approve draft content → ``USER_APPROVED`` (Requirement 32.5)."""

    draft = service.approve_draft(user.organization_id, user.id, draft_id)
    return EmailDraftView.from_draft(draft)


@router.post("/{draft_id}/reject", response_model=EmailDraftView)
def reject_draft(
    draft_id: UUID,
    service: EmailDraftService = Depends(_service),
    user: User = Depends(get_current_user),
) -> EmailDraftView:
    """Reject the draft → ``REJECTED`` (Requirement 32.5)."""

    draft = service.reject_draft(user.organization_id, user.id, draft_id)
    return EmailDraftView.from_draft(draft)


@router.post("/{draft_id}/create-gmail-draft", response_model=EmailDraftView)
def create_gmail_draft(
    draft_id: UUID,
    service: EmailDraftService = Depends(_service),
    user: User = Depends(get_current_user),
) -> EmailDraftView | JSONResponse:
    """Create a Gmail draft from an approved draft (Requirement 32.6).

    Requires the incremental ``gmail.compose`` scope; without it the call yields
    ``403``. Records one ``CREATE_GMAIL_DRAFT`` audit row in the same
    transaction.
    """

    try:
        draft = service.create_gmail_draft(
            user.organization_id, user.id, draft_id
        )
    except EmailDraftCreateError as exc:
        # A normal response commits the terminal, reconcilable failure state.
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": str(exc)},
        )
    except GoogleOAuthError:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": "Gmail authorization expired. Please reconnect."},
        )
    return EmailDraftView.from_draft(draft)


@router.post("/{draft_id}/send", response_model=EmailDraftView)
def send_draft(
    draft_id: UUID,
    payload: EmailDraftSendRequest,
    service: EmailDraftService = Depends(_service),
    user: User = Depends(get_current_user),
) -> EmailDraftView | JSONResponse:
    """Send the draft after a separate explicit confirmation (Req 32.7, 32.8).

    ``confirm`` must be ``true`` or the send is rejected (``400``). The send is
    locally guarded: a concurrent request observing ``SENDING``/``SENT`` does
    not issue another Gmail call. An ambiguous transport failure is terminal
    until the user checks Gmail Sent.
    """

    try:
        draft = service.send_draft(
            user.organization_id, user.id, draft_id, confirm=payload.confirm
        )
    except EmailDraftSendError as exc:
        # A normal response lets the request transaction commit FAILED instead
        # of rolling it back with the exception.
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": str(exc)},
        )
    return EmailDraftView.from_draft(draft)
