"""Selected-source extraction integration with offline providers only.

Assertions verify control flow/grounding, not live model semantic accuracy.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.config import get_settings
from app.core.fact_extraction_models import FactExtractionRun
from app.core.fact_models import FactIdentity, FactProposal, FactVersion
from app.core.models import BusinessEntity, ClassificationResult, KnowledgeItem, SourceItem, User
from app.core.services.ai_provider import AIProviderError, MockAIProvider
from app.core.services.fact_extraction_contract import AtomicDateCandidate, FactBundleOutput
from app.core.services.fact_memory_service import FactMemoryService
from app.core.services import fact_extraction_service as service
from app.modules.cwi.models import DocumentChunk, EmailMessageRecord, IntegrationConnection, SourceProposal
from tests.test_cwi_calendar_routes import _login
from tests.test_source_proposals import calendar_source, file_source


TEXT = "The UAT review was rescheduled from 17 September 2026 to 18 September 2026."


def _count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def _output(text=TEXT, **candidate_changes):
    candidate = dict(fact_type="MEETING_TIME", subject="UAT review", occurrence=None,
        value_date="2026-09-18", original_value="18 September 2026", modality="ASSERTION",
        previous_value_date="2026-09-17", change_cue="rescheduled from",
        evidence_text=text, source_context="DIRECT")
    candidate.update(candidate_changes)
    return FactBundleOutput(summary="The UAT review date changed.", key_points=["Review the new date."],
        evidence_text=text, facts=[AtomicDateCandidate(**candidate)])


@pytest.fixture
def workflow(client, db_session, seeded_user, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "fact_memory_enabled", True)
    monkeypatch.setattr(settings, "mode", "DEVELOPMENT")
    _login(client, seeded_user)
    user = seeded_user["user"]
    project = BusinessEntity(organization_id=user.organization_id, workspace_owner_id=user.id,
        entity_type="PROJECT", name="Project A")
    db_session.add(project); db_session.flush()
    provider = Mock(wraps=MockAIProvider())
    monkeypatch.setattr(service, "get_ai_provider", lambda settings: provider)

    def source(text=TEXT, owner=user):
        row = SourceItem(organization_id=owner.organization_id, created_by=owner.id,
            source_type="MANUAL", title="UAT review", content=text,
            received_at=datetime(2026, 9, 2, tzinfo=timezone.utc))
        db_session.add(row); db_session.flush(); return row

    def email(text=TEXT, *, provider_id=None):
        raw = source(text)
        connection = IntegrationConnection(organization_id=user.organization_id, user_id=user.id,
            provider="GOOGLE", service="GMAIL", external_account_id=uuid4().hex,
            access_token_encrypted=b"offline-test-only", account_email=user.email, status="CONNECTED")
        db_session.add(connection); db_session.flush()
        message = EmailMessageRecord(organization_id=user.organization_id,
            integration_connection_id=connection.id, source_item_id=raw.id,
            gmail_message_id=provider_id or uuid4().hex, gmail_thread_id="thread",
            sender="reviewer@example.test", subject="UAT review", content_hash=uuid4().hex * 2,
            received_at=datetime(2026, 9, 2, tzinfo=timezone.utc))
        db_session.add(message); db_session.flush()
        return raw, message

    def request(kind, source_id, *, request_key=None, **changes):
        payload = dict(target="knowledge", extract_facts=True, project_id=str(project.id),
            request_key=request_key or uuid4().hex)
        payload.update(changes)
        return client.post(f"/api/workspace/source/{kind}/{source_id}/proposals", json=payload)

    return SimpleNamespace(client=client, db=db_session, user=user, project=project,
        source=source, email=email, request=request, provider=provider)


def test_selected_email_creates_pending_candidates_and_summary_approval_confirms_context_only(workflow):
    w = workflow
    _, message = w.email()
    response = w.request("email", message.id)
    assert response.status_code == 200, response.text
    draft = response.json()
    extraction = draft["fact_extraction"]
    assert draft["status"] == "SUGGESTED" and extraction["status"] == "SUCCEEDED"
    assert extraction["project_id"] == str(w.project.id)
    assert extraction["coverage"]["partial"] is False
    candidate = extraction["candidates"][0]
    proposal = w.db.get(FactProposal, UUID(candidate["proposal_id"]))
    assert candidate["value_date"] == "2026-09-18" and candidate["proposal_status"] == "PENDING"
    assert proposal.comparison_ready is False and proposal.relationship == "UNCLEAR"
    assert proposal.extraction_context["identity_unverified"] is True
    assert _count(w.db, FactVersion) == _count(w.db, KnowledgeItem) == 0
    approved = w.client.post(f"/api/workspace/proposals/{draft['id']}/approve",
        json={"version": draft["version"], "payload": draft["payload"]})
    assert approved.status_code == 200, approved.text
    knowledge = w.db.scalar(select(KnowledgeItem))
    assert knowledge.knowledge_type == "CONTEXT" and knowledge.business_entity_id == w.project.id
    assert _count(w.db, FactVersion) == 0 and proposal.status == "PENDING"
    w.provider.prepare_fact_bundle.assert_called_once()


def test_zero_facts_is_successful_context_not_provider_failure(workflow):
    w = workflow
    raw = w.source("The team discussed concerns about API integration.")
    response = w.request("source", raw.id)
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["payload"]["summary"] and draft["fact_extraction"]["candidates"] == []
    assert _count(w.db, FactProposal) == _count(w.db, FactVersion) == 0
    assert w.db.scalar(select(FactExtractionRun.status)) == "SUCCEEDED"


@pytest.mark.parametrize("failure", ["provider", "quote", "invented-year", "unsupported-type"])
def test_failed_generation_records_failure_without_partial_facts_or_summary(workflow, failure):
    w = workflow
    raw = w.source()
    if failure == "provider":
        w.provider.prepare_fact_bundle.side_effect = AIProviderError("offline upstream failure")
    elif failure == "quote":
        w.provider.prepare_fact_bundle.return_value = _output(evidence_text="An invented date on 18 September 2026.")
    elif failure == "invented-year":
        raw.content = "UAT review moved from 17 September to 18 September."
        w.db.flush()
        w.provider.prepare_fact_bundle.return_value = _output(text=raw.content,
            original_value="18 September", previous_value_date=None)
    else:
        output = _output().model_dump()
        output["facts"][0]["fact_type"] = "DEADLINE"
        w.provider.prepare_fact_bundle.return_value = output
    response = w.request("source", raw.id)
    assert response.status_code == 503, response.text
    run = w.db.scalar(select(FactExtractionRun))
    assert run.status == "FAILED" and run.error_code in {"AI_UNAVAILABLE", "INVALID_AI_OUTPUT"}
    assert run.output_json is None and run.proposal_ids == []
    assert _count(w.db, FactIdentity) == _count(w.db, FactProposal) == _count(w.db, FactVersion) == 0
    assert _count(w.db, SourceProposal) == _count(w.db, KnowledgeItem) == 0


@pytest.mark.parametrize("mutation", ["change", "remove"])
def test_source_changed_or_removed_during_provider_cannot_publish_old_output(workflow, mutation):
    w = workflow
    raw = w.source()
    def generate(*args):
        if mutation == "change":
            raw.content += " The rescheduling announcement was retracted."
        else:
            w.db.delete(raw)
        w.db.flush()
        return _output()
    w.provider.prepare_fact_bundle.side_effect = generate
    response = w.request("source", raw.id)
    assert response.status_code == 503, response.text
    assert w.db.scalar(select(FactExtractionRun.error_code)) == (
        "SOURCE_CHANGED" if mutation == "change" else "SOURCE_UNAVAILABLE")
    assert _count(w.db, FactProposal) == _count(w.db, SourceProposal) == 0


def test_owner_project_and_sensitive_checks_happen_before_provider(workflow):
    w = workflow
    other = User(organization_id=w.user.organization_id, email="foreign-extraction@example.test",
        full_name="Other", password_hash="unused", role="MEMBER")
    w.db.add(other); w.db.flush()
    foreign_source = w.source(owner=other)
    foreign_project = BusinessEntity(organization_id=other.organization_id, workspace_owner_id=other.id,
        entity_type="PROJECT", name="Project A")
    w.db.add(foreign_project); w.db.flush()
    raw = w.source()
    assert w.request("source", foreign_source.id).status_code == 404
    assert w.request("source", raw.id, project_id=str(foreign_project.id)).status_code == 404
    assert w.request("source", raw.id, project_id=None).status_code == 422
    classification = ClassificationResult(source_item_id=raw.id, relevance="WORK_RELATED", business_category="PROJECT",
        sensitivity="HIGHLY_SENSITIVE", confidence=.9, status="CONFIRMED")
    w.db.add(classification); w.db.flush()
    response = w.request("source", raw.id)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "SENSITIVE_ACK_REQUIRED"
    w.provider.prepare_fact_bundle.assert_not_called()
    assert w.request("source", raw.id, acknowledge_sensitive=True).status_code == 200


def test_same_request_cache_and_bounded_regeneration_do_not_repeat_unlimited_paid_calls(workflow):
    w = workflow
    raw = w.source()
    first = w.request("source", raw.id, request_key="repeat-once")
    assert first.status_code == 200, first.text
    assert w.request("source", raw.id, request_key="repeat-once").json()["id"] == first.json()["id"]
    assert w.request("source", raw.id).json()["id"] == first.json()["id"]
    assert w.provider.prepare_fact_bundle.call_count == 1
    for generation in (2, 3):
        response = w.request("source", raw.id, regenerate=True)
        assert response.status_code == 200, response.text
        assert response.json()["fact_extraction"]["generation"] == generation
    assert w.request("source", raw.id, regenerate=True).status_code == 429
    assert w.provider.prepare_fact_bundle.call_count == 3
    assert _count(w.db, FactVersion) == 0


def test_reimport_reuses_model_output_with_fresh_pending_proposal_ids(workflow):
    from app.modules.cwi.services.privacy_service import PrivacyService
    w = workflow
    _, message = w.email(provider_id="same-provider-message")
    first = w.request("email", message.id)
    assert first.status_code == 200, first.text
    first_candidate = first.json()["fact_extraction"]["candidates"][0]
    PrivacyService(w.db).delete_email_data(w.user.organization_id, w.user.id,
        scope="SELECTED", record_ids=[message.id])
    _, imported = w.email(provider_id="same-provider-message")
    second = w.request("email", imported.id)
    assert second.status_code == 200, second.text
    candidate = second.json()["fact_extraction"]["candidates"][0]
    assert second.json()["id"] != first.json()["id"]
    assert candidate["proposal_id"] != first_candidate["proposal_id"] and candidate["proposal_status"] == "PENDING"
    assert w.provider.prepare_fact_bundle.call_count == 1
    assert _count(w.db, FactVersion) == 0


def test_edit_is_visible_in_bundle_and_unverified_identity_cannot_be_approved(workflow):
    w = workflow
    raw = w.source()
    response = w.request("source", raw.id)
    assert response.status_code == 200, response.text
    draft = response.json()
    candidate = draft["fact_extraction"]["candidates"][0]
    proposal_id = UUID(candidate["proposal_id"])
    memory = FactMemoryService(w.db, owner_id=w.user.id, org_id=w.user.organization_id)
    with pytest.raises(HTTPException) as caught:
        memory.record_comparison(proposal_id, revision=candidate["proposal_revision"],
            expected_head_id=None, relationship="NEW")
    assert caught.value.status_code == 409
    edited = w.client.patch(f"/api/dev/fact-memory/proposals/{proposal_id}", json={
        "expected_revision": candidate["proposal_revision"], "value_date": "2026-09-17",
        "excerpt": TEXT, "request_key": "edit-candidate"})
    assert edited.status_code == 200, edited.text
    latest = w.client.get(f"/api/workspace/proposals/{draft['id']}").json()["fact_extraction"]["candidates"][0]
    assert latest["value_date"] == "2026-09-17" and latest["proposal_revision"] == candidate["proposal_revision"] + 1
    assert "EDIT_REQUIRES_REVALIDATION" in latest["reason_codes"]
    assert _count(w.db, FactVersion) == 0


def test_long_sources_report_partial_coverage_and_changes_after_limit_invalidate_summary_approval(workflow):
    w = workflow
    raw = w.source(TEXT + " " + "x " * 15000)
    response = w.request("source", raw.id)
    assert response.status_code == 200, response.text
    draft = response.json()
    extraction = draft["fact_extraction"]
    assert extraction["coverage"]["partial"] is True and extraction["warnings"]
    assert extraction["coverage"]["analyzed_characters"] <= 24000
    assert extraction["coverage"]["total_characters"] == len(raw.content)
    assert w.provider.prepare_fact_bundle.call_count == 3
    assert all(len(call.args[0]) <= 8000 for call in w.provider.prepare_fact_bundle.call_args_list)
    raw.content += " Changed outside the analyzed prefix."
    w.db.flush()
    approved = w.client.post(f"/api/workspace/proposals/{draft['id']}/approve",
        json={"version": draft["version"], "payload": draft["payload"]})
    assert approved.status_code == 409 and _count(w.db, KnowledgeItem) == 0


@pytest.mark.parametrize("kind", ["file", "calendar"])
def test_file_and_all_day_calendar_use_same_contract_without_approving_facts(workflow, kind):
    w = workflow
    if kind == "file":
        source = file_source(w.db, w.user)
        chunk = w.db.scalar(select(DocumentChunk).where(DocumentChunk.document_asset_id == source.id))
        chunk.text = TEXT
        w.db.flush()
    else:
        source = calendar_source(w.db, w.user)
        source.title, source.starts_at, source.ends_at = "UAT review", "2026-09-18", "2026-09-19"
        w.db.flush()
        def calendar_bundle(content, title, context):
            return FactBundleOutput(summary="UAT review is on 18 September 2026.", key_points=[], evidence_text=content,
                facts=[AtomicDateCandidate(**{
                    **_output().facts[0].model_dump(), "evidence_text": content,
                    "original_value": "2026-09-18", "previous_value_date": None, "change_cue": None})])
        w.provider.prepare_fact_bundle.side_effect = calendar_bundle
    response = w.request(kind, source.id)
    assert response.status_code == 200, response.text
    candidate = response.json()["fact_extraction"]["candidates"][0]
    assert candidate["value_date"] == "2026-09-18" and candidate["proposal_status"] == "PENDING"
    assert _count(w.db, FactVersion) == 0


@pytest.mark.parametrize("text,changes,reason", [
    ("Could the UAT review be on 18 September 2026?", {}, "ASSERTION_NOT_ESTABLISHED"),
    ("We suggest the UAT review on 18 September 2026.", {}, "ASSERTION_NOT_ESTABLISHED"),
    ("The UAT review is not on 18 September 2026.", {}, "NEGATED_OR_UNCERTAIN_STATEMENT"),
    ("Forwarded message\nThe UAT review is on 18 September 2026.", {}, "SOURCE_CONTEXT_REQUIRES_REVIEW"),
    ("> The UAT review is on 18 September 2026.", {}, "SOURCE_CONTEXT_REQUIRES_REVIEW"),
    ("The weekly UAT review is on 18 September 2026.", {}, "OCCURRENCE_REQUIRED"),
    ("The UAT review is next Friday.", {"value_date": None, "original_value": "next Friday"}, "COMPLETE_DATE_REQUIRED"),
])
def test_unresolved_qualifiers_are_visible_and_not_turned_into_ready_fact_proposals(workflow, text, changes, reason):
    w = workflow
    raw = w.source(text)
    w.provider.prepare_fact_bundle.return_value = _output(text=text,
        previous_value_date=None, change_cue=None, **changes)
    response = w.request("source", raw.id)
    assert response.status_code == 200, response.text
    candidate = response.json()["fact_extraction"]["candidates"][0]
    assert candidate["outcome"] == "NEEDS_CLARIFICATION" and reason in candidate["reason_codes"]
    assert candidate["proposal_id"] is None
    assert _count(w.db, FactProposal) == _count(w.db, FactVersion) == 0


def test_failed_request_receipt_is_replayable_but_new_request_can_retry_successfully(workflow):
    w = workflow
    raw = w.source()
    w.provider.prepare_fact_bundle.side_effect = AIProviderError("temporary offline failure")
    first = w.request("source", raw.id, request_key="failed-request")
    replay = w.request("source", raw.id, request_key="failed-request")
    assert first.status_code == replay.status_code == 503
    assert first.json()["detail"]["extraction_id"] == replay.json()["detail"]["extraction_id"]
    assert w.provider.prepare_fact_bundle.call_count == 1
    w.provider.prepare_fact_bundle.side_effect = None
    w.provider.prepare_fact_bundle.return_value = _output()
    recovered = w.request("source", raw.id, request_key="recovered-request")
    assert recovered.status_code == 200, recovered.text
    assert sorted(w.db.scalars(select(FactExtractionRun.status))) == ["FAILED", "SUCCEEDED"]
    assert _count(w.db, FactProposal) == _count(w.db, SourceProposal) == 1


def test_request_key_cannot_be_reused_for_a_different_project_and_capability_is_development_only(workflow, monkeypatch):
    w = workflow
    raw = w.source()
    first = w.request("source", raw.id, request_key="chosen-project")
    assert first.status_code == 200, first.text
    other_project = BusinessEntity(organization_id=w.user.organization_id, workspace_owner_id=w.user.id,
        entity_type="PROJECT", name="Project A")
    w.db.add(other_project); w.db.flush()
    assert w.request("source", raw.id, request_key="chosen-project", project_id=str(other_project.id)).status_code == 409
    assert w.provider.prepare_fact_bundle.call_count == 1
    monkeypatch.setattr(get_settings(), "mode", "PRODUCTION")
    assert w.client.get("/api/workspace/fact-extraction/capabilities").json()["enabled"] is False
    assert w.request("source", raw.id).status_code == 404


def test_time_specific_calendar_is_visible_as_unsupported_precision_not_date_only_fact(workflow):
    w = workflow
    calendar = calendar_source(w.db, w.user)
    calendar.title = "UAT review"
    calendar.starts_at, calendar.ends_at = "2026-09-18T14:00:00+08:00", "2026-09-18T15:00:00+08:00"
    w.db.flush()
    def output(content, title, context):
        return _output(text=content, original_value="2026-09-18",
            previous_value_date=None, change_cue=None)
    w.provider.prepare_fact_bundle.side_effect = output
    response = w.request("calendar", calendar.id)
    assert response.status_code == 200, response.text
    candidate = response.json()["fact_extraction"]["candidates"][0]
    assert candidate["outcome"] == "NEEDS_CLARIFICATION"
    assert "TIME_SPECIFIC_VALUE_NOT_SUPPORTED" in candidate["reason_codes"]
    assert candidate["proposal_id"] is None and _count(w.db, FactVersion) == 0


def test_request_receipt_for_cached_success_cannot_be_reused_for_another_project(workflow):
    from app.core.fact_extraction_models import FactExtractionRequest
    w = workflow
    raw = w.source()
    first = w.request("source", raw.id, request_key="original-generation")
    cached = w.request("source", raw.id, request_key="cached-request")
    assert first.status_code == cached.status_code == 200
    assert first.json()["id"] == cached.json()["id"]
    assert _count(w.db, FactExtractionRun) == 1 and _count(w.db, FactExtractionRequest) == 2
    other_project = BusinessEntity(organization_id=w.user.organization_id, workspace_owner_id=w.user.id,
        entity_type="PROJECT", name="Project B")
    w.db.add(other_project); w.db.flush()
    wrong = w.request("source", raw.id, request_key="cached-request", project_id=str(other_project.id))
    assert wrong.status_code == 409
    assert w.provider.prepare_fact_bundle.call_count == 1 and _count(w.db, FactExtractionRun) == 1


@pytest.mark.parametrize("replay", [False, True])
def test_cached_success_does_not_bypass_newly_excluded_source(workflow, replay):
    w = workflow
    raw = w.source()
    first = w.request("source", raw.id, request_key="before-exclusion")
    assert first.status_code == 200, first.text
    w.db.add(ClassificationResult(source_item_id=raw.id, relevance="PERSONAL", business_category="OTHER",
        sensitivity="PUBLIC", confidence=.99, status="CONFIRMED"))
    w.db.flush()
    excluded = w.request("source", raw.id, request_key="before-exclusion" if replay else "after-exclusion")
    assert excluded.status_code == 409
    assert "excluded" in excluded.text
    assert w.provider.prepare_fact_bundle.call_count == 1 and _count(w.db, FactExtractionRun) == 1


def test_mid_publication_failure_rolls_back_all_candidates_and_keeps_only_failure_receipt(workflow, monkeypatch):
    w = workflow
    second_text = "The kickoff meeting is on 19 September 2026."
    raw = w.source(TEXT + " " + second_text)
    output = _output()
    output.facts.append(AtomicDateCandidate(**{
        **output.facts[0].model_dump(), "subject": "kickoff meeting", "value_date": "2026-09-19",
        "original_value": "19 September 2026", "previous_value_date": None,
        "change_cue": None, "evidence_text": second_text}))
    w.provider.prepare_fact_bundle.return_value = output
    original_propose = FactMemoryService.propose
    calls = 0
    def fail_second_proposal(self, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise HTTPException(409, "Injected publication conflict")
        return original_propose(self, **kwargs)
    monkeypatch.setattr(FactMemoryService, "propose", fail_second_proposal)
    response = w.request("source", raw.id)
    assert response.status_code == 503, response.text
    assert calls == 2
    run = w.db.scalar(select(FactExtractionRun))
    assert run.status == "FAILED" and run.output_json is None and run.proposal_ids == []
    assert _count(w.db, FactProposal) == _count(w.db, FactIdentity) == _count(w.db, SourceProposal) == 0


def test_new_sensitive_classification_requires_and_retains_explicit_cache_acknowledgement(workflow):
    w = workflow
    raw = w.source()
    first = w.request("source", raw.id)
    assert first.status_code == 200, first.text
    w.db.add(ClassificationResult(source_item_id=raw.id, relevance="WORK_RELATED", business_category="OTHER",
        sensitivity="HIGHLY_SENSITIVE", confidence=.99, status="CONFIRMED"))
    w.db.flush()
    refused = w.request("source", raw.id)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "SENSITIVE_ACK_REQUIRED"
    acknowledged = w.request("source", raw.id, acknowledge_sensitive=True)
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["id"] == first.json()["id"] and w.provider.prepare_fact_bundle.call_count == 1
    draft = acknowledged.json()
    candidate = draft["fact_extraction"]["candidates"][0]
    edit = w.client.patch(f"/api/dev/fact-memory/proposals/{candidate['proposal_id']}", json={
        "expected_revision": candidate["proposal_revision"], "value_date": candidate["value_date"],
        "excerpt": candidate["evidence_text"], "request_key": uuid4().hex})
    assert edit.status_code == 200, edit.text
    approved = w.client.post(f"/api/workspace/proposals/{draft['id']}/approve",
        json={"version": draft["version"], "payload": draft["payload"]})
    assert approved.status_code == 200, approved.text
    assert _count(w.db, FactVersion) == 0
