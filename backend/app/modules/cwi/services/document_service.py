"""Document upload / processing / deletion (M6.4, Requirement 29).

:class:`DocumentService` owns the document lifecycle:

* :meth:`upload` streams the binary into the injected
  :class:`~app.modules.cwi.services.storage.StorageBackend` (never Postgres),
  records a :class:`~app.modules.cwi.models.DocumentAsset` with a ``checksum``
  and ``processing_status = UPLOADED``, and writes exactly one
  ``UPLOAD_DOCUMENT`` audit row in the caller's transaction (Requirement 29.1,
  29.2).
* :meth:`process` parses the stored binary **once**, chunks it **once**, embeds
  it **once** through the injected
  :class:`~app.modules.cwi.services.embedding.EmbeddingProvider`, persists the
  :class:`~app.modules.cwi.models.DocumentChunk` rows with their embeddings, and
  advances the asset to ``INDEXED`` (Requirement 29.3). One
  ``PROCESS_DOCUMENT`` audit row is written on success.
* :meth:`delete` removes the binary from storage, cascades deletion of the
  asset's chunks + embeddings, and writes exactly one ``DELETE_DOCUMENT`` audit
  row in the same transaction (design *Deletion cascade*).

Every lookup is org-scoped: a cross-tenant/missing ``document_asset_id`` is
indistinguishable from a genuinely missing one and yields ``404`` (Requirement
29.5 / Property 13). The service ``add``/``flush``es within the caller's
transaction and never commits, so each mutation and its audit row commit
atomically.
"""

from __future__ import annotations

import hashlib
import uuid
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.services.audit_service import AuditService
from app.core.models import Sensitivity
from app.dependencies import not_found, scope_select
from app.modules.cwi.models import (
    DocumentAsset,
    DocumentChunk,
    DocumentProcessingStatus,
)
from app.modules.cwi.services.document_parsing import (
    chunk_text,
    parse_document,
)
from app.modules.cwi.services.embedding import EmbeddingProvider
from app.modules.cwi.services.storage import StorageBackend

# Audit action types recorded per document mutation (Requirement 29 / P20).
UPLOAD_DOCUMENT = "UPLOAD_DOCUMENT"
PROCESS_DOCUMENT = "PROCESS_DOCUMENT"
DELETE_DOCUMENT = "DELETE_DOCUMENT"

_TARGET_TYPE = "DocumentAsset"


class DocumentProcessingError(RuntimeError):
    """Raised when a document cannot be processed into indexed chunks."""


class DocumentService:
    """Upload, process, and delete documents for one organization (Req 29)."""

    def __init__(
        self,
        db: Session,
        storage: StorageBackend,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        """Bind the service to a session and injected storage + embeddings.

        Args:
            db: Request-scoped session (transaction owned by the caller).
            storage: The binary :class:`StorageBackend` (a temp dir in tests).
            embedding_provider: The :class:`EmbeddingProvider` (a deterministic
                fake in tests, so no embeddings API is called).
        """

        self.db = db
        self.storage = storage
        self.embedding_provider = embedding_provider
        self.audit = AuditService(db)

    # -- Scoped lookups -----------------------------------------------------

    def _get_asset(self, org_id: UUID, document_asset_id: UUID) -> DocumentAsset:
        stmt = scope_select(select(DocumentAsset), DocumentAsset, org_id).where(
            DocumentAsset.id == document_asset_id
        )
        asset = self.db.execute(stmt).scalar_one_or_none()
        if asset is None:
            raise not_found("Document not found.")
        return asset

    # -- Public API ---------------------------------------------------------

    def upload(
        self,
        org_id: UUID,
        user_id: UUID,
        *,
        filename: str,
        mime_type: str,
        data: bytes,
        sensitivity: Sensitivity = Sensitivity.INTERNAL,
    ) -> DocumentAsset:
        """Store a document binary and record its asset (Requirements 29.1, 29.2).

        The bytes are written to the :class:`StorageBackend` under an opaque,
        randomly-generated ``storage_key`` (never into Postgres); the row keeps
        only the key plus a ``sha256`` ``checksum`` for dedup/integrity and
        ``processing_status = UPLOADED``. Writes exactly one ``UPLOAD_DOCUMENT``
        audit row in the caller's transaction.
        """

        checksum = hashlib.sha256(data).hexdigest()
        storage_key = uuid.uuid4().hex
        self.storage.put(org_id, storage_key, data, mime_type)

        asset = DocumentAsset(
            organization_id=org_id,
            uploaded_by=user_id,
            filename=filename,
            mime_type=mime_type,
            storage_key=storage_key,
            checksum=checksum,
            processing_status=DocumentProcessingStatus.UPLOADED,
            sensitivity=sensitivity,
            source_deleted=False,
        )
        self.db.add(asset)
        self.db.flush()

        self.audit.record(
            org_id=org_id,
            actor_id=user_id,
            action_type=UPLOAD_DOCUMENT,
            target_type=_TARGET_TYPE,
            target_id=asset.id,
            detail={
                "filename": filename,
                "mime_type": mime_type,
                "checksum": checksum,
            },
        )
        self.db.flush()
        return asset

    def process(
        self, org_id: UUID, user_id: UUID, document_asset_id: UUID
    ) -> DocumentAsset:
        """Parse → chunk → embed a document once, reaching INDEXED (Req 29.3).

        Idempotent-ish: re-processing an already-``INDEXED`` asset returns it
        unchanged (its chunks are not duplicated). On a parse/processing failure
        the asset is marked ``FAILED`` (retriable) and a
        :class:`DocumentProcessingError` is raised so the caller's transaction
        can decide how to surface it. On success writes exactly one
        ``PROCESS_DOCUMENT`` audit row.
        """

        asset = self._get_asset(org_id, document_asset_id)
        if asset.processing_status == DocumentProcessingStatus.INDEXED:
            return asset

        # Parse once.
        asset.processing_status = DocumentProcessingStatus.PARSING
        self.db.add(asset)
        self.db.flush()
        try:
            data = self.storage.get(org_id, asset.storage_key)
            text = parse_document(data, asset.mime_type, asset.filename)
        except Exception as exc:  # noqa: BLE001 - any parse/read error → FAILED
            asset.processing_status = DocumentProcessingStatus.FAILED
            self.db.add(asset)
            self.db.flush()
            raise DocumentProcessingError(
                "Could not parse the document for processing."
            ) from exc

        # Chunk once.
        asset.processing_status = DocumentProcessingStatus.CHUNKING
        self.db.add(asset)
        self.db.flush()
        parsed_chunks = chunk_text(text)

        # Embed once (a single batched call through the injected provider).
        asset.processing_status = DocumentProcessingStatus.EMBEDDING
        self.db.add(asset)
        self.db.flush()
        vectors: list[list[float]] = []
        if parsed_chunks:
            vectors = self.embedding_provider.embed(
                [chunk.text for chunk in parsed_chunks]
            )

        for parsed, vector in zip(parsed_chunks, vectors):
            self.db.add(
                DocumentChunk(
                    organization_id=org_id,
                    document_asset_id=asset.id,
                    chunk_index=parsed.chunk_index,
                    text=parsed.text,
                    page_number=parsed.page_number,
                    metadata_json={
                        "filename": asset.filename,
                        "chunk_index": parsed.chunk_index,
                    },
                    embedding=vector,
                )
            )

        asset.processing_status = DocumentProcessingStatus.INDEXED
        self.db.add(asset)
        self.db.flush()

        self.audit.record(
            org_id=org_id,
            actor_id=user_id,
            action_type=PROCESS_DOCUMENT,
            target_type=_TARGET_TYPE,
            target_id=asset.id,
            detail={"chunks": len(parsed_chunks)},
        )
        self.db.flush()
        return asset

    def delete(
        self, org_id: UUID, user_id: UUID, document_asset_id: UUID
    ) -> None:
        """Delete a document: binary + chunks + embeddings (design *Deletion*).

        Removes the binary from the :class:`StorageBackend`, deletes the asset's
        :class:`DocumentChunk` rows (and their embeddings) and the asset itself,
        and writes exactly one ``DELETE_DOCUMENT`` audit row in the same
        transaction. Org-scoped: a cross-org id yields ``404``.
        """

        asset = self._get_asset(org_id, document_asset_id)

        # Cascade chunk deletion explicitly with a bulk DELETE (portable across
        # SQLite/PG, and avoids per-row identity-map races with the FK cascade).
        self.db.execute(
            sa_delete(DocumentChunk)
            .where(DocumentChunk.organization_id == org_id)
            .where(DocumentChunk.document_asset_id == asset.id)
        )

        # Remove the binary from storage (idempotent).
        self.storage.delete(org_id, asset.storage_key)

        asset_id = asset.id
        filename = asset.filename
        self.db.delete(asset)
        self.db.flush()

        self.audit.record(
            org_id=org_id,
            actor_id=user_id,
            action_type=DELETE_DOCUMENT,
            target_type=_TARGET_TYPE,
            target_id=asset_id,
            detail={"filename": filename},
        )
        self.db.flush()

    # -- Reads --------------------------------------------------------------

    def list_documents(self, org_id: UUID) -> list[DocumentAsset]:
        """Return the organization's documents, newest first."""

        stmt = scope_select(select(DocumentAsset), DocumentAsset, org_id)
        stmt = stmt.order_by(
            DocumentAsset.created_at.desc(), DocumentAsset.id.desc()
        )
        return list(self.db.execute(stmt).scalars().all())

    def get_document(self, org_id: UUID, document_asset_id: UUID) -> DocumentAsset:
        """Return a single org-scoped document or ``404`` (Requirement 29.5)."""

        return self._get_asset(org_id, document_asset_id)


__all__ = [
    "DocumentService",
    "DocumentProcessingError",
    "UPLOAD_DOCUMENT",
    "PROCESS_DOCUMENT",
    "DELETE_DOCUMENT",
]
