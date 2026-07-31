"""Enterprise Copilot routes (M6.5, Requirements 30, 31).

Exposes the grounded Copilot over HTTP:

* ``POST /api/copilot/ask`` — a grounded question → an answer + citations, or a
  ``SUGGESTED`` artifact (DRAFT/ACT) that requires explicit confirmation.
* ``GET  /api/copilot/suggested-questions`` — a fixed set plus a context-derived
  dynamic set (Requirement 30.6).
* ``POST /api/copilot/confirm`` — the *separate*, explicit confirm-before-mutate
  step for a DRAFT/ACT artifact (Requirement 30.3, 31.3).

Every route is protected by :func:`app.dependencies.get_current_user`
(missing/invalid session → ``401``) and scoped to the caller's organization, so
another tenant's data is unreachable (cross-org yields ``404`` in the services
it delegates to). Tokens are never involved and never serialized.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.models import User
from app.core.services.ai_provider import AIProvider
from app.database import get_db
from app.dependencies import get_ai_provider, get_current_user
from app.modules.cwi.dependencies import embedding_provider
from app.modules.cwi.schemas import (
    CopilotAskRequest,
    CopilotConfirmRequest,
    CopilotConfirmResponse,
    CopilotResponse,
    SuggestedQuestionsResponse,
)
from app.modules.cwi.services.copilot_service import CopilotService
from app.modules.cwi.services.embedding import EmbeddingProvider

router = APIRouter(tags=["copilot"])


def _ai_provider(settings: Settings = Depends(get_settings)) -> AIProvider:
    """Resolve the :class:`AIProvider` (mock by default; never a real network).

    A thin dependency wrapper so tests can override provider selection and so
    the deterministic ``MockAIProvider`` is used whenever ``AI_PROVIDER=mock``.
    """

    return get_ai_provider(settings)


def _service(
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(_ai_provider),
    embeddings: EmbeddingProvider = Depends(embedding_provider),
    settings: Settings = Depends(get_settings),
) -> CopilotService:
    """Build a :class:`CopilotService` bound to the request transaction."""

    return CopilotService(db, provider, embeddings, settings=settings)


@router.post("/copilot/ask", response_model=CopilotResponse)
def ask(
    payload: CopilotAskRequest,
    service: CopilotService = Depends(_service),
    user: User = Depends(get_current_user),
) -> CopilotResponse:
    """Answer a grounded question or return a ``SUGGESTED`` artifact.

    No mutation occurs here (Requirement 31.3). Every returned citation is
    validated against the bounded evidence set; when evidence is insufficient
    the response sets ``insufficient_evidence`` and does not guess.
    """

    return service.ask(user.organization_id, user.id, payload)


@router.get(
    "/copilot/suggested-questions", response_model=SuggestedQuestionsResponse
)
def suggested_questions(
    service: CopilotService = Depends(_service),
    user: User = Depends(get_current_user),
) -> SuggestedQuestionsResponse:
    """Return the fixed + dynamic, org-scoped suggested questions (30.6)."""

    return service.suggested_questions(user.organization_id, user.id)


@router.post("/copilot/confirm", response_model=CopilotConfirmResponse)
def confirm(
    payload: CopilotConfirmRequest,
    service: CopilotService = Depends(_service),
    user: User = Depends(get_current_user),
) -> CopilotConfirmResponse:
    """Apply a previously-``SUGGESTED`` artifact (confirm-before-mutate).

    Applying an ``ACTION_ITEM`` creates a real action and writes exactly one
    audit row in the same transaction (Requirement 31.4). Unsupported kinds
    yield ``422``.
    """

    return service.confirm(user.organization_id, user.id, payload)
