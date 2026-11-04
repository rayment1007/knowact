"""Owner-scoped, bounded read models for the M1 fact review workspace.

These views do not classify or approve facts. The write services recheck the
source, candidate and accepted head; presentation eligibility is only a hint.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import and_, exists, func, or_, select

from app.core.fact_models import (
    FactEvidence, FactEvidenceLink, FactIdentity, FactProposal, FactResolution,
    FactReview, FactSourceSnapshot, FactVersion,
)
from app.core.models import BusinessEntity, User


def scope(model, user):
    return (model.organization_id == user.organization_id,
            model.workspace_owner_id == user.id)


def owned(db, user, model, item_id):
    row = db.scalar(select(model).where(model.id == item_id, *scope(model, user)))
    if row is None:
        raise HTTPException(404, "Fact record not found.")
    return row


def fields(row, names):
    return {name: getattr(row, name) for name in names.split()}


def proposal(row):
    return fields(row, "id identity_id evidence_id revision value_json extraction_context comparison_json relationship comparison_ready expected_head_id status result_version_id redacted_at created_at updated_at")


def version(row, approved_by_name=None):
    return {**fields(row, "id identity_id version_number value_json normalization_version status predecessor_id approved_by approved_at redacted_at"),
            "approved_by_name": approved_by_name}


def identity(row):
    return fields(row, "id project_id fact_type canonical_key occurrence_key")


def review(row, reviewer_name=None):
    return {**fields(row, "id identity_id proposal_id proposal_revision decision result_version_id detail redacted_at created_at"),
            "reviewer_name": reviewer_name}


def evidence(db, user, evidence_id):
    item = owned(db, user, FactEvidence, evidence_id)
    snapshot = owned(db, user, FactSourceSnapshot, item.snapshot_id)
    return evidence_rows(item, snapshot)


def evidence_rows(item, snapshot):
    return {**fields(item, "id excerpt locator_json redacted_at"),
            "source": fields(snapshot, "id source_kind source_id source_fingerprint source_title source_time captured_at available purged_at")}


def _page(db, query, limit, offset):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    return db.execute(query.limit(limit).offset(offset)).all(), total


def _project(db, user, project_id):
    return owned(db, user, BusinessEntity, project_id)


def visible_proposals(user):
    """One queue entry per candidate, retaining all inactive lineage in history."""
    active_child = exists(select(FactResolution.id).where(*scope(FactResolution, user),
        FactResolution.candidate_id == FactProposal.id, FactResolution.active.is_(True)))
    is_child = exists(select(FactResolution.id).where(*scope(FactResolution, user),
        FactResolution.resolved_proposal_id == FactProposal.id))
    active_child_itself = exists(select(FactResolution.id).where(*scope(FactResolution, user),
        FactResolution.resolved_proposal_id == FactProposal.id, FactResolution.active.is_(True)))
    return and_(~active_child, or_(~is_child, active_child_itself))


def _proposal_query(user):
    return (select(FactProposal, FactIdentity, BusinessEntity.name, FactSourceSnapshot.source_title)
        .join(FactIdentity, and_(FactIdentity.id == FactProposal.identity_id, *scope(FactIdentity, user)))
        .join(BusinessEntity, and_(BusinessEntity.id == FactIdentity.project_id, *scope(BusinessEntity, user)))
        .join(FactEvidence, and_(FactEvidence.id == FactProposal.evidence_id, *scope(FactEvidence, user)))
        .join(FactSourceSnapshot, and_(FactSourceSnapshot.id == FactEvidence.snapshot_id, *scope(FactSourceSnapshot, user)))
        .where(*scope(FactProposal, user), visible_proposals(user)))


def _version_query(user):
    return (select(FactVersion, FactIdentity, BusinessEntity.name)
        .join(FactIdentity, and_(FactIdentity.id == FactVersion.identity_id, *scope(FactIdentity, user)))
        .join(BusinessEntity, and_(BusinessEntity.id == FactIdentity.project_id, *scope(BusinessEntity, user)))
        .where(*scope(FactVersion, user)))


def _search(query, text, *, sources=False):
    if not text or not text.strip():
        return query
    # Escape LIKE metacharacters: a search for '%' is not a whole-workspace scan.
    needle = text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    columns = [FactIdentity.canonical_key, FactIdentity.occurrence_key, BusinessEntity.name]
    if sources:
        columns.append(FactSourceSnapshot.source_title)
    return query.where(or_(*(column.ilike(f"%{needle}%", escape="\\") for column in columns)))


def counts(db, user, project_id=None):
    if project_id is not None:
        _project(db, user, project_id)
    pending = _proposal_query(user).where(FactProposal.redacted_at.is_(None),
        FactProposal.status.in_(["PENDING", "UNRESOLVED"]))
    versions = _version_query(user)
    if project_id is not None:
        pending = pending.where(FactIdentity.project_id == project_id)
        versions = versions.where(FactIdentity.project_id == project_id)

    def count(query):
        return db.scalar(select(func.count()).select_from(query.subquery())) or 0

    # A newly detected conflict already needs attention, before a user chooses
    # Keep unresolved. Unverified extraction candidates are not conflict alerts.
    unresolved = or_(FactProposal.status == "UNRESOLVED", and_(
        FactProposal.comparison_ready.is_(True),
        FactProposal.relationship.in_(["CONFLICT", "UNCLEAR"]),
        ~FactIdentity.canonical_key.startswith("candidate:")))
    return {"needs_review": count(pending),
            "unresolved": count(pending.where(unresolved)),
            "current": count(versions.where(FactVersion.status == "CURRENT")),
            "history": count(versions)}


def memory_page(db, user, *, view, relationship=None, project_id=None, q=None, limit=25, offset=0):
    if project_id is not None:
        _project(db, user, project_id)
    if view == "needs_review":
        query = _proposal_query(user).where(FactProposal.redacted_at.is_(None),
            FactProposal.status.in_(["PENDING", "UNRESOLVED"]))
        if relationship:
            query = query.where(FactProposal.relationship == relationship)
        query = _search(query, q, sources=True)
        if project_id is not None:
            query = query.where(FactIdentity.project_id == project_id)
        rows, total = _page(db, query.order_by(FactProposal.created_at.desc(), FactProposal.id.desc()), limit, offset)
        items = []
        for item, fact, project_name, source_title in rows:
            provisional = fact.canonical_key.startswith("candidate:")
            title = (item.extraction_context or {}).get("subject") if provisional else fact.canonical_key
            items.append({"id": item.id, "record_type": "proposal", "identity": identity(fact),
                "project_name": project_name, "proposal": proposal(item), "version": None,
                "title": title or source_title or "Meeting date candidate",
                "value_date": (item.value_json or {}).get("date"), "relationship": item.relationship,
                "status": item.status, "created_at": item.created_at})
    else:
        query = _version_query(user)
        if view == "current":
            query = query.where(FactVersion.status == "CURRENT")
        if relationship:
            query = query.where(exists(select(FactProposal.id).where(*scope(FactProposal, user),
                FactProposal.result_version_id == FactVersion.id, FactProposal.relationship == relationship,
                FactProposal.status == "APPROVED")))
        query = _search(query, q)
        if project_id is not None:
            query = query.where(FactIdentity.project_id == project_id)
        rows, total = _page(db, query.order_by(FactVersion.approved_at.desc(), FactVersion.id.desc()), limit, offset)
        items = [{"id": item.id, "record_type": "version", "identity": identity(fact),
            "project_name": project_name, "proposal": None, "version": version(item, user.full_name),
            "title": fact.canonical_key, "value_date": (item.value_json or {}).get("date"),
            "relationship": None, "status": item.status, "created_at": item.approved_at}
            for item, fact, project_name in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def identity_page(db, user, *, project_id, q=None, limit=25, offset=0):
    _project(db, user, project_id)
    query = (select(FactIdentity, BusinessEntity.name, FactVersion)
        .join(BusinessEntity, and_(BusinessEntity.id == FactIdentity.project_id, *scope(BusinessEntity, user)))
        .outerjoin(FactVersion, and_(FactVersion.identity_id == FactIdentity.id,
            FactVersion.status == "CURRENT", *scope(FactVersion, user)))
        .where(*scope(FactIdentity, user), FactIdentity.project_id == project_id,
            ~FactIdentity.canonical_key.startswith("candidate:")))
    rows, total = _page(db, _search(query, q).order_by(FactIdentity.canonical_key, FactIdentity.occurrence_key, FactIdentity.id), limit, offset)
    return {"items": [{**identity(fact), "project_name": project_name,
        "current": version(head, user.full_name) if head else None} for fact, project_name, head in rows],
        "total": total, "limit": limit, "offset": offset}


def version_evidence(db, user, version_id, *, limit=25, offset=0):
    owned(db, user, FactVersion, version_id)
    query = (select(FactEvidence, FactSourceSnapshot)
        .select_from(FactEvidenceLink)
        .join(FactEvidence, and_(FactEvidence.id == FactEvidenceLink.evidence_id, *scope(FactEvidence, user)))
        .join(FactSourceSnapshot, and_(FactSourceSnapshot.id == FactEvidence.snapshot_id, *scope(FactSourceSnapshot, user)))
        .where(*scope(FactEvidenceLink, user), FactEvidenceLink.version_id == version_id)
        .order_by(FactEvidenceLink.created_at, FactEvidenceLink.id))
    rows, total = _page(db, query, limit, offset)
    return {"items": [evidence_rows(item, snapshot) for item, snapshot in rows],
        "total": total, "limit": limit, "offset": offset}


def proposal_detail(db, user, proposal_id):
    item = owned(db, user, FactProposal, proposal_id)
    fact = owned(db, user, FactIdentity, item.identity_id)
    project = _project(db, user, fact.project_id)
    head = db.scalar(select(FactVersion).where(*scope(FactVersion, user),
        FactVersion.identity_id == fact.id, FactVersion.status == "CURRENT"))
    own_evidence = evidence(db, user, item.evidence_id)
    current_evidence = version_evidence(db, user, head.id) if head else {"items": [], "total": 0}
    lineage_row = db.scalar(select(FactResolution).where(*scope(FactResolution, user),
        FactResolution.resolved_proposal_id == item.id))
    active_child = db.scalar(select(FactResolution.resolved_proposal_id).where(*scope(FactResolution, user),
        FactResolution.candidate_id == item.id, FactResolution.active.is_(True)))
    lineage = None
    lineage_valid = True
    if lineage_row:
        candidate = owned(db, user, FactProposal, lineage_row.candidate_id)
        lineage = {**fields(lineage_row, "candidate_id candidate_revision resolved_proposal_id active"),
            "candidate_current_revision": candidate.revision, "candidate_status": candidate.status}
        lineage_valid = (lineage_row.active and lineage_row.candidate_revision == candidate.revision
            and candidate.redacted_at is None and candidate.status in {"PENDING", "UNRESOLVED"})
    peers_query = (select(FactProposal).where(*scope(FactProposal, user), visible_proposals(user),
        FactProposal.identity_id == fact.id, FactProposal.id != item.id,
        FactProposal.status.in_(["PENDING", "UNRESOLVED"]), FactProposal.redacted_at.is_(None))
        .order_by(FactProposal.created_at, FactProposal.id))
    peers, peer_count = _page(db, peers_query, 10, 0)
    comparison = item.comparison_json or {}
    source = own_evidence["source"]
    context = item.extraction_context or {}
    fresh = (head.id if head else None) == item.expected_head_id
    ready = bool(item.comparison_ready and item.redacted_at is None and
        item.status in {"PENDING", "UNRESOLVED"} and not active_child and lineage_valid and fresh and
        source["available"] and not source["purged_at"] and not own_evidence["redacted_at"] and
        not (head and head.redacted_at) and not context.get("identity_unverified") and
        not context.get("requires_revalidation"))
    return {"proposal": proposal(item), "identity": identity(fact), "project_name": project.name,
        "current": version(head, user.full_name) if head else None, "evidence": own_evidence,
        "current_evidence": current_evidence["items"], "current_evidence_total": current_evidence["total"],
        "lineage": lineage, "active_resolution_id": active_child,
        "pending_peers": [proposal(row[0]) for row in peers], "pending_peers_total": peer_count,
        "can_approve": ready and bool(comparison.get("can_approve", item.relationship in {"NEW", "SAME", "REVISION"})),
        "needs_resolution_reason": ready and bool(comparison.get("needs_resolution_reason", False))}


def version_detail(db, user, version_id):
    item = owned(db, user, FactVersion, version_id)
    fact = owned(db, user, FactIdentity, item.identity_id)
    project = _project(db, user, fact.project_id)
    evidence_page = version_evidence(db, user, item.id)
    rows, total = _page(db, select(FactReview).where(*scope(FactReview, user),
        FactReview.result_version_id == item.id).order_by(FactReview.created_at, FactReview.id), 25, 0)
    return {"version": version(item, user.full_name), "identity": identity(fact), "project_name": project.name,
        "evidence": evidence_page["items"], "evidence_total": evidence_page["total"],
        "reviews": [review(row[0], user.full_name) for row in rows], "reviews_total": total}
