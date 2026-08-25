"""Route tests for the CWI Document endpoints (M6.4, Requirement 29).

Exercises the real FastAPI app against the ephemeral SQLite database with a FAKE
deterministic embedding provider and a temp-dir-backed StorageBackend injected,
so no embeddings API is ever called and no binary is written outside the test
sandbox. Covers upload → process (parse/chunk/embed once → INDEXED) → list →
get → delete, plus auth and cross-org 404 isolation, and asserts the binary is
never stored in the database (only an opaque storage key).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.core import models as core_models
from app.modules.cwi.dependencies import embedding_provider, storage_backend
from app.modules.cwi.models import DocumentAsset, DocumentChunk
from app.modules.cwi.services.embedding import FakeEmbeddingProvider
from app.modules.cwi.services.storage import LocalFilesystemStorage
from app.modules.cwi.services.upload_validation import MAX_DOCUMENT_UPLOAD_BYTES

_DIM = 64


@pytest.fixture()
def fake_storage(tmp_path) -> LocalFilesystemStorage:
    return LocalFilesystemStorage(tmp_path / "storage")


@pytest.fixture()
def cwi_client(
    client: TestClient, fake_storage: LocalFilesystemStorage
) -> TestClient:
    test_settings = Settings(
        ai_provider="mock",
        embedding_provider="mock",
        embedding_dimension=_DIM,
    )
    client.app.dependency_overrides[get_settings] = lambda: test_settings
    client.app.dependency_overrides[storage_backend] = lambda: fake_storage
    client.app.dependency_overrides[embedding_provider] = (
        lambda: FakeEmbeddingProvider(dimension=_DIM)
    )
    return client


def _login(client: TestClient, seeded_user: dict[str, Any]) -> None:
    resp = client.post(
        "/api/auth/login",
        json={"email": seeded_user["email"], "password": seeded_user["password"]},
    )
    assert resp.status_code == 200


def _upload(
    client: TestClient,
    *,
    filename: str = "notes.txt",
    body: bytes = b"First paragraph about the acme client.\n\nSecond paragraph.",
    content_type: str = "text/plain",
    sensitivity: str = "INTERNAL",
):
    return client.post(
        "/api/documents",
        files={"file": (filename, body, content_type)},
        data={"sensitivity": sensitivity},
    )


def test_upload_stores_binary_outside_db(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
    fake_storage: LocalFilesystemStorage,
) -> None:
    _login(cwi_client, seeded_user)
    resp = _upload(cwi_client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["processing_status"] == "UPLOADED"
    assert body["filename"] == "notes.txt"
    assert body["checksum"]
    # The storage key is never serialized into the response.
    assert "storage_key" not in body

    # The binary lives behind the StorageBackend, not in Postgres/SQLite.
    asset = db_session.query(DocumentAsset).one()
    assert asset.storage_key
    stored = fake_storage.get(asset.organization_id, asset.storage_key)
    assert stored == b"First paragraph about the acme client.\n\nSecond paragraph."

    # One UPLOAD_DOCUMENT audit row.
    audits = (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == "UPLOAD_DOCUMENT")
        .all()
    )
    assert len(audits) == 1


def test_process_indexes_chunks_with_embeddings(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    doc_id = _upload(cwi_client).json()["id"]

    resp = cwi_client.post(f"/api/documents/{doc_id}/process")
    assert resp.status_code == 200
    assert resp.json()["processing_status"] == "INDEXED"

    chunks = (
        db_session.query(DocumentChunk)
        .filter(DocumentChunk.document_asset_id == UUID(doc_id))
        .all()
    )
    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk.embedding is not None
        assert len(chunk.embedding) == _DIM

    # One PROCESS_DOCUMENT audit row.
    audits = (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == "PROCESS_DOCUMENT")
        .all()
    )
    assert len(audits) == 1


def test_process_is_idempotent_no_duplicate_chunks(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    doc_id = _upload(cwi_client).json()["id"]

    cwi_client.post(f"/api/documents/{doc_id}/process")
    first = (
        db_session.query(DocumentChunk)
        .filter(DocumentChunk.document_asset_id == UUID(doc_id))
        .count()
    )
    # Re-processing an INDEXED document does not duplicate chunks.
    cwi_client.post(f"/api/documents/{doc_id}/process")
    second = (
        db_session.query(DocumentChunk)
        .filter(DocumentChunk.document_asset_id == UUID(doc_id))
        .count()
    )
    assert first == second


def test_list_and_get_document(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    doc_id = _upload(cwi_client).json()["id"]

    listed = cwi_client.get("/api/documents")
    assert listed.status_code == 200
    assert any(item["id"] == doc_id for item in listed.json())

    detail = cwi_client.get(f"/api/documents/{doc_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == doc_id


def test_delete_cascades_chunks_and_binary(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
    fake_storage: LocalFilesystemStorage,
) -> None:
    _login(cwi_client, seeded_user)
    doc_id = _upload(cwi_client).json()["id"]
    cwi_client.post(f"/api/documents/{doc_id}/process")

    asset = db_session.query(DocumentAsset).filter_by(id=UUID(doc_id)).one()
    storage_key = asset.storage_key
    org_id = asset.organization_id

    resp = cwi_client.delete(f"/api/documents/{doc_id}")
    assert resp.status_code == 204

    assert db_session.query(DocumentAsset).filter_by(id=UUID(doc_id)).count() == 0
    assert (
        db_session.query(DocumentChunk)
        .filter(DocumentChunk.document_asset_id == UUID(doc_id))
        .count()
        == 0
    )
    # The binary is gone from storage too.
    from app.modules.cwi.services.storage import StorageError

    with pytest.raises(StorageError):
        fake_storage.get(org_id, storage_key)

    audits = (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == "DELETE_DOCUMENT")
        .all()
    )
    assert len(audits) == 1


def test_documents_require_authentication(cwi_client: TestClient) -> None:
    assert cwi_client.get("/api/documents").status_code == 401


def test_cross_org_document_returns_404(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    doc_id = _upload(cwi_client).json()["id"]

    # A second org/user.
    from app.security import hash_password

    other_org = core_models.Organization(name="Other Org")
    db_session.add(other_org)
    db_session.flush()
    other_user = core_models.User(
        organization_id=other_org.id,
        email="other-doc@example.com",
        full_name="Other",
        password_hash=hash_password("pw2"),
        role="ADMIN",
    )
    db_session.add(other_user)
    db_session.flush()

    cwi_client.cookies.clear()
    assert cwi_client.post(
        "/api/auth/login",
        json={"email": "other-doc@example.com", "password": "pw2"},
    ).status_code == 200

    assert cwi_client.get(f"/api/documents/{doc_id}").status_code == 404
    assert cwi_client.post(f"/api/documents/{doc_id}/process").status_code == 404
    assert cwi_client.delete(f"/api/documents/{doc_id}").status_code == 404


@pytest.mark.parametrize(
    ("filename", "body", "content_type", "expected_status"),
    [
        ("empty.txt", b"", "text/plain", 400),
        ("payload.exe", b"hello", "application/octet-stream", 415),
        ("renamed.txt", b"\x89PNG\r\n\x1a\ncontent", "text/plain", 415),
        ("notes.txt", b"hello", "application/pdf", 415),
    ],
)
def test_upload_rejects_invalid_files_without_creating_asset(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
    filename: str,
    body: bytes,
    content_type: str,
    expected_status: int,
) -> None:
    _login(cwi_client, seeded_user)
    response = _upload(
        cwi_client,
        filename=filename,
        body=body,
        content_type=content_type,
    )
    assert response.status_code == expected_status
    assert db_session.query(DocumentAsset).count() == 0


def test_upload_rejects_oversized_file_without_creating_asset(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
) -> None:
    _login(cwi_client, seeded_user)
    response = _upload(
        cwi_client,
        filename="large.txt",
        body=b"a" * (MAX_DOCUMENT_UPLOAD_BYTES + 1),
        content_type="text/plain",
    )
    assert response.status_code == 413
    assert db_session.query(DocumentAsset).count() == 0


def test_failed_processing_is_visible_with_safe_reason(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
) -> None:
    _login(cwi_client, seeded_user)
    uploaded = _upload(
        cwi_client,
        filename="invalid.pdf",
        body=b"%PDF-1.7\nnot a real PDF",
        content_type="application/pdf",
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["id"]

    processed = cwi_client.post(f"/api/documents/{document_id}/process")
    assert processed.status_code == 422
    assert processed.json()["detail"]

    detail = cwi_client.get(f"/api/documents/{document_id}")
    assert detail.status_code == 200
    assert detail.json()["processing_status"] == "FAILED"
    assert detail.json()["failure_reason"]


def test_same_org_user_cannot_access_another_users_document(
    cwi_client: TestClient,
    seeded_user: dict[str, Any],
    db_session: Any,
) -> None:
    _login(cwi_client, seeded_user)
    document_id = _upload(cwi_client).json()["id"]

    from app.security import hash_password

    other_user = core_models.User(
        organization_id=seeded_user["organization"].id,
        email="other-same-org@example.com",
        full_name="Other Same Org User",
        password_hash=hash_password("pw2"),
        role="MEMBER",
    )
    db_session.add(other_user)
    db_session.flush()

    cwi_client.cookies.clear()
    assert cwi_client.post(
        "/api/auth/login",
        json={"email": other_user.email, "password": "pw2"},
    ).status_code == 200

    listed = cwi_client.get("/api/documents")
    assert listed.status_code == 200
    assert listed.json() == []
    assert cwi_client.get(f"/api/documents/{document_id}").status_code == 404
    assert cwi_client.post(f"/api/documents/{document_id}/process").status_code == 404
    assert cwi_client.delete(f"/api/documents/{document_id}").status_code == 404
