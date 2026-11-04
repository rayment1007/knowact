from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
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
    project_id: UUID | None = None
    extract_facts: bool = False
    request_key: str | None = Field(default=None, min_length=1, max_length=128)
    regenerate: bool = False


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class EditRequest(VersionRequest):
    payload: service.DraftPayload


def view(row, db=None, user=None):
    result = {field: getattr(row, field) for field in ["id", "source_kind", "source_id", "source_title", "target", "status",
        "payload", "evidence_text", "analysis_truncated", "provider", "version", "result_id", "created_at", "updated_at"]}
    if db is not None and user is not None and row.target == "knowledge":
        from app.core.services import fact_extraction_service as facts
        run = facts.for_summary(db, user, row.id)
        if run is not None:
            result["fact_extraction"] = facts.view(db, user, run)
    return result


@router.get("/fact-extraction/capabilities")
def capabilities(user: User = Depends(get_current_user)):
    from app.core.services import fact_extraction_service as facts
    return {"enabled": facts.enabled(), "supported_fact_types": ["MEETING_TIME"],
        "max_characters": facts.MAX_CHARACTERS, "max_segments": facts.MAX_SEGMENTS}


@router.post("/source/{kind}/{item_id}/proposals")
def generate(kind: SourceKind, item_id: UUID, body: GenerateRequest,
             db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if body.extract_facts:
        from app.core.services import fact_extraction_service as facts
        if body.target != "knowledge" or body.project_id is None or body.request_key is None:
            raise HTTPException(422, "Select a project and request key for knowledge fact extraction.")
        run = facts.generate(db, user, kind, item_id, project_id=body.project_id,
            request_key=body.request_key, acknowledged=body.acknowledge_sensitive, regenerate=body.regenerate)
        if run.status != "SUCCEEDED":
            # Returning a response (rather than raising) commits the safe failed
            # receipt while leaving facts/summary absent. A retry uses a new key.
            return JSONResponse(status_code=503, content=jsonable_encoder({"detail": {
                "code": run.error_code, "message": "Fact extraction could not be completed. No knowledge or facts were approved. Please retry.",
                "extraction_id": run.id, "retryable": run.error_code in {"AI_UNAVAILABLE", "INVALID_AI_OUTPUT", "AI_NOT_CONFIGURED"}}}))
        return view(service.proposal(db, user, run.source_proposal_id), db, user)
    if body.project_id is not None or body.regenerate:
        raise HTTPException(422, "Project extraction options require extract_facts=true.")
    return view(service.generate(db, user, kind, item_id, body.target, body.acknowledge_sensitive), db, user)


@router.get("/proposals/{item_id}")
def get(item_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return view(service.proposal(db, user, item_id), db, user)


@router.patch("/proposals/{item_id}")
def edit(item_id: UUID, body: EditRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return view(service.update(db, user, service.proposal(db, user, item_id, True), body.version, body.payload), db, user)


@router.post("/proposals/{item_id}/approve")
def approve(item_id: UUID, body: EditRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # Save the exact displayed edits and approve in one version-checked transaction.
    return view(service.update(db, user, service.proposal(db, user, item_id, True), body.version, body.payload, True), db, user)


@router.post("/proposals/{item_id}/reject")
def reject(item_id: UUID, body: VersionRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = service.proposal(db, user, item_id, True)
    if row.status == "REJECTED":
        return view(row, db, user)
    if row.status != "SUGGESTED" or row.version != body.version:
        raise HTTPException(409, "This draft has changed. Reopen it before discarding.")
    row.status = "REJECTED"; row.active_key = None; row.version += 1
    service.audit(db, user, row, "REJECT_SOURCE_PROPOSAL")
    db.flush()
    return view(row, db, user)
