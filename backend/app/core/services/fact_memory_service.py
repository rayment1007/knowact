"""Human-reviewed atomic memory. No extraction, provider write or implicit approval.

Phase 2 accepts controlled date candidates through Python only. Comparison is
server-side preparation, not a client-supplied permission to replace history.
The caller owns the outer transaction; each mutation has a rollback savepoint.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timezone
import hashlib
import json
import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.fact_models import (
    FactEvidence, FactEvidenceLink, FactIdentity, FactProposal, FactReview,
    FactSourceSnapshot, FactVersion,
)
from app.core.models import BusinessEntity, User
from app.core.services.audit_service import AuditService
from app.core.services.fact_lifecycle import lock_fact_origin
from app.core.services.fact_sources import read_fact_source


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), default=str).encode()).hexdigest()


def _fail(message):
    raise HTTPException(409, message)


def _date_value(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise HTTPException(422, "A complete ISO date (YYYY-MM-DD) is required.")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, "The date is invalid.") from None
    return {"date": value, "precision": "DATE"}


def _date_pattern(value):
    d = date.fromisoformat(value["date"])
    months = ("January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December")
    month = months[d.month - 1]
    month_pattern = f"(?:{month}|{month[:3]})"
    return (rf"(?:{re.escape(d.isoformat())}|0?{d.day}\s+{month_pattern}\s+{d.year}"
            rf"|{month_pattern}\s+0?{d.day},?\s+{d.year})")


def _time(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


class FactMemoryService:
    def __init__(self, db, *, owner_id: UUID, org_id: UUID):
        self.db, self.owner_id, self.org_id = db, owner_id, org_id
        if not isinstance(owner_id, UUID) or not isinstance(org_id, UUID):
            raise ValueError("An explicit organization and workspace owner are required.")
        self.user = db.scalar(select(User).where(User.id == owner_id, User.organization_id == org_id))
        if self.user is None:
            raise HTTPException(404, "Workspace not found.")

    def _scope(self, model):
        return select(model).where(model.organization_id == self.org_id,
            model.workspace_owner_id == self.owner_id)

    def _get(self, model, item_id, *, lock=False):
        query = self._scope(model).where(model.id == item_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        item = self.db.scalar(query)
        if item is None:
            raise HTTPException(404, "Fact record not found.")
        return item

    @contextmanager
    def _mutation(self):
        try:
            with self.db.begin_nested():
                yield
                self.db.flush()
        except IntegrityError as exc:
            raise HTTPException(409, "The fact changed during review. Reload it and try again.") from exc

    def _head(self, identity_id):
        return self.db.scalar(self._scope(FactVersion).where(
            FactVersion.identity_id == identity_id, FactVersion.status == "CURRENT"
        ).execution_options(populate_existing=True))

    def _locked_proposal(self, proposal_id):
        # A source-origin advisory lock also serializes purge versus new
        # proposals on different identities. Discover it without row locks.
        peek = self._get(FactProposal, proposal_id)
        evidence = self._get(FactEvidence, peek.evidence_id)
        snapshot = self._get(FactSourceSnapshot, evidence.snapshot_id)
        lock_fact_origin(self.db, org_id=self.org_id, owner_id=self.owner_id,
            origin_key=snapshot.origin_key)
        self._get(FactIdentity, peek.identity_id, lock=True)
        proposal = self._get(FactProposal, proposal_id, lock=True)
        # Another edit may have changed the quote, but cannot change its origin.
        return proposal

    @staticmethod
    def _editable(proposal, revision):
        if proposal.redacted_at is not None:
            _fail("This proposal's evidence was deleted.")
        if proposal.status not in {"PENDING", "UNRESOLVED"}:
            _fail("This proposal has already been reviewed.")
        if proposal.revision != revision:
            _fail("This proposal was edited. Reload before reviewing.")

    def _fresh_source(self, proposal, expected_fingerprint=None):
        evidence = self._get(FactEvidence, proposal.evidence_id)
        snapshot = self._get(FactSourceSnapshot, evidence.snapshot_id)
        self.db.refresh(snapshot)
        self.db.refresh(evidence)
        if not snapshot.available or snapshot.purged_at or evidence.redacted_at:
            _fail("The original source is unavailable. Select a fresh source before reviewing.")
        if expected_fingerprint is not None and expected_fingerprint != snapshot.source_fingerprint:
            _fail("The displayed source revision is stale.")
        try:
            source = read_fact_source(self.db, self.user, snapshot.source_kind,
                snapshot.source_id, lock=True,
                acknowledge_sensitive=bool(evidence.locator_json.get("sensitive_acknowledged")))
        except HTTPException as exc:
            if exc.status_code == 404:
                _fail("The original source was removed. Select a fresh source before reviewing.")
            raise
        if source.fingerprint != snapshot.source_fingerprint or source.origin_key != snapshot.origin_key:
            _fail("The source changed. Extract and review a fresh proposal.")
        return source, evidence, snapshot

    @staticmethod
    def _quote(source, excerpt, value):
        if not isinstance(excerpt, str) or not 1 <= len(excerpt.strip()) <= 4000:
            raise HTTPException(422, "Choose an evidence passage of 1 to 4,000 characters.")
        start = source.text.find(excerpt)
        if start < 0:
            raise HTTPException(422, "Evidence must be an exact passage from this source.")
        if re.search(r"(?<!\w)" + _date_pattern(value) + r"(?!\w)", excerpt, re.I) is None:
            raise HTTPException(422, "The evidence must contain the complete proposed date.")
        # This proves location and date presence, not semantic entailment.
        # Semantic/modality classification belongs to the next phase.
        return start

    def _evidence(self, source, excerpt, value, acknowledge_sensitive=False):
        start = self._quote(source, excerpt, value)
        snapshot = self.db.scalar(self._scope(FactSourceSnapshot).where(
            FactSourceSnapshot.source_kind == source.kind,
            FactSourceSnapshot.source_id == source.source_id,
            FactSourceSnapshot.source_fingerprint == source.fingerprint))
        if snapshot is None:
            snapshot = FactSourceSnapshot(organization_id=self.org_id, workspace_owner_id=self.owner_id,
                source_kind=source.kind, source_id=source.source_id, origin_key=source.origin_key,
                source_fingerprint=source.fingerprint, source_title=source.title,
                source_time=source.source_time, available=True)
            self.db.add(snapshot)
            self.db.flush()
        elif snapshot.purged_at or not snapshot.available:
            _fail("This source revision was removed. Import it again before making a new proposal.")
        quote_hash = _hash(excerpt)
        evidence = self.db.scalar(self._scope(FactEvidence).where(
            FactEvidence.snapshot_id == snapshot.id, FactEvidence.excerpt_hash == quote_hash))
        if evidence is None:
            evidence = FactEvidence(organization_id=self.org_id, workspace_owner_id=self.owner_id,
                snapshot_id=snapshot.id, excerpt=excerpt, excerpt_hash=quote_hash,
                locator_json={**source.locator, "start": start, "end": start + len(excerpt),
                    "sensitive_acknowledged": acknowledge_sensitive})
            self.db.add(evidence)
            self.db.flush()
        elif evidence.redacted_at:
            _fail("This evidence was deleted. Select a fresh source.")
        return evidence

    def propose(self, *, project_id, canonical_key, source_kind, source_id,
                value_date, excerpt, occurrence_key="", acknowledge_sensitive=False):
        """Internal controlled-candidate entry; creates no accepted knowledge."""
        value = _date_value(value_date)
        if not isinstance(canonical_key, str) or not canonical_key.strip() or len(canonical_key) > 160:
            raise HTTPException(422, "Choose an explicit fact identity within this project.")
        if not isinstance(occurrence_key, str) or len(occurrence_key) > 160:
            raise HTTPException(422, "Invalid occurrence identity.")
        key, occurrence = canonical_key.strip().casefold(), occurrence_key.strip().casefold()
        if len(key) > 160 or len(occurrence) > 160:
            raise HTTPException(422, "The normalized fact identity is too long.")
        with self._mutation():
            source = read_fact_source(self.db, self.user, source_kind, source_id, lock=False,
                acknowledge_sensitive=acknowledge_sensitive)
            lock_fact_origin(self.db, org_id=self.org_id, owner_id=self.owner_id, origin_key=source.origin_key)
            project = self.db.scalar(select(BusinessEntity).where(BusinessEntity.id == project_id,
                BusinessEntity.organization_id == self.org_id, BusinessEntity.workspace_owner_id == self.owner_id
            ).with_for_update().execution_options(populate_existing=True))
            if project is None:
                raise HTTPException(404, "Project not found.")
            if project.entity_type != "PROJECT":
                raise HTTPException(422, "Structured facts require an explicitly selected project.")
            identity = self.db.scalar(self._scope(FactIdentity).where(
                FactIdentity.project_id == project.id, FactIdentity.fact_type == "MEETING_TIME",
                FactIdentity.canonical_key == key, FactIdentity.occurrence_key == occurrence).with_for_update())
            if identity is None:
                identity = FactIdentity(organization_id=self.org_id, workspace_owner_id=self.owner_id,
                    project_id=project.id, fact_type="MEETING_TIME", canonical_key=key, occurrence_key=occurrence)
                self.db.add(identity)
                self.db.flush()
            fresh = read_fact_source(self.db, self.user, source.kind, source.source_id, lock=True,
                acknowledge_sensitive=acknowledge_sensitive)
            if fresh.fingerprint != source.fingerprint or fresh.origin_key != source.origin_key:
                _fail("The source changed while preparing evidence. Try again.")
            evidence = self._evidence(fresh, excerpt, value, acknowledge_sensitive)
            candidates = self.db.scalars(self._scope(FactProposal).where(
                FactProposal.identity_id == identity.id, FactProposal.evidence_id == evidence.id,
                FactProposal.status.in_(["PENDING", "UNRESOLVED"])))
            for existing in candidates:
                if existing.value_json == value and existing.redacted_at is None:
                    return existing
            head = self._head(identity.id)
            proposal = FactProposal(organization_id=self.org_id, workspace_owner_id=self.owner_id,
                identity_id=identity.id, evidence_id=evidence.id, value_json=value,
                revision=1, relationship="UNCLEAR", comparison_ready=False,
                expected_head_id=head.id if head else None, status="PENDING")
            self.db.add(proposal)
            self.db.flush()
            return proposal

    @staticmethod
    def _check_head(head, expected_head_id):
        if (head.id if head else None) != expected_head_id:
            _fail("The accepted fact changed. Compare against its current version again.")
        if head and head.redacted_at:
            _fail("The current fact's evidence was deleted. Resolve it before replacing it.")

    def _supported(self, proposal, head, evidence, snapshot, relationship):
        if relationship == "NEW":
            if head is not None:
                _fail("This identity already has an accepted version.")
        elif relationship == "SAME":
            if head is None or proposal.value_json != head.value_json:
                _fail("Same evidence must support the exact current value.")
        elif relationship == "REVISION":
            if head is None or proposal.value_json == head.value_json:
                _fail("Replacement requires a different current value.")
            # Deliberately narrow M1 gate: complete explicit from/to dates,
            # replacement wording, and source chronology. Not a five-class AI.
            quote = evidence.excerpt or ""
            if re.search(r"\b(?:not|never|might|may|could|should|propos\w*|suggest\w*|if)\b|\?", quote, re.I):
                _fail("This passage does not establish an explicit completed rescheduling.")
            pattern = (r"\b(?:rescheduled|moved|changed)\s+from\s+" + _date_pattern(head.value_json)
                + r"\s+to\s+" + _date_pattern(proposal.value_json) + r"(?!\w)")
            if re.search(pattern, quote, re.I) is None:
                _fail("The passage must explicitly replace the current date with the proposed date.")
            previous_times = list(self.db.scalars(select(FactSourceSnapshot.source_time)
                .join(FactEvidence, FactEvidence.snapshot_id == FactSourceSnapshot.id)
                .join(FactEvidenceLink, FactEvidenceLink.evidence_id == FactEvidence.id)
                .where(FactEvidenceLink.version_id == head.id,
                    FactEvidenceLink.organization_id == self.org_id,
                    FactEvidenceLink.workspace_owner_id == self.owner_id,
                    FactEvidence.redacted_at.is_(None))))
            if not snapshot.source_time or not previous_times or any(t is None for t in previous_times):
                _fail("Reliable source chronology is required for this replacement.")
            if _time(snapshot.source_time) <= max(_time(t) for t in previous_times):
                _fail("An older or simultaneous statement cannot replace the current fact automatically.")
        else:
            _fail("Conflict or unclear proposals need further resolution before approval.")

    def record_comparison(self, proposal_id, *, revision, expected_head_id, relationship):
        """Internal preparation for controlled M1 candidates; not exposed by HTTP."""
        if relationship not in {"NEW", "SAME", "REVISION", "CONFLICT", "UNCLEAR"}:
            raise HTTPException(422, "Invalid fact relationship.")
        with self._mutation():
            proposal = self._locked_proposal(proposal_id)
            self._editable(proposal, revision)
            head = self._head(proposal.identity_id)
            self._check_head(head, expected_head_id)
            _, evidence, snapshot = self._fresh_source(proposal)
            if relationship not in {"CONFLICT", "UNCLEAR"}:
                self._supported(proposal, head, evidence, snapshot, relationship)
            proposal.relationship, proposal.expected_head_id = relationship, expected_head_id
            proposal.comparison_ready = True
            proposal.revision += 1
            self.db.flush()
            return proposal

    def _replay(self, request_key, payload):
        if not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 128:
            raise HTTPException(422, "A request key of 1 to 128 characters is required.")
        review = self.db.scalar(self._scope(FactReview).where(FactReview.request_key == request_key))
        if review:
            if review.request_hash != _hash(payload):
                _fail("This request key was already used for a different review.")
            return review
        return None

    def _record(self, proposal, *, revision, decision, request_key, payload, detail):
        review = FactReview(organization_id=self.org_id, workspace_owner_id=self.owner_id,
            identity_id=proposal.identity_id, proposal_id=proposal.id,
            proposal_revision=revision, decision=decision, request_key=request_key,
            request_hash=_hash(payload), result_version_id=proposal.result_version_id, detail=detail)
        self.db.add(review)
        self.db.flush()
        AuditService(self.db).record(self.org_id, self.owner_id, "FACT_" + decision,
            "FactProposal", proposal.id, {"review_id": str(review.id),
                "proposal_revision": revision, "identity_id": str(proposal.identity_id),
                "version_id": str(review.result_version_id) if review.result_version_id else None})
        return review

    def edit(self, proposal_id, *, expected_revision, value_date, excerpt, request_key):
        value = _date_value(value_date)
        payload = dict(proposal_id=str(proposal_id), revision=expected_revision,
            decision="EDIT", value=value, excerpt=excerpt)
        with self._mutation():
            replay = self._replay(request_key, payload)
            if replay:
                return replay
            proposal = self._locked_proposal(proposal_id)
            replay = self._replay(request_key, payload)
            if replay:
                return replay
            self._editable(proposal, expected_revision)
            source, old_evidence, _ = self._fresh_source(proposal)
            evidence = self._evidence(source, excerpt, value,
                bool(old_evidence.locator_json.get("sensitive_acknowledged")))
            detail = {"before": proposal.value_json, "after": value,
                "before_evidence_id": str(proposal.evidence_id), "evidence_id": str(evidence.id)}
            proposal.value_json, proposal.evidence_id = value, evidence.id
            proposal.revision += 1
            proposal.status, proposal.relationship, proposal.comparison_ready = "PENDING", "UNCLEAR", False
            return self._record(proposal, revision=expected_revision, decision="EDIT",
                request_key=request_key, payload=payload, detail=detail)

    def _link(self, version, evidence, snapshot):
        # Do not use a local source/connection UUID in the deduplication key:
        # deleting and reimporting the same Google message must not add evidence.
        key = _hash([snapshot.origin_key, snapshot.source_fingerprint, evidence.excerpt_hash])
        existing = self.db.scalar(self._scope(FactEvidenceLink).where(
            FactEvidenceLink.version_id == version.id, FactEvidenceLink.evidence_key == key))
        if existing is None:
            self.db.add(FactEvidenceLink(organization_id=self.org_id, workspace_owner_id=self.owner_id,
                identity_id=version.identity_id, version_id=version.id,
                evidence_id=evidence.id, evidence_key=key))

    def review(self, proposal_id, *, expected_revision, expected_head_id,
               expected_source_fingerprint, decision, request_key):
        if decision not in {"APPROVE", "REJECT", "KEEP_UNRESOLVED"}:
            raise HTTPException(422, "Invalid review decision.")
        payload = dict(proposal_id=str(proposal_id), revision=expected_revision,
            head_id=str(expected_head_id) if expected_head_id else None,
            source_fingerprint=expected_source_fingerprint, decision=decision)
        with self._mutation():
            replay = self._replay(request_key, payload)
            if replay:
                return replay
            proposal = self._locked_proposal(proposal_id)
            replay = self._replay(request_key, payload)
            if replay:
                return replay
            self._editable(proposal, expected_revision)
            if decision == "APPROVE":
                if not proposal.comparison_ready:
                    _fail("Compare this proposal again before approving it.")
                head = self._head(proposal.identity_id)
                self._check_head(head, expected_head_id)
                if proposal.expected_head_id != expected_head_id:
                    _fail("The displayed comparison is stale. Reload it before approving.")
                if not expected_source_fingerprint:
                    raise HTTPException(422, "The displayed source fingerprint is required.")
                _, evidence, snapshot = self._fresh_source(proposal, expected_source_fingerprint)
                self._supported(proposal, head, evidence, snapshot, proposal.relationship)
                if proposal.relationship == "SAME":
                    version = head
                else:
                    number = (head.version_number + 1) if head else 1
                    if head:
                        head.status = "SUPERSEDED"
                        self.db.flush()
                    version = FactVersion(organization_id=self.org_id, workspace_owner_id=self.owner_id,
                        identity_id=proposal.identity_id, version_number=number,
                        value_json=dict(proposal.value_json), status="CURRENT",
                        predecessor_id=head.id if head else None, approved_by=self.owner_id)
                    self.db.add(version)
                    self.db.flush()
                self._link(version, evidence, snapshot)
                proposal.status, proposal.result_version_id = "APPROVED", version.id
            elif decision == "REJECT":
                proposal.status = "REJECTED"
            else:
                proposal.status = "UNRESOLVED"
                # Another tab must reload after a deliberate defer decision;
                # an old Approve must not silently undo that newer review.
                proposal.revision += 1
            return self._record(proposal, revision=expected_revision, decision=decision,
                request_key=request_key, payload=payload,
                detail={"value": proposal.value_json, "evidence_id": str(proposal.evidence_id),
                    "relationship": proposal.relationship,
                    "expected_head_id": str(expected_head_id) if expected_head_id else None})
