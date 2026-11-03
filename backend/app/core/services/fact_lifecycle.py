"""Local fact provenance lifecycle; this is not a whole-workspace purge API.

Ordinary source removal only marks originals unavailable. The internal purge
helper redacts the new fact stores, including historical edit copies. Legacy
summaries, raw sources, files and AI logs need the Phase 6 purge coordinator.
Callers own the outer transaction and must never commit half a purge.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.fact_models import (
    FactEvidence, FactEvidenceLink, FactIdentity, FactProposal, FactReview,
    FactSourceSnapshot, FactVersion,
)


def lock_fact_origin(db: Session, *, org_id: UUID, owner_id: UUID, origin_key: str) -> None:
    """Serialize fact writers/purge by origin before taking identity locks.

    PostgreSQL's transaction lock also covers the no-snapshot-yet case. SQLite
    tests are sequential and cannot certify this concurrency guarantee.
    """
    if db.get_bind().dialect.name == "postgresql":
        digest = hashlib.sha256(f"fact-origin:{org_id}:{owner_id}:{origin_key}".encode()).digest()
        key = int.from_bytes(digest[:8], "big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def mark_fact_sources_removed(
    db: Session, *, org_id: UUID, owner_id: UUID,
    source_kind: str, source_ids: list[UUID],
) -> int:
    """Mark all captured revisions unavailable in the raw-delete transaction.

    No identity/proposal locks are acquired here: legacy deletion can already
    hold raw-source locks, while review locks identity before raw source. Draft
    minimization is deliberately deferred to the coordinated Phase 6 lifecycle.
    Retained excerpts must never be presented as an available original.
    """
    if not source_ids:
        return 0
    result = db.execute(update(FactSourceSnapshot).where(
        FactSourceSnapshot.organization_id == org_id,
        FactSourceSnapshot.workspace_owner_id == owner_id,
        FactSourceSnapshot.source_kind == source_kind,
        FactSourceSnapshot.source_id.in_(source_ids),
    ).values(available=False))
    return int(result.rowcount or 0)


@dataclass(frozen=True)
class FactPurgeResult:
    snapshots: int
    evidence: int
    proposals: int
    versions: int
    reviews: int


def purge_fact_origin(
    db: Session, *, org_id: UUID, owner_id: UUID, snapshot_id: UUID,
) -> FactPurgeResult:
    """Redact fact-layer content for an origin, including local reimports.

    This INTERNAL helper must be coordinated with actual raw-content erasure;
    it must not be exposed as a successful whole-workspace purge endpoint.
    The same origin is never independent corroboration of itself. A surviving
    approved link from a different nonpurged origin can preserve a fact value.
    Review/proposal publication uses ``lock_fact_origin`` too.
    """
    def scoped(model):
        return select(model).where(model.organization_id == org_id,
            model.workspace_owner_id == owner_id)

    target = db.scalar(scoped(FactSourceSnapshot).where(FactSourceSnapshot.id == snapshot_id))
    if target is None:
        raise HTTPException(404, "Fact source not found.")
    origin_key = target.origin_key
    lock_fact_origin(db, org_id=org_id, owner_id=owner_id, origin_key=origin_key)
    with db.begin_nested():
        snapshots = list(db.scalars(scoped(FactSourceSnapshot).where(
            FactSourceSnapshot.origin_key == origin_key)))
        snapshot_ids = [row.id for row in snapshots]
        evidence_ids = list(db.scalars(select(FactEvidence.id).where(
            FactEvidence.organization_id == org_id, FactEvidence.workspace_owner_id == owner_id,
            FactEvidence.snapshot_id.in_(snapshot_ids))))
        # Acquire the same identity-before-proposal order as fact review. All
        # writers for this origin are excluded by the advisory lock above.
        identities = set(db.scalars(select(FactProposal.identity_id).where(
            FactProposal.organization_id == org_id, FactProposal.workspace_owner_id == owner_id,
            FactProposal.evidence_id.in_(evidence_ids))))
        identities.update(db.scalars(select(FactEvidenceLink.identity_id).where(
            FactEvidenceLink.organization_id == org_id, FactEvidenceLink.workspace_owner_id == owner_id,
            FactEvidenceLink.evidence_id.in_(evidence_ids))))
        if identities:
            list(db.scalars(scoped(FactIdentity).where(FactIdentity.id.in_(identities))
                .order_by(FactIdentity.id).with_for_update()))
        snapshots = list(db.scalars(scoped(FactSourceSnapshot).where(
            FactSourceSnapshot.origin_key == origin_key).with_for_update()
            .execution_options(populate_existing=True)))
        evidence = list(db.scalars(scoped(FactEvidence).where(
            FactEvidence.id.in_(evidence_ids)).with_for_update()
            .execution_options(populate_existing=True)))
        proposals = list(db.scalars(scoped(FactProposal).where(
            FactProposal.evidence_id.in_(evidence_ids)).with_for_update()
            .execution_options(populate_existing=True)))
        affected_version_ids = set(db.scalars(select(FactEvidenceLink.version_id).where(
            FactEvidenceLink.organization_id == org_id, FactEvidenceLink.workspace_owner_id == owner_id,
            FactEvidenceLink.evidence_id.in_(evidence_ids))))
        independent_ids = set(db.scalars(select(FactEvidenceLink.version_id)
            .join(FactEvidence, FactEvidence.id == FactEvidenceLink.evidence_id)
            .join(FactSourceSnapshot, FactSourceSnapshot.id == FactEvidence.snapshot_id).where(
                FactEvidenceLink.organization_id == org_id,
                FactEvidenceLink.workspace_owner_id == owner_id,
                FactEvidence.organization_id == org_id, FactEvidence.workspace_owner_id == owner_id,
                FactSourceSnapshot.organization_id == org_id,
                FactSourceSnapshot.workspace_owner_id == owner_id,
                FactEvidenceLink.version_id.in_(affected_version_ids),
                FactSourceSnapshot.origin_key != origin_key,
                FactSourceSnapshot.purged_at.is_(None), FactEvidence.redacted_at.is_(None))))
        versions = list(db.scalars(scoped(FactVersion).where(
            FactVersion.id.in_(affected_version_ids - independent_ids)).with_for_update()
            .execution_options(populate_existing=True)))
        redacted_identities = {row.identity_id for row in versions}
        proposal_ids = {row.id for row in proposals}
        # EDIT history may contain the previous accepted value. Scrub all review
        # details for an identity whose accepted value has to be removed.
        reviews = list(db.scalars(scoped(FactReview).where(
            FactReview.proposal_id.in_(proposal_ids) |
            FactReview.identity_id.in_(redacted_identities)).with_for_update()
            .execution_options(populate_existing=True)))
        now = datetime.now(timezone.utc)
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET LOCAL knowact.allow_fact_redaction = 'on'"))
        counts = [0, 0, 0, 0, 0]
        for row in snapshots:
            if row.purged_at is None:
                counts[0] += 1
                row.available, row.source_title, row.purged_at = False, None, now
        for row in evidence:
            if row.redacted_at is None:
                counts[1] += 1
                row.excerpt, row.locator_json, row.redacted_at = None, {}, now
        for row in proposals:
            if row.redacted_at is None:
                counts[2] += 1
                row.value_json, row.redacted_at = None, now
                row.comparison_ready = False
        for row in versions:
            if row.redacted_at is None:
                counts[3] += 1
                row.value_json, row.redacted_at = None, now
        for row in reviews:
            if row.redacted_at is None:
                counts[4] += 1
                row.detail, row.redacted_at = {}, now
        # A pending candidate cannot keep a cached comparison to a now-redacted
        # head, even if its own independent source remains available.
        if versions:
            db.execute(update(FactProposal).where(
                FactProposal.organization_id == org_id, FactProposal.workspace_owner_id == owner_id,
                FactProposal.expected_head_id.in_([row.id for row in versions]),
                FactProposal.status.in_(["PENDING", "UNRESOLVED"]),
            ).values(comparison_ready=False))
        db.flush()
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET LOCAL knowact.allow_fact_redaction = 'off'"))
        return FactPurgeResult(*counts)
