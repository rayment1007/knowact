"""Service-level unit tests for action creation (Requirement 8).

These exercise :class:`~app.core.services.action_service.ActionService` directly
against the ephemeral SQLite ``db_session`` fixture (see ``conftest.py``),
complementing the HTTP/route coverage in ``test_action_routes.py``. They focus
on this task's three points at the service layer:

* **OPEN status on creation** — both a manual action and one created from a
  confirmed suggestion are persisted in status ``OPEN`` (Requirement 8.2);
* **Audit-in-same-transaction** — creating an action writes exactly one
  ``CREATE_ACTION`` audit row, and it is *atomic*: performed inside a nested
  savepoint, the action and its audit row both appear; rolling the savepoint
  back removes **both**, proving they live in one transaction (Requirements 8.1,
  13.1);
* **AI-vs-human origin indicator** — a from-confirmed-suggestion action carries
  ``ai_generated=True`` and inherits the knowledge item's evidence/entity, while
  a manual action carries ``ai_generated=False`` (Requirement 8.5).

An edge case is included: creating from a non-confirmed knowledge item raises
``409`` (Requirement 8.1).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import models as core_models
from app.core.models import (
    ActionItem,
    ActionStatus,
    AuditLog,
    BusinessEntity,
    BusinessEntityType,
    KnowledgeItem,
    SuggestionStatus,
)
from app.core.schemas import ActionCreate
from app.core.services.action_service import CREATE_ACTION, ActionService
from app.security import hash_password


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


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


def _make_entity(
    db: Session, org: core_models.Organization, name: str
) -> BusinessEntity:
    entity = BusinessEntity(
        organization_id=org.id,
        entity_type=BusinessEntityType.PROJECT,
        name=name,
    )
    db.add(entity)
    db.flush()
    return entity


def _make_knowledge(
    db: Session,
    org: core_models.Organization,
    *,
    status: SuggestionStatus = SuggestionStatus.CONFIRMED,
    business_entity_id=None,
    evidence_text: str = "Confirmed suggestion evidence.",
) -> KnowledgeItem:
    knowledge = KnowledgeItem(
        organization_id=org.id,
        business_entity_id=business_entity_id,
        summary="A recommendation",
        evidence_text=evidence_text,
        knowledge_type="FACT",
        status=status,
    )
    db.add(knowledge)
    db.flush()
    return knowledge


def _count_audit(db: Session, target_id) -> int:
    return db.execute(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action_type == CREATE_ACTION,
            AuditLog.target_id == target_id,
        )
    ).scalar_one()


def _count_actions(db: Session, action_id) -> int:
    return db.execute(
        select(func.count()).select_from(ActionItem).where(ActionItem.id == action_id)
    ).scalar_one()


# ---------------------------------------------------------------------------
# OPEN status on creation (Requirement 8.2)
# ---------------------------------------------------------------------------


def test_manual_create_is_open(db_session: Session) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    service = ActionService(db_session)

    action = service.create(
        org.id, user.id, ActionCreate(title="Call the client back")
    )

    assert action.status == ActionStatus.OPEN  # Requirement 8.2
    assert action.id is not None


def test_create_from_confirmed_suggestion_is_open(db_session: Session) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    knowledge = _make_knowledge(db_session, org)
    service = ActionService(db_session)

    action = service.create(
        org.id,
        user.id,
        ActionCreate(title="Prepare proposal", knowledge_item_id=knowledge.id),
    )

    assert action.status == ActionStatus.OPEN  # Requirement 8.2
    assert action.knowledge_item_id == knowledge.id


# ---------------------------------------------------------------------------
# AI-vs-human origin indicator (Requirement 8.5)
# ---------------------------------------------------------------------------


def test_manual_create_is_human_originated(db_session: Session) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    service = ActionService(db_session)

    action = service.create(org.id, user.id, ActionCreate(title="Manual task"))

    assert action.ai_generated is False  # Requirement 8.5


def test_from_confirmed_suggestion_is_ai_generated_and_inherits_evidence(
    db_session: Session,
) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    entity = _make_entity(db_session, org, "Orion")
    knowledge = _make_knowledge(
        db_session,
        org,
        business_entity_id=entity.id,
        evidence_text="Client asked about estate planning.",
    )
    service = ActionService(db_session)

    action = service.create(
        org.id,
        user.id,
        ActionCreate(
            title="Prepare estate planning proposal",
            knowledge_item_id=knowledge.id,
        ),
    )

    # Requirement 8.5: AI-originated, with evidence/entity inherited from the
    # confirmed knowledge item.
    assert action.ai_generated is True
    assert action.evidence_text == "Client asked about estate planning."
    assert action.business_entity_id == entity.id


# ---------------------------------------------------------------------------
# Audit-in-same-transaction / atomicity (Requirements 8.1, 13.1)
# ---------------------------------------------------------------------------


def test_manual_create_writes_exactly_one_create_action_audit(
    db_session: Session,
) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    service = ActionService(db_session)

    action = service.create(org.id, user.id, ActionCreate(title="Manual task"))

    assert _count_audit(db_session, action.id) == 1  # Requirements 8.1, 13.1


def test_from_suggestion_writes_exactly_one_create_action_audit(
    db_session: Session,
) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    knowledge = _make_knowledge(db_session, org)
    service = ActionService(db_session)

    action = service.create(
        org.id,
        user.id,
        ActionCreate(title="Prepare proposal", knowledge_item_id=knowledge.id),
    )

    audit_rows = db_session.execute(
        select(AuditLog).where(
            AuditLog.action_type == CREATE_ACTION,
            AuditLog.target_id == action.id,
        )
    ).scalars().all()
    assert len(audit_rows) == 1  # Requirements 8.1, 13.1
    assert audit_rows[0].target_type == "ActionItem"
    assert audit_rows[0].actor_id == user.id
    assert audit_rows[0].detail["ai_generated"] is True


def test_action_and_audit_row_commit_or_rollback_together(
    db_session: Session,
) -> None:
    """The action and its audit row share one transaction (Requirements 8.1, 13.1).

    Creating inside a nested savepoint yields exactly one audit row alongside the
    action; rolling the savepoint back removes **both**, proving they are written
    atomically in the same transaction rather than independently.
    """

    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    service = ActionService(db_session)

    savepoint = db_session.begin_nested()
    action = service.create(org.id, user.id, ActionCreate(title="Atomic task"))
    action_id = action.id

    # Within the savepoint: both the action and its single audit row exist.
    assert _count_actions(db_session, action_id) == 1
    assert _count_audit(db_session, action_id) == 1

    # Rolling back the savepoint must remove both — they live in one transaction.
    savepoint.rollback()

    assert _count_actions(db_session, action_id) == 0
    assert _count_audit(db_session, action_id) == 0


# ---------------------------------------------------------------------------
# Edge: creating from a non-confirmed suggestion is refused (Requirement 8.1)
# ---------------------------------------------------------------------------


def test_create_from_unconfirmed_suggestion_raises_409(db_session: Session) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    knowledge = _make_knowledge(
        db_session, org, status=SuggestionStatus.SUGGESTED
    )
    service = ActionService(db_session)

    with pytest.raises(HTTPException) as exc:
        service.create(
            org.id,
            user.id,
            ActionCreate(title="Too early", knowledge_item_id=knowledge.id),
        )

    assert exc.value.status_code == 409  # Requirement 8.1
