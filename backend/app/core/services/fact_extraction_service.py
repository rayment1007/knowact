"""Bounded, selected-source M1 extraction. Generation never approves facts."""
from __future__ import annotations

import re
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from app.config import get_settings
from app.core.fact_extraction_models import FactExtractionRequest, FactExtractionRun
from app.core.fact_models import FactEvidence, FactProposal, FactSourceSnapshot
from app.core.models import BusinessEntity
from app.core.services.ai_provider import AIProviderError
from app.core.services.fact_extraction_contract import FactBundleOutput
from app.core.services.fact_lifecycle import lock_fact_origin
from app.core.services.fact_memory_service import FactMemoryService, _date_pattern, _date_value, _hash
from app.core.services.fact_sources import read_fact_source
from app.dependencies import get_ai_provider
from app.modules.cwi.models import SourceProposal

EXTRACTOR_VERSION = "m1-meeting-date-v1"
MAX_CHARACTERS = 24000
SEGMENT_CHARACTERS = 8000
MAX_SEGMENTS = 3
MAX_GENERATIONS = 3
MAX_FACTS = 20


class ExtractionOutputError(Exception):
    """Invalid generated output, distinct from valid zero-fact content."""


def enabled(settings=None):
    settings = settings or get_settings()
    return settings.mode == "DEVELOPMENT" and settings.fact_memory_enabled


def _scope(model, user):
    return select(model).where(model.organization_id == user.organization_id,
        model.workspace_owner_id == user.id)


def _segments(text):
    ranges, start = [], 0
    while start < min(len(text), MAX_CHARACTERS) and len(ranges) < MAX_SEGMENTS:
        end = min(start + SEGMENT_CHARACTERS, len(text), MAX_CHARACTERS)
        if end < len(text):
            boundary = max(text.rfind("\n", start + SEGMENT_CHARACTERS // 2, end),
                text.rfind(". ", start + SEGMENT_CHARACTERS // 2, end))
            if boundary >= 0:
                end = boundary + 1
        ranges.append({"start": start, "end": end})
        start = end
    return ranges


def _validate_output(output, segment, full_text, offset, kind):
    bundle = FactBundleOutput.model_validate(output.model_dump() if hasattr(output, "model_dump") else output)
    if not bundle.summary.strip() or len(bundle.summary) > 12000 or len(bundle.key_points) > 20:
        raise ExtractionOutputError()
    if any(not p.strip() or len(p) > 2000 for p in bundle.key_points):
        raise ExtractionOutputError()
    if not 1 <= len(bundle.evidence_text) <= 4000 or bundle.evidence_text not in segment or len(bundle.facts) > MAX_FACTS:
        raise ExtractionOutputError()
    candidates = []
    for fact in bundle.facts:
        item = fact.model_dump()
        if not fact.subject.strip() or len(fact.subject) > 160 or not fact.original_value.strip() or len(fact.original_value) > 160:
            raise ExtractionOutputError()
        if fact.occurrence is not None and len(fact.occurrence) > 160:
            raise ExtractionOutputError()
        quote = fact.evidence_text
        if not 1 <= len(quote) <= 4000 or quote not in segment or fact.original_value not in quote:
            raise ExtractionOutputError()
        reasons = []
        if fact.value_date is not None:
            try:
                value = _date_value(fact.value_date)
            except HTTPException:
                raise ExtractionOutputError() from None
            if re.fullmatch(_date_pattern(value), fact.original_value, re.I) is None:
                # A year elsewhere in the paragraph must not fill a missing
                # year in the actual source value (e.g. 'next Friday').
                raise ExtractionOutputError()
        else:
            reasons.append("COMPLETE_DATE_REQUIRED")
        if fact.previous_value_date is not None:
            try:
                previous = _date_value(fact.previous_value_date)
            except HTTPException:
                raise ExtractionOutputError() from None
            if re.search(_date_pattern(previous), quote, re.I) is None:
                raise ExtractionOutputError()
        if fact.change_cue is not None and (len(fact.change_cue) > 160 or fact.change_cue not in quote):
            raise ExtractionOutputError()
        position = offset + segment.index(quote)
        prefix = full_text[:position]
        if re.search(r"(?:forwarded message|begin forwarded|original message)", prefix + quote, re.I):
            item["source_context"] = "FORWARDED"
        elif quote.lstrip().startswith(">") or re.search(r"(?m)^On .+wrote:\s*$", prefix):
            item["source_context"] = "QUOTED"
        if item["source_context"] != "DIRECT":
            reasons.append("SOURCE_CONTEXT_REQUIRES_REVIEW")
        if "?" in quote:
            item["modality"] = "QUESTION"
        elif re.search(r"\b(?:not|never|cancelled|canceled|unconfirmed|uncertain)\b|no longer", quote, re.I):
            item["modality"] = "UNCERTAIN"
            reasons.append("NEGATED_OR_UNCERTAIN_STATEMENT")
        elif re.search(r"\b(?:might|could|should|would|propos\w*|suggest\w*|consider|pending)\b|\bmay\s+(?:be|move|change|reschedule)", quote, re.I):
            item["modality"] = "PROPOSED_CHANGE"
        if item["modality"] != "ASSERTION":
            reasons.append("ASSERTION_NOT_ESTABLISHED")
        if re.search(r"\b(?:weekly|recurring|every\s+\w+)\b", segment, re.I) and not fact.occurrence:
            reasons.append("OCCURRENCE_REQUIRED")
        if fact.occurrence and fact.occurrence.casefold() not in segment.casefold():
            reasons.append("OCCURRENCE_NOT_GROUNDED")
        if fact.subject.casefold() not in segment.casefold():
            reasons.append("SUBJECT_REQUIRES_CONFIRMATION")
        if kind == "calendar" and re.search(r"(?m)^Starts: \d{4}-\d{2}-\d{2}T", full_text):
            reasons.append("TIME_SPECIFIC_VALUE_NOT_SUPPORTED")
        item.update(outcome="NEEDS_CLARIFICATION" if reasons else "PROPOSED",
            reason_codes=list(dict.fromkeys(reasons)), span={"start": position, "end": position + len(quote)},
            proposal_id=None)
        candidates.append(item)
    return bundle, candidates


def _snapshot(db, user, source):
    snapshot = db.scalar(_scope(FactSourceSnapshot, user).where(
        FactSourceSnapshot.source_kind == source.kind, FactSourceSnapshot.source_id == source.source_id,
        FactSourceSnapshot.source_fingerprint == source.fingerprint))
    if snapshot is None:
        snapshot = FactSourceSnapshot(organization_id=user.organization_id, workspace_owner_id=user.id,
            source_kind=source.kind, source_id=source.source_id, origin_key=source.origin_key,
            source_fingerprint=source.fingerprint, source_title=source.title, source_time=source.source_time,
            available=True)
        db.add(snapshot)
        db.flush()
    elif not snapshot.available or snapshot.purged_at:
        raise HTTPException(409, "This local source revision was removed. Import it again before extracting.")
    return snapshot


def _failed(db, run, code):
    run.status, run.error_code, run.output_json, run.proposal_ids = "FAILED", code, None, []
    db.flush()
    return run


def _receipt(db, user, run, request_key, request_hash):
    db.add(FactExtractionRequest(organization_id=user.organization_id, workspace_owner_id=user.id,
        request_key=request_key, request_hash=request_hash, extraction_run_id=run.id))
    db.flush()
    return run


def _acknowledge_cached(db, user, run, acknowledged):
    """Carry explicit renewed consent to the unchanged cached content only."""
    if not acknowledged:
        return
    summary = db.scalar(select(SourceProposal).where(SourceProposal.id == run.source_proposal_id,
        SourceProposal.organization_id == user.organization_id, SourceProposal.user_id == user.id))
    if summary is not None:
        summary.sensitivity_acknowledged = True
    for proposal in db.scalars(_scope(FactProposal, user).where(
            FactProposal.id.in_([UUID(value) for value in run.proposal_ids]))):
        evidence = db.scalar(_scope(FactEvidence, user).where(FactEvidence.id == proposal.evidence_id))
        if evidence and not evidence.redacted_at:
            evidence.locator_json = {**evidence.locator_json, "sensitive_acknowledged": True}


def generate(db, user, kind, source_id, *, project_id, request_key, acknowledged=False, regenerate=False):
    settings = get_settings()
    if not enabled(settings):
        raise HTTPException(404, "Not found.")
    if not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 128:
        raise HTTPException(422, "A request key of 1 to 128 characters is required.")
    project = db.scalar(select(BusinessEntity).where(BusinessEntity.id == project_id,
        BusinessEntity.organization_id == user.organization_id, BusinessEntity.workspace_owner_id == user.id))
    if project is None:
        raise HTTPException(404, "Project not found.")
    if project.entity_type != "PROJECT":
        raise HTTPException(422, "Choose a project for structured fact extraction.")
    source = read_fact_source(db, user, kind, source_id, lock=False, acknowledge_sensitive=acknowledged)
    intent_hash = _hash([kind, source_id, project_id, regenerate, acknowledged])
    # Request keys are scoped independently of source origin. This also guards
    # accidental concurrent reuse of one key for two different sources.
    lock_fact_origin(db, org_id=user.organization_id, owner_id=user.id,
        origin_key=_hash(["extraction-request", request_key]))
    # One origin lock also prevents publication of an old content copy after
    # a concurrent fact-layer purge. Do not lock the raw source during AI calls.
    lock_fact_origin(db, org_id=user.organization_id, owner_id=user.id, origin_key=source.origin_key)
    # Recheck eligibility and full content after waiting for competing writers.
    current = read_fact_source(db, user, source.kind, source.source_id, lock=False,
        acknowledge_sensitive=acknowledged)
    if current.fingerprint != source.fingerprint or current.origin_key != source.origin_key:
        raise HTTPException(409, "The source changed while extraction was starting. Please retry.")
    receipt = db.scalar(_scope(FactExtractionRequest, user).where(FactExtractionRequest.request_key == request_key))
    if receipt:
        if receipt.request_hash != intent_hash:
            raise HTTPException(409, "This request key was used for a different extraction.")
        replay = db.scalar(_scope(FactExtractionRun, user).where(FactExtractionRun.id == receipt.extraction_run_id))
        if replay is None or replay.redacted_at:
            raise HTTPException(409, "This extraction's content was deleted.")
        if replay.source_fingerprint != current.fingerprint:
            raise HTTPException(409, "The source changed since this request. Start a new extraction.")
        if replay.status == "SUCCEEDED" and not db.scalar(select(SourceProposal.id).where(
                SourceProposal.id == replay.source_proposal_id, SourceProposal.organization_id == user.organization_id,
                SourceProposal.user_id == user.id)):
            raise HTTPException(409, "This extraction's summary was removed. Start a new extraction.")
        return replay
    provider_name = "openai" if settings.ai_provider == "llm" else "mock"
    model = settings.llm_model if settings.ai_provider == "llm" else "deterministic-m1"
    cache_key = _hash([user.organization_id, user.id, project_id, source.kind, source.source_id,
        source.origin_key, source.fingerprint, EXTRACTOR_VERSION, provider_name, model])
    previous = db.scalar(_scope(FactExtractionRun, user).where(FactExtractionRun.cache_key == cache_key)
        .order_by(FactExtractionRun.generation.desc()).limit(1))
    if not regenerate:
        cached_run = db.scalar(_scope(FactExtractionRun, user).where(
            FactExtractionRun.cache_key == cache_key, FactExtractionRun.status == "SUCCEEDED",
            FactExtractionRun.redacted_at.is_(None)).order_by(FactExtractionRun.generation.desc()).limit(1))
        if cached_run and db.scalar(select(SourceProposal.id).where(SourceProposal.id == cached_run.source_proposal_id,
                SourceProposal.organization_id == user.organization_id, SourceProposal.user_id == user.id)):
            _acknowledge_cached(db, user, cached_run, acknowledged)
            return _receipt(db, user, cached_run, request_key, intent_hash)
    generation = previous.generation + 1 if previous else 1
    # Bound all re-generation attempts for this owner/project/resource revision,
    # including reimports, so repeated clicks cannot create unlimited drafts.
    attempt_count = db.scalar(select(func.count()).select_from(FactExtractionRun).where(
        FactExtractionRun.organization_id == user.organization_id, FactExtractionRun.workspace_owner_id == user.id,
        FactExtractionRun.project_id == project_id, FactExtractionRun.origin_key == source.origin_key,
        FactExtractionRun.source_fingerprint == source.fingerprint, FactExtractionRun.extractor_version == EXTRACTOR_VERSION))
    if attempt_count >= MAX_GENERATIONS:
        raise HTTPException(429, "The generation limit for this source revision was reached. Edit an existing candidate or update the source.")
    # Lock/revalidate the project before inserting its dependent receipt. It
    # cannot be deleted between validation and publishing a zero-fact result.
    project = db.scalar(select(BusinessEntity).where(BusinessEntity.id == project_id,
        BusinessEntity.organization_id == user.organization_id, BusinessEntity.workspace_owner_id == user.id)
        .with_for_update().execution_options(populate_existing=True))
    if project is None or project.entity_type != "PROJECT":
        raise HTTPException(409, "The selected project changed. Choose a project again.")
    _snapshot(db, user, source)  # Includes 0-fact/failed runs in purge provenance.
    ranges = _segments(source.text)
    analyzed = sum(span["end"] - span["start"] for span in ranges)
    coverage = dict(total_characters=len(source.text), analyzed_characters=analyzed,
        ranges=ranges, partial=analyzed < len(source.text))
    run = FactExtractionRun(organization_id=user.organization_id, workspace_owner_id=user.id,
        project_id=project_id, source_kind=source.kind, source_id=source.source_id,
        origin_key=source.origin_key, source_fingerprint=source.fingerprint,
        cache_key=cache_key, request_key=request_key, request_hash=intent_hash,
        generation=generation, status="PROCESSING", extractor_version=EXTRACTOR_VERSION,
        provider=provider_name, model=model, coverage_json=coverage, proposal_ids=[])
    db.add(run)
    db.flush()
    _receipt(db, user, run, request_key, intent_hash)
    try:
        # A reimport can reuse validated model output, but publishes fresh local
        # evidence/proposals. It never revives a removed source or an old approval.
        reusable = None if regenerate else db.scalar(_scope(FactExtractionRun, user).where(
            FactExtractionRun.id != run.id, FactExtractionRun.project_id == project_id,
            FactExtractionRun.origin_key == source.origin_key, FactExtractionRun.source_fingerprint == source.fingerprint,
            FactExtractionRun.extractor_version == EXTRACTOR_VERSION, FactExtractionRun.provider == provider_name,
            FactExtractionRun.model == model, FactExtractionRun.status == "SUCCEEDED",
            FactExtractionRun.redacted_at.is_(None)).order_by(FactExtractionRun.created_at.desc()).limit(1))
        if reusable and reusable.output_json:
            output = {**reusable.output_json, "candidates": [dict(c, proposal_id=None) for c in reusable.output_json["candidates"]]}
        else:
            if settings.ai_provider == "llm" and not settings.llm_api_key:
                return _failed(db, run, "AI_NOT_CONFIGURED")
            provider = get_ai_provider(settings)
            summaries, points, candidates, quote = [], [], [], None
            for index, span in enumerate(ranges):
                segment = source.text[span["start"]:span["end"]]
                if not segment.strip():
                    continue
                raw = provider.prepare_fact_bundle(segment, source.title, {
                    "project_name": project.name, "source_kind": source.kind,
                    "source_time": source.source_time.isoformat() if source.source_time else None,
                    "segment_index": index, "segment_start": span["start"],
                    "supported_fact_types": ["MEETING_TIME"], "date_precision": "COMPLETE_DATE_ONLY"})
                bundle, items = _validate_output(raw, segment, source.text, span["start"], source.kind)
                summaries.append(bundle.summary.strip())
                points.extend(bundle.key_points)
                candidates.extend(items)
                quote = quote or bundle.evidence_text
            deduplicated = {}
            for item in candidates:
                key = _hash([item["subject"].casefold(), item["occurrence"], item["value_date"],
                    item["original_value"], item["modality"], item["source_context"], item["evidence_text"]])
                deduplicated.setdefault(key, item)
            if len(deduplicated) > MAX_FACTS or not summaries or quote is None:
                raise ExtractionOutputError()
            summary = "\n\n".join(dict.fromkeys(summaries))
            if len(summary) > 12000:
                raise ExtractionOutputError()
            output = dict(summary=summary, key_points=list(dict.fromkeys(points))[:20],
                evidence_text=quote, candidates=list(deduplicated.values()))
        fresh = read_fact_source(db, user, source.kind, source.source_id, lock=True,
            acknowledge_sensitive=acknowledged)
        if fresh.fingerprint != source.fingerprint or fresh.origin_key != source.origin_key:
            return _failed(db, run, "SOURCE_CHANGED")
        # All proposals + summary publish together; invalid candidates cannot
        # leave half a successful generation behind.
        with db.begin_nested():
            memory = FactMemoryService(db, owner_id=user.id, org_id=user.organization_id)
            ids = []
            for index, candidate in enumerate(output["candidates"]):
                if candidate["outcome"] != "PROPOSED":
                    continue
                fact = memory.propose(project_id=project_id,
                    canonical_key=f"candidate:{run.id.hex}:{index}", occurrence_key="",
                    source_kind=source.kind, source_id=source.source_id,
                    value_date=candidate["value_date"], excerpt=candidate["evidence_text"],
                    acknowledge_sensitive=acknowledged)
                # A generated subject is a suggested identity, never a match
                # to an already accepted fact. Phase 4 must resolve it first.
                fact.extraction_context = {"extraction_run_id": str(run.id), "candidate_index": index,
                    "identity_unverified": True, "requires_revalidation": False,
                    "modality": candidate["modality"], "source_context": candidate["source_context"],
                    "subject": candidate["subject"], "occurrence": candidate["occurrence"],
                    "previous_value_date": candidate["previous_value_date"], "change_cue": candidate["change_cue"]}
                candidate["proposal_id"] = str(fact.id)
                ids.append(str(fact.id))
            summary = SourceProposal(organization_id=user.organization_id, user_id=user.id,
                source_kind=source.kind, source_id=source.source_id, source_title=source.title,
                source_fingerprint=source.fingerprint, target="knowledge", status="SUGGESTED",
                payload={"summary": output["summary"], "key_points": output["key_points"]},
                evidence_text=output["evidence_text"], analysis_truncated=coverage["partial"],
                sensitivity_acknowledged=acknowledged, provider=provider_name, version=1,
                active_key=_hash(["fact-summary", run.id]))
            db.add(summary)
            db.flush()
            run.source_proposal_id, run.proposal_ids, run.output_json = summary.id, ids, output
            run.status = "SUCCEEDED"
            from app.modules.cwi.services.source_proposal_service import audit
            audit(db, user, summary, "GENERATE_FACT_EXTRACTION")
            db.flush()
    except AIProviderError:
        return _failed(db, run, "AI_UNAVAILABLE")
    except (ValidationError, ExtractionOutputError):
        return _failed(db, run, "INVALID_AI_OUTPUT")
    except HTTPException as error:
        if error.status_code == 404:
            missing_snapshot = db.scalar(_scope(FactSourceSnapshot, user).where(
                FactSourceSnapshot.source_kind == source.kind, FactSourceSnapshot.source_id == source.source_id,
                FactSourceSnapshot.source_fingerprint == source.fingerprint))
            if missing_snapshot is not None:
                missing_snapshot.available = False
        return _failed(db, run, "SOURCE_UNAVAILABLE" if error.status_code == 404 else "SOURCE_REQUIRES_REVIEW")
    return run


def view(db, user, run):
    output = {} if run.redacted_at else run.output_json or {}
    coverage = run.coverage_json or {}
    if run.redacted_at:
        coverage = {"total_characters": 0, "analyzed_characters": 0, "ranges": [], "partial": False}
    candidates = []
    for candidate in output.get("candidates", []):
        item = dict(candidate, proposal_revision=None, proposal_status=None,
            active_resolution_id=None, resolved_status=None)
        if item.get("proposal_id"):
            proposal = db.scalar(_scope(FactProposal, user).where(FactProposal.id == UUID(item["proposal_id"])))
            if proposal is not None:
                display_proposal = proposal
                evidence = db.scalar(_scope(FactEvidence, user).where(FactEvidence.id == proposal.evidence_id))
                item.update(value_date=(proposal.value_json or {}).get("date"),
                    evidence_text=evidence.excerpt if evidence and not evidence.redacted_at else None,
                    proposal_revision=proposal.revision, proposal_status=proposal.status)
                # Display the linked review outcome without rewriting the
                # original candidate's immutable identity/edit history.
                from app.core.fact_models import FactResolution
                resolved = db.execute(select(FactProposal, FactEvidence)
                    .join(FactResolution, FactResolution.resolved_proposal_id == FactProposal.id)
                    .join(FactEvidence, FactEvidence.id == FactProposal.evidence_id)
                    .where(FactProposal.organization_id == user.organization_id,
                        FactProposal.workspace_owner_id == user.id,
                        FactResolution.organization_id == user.organization_id,
                        FactResolution.workspace_owner_id == user.id,
                        FactResolution.candidate_id == proposal.id, FactResolution.active.is_(True))).first()
                if resolved is not None:
                    reviewed, reviewed_evidence = resolved
                    display_proposal = reviewed
                    item.update(active_resolution_id=str(reviewed.id), resolved_status=reviewed.status,
                        value_date=(reviewed.value_json or {}).get("date"),
                        evidence_text=reviewed_evidence.excerpt if not reviewed_evidence.redacted_at else None)
                if display_proposal.extraction_context.get("requires_revalidation"):
                    item["reason_codes"] = [*item.get("reason_codes", []), "EDIT_REQUIRES_REVALIDATION"]
        candidates.append(item)
    project_name = db.scalar(select(BusinessEntity.name).where(BusinessEntity.id == run.project_id,
        BusinessEntity.organization_id == user.organization_id, BusinessEntity.workspace_owner_id == user.id))
    return {"id": run.id, "status": run.status, "project_id": run.project_id, "project_name": project_name, "generation": run.generation,
        "extractor_version": run.extractor_version, "coverage": coverage,
        "warnings": (["Only part of this source was analysed. The remaining text has not been checked."]
            if coverage.get("partial") else []), "candidates": candidates, "redacted_at": run.redacted_at}


def for_summary(db, user, summary_id):
    return db.scalar(_scope(FactExtractionRun, user).where(FactExtractionRun.source_proposal_id == summary_id,
        FactExtractionRun.status == "SUCCEEDED").order_by(FactExtractionRun.created_at.desc()).limit(1))
