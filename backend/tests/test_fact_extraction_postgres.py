"""0016 additive migration and cache ownership on disposable PostgreSQL only."""
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import Session

from tests.test_fact_memory_postgres import (
    fact_pg, _workspace, _scope, _insert, _table, _identity, _evidence, _proposal, _rejects, _local_test_engine, FACT_TABLES,
)


def _migration(db):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0016_fact_extraction_runs.py"
    spec = importlib.util.spec_from_file_location("fact_extraction_migration_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(db))
    return module


def _upgrade(pg):
    _migration(pg.db).upgrade()
    pg.metadata.clear()
    pg.metadata.reflect(bind=pg.db)


def _run(pg, workspace, **overrides):
    values = dict(id=uuid4(), **_scope(workspace), project_id=workspace.project,
        source_kind="email", source_id=uuid4(), origin_key="a" * 64,
        source_fingerprint="b" * 64, cache_key="c" * 64, request_key=uuid4().hex, request_hash="e" * 64,
        status="SUCCEEDED", extractor_version="M1_DATE_EXTRACTION_V1", provider="mock",
        model="mock", coverage_json={"complete": True}, output_json={"summary": "Synthetic", "facts": []},
        proposal_ids=[], generation=1)
    values.update(overrides)
    pg.db.execute(_table(pg, "fact_extraction_runs").insert().values(**values))
    return values["id"]


def test_0016_upgrade_downgrade_preserves_prior_fact_proposal(fact_pg):
    pg = fact_pg
    workspace = _workspace(pg)
    proposal = _proposal(pg, workspace, _identity(pg, workspace), _evidence(pg, workspace))
    _upgrade(pg)
    proposal_table = _table(pg, "fact_proposals")
    assert pg.db.scalar(sa.select(proposal_table.c.extraction_context).where(proposal_table.c.id == proposal)) == {}
    _run(pg, workspace)
    migration = _migration(pg.db)
    migration.downgrade()
    assert "fact_extraction_runs" not in sa.inspect(pg.db).get_table_names(schema=pg.schema)
    assert "extraction_context" not in {column["name"] for column in sa.inspect(pg.db).get_columns("fact_proposals", schema=pg.schema)}
    assert pg.db.scalar(sa.text("SELECT count(*) FROM fact_proposals")) == 1
    migration.upgrade()
    assert pg.db.scalar(sa.text("SELECT extraction_context FROM fact_proposals WHERE id=:id"), {"id": proposal}) == {}


def test_extraction_run_owner_and_project_are_consistent(fact_pg):
    pg = fact_pg
    own = _workspace(pg)
    peer = _workspace(pg, org=own.org)
    _upgrade(pg)
    table = _table(pg, "fact_extraction_runs")
    run_id = _run(pg, own)
    assert pg.db.scalar(sa.select(table.c.workspace_owner_id).where(table.c.id == run_id)) == own.owner
    with pytest.raises(sa.exc.DBAPIError) as caught:
        with pg.db.begin_nested():
            _run(pg, own, project_id=peer.project, cache_key="d" * 64)
    assert caught.value.orig.diag.constraint_name == "fk_fact_extraction_project"


def test_extraction_attempt_and_cache_generation_are_unique(fact_pg):
    pg = fact_pg
    workspace = _workspace(pg)
    _upgrade(pg)
    _run(pg, workspace, request_key="idempotent-request")
    with pytest.raises(sa.exc.DBAPIError) as caught:
        with pg.db.begin_nested():
            _run(pg, workspace, request_key="idempotent-request", cache_key="d" * 64)
    assert caught.value.orig.diag.constraint_name == "uq_fact_extraction_request"
    with pytest.raises(sa.exc.DBAPIError) as caught:
        with pg.db.begin_nested():
            _run(pg, workspace)
    assert caught.value.orig.diag.constraint_name == "uq_fact_extraction_generation"
    _run(pg, workspace, generation=2)


def test_failed_and_redacted_run_do_not_look_like_successful_content(fact_pg):
    pg = fact_pg
    workspace = _workspace(pg)
    _upgrade(pg)
    with pytest.raises(sa.exc.DBAPIError) as caught:
        with pg.db.begin_nested():
            _run(pg, workspace, status="FAILED", output_json=None)
    assert caught.value.orig.diag.constraint_name == "ck_fact_extraction_error"
    run_id = _run(pg, workspace, status="FAILED", output_json=None, error_code="AI_UNAVAILABLE")
    table = _table(pg, "fact_extraction_runs")
    _rejects(pg, table.update().where(table.c.id == run_id).values(
        output_json={"secret": "cannot restore on redacted row"}, redacted_at=sa.func.now()),
        constraint="ck_fact_extraction_redaction")


def test_cached_request_receipts_cannot_cross_workspace_or_reuse_request_key(fact_pg):
    pg = fact_pg
    own = _workspace(pg)
    peer = _workspace(pg, org=own.org)
    _upgrade(pg)
    run_id = _run(pg, own)
    receipt = _table(pg, "fact_extraction_requests")
    values = dict(id=uuid4(), **_scope(own), request_key="cached-hit", request_hash="e" * 64,
        extraction_run_id=run_id)
    pg.db.execute(receipt.insert().values(**values))
    _rejects(pg, receipt.insert().values(**{**values, "id": uuid4()}),
        constraint="uq_fact_extraction_receipt_request")
    _rejects(pg, receipt.insert().values(**{**values, "id": uuid4(), "workspace_owner_id": peer.owner}),
        constraint="fk_fact_extraction_receipt_run")


@pytest.fixture
def extraction_service_pg():
    from app.database import Base
    engine = _local_test_engine()
    schema = "phase3_" + uuid4().hex
    tables = FACT_TABLES | {"organizations", "users", "business_entities", "source_items",
        "classification_results", "audit_logs", "integration_connections", "email_message_records",
        "source_proposals", "fact_extraction_runs", "fact_extraction_requests"}
    try:
        with engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            Base.metadata.create_all(connection, tables=[Base.metadata.tables[name] for name in tables])

        def session():
            db = Session(engine, expire_on_commit=False)
            db.execute(sa.text(f'SET search_path TO "{schema}"'))
            db.execute(sa.text("SET lock_timeout='10s'"))
            db.execute(sa.text("SET statement_timeout='20s'"))
            return db

        yield SimpleNamespace(engine=engine, schema=schema, session=session)
    finally:
        with engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def _source_workspace(pg):
    from app.core.models import Organization, User, BusinessEntity, SourceItem
    from app.modules.cwi.models import IntegrationConnection, EmailMessageRecord
    with pg.session() as db:
        organization = Organization(name="Synthetic extraction concurrency")
        db.add(organization); db.flush()
        user = User(organization_id=organization.id, email=uuid4().hex + "@example.invalid",
            full_name="Synthetic extractor", password_hash="not-used", role="MEMBER")
        db.add(user); db.flush()
        project = BusinessEntity(organization_id=organization.id, workspace_owner_id=user.id,
            entity_type="PROJECT", name="Synthetic project")
        connection = IntegrationConnection(organization_id=organization.id, user_id=user.id,
            provider="GOOGLE", service="GMAIL", external_account_id=uuid4().hex,
            account_email=user.email, access_token_encrypted=b"synthetic-only")
        db.add_all([project, connection]); db.flush()
        message_ids, raw_ids = [], []
        for index in range(2):
            source = SourceItem(organization_id=organization.id, created_by=user.id,
                source_type="EMAIL", title="UAT notice", content="UAT review is on 17 September 2026.",
                received_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
            db.add(source); db.flush()
            message = EmailMessageRecord(organization_id=organization.id,
                integration_connection_id=connection.id, source_item_id=source.id,
                gmail_message_id=f"message-{index}", gmail_thread_id=f"thread-{index}",
                sender="synthetic@example.invalid", subject=source.title,
                content_hash=str(index) * 64, received_at=source.received_at)
            db.add(message); db.flush()
            message_ids.append(message.id); raw_ids.append(source.id)
        result = SimpleNamespace(owner=user.id, org=organization.id, project=project.id,
            messages=message_ids, sources=raw_ids)
        db.commit()
        return result


def _configure_extraction(monkeypatch, provider):
    from app.core.services import fact_extraction_service as service
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(
        mode="DEVELOPMENT", fact_memory_enabled=True, ai_provider="mock", llm_model="mock", llm_api_key=""))
    monkeypatch.setattr(service, "get_ai_provider", lambda settings: provider)
    return service


def _parallel_extractions(pg, workspace, requests, service):
    from app.core.models import User
    from fastapi import HTTPException
    barrier = Barrier(2)

    def generate(request):
        with pg.session() as db:
            user = db.get(User, workspace.owner)
            barrier.wait(timeout=10)
            try:
                run = service.generate(db, user, "email", request["source_id"],
                    project_id=workspace.project, request_key=request["request_key"])
                result = ("ok", run.id, run.status)
                db.commit()
                return result
            except HTTPException as error:
                db.rollback()
                return ("error", error.status_code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(generate, request) for request in requests]
        return [future.result(timeout=30) for future in futures]


def test_concurrent_extraction_reuses_paid_output_and_records_cache_request_receipts(extraction_service_pg, monkeypatch):
    from app.core.fact_extraction_models import FactExtractionRun, FactExtractionRequest
    from app.core.fact_models import FactProposal
    from app.core.services.fact_extraction_contract import mock_fact_bundle
    pg = extraction_service_pg
    workspace = _source_workspace(pg)
    counter, lock = [], Lock()

    class Provider:
        def prepare_fact_bundle(self, content, title, context):
            with lock:
                counter.append(True)
            return mock_fact_bundle(content, title, context)

    service = _configure_extraction(monkeypatch, Provider())
    requests = [dict(source_id=workspace.messages[0], request_key=f"extract-{index}") for index in range(2)]
    results = _parallel_extractions(pg, workspace, requests, service)
    assert all(item[0] == "ok" and item[2] == "SUCCEEDED" for item in results)
    assert results[0][1] == results[1][1] and len(counter) == 1
    with pg.session() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(FactExtractionRun)) == 1
        assert db.scalar(sa.select(sa.func.count()).select_from(FactExtractionRequest)) == 2
        assert db.scalar(sa.select(sa.func.count()).select_from(FactProposal)) == 1


def test_same_request_key_different_source_is_serialized_and_rejected(extraction_service_pg, monkeypatch):
    from app.core.fact_extraction_models import FactExtractionRun
    from app.core.services.fact_extraction_contract import mock_fact_bundle
    pg = extraction_service_pg
    workspace = _source_workspace(pg)
    calls = []

    class Provider:
        def prepare_fact_bundle(self, content, title, context):
            calls.append(True)
            return mock_fact_bundle(content, title, context)

    service = _configure_extraction(monkeypatch, Provider())
    requests = [dict(source_id=source, request_key="one-key-two-sources") for source in workspace.messages]
    results = _parallel_extractions(pg, workspace, requests, service)
    assert sorted(item[0] for item in results) == ["error", "ok"]
    assert next(item[1] for item in results if item[0] == "error") == 409
    assert len(calls) == 1
    with pg.session() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(FactExtractionRun)) == 1


def test_source_mutated_during_generation_cannot_publish_old_fact(extraction_service_pg, monkeypatch):
    from app.core.models import SourceItem, User
    from app.core.fact_models import FactProposal
    from app.core.services.fact_extraction_contract import mock_fact_bundle
    pg = extraction_service_pg
    workspace = _source_workspace(pg)

    class Provider:
        def prepare_fact_bundle(self, content, title, context):
            with pg.session() as other:
                row = other.get(SourceItem, workspace.sources[0])
                row.content = "This UAT notice was withdrawn."
                other.commit()
            return mock_fact_bundle(content, title, context)

    service = _configure_extraction(monkeypatch, Provider())
    with pg.session() as db:
        run = service.generate(db, db.get(User, workspace.owner), "email", workspace.messages[0],
            project_id=workspace.project, request_key="source-changed")
        assert run.status == "FAILED" and run.error_code == "SOURCE_CHANGED"
        assert db.scalar(sa.select(sa.func.count()).select_from(FactProposal)) == 0
        db.commit()


def test_fact_purge_scrubs_extraction_cache_and_candidate_context(db_session, seeded_user):
    from app.core.fact_extraction_models import FactExtractionRun
    from app.core.services.fact_lifecycle import purge_fact_origin
    from tests.test_fact_lifecycle import _graph

    user = seeded_user["user"]
    _, identity, snapshot, _, _, proposal, _ = _graph(db_session, user)
    proposal.extraction_context = {"subject": "private source-derived subject", "previous_value_date": "2026-09-16"}
    run = FactExtractionRun(organization_id=user.organization_id, workspace_owner_id=user.id,
        project_id=identity.project_id, source_kind=snapshot.source_kind, source_id=snapshot.source_id,
        origin_key=snapshot.origin_key, source_fingerprint=snapshot.source_fingerprint,
        cache_key="d" * 64, request_key="privacy-run", request_hash="e" * 64, generation=1, status="SUCCEEDED",
        extractor_version="v1", provider="mock", model="mock", coverage_json={"copied_text": "Private"},
        output_json={"summary": "private summary", "facts": [{"evidence": "Private"}]},
        proposal_ids=[str(proposal.id)])
    db_session.add(run)
    db_session.flush()
    purge_fact_origin(db_session, org_id=user.organization_id, owner_id=user.id, snapshot_id=snapshot.id)
    assert run.output_json is None and run.coverage_json == {} and run.redacted_at is not None
    assert proposal.extraction_context == {} and proposal.value_json is None
    assert run.proposal_ids == [str(proposal.id)]  # content-free result references survive


def test_zero_fact_run_project_delete_returns_conflict(client, db_session, seeded_user):
    from app.core.models import BusinessEntity
    from app.core.fact_extraction_models import FactExtractionRun
    from app.dependencies import get_current_user

    user = seeded_user["user"]
    project = BusinessEntity(organization_id=user.organization_id, workspace_owner_id=user.id,
        entity_type="PROJECT", name="Summary-only project")
    db_session.add(project)
    db_session.flush()
    db_session.add(FactExtractionRun(organization_id=user.organization_id, workspace_owner_id=user.id,
        project_id=project.id, source_kind="email", source_id=uuid4(), origin_key="a" * 64,
        source_fingerprint="b" * 64, cache_key="c" * 64, request_key="zero-facts", request_hash="e" * 64,
        status="SUCCEEDED", extractor_version="v1", provider="mock", model="mock",
        output_json={"summary": "General context", "facts": []}))
    db_session.flush()
    client.app.dependency_overrides[get_current_user] = lambda: user
    response = client.delete(f"/api/business-entities/{project.id}")
    assert response.status_code == 409 and "extraction history" in response.json()["detail"]
    assert db_session.get(BusinessEntity, project.id) is not None
