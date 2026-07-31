"""Tests for the AuditService and the ``GET /api/audit`` route (Requirement 13).

Covers the service's core contract:

* ``record`` writes exactly one ``AuditLog`` row with the supplied fields
  (Requirements 13.1, 13.2);
* ``record`` does **not** commit — the row lives in the caller's transaction
  and disappears if the caller rolls back (Requirement 13.1);
* ``list`` returns entries scoped to the organization, newest first, and never
  leaks another organization's entries (Requirement 13.3);
* ``list`` honors the optional filters.

And the route:

* ``GET /api/audit`` requires authentication (``401`` without a session);
* returns only the caller's organization's entries.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import models as core_models
from app.core.models import AuditLog
from app.core.schemas import AuditFilter
from app.core.services.audit_service import AuditService
from app.security import hash_password

COOKIE_NAME = get_settings().auth_cookie_name


def _make_org_and_user(
    db: Session, org_name: str, email: str
) -> tuple[core_models.Organization, core_models.User]:
    org = core_models.Organization(name=org_name)
    db.add(org)
    db.flush()
    user = core_models.User(
        organization_id=org.id,
        email=email,
        full_name="Actor",
        password_hash=hash_password("pw"),
        role="ADMIN",
    )
    db.add(user)
    db.flush()
    return org, user


def _count_audit(db: Session) -> int:
    return db.execute(select(func.count()).select_from(AuditLog)).scalar_one()


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------


def test_record_writes_exactly_one_row_with_fields(db_session: Session) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    service = AuditService(db_session)

    before = _count_audit(db_session)
    entry = service.record(
        org_id=org.id,
        actor_id=user.id,
        action_type="CONFIRM_CLASSIFICATION",
        target_type="SourceItem",
        target_id=user.id,  # any UUID; using user.id for convenience
        detail={"note": "hello"},
    )

    assert _count_audit(db_session) == before + 1
    assert entry.id is not None
    assert entry.organization_id == org.id
    assert entry.actor_id == user.id
    assert entry.action_type == "CONFIRM_CLASSIFICATION"
    assert entry.target_type == "SourceItem"
    assert entry.detail == {"note": "hello"}
    assert entry.created_at is not None


def test_record_defaults_detail_to_empty_dict(db_session: Session) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    service = AuditService(db_session)

    entry = service.record(
        org_id=org.id,
        actor_id=user.id,
        action_type="CREATE_ACTION",
        target_type="ActionItem",
        target_id=user.id,
    )

    assert entry.detail == {}


def test_record_does_not_commit_rolls_back_with_caller(db_session: Session) -> None:
    """The audit row must be atomic with the caller's transaction.

    ``record`` only flushes, so rolling back the caller's (nested) transaction
    removes the audit row — proving ``record`` never commits on its own.
    """

    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    service = AuditService(db_session)

    savepoint = db_session.begin_nested()
    service.record(
        org_id=org.id,
        actor_id=user.id,
        action_type="CREATE_DECISION",
        target_type="DecisionRecord",
        target_id=user.id,
    )
    assert _count_audit(db_session) == 1
    savepoint.rollback()

    # Rolling back the caller's transaction discarded the (uncommitted) row.
    assert _count_audit(db_session) == 0


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_is_scoped_to_organization(db_session: Session) -> None:
    org_a, user_a = _make_org_and_user(db_session, "Org A", "a@example.com")
    org_b, user_b = _make_org_and_user(db_session, "Org B", "b@example.com")
    service = AuditService(db_session)

    service.record(org_a.id, user_a.id, "CREATE_ACTION", "ActionItem", user_a.id)
    service.record(org_b.id, user_b.id, "CREATE_ACTION", "ActionItem", user_b.id)

    a_entries = service.list(org_a.id)
    assert len(a_entries) == 1
    assert all(e.organization_id == org_a.id for e in a_entries)

    b_entries = service.list(org_b.id)
    assert len(b_entries) == 1
    assert all(e.organization_id == org_b.id for e in b_entries)


def test_list_orders_newest_first(db_session: Session) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    service = AuditService(db_session)

    created = [
        service.record(org.id, user.id, "A", "T", user.id),
        service.record(org.id, user.id, "B", "T", user.id),
        service.record(org.id, user.id, "C", "T", user.id),
    ]

    entries = service.list(org.id)
    # All entries are returned, ...
    assert len(entries) == len(created)
    assert {e.id for e in entries} == {e.id for e in created}
    # ... ordered newest-first: created_at is non-increasing down the list.
    timestamps = [e.created_at for e in entries]
    assert timestamps == sorted(timestamps, reverse=True)


def test_list_filters_by_action_and_target_type(db_session: Session) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    service = AuditService(db_session)

    service.record(org.id, user.id, "CONFIRM_KNOWLEDGE", "KnowledgeItem", user.id)
    service.record(org.id, user.id, "CREATE_ACTION", "ActionItem", user.id)

    by_action = service.list(org.id, AuditFilter(action_type="CREATE_ACTION"))
    assert len(by_action) == 1
    assert by_action[0].action_type == "CREATE_ACTION"

    by_target = service.list(org.id, AuditFilter(target_type="KnowledgeItem"))
    assert len(by_target) == 1
    assert by_target[0].target_type == "KnowledgeItem"


# ---------------------------------------------------------------------------
# GET /api/audit route
# ---------------------------------------------------------------------------


def test_audit_route_requires_auth(client: TestClient) -> None:
    resp = client.get("/api/audit")
    assert resp.status_code == 401


def test_audit_route_returns_org_scoped_entries(
    client: TestClient, db_session: Session, seeded_user: dict[str, Any]
) -> None:
    user = seeded_user["user"]
    org = seeded_user["organization"]

    # Seed an entry for the caller's org and one for a different org.
    AuditService(db_session).record(
        org.id, user.id, "CONFIRM_CLASSIFICATION", "SourceItem", user.id
    )
    other_org, other_user = _make_org_and_user(
        db_session, "Other Org", "other@example.com"
    )
    AuditService(db_session).record(
        other_org.id, other_user.id, "CREATE_ACTION", "ActionItem", other_user.id
    )
    db_session.flush()

    client.post(
        "/api/auth/login",
        json={"email": seeded_user["email"], "password": seeded_user["password"]},
    )
    resp = client.get("/api/audit")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["action_type"] == "CONFIRM_CLASSIFICATION"
    assert body[0]["organization_id"] == str(org.id)
