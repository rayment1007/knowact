"""Ownership for legacy fixtures that explicitly seed one user per organization.

This is test data construction, never an application ownership fallback. The
single-row assertion deliberately fails for multi-user fixtures, which must
specify their intended owner instead of guessing one.
"""
from sqlalchemy import select
from app.core.models import User


def only_user_id(db, org_id):
    return db.scalars(select(User.id).where(User.organization_id == org_id)).one()
