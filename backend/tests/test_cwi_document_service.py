"""Unit tests for DocumentService processing + RetrievalService ranking (M6.4).

These exercise the core logic directly (no HTTP): a document is parsed once,
chunked once, and embedded **once** (a single batched embedding call), and
retrieval returns a bounded, similarity-ranked set carrying the citation fields
required by Requirement 29.7. Storage is an in-memory fake and embeddings are
the deterministic :class:`FakeEmbeddingProvider`, so nothing touches the network
or filesystem.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core import models as core_models
from app.core.models import Sensitivity
from app.modules.cwi.models import DocumentChunk, DocumentProcessingStatus
from app.modules.cwi.services.document_parsing import (
    DocumentParseError,
    chunk_text,
    parse_document,
)
from app.modules.cwi.services.document_service import (
    DocumentProcessingError,
    DocumentService,
)
from app.modules.cwi.services.embedding import FakeEmbeddingProvider
from app.modules.cwi.services.retrieval_service import (
    RetrievalFilters,
    RetrievalService,
)

_DIM = 32


class _InMemoryStorage:
    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}

    def put(self, org_id, key: str, data: bytes, content_type: str) -> str:
        self._objects[(str(org_id), key)] = data
        return key

    def get(self, org_id, key: str) -> bytes:
        return self._objects[(str(org_id), key)]

    def delete(self, org_id, key: str) -> None:
        self._objects.pop((str(org_id), key), None)


class _ShortEmbeddingProvider:
    def embed(self, texts: list[str]) -> list[list[float]]:
        del texts
        return []


@pytest.fixture()
def org_user(db_session: Session) -> dict[str, Any]:
    org = core_models.Organization(name="Doc Org")
    db_session.add(org)
    db_session.flush()
    user = core_models.User(
        organization_id=org.id,
        email="doc-user@example.com",
        full_name="Doc User",
        password_hash="x",
        role="ADMIN",
    )
    db_session.add(user)
    db_session.flush()
    return {"org": org, "user": user}


def test_chunker_produces_bounded_chunks() -> None:
    text = "\n\n".join(f"Paragraph number {i} with some words." for i in range(50))
    chunks = chunk_text(text, chunk_size=200, overlap=20)
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 200 for chunk in chunks)
    # Chunk indexes are contiguous starting at 0.
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_process_embeds_once(
    db_session: Session, org_user: dict[str, Any]
) -> None:
    storage = _InMemoryStorage()
    fake = FakeEmbeddingProvider(dimension=_DIM)
    service = DocumentService(db_session, storage, fake)

    body = ("First paragraph.\n\n" * 3 + "Second topic.\n\n" * 3).encode("utf-8")
    asset = service.upload(
        org_user["org"].id,
        org_user["user"].id,
        filename="notes.txt",
        mime_type="text/plain",
        data=body,
        sensitivity=Sensitivity.INTERNAL,
    )
    service.process(org_user["org"].id, org_user["user"].id, asset.id)

    # Parsed/chunked/embedded exactly once: a single batched embedding call.
    assert fake.embed_calls == 1
    assert asset.processing_status == DocumentProcessingStatus.INDEXED
    chunks = (
        db_session.query(DocumentChunk)
        .filter(DocumentChunk.document_asset_id == asset.id)
        .all()
    )
    assert len(chunks) >= 1
    assert all(len(c.embedding) == _DIM for c in chunks)


def test_text_parser_rejects_legacy_and_binary_bytes() -> None:
    with pytest.raises(DocumentParseError):
        parse_document(b"\x80\x81\x82", "text/plain", "bad.txt")
    with pytest.raises(DocumentParseError):
        parse_document(b"hello\x00world", "text/plain", "bad.txt")
    with pytest.raises(DocumentParseError):
        parse_document(b"hello", "application/octet-stream", "bad.txt")


def test_process_rejects_empty_text_and_incomplete_embeddings(
    db_session: Session, org_user: dict[str, Any]
) -> None:
    storage = _InMemoryStorage()
    empty_service = DocumentService(
        db_session, storage, FakeEmbeddingProvider(dimension=_DIM)
    )
    empty = empty_service.upload(
        org_user["org"].id,
        org_user["user"].id,
        filename="empty.txt",
        mime_type="text/plain",
        data=b"   \n\n",
    )
    with pytest.raises(DocumentProcessingError):
        empty_service.process(
            org_user["org"].id, org_user["user"].id, empty.id
        )
    assert empty.processing_status == DocumentProcessingStatus.FAILED
    assert empty.failure_reason

    short_service = DocumentService(
        db_session, storage, _ShortEmbeddingProvider()
    )
    incomplete = short_service.upload(
        org_user["org"].id,
        org_user["user"].id,
        filename="notes.txt",
        mime_type="text/plain",
        data=b"Useful text",
    )
    with pytest.raises(DocumentProcessingError):
        short_service.process(
            org_user["org"].id, org_user["user"].id, incomplete.id
        )
    assert incomplete.processing_status == DocumentProcessingStatus.FAILED
    assert (
        db_session.query(DocumentChunk)
        .filter(DocumentChunk.document_asset_id == incomplete.id)
        .count()
        == 0
    )


def test_upload_compensates_storage_when_database_work_fails(
    db_session: Session,
    org_user: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _InMemoryStorage()
    service = DocumentService(
        db_session, storage, FakeEmbeddingProvider(dimension=_DIM)
    )

    def _fail_audit(**_kwargs: Any) -> None:
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(service.audit, "record", _fail_audit)
    with pytest.raises(RuntimeError, match="simulated audit failure"):
        service.upload(
            org_user["org"].id,
            org_user["user"].id,
            filename="notes.txt",
            mime_type="text/plain",
            data=b"Useful text",
        )
    assert storage._objects == {}


def test_retrieve_ranks_relevant_chunk_and_carries_citation_fields(
    db_session: Session, org_user: dict[str, Any]
) -> None:
    storage = _InMemoryStorage()
    fake = FakeEmbeddingProvider(dimension=_DIM)
    doc_service = DocumentService(db_session, storage, fake)

    # Two documents with distinct content.
    target = doc_service.upload(
        org_user["org"].id,
        org_user["user"].id,
        filename="acme-contract.txt",
        mime_type="text/plain",
        data=b"the acme corporation quarterly revenue report",
        sensitivity=Sensitivity.INTERNAL,
    )
    doc_service.process(org_user["org"].id, org_user["user"].id, target.id)
    other = doc_service.upload(
        org_user["org"].id,
        org_user["user"].id,
        filename="lunch-menu.txt",
        mime_type="text/plain",
        data=b"tuesday cafeteria lunch menu options",
        sensitivity=Sensitivity.INTERNAL,
    )
    doc_service.process(org_user["org"].id, org_user["user"].id, other.id)

    retrieval = RetrievalService(db_session, fake)
    results = retrieval.retrieve(
        org_user["org"].id,
        org_user["user"].id,
        query="the acme corporation quarterly revenue report",
        filters=RetrievalFilters(max_sensitivity=Sensitivity.CONFIDENTIAL),
        k=1,
    )

    # Bounded top-k (never resends all history).
    assert len(results) == 1
    top = results[0]
    # Deterministic embeddings: the exact-match chunk ranks first.
    assert top.source_id == target.id
    # Citation fields present (Requirement 29.7).
    assert top.source_type.value == "DOCUMENT"
    assert top.title == "acme-contract.txt"
    assert top.evidence_excerpt
    assert top.timestamp is not None
    assert top.deep_link.startswith(f"/documents/{target.id}")
