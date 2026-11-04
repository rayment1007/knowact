"""Explicit identity resolution and conservative M1 meeting-date comparison.

Similarity is a search aid, never identity proof. This service creates a new
linked proposal; it does not reparent an extracted candidate or approve it.
"""
from __future__ import annotations

import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.fact_models import (
    FactComparisonRequest, FactEvidence, FactEvidenceLink, FactIdentity,
    FactProposal, FactResolution, FactSourceSnapshot,
)
from app.core.models import BusinessEntity
from app.core.services.fact_lifecycle import lock_fact_origin
from app.core.services.fact_memory_service import FactMemoryService, _assertion_risk, _date_pattern, _fail, _hash, _time

RULE_VERSION = "m1-date-comparison-v1"


def canonicalize(value: str) -> str:
    """Normalize explicit human-entered identity text, not semantic meaning."""
    return " ".join(value.strip().casefold().split())


def source_statement(text: str, start: int, excerpt: str) -> str:
    """Keep surrounding assertion wording when a reviewer trims a quote.

    This bounded English M1 check is conservative, not general entailment.
    A date cut out of 'could we meet ...?' must retain its question context.
    """
    before = text[:start]
    boundary = max(before.rfind(mark) for mark in (".", "!", "?", "\n")) + 1
    end = start + len(excerpt)
    if not excerpt.rstrip().endswith((".", "!", "?", "\n")):
        tail = re.search(r"[.!?\n]", text[end:])
        end = end + tail.end() if tail else len(text)
    return text[boundary:end]


def active_resolution(db, org_id, owner_id, proposal_id):
    return db.scalar(select(FactResolution).where(
        FactResolution.organization_id == org_id, FactResolution.workspace_owner_id == owner_id,
        FactResolution.resolved_proposal_id == proposal_id))


def validate_lineage(service, proposal):
    """Called under the source-origin lock; no later match can revive an old one."""
    link = active_resolution(service.db, service.org_id, service.owner_id, proposal.id)
    if link is None:
        if (proposal.extraction_context or {}).get("resolved_candidate_id"):
            _fail("This proposal's identity resolution is unavailable.")
        return None
    service.db.refresh(link)
    if not link.active:
        _fail("This match was replaced or its candidate was edited. Reload the candidate.")
    candidate = service._get(FactProposal, link.candidate_id, lock=True)
    if candidate.redacted_at or candidate.revision != link.candidate_revision:
        _fail("The original candidate changed. Resolve its identity again.")
    if candidate.status not in {"PENDING", "UNRESOLVED"}:
        _fail("The original candidate was reviewed. Reload before continuing.")
    return link


def invalidate_candidate_resolutions(service, candidate, *, for_edit=False):
    links = list(service.db.scalars(service._scope(FactResolution).where(
        FactResolution.candidate_id == candidate.id, FactResolution.active.is_(True))))
    for link in links:
        proposal = service._get(FactProposal, link.resolved_proposal_id, lock=True)
        if proposal.status == "APPROVED":
            _fail("This candidate already has an approved fact. Use a new source to propose a change.")
        link.active = False
        proposal.comparison_ready = False
    service.db.flush()


class FactResolutionService(FactMemoryService):
    def _request_lock(self, request_key):
        if not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 128:
            raise HTTPException(422, "A request key of 1 to 128 characters is required.")
        lock_fact_origin(self.db, org_id=self.org_id, owner_id=self.owner_id,
            origin_key=_hash(["fact-resolution-request", request_key]))

    def _project(self, project_id):
        project = self.db.scalar(select(BusinessEntity).where(BusinessEntity.id == project_id,
            BusinessEntity.organization_id == self.org_id, BusinessEntity.workspace_owner_id == self.owner_id)
            .with_for_update().execution_options(populate_existing=True))
        if project is None:
            raise HTTPException(404, "Project not found.")
        if project.entity_type != "PROJECT":
            raise HTTPException(422, "Choose a project for this fact.")
        return project

    def resolve(self, candidate_id, *, expected_revision, expected_source_fingerprint,
                project_id, request_key, identity_id=None, canonical_key=None, occurrence_key=""):
        payload = dict(candidate_id=str(candidate_id), revision=expected_revision,
            fingerprint=expected_source_fingerprint, project_id=str(project_id),
            identity_id=str(identity_id) if identity_id else None,
            canonical_key=canonical_key, occurrence_key=occurrence_key)
        with self._mutation():
            self._request_lock(request_key)
            replay = self.db.scalar(self._scope(FactResolution).where(FactResolution.request_key == request_key))
            if replay:
                if replay.request_hash != _hash(payload):
                    _fail("This request key was already used for a different identity match.")
                proposal = self._locked_proposal(replay.resolved_proposal_id)
                validate_lineage(self, proposal)
                self._fresh_source(proposal, expected_source_fingerprint)
                return proposal
            peek = self._get(FactProposal, candidate_id)
            evidence = self._get(FactEvidence, peek.evidence_id)
            snapshot = self._get(FactSourceSnapshot, evidence.snapshot_id)
            lock_fact_origin(self.db, org_id=self.org_id, owner_id=self.owner_id, origin_key=snapshot.origin_key)
            self._project(project_id)
            candidate = self._locked_proposal(candidate_id)
            self._editable(candidate, expected_revision)
            if not (candidate.extraction_context or {}).get("identity_unverified"):
                raise HTTPException(422, "Select the original extracted candidate to correct its identity.")
            source, evidence, snapshot = self._fresh_source(candidate, expected_source_fingerprint)
            invalidate_candidate_resolutions(self, candidate)
            if identity_id:
                if canonical_key:
                    raise HTTPException(422, "Choose an existing identity or enter a new one, not both.")
                identity = self._get(FactIdentity, identity_id, lock=True)
                if identity.project_id != project_id:
                    raise HTTPException(422, "The selected fact belongs to a different project.")
                if identity.fact_type != "MEETING_TIME" or identity.canonical_key.startswith("candidate:"):
                    raise HTTPException(422, "Choose a reviewed meeting identity.")
            else:
                key = canonicalize(canonical_key) if isinstance(canonical_key, str) else ""
                occurrence = canonicalize(occurrence_key) if isinstance(occurrence_key, str) else ""
                if not key or len(key) > 160 or key.startswith("candidate:") or len(occurrence) > 160:
                    raise HTTPException(422, "Enter a meeting identity and occurrence of at most 160 characters.")
                identity = self.db.scalar(self._scope(FactIdentity).where(FactIdentity.project_id == project_id,
                    FactIdentity.fact_type == "MEETING_TIME", FactIdentity.canonical_key == key,
                    FactIdentity.occurrence_key == occurrence).with_for_update())
                if identity is None:
                    identity = FactIdentity(organization_id=self.org_id, workspace_owner_id=self.owner_id,
                        project_id=project_id, fact_type="MEETING_TIME", canonical_key=key, occurrence_key=occurrence)
                    self.db.add(identity)
                    self.db.flush()
            context = {**candidate.extraction_context, "identity_unverified": False,
                "requires_revalidation": False, "resolved_candidate_id": str(candidate.id),
                "resolved_candidate_revision": candidate.revision}
            proposal = FactProposal(organization_id=self.org_id, workspace_owner_id=self.owner_id,
                identity_id=identity.id, evidence_id=evidence.id, value_json=dict(candidate.value_json),
                extraction_context=context, revision=1, relationship="UNCLEAR", comparison_ready=False,
                status="PENDING")
            self.db.add(proposal)
            self.db.flush()
            link = FactResolution(organization_id=self.org_id, workspace_owner_id=self.owner_id,
                candidate_id=candidate.id, candidate_revision=candidate.revision, resolved_proposal_id=proposal.id,
                request_key=request_key, request_hash=_hash(payload), active=True)
            self.db.add(link)
            self.db.flush()
            self._prepare(proposal, self._head(identity.id), source, evidence, snapshot)
            return proposal

    def compare(self, proposal_id, *, expected_revision, expected_head_id,
                expected_source_fingerprint, request_key):
        payload = dict(proposal_id=str(proposal_id), revision=expected_revision,
            head_id=str(expected_head_id) if expected_head_id else None, fingerprint=expected_source_fingerprint)
        with self._mutation():
            self._request_lock(request_key)
            receipt = self.db.scalar(self._scope(FactComparisonRequest).where(FactComparisonRequest.request_key == request_key))
            proposal = self._locked_proposal(proposal_id)
            validate_lineage(self, proposal)
            if receipt:
                if receipt.request_hash != _hash(payload):
                    _fail("This request key was used for a different comparison.")
                self._fresh_source(proposal, expected_source_fingerprint)
                return proposal
            self._editable(proposal, expected_revision)
            if not active_resolution(self.db, self.org_id, self.owner_id, proposal.id):
                _fail("Confirm this candidate's identity before comparing it.")
            head = self._head(proposal.identity_id)
            self._check_head(head, expected_head_id)
            source, evidence, snapshot = self._fresh_source(proposal, expected_source_fingerprint)
            self._prepare(proposal, head, source, evidence, snapshot)
            self.db.add(FactComparisonRequest(organization_id=self.org_id, workspace_owner_id=self.owner_id,
                proposal_id=proposal.id, proposal_revision=proposal.revision,
                request_key=request_key, request_hash=_hash(payload)))
            self.db.flush()
            return proposal

    def _prepare(self, proposal, head, source, evidence, snapshot):
        comparison = self.assess(proposal, head, source, evidence, snapshot)
        proposal.relationship = comparison.pop("relationship")
        proposal.comparison_json = comparison
        proposal.comparison_ready = True
        proposal.expected_head_id = head.id if head else None
        proposal.extraction_context = {**proposal.extraction_context, "requires_revalidation": False}
        proposal.revision += 1
        self.db.flush()

    def assess(self, proposal, head, source, evidence, snapshot):
        """Recomputable explanation; no trusted label comes from the browser."""
        context = proposal.extraction_context or {}
        identity = self._get(FactIdentity, proposal.identity_id)
        reasons, codes, warnings = [], [], []
        def result(relationship, code, reason, *, supported=True, manual=False):
            return dict(relationship=relationship, rule_version=RULE_VERSION,
                reason_codes=[*codes, code], reasons=[*reasons, reason], warnings=warnings,
                can_approve=supported, needs_resolution_reason=manual,
                evidence_supported=supported)
        quote = evidence.excerpt or ""
        start = self._quote(source, quote, proposal.value_json)
        if head and head.redacted_at:
            return result("UNCLEAR", "CURRENT_EVIDENCE_DELETED", "The current fact's evidence was deleted.", supported=False)
        if context.get("identity_unverified"):
            return result("UNCLEAR", "IDENTITY_UNVERIFIED", "Confirm which meeting occurrence this statement describes.", supported=False)
        before = source.text[:start]
        statement = source_statement(source.text, start, quote)
        if context.get("source_context") not in {None, "DIRECT"} or re.search(
                r"(?im)^\s*(?:>|-{2,}\s*(?:forwarded|original)\s+message|on .{1,160} wrote:)", before + quote):
            return result("UNCLEAR", "NON_DIRECT_STATEMENT", "Quoted or forwarded text needs a fresh direct assertion.", supported=False)
        if context.get("modality") not in {None, "ASSERTION"} or _assertion_risk(statement):
            return result("UNCLEAR", "NON_ASSERTIVE_STATEMENT", "This passage is a question, negation or tentative proposal, not a confirmed statement.", supported=False)
        subject = canonicalize(context.get("subject") or "")
        if subject and subject not in canonicalize(statement):
            return result("UNCLEAR", "SUBJECT_NOT_IN_STATEMENT", "The supporting statement does not identify the extracted meeting. Choose its complete passage.", supported=False)
        if re.search(r"\b(?:every|weekly|daily|monthly|recurring|each)\b", statement, re.I) and not identity.occurrence_key:
            return result("UNCLEAR", "OCCURRENCE_REQUIRED", "Specify the occurrence of this recurring meeting.", supported=False)
        if re.search(r"\b(?:effective|starting|beginning)\s+(?:from|on|after)\b", statement, re.I):
            return result("UNCLEAR", "EFFECTIVE_TIME_UNSUPPORTED", "The statement has a separate effective period that this date-only flow cannot interpret.", supported=False)
        if re.search(r"\b(?:\d{1,2}:\d{2}|\d{1,2}\s*(?:am|pm))\b", statement, re.I):
            return result("UNCLEAR", "TIME_PRECISION_UNSUPPORTED", "This statement includes a time; do not reduce it to an all-day date.", supported=False)
        # A date found only on the old side of an explicit from/to cannot become
        # the asserted new value simply because the user edited its date field.
        value_pattern = _date_pattern(proposal.value_json)
        from_value = re.search(r"\b(?:rescheduled|moved|changed)\s+from\s+" + value_pattern + r"\s+to\b", statement, re.I)
        if from_value:
            return result("UNCLEAR", "VALUE_IS_PREVIOUS_DATE", "The selected value is the previous date in this passage, not the proposed date.", supported=False)
        # Same-source, same resolved identity and different values are not
        # independent corroboration. Surface pending alternatives explicitly.
        peers = self._scope(FactProposal).join(FactResolution,
            FactResolution.resolved_proposal_id == FactProposal.id).where(
                FactProposal.identity_id == proposal.identity_id, FactProposal.id != proposal.id,
                FactProposal.status.in_(["PENDING", "UNRESOLVED"]), FactProposal.redacted_at.is_(None),
                FactResolution.active.is_(True), FactProposal.value_json != proposal.value_json)
        different_peers = self.db.scalar(select(func.count()).select_from(peers.subquery()))
        if different_peers:
            warnings.append("Other pending proposals for this meeting have different dates. Review them before choosing.")
            contradiction = self.db.scalar(peers.join(FactEvidence, FactEvidence.id == FactProposal.evidence_id)
                .join(FactSourceSnapshot, FactSourceSnapshot.id == FactEvidence.snapshot_id).where(
                    FactSourceSnapshot.origin_key == snapshot.origin_key,
                    FactSourceSnapshot.source_fingerprint == snapshot.source_fingerprint).limit(1))
            if contradiction:
                return result("UNCLEAR", "SOURCE_INTERNAL_CONTRADICTION", "This source has incompatible candidates for the same meeting occurrence.", supported=False)
        # Extraction siblings need review even before each one is identity-
        # matched. Different occurrences remain separate, and rejected siblings
        # no longer prevent a user from resolving a mistaken extraction.
        if context.get("extraction_run_id"):
            from app.core.fact_extraction_models import FactExtractionRun
            run = self._get(FactExtractionRun, UUID(context["extraction_run_id"]))
            for item in (run.output_json or {}).get("candidates", [])[:20]:
                if (canonicalize(item.get("subject") or "") != canonicalize(context.get("subject") or "")
                    or canonicalize(item.get("occurrence") or "") != canonicalize(context.get("occurrence") or "")
                    or not item.get("proposal_id") or item["proposal_id"] == context.get("resolved_candidate_id")):
                    continue
                sibling = self._get(FactProposal, UUID(item["proposal_id"]))
                sibling_link = self.db.scalar(self._scope(FactResolution).where(
                    FactResolution.candidate_id == sibling.id, FactResolution.active.is_(True)))
                if sibling_link:
                    sibling = self._get(FactProposal, sibling_link.resolved_proposal_id)
                    # A user-resolved different occurrence is no longer an
                    # assumed match merely because the extractor used one name.
                    if sibling.identity_id != proposal.identity_id:
                        continue
                if sibling.status in {"PENDING", "UNRESOLVED"} and not sibling.redacted_at and sibling.value_json != proposal.value_json:
                    return result("UNCLEAR", "SOURCE_INTERNAL_CONTRADICTION", "This source has incompatible extracted dates for the same meeting occurrence. Clarify or reject the mistaken candidate first.", supported=False)
        if head is None:
            if different_peers:
                return result("UNCLEAR", "COMPETING_NEW_PROPOSALS", "No accepted value exists, but pending alternatives disagree. Explain why this candidate should establish the first version.", manual=True)
            return result("NEW", "NO_CURRENT_VERSION", "This explicitly selected identity has no accepted current version.")
        if proposal.value_json == head.value_json:
            return result("SAME", "NORMALIZED_VALUE_EQUAL", "The complete date matches the current value. Approval adds evidence without creating a version.")
        latest_time, known_count, total_count = self.db.execute(select(func.max(FactSourceSnapshot.source_time),
                func.count(FactSourceSnapshot.source_time), func.count())
            .join(FactEvidence, FactEvidence.snapshot_id == FactSourceSnapshot.id)
            .join(FactEvidenceLink, FactEvidenceLink.evidence_id == FactEvidence.id).where(
                FactEvidenceLink.version_id == head.id, FactEvidenceLink.organization_id == self.org_id,
                FactEvidenceLink.workspace_owner_id == self.owner_id, FactEvidence.redacted_at.is_(None))).one()
        is_change = bool(re.search(r"\b(?:rescheduled|moved|changed)\s+from\b", quote, re.I))
        exact_change = bool(re.search(r"\b(?:rescheduled|moved|changed)\s+from\s+" + _date_pattern(head.value_json)
            + r"\s+to\s+" + value_pattern + r"(?!\w)", quote, re.I))
        if is_change and not exact_change:
            return result("UNCLEAR", "REPLACEMENT_BASE_MISMATCH", "The stated previous date does not match the current version. A delayed notice must not silently replace it.", manual=True)
        if not snapshot.source_time or not total_count or known_count != total_count:
            return result("UNCLEAR", "SOURCE_CHRONOLOGY_UNKNOWN", "Reliable authored times are missing. A human must resolve the differing dates with an explicit reason.", manual=True)
        if _time(snapshot.source_time) <= _time(latest_time):
            return result("UNCLEAR", "OLDER_OR_SIMULTANEOUS_STATEMENT", "This statement is older than or simultaneous with accepted evidence. It cannot automatically replace the current date.", manual=True)
        if exact_change:
            return result("REVISION", "EXPLICIT_NEWER_REPLACEMENT", "A newer direct statement explicitly replaces the current date with this date.")
        return result("CONFLICT", "INCOMPATIBLE_ASSERTED_DATE", "The same meeting occurrence has incompatible asserted dates without an explicit replacement. Choose a value and explain the decision.", manual=True)

    def check_approval(self, proposal, head, source, evidence, snapshot, resolution_reason):
        validate_lineage(self, proposal)
        result = self.assess(proposal, head, source, evidence, snapshot)
        if result["relationship"] != proposal.relationship or result["reason_codes"] != proposal.comparison_json.get("reason_codes"):
            _fail("The comparison changed. Compare again before approving.")
        if not result["can_approve"]:
            _fail("This statement needs clarification or a better source before it can be approved.")
        if result["needs_resolution_reason"] and not resolution_reason:
            _fail("Explain why this value should be accepted before resolving this disagreement.")
        if proposal.extraction_context.get("requires_revalidation"):
            _fail("Compare the edited statement again before approving it.")
        return result
