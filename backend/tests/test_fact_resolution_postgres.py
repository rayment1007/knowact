"""0017 and Phase 4 transactions against the disposable local PostgreSQL only.

The imported fixtures reject every endpoint except the synthetic test cluster
and clean their random schemas after all concurrent connections have closed.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
import sqlalchemy as sa

from app.core.fact_models import FactEvidence, FactIdentity, FactProposal, FactResolution, FactReview, FactSourceSnapshot, FactVersion
from app.core.models import BusinessEntity, Organization, SourceItem, User
from app.core.services.fact_memory_service import FactMemoryService
from app.core.services.fact_resolution_service import FactResolutionService
from app.modules.cwi.models import EmailMessageRecord, IntegrationConnection
from tests.test_fact_extraction_postgres import _upgrade as _upgrade_0016, _run
from tests.test_fact_memory_postgres import (
    fact_pg, fact_service_pg, _workspace, _scope, _table, _insert,
    _identity, _evidence, _proposal, _review, _rejects,
)


def _migration(db):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0017_fact_resolution.py"
    spec = importlib.util.spec_from_file_location("fact_resolution_migration_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(db))
    return module


def _upgrade(pg):
    _upgrade_0016(pg)
    _migration(pg.db).upgrade()
    pg.metadata.clear()
    pg.metadata.reflect(bind=pg.db)


def _resolution(pg, workspace, candidate, resolved, **overrides):
    return _insert(pg, "fact_resolutions", **{
        **_scope(workspace), "candidate_id": candidate, "candidate_revision": 1,
        "resolved_proposal_id": resolved, "request_key": uuid4().hex,
        "request_hash": "a" * 64, **overrides,
    })


def test_0017_roundtrip_keeps_candidates_reviews_and_0016_cache(fact_pg):
    pg = fact_pg
    workspace = _workspace(pg)
    identity = _identity(pg, workspace)
    candidate = _proposal(pg, workspace, identity, _evidence(pg, workspace))
    review = _review(pg, workspace, identity, candidate)
    _upgrade(pg)
    run = _run(pg, workspace)
    proposals = _table(pg, "fact_proposals")
    assert pg.db.scalar(sa.select(proposals.c.comparison_json).where(proposals.c.id == candidate)) == {}
    resolved = _proposal(pg, workspace, identity, _evidence(pg, workspace))
    _resolution(pg, workspace, candidate, resolved)
    migration = _migration(pg.db)
    migration.downgrade()
    assert "fact_resolutions" not in sa.inspect(pg.db).get_table_names(schema=pg.schema)
    assert "comparison_json" not in {c["name"] for c in sa.inspect(pg.db).get_columns("fact_proposals", schema=pg.schema)}
    assert pg.db.scalar(sa.text("SELECT id FROM fact_reviews WHERE id=:id"), {"id": review}) == review
    assert pg.db.scalar(sa.text("SELECT id FROM fact_extraction_runs WHERE id=:id"), {"id": run}) == run
    assert pg.db.scalar(sa.text("SELECT count(*) FROM fact_proposals")) == 2
    migration.upgrade()
    assert pg.db.scalar(sa.text("SELECT comparison_json FROM fact_proposals WHERE id=:id"), {"id": candidate}) == {}


def test_0017_resolution_and_comparison_receipts_enforce_owner_scope(fact_pg):
    pg = fact_pg
    own = _workspace(pg)
    peer = _workspace(pg, org=own.org)
    candidate = _proposal(pg, own, _identity(pg, own), _evidence(pg, own))
    peer_candidate = _proposal(pg, peer, _identity(pg, peer), _evidence(pg, peer))
    _upgrade(pg)
    table = _table(pg, "fact_resolutions")
    payload = dict(id=uuid4(), **_scope(own), candidate_id=candidate, candidate_revision=1,
        resolved_proposal_id=peer_candidate, request_key="cross-owner-child", request_hash="f" * 64)
    _rejects(pg, table.insert().values(**payload), constraint="fk_fact_resolution_proposal")
    _rejects(pg, table.insert().values(**{**payload, "candidate_id": peer_candidate,
        "resolved_proposal_id": candidate}), constraint="fk_fact_resolution_candidate")
    receipts = _table(pg, "fact_comparison_requests")
    receipt = dict(id=uuid4(), **_scope(own), proposal_id=peer_candidate, proposal_revision=2,
        request_key="cross-owner-comparison", request_hash="d" * 64)
    _rejects(pg, receipts.insert().values(**receipt), constraint="fk_fact_comparison_proposal")
    receipt.update(proposal_id=candidate)
    pg.db.execute(receipts.insert().values(**receipt))
    _rejects(pg, receipts.insert().values(**{**receipt, "id": uuid4()}), constraint="uq_fact_comparison_request")


def test_0017_lineage_is_immutable_and_has_one_active_match(fact_pg):
    pg = fact_pg
    own = _workspace(pg)
    identity = _identity(pg, own)
    evidence = _evidence(pg, own)
    candidate = _proposal(pg, own, identity, evidence)
    child = _proposal(pg, own, identity, evidence)
    another = _proposal(pg, own, identity, evidence)
    _upgrade(pg)
    link = _resolution(pg, own, candidate, child)
    table = _table(pg, "fact_resolutions")
    _rejects(pg, table.insert().values(id=uuid4(), **_scope(own), candidate_id=candidate,
        candidate_revision=1, resolved_proposal_id=another, request_key="parallel-match", request_hash="b" * 64),
        constraint="uq_fact_resolution_active")
    _rejects(pg, table.update().where(table.c.id == link).values(candidate_revision=2), message="lineage is immutable")
    _rejects(pg, table.delete().where(table.c.id == link), message="history cannot be deleted")
    pg.db.execute(table.update().where(table.c.id == link).values(active=False))
    _resolution(pg, own, candidate, another)
    _rejects(pg, table.update().where(table.c.id == link).values(active=True), message="lineage is immutable")
    assert pg.db.scalar(sa.select(sa.func.count()).select_from(table).where(table.c.active)) == 1


def _candidates(pg):
    """Two independent emails with extracted, unverified meeting candidates."""
    with pg.session() as db:
        org = Organization(name="Synthetic Phase 4 workspace")
        db.add(org); db.flush()
        user = User(organization_id=org.id, email=f"{uuid4().hex}@example.invalid",
            full_name="Synthetic reviewer", password_hash="not-a-login", role="MEMBER")
        db.add(user); db.flush()
        project = BusinessEntity(organization_id=org.id, workspace_owner_id=user.id,
            name="Synthetic UAT", entity_type="PROJECT")
        connection = IntegrationConnection(organization_id=org.id, user_id=user.id,
            provider="GOOGLE", service="GMAIL", external_account_id=uuid4().hex,
            account_email=user.email, access_token_encrypted=b"synthetic-never-used")
        db.add_all([project, connection]); db.flush()
        service = FactMemoryService(db, owner_id=user.id, org_id=org.id)
        requests = []
        for index in range(2):
            quote = "UAT review is on 17 September 2026."
            raw = SourceItem(organization_id=org.id, created_by=user.id, source_type="EMAIL",
                title=f"Synthetic UAT notice {index}", content=quote,
                received_at=datetime(2026, 9, index + 1, tzinfo=timezone.utc))
            db.add(raw); db.flush()
            email = EmailMessageRecord(organization_id=org.id, integration_connection_id=connection.id,
                source_item_id=raw.id, gmail_message_id=f"m{index}", gmail_thread_id=f"t{index}",
                sender="coordinator@example.invalid", subject=raw.title,
                content_hash=str(index) * 64, received_at=raw.received_at)
            db.add(email); db.flush()
            item = service.propose(project_id=project.id, canonical_key="candidate:" + uuid4().hex,
                source_kind="email", source_id=email.id, value_date="2026-09-17", excerpt=quote)
            item.extraction_context = {"identity_unverified": True, "subject": "UAT review",
                "source_context": "DIRECT", "modality": "ASSERTION", "requires_revalidation": False}
            evidence = db.get(FactEvidence, item.evidence_id)
            snapshot = db.get(FactSourceSnapshot, evidence.snapshot_id)
            requests.append(dict(candidate_id=item.id, expected_revision=item.revision,
                expected_source_fingerprint=snapshot.source_fingerprint, project_id=project.id,
                canonical_key="UAT review", occurrence_key="release 1"))
        db.commit()
        return SimpleNamespace(owner=user.id, org=org.id, project=project.id, requests=requests)


def _concurrent(pg, workspace, operations):
    barrier = Barrier(2)
    def execute(operation):
        method, request = operation
        with pg.session() as db:
            service = FactResolutionService(db, owner_id=workspace.owner, org_id=workspace.org)
            barrier.wait(timeout=10)
            try:
                result = getattr(service, method)(**request)
                result_id = result.id
                db.commit()
                return ("ok", result_id)
            except HTTPException as error:
                db.rollback()
                return ("error", error.status_code, str(error.detail))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(execute, operation) for operation in operations]
        return [future.result(timeout=30) for future in futures]


def _review_request(db, proposal_id, key):
    item = db.get(FactProposal, proposal_id)
    evidence = db.get(FactEvidence, item.evidence_id)
    snapshot = db.get(FactSourceSnapshot, evidence.snapshot_id)
    return dict(proposal_id=proposal_id, expected_revision=item.revision,
        expected_head_id=item.expected_head_id, expected_source_fingerprint=snapshot.source_fingerprint,
        decision="APPROVE", request_key=key)


def test_concurrent_same_resolution_request_returns_one_linked_proposal(fact_service_pg):
    pg = fact_service_pg
    workspace = _candidates(pg)
    request = dict(workspace.requests[0], request_key="double-click-resolve")
    results = _concurrent(pg, workspace, [("resolve", request), ("resolve", request)])
    assert results[0] == results[1] and results[0][0] == "ok"
    with pg.session() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(FactResolution)) == 1
        assert db.scalar(sa.select(sa.func.count()).select_from(FactProposal)) == 3
        original = db.get(FactProposal, request["candidate_id"])
        assert original.extraction_context["identity_unverified"] and original.revision == 1


def test_concurrent_rematches_keep_one_active_and_old_proposal_cannot_approve(fact_service_pg):
    pg = fact_service_pg
    workspace = _candidates(pg)
    requests = [dict(workspace.requests[0], request_key=f"match-{index}", canonical_key=f"meeting {index}") for index in range(2)]
    results = _concurrent(pg, workspace, [("resolve", request) for request in requests])
    assert all(result[0] == "ok" for result in results)
    with pg.session() as db:
        links = list(db.scalars(sa.select(FactResolution)))
        assert len(links) == 2 and sum(link.active for link in links) == 1
        old = next(link for link in links if not link.active)
        request = _review_request(db, old.resolved_proposal_id, "stale-match-approval")
        service = FactMemoryService(db, owner_id=workspace.owner, org_id=workspace.org)
        try:
            service.review(**request)
            raise AssertionError("An inactive match was approved")
        except HTTPException as error:
            assert error.status_code == 409
        assert db.scalar(sa.select(sa.func.count()).select_from(FactVersion)) == 0


def test_concurrent_new_candidates_reuse_identity_and_only_one_head_is_accepted(fact_service_pg):
    pg = fact_service_pg
    workspace = _candidates(pg)
    results = _concurrent(pg, workspace, [("resolve", dict(request, request_key=f"resolve-{index}"))
        for index, request in enumerate(workspace.requests)])
    assert all(result[0] == "ok" for result in results)
    with pg.session() as db:
        resolved = [db.get(FactProposal, result[1]) for result in results]
        assert resolved[0].identity_id == resolved[1].identity_id
        assert all(item.relationship == "NEW" for item in resolved)
        requests = [_review_request(db, item.id, f"approve-{index}") for index, item in enumerate(resolved)]
    reviews = _concurrent(pg, workspace, [("review", request) for request in requests])
    assert sorted(result[0] for result in reviews) == ["error", "ok"]
    assert [result[1] for result in reviews if result[0] == "error"] == [409]
    with pg.session() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(FactVersion)) == 1
        assert db.scalar(sa.select(sa.func.count()).select_from(FactReview)) == 1
        assert db.scalar(sa.select(FactVersion.status)) == "CURRENT"


def test_resolved_approval_double_click_is_idempotent_on_postgres(fact_service_pg):
    pg = fact_service_pg
    workspace = _candidates(pg)
    with pg.session() as db:
        item = FactResolutionService(db, owner_id=workspace.owner, org_id=workspace.org).resolve(
            **workspace.requests[0], request_key="one-match")
        request = _review_request(db, item.id, "one-approval")
        db.commit()
    results = _concurrent(pg, workspace, [("review", request), ("review", request)])
    assert results[0] == results[1] and results[0][0] == "ok"
    with pg.session() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(FactVersion)) == 1
        assert db.scalar(sa.select(sa.func.count()).select_from(FactReview)) == 1


def test_concurrent_request_key_reuse_for_different_candidates_is_rejected(fact_service_pg):
    pg = fact_service_pg
    workspace = _candidates(pg)
    results = _concurrent(pg, workspace, [("resolve", dict(request, request_key="accidental-shared-key"))
        for request in workspace.requests])
    assert sorted(result[0] for result in results) == ["error", "ok"]
    assert [result[1] for result in results if result[0] == "error"] == [409]
    with pg.session() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(FactResolution)) == 1
        assert db.scalar(sa.select(sa.func.count()).select_from(FactProposal)) == 3


def test_concurrent_rematch_cannot_reinterpret_an_approved_candidate(fact_service_pg):
    pg = fact_service_pg
    workspace = _candidates(pg)
    with pg.session() as db:
        item = FactResolutionService(db, owner_id=workspace.owner, org_id=workspace.org).resolve(
            **workspace.requests[0], request_key="initial-match")
        old_id = item.id
        review_request = _review_request(db, item.id, "approve-original-match")
        db.commit()
    rematch_request = dict(workspace.requests[0], canonical_key="different meeting", request_key="correct-match")
    results = _concurrent(pg, workspace, [("review", review_request), ("resolve", rematch_request)])
    assert sorted(result[0] for result in results) == ["error", "ok"]
    assert [result[1] for result in results if result[0] == "error"] == [409]
    with pg.session() as db:
        original = db.get(FactProposal, old_id)
        active = db.scalar(sa.select(FactResolution).where(FactResolution.active.is_(True)))
        versions = list(db.scalars(sa.select(FactVersion)))
        if results[0][0] == "ok":
            assert original.status == "APPROVED" and active.resolved_proposal_id == old_id
            assert len(versions) == 1 and versions[0].identity_id == original.identity_id
        else:
            assert original.status == "PENDING" and active.resolved_proposal_id != old_id
            assert not versions
