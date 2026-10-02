"""Shared settings for login-time and manual sync. No provider calls on save."""
from hashlib import sha256
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from app.modules.cwi.models import SyncPreference, SyncExclusion


class SyncPreferenceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    email_days: int = Field(default=7, ge=1, le=365)
    calendar_past_days: int = Field(default=7, ge=0, le=365)
    calendar_future_days: int = Field(default=90, ge=1, le=365)


def preferences(db, org_id, user_id):
    row = db.scalar(select(SyncPreference).where(
        SyncPreference.organization_id == org_id, SyncPreference.user_id == user_id))
    return SyncPreferenceView.model_validate(row) if row else SyncPreferenceView()


def exclusion_query(connection, external_id):
    return select(SyncExclusion).where(
        SyncExclusion.organization_id == connection.organization_id,
        SyncExclusion.user_id == connection.user_id,
        SyncExclusion.account_email == connection.account_email.lower(),
        SyncExclusion.external_id_hash == sha256(external_id.encode()).hexdigest())


def exclude_email(db, connection, external_id):
    if db.scalar(exclusion_query(connection, external_id)) is None:
        db.add(SyncExclusion(organization_id=connection.organization_id,
            user_id=connection.user_id, account_email=connection.account_email.lower(),
            external_id_hash=sha256(external_id.encode()).hexdigest()))
        db.flush()
