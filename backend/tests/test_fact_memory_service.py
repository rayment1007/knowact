"""Controlled M1 transactions; these are not AI extraction accuracy results."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.fact_models import (
    FactEvidence, FactEvidenceLink, FactIdentity, FactProposal, FactReview,
    FactSourceSnapshot, FactVersion,
)
from app.core.models import AuditLog, BusinessEntity, KnowledgeItem, SourceItem, User
from app.core.services.audit_service import AuditService
from app.core.services.fact_memory_service import FactMemoryService
from app.modules.cwi.models import EmailMessageRecord, IntegrationConnection


@pytest.fixture
def memory(db_session, seeded_user):
    db, user = db_session, seeded_user["user"]
    project = BusinessEntity(organization_id=user.organization_id, workspace_owner_id=user.id,
        name="Project A", entity_type="PROJECT")
    connection = IntegrationConnection(organization_id=user.organization_id, user_id=user.id,
        provider="GOOGLE", service="GMAIL", status="CONNECTED", account_email=user.email,
        external_account_id="synthetic-account", granted_scopes_json=[], access_token_encrypted=b"test-only")
    db.add_all([project, connection])
    db.flush()
    service = FactMemoryService(db, owner_id=user.id, org_id=user.organization_id)

    def email(text, day=1, provider_id=None):
        timestamp = datetime(2026, 9, day, 12, tzinfo=timezone.utc)
        source = SourceItem(organization_id=user.organization_id, created_by=user.id,
            source_type="EMAIL", title="UAT review", content=text, received_at=timestamp)
        db.add(source)
        db.flush()
        message = EmailMessageRecord(organization_id=user.organization_id,
            integration_connection_id=connection.id, source_item_id=source.id,
            gmail_message_id=provider_id or uuid4().hex, gmail_thread_id=uuid4().hex,
            sender="coordinator@example.test", subject="UAT review", received_at=timestamp,
            content_hash=uuid4().hex * 2)
        db.add(message)
        db.flush()
        return source, message

    def proposal(text, value="2026-09-17", day=1, relationship="NEW", **kwargs):
        source, message = email(text, day)
        item = service.propose(project_id=kwargs.pop("project_id", project.id),
            canonical_key=kwargs.pop("canonical_key", "uat-review"),
            occurrence_key=kwargs.pop("occurrence_key", "release-1"),
            source_kind="email", source_id=message.id, value_date=value, excerpt=text, **kwargs)
        if relationship:
            service.record_comparison(item.id, revision=item.revision,
                expected_head_id=item.expected_head_id, relationship=relationship)
        return item, source, message

    def request(item, **kwargs):
        snapshot = db.get(FactSourceSnapshot, db.get(FactEvidence, item.evidence_id).snapshot_id)
        return dict(expected_revision=item.revision, expected_head_id=item.expected_head_id,
            expected_source_fingerprint=snapshot.source_fingerprint,
            decision=kwargs.pop("decision", "APPROVE"), request_key=kwargs.pop("request_key", uuid4().hex), **kwargs)

    return SimpleNamespace(db=db, user=user, project=project, connection=connection,
        service=service, email=email, proposal=proposal, request=request)


def _count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def _approve(m, proposal):
    return m.service.review(proposal.id, **m.request(proposal))


def _baseline(m):
    proposal, _, _ = m.proposal("The UAT review is on 17 September 2026.")
    review = _approve(m, proposal)
    return m.db.get(FactVersion, review.result_version_id)


def test_m1_email_revision_appends_immutable_history_without_summary_or_action_side_effect(memory):
    m = memory
    before_summary_count = _count(m.db, KnowledgeItem)
    first = _baseline(m)
    proposal, _, _ = m.proposal(
        "The UAT review was rescheduled from 17 September 2026 to 18 September 2026.",
        value="2026-09-18", day=2, relationship="REVISION")
    assert first.status == "CURRENT" and _count(m.db, FactVersion) == 1
    review = _approve(m, proposal)
    versions = list(m.db.scalars(select(FactVersion).order_by(FactVersion.version_number)))
    assert [(v.value_json["date"], v.status) for v in versions] == [
        ("2026-09-17", "SUPERSEDED"), ("2026-09-18", "CURRENT")]
    assert versions[1].predecessor_id == first.id
    assert review.result_version_id == versions[1].id
    assert _count(m.db, FactReview) == _count(m.db, AuditLog) == 2
    assert _count(m.db, KnowledgeItem) == before_summary_count
    assert _count(m.db, FactEvidenceLink) == 2


def test_same_links_new_evidence_without_version_and_deduplicates_original(memory):
    m = memory
    first = _baseline(m)
    proposal, _, message = m.proposal("Confirmed: the UAT review is on 17 September 2026.",
        day=2, relationship="SAME")
    review = _approve(m, proposal)
    assert review.result_version_id == first.id and _count(m.db, FactVersion) == 1
    assert _count(m.db, FactEvidenceLink) == 2
    repeated = m.service.propose(project_id=m.project.id, canonical_key="uat-review", occurrence_key="release-1",
        source_kind="email", source_id=message.id, value_date="2026-09-17",
        excerpt="Confirmed: the UAT review is on 17 September 2026.")
    m.service.record_comparison(repeated.id, revision=repeated.revision,
        expected_head_id=first.id, relationship="SAME")
    _approve(m, repeated)
    assert _count(m.db, FactEvidenceLink) == 2 and _count(m.db, FactVersion) == 1


def test_retries_are_idempotent_and_request_key_reuse_is_rejected(memory):
    m = memory
    proposal, _, _ = m.proposal("The UAT review is on 17 September 2026.")
    request = m.request(proposal, request_key="double-click")
    first = m.service.review(proposal.id, **request)
    assert m.service.review(proposal.id, **request).id == first.id
    assert _count(m.db, FactReview) == _count(m.db, FactVersion) == 1
    with pytest.raises(HTTPException) as caught:
        m.service.review(proposal.id, **{**request, "decision": "REJECT"})
    assert caught.value.status_code == 409


@pytest.mark.parametrize("decision,status", [("REJECT", "REJECTED"), ("KEEP_UNRESOLVED", "UNRESOLVED")])
def test_reject_and_unresolved_do_not_change_current(memory, decision, status):
    m = memory
    first = _baseline(m)
    proposal, _, _ = m.proposal("The UAT review is on 18 September 2026.",
        value="2026-09-18", day=2, relationship="CONFLICT")
    m.service.review(proposal.id, **m.request(proposal, decision=decision))
    assert proposal.status == status
    assert first.status == "CURRENT" and first.value_json["date"] == "2026-09-17"
    assert _count(m.db, FactVersion) == 1


def test_edit_preserves_payload_history_invalidates_comparison_and_requires_fresh_review(memory):
    m = memory
    first = _baseline(m)
    text = "The UAT review was rescheduled from 17 September 2026 to 18 September 2026. Alternative: 19 September 2026."
    proposal, _, _ = m.proposal(text, value="2026-09-18", day=2, relationship="REVISION")
    old_request = m.request(proposal)
    edit_args = dict(expected_revision=proposal.revision, value_date="2026-09-19",
        excerpt="Alternative: 19 September 2026.", request_key="edit-once")
    review = m.service.edit(proposal.id, **edit_args)
    assert m.service.edit(proposal.id, **edit_args).id == review.id
    assert review.detail["before"]["date"] == "2026-09-18"
    assert review.detail["after"]["date"] == "2026-09-19"
    assert not proposal.comparison_ready and proposal.relationship == "UNCLEAR"
    with pytest.raises(HTTPException, match="edited"):
        m.service.review(proposal.id, **old_request)
    with pytest.raises(HTTPException, match="Compare"):
        _approve(m, proposal)
    assert first.status == "CURRENT" and _count(m.db, FactVersion) == 1


@pytest.mark.parametrize("change", ["content", "delete", "title"])
def test_approval_rejects_source_changes_or_missing_source(memory, change):
    m = memory
    proposal, source, message = m.proposal("The UAT review is on 17 September 2026.")
    if change == "content":
        source.content += " This date is no longer correct."
    elif change == "title":
        message.subject = "Retracted schedule"
    else:
        m.db.delete(message)
        m.db.flush()
        m.db.delete(source)
    m.db.flush()
    with pytest.raises(HTTPException) as caught:
        _approve(m, proposal)
    assert caught.value.status_code == 409
    assert _count(m.db, FactVersion) == _count(m.db, FactReview) == 0


def test_stale_head_and_fake_source_revision_cannot_be_approved(memory):
    m = memory
    first = _baseline(m)
    text = "The UAT review was rescheduled from 17 September 2026 to 18 September 2026."
    p1, _, _ = m.proposal(text, value="2026-09-18", day=2, relationship="REVISION")
    p2, _, _ = m.proposal(text.replace("18 September", "20 September"),
        value="2026-09-20", day=3, relationship="REVISION")
    _approve(m, p2)
    with pytest.raises(HTTPException, match="accepted fact changed"):
        _approve(m, p1)
    assert first.status == "SUPERSEDED" and _count(m.db, FactVersion) == 2
    same, _, _ = m.proposal("The UAT review is on 20 September 2026.",
        value="2026-09-20", day=4, relationship="SAME")
    with pytest.raises(HTTPException, match="source revision is stale"):
        m.service.review(same.id, **{**m.request(same), "expected_source_fingerprint": "0" * 64})


@pytest.mark.parametrize("text,day", [
    ("The UAT review is on 18 September 2026.", 2),
    ("The UAT review might be rescheduled from 17 September 2026 to 18 September 2026.", 2),
    ("The UAT review was not rescheduled from 17 September 2026 to 18 September 2026.", 2),
    ("The UAT review was rescheduled from 17 September 2026 to 18 September 2026.", 1),
    ("The UAT review was rescheduled from 16 September 2026 to 18 September 2026.", 2),
])
def test_unsupported_replacement_is_not_ready_for_approval(memory, text, day):
    m = memory
    first = _baseline(m)
    proposal, _, _ = m.proposal(text, value="2026-09-18", day=day, relationship=None)
    with pytest.raises(HTTPException) as caught:
        m.service.record_comparison(proposal.id, revision=proposal.revision,
            expected_head_id=first.id, relationship="REVISION")
    assert caught.value.status_code == 409
    assert not proposal.comparison_ready and _count(m.db, FactVersion) == 1


@pytest.mark.parametrize("relationship", ["CONFLICT", "UNCLEAR"])
def test_conflict_and_unclear_do_not_allow_approval(memory, relationship):
    m = memory
    proposal, _, _ = m.proposal("The UAT review is on 17 September 2026.", relationship=relationship)
    with pytest.raises(HTTPException, match="further resolution"):
        _approve(m, proposal)
    assert _count(m.db, FactVersion) == 0


def test_wrong_owner_cannot_propose_or_review_and_projects_are_not_merged(memory):
    m = memory
    proposal, _, message = m.proposal("The UAT review is on 17 September 2026.")
    other = User(organization_id=m.user.organization_id, email="other@example.test",
        full_name="Other", password_hash="test-only", role="MEMBER")
    m.db.add(other)
    m.db.flush()
    foreign = FactMemoryService(m.db, owner_id=other.id, org_id=other.organization_id)
    with pytest.raises(HTTPException) as caught:
        foreign.review(proposal.id, **m.request(proposal))
    assert caught.value.status_code == 404
    with pytest.raises(HTTPException) as caught:
        foreign.propose(project_id=m.project.id, canonical_key="uat-review", source_kind="email",
            source_id=message.id, value_date="2026-09-17", excerpt="The UAT review is on 17 September 2026.")
    assert caught.value.status_code == 404
    project = BusinessEntity(organization_id=m.user.organization_id, workspace_owner_id=m.user.id,
        name="Project A", entity_type="PROJECT")
    m.db.add(project)
    m.db.flush()
    another, _, _ = m.proposal("The UAT review is on 17 September 2026.", project_id=project.id)
    assert another.identity_id != proposal.identity_id


@pytest.mark.parametrize("value,quote", [
    ("17 Sep", "The UAT review is on 17 September 2026."),
    ("2026-02-30", "The UAT review is on 30 February 2026."),
    ("2026-09-17", "An invented quote on 17 September 2026."),
    ("2026-09-18", "The UAT review is on 17 September 2026."),
])
def test_incomplete_or_ungrounded_candidate_creates_no_partial_identity(memory, value, quote):
    m = memory
    _, message = m.email("The UAT review is on 17 September 2026.")
    with pytest.raises(HTTPException) as caught:
        m.service.propose(project_id=m.project.id, canonical_key="uat-review", source_kind="email",
            source_id=message.id, value_date=value, excerpt=quote)
    assert caught.value.status_code == 422
    assert _count(m.db, FactIdentity) == _count(m.db, FactEvidence) == _count(m.db, FactProposal) == 0


def test_audit_failure_rolls_back_every_part_of_approved_revision(memory, monkeypatch):
    m = memory
    first = _baseline(m)
    proposal, _, _ = m.proposal(
        "The UAT review was rescheduled from 17 September 2026 to 18 September 2026.",
        value="2026-09-18", day=2, relationship="REVISION")

    def fail(*args, **kwargs):
        raise RuntimeError("simulated audit storage failure")

    monkeypatch.setattr(AuditService, "record", fail)
    with pytest.raises(RuntimeError, match="simulated audit"):
        _approve(m, proposal)
    m.db.refresh(first)
    m.db.refresh(proposal)
    assert first.status == "CURRENT" and proposal.status == "PENDING"
    assert _count(m.db, FactVersion) == _count(m.db, FactEvidenceLink) == _count(m.db, FactReview) == 1


def test_defer_invalidates_another_tabs_approve(memory):
    m = memory
    proposal, _, _ = m.proposal("The UAT review is on 17 September 2026.")
    old_request = m.request(proposal)
    m.service.review(proposal.id, **m.request(proposal, decision="KEEP_UNRESOLVED"))
    with pytest.raises(HTTPException, match="edited"):
        m.service.review(proposal.id, **old_request)
    assert _count(m.db, FactVersion) == 0


def test_normalized_identity_length_is_validated_before_database_write(memory):
    m = memory
    _, message = m.email("The UAT review is on 17 September 2026.")
    with pytest.raises(HTTPException) as caught:
        m.service.propose(project_id=m.project.id, canonical_key="ß" * 160,
            source_kind="email", source_id=message.id, value_date="2026-09-17",
            excerpt="The UAT review is on 17 September 2026.")
    assert caught.value.status_code == 422 and _count(m.db, FactIdentity) == 0


def test_removed_email_reimport_needs_explicit_review_but_does_not_duplicate_same_evidence(memory):
    from app.core.services.fact_lifecycle import mark_fact_sources_removed
    m = memory
    text = "The UAT review is on 17 September 2026."
    proposal, source, email = m.proposal(text)
    first = _approve(m, proposal)
    provider_id = email.gmail_message_id
    mark_fact_sources_removed(m.db, org_id=m.user.organization_id, owner_id=m.user.id,
        source_kind="email", source_ids=[email.id])
    old_snapshot = m.db.get(FactSourceSnapshot, m.db.get(FactEvidence, proposal.evidence_id).snapshot_id)
    m.db.delete(email)
    m.db.flush()
    m.db.delete(source)
    m.db.flush()
    _, fresh_email = m.email(text, provider_id=provider_id)
    assert _count(m.db, FactProposal) == 1  # importing alone never proposes
    fresh = m.service.propose(project_id=m.project.id, canonical_key="uat-review", occurrence_key="release-1",
        source_kind="email", source_id=fresh_email.id, value_date="2026-09-17", excerpt=text)
    m.service.record_comparison(fresh.id, revision=fresh.revision,
        expected_head_id=first.result_version_id, relationship="SAME")
    _approve(m, fresh)
    assert not old_snapshot.available
    assert _count(m.db, FactVersion) == _count(m.db, FactEvidenceLink) == 1
