"""Tests for the BriefService daily/entity briefs and the Learn loop (Req 10).

Covers task 12.1's contract:

* ``daily_brief`` persists a ``DAILY`` :class:`Brief` for the user with a
  headline and the structured content sections, each referencing evidence
  (Requirements 10.1, 10.3, 10.4);
* context is assembled from **only** confirmed knowledge — a ``SUGGESTED``
  knowledge item must never appear in a brief (Requirement 10.2);
* open actions feed the brief while ``DONE`` actions do not (Requirement 10.1);
* ``entity_brief`` persists an ``ENTITY`` brief scoped to the entity from that
  entity's confirmed knowledge and open actions (Requirements 10.4, 10.5);
* organization scoping: an entity brief for another tenant's entity yields
  ``404`` (Requirement 2.3).
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import models as core_models
from app.core.models import (
    ActionItem,
    ActionStatus,
    Brief,
    BusinessEntity,
    BusinessEntityType,
    DecisionRecord,
    KnowledgeItem,
    SuggestionStatus,
)
from app.core.services.brief_service import BriefService
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
    db: Session,
    org: core_models.Organization,
    name: str = "Project Orion",
    entity_type: BusinessEntityType = BusinessEntityType.PROJECT,
) -> BusinessEntity:
    entity = BusinessEntity(
        organization_id=org.id,
        entity_type=entity_type,
        name=name,
        description="A strategic project.",
    )
    db.add(entity)
    db.flush()
    return entity


def _make_knowledge(
    db: Session,
    org: core_models.Organization,
    summary: str,
    status: SuggestionStatus = SuggestionStatus.CONFIRMED,
    entity_id=None,
    evidence_text: str = "Supporting evidence text.",
) -> KnowledgeItem:
    item = KnowledgeItem(
        organization_id=org.id,
        business_entity_id=entity_id,
        summary=summary,
        key_points=["kp1"],
        evidence_text=evidence_text,
        knowledge_type="FACT",
        status=status,
    )
    db.add(item)
    db.flush()
    return item


def _make_action(
    db: Session,
    org: core_models.Organization,
    title: str,
    status: ActionStatus = ActionStatus.OPEN,
    entity_id=None,
) -> ActionItem:
    action = ActionItem(
        organization_id=org.id,
        business_entity_id=entity_id,
        title=title,
        status=status,
        due_date=date(2030, 1, 1),
    )
    db.add(action)
    db.flush()
    return action


def _make_decision(
    db: Session,
    org: core_models.Organization,
    user: core_models.User,
    title: str,
    entity_id=None,
) -> DecisionRecord:
    decision = DecisionRecord(
        organization_id=org.id,
        business_entity_id=entity_id,
        title=title,
        decision="We will proceed.",
        rationale="It is the best option.",
        evidence_text="Decision evidence.",
        decided_by=user.id,
    )
    db.add(decision)
    db.flush()
    return decision


# ---------------------------------------------------------------------------
# daily_brief
# ---------------------------------------------------------------------------


def test_daily_brief_persists_daily_brief_with_content(db_session: Session) -> None:
    """A daily brief is persisted as DAILY for the user with content sections."""

    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    _make_knowledge(db_session, org, "Milestone approved for the roadmap.")
    _make_action(db_session, org, "Send the updated proposal")
    _make_decision(db_session, org, user, "Adopt the new vendor")

    service = BriefService(db_session)
    brief = service.daily_brief(org.id, user.id)

    assert brief.id is not None
    assert brief.scope == "DAILY"
    assert brief.scope_ref_id is None
    assert brief.generated_for == user.id
    assert brief.organization_id == org.id

    content = brief.content
    assert content["headline"]
    # All five structured sections are present (Requirement 10.3).
    for section in ("priorities", "recommended_actions", "follow_ups", "risks"):
        assert section in content
    assert len(content["priorities"]) == 1
    assert len(content["recommended_actions"]) == 1
    # Each brief line references its source evidence (Requirement 10.4).
    assert content["priorities"][0]["evidence_ref"]
    assert content["recommended_actions"][0]["evidence_text"]

    # Persisted and retrievable.
    stored = db_session.get(Brief, brief.id)
    assert stored is not None and stored.scope == "DAILY"


def test_daily_brief_resolves_readable_evidence_text(db_session: Session) -> None:
    """Each brief line exposes human-readable evidence, never a bare id (10.4)."""

    org, user = _make_org_and_user(db_session, "Org Ev", "ev@example.com")
    _make_knowledge(
        db_session,
        org,
        "Milestone approved for the roadmap.",
        evidence_text="Client confirmed the roadmap milestone on the call.",
    )

    service = BriefService(db_session)
    brief = service.daily_brief(org.id, user.id)

    priorities = brief.content["priorities"]
    assert priorities, "expected at least one priority line"
    line = priorities[0]
    # evidence_ref is preserved for traceability (still the source id) ...
    assert line["evidence_ref"]
    # ... but evidence_text is the resolved, human-readable snippet, never a UUID.
    assert line["evidence_text"] == "Client confirmed the roadmap milestone on the call."
    import uuid as _uuid

    with pytest.raises(ValueError):
        _uuid.UUID(line["evidence_text"])


def test_daily_brief_hides_unresolvable_id_evidence(db_session: Session) -> None:
    """A brief line whose id-shaped ref can't be resolved surfaces no id (10.4)."""

    from uuid import uuid4

    from app.core.services.ai_provider import (
        BriefLine,
        DailyBriefOutput,
        OrgContext,
    )

    org, user = _make_org_and_user(db_session, "Org Un", "un@example.com")

    # A generator that references an id which is NOT in the confirmed context.
    stray_id = str(uuid4())

    def generator(_context: OrgContext) -> DailyBriefOutput:
        return DailyBriefOutput(
            headline="Today",
            priorities=[BriefLine(text="Something", evidence_ref=stray_id)],
        )

    service = BriefService(db_session)
    brief = service.daily_brief(org.id, user.id, generator=generator)

    line = brief.content["priorities"][0]
    # The opaque, unresolvable id is never surfaced as readable evidence text.
    assert line["evidence_text"] is None


def test_daily_brief_excludes_suggested_knowledge(db_session: Session) -> None:
    """Only CONFIRMED knowledge feeds the brief; SUGGESTED must not appear (10.2)."""

    org, user = _make_org_and_user(db_session, "Org B", "b@example.com")
    confirmed = _make_knowledge(
        db_session, org, "Confirmed fact", status=SuggestionStatus.CONFIRMED
    )
    _make_knowledge(
        db_session, org, "Unconfirmed suggestion", status=SuggestionStatus.SUGGESTED
    )
    _make_knowledge(
        db_session, org, "Rejected item", status=SuggestionStatus.REJECTED
    )

    service = BriefService(db_session)
    brief = service.daily_brief(org.id, user.id)

    priority_refs = {p["evidence_ref"] for p in brief.content["priorities"]}
    # Exactly the confirmed item is present as a priority.
    assert priority_refs == {str(confirmed.id)}


def test_daily_brief_excludes_closed_actions(db_session: Session) -> None:
    """Open actions feed the brief; DONE/CANCELLED actions do not (10.1)."""

    org, user = _make_org_and_user(db_session, "Org C", "c@example.com")
    _make_action(db_session, org, "Open task", status=ActionStatus.OPEN)
    _make_action(db_session, org, "In progress task", status=ActionStatus.IN_PROGRESS)
    _make_action(db_session, org, "Finished task", status=ActionStatus.DONE)
    _make_action(db_session, org, "Cancelled task", status=ActionStatus.CANCELLED)

    service = BriefService(db_session)
    brief = service.daily_brief(org.id, user.id)

    titles = {a["title"] for a in brief.content["recommended_actions"]}
    assert titles == {"Open task", "In progress task"}


# ---------------------------------------------------------------------------
# entity_brief
# ---------------------------------------------------------------------------


def test_entity_brief_persists_entity_scoped_brief(db_session: Session) -> None:
    """An entity brief is persisted as ENTITY, scoped to the entity (10.4, 10.5)."""

    org, user = _make_org_and_user(db_session, "Org D", "d@example.com")
    entity = _make_entity(db_session, org)
    other_entity = _make_entity(db_session, org, name="Other", )

    # Knowledge/actions on the target entity plus noise on another entity.
    k = _make_knowledge(
        db_session, org, "Entity knowledge", entity_id=entity.id
    )
    _make_knowledge(db_session, org, "Other entity knowledge", entity_id=other_entity.id)
    _make_action(db_session, org, "Entity action", entity_id=entity.id)
    _make_action(db_session, org, "Other entity action", entity_id=other_entity.id)

    service = BriefService(db_session)
    brief = service.entity_brief(org.id, entity.id, user.id)

    assert brief.scope == "ENTITY"
    assert brief.scope_ref_id == entity.id
    assert brief.generated_for == user.id

    content = brief.content
    assert content["entity_id"] == str(entity.id)
    # Only the target entity's confirmed knowledge/actions are included.
    knowledge_refs = {b["evidence_ref"] for b in content["recent_knowledge"]}
    assert knowledge_refs == {str(k.id)}
    action_titles = {b["text"] for b in content["open_actions"]}
    assert action_titles == {"Entity action"}
    # Recommended next steps reference evidence (Requirement 10.4).
    assert content["recommended_next_steps"][0]["evidence_text"]


def test_entity_brief_excludes_suggested_knowledge(db_session: Session) -> None:
    """Entity brief context is confirmed-only (Requirement 10.2)."""

    org, user = _make_org_and_user(db_session, "Org E", "e@example.com")
    entity = _make_entity(db_session, org)
    confirmed = _make_knowledge(
        db_session, org, "Confirmed", entity_id=entity.id,
        status=SuggestionStatus.CONFIRMED,
    )
    _make_knowledge(
        db_session, org, "Suggested", entity_id=entity.id,
        status=SuggestionStatus.SUGGESTED,
    )

    service = BriefService(db_session)
    brief = service.entity_brief(org.id, entity.id, user.id)

    refs = {b["evidence_ref"] for b in brief.content["recent_knowledge"]}
    assert refs == {str(confirmed.id)}


def test_entity_brief_cross_org_is_404(db_session: Session) -> None:
    """An entity brief for another tenant's entity yields 404 (Requirement 2.3)."""

    org_a, user_a = _make_org_and_user(db_session, "Org F", "f@example.com")
    org_b, _user_b = _make_org_and_user(db_session, "Org G", "g@example.com")
    entity_b = _make_entity(db_session, org_b)

    service = BriefService(db_session)
    with pytest.raises(HTTPException) as exc:
        service.entity_brief(org_a.id, entity_b.id, user_a.id)
    assert exc.value.status_code == 404


def test_daily_brief_content_is_json_serializable(db_session: Session) -> None:
    """The persisted content survives a round-trip through the DB (JSONB)."""

    org, user = _make_org_and_user(db_session, "Org H", "h@example.com")
    _make_knowledge(db_session, org, "A confirmed fact")

    service = BriefService(db_session)
    brief = service.daily_brief(org.id, user.id)

    db_session.expire(brief)
    reloaded = db_session.get(Brief, brief.id)
    assert reloaded is not None
    assert isinstance(reloaded.content, dict)
    assert reloaded.content["headline"]
