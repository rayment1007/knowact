"""M1 rules and independently reviewed version transitions; offline only."""
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.fact_models import FactEvidence, FactEvidenceLink, FactIdentity, FactProposal, FactResolution, FactReview, FactSourceSnapshot, FactVersion
from app.core.models import ActionItem, BusinessEntity, KnowledgeItem, User
from app.core.services.fact_resolution_service import FactResolutionService
from app.core.services.fact_lifecycle import purge_fact_origin
from tests.test_fact_memory_service import memory
from tests.test_fact_extraction_workflow import workflow


def candidate(m, text="UAT review is on 17 September 2026.", value="2026-09-17", day=1, *, excerpt=None, **context):
    raw, message = m.email(text, day)
    item = m.service.propose(project_id=m.project.id, canonical_key="candidate:" + uuid4().hex,
        source_kind="email", source_id=message.id, value_date=value, excerpt=excerpt or text)
    item.extraction_context = {"identity_unverified": True, "requires_revalidation": False,
        "subject": "UAT review", "modality": "ASSERTION", "source_context": "DIRECT", **context}
    m.db.flush()
    return item, raw, message


def fingerprint(m, item):
    return m.db.get(FactSourceSnapshot, m.db.get(FactEvidence, item.evidence_id).snapshot_id).source_fingerprint


def resolve(m, item, **fields):
    payload = dict(expected_revision=item.revision, expected_source_fingerprint=fingerprint(m, item),
        project_id=m.project.id, canonical_key="UAT review", occurrence_key="release 1", request_key=uuid4().hex)
    payload.update(fields)
    return FactResolutionService(m.db, owner_id=m.user.id, org_id=m.user.organization_id).resolve(item.id, **payload)


def compare(m, item):
    service = FactResolutionService(m.db, owner_id=m.user.id, org_id=m.user.organization_id)
    head = service._head(item.identity_id)
    return service.compare(item.id, expected_revision=item.revision, expected_head_id=head.id if head else None,
        expected_source_fingerprint=fingerprint(m, item), request_key=uuid4().hex)


def approve(m, item, **fields):
    return m.service.review(item.id, **m.request(item, **fields))


def count(m, model):
    return m.db.scalar(select(func.count()).select_from(model))


def baseline(m):
    original, _, _ = candidate(m)
    resolved = resolve(m, original)
    assert resolved.relationship == "NEW"
    approve(m, resolved)
    return resolved


def test_new_same_revision_conflict_and_delayed_notice_preserve_memory_until_review(memory):
    m = memory
    first = baseline(m)
    same, _, _ = candidate(m, "UAT review is confirmed for 17 September 2026.", day=2)
    same = resolve(m, same)
    assert same.relationship == "SAME"
    approve(m, same)
    assert count(m, FactVersion) == 1 and count(m, FactEvidenceLink) == 2
    changed, _, _ = candidate(m, "UAT review was rescheduled from 17 September 2026 to 18 September 2026.", "2026-09-18", day=3)
    changed = resolve(m, changed)
    assert changed.relationship == "REVISION" and count(m, FactVersion) == 1
    approve(m, changed)
    assert m.db.get(FactVersion, first.result_version_id).status == "SUPERSEDED"
    conflicting, _, _ = candidate(m, "UAT review is on 20 September 2026.", "2026-09-20", day=4)
    conflicting = resolve(m, conflicting)
    assert conflicting.relationship == "CONFLICT"
    with pytest.raises(HTTPException) as error:
        approve(m, conflicting)
    assert error.value.status_code == 409 and count(m, FactVersion) == 2
    approve(m, conflicting, resolution_reason="I checked the coordinator's replacement date.")
    delayed, _, _ = candidate(m, "UAT review was rescheduled from 17 September 2026 to 18 September 2026.", "2026-09-18", day=5)
    delayed = resolve(m, delayed)
    assert delayed.relationship == "UNCLEAR" and "REPLACEMENT_BASE_MISMATCH" in delayed.comparison_json["reason_codes"]
    assert m.db.get(FactVersion, conflicting.result_version_id).value_json["date"] == "2026-09-20"
    assert count(m, KnowledgeItem) == count(m, ActionItem) == 0
    versions = list(m.db.scalars(select(FactVersion).order_by(FactVersion.version_number)))
    assert [v.status for v in versions] == ["SUPERSEDED", "SUPERSEDED", "CURRENT"]
    assert versions[2].predecessor_id == versions[1].id


def test_may_month_is_not_modal_and_older_or_unknown_time_requires_resolution(memory):
    m = memory
    first, _, _ = candidate(m, "UAT review is on May 17, 2026.", "2026-05-17", day=3)
    first = resolve(m, first)
    assert first.relationship == "NEW"
    approve(m, first)
    second, _, _ = candidate(m, "UAT review was moved from 17 May 2026 to 18 May 2026.", "2026-05-18", day=4)
    second = resolve(m, second)
    assert second.relationship == "REVISION"
    approve(m, second)
    older, _, _ = candidate(m, "UAT review is on 19 May 2026.", "2026-05-19", day=2)
    older = resolve(m, older)
    assert older.relationship == "UNCLEAR" and older.comparison_json["needs_resolution_reason"]
    with pytest.raises(HTTPException):
        approve(m, older)
    assert count(m, FactVersion) == 2


@pytest.mark.parametrize("text,code", [
    ("Could UAT review be on 18 September 2026?", "NON_ASSERTIVE_STATEMENT"),
    ("UAT review is NOT on 18 September 2026.", "NON_ASSERTIVE_STATEMENT"),
    ("UAT review on 18 September 2026 is cancelled.", "NON_ASSERTIVE_STATEMENT"),
    ("UAT review on 18 September 2026 remains unconfirmed.", "NON_ASSERTIVE_STATEMENT"),
    ("UAT review on 18 September 2026 is pending approval.", "NON_ASSERTIVE_STATEMENT"),
    ("UAT review is at 10:00 on 18 September 2026.", "TIME_PRECISION_UNSUPPORTED"),
    ("Backup meeting is on 18 September 2026.", "SUBJECT_NOT_IN_STATEMENT"),
    ("UAT review moved from 18 September 2026 to 20 September 2026.", "VALUE_IS_PREVIOUS_DATE"),
])
def test_trimming_evidence_to_bare_date_cannot_launder_context(memory, text, code):
    m = memory
    original, _, _ = candidate(m, "UAT review is on 17 September 2026. " + text)
    m.service.edit(original.id, expected_revision=original.revision, value_date="2026-09-18",
        excerpt="18 September 2026", request_key=uuid4().hex)
    item = resolve(m, original)
    assert item.relationship == "UNCLEAR" and code in item.comparison_json["reason_codes"]
    assert not item.comparison_json["can_approve"]
    with pytest.raises(HTTPException):
        approve(m, item, resolution_reason="A reason must not bypass unsupported evidence.")
    assert count(m, FactVersion) == 0


def test_candidate_edit_and_project_rematch_append_lineage_without_reparenting_history(memory):
    m = memory
    text = "UAT review is on 17 September 2026. UAT review is confirmed for 18 September 2026."
    original, _, _ = candidate(m, text)
    original_identity = original.identity_id
    m.service.edit(original.id, expected_revision=original.revision, value_date="2026-09-18",
        excerpt="UAT review is confirmed for 18 September 2026.", request_key="edit-before-resolve")
    first = resolve(m, original)
    project = BusinessEntity(organization_id=m.user.organization_id, workspace_owner_id=m.user.id,
        entity_type="PROJECT", name="Corrected project")
    m.db.add(project); m.db.flush()
    second = resolve(m, original, project_id=project.id)
    assert original.identity_id == original_identity and first.identity_id != second.identity_id
    assert m.db.get(FactIdentity, second.identity_id).project_id == project.id
    with pytest.raises(HTTPException):
        approve(m, first)
    assert count(m, FactReview) == 1
    approve(m, second)
    with pytest.raises(HTTPException):
        m.service.edit(original.id, expected_revision=original.revision, value_date="2026-09-17",
            excerpt=text, request_key="cannot-edit-approved-lineage")
    assert count(m, FactVersion) == 1


def test_replay_stale_source_candidate_and_head_are_checked(memory):
    m = memory
    original, raw, _ = candidate(m)
    args = dict(expected_revision=original.revision, expected_source_fingerprint=fingerprint(m, original),
        project_id=m.project.id, canonical_key="UAT review", occurrence_key="release 1", request_key="match-key")
    service = FactResolutionService(m.db, owner_id=m.user.id, org_id=m.user.organization_id)
    resolved = service.resolve(original.id, **args)
    assert service.resolve(original.id, **args).id == resolved.id
    with pytest.raises(HTTPException):
        service.resolve(original.id, **{**args, "canonical_key": "different meeting"})
    review_args = m.request(resolved, request_key="approve-once")
    result = m.service.review(resolved.id, **review_args)
    assert m.service.review(resolved.id, **review_args).id == result.id
    assert count(m, FactVersion) == 1
    fresh, raw2, _ = candidate(m, "UAT review is on 18 September 2026.", "2026-09-18", day=2)
    new = resolve(m, fresh)
    raw2.content += " Withdrawn."
    m.db.flush()
    with pytest.raises(HTTPException) as error:
        approve(m, new, resolution_reason="I choose this.")
    assert error.value.status_code == 409 and count(m, FactVersion) == 1


def test_candidate_edit_invalidates_child_and_defer_invalidates_old_tab(memory):
    m = memory
    original, _, _ = candidate(m)
    child = resolve(m, original)
    m.service.edit(original.id, expected_revision=original.revision, value_date="2026-09-17",
        excerpt="UAT review is on 17 September 2026.", request_key="edit")
    with pytest.raises(HTTPException):
        approve(m, child)
    fresh = resolve(m, original)
    old_review = m.request(fresh)
    approve(m, fresh, decision="KEEP_UNRESOLVED")
    with pytest.raises(HTTPException):
        m.service.review(fresh.id, **old_review)
    assert count(m, FactVersion) == 0


def test_two_new_proposals_recompare_when_competitor_or_head_changes(memory):
    m = memory
    first = resolve(m, candidate(m)[0])
    second = resolve(m, candidate(m, "UAT review is on 18 September 2026.", "2026-09-18", day=2)[0])
    assert second.relationship == "UNCLEAR"
    with pytest.raises(HTTPException):
        approve(m, first)
    compare(m, first)
    assert first.relationship == "UNCLEAR"
    approve(m, first, resolution_reason="The first notice is still authoritative.")
    with pytest.raises(HTTPException):
        approve(m, second, resolution_reason="Select second")
    compare(m, second)
    assert second.relationship == "CONFLICT" and count(m, FactVersion) == 1


def test_scope_guards_and_purge_redact_comparison_and_invalidate_lineage(memory):
    m = memory
    original, _, _ = candidate(m)
    peer = User(organization_id=m.user.organization_id, email="peer-facts@example.test",
        full_name="Peer", password_hash="unused", role="MEMBER")
    m.db.add(peer); m.db.flush()
    service = FactResolutionService(m.db, owner_id=peer.id, org_id=peer.organization_id)
    with pytest.raises(HTTPException) as error:
        service.resolve(original.id, expected_revision=1, expected_source_fingerprint=fingerprint(m, original),
            project_id=m.project.id, canonical_key="uat", request_key="peer")
    assert error.value.status_code == 404
    resolved = resolve(m, original)
    approve(m, resolved)
    snapshot_id = m.db.get(FactEvidence, resolved.evidence_id).snapshot_id
    purge_fact_origin(m.db, org_id=m.user.organization_id, owner_id=m.user.id, snapshot_id=snapshot_id)
    assert resolved.comparison_json == {} and resolved.value_json is None
    assert not m.db.scalar(select(FactResolution.active))
    assert m.db.get(FactVersion, resolved.result_version_id).redacted_at is not None


def test_actual_extraction_bundle_resolves_and_approves_fact_independently(workflow):
    w = workflow
    _, message = w.email("UAT review is on 17 September 2026.")
    response = w.request("email", message.id)
    assert response.status_code == 200, response.text
    bundle = response.json()
    original = w.db.get(FactProposal, UUID(bundle["fact_extraction"]["candidates"][0]["proposal_id"]))
    snapshot = w.db.get(FactSourceSnapshot, w.db.get(FactEvidence, original.evidence_id).snapshot_id)
    service = FactResolutionService(w.db, owner_id=w.user.id, org_id=w.user.organization_id)
    resolved = service.resolve(original.id, expected_revision=original.revision,
        expected_source_fingerprint=snapshot.source_fingerprint, project_id=w.project.id,
        canonical_key="UAT review", request_key="from-real-extraction")
    assert resolved.relationship == "NEW"
    service.review(resolved.id, expected_revision=resolved.revision, expected_head_id=None,
        expected_source_fingerprint=snapshot.source_fingerprint, decision="APPROVE", request_key="approve-fact")
    assert w.db.scalar(select(func.count()).select_from(FactVersion)) == 1
    assert w.db.scalar(select(func.count()).select_from(KnowledgeItem)) == 0

