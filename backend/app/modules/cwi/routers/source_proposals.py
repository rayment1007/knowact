from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.models import User
from app.database import get_db
from app.dependencies import get_current_user
from app.modules.cwi.services import source_proposal_service as service

router = APIRouter(prefix="/workspace", tags=["workspace"])
SourceKind = Literal["source", "email", "file", "calendar"]


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["knowledge", "action"]
    acknowledge_sensitive: bool = False


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class EditRequest(VersionRequest):
    payload: service.DraftPayload


def view(row):
    return {field: getattr(row, field) for field in ["id", "source_kind", "source_id", "source_title", "target", "status",
        "payload", "evidence_text", "analysis_truncated", "provider", "version", "result_id", "created_at", "updated_at"]}


@router.post("/source/{kind}/{item_id}/proposals")
def generate(kind: SourceKind, item_id: UUID, body: GenerateRequest,
             db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return view(service.generate(db, user, kind, item_id, body.target, body.acknowledge_sensitive))


@router.get("/proposals/{item_id}")
def get(item_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return view(service.proposal(db, user, item_id))


@router.patch("/proposals/{item_id}")
def edit(item_id: UUID, body: EditRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return view(service.update(db, user, service.proposal(db, user, item_id, True), body.version, body.payload))


@router.post("/proposals/{item_id}/approve")
def approve(item_id: UUID, body: EditRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # Save the exact displayed edits and approve in one version-checked transaction.
    return view(service.update(db, user, service.proposal(db, user, item_id, True), body.version, body.payload, True))


@router.post("/proposals/{item_id}/reject")
def reject(item_id: UUID, body: VersionRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = service.proposal(db, user, item_id, True)
    if row.status == "REJECTED":
        return view(row)
    if row.status != "SUGGESTED" or row.version != body.version:
        raise HTTPException(409, "This draft has changed. Reopen it before discarding.")
    row.status = "REJECTED"; row.active_key = None; row.version += 1
    service.audit(db, user, row, "REJECT_SOURCE_PROPOSAL")
    db.flush()
    return view(row)
