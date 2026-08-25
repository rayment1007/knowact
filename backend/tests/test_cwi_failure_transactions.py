"""Failure states must survive the real request transaction boundary."""

from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app import database
from app.config import Settings, get_settings
from app.core import models as core_models
from app.main import create_app
from app.modules.cwi.dependencies import embedding_provider, storage_backend
from app.modules.cwi.models import DocumentAsset, DocumentProcessingStatus
from app.modules.cwi.services.embedding import FakeEmbeddingProvider
from app.modules.cwi.services.storage import LocalFilesystemStorage
from app.security import hash_password


def test_document_failed_state_commits_after_422_response(
    engine: Engine,
    monkeypatch,
    tmp_path,
) -> None:
    connection = engine.connect()
    outer_transaction = connection.begin()
    session_factory = sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
        future=True,
    )
    monkeypatch.setattr(database, "SessionLocal", session_factory)

    seed = session_factory()
    try:
        organization = core_models.Organization(name="Transaction Test")
        seed.add(organization)
        seed.flush()
        user = core_models.User(
            organization_id=organization.id,
            email="transaction@example.com",
            full_name="Transaction User",
            password_hash=hash_password("password"),
            role="ADMIN",
        )
        seed.add(user)
        seed.commit()
    finally:
        seed.close()

    settings = Settings(ai_provider="mock", embedding_provider="mock")
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[storage_backend] = lambda: LocalFilesystemStorage(
        tmp_path / "storage"
    )
    app.dependency_overrides[embedding_provider] = lambda: FakeEmbeddingProvider(
        dimension=settings.embedding_dimension
    )

    with TestClient(app) as client:
        assert client.post(
            "/api/auth/login",
            json={"email": "transaction@example.com", "password": "password"},
        ).status_code == 200
        uploaded = client.post(
            "/api/documents",
            files={
                "file": (
                    "broken.pdf",
                    b"%PDF-1.7\nnot actually a PDF",
                    "application/pdf",
                )
            },
        )
        assert uploaded.status_code == 201
        document_id = uploaded.json()["id"]
        failed = client.post(f"/api/documents/{document_id}/process")
        assert failed.status_code == 422

    try:
        verify = session_factory()
        try:
            asset = verify.get(DocumentAsset, UUID(document_id))
            assert asset is not None
            assert asset.processing_status == DocumentProcessingStatus.FAILED
            assert asset.failure_reason
        finally:
            verify.close()
    finally:
        outer_transaction.rollback()
        connection.close()
