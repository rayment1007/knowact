"""Phase 2 HTTP gate, personal reads and review request validation."""
from uuid import uuid4

import pytest

from app.config import Settings, get_settings
from app.core.fact_models import FactEvidence, FactIdentity, FactProposal, FactSourceSnapshot, FactVersion
from app.core.models import BusinessEntity, SourceItem, User
from app.dependencies import get_current_user


@pytest.fixture()
def facts_client(client, seeded_user):
    client.app.dependency_overrides[get_current_user] = lambda: seeded_user["user"]
    client.app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, fact_memory_enabled=True, mode="DEVELOPMENT")
    return client


def _graph(db, user, label):
    scope = dict(organization_id=user.organization_id, workspace_owner_id=user.id)
    project = BusinessEntity(**scope, entity_type="PROJECT", name=label)
    db.add(project); db.flush()
    identity = FactIdentity(**scope, project_id=project.id, fact_type="MEETING_TIME", canonical_key="uat")
    snapshot = FactSourceSnapshot(**scope, source_kind="source", source_id=uuid4(),
        origin_key="a" * 64, source_fingerprint="b" * 64, source_title=label + " source", available=True)
    db.add_all([identity, snapshot]); db.flush()
    evidence = FactEvidence(**scope, snapshot_id=snapshot.id, excerpt=label + " exact quote", excerpt_hash="c" * 64)
    version = FactVersion(**scope, identity_id=identity.id, version_number=1,
        value_json={"date": "2026-09-17", "precision": "DATE"}, approved_by=user.id)
    db.add_all([evidence, version]); db.flush()
    proposal = FactProposal(**scope, identity_id=identity.id, evidence_id=evidence.id,
        value_json={"date": "2026-09-18", "precision": "DATE"}, expected_head_id=version.id)
    db.add(proposal); db.flush()
    return project, identity, proposal, version


def test_fact_routes_disabled_by_default_and_always_in_production(facts_client):
    for settings in [Settings(_env_file=None), Settings(_env_file=None, mode="PRODUCTION", fact_memory_enabled=True)]:
        facts_client.app.dependency_overrides[get_settings] = lambda: settings
        assert facts_client.get("/api/dev/fact-memory/proposals").status_code == 404
        assert facts_client.post(f"/api/dev/fact-memory/proposals/{uuid4()}/review", json={}).status_code == 404


def test_enabled_fact_routes_require_session(client):
    client.app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, fact_memory_enabled=True)
    assert client.get("/api/dev/fact-memory/proposals").status_code == 401


def test_fact_reads_are_owner_scoped_and_paginated(facts_client, db_session, seeded_user):
    user = seeded_user["user"]
    other = User(organization_id=user.organization_id, email="private-facts@example.com", full_name="Other", password_hash="unused", role="MEMBER")
    db_session.add(other); db_session.flush()
    _, own_identity, own_proposal, _ = _graph(db_session, user, "Own")
    _, identity, proposal, version = _graph(db_session, other, "Private")
    prefix = "/api/dev/fact-memory"
    result = facts_client.get(prefix + "/proposals?limit=1").json()
    assert result["total"] == 1
    assert result["items"][0]["id"] == str(own_proposal.id)
    assert facts_client.get(prefix + "/proposals?offset=1").json()["items"] == []
    detail = facts_client.get(f"{prefix}/proposals/{own_proposal.id}").json()
    assert detail["evidence"]["excerpt"] == "Own exact quote"
    assert detail["current"]["version_number"] == 1
    assert "request_hash" not in str(detail)
    for path in [f"proposals/{proposal.id}", f"proposals/{proposal.id}/reviews",
                 f"identities/{identity.id}/current", f"identities/{identity.id}/history",
                 f"versions/{version.id}/evidence"]:
        response = facts_client.get(prefix + "/" + path)
        assert response.status_code == 404, response.text
        assert "Private" not in response.text
    foreign_review = facts_client.post(f"{prefix}/proposals/{proposal.id}/review", json={
        "expected_revision": 1, "expected_head_id": str(version.id),
        "expected_source_fingerprint": "b" * 64, "decision": "REJECT", "request_key": "foreign-review"})
    assert foreign_review.status_code == 404
    foreign_edit = facts_client.patch(f"{prefix}/proposals/{proposal.id}", json={
        "expected_revision": 1, "value_date": "2026-09-18", "excerpt": "2026-09-18", "request_key": "foreign-edit"})
    assert foreign_edit.status_code == 404
    history = facts_client.get(f"{prefix}/identities/{own_identity.id}/history").json()
    assert history["total"] == 1
    assert facts_client.get(prefix + "/proposals?limit=101").status_code == 422
    assert facts_client.get(prefix + "/proposals?offset=-1").status_code == 422


def test_review_payload_cannot_supply_relationship_or_omit_head(facts_client):
    url = f"/api/dev/fact-memory/proposals/{uuid4()}/review"
    body = dict(expected_revision=1, expected_source_fingerprint="b" * 64, decision="APPROVE", request_key="review-1")
    assert facts_client.post(url, json=body).status_code == 422
    body.update(expected_head_id=None, relationship="REVISION")
    assert facts_client.post(url, json=body).status_code == 422
    # No creation or classification endpoint accepts caller-controlled facts.
    assert facts_client.post("/api/dev/fact-memory/proposals", json={}).status_code == 405


def test_project_with_fact_history_cannot_be_deleted(facts_client, db_session, seeded_user):
    project, _, _, _ = _graph(db_session, seeded_user["user"], "History")
    response = facts_client.delete(f"/api/business-entities/{project.id}")
    assert response.status_code == 409, response.text
    assert db_session.get(BusinessEntity, project.id) is not None


def test_edit_and_approve_routes_bind_reviewed_revision_and_are_idempotent(facts_client, db_session, seeded_user):
    from app.core.services.fact_memory_service import FactMemoryService
    user = seeded_user["user"]
    project = BusinessEntity(organization_id=user.organization_id, workspace_owner_id=user.id,
        entity_type="PROJECT", name="UAT")
    quote = "UAT moved from 2026-09-17 to 2026-09-18."
    source = SourceItem(organization_id=user.organization_id, created_by=user.id,
        source_type="MANUAL", title="UAT date", content=quote)
    db_session.add_all([project, source]); db_session.flush()
    service = FactMemoryService(db_session, owner_id=user.id, org_id=user.organization_id)
    proposal = service.propose(project_id=project.id, canonical_key="uat", source_kind="source", source_id=source.id,
        value_date="2026-09-17", excerpt=quote)
    service.record_comparison(proposal.id, revision=1, expected_head_id=None, relationship="NEW")
    prefix = f"/api/dev/fact-memory/proposals/{proposal.id}"
    edited = facts_client.patch(prefix, json={"expected_revision": proposal.revision, "value_date": "2026-09-18",
        "excerpt": quote, "request_key": "edit-reviewed-date"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["decision"] == "EDIT"
    detail = facts_client.get(prefix).json()
    assert detail["proposal"]["revision"] == 3
    assert detail["proposal"]["comparison_ready"] is False
    body = {"expected_revision": detail["proposal"]["revision"], "expected_head_id": None,
        "expected_source_fingerprint": detail["evidence"]["source"]["source_fingerprint"],
        "decision": "APPROVE", "request_key": "approve-reviewed-date"}
    assert facts_client.post(prefix + "/review", json=body).status_code == 409
    service.record_comparison(proposal.id, revision=proposal.revision, expected_head_id=None, relationship="NEW")
    body["expected_revision"] = proposal.revision
    approved = facts_client.post(prefix + "/review", json=body)
    assert approved.status_code == 200, approved.text
    retry = facts_client.post(prefix + "/review", json=body)
    assert retry.status_code == 200, retry.text
    assert retry.json()["id"] == approved.json()["id"]
    assert facts_client.get(prefix).json()["current"]["value_json"]["date"] == "2026-09-18"
    assert facts_client.get(prefix + "/reviews").json()["total"] == 2
