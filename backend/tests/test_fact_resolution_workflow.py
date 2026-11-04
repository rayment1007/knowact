"""Selected-source → explicit identity → review through the real HTTP routes."""
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.core.fact_models import FactProposal, FactReview, FactVersion
from app.core.models import KnowledgeItem
from app.core.services.fact_extraction_contract import AtomicDateCandidate, FactBundleOutput
from tests.test_fact_extraction_workflow import workflow

PREFIX = "/api/dev/fact-memory"


def extract(w, text, day):
    _, message = w.email(text)
    message.received_at = datetime(2026, 9, day, tzinfo=timezone.utc)
    w.db.flush()
    result = w.request("email", message.id)
    assert result.status_code == 200, result.text
    bundle = result.json()
    candidate_id = bundle["fact_extraction"]["candidates"][0]["proposal_id"]
    detail = w.client.get(PREFIX + f"/proposals/{candidate_id}").json()
    return bundle, detail


def resolve(w, detail, identity_id=None):
    body = {"expected_revision": detail["proposal"]["revision"],
        "expected_source_fingerprint": detail["evidence"]["source"]["source_fingerprint"],
        "project_id": str(w.project.id), "request_key": uuid4().hex,
        **({"identity_id": identity_id} if identity_id else {"canonical_key": "UAT review", "occurrence_key": "release 1"})}
    response = w.client.post(PREFIX + f"/proposals/{detail['proposal']['id']}/resolve", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def review(w, detail, decision="APPROVE", **changes):
    return w.client.post(PREFIX + f"/proposals/{detail['proposal']['id']}/review", json={
        "expected_revision": detail["proposal"]["revision"],
        "expected_head_id": detail["current"]["id"] if detail["current"] else None,
        "expected_source_fingerprint": detail["evidence"]["source"]["source_fingerprint"],
        "decision": decision, "request_key": uuid4().hex, **changes})


def test_visible_mvp_new_revision_conflict_history_and_source_status(workflow):
    w = workflow
    bundle, original = extract(w, "UAT review is on 17 September 2026.", 1)
    first = resolve(w, original)
    assert first["proposal"]["relationship"] == "NEW" and first["can_approve"]
    assert review(w, first).status_code == 200
    refreshed = w.client.get(f"/api/workspace/proposals/{bundle['id']}").json()
    candidate = refreshed["fact_extraction"]["candidates"][0]
    assert candidate["proposal_status"] == "PENDING"  # immutable candidate lineage
    assert candidate["resolved_status"] == "APPROVED"
    assert candidate["active_resolution_id"] == first["proposal"]["id"]
    assert w.db.scalar(select(func.count()).select_from(KnowledgeItem)) == 0

    _, change = extract(w, "UAT review was rescheduled from 17 September 2026 to 18 September 2026.", 2)
    second = resolve(w, change, first["identity"]["id"])
    assert second["proposal"]["relationship"] == "REVISION"
    assert second["current"]["value_json"]["date"] == "2026-09-17"
    assert second["current_evidence"][0]["excerpt"] == "UAT review is on 17 September 2026."
    assert review(w, second).status_code == 200

    _, change = extract(w, "UAT review is on 20 September 2026.", 3)
    conflict = resolve(w, change, first["identity"]["id"])
    assert conflict["proposal"]["relationship"] == "CONFLICT" and conflict["needs_resolution_reason"]
    assert review(w, conflict).status_code == 409
    assert review(w, conflict, "KEEP_UNRESOLVED").status_code == 200
    assert review(w, conflict, resolution_reason="A stale page must not approve.").status_code == 409
    current = w.client.get(PREFIX + "/memory?view=current").json()
    assert current["total"] == 1 and current["items"][0]["value_date"] == "2026-09-18"
    history = w.client.get(PREFIX + f"/identities/{first['identity']['id']}/history").json()
    assert [(v["value_json"]["date"], v["status"]) for v in history["items"]] == [
        ("2026-09-18", "CURRENT"), ("2026-09-17", "SUPERSEDED")]
    assert history["items"][0]["predecessor_id"] == history["items"][1]["id"]
    summary = w.client.get("/api/workspace/summary").json()
    assert summary["fact_reviews"] == summary["fact_unresolved"] == 1
    assert summary["fact_current"] == 1


def test_same_source_contradiction_can_be_resolved_by_rejecting_mistaken_child(workflow):
    w = workflow
    text = "UAT review is on 17 September 2026. UAT review is on 18 September 2026."
    facts = [AtomicDateCandidate(fact_type="MEETING_TIME", subject="UAT review", occurrence=None,
        value_date=f"2026-09-{day}", original_value=f"{day} September 2026", modality="ASSERTION",
        previous_value_date=None, change_cue=None, evidence_text=f"UAT review is on {day} September 2026.",
        source_context="DIRECT") for day in (17, 18)]
    w.provider.prepare_fact_bundle.return_value = FactBundleOutput(summary="Two dates were stated.",
        key_points=[], evidence_text=text, facts=facts)
    bundle, original = extract(w, text, 1)
    first = resolve(w, original)
    assert first["proposal"]["relationship"] == "UNCLEAR" and not first["can_approve"]
    sibling_id = bundle["fact_extraction"]["candidates"][1]["proposal_id"]
    sibling = resolve(w, w.client.get(PREFIX + f"/proposals/{sibling_id}").json(), first["identity"]["id"])
    assert review(w, sibling, "REJECT").status_code == 200
    response = w.client.post(PREFIX + f"/proposals/{first['proposal']['id']}/compare", json={
        "expected_revision": first["proposal"]["revision"], "expected_head_id": None,
        "expected_source_fingerprint": first["evidence"]["source"]["source_fingerprint"], "request_key": uuid4().hex})
    assert response.status_code == 200, response.text
    fresh = response.json()
    assert fresh["proposal"]["relationship"] == "NEW" and fresh["can_approve"]
    assert review(w, fresh).status_code == 200
    assert w.db.scalar(select(func.count()).select_from(FactVersion)) == 1


def test_source_card_shows_approved_child_value_without_rewriting_candidate(workflow):
    w = workflow
    text = "UAT review was rescheduled from 17 September 2026 to 18 September 2026."
    bundle, original = extract(w, text, 1)
    edited = w.client.patch(PREFIX + f"/proposals/{original['proposal']['id']}", json={
        "expected_revision": original["proposal"]["revision"], "value_date": "2026-09-18",
        "excerpt": text, "request_key": uuid4().hex})
    assert edited.status_code == 200, edited.text
    original = w.client.get(PREFIX + f"/proposals/{original['proposal']['id']}").json()
    # Simulate a corrected evidence passage inside the independent review.
    child = resolve(w, original)
    quote = "18 September 2026"
    changed = w.client.patch(PREFIX + f"/proposals/{child['proposal']['id']}", json={
        "expected_revision": child["proposal"]["revision"], "value_date": "2026-09-18",
        "excerpt": quote, "request_key": uuid4().hex})
    assert changed.status_code == 200, changed.text
    child = w.client.get(PREFIX + f"/proposals/{child['proposal']['id']}").json()
    response = w.client.post(PREFIX + f"/proposals/{child['proposal']['id']}/compare", json={
        "expected_revision": child["proposal"]["revision"], "expected_head_id": None,
        "expected_source_fingerprint": child["evidence"]["source"]["source_fingerprint"], "request_key": uuid4().hex})
    assert response.status_code == 200, response.text
    assert review(w, response.json()).status_code == 200
    refreshed = w.client.get(f"/api/workspace/proposals/{bundle['id']}").json()["fact_extraction"]["candidates"][0]
    assert refreshed["resolved_status"] == "APPROVED" and refreshed["evidence_text"] == quote
    assert "EDIT_REQUIRES_REVALIDATION" not in refreshed["reason_codes"]
    original_row = w.db.get(FactProposal, UUID(original["proposal"]["id"]))
    assert original_row.evidence_id == UUID(original["evidence"]["id"])
    assert refreshed["proposal_id"] == str(original_row.id)
