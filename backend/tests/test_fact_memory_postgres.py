"""Phase 2 migration and transaction checks against disposable PostgreSQL.

Opt in with KNOWACT_TEST_POSTGRES_URL. The URL guard permits only the local
test cluster, never the configured application database. Every test owns a
random schema; all synthetic rows and DDL are removed after its connections
close. This tests migration 0015, not the complete historic migration chain.
"""
from __future__ import annotations

import importlib.util
import os
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import Session


FACT_TABLES = {
    "fact_identities", "fact_source_snapshots", "fact_evidence",
    "fact_versions", "fact_proposals", "fact_evidence_links", "fact_reviews",
}


def _local_test_engine():
    url = os.environ.get("KNOWACT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set KNOWACT_TEST_POSTGRES_URL for disposable local PostgreSQL")
    parsed = sa.engine.make_url(url)
    assert parsed.drivername == "postgresql+psycopg"
    assert parsed.host == "127.0.0.1" and parsed.port == 55439
    assert parsed.username == "knowact_test" and parsed.database == "postgres"
    assert not parsed.query, "The test connection must not override its endpoint"
    return sa.create_engine(url)


def _migration(db):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0015_structured_fact_memory.py"
    spec = importlib.util.spec_from_file_location("fact_memory_migration_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(db))
    return module


@pytest.fixture()
def fact_pg():
    engine = _local_test_engine()
    schema = "phase2_" + uuid4().hex
    assert re.fullmatch(r"phase2_[0-9a-f]{32}", schema)
    try:
        with engine.begin() as db:
            db.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            db.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            db.execute(sa.text("CREATE TABLE organizations (id uuid PRIMARY KEY)"))
            db.execute(sa.text("CREATE TABLE users (id uuid PRIMARY KEY, organization_id uuid NOT NULL)"))
            db.execute(sa.text("CREATE TABLE business_entities (id uuid PRIMARY KEY, organization_id uuid NOT NULL, workspace_owner_id uuid)"))
            _migration(db).upgrade()
        with engine.connect() as db:
            db.execute(sa.text(f'SET search_path TO "{schema}"'))
            db.commit()
            # Explicit schema keeps reflected relations isolated from public.
            metadata = sa.MetaData(schema=schema)
            metadata.reflect(bind=db)
            db.commit()
            yield SimpleNamespace(engine=engine, db=db, schema=schema, metadata=metadata)
            db.rollback()
    finally:
        # The generated identifier is validated above and belongs only to this
        # fixture. Parallel-test workers must finish before fixture teardown.
        with engine.begin() as db:
            db.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


@pytest.fixture()
def fact_service_pg():
    """Real ORM tables needed by reviews, without the unrelated vector stack."""
    from app.database import Base
    engine = _local_test_engine()
    schema = "phase2_" + uuid4().hex
    assert re.fullmatch(r"phase2_[0-9a-f]{32}", schema)
    tables = FACT_TABLES | {"organizations", "users", "business_entities", "source_items",
        "classification_results", "audit_logs", "integration_connections", "email_message_records",
        "fact_resolutions", "fact_comparison_requests", "fact_extraction_runs", "fact_extraction_requests"}
    try:
        with engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            Base.metadata.create_all(connection, tables=[Base.metadata.tables[name] for name in tables])

        def session():
            db = Session(engine, expire_on_commit=False)
            db.execute(sa.text(f'SET search_path TO "{schema}"'))
            db.execute(sa.text("SET lock_timeout = '10s'"))
            db.execute(sa.text("SET statement_timeout = '20s'"))
            return db

        yield SimpleNamespace(engine=engine, schema=schema, session=session)
    finally:
        with engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def _service_candidates(pg):
    from app.core.models import Organization, User, BusinessEntity, SourceItem
    from app.core.fact_models import FactEvidence, FactSourceSnapshot
    from app.modules.cwi.models import IntegrationConnection, EmailMessageRecord
    from app.core.services.fact_memory_service import FactMemoryService
    with pg.session() as db:
        organization = Organization(name="Synthetic review concurrency")
        db.add(organization); db.flush()
        user = User(organization_id=organization.id, email=f"{uuid4().hex}@example.invalid",
                    full_name="Synthetic reviewer", password_hash="not-a-real-login", role="MEMBER")
        db.add(user); db.flush()
        project = BusinessEntity(organization_id=organization.id, workspace_owner_id=user.id,
                                entity_type="PROJECT", name="UAT test project")
        connection = IntegrationConnection(organization_id=organization.id, user_id=user.id,
            provider="GOOGLE", service="GMAIL", external_account_id=uuid4().hex,
            account_email=user.email, access_token_encrypted=b"synthetic-never-used")
        db.add_all([project, connection]); db.flush()
        service = FactMemoryService(db, owner_id=user.id, org_id=organization.id)
        proposals = []
        for index in range(2):
            quote = "UAT is scheduled for 17 September 2026."
            source = SourceItem(organization_id=organization.id, created_by=user.id,
                source_type="EMAIL", title=f"UAT notice {index}", content=quote,
                received_at=datetime(2026, 9, 1 + index, tzinfo=timezone.utc))
            db.add(source); db.flush()
            email = EmailMessageRecord(organization_id=organization.id,
                integration_connection_id=connection.id, source_item_id=source.id,
                gmail_message_id=f"synthetic-{index}", gmail_thread_id=f"thread-{index}",
                sender="synthetic@example.invalid", subject=source.title,
                content_hash=str(index) * 64, received_at=source.received_at)
            db.add(email); db.flush()
            proposal = service.propose(project_id=project.id, canonical_key="uat_date",
                source_kind="email", source_id=email.id, value_date="2026-09-17", excerpt=quote)
            service.record_comparison(proposal_id=proposal.id, revision=proposal.revision,
                                      expected_head_id=None, relationship="NEW")
            evidence = db.get(FactEvidence, proposal.evidence_id)
            snapshot = db.get(FactSourceSnapshot, evidence.snapshot_id)
            proposals.append(dict(proposal_id=proposal.id, expected_revision=proposal.revision,
                expected_head_id=None, expected_source_fingerprint=snapshot.source_fingerprint,
                decision="APPROVE"))
        result = SimpleNamespace(owner=user.id, org=organization.id, proposals=proposals)
        db.commit()
        return result


def _concurrent_reviews(pg, workspace, requests):
    from fastapi import HTTPException
    from app.core.services.fact_memory_service import FactMemoryService
    barrier = Barrier(2)

    def approve(request):
        with pg.session() as db:
            service = FactMemoryService(db, owner_id=workspace.owner, org_id=workspace.org)
            barrier.wait(timeout=10)
            try:
                review = service.review(**request)
                result = ("ok", review.id, review.result_version_id)
                db.commit()
                return result
            except HTTPException as error:
                db.rollback()
                return ("error", error.status_code, str(error.detail))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(approve, request) for request in requests]
        return [future.result(timeout=30) for future in futures]


def test_concurrent_approvals_only_accept_one_current_head(fact_service_pg):
    from app.core.fact_models import FactVersion, FactReview, FactProposal
    pg = fact_service_pg
    workspace = _service_candidates(pg)
    requests = [dict(proposal, request_key=f"approve-{index}")
                for index, proposal in enumerate(workspace.proposals)]
    results = _concurrent_reviews(pg, workspace, requests)
    assert sorted(result[0] for result in results) == ["error", "ok"]
    assert [result[1] for result in results if result[0] == "error"] == [409]
    with pg.session() as db:
        versions = list(db.scalars(sa.select(FactVersion)))
        assert len(versions) == 1 and versions[0].status == "CURRENT"
        assert db.scalar(sa.select(sa.func.count()).select_from(FactReview)) == 1
        assert sorted(db.scalars(sa.select(FactProposal.status))) == ["APPROVED", "PENDING"]


def test_concurrent_identical_retry_returns_same_review(fact_service_pg):
    from app.core.fact_models import FactVersion, FactReview
    pg = fact_service_pg
    workspace = _service_candidates(pg)
    request = dict(workspace.proposals[0], request_key="same-double-click")
    results = _concurrent_reviews(pg, workspace, [request, request])
    assert all(result[0] == "ok" for result in results)
    assert results[0][1:] == results[1][1:]
    with pg.session() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(FactVersion)) == 1
        assert db.scalar(sa.select(sa.func.count()).select_from(FactReview)) == 1


def _revision_candidates(pg, workspace):
    from app.core.models import SourceItem
    from app.core.fact_models import FactIdentity, FactEvidence, FactSourceSnapshot
    from app.modules.cwi.models import IntegrationConnection, EmailMessageRecord
    from app.core.services.fact_memory_service import FactMemoryService
    with pg.session() as db:
        service = FactMemoryService(db, owner_id=workspace.owner, org_id=workspace.org)
        first_review = service.review(**workspace.proposals[0], request_key="initial-approval")
        head_id = first_review.result_version_id
        project_id = db.scalar(sa.select(FactIdentity.project_id))
        connection_id = db.scalar(sa.select(IntegrationConnection.id))
        proposals = []
        for day in (18, 19):
            quote = f"UAT was rescheduled from 17 September 2026 to {day} September 2026."
            source = SourceItem(organization_id=workspace.org, created_by=workspace.owner,
                source_type="EMAIL", title=f"Revised UAT {day}", content=quote,
                received_at=datetime(2026, 9, 5, tzinfo=timezone.utc))
            db.add(source); db.flush()
            email = EmailMessageRecord(organization_id=workspace.org,
                integration_connection_id=connection_id, source_item_id=source.id,
                gmail_message_id=f"revision-{day}", gmail_thread_id="revision-thread",
                sender="synthetic@example.invalid", subject=source.title,
                content_hash=str(day) * 32, received_at=source.received_at)
            db.add(email); db.flush()
            proposal = service.propose(project_id=project_id, canonical_key="uat_date",
                source_kind="email", source_id=email.id, value_date=f"2026-09-{day}", excerpt=quote)
            service.record_comparison(proposal.id, revision=proposal.revision,
                                      expected_head_id=head_id, relationship="REVISION")
            evidence = db.get(FactEvidence, proposal.evidence_id)
            snapshot = db.get(FactSourceSnapshot, evidence.snapshot_id)
            proposals.append(dict(proposal_id=proposal.id, expected_revision=proposal.revision,
                expected_head_id=head_id, expected_source_fingerprint=snapshot.source_fingerprint,
                decision="APPROVE", request_key=f"revision-{day}"))
        db.commit()
        return proposals, head_id


def test_concurrent_revisions_preserve_previous_head_and_reject_stale_comparison(fact_service_pg):
    from app.core.fact_models import FactVersion, FactReview
    pg = fact_service_pg
    workspace = _service_candidates(pg)
    requests, previous_head = _revision_candidates(pg, workspace)
    results = _concurrent_reviews(pg, workspace, requests)
    assert sorted(result[0] for result in results) == ["error", "ok"]
    assert [result[1] for result in results if result[0] == "error"] == [409]
    with pg.session() as db:
        versions = list(db.scalars(sa.select(FactVersion).order_by(FactVersion.version_number)))
        assert [(version.version_number, version.status) for version in versions] == [(1, "SUPERSEDED"), (2, "CURRENT")]
        assert versions[0].id == previous_head and versions[1].predecessor_id == previous_head
        assert versions[0].value_json["date"] == "2026-09-17"
        assert versions[1].value_json["date"] in {"2026-09-18", "2026-09-19"}
        assert db.scalar(sa.select(sa.func.count()).select_from(FactReview)) == 2


def test_failed_audit_rolls_back_revision_and_review_atomically(fact_service_pg, monkeypatch):
    from app.core.fact_models import FactVersion, FactReview, FactProposal, FactEvidenceLink
    from app.core.services.audit_service import AuditService
    from app.core.services.fact_memory_service import FactMemoryService
    pg = fact_service_pg
    workspace = _service_candidates(pg)
    requests, previous_head = _revision_candidates(pg, workspace)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("Injected failure after version and review flush")

    with monkeypatch.context() as patch:
        patch.setattr(AuditService, "record", fail_audit)
        with pg.session() as db:
            service = FactMemoryService(db, owner_id=workspace.owner, org_id=workspace.org)
            with pytest.raises(RuntimeError, match="Injected failure"):
                service.review(**requests[0])
            # Deliberately commit the outer transaction after the failure:
            # the service savepoint must have removed every partial mutation.
            db.commit()
    with pg.session() as db:
        versions = list(db.scalars(sa.select(FactVersion)))
        assert len(versions) == 1 and versions[0].id == previous_head
        assert versions[0].status == "CURRENT" and versions[0].value_json["date"] == "2026-09-17"
        assert db.get(FactProposal, requests[0]["proposal_id"]).status == "PENDING"
        assert db.scalar(sa.select(sa.func.count()).select_from(FactReview)) == 1
        assert db.scalar(sa.select(sa.func.count()).select_from(FactEvidenceLink)) == 1
        review = FactMemoryService(db, owner_id=workspace.owner, org_id=workspace.org).review(**requests[0])
        assert review.result_version_id != previous_head
        db.commit()


def _table(pg, name):
    return pg.metadata.tables[f"{pg.schema}.{name}"]


def _insert(pg, name, **values):
    values.setdefault("id", uuid4())
    pg.db.execute(_table(pg, name).insert().values(**values))
    return values["id"]


def _workspace(pg, *, org=None, owner=None):
    if org is None:
        org = _insert(pg, "organizations")
    if owner is None:
        owner = _insert(pg, "users", organization_id=org)
    project = _insert(pg, "business_entities", organization_id=org, workspace_owner_id=owner)
    return SimpleNamespace(org=org, owner=owner, project=project)


def _scope(workspace):
    return {"organization_id": workspace.org, "workspace_owner_id": workspace.owner}


def _identity(pg, workspace, **overrides):
    return _insert(pg, "fact_identities", **{
        **_scope(workspace), "project_id": workspace.project,
        "fact_type": "DEADLINE", "canonical_key": "uat_date", "occurrence_key": "",
        **overrides,
    })


def _evidence(pg, workspace):
    snapshot = _insert(pg, "fact_source_snapshots", **_scope(workspace),
        source_kind="email", source_id=uuid4(), origin_key="a" * 64,
        source_fingerprint="b" * 64, source_title="Synthetic UAT notice")
    return _insert(pg, "fact_evidence", **_scope(workspace), snapshot_id=snapshot,
        excerpt="UAT is scheduled for 17 September 2026.", excerpt_hash="c" * 64)


def _version(pg, workspace, identity, **overrides):
    return _insert(pg, "fact_versions", **{
        **_scope(workspace), "identity_id": identity, "version_number": 1,
        "value_json": {"date": "2026-09-17"}, "approved_by": workspace.owner,
        **overrides,
    })


def _proposal(pg, workspace, identity, evidence, **overrides):
    return _insert(pg, "fact_proposals", **{
        **_scope(workspace), "identity_id": identity, "evidence_id": evidence,
        "value_json": {"date": "2026-09-18"}, **overrides,
    })


def _review(pg, workspace, identity, proposal, **overrides):
    return _insert(pg, "fact_reviews", **{
        **_scope(workspace), "identity_id": identity, "proposal_id": proposal,
        "proposal_revision": 1, "decision": "EDIT", "request_key": uuid4().hex,
        "request_hash": "d" * 64, "detail": {"previous_date": "2026-09-17"},
        **overrides,
    })


def _rejects(pg, statement, *, constraint=None, message=None):
    with pytest.raises(sa.exc.DBAPIError) as caught:
        with pg.db.begin_nested():
            pg.db.execute(statement)
    if constraint:
        assert caught.value.orig.diag.constraint_name == constraint
    if message:
        assert message in str(caught.value.orig)


def test_0015_migration_roundtrip_preserves_legacy_rows(fact_pg):
    pg = fact_pg
    workspace = _workspace(pg)
    _identity(pg, workspace)
    assert FACT_TABLES <= set(sa.inspect(pg.db).get_table_names(schema=pg.schema))
    migration = _migration(pg.db)
    migration.downgrade()
    assert not FACT_TABLES & set(sa.inspect(pg.db).get_table_names(schema=pg.schema))
    for table, expected in [("users", workspace.owner), ("business_entities", workspace.project)]:
        assert pg.db.scalar(sa.select(_table(pg, table).c.id)) == expected
    migration.upgrade()
    assert FACT_TABLES <= set(sa.inspect(pg.db).get_table_names(schema=pg.schema))
    assert pg.db.scalar(sa.select(sa.func.count()).select_from(_table(pg, "fact_versions"))) == 0
    _identity(pg, workspace)


def test_fact_identity_cannot_cross_owner_project_or_organization(fact_pg):
    pg = fact_pg
    a = _workspace(pg)
    b = _workspace(pg, org=a.org)
    outsider = _workspace(pg)
    table = _table(pg, "fact_identities")
    base = dict(id=uuid4(), **_scope(a), project_id=b.project,
                fact_type="DEADLINE", canonical_key="uat_date", occurrence_key="")
    _rejects(pg, table.insert().values(**base), constraint="fk_fact_identity_project")
    base.update(id=uuid4(), project_id=outsider.project)
    _rejects(pg, table.insert().values(**base), constraint="fk_fact_identity_project")
    snapshot = _table(pg, "fact_source_snapshots")
    _rejects(pg, snapshot.insert().values(id=uuid4(), **_scope(a),
        source_kind="email", source_id=uuid4(), origin_key="a" * 64,
        source_fingerprint="b" * 64).values(workspace_owner_id=outsider.owner),
        constraint="fk_fact_snapshot_owner")


def test_fact_identity_unique_within_project_and_occurrence(fact_pg):
    pg = fact_pg
    a = _workspace(pg)
    _identity(pg, a)
    _rejects(pg, _table(pg, "fact_identities").insert().values(
        id=uuid4(), **_scope(a), project_id=a.project,
        fact_type="DEADLINE", canonical_key="uat_date", occurrence_key=""),
        constraint="uq_fact_identity_key")
    _identity(pg, a, occurrence_key="round_2")
    _identity(pg, _workspace(pg, org=a.org))


def test_fact_identity_cannot_reinterpret_accepted_history(fact_pg):
    pg = fact_pg
    workspace = _workspace(pg)
    identity = _identity(pg, workspace)
    _version(pg, workspace, identity)
    table = _table(pg, "fact_identities")
    for changes in ({"canonical_key": "different_meeting"}, {"occurrence_key": "round_2"},
                    {"fact_type": "PROJECT_STATUS"}, {"project_id": uuid4()}):
        _rejects(pg, table.update().where(table.c.id == identity).values(**changes),
                 message="Fact identity is immutable")


def test_owner_and_identity_scoping_on_evidence_proposal_and_version(fact_pg):
    pg = fact_pg
    a = _workspace(pg)
    b = _workspace(pg, org=a.org)
    identity = _identity(pg, a)
    other_identity = _identity(pg, a, canonical_key="release_date")
    evidence = _evidence(pg, a)
    foreign_evidence = _evidence(pg, b)
    version = _version(pg, a, identity)
    other_version = _version(pg, a, other_identity)
    proposal_base = dict(id=uuid4(), **_scope(a), identity_id=identity,
                        evidence_id=foreign_evidence, value_json={"date": "2026-09-18"})
    _rejects(pg, _table(pg, "fact_proposals").insert().values(**proposal_base),
             constraint="fk_fact_proposal_evidence")
    proposal_base.update(id=uuid4(), evidence_id=evidence, expected_head_id=other_version)
    _rejects(pg, _table(pg, "fact_proposals").insert().values(**proposal_base),
             constraint="fk_fact_proposal_head")
    _rejects(pg, _table(pg, "fact_versions").insert().values(id=uuid4(), **_scope(b),
        identity_id=identity, version_number=2, status="SUPERSEDED",
        value_json={"date": "2026-09-18"}, approved_by=b.owner),
        constraint="fk_fact_version_identity")
    _rejects(pg, _table(pg, "fact_versions").insert().values(id=uuid4(), **_scope(a),
        identity_id=identity, version_number=2, status="SUPERSEDED", predecessor_id=other_version,
        value_json={"date": "2026-09-18"}, approved_by=a.owner),
        constraint="fk_fact_version_predecessor")
    assert version != other_version


def test_only_one_current_version_and_append_only_revision_chain(fact_pg):
    pg = fact_pg
    a = _workspace(pg)
    identity = _identity(pg, a)
    version = _version(pg, a, identity)
    table = _table(pg, "fact_versions")
    _rejects(pg, table.insert().values(id=uuid4(), **_scope(a), identity_id=identity,
        version_number=2, approved_by=a.owner, value_json={"date": "2026-09-18"}),
        constraint="uq_fact_version_current")
    pg.db.execute(table.update().where(table.c.id == version).values(status="SUPERSEDED"))
    next_version = _version(pg, a, identity, version_number=2, predecessor_id=version,
                            value_json={"date": "2026-09-18"})
    _rejects(pg, table.update().where(table.c.id == version).values(status="CURRENT"),
             message="Accepted fact status cannot be restored")
    rows = pg.db.execute(sa.select(table.c.id, table.c.status).order_by(table.c.version_number)).all()
    assert rows == [(version, "SUPERSEDED"), (next_version, "CURRENT")]


@pytest.mark.parametrize("changes,message", [
    ({"value_json": {"date": "2026-09-19"}}, "Accepted fact values are immutable"),
    ({"version_number": 2}, "Accepted fact identity and review metadata are immutable"),
    ({"normalization_version": "OTHER"}, "Accepted fact identity and review metadata are immutable"),
    ({"approved_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}, "Accepted fact identity and review metadata are immutable"),
])
def test_accepted_value_and_review_metadata_cannot_be_rewritten(fact_pg, changes, message):
    pg = fact_pg
    a = _workspace(pg)
    version = _version(pg, a, _identity(pg, a))
    table = _table(pg, "fact_versions")
    _rejects(pg, table.update().where(table.c.id == version).values(**changes), message=message)


def test_accepted_history_cannot_be_deleted_and_redaction_requires_explicit_flag(fact_pg):
    pg = fact_pg
    a = _workspace(pg)
    version = _version(pg, a, _identity(pg, a))
    table = _table(pg, "fact_versions")
    row = table.c.id == version
    _rejects(pg, table.delete().where(row), message="Accepted fact history cannot be deleted")
    redaction = table.update().where(row).values(value_json=sa.null(), redacted_at=sa.func.now())
    # Includes the unset-custom-setting path: SQL NULL must not bypass the guard.
    _rejects(pg, redaction, message="Accepted fact values are immutable")
    pg.db.execute(sa.text("SET LOCAL knowact.allow_fact_redaction = 'on'"))
    pg.db.execute(redaction)
    assert pg.db.scalar(sa.select(table.c.value_json).where(row)) is None
    _rejects(pg, table.update().where(row).values(value_json={"date": "2026-09-17"}, redacted_at=None),
             message="Accepted fact values are immutable")


def test_review_history_is_append_only_but_can_explicitly_redact_content(fact_pg):
    pg = fact_pg
    a = _workspace(pg)
    identity = _identity(pg, a)
    proposal = _proposal(pg, a, identity, _evidence(pg, a))
    review = _review(pg, a, identity, proposal)
    table = _table(pg, "fact_reviews")
    row = table.c.id == review
    _rejects(pg, table.delete().where(row), message="Fact review history cannot be deleted")
    _rejects(pg, table.update().where(row).values(decision="REJECT"), message="Fact review history is immutable")
    redaction = table.update().where(row).values(detail={}, redacted_at=sa.func.now())
    _rejects(pg, redaction, message="Fact review content can only be explicitly redacted")
    pg.db.execute(sa.text("SET LOCAL knowact.allow_fact_redaction = 'on'"))
    pg.db.execute(redaction)
    _rejects(pg, table.update().where(row).values(detail={"restore": True}, redacted_at=None),
             message="Fact review content can only be explicitly redacted")


def test_evidence_link_and_request_keys_are_idempotency_boundaries(fact_pg):
    pg = fact_pg
    a = _workspace(pg)
    identity = _identity(pg, a)
    evidence = _evidence(pg, a)
    version = _version(pg, a, identity)
    proposal = _proposal(pg, a, identity, evidence)
    values = dict(**_scope(a), identity_id=identity, evidence_id=evidence,
                  version_id=version, evidence_key="e" * 64)
    _insert(pg, "fact_evidence_links", **values)
    _rejects(pg, _table(pg, "fact_evidence_links").insert().values(id=uuid4(), **values),
             constraint="uq_fact_link_evidence_key")
    _review(pg, a, identity, proposal, request_key="retry-key")
    _rejects(pg, _table(pg, "fact_reviews").insert().values(id=uuid4(), **_scope(a),
        identity_id=identity, proposal_id=proposal, proposal_revision=1, decision="EDIT",
        request_key="retry-key", request_hash="f" * 64), constraint="uq_fact_review_request")
