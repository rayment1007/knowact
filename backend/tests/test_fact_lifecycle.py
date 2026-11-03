"""Fact-only redaction guarantees; never claim whole-workspace erasure here."""
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.fact_models import (FactIdentity, FactSourceSnapshot, FactEvidence,
    FactVersion, FactEvidenceLink, FactProposal, FactReview)
from app.core.models import BusinessEntity, SourceItem, User
from app.core.services.fact_lifecycle import mark_fact_sources_removed, purge_fact_origin
from app.core.services.ingestion_service import IngestionService


def _scope(user):
    return {"organization_id": user.organization_id, "workspace_owner_id": user.id}


def _snapshot(db, user, *, origin="a", source_id=None, kind="source"):
    row = FactSourceSnapshot(**_scope(user), source_kind=kind,
        source_id=source_id or uuid4(), origin_key=origin * 64,
        source_fingerprint=uuid4().hex * 2, source_title="Private title")
    db.add(row); db.flush()
    evidence = FactEvidence(**_scope(user), snapshot_id=row.id,
        excerpt="UAT is on 17 September 2026.", excerpt_hash="f" * 64,
        locator_json={"quote": "17 September 2026"})
    db.add(evidence); db.flush()
    return row, evidence


def _graph(db, user):
    source = SourceItem(organization_id=user.organization_id, created_by=user.id,
        source_type="MANUAL", title="UAT", content="UAT is on 17 September 2026.")
    project = BusinessEntity(**_scope(user), entity_type="PROJECT", name="Project")
    db.add_all([source, project]); db.flush()
    identity = FactIdentity(**_scope(user), project_id=project.id,
        fact_type="MEETING_TIME", canonical_key="uat_review")
    db.add(identity); db.flush()
    snapshot, evidence = _snapshot(db, user, source_id=source.id)
    version = FactVersion(**_scope(user), identity_id=identity.id, version_number=1,
        value_json={"date": "2026-09-17", "precision": "DATE"}, approved_by=user.id)
    db.add(version); db.flush()
    proposal = FactProposal(**_scope(user), identity_id=identity.id, evidence_id=evidence.id,
        value_json=version.value_json, relationship="NEW", comparison_ready=True,
        status="APPROVED", result_version_id=version.id)
    link = FactEvidenceLink(**_scope(user), identity_id=identity.id, version_id=version.id,
        evidence_id=evidence.id, evidence_key="a" * 64)
    db.add_all([proposal, link]); db.flush()
    review = FactReview(**_scope(user), identity_id=identity.id, proposal_id=proposal.id,
        proposal_revision=1, decision="APPROVE", request_key=str(uuid4()),
        request_hash="1" * 64, result_version_id=version.id,
        detail={"old_value": "17 September 2026"})
    db.add(review); db.flush()
    return source, identity, snapshot, evidence, version, proposal, review


def test_local_source_removal_marks_original_unavailable_retains_approved_fact(db_session, seeded_user):
    user = seeded_user["user"]
    source, identity, snapshot, evidence, version, proposal, review = _graph(db_session, user)
    IngestionService(db_session, owner_id=user.id).delete(user.organization_id, source.id, user.id)
    db_session.expire_all()
    assert db_session.get(SourceItem, source.id) is None
    assert snapshot.available is False and snapshot.purged_at is None
    assert evidence.excerpt == "UAT is on 17 September 2026."
    assert version.value_json["date"] == "2026-09-17"
    assert proposal.status == "APPROVED" and review.redacted_at is None


def test_fact_purge_redacts_all_copies_without_removing_history(db_session, seeded_user):
    user = seeded_user["user"]
    source, identity, snapshot, evidence, version, proposal, review = _graph(db_session, user)
    result = purge_fact_origin(db_session, org_id=user.organization_id,
        owner_id=user.id, snapshot_id=snapshot.id)
    assert (result.snapshots, result.evidence, result.proposals, result.versions, result.reviews) == (1, 1, 1, 1, 1)
    assert snapshot.available is False and snapshot.source_title is None
    assert evidence.excerpt is None and evidence.locator_json == {}
    assert proposal.value_json is None and proposal.comparison_ready is False
    assert version.value_json is None and version.status == "CURRENT"
    assert version.version_number == 1 and version.approved_by == user.id
    assert review.detail == {} and review.decision == "APPROVE"
    assert review.redacted_at is not None
    # Honest scope: this helper is internal fact-layer work, not raw erasure.
    assert db_session.get(SourceItem, source.id).content
    again = purge_fact_origin(db_session, org_id=user.organization_id,
        owner_id=user.id, snapshot_id=snapshot.id)
    assert (again.snapshots, again.evidence, again.proposals, again.versions, again.reviews) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize("other_origin,kept", [("a", False), ("b", True)])
def test_reimport_is_not_independent_support(db_session, seeded_user, other_origin, kept):
    user = seeded_user["user"]
    _, identity, snapshot, evidence, version, proposal, review = _graph(db_session, user)
    another, quote = _snapshot(db_session, user, origin=other_origin)
    # A removed original still has legitimate retained approved evidence.
    another.available = False
    db_session.add(FactEvidenceLink(**_scope(user), identity_id=identity.id,
        version_id=version.id, evidence_id=quote.id, evidence_key="b" * 64))
    db_session.flush()
    purge_fact_origin(db_session, org_id=user.organization_id, owner_id=user.id,
        snapshot_id=snapshot.id)
    assert (version.value_json is not None) is kept
    assert (quote.excerpt is not None) is kept
    assert (another.purged_at is None) is kept
    assert evidence.excerpt is None and proposal.value_json is None and review.detail == {}


def test_purge_preserves_version_chain_and_invalidates_other_cached_comparison(db_session, seeded_user):
    user = seeded_user["user"]
    _, identity, snapshot, evidence, old, proposal, review = _graph(db_session, user)
    old.status = "SUPERSEDED"; db_session.flush()
    current = FactVersion(**_scope(user), identity_id=identity.id, version_number=2,
        value_json={"date": "2026-09-18", "precision": "DATE"},
        predecessor_id=old.id, approved_by=user.id)
    db_session.add(current); db_session.flush()
    db_session.add(FactEvidenceLink(**_scope(user), identity_id=identity.id,
        version_id=current.id, evidence_id=evidence.id, evidence_key="2" * 64))
    second_source, second_evidence = _snapshot(db_session, user, origin="b")
    pending = FactProposal(**_scope(user), identity_id=identity.id, evidence_id=second_evidence.id,
        value_json={"date": "2026-09-20"}, expected_head_id=current.id,
        relationship="REVISION", comparison_ready=True)
    db_session.add(pending); db_session.flush()
    purge_fact_origin(db_session, org_id=user.organization_id, owner_id=user.id,
        snapshot_id=snapshot.id)
    assert old.status == "SUPERSEDED" and old.value_json is None
    assert current.status == "CURRENT" and current.value_json is None and current.predecessor_id == old.id
    assert pending.value_json == {"date": "2026-09-20"}
    assert pending.comparison_ready is False


def test_fact_lifecycle_is_owner_scoped(db_session, seeded_user):
    user = seeded_user["user"]
    source, _, snapshot, evidence, version, _, _ = _graph(db_session, user)
    other = User(organization_id=user.organization_id, full_name="Other", email="other-facts@example.test",
        password_hash="unused", role="MEMBER")
    db_session.add(other); db_session.flush()
    assert mark_fact_sources_removed(db_session, org_id=user.organization_id,
        owner_id=other.id, source_kind="source", source_ids=[source.id]) == 0
    with pytest.raises(HTTPException) as exc:
        purge_fact_origin(db_session, org_id=user.organization_id, owner_id=other.id,
            snapshot_id=snapshot.id)
    assert exc.value.status_code == 404
    assert snapshot.available and evidence.excerpt and version.value_json


def test_fact_purge_rolls_back_as_a_unit(db_session, seeded_user, monkeypatch):
    user = seeded_user["user"]
    _, _, snapshot, evidence, version, proposal, review = _graph(db_session, user)
    original_flush = db_session.flush
    calls = 0
    def fail_final_flush(*args, **kwargs):
        nonlocal calls
        calls += 1
        # begin_nested flushes once, the helper's final flush is second.
        if calls == 2:
            raise RuntimeError("Simulated store failure")
        return original_flush(*args, **kwargs)
    monkeypatch.setattr(db_session, "flush", fail_final_flush)
    with pytest.raises(RuntimeError):
        purge_fact_origin(db_session, org_id=user.organization_id, owner_id=user.id,
            snapshot_id=snapshot.id)
    monkeypatch.setattr(db_session, "flush", original_flush)
    db_session.expire_all()
    assert snapshot.available and snapshot.purged_at is None
    assert evidence.excerpt and version.value_json and proposal.value_json and review.detail


@pytest.mark.parametrize("remove_raw", [False, True])
def test_email_removal_marks_canonical_and_raw_alias_snapshots(db_session, seeded_user, remove_raw):
    from tests.test_cwi_privacy_routes import _seed_connection, _seed_email_record
    from app.modules.cwi.services.privacy_service import PrivacyService
    user = seeded_user["user"]
    source, _, raw_snapshot, _, _, _, _ = _graph(db_session, user)
    connection = _seed_connection(db_session, user.organization_id, user.id)
    email = _seed_email_record(db_session, user.organization_id, connection.id, source_item_id=source.id)
    canonical, _ = _snapshot(db_session, user, source_id=email.id, kind="email")
    if remove_raw:
        IngestionService(db_session, owner_id=user.id).delete(user.organization_id, source.id, user.id)
    else:
        PrivacyService(db_session).delete_email_data(user.organization_id, user.id,
            scope="SELECTED", record_ids=[email.id])
    db_session.expire_all()
    assert raw_snapshot.available is False and canonical.available is False


def test_document_remove_marks_fact_source_unavailable(db_session, seeded_user, tmp_path):
    from app.modules.cwi.services.document_service import DocumentService
    from app.modules.cwi.services.embedding import FakeEmbeddingProvider
    from app.modules.cwi.services.storage import LocalFilesystemStorage
    user = seeded_user["user"]
    storage = LocalFilesystemStorage(tmp_path / "files")
    service = DocumentService(db_session, storage, FakeEmbeddingProvider(dimension=32))
    asset = service.upload(user.organization_id, user.id, filename="uat.txt", mime_type="text/plain",
        data=b"UAT is on 17 September 2026.")
    snapshot, _ = _snapshot(db_session, user, source_id=asset.id, kind="file")
    service.delete(user.organization_id, user.id, asset.id)
    db_session.expire_all()
    assert snapshot.available is False and snapshot.purged_at is None
