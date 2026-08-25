"""Document upload / processing / retrieval routes (M6.4, Requirement 29).

Exposes the document lifecycle over HTTP. Every route is protected by
:func:`app.dependencies.get_current_user` (missing/invalid session → ``401``)
and scoped to the caller's organization, so a
:class:`~app.modules.cwi.models.DocumentAsset` belonging to another tenant is
indistinguishable from a missing one and yields ``404`` (Requirement 29.5 /
Property 13). The document binary itself is never returned in any response.

Routes:
* ``POST   /api/documents`` — multipart upload (file + optional sensitivity).
* ``POST   /api/documents/{id}/process`` — parse → chunk → embed → INDEXED.
* ``GET    /api/documents`` — list the organization's documents.
* ``GET    /api/documents/{id}`` — a single document's metadata/status.
* ``DELETE /api/documents/{id}`` — delete binary + chunks + embeddings.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.models import Sensitivity, User
from app.database import get_db
from app.dependencies import get_current_user
from app.modules.cwi.dependencies import embedding_provider, storage_backend
from app.modules.cwi.schemas import DocumentAssetView
from app.modules.cwi.services.document_service import (
    DocumentProcessingError,
    DocumentService,
)
from app.modules.cwi.services.embedding import EmbeddingProvider
from app.modules.cwi.services.storage import StorageBackend
from app.modules.cwi.services.upload_validation import (
    MAX_DOCUMENT_UPLOAD_BYTES,
    UploadValidationError,
    validate_document_upload,
)

router = APIRouter(tags=["documents"])


def _service(
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(storage_backend),
    embeddings: EmbeddingProvider = Depends(embedding_provider),
) -> DocumentService:
    """Build a :class:`DocumentService` bound to the request transaction."""

    return DocumentService(db, storage, embeddings)


@router.post(
    "/documents",
    response_model=DocumentAssetView,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    file: UploadFile = File(...),
    sensitivity: Sensitivity = Form(Sensitivity.INTERNAL),
    service: DocumentService = Depends(_service),
    user: User = Depends(get_current_user),
) -> DocumentAssetView:
    """Upload a document binary and record its asset (Requirements 29.1, 29.2).

    The binary is streamed into the ``StorageBackend`` (never Postgres); the
    persisted asset carries only metadata, a checksum, and status ``UPLOADED``.
    """

    # UploadFile is spooled by Starlette; read at most one byte beyond the cap
    # so an arbitrarily large request is never copied wholesale into memory.
    data = await file.read(MAX_DOCUMENT_UPLOAD_BYTES + 1)
    try:
        validated = validate_document_upload(
            filename=file.filename or "document",
            content_type=file.content_type or "",
            data=data,
        )
    except UploadValidationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc
    asset = service.upload(
        user.organization_id,
        user.id,
        filename=validated.filename,
        mime_type=validated.mime_type,
        data=validated.data,
        sensitivity=sensitivity,
    )
    return DocumentAssetView.model_validate(asset)


@router.post(
    "/documents/{document_id}/process", response_model=DocumentAssetView
)
def process_document(
    document_id: UUID,
    service: DocumentService = Depends(_service),
    user: User = Depends(get_current_user),
) -> DocumentAssetView | JSONResponse:
    """Parse → chunk → embed a document to ``INDEXED`` (Requirement 29.3).

    A cross-org/missing id yields ``404``; a document that cannot be parsed is
    marked ``FAILED`` and surfaces as ``422``.
    """

    try:
        asset = service.process(user.organization_id, user.id, document_id)
    except DocumentProcessingError as exc:
        # Return a normal response object instead of raising HTTPException.
        # The request-scoped transaction therefore commits FAILED + reason.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": str(exc)},
        )
    return DocumentAssetView.model_validate(asset)


@router.get("/documents", response_model=list[DocumentAssetView])
def list_documents(
    service: DocumentService = Depends(_service),
    user: User = Depends(get_current_user),
) -> list[DocumentAssetView]:
    """List the organization's documents, newest first (Requirement 29.2)."""

    assets = service.list_documents(user.organization_id, user.id)
    return [DocumentAssetView.model_validate(asset) for asset in assets]


@router.get("/documents/{document_id}", response_model=DocumentAssetView)
def get_document(
    document_id: UUID,
    service: DocumentService = Depends(_service),
    user: User = Depends(get_current_user),
) -> DocumentAssetView:
    """Return a single org-scoped document or ``404`` (Requirement 29.5)."""

    asset = service.get_document(user.organization_id, user.id, document_id)
    return DocumentAssetView.model_validate(asset)


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_document(
    document_id: UUID,
    service: DocumentService = Depends(_service),
    user: User = Depends(get_current_user),
) -> Response:
    """Delete a document: binary + chunks + embeddings (design *Deletion*).

    Org-scoped: a cross-org/missing id yields ``404``. Writes exactly one
    ``DELETE_DOCUMENT`` audit row in the same transaction.
    """

    service.delete(user.organization_id, user.id, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
