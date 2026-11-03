"""Controlled Phase 2 review API; no extraction or client-supplied comparison.

This router is development-only. General Knowledge continues through its
existing routes. Every fact read, including history and evidence, is personal.
"""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.fact_models import (
    FactEvidence, FactEvidenceLink, FactIdentity, FactProposal, FactReview,
    FactSourceSnapshot, FactVersion,
)
from app.core.models import User
from app.database import get_db
from app.dependencies import get_current_user


def require_development_facts(settings: Settings = Depends(get_settings)):
    if settings.mode != "DEVELOPMENT" or not settings.fact_memory_enabled:
        raise HTTPException(404, "Not found.")


router = APIRouter(prefix="/dev/fact-memory", tags=["development-fact-memory"],
    dependencies=[Depends(require_development_facts)])


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    value_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    excerpt: str = Field(min_length=1, max_length=4000)
    request_key: str = Field(min_length=1, max_length=128)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    # Required, including explicit null for a NEW fact, to bind the review.
    expected_head_id: UUID | None
    expected_source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["APPROVE", "REJECT", "KEEP_UNRESOLVED"]
    request_key: str = Field(min_length=1, max_length=128)


def _scope(model, user):
    return (model.organization_id == user.organization_id,
            model.workspace_owner_id == user.id)


def _owned(db, user, model, item_id):
    row = db.scalar(select(model).where(model.id == item_id, *_scope(model, user)))
    if row is None:
        raise HTTPException(404, "Fact record not found.")
    return row


def _fields(row, names):
    return {name: getattr(row, name) for name in names.split()}


def _proposal(row):
    return _fields(row, "id identity_id evidence_id revision value_json relationship comparison_ready expected_head_id status result_version_id redacted_at created_at updated_at")


def _version(row):
    return _fields(row, "id identity_id version_number value_json normalization_version status predecessor_id approved_by approved_at redacted_at")


def _review(row):
    # Request hashes are internal replay guards, not user-facing evidence.
    return _fields(row, "id identity_id proposal_id proposal_revision decision result_version_id detail redacted_at created_at")


def _evidence(db, user, evidence_id):
    evidence = _owned(db, user, FactEvidence, evidence_id)
    snapshot = _owned(db, user, FactSourceSnapshot, evidence.snapshot_id)
    return {
        **_fields(evidence, "id excerpt locator_json redacted_at"),
        "source": _fields(snapshot, "id source_kind source_id source_fingerprint source_title source_time captured_at available purged_at"),
    }


@router.get("/proposals")
def list_proposals(status: Literal["PENDING", "APPROVED", "REJECTED", "UNRESOLVED"] | None = None,
                   limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    filters = [*_scope(FactProposal, user)]
    if status is not None:
        filters.append(FactProposal.status == status)
    total = db.scalar(select(func.count()).select_from(FactProposal).where(*filters))
    rows = db.scalars(select(FactProposal).where(*filters)
        .order_by(FactProposal.created_at.desc(), FactProposal.id.desc()).limit(limit).offset(offset)).all()
    return {"items": [_proposal(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/proposals/{proposal_id}")
def proposal_detail(proposal_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = _owned(db, user, FactProposal, proposal_id)
    identity = _owned(db, user, FactIdentity, row.identity_id)
    head = db.scalar(select(FactVersion).where(*_scope(FactVersion, user),
        FactVersion.identity_id == identity.id, FactVersion.status == "CURRENT"))
    return {"proposal": _proposal(row),
        "identity": _fields(identity, "id project_id fact_type canonical_key occurrence_key"),
        "current": _version(head) if head else None,
        "evidence": _evidence(db, user, row.evidence_id)}


@router.get("/identities/{identity_id}/current")
def current(identity_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _owned(db, user, FactIdentity, identity_id)
    row = db.scalar(select(FactVersion).where(*_scope(FactVersion, user),
        FactVersion.identity_id == identity_id, FactVersion.status == "CURRENT"))
    return {"current": _version(row) if row else None}


@router.get("/identities/{identity_id}/history")
def history(identity_id: UUID, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
            db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _owned(db, user, FactIdentity, identity_id)
    filters = [*_scope(FactVersion, user), FactVersion.identity_id == identity_id]
    total = db.scalar(select(func.count()).select_from(FactVersion).where(*filters))
    versions = db.scalars(select(FactVersion).where(*filters)
        .order_by(FactVersion.version_number.desc()).limit(limit).offset(offset)).all()
    return {"items": [_version(row) for row in versions], "total": total, "limit": limit, "offset": offset}


@router.get("/versions/{version_id}/evidence")
def version_evidence(version_id: UUID, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
                     db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _owned(db, user, FactVersion, version_id)
    filters = [*_scope(FactEvidenceLink, user), FactEvidenceLink.version_id == version_id]
    total = db.scalar(select(func.count()).select_from(FactEvidenceLink).where(*filters))
    links = db.scalars(select(FactEvidenceLink).where(*filters)
        .order_by(FactEvidenceLink.created_at, FactEvidenceLink.id).limit(limit).offset(offset)).all()
    return {"items": [_evidence(db, user, link.evidence_id) for link in links],
            "total": total, "limit": limit, "offset": offset}


@router.get("/proposals/{proposal_id}/reviews")
def reviews(proposal_id: UUID, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
            db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _owned(db, user, FactProposal, proposal_id)
    filters = [*_scope(FactReview, user), FactReview.proposal_id == proposal_id]
    total = db.scalar(select(func.count()).select_from(FactReview).where(*filters))
    rows = db.scalars(select(FactReview).where(*filters)
        .order_by(FactReview.created_at, FactReview.id).limit(limit).offset(offset)).all()
    return {"items": [_review(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.patch("/proposals/{proposal_id}")
def edit(proposal_id: UUID, body: EditRequest,
         db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.core.services.fact_memory_service import FactMemoryService
    return _review(FactMemoryService(db, owner_id=user.id, org_id=user.organization_id)
        .edit(proposal_id=proposal_id, **body.model_dump()))


@router.post("/proposals/{proposal_id}/review")
def review(proposal_id: UUID, body: ReviewRequest,
           db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.core.services.fact_memory_service import FactMemoryService
    return _review(FactMemoryService(db, owner_id=user.id, org_id=user.organization_id)
        .review(proposal_id=proposal_id, **body.model_dump()))
