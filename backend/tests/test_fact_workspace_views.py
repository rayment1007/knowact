"""M1 review queue/read API: one visible candidate, scoped evidence and history."""
from uuid import uuid4

import pytest

from app.config import Settings, get_settings
from app.core.fact_models import (
    FactEvidenceLink, FactIdentity, FactProposal, FactResolution, FactReview,
)
from app.core.models import User
from app.core.services import fact_extraction_service
from tests.test_fact_memory_routes import facts_client, _graph


PREFIX = "/api/dev/fact-memory"


def _candidate(db, user, project, evidence_id):
    scope = dict(organization_id=user.organization_id, workspace_owner_id=user.id)
    identity = FactIdentity(**scope, project_id=project.id, fact_type="MEETING_TIME",
        canonical_key="candidate:" + uuid4().hex)
    db.add(identity); db.flush()
    item = FactProposal(**scope, identity_id=identity.id, evidence_id=evidence_id,
        value_json={"date": "2026-09-18", "precision": "DATE"},
        extraction_context={"identity_unverified": True, "subject": "UAT meeting"})
    db.add(item); db.flush()
    return item


def _lineage(db, user, candidate, resolved):
    item = FactResolution(organization_id=user.organization_id, workspace_owner_id=user.id,
        candidate_id=candidate.id, candidate_revision=candidate.revision,
        resolved_proposal_id=resolved.id, request_key=uuid4().hex, request_hash="e" * 64)
    db.add(item); db.flush()
    return item


def _other(db, user):
    peer = User(organization_id=user.organization_id, email=f"{uuid4().hex}@private.test",
        full_name="Private owner", password_hash="unused", role="MEMBER")
    db.add(peer); db.flush()
    return peer


def test_memory_counts_filters_and_identity_choices_are_owned_and_paged(facts_client, db_session, seeded_user):
    db, user = db_session, seeded_user["user"]
    project, identity, pending, head = _graph(db, user, "Own 100% project")
    peer_project, peer_identity, peer_pending, peer_head = _graph(db, _other(db, user), "Private project")
    candidate = _candidate(db, user, project, pending.evidence_id)
    pending.relationship = "CONFLICT"
    pending.comparison_ready = True
    db.flush()

    result = facts_client.get(PREFIX + "/memory?limit=1").json()
    assert result["total"] == 2 and len(result["items"]) == 1
    second = facts_client.get(PREFIX + "/memory?limit=1&offset=1").json()
    assert {row["id"] for row in result["items"]}.isdisjoint(row["id"] for row in second["items"])
    assert facts_client.get(PREFIX + "/memory?relationship=CONFLICT").json()["items"][0]["id"] == str(pending.id)
    assert facts_client.get(PREFIX + "/memory?q=%25").json()["total"] == 2
    assert facts_client.get(PREFIX + "/memory?q=Private").json()["total"] == 0
    assert facts_client.get(PREFIX + "/counts").json() == {"needs_review": 2, "unresolved": 1, "current": 1, "history": 1}
    assert facts_client.get(PREFIX + "/memory?view=current").json()["items"][0]["id"] == str(head.id)
    choices = facts_client.get(PREFIX + f"/identities?project_id={project.id}").json()
    assert choices["total"] == 1 and choices["items"][0]["id"] == str(identity.id)
    assert choices["items"][0]["current"]["value_json"]["date"] == "2026-09-17"
    for suffix in [f"/memory?project_id={peer_project.id}", f"/counts?project_id={peer_project.id}",
                   f"/identities?project_id={peer_project.id}", f"/versions/{peer_head.id}"]:
        response = facts_client.get(PREFIX + suffix)
        assert response.status_code == 404 and "Private" not in response.text
    for suffix in ["/memory?limit=101", "/memory?offset=-1", "/memory?view=all", "/identities"]:
        assert facts_client.get(PREFIX + suffix).status_code == 422


def test_active_resolution_is_single_queue_entry_and_inactive_child_stays_hidden(facts_client, db_session, seeded_user):
    db, user = db_session, seeded_user["user"]
    project, identity, resolved, _ = _graph(db, user, "UAT")
    candidate = _candidate(db, user, project, resolved.evidence_id)
    lineage = _lineage(db, user, candidate, resolved)
    page = facts_client.get(PREFIX + "/memory").json()
    assert page["total"] == 1 and page["items"][0]["id"] == str(resolved.id)
    original = facts_client.get(PREFIX + f"/proposals/{candidate.id}").json()
    assert original["active_resolution_id"] == str(resolved.id)
    detail = facts_client.get(PREFIX + f"/proposals/{resolved.id}").json()
    assert detail["lineage"]["candidate_id"] == str(candidate.id)
    assert "request_hash" not in str(detail)
    lineage.active = False
    db.flush()
    page = facts_client.get(PREFIX + "/memory").json()
    assert page["total"] == 1 and page["items"][0]["id"] == str(candidate.id)
    assert facts_client.get(PREFIX + f"/proposals/{resolved.id}").json()["can_approve"] is False


def test_detail_eligibility_tracks_lineage_head_and_source_availability(facts_client, db_session, seeded_user):
    from app.core.fact_models import FactEvidence, FactSourceSnapshot
    db, user = db_session, seeded_user["user"]
    project, _, resolved, head = _graph(db, user, "UAT")
    candidate = _candidate(db, user, project, resolved.evidence_id)
    lineage = _lineage(db, user, candidate, resolved)
    resolved.relationship = "CONFLICT"
    resolved.comparison_ready = True
    resolved.comparison_json = {"can_approve": True, "needs_resolution_reason": True,
        "reasons": ["Two dates for this meeting."], "warnings": [], "rule_version": "m1-test"}
    db.flush()
    url = PREFIX + f"/proposals/{resolved.id}"
    detail = facts_client.get(url).json()
    assert detail["can_approve"] and detail["needs_resolution_reason"]
    candidate.revision += 1
    db.flush()
    assert facts_client.get(url).json()["can_approve"] is False
    # A fresh lineage can still be blocked by an unavailable original.
    candidate.revision -= 1
    evidence = db.get(FactEvidence, resolved.evidence_id)
    snapshot = db.get(FactSourceSnapshot, evidence.snapshot_id)
    snapshot.available = False
    db.flush()
    assert facts_client.get(url).json()["can_approve"] is False


def test_version_detail_has_evidence_and_review_without_internal_receipt_hash(facts_client, db_session, seeded_user):
    db, user = db_session, seeded_user["user"]
    _, identity, item, head = _graph(db, user, "History")
    item.status, item.result_version_id = "APPROVED", head.id
    scope = dict(organization_id=user.organization_id, workspace_owner_id=user.id)
    db.add(FactEvidenceLink(**scope, identity_id=identity.id, version_id=head.id,
        evidence_id=item.evidence_id, evidence_key="a" * 64))
    db.add(FactReview(**scope, identity_id=identity.id, proposal_id=item.id,
        proposal_revision=1, decision="APPROVE", request_key="private-receipt", request_hash="f" * 64,
        result_version_id=head.id, detail={"relationship": "NEW"}))
    db.flush()
    detail = facts_client.get(PREFIX + f"/versions/{head.id}").json()
    assert detail["evidence_total"] == 1 and detail["reviews_total"] == 1
    assert detail["evidence"][0]["excerpt"] == "History exact quote"
    assert detail["version"]["approved_by_name"] == user.full_name
    assert detail["reviews"][0]["decision"] == "APPROVE"
    assert detail["reviews"][0]["reviewer_name"] == user.full_name
    assert "request_hash" not in str(detail) and "private-receipt" not in str(detail)
    assert facts_client.get(PREFIX + "/memory").json()["total"] == 0
    assert facts_client.get(PREFIX + "/memory?view=history").json()["total"] == 1


def test_new_mutation_payloads_cannot_classify_or_skip_bound_revision(facts_client):
    resolve = {"expected_revision": 1, "expected_source_fingerprint": "a" * 64,
        "project_id": str(uuid4()), "canonical_key": "uat", "request_key": "resolve"}
    endpoint = PREFIX + f"/proposals/{uuid4()}"
    assert facts_client.post(endpoint + "/resolve", json={**resolve, "relationship": "NEW"}).status_code == 422
    compare = {"expected_revision": 1, "expected_source_fingerprint": "a" * 64, "request_key": "compare"}
    assert facts_client.post(endpoint + "/compare", json=compare).status_code == 422
    assert facts_client.post(endpoint + "/compare", json={**compare, "expected_head_id": None,
        "relationship": "REVISION"}).status_code == 422


def test_workspace_aggregates_count_active_resolution_once_and_hide_flag_off(facts_client, db_session, seeded_user, monkeypatch):
    db, user = db_session, seeded_user["user"]
    project, _, resolved, head = _graph(db, user, "UAT")
    candidate = _candidate(db, user, project, resolved.evidence_id)
    _lineage(db, user, candidate, resolved)
    _graph(db, _other(db, user), "Private")
    resolved.relationship, resolved.comparison_ready = "CONFLICT", True
    db.flush()
    monkeypatch.setattr(fact_extraction_service, "enabled", lambda: True)
    for endpoint in ["/api/workspace/counts", "/api/workspace/summary"]:
        data = facts_client.get(endpoint).json()
        assert data["fact_reviews"] == 1 and data["fact_unresolved"] == 1 and data["fact_current"] == 1
    resolved.status, resolved.result_version_id = "APPROVED", head.id
    db.flush()
    data = facts_client.get("/api/workspace/summary").json()
    assert data["fact_reviews"] == data["fact_unresolved"] == 0
    monkeypatch.setattr(fact_extraction_service, "enabled", lambda: False)
    data = facts_client.get("/api/workspace/counts").json()
    assert data["fact_reviews"] == data["fact_unresolved"] == data["fact_current"] == 0


def test_new_read_and_mutation_routes_stay_development_only(facts_client):
    for settings in [Settings(_env_file=None), Settings(_env_file=None, mode="PRODUCTION", fact_memory_enabled=True)]:
        facts_client.app.dependency_overrides[get_settings] = lambda: settings
        for suffix in ["/counts", "/memory", f"/identities?project_id={uuid4()}", f"/versions/{uuid4()}"]:
            assert facts_client.get(PREFIX + suffix).status_code == 404
        for suffix in ["resolve", "compare"]:
            assert facts_client.post(PREFIX + f"/proposals/{uuid4()}/{suffix}", json={}).status_code == 404


def test_version_review_history_is_paged_and_private(facts_client, db_session, seeded_user):
    db, user = db_session, seeded_user["user"]
    _, identity, item, head = _graph(db, user, "History pages")
    _, _, _, foreign_head = _graph(db, _other(db, user), "Private history")
    for index in range(27):
        db.add(FactReview(organization_id=user.organization_id, workspace_owner_id=user.id,
            identity_id=identity.id, proposal_id=item.id, proposal_revision=index + 1,
            decision="APPROVE", request_key=f"history-{index}", request_hash="f" * 64,
            result_version_id=head.id, detail={"relationship": "SAME"}))
    db.flush()
    first = facts_client.get(PREFIX + f"/versions/{head.id}/reviews").json()
    second = facts_client.get(PREFIX + f"/versions/{head.id}/reviews?offset=25").json()
    assert first["total"] == second["total"] == 27
    assert len(first["items"]) == 25 and len(second["items"]) == 2
    assert {row["id"] for row in first["items"]}.isdisjoint(row["id"] for row in second["items"])
    assert facts_client.get(PREFIX + f"/versions/{foreign_head.id}/reviews").status_code == 404
