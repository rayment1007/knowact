"""Owner-scoped complete-source snapshots; no AI/provider calls."""
from datetime import datetime, timezone, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.models import SourceItem, ClassificationResult, User
from app.core.services.fact_sources import read_fact_source
from app.modules.cwi.models import DocumentChunk
from tests.test_cwi_privacy_routes import _seed_connection, _seed_email_record


def _note(db, user, text="UAT is on 17 September 2026."):
    source = SourceItem(organization_id=user.organization_id, created_by=user.id,
        source_type="MANUAL", title="UAT", content=text,
        received_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    db.add(source); db.flush(); return source


def test_gmail_alias_reimport_origin_and_complete_revision(db_session, seeded_user):
    user = seeded_user["user"]
    source = _note(db_session, user)
    connection = _seed_connection(db_session, user.organization_id, user.id)
    record = _seed_email_record(db_session, user.organization_id, connection.id, source_item_id=source.id,
        stored_raw=False)
    original = read_fact_source(db_session, user, "email", record.id)
    alias = read_fact_source(db_session, user, "source", source.id)
    assert alias == original and original.text == source.content
    # Sync timestamps don't change source revision; provider message identity
    # survives a new local ID and even a reconnection row.
    connection.last_sync_at = datetime.now(timezone.utc) + timedelta(days=1)
    another_connection = _seed_connection(db_session, user.organization_id, user.id)
    another_connection.account_email = connection.account_email
    second_source = _note(db_session, user)
    second = _seed_email_record(db_session, user.organization_id, another_connection.id,
        source_item_id=second_source.id)
    second.gmail_message_id = record.gmail_message_id
    second.received_at = record.received_at
    db_session.flush()
    reimport = read_fact_source(db_session, user, "email", second.id)
    assert reimport.source_id != original.source_id
    assert reimport.origin_key == original.origin_key and reimport.fingerprint == original.fingerprint
    second_source.content += " Additional late content."
    db_session.flush()
    assert read_fact_source(db_session, user, "email", second.id).fingerprint != original.fingerprint


def test_other_owner_cannot_read_and_has_distinct_origin(db_session, seeded_user):
    user = seeded_user["user"]
    source = _note(db_session, user)
    other = User(organization_id=user.organization_id, full_name="Other", email="source-peer@example.test",
        password_hash="unused", role="MEMBER")
    db_session.add(other); db_session.flush()
    with pytest.raises(HTTPException) as exc:
        read_fact_source(db_session, other, "source", source.id)
    assert exc.value.status_code == 404
    assert read_fact_source(db_session, user, "source", source.id).source_time is None
    assert read_fact_source(db_session, user, "source", source.id).origin_key != read_fact_source(
        db_session, other, "source", _note(db_session, other).id).origin_key


def test_sensitive_and_dismissed_sources_require_explicit_resolution(db_session, seeded_user):
    user = seeded_user["user"]
    source = _note(db_session, user)
    classification = ClassificationResult(source_item_id=source.id, relevance="WORK_RELATED",
        business_category="OTHER", sensitivity="HIGHLY_SENSITIVE", confidence=1,
        status="CONFIRMED")
    db_session.add(classification); db_session.flush()
    with pytest.raises(HTTPException) as exc:
        read_fact_source(db_session, user, "source", source.id)
    assert exc.value.status_code == 409
    assert read_fact_source(db_session, user, "source", source.id, acknowledge_sensitive=True).text
    source.status = "DISMISSED"; db_session.flush()
    with pytest.raises(HTTPException) as exc:
        read_fact_source(db_session, user, "source", source.id, acknowledge_sensitive=True)
    assert exc.value.status_code == 409


def test_file_snapshot_covers_content_after_24000_characters(db_session, seeded_user, tmp_path):
    from app.modules.cwi.services.document_service import DocumentService
    from app.modules.cwi.services.embedding import FakeEmbeddingProvider
    from app.modules.cwi.services.storage import LocalFilesystemStorage
    user = seeded_user["user"]
    service = DocumentService(db_session, LocalFilesystemStorage(tmp_path / "files"),
        FakeEmbeddingProvider(dimension=32))
    asset = service.upload(user.organization_id, user.id, filename="uat.txt", mime_type="text/plain",
        data=("a " * 13000 + "UAT is on 17 September 2026.").encode())
    service.process(user.organization_id, user.id, asset.id)
    before = read_fact_source(db_session, user, "file", asset.id)
    assert len(before.text) > 24000 and before.source_time is None
    last = db_session.scalar(select(DocumentChunk).where(DocumentChunk.document_asset_id == asset.id)
        .order_by(DocumentChunk.chunk_index.desc()).limit(1))
    last.text += " Late revision."
    db_session.flush()
    after = read_fact_source(db_session, user, "file", asset.id)
    assert before.fingerprint != after.fingerprint
    assert after.locator["chunks"][-1]["end"] == len(after.text)
