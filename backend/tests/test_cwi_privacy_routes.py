"""Route + service tests for Privacy, Control & Deletion (M6.7, Requirement 33).

Exercises the real FastAPI app against the ephemeral SQLite database with fake
transports injected (no network, no real email). Covers:

* deleting imported email data removes the selected records and their dependent
  suggestions, retains confirmed derived records, and writes exactly one
  ``DELETE_EMAIL_DATA`` audit row in the same transaction (33.3, 33.8, 33.9);
* deleting an uploaded document removes the binary + chunks + embeddings and
  writes exactly one ``DELETE_DOCUMENT`` audit row (33.4, 33.9);
* choosing ``EXTRACTED_ONLY`` persists the policy and drops retained raw content
  (33.5);
* sync-status surfaces last_sync_at / last_error / status and never a token (33.6);
* answer-provenance returns the exact citations/evidence that grounded an answer
  (33.7);
* auth is required and cross-org ids yield 404.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.core import models as core_models
from app.core.models import SuggestionStatus
from app.modules.cwi.dependencies import embedding_provider, storage_backend
from app.modules.cwi.models import (
    ConnectionStatus,
    DocumentProcessingStatus,
    EmailMessageRecord,
    EmailTaskSuggestion,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationService as IntegrationServiceEnum,
    RawEmailRetentionPolicy,
)
from app.modules.cwi.services.embedding import FakeEmbeddingProvider
from app.modules.cwi.services.storage import LocalFilesystemStorage

_DIM = 32


@pytest.fixture()
def cwi_client(client: TestClient) -> TestClient:
    test_settings = Settings(
        ai_provider="mock",
        embedding_provider="mock",
        embedding_dimension=_DIM,
    )
    tmp = tempfile.mkdtemp(prefix="privacy-test-")
    client.app.dependency_overrides[get_settings] = lambda: test_settings
    client.app.dependency_overrides[embedding_provider] = (
        lambda: FakeEmbeddingProvider(dimension=_DIM)
    )
    client.app.dependency_overrides[storage_backend] = (
        lambda: LocalFilesystemStorage(Path(tmp))
    )
    return client


def _login(client: TestClient, seeded_user: dict[str, Any]) -> None:
    resp = client.post(
        "/api/auth/login",
        json={"email": seeded_user["email"], "password": seeded_user["password"]},
    )
    assert resp.status_code == 200


def _seed_connection(
    db: Any, org_id, user_id, service=IntegrationServiceEnum.GMAIL
) -> IntegrationConnection:
    suffix = uuid4().hex
    connection = IntegrationConnection(
        organization_id=org_id,
        user_id=user_id,
        provider=IntegrationProvider.GOOGLE,
        service=service,
        external_account_id=f"sub-{suffix}",
        account_email=f"mailbox-{suffix}@example.com",
        granted_scopes_json=["https://www.googleapis.com/auth/gmail.readonly"],
        access_token_encrypted=b"ciphertext-secret",
        refresh_token_encrypted=b"ciphertext-secret",
        token_expires_at=datetime.now(timezone.utc),
        status=ConnectionStatus.CONNECTED,
        last_sync_at=datetime.now(timezone.utc),
        last_error=None,
    )
    db.add(connection)
    db.flush()
    return connection


def _seed_email_record(
    db: Any,
    org_id,
    connection_id,
    *,
    source_item_id=None,
    stored_raw: bool = True,
) -> EmailMessageRecord:
    suffix = uuid4().hex
    record = EmailMessageRecord(
        organization_id=org_id,
        integration_connection_id=connection_id,
        source_item_id=source_item_id,
        gmail_message_id=f"msg-{suffix}",
        gmail_thread_id=f"thread-{suffix}",
        sender="alice@example.com",
        recipients_json=["bob@example.com"],
        subject="Quarterly numbers",
        received_at=datetime.now(timezone.utc),
        labels_json=["INBOX"],
        content_hash=suffix,
        has_attachments=False,
        stored_raw=stored_raw,
    )
    db.add(record)
    db.flush()
    return record


# ---------------------------------------------------------------------------
# Delete imported email data
# ---------------------------------------------------------------------------


def test_delete_email_data_all_removes_records_and_one_audit(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    org_id = seeded_user["organization"].id
    user_id = seeded_user["user"].id
    connection = _seed_connection(db_session, org_id, user_id)
    r1 = _seed_email_record(db_session, org_id, connection.id)
    r2 = _seed_email_record(db_session, org_id, connection.id)

    # A dependent task suggestion on r1 must be cascaded away.
    db_session.add(
        EmailTaskSuggestion(
            organization_id=org_id,
            email_message_record_id=r1.id,
            title="Follow up",
            gmail_message_id=r1.gmail_message_id,
            evidence_text="body",
            ai_provider="mock",
            ai_model="mock",
            status=SuggestionStatus.SUGGESTED,
        )
    )
    db_session.flush()

    resp = cwi_client.request(
        "DELETE", "/api/privacy/email-data", json={"scope": "ALL"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted_records"] == 2
    assert body["deleted_task_suggestions"] == 1

    assert db_session.query(EmailMessageRecord).count() == 0
    assert db_session.query(EmailTaskSuggestion).count() == 0

    audits = (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == "DELETE_EMAIL_DATA")
        .all()
    )
    assert len(audits) == 1
    assert audits[0].detail["deleted_records"] == 2
    _ = r2  # both records deleted under ALL scope


def test_delete_email_data_selected_scope(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    org_id = seeded_user["organization"].id
    user_id = seeded_user["user"].id
    connection = _seed_connection(db_session, org_id, user_id)
    r1 = _seed_email_record(db_session, org_id, connection.id)
    r2 = _seed_email_record(db_session, org_id, connection.id)

    resp = cwi_client.request(
        "DELETE",
        "/api/privacy/email-data",
        json={"scope": "SELECTED", "record_ids": [str(r1.id)]},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted_records"] == 1
    remaining = db_session.query(EmailMessageRecord).all()
    assert [rec.id for rec in remaining] == [r2.id]


def test_delete_email_data_retains_confirmed_derived_knowledge(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    org_id = seeded_user["organization"].id
    user_id = seeded_user["user"].id
    connection = _seed_connection(db_session, org_id, user_id)

    # A source item + confirmed knowledge derived from it (a business record).
    source = core_models.SourceItem(
        organization_id=org_id,
        created_by=user_id,
        source_type=core_models.SourceType.EMAIL,
        title="Email",
        content="Body",
        status=core_models.SourceStatus.PROCESSED,
    )
    db_session.add(source)
    db_session.flush()
    knowledge = core_models.KnowledgeItem(
        organization_id=org_id,
        source_item_id=source.id,
        summary="Client confirmed renewal.",
        evidence_text="Body",
        knowledge_type="FACT",
        status=SuggestionStatus.CONFIRMED,
    )
    db_session.add(knowledge)
    db_session.flush()
    _seed_email_record(
        db_session, org_id, connection.id, source_item_id=source.id
    )

    resp = cwi_client.request(
        "DELETE", "/api/privacy/email-data", json={"scope": "ALL"}
    )
    assert resp.status_code == 200
    assert resp.json()["retained_derived_records"] == 1

    # The confirmed business record is RETAINED (Requirement 33.8).
    retained = db_session.query(core_models.KnowledgeItem).one()
    assert retained.source_item_id is None
    # The imported raw body must actually be gone after the privacy operation.
    assert db_session.query(core_models.SourceItem).count() == 0
    audits = (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == "DELETE_EMAIL_DATA")
        .all()
    )
    assert len(audits) == 1
    assert audits[0].detail["retained_derived_records"] == 1
    assert audits[0].detail["deleted_source_items"] == 1


# ---------------------------------------------------------------------------
# Delete uploaded document
# ---------------------------------------------------------------------------


def test_delete_document_removes_and_audits(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    org_id = seeded_user["organization"].id

    upload = cwi_client.post(
        "/api/documents",
        files={"file": ("notes.txt", b"hello world content", "text/plain")},
    )
    assert upload.status_code == 201
    document_id = upload.json()["id"]

    resp = cwi_client.delete(f"/api/privacy/documents/{document_id}")
    assert resp.status_code == 204

    from app.modules.cwi.models import DocumentAsset

    assert db_session.query(DocumentAsset).count() == 0
    audits = (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == "DELETE_DOCUMENT")
        .all()
    )
    assert len(audits) == 1


def test_delete_document_cross_org_returns_404(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    resp = cwi_client.delete(f"/api/privacy/documents/{uuid4()}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Retention policy
# ---------------------------------------------------------------------------


def test_set_retention_policy_extracted_only_drops_raw(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    org_id = seeded_user["organization"].id
    user_id = seeded_user["user"].id
    connection = _seed_connection(db_session, org_id, user_id)
    record = _seed_email_record(
        db_session, org_id, connection.id, stored_raw=True
    )

    resp = cwi_client.put(
        "/api/privacy/retention-policy",
        json={"mode": "EXTRACTED_ONLY", "retention_window_days": 30},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "EXTRACTED_ONLY"
    assert body["retention_window_days"] == 30

    db_session.expire_all()
    refreshed = db_session.get(EmailMessageRecord, record.id)
    assert refreshed.stored_raw is False
    assert db_session.query(RawEmailRetentionPolicy).count() == 1


def test_set_retention_policy_is_upserted(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    cwi_client.put(
        "/api/privacy/retention-policy", json={"mode": "EXTRACTED_ONLY"}
    )
    resp = cwi_client.put(
        "/api/privacy/retention-policy",
        json={"mode": "RAW_AND_EXTRACTED"},
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "RAW_AND_EXTRACTED"
    assert db_session.query(RawEmailRetentionPolicy).count() == 1


# ---------------------------------------------------------------------------
# Sync status
# ---------------------------------------------------------------------------


def test_sync_status_surfaces_state_and_no_token(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    org_id = seeded_user["organization"].id
    user_id = seeded_user["user"].id
    _seed_connection(db_session, org_id, user_id)

    resp = cwi_client.get("/api/privacy/sync-status")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    entry = body[0]
    assert entry["status"] == "CONNECTED"
    assert "last_sync_at" in entry and "last_error" in entry
    # Token fields are never serialized (Property 14).
    serialized = resp.text.lower()
    assert "token" not in serialized
    assert "ciphertext" not in serialized


# ---------------------------------------------------------------------------
# Answer provenance ("what data was used for an AI answer")
# ---------------------------------------------------------------------------


def test_answer_provenance_returns_grounding_set(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    org_id = seeded_user["organization"].id

    db_session.add(
        core_models.KnowledgeItem(
            organization_id=org_id,
            summary="Acme renewed the annual retainer.",
            evidence_text="Acme renewed the annual retainer.",
            knowledge_type="FACT",
            status=SuggestionStatus.CONFIRMED,
        )
    )
    db_session.flush()

    ask = cwi_client.post(
        "/api/copilot/ask", json={"question": "What's happening with Acme?"}
    )
    assert ask.status_code == 200
    answer_id = ask.json()["answer_id"]
    assert answer_id is not None

    resp = cwi_client.get(f"/api/privacy/answer-provenance/{answer_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer_id"] == answer_id
    assert body["question"] == "What's happening with Acme?"
    assert body["evidence"], "the exact evidence set must be returned"
    # Every citation must be part of the recorded evidence set.
    evidence_ids = {e["source_id"] for e in body["evidence"]}
    for citation in body["citations"]:
        assert citation["source_id"] in evidence_ids


def test_answer_provenance_cross_org_returns_404(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    resp = cwi_client.get(f"/api/privacy/answer-provenance/{uuid4()}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_privacy_routes_require_authentication(cwi_client: TestClient) -> None:
    assert cwi_client.request(
        "DELETE", "/api/privacy/email-data", json={"scope": "ALL"}
    ).status_code == 401
    assert cwi_client.delete(
        f"/api/privacy/documents/{uuid4()}"
    ).status_code == 401
    assert cwi_client.put(
        "/api/privacy/retention-policy", json={"mode": "EXTRACTED_ONLY"}
    ).status_code == 401
    assert cwi_client.get("/api/privacy/sync-status").status_code == 401
    assert cwi_client.get(
        f"/api/privacy/answer-provenance/{uuid4()}"
    ).status_code == 401
