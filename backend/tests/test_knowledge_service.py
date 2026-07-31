"""Tests for the KnowledgeService extraction path and safety gates (Req 6).

Covers task 10.1's contract:

* the privacy gate refuses extraction (``409``) and persists **nothing** for
  every excluded relevance (Requirement 6.3);
* the sensitivity gate refuses (``409``) for ``HIGHLY_SENSITIVE`` without an
  acknowledgment and proceeds with ``acknowledged=True`` (Requirements 6.4, 6.5);
* a successful extraction persists a ``SUGGESTED`` :class:`KnowledgeItem` with
  non-empty ``evidence_text`` and never auto-confirms (Requirements 6.2, 6.6);
* ``link_or_create_entity`` reuses an existing entity and creates a new one when
  no match exists (Requirement 6.7);
* organization scoping: cross-tenant access yields ``404`` (Requirement 2.3);
* extraction is refused (``409``) when the item has no confirmed classification
  (Requirement 6.1).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import models as core_models
from app.core.models import (
    BusinessCategory,
    BusinessEntity,
    BusinessEntityType,
    ClassificationResult,
    KnowledgeItem,
    Relevance,
    Sensitivity,
    SourceStatus,
    SourceType,
    SuggestionStatus,
)
from app.core.services.ai_provider import EntitySuggestion, KnowledgeOutput
from app.core.services.knowledge_service import KnowledgeService
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


def _make_item(
    db: Session,
    org: core_models.Organization,
    user: core_models.User,
    title: str = "Project Orion milestone update",
    content: str = "The project milestone was approved. Please follow up.",
) -> core_models.SourceItem:
    item = core_models.SourceItem(
        organization_id=org.id,
        created_by=user.id,
        source_type=SourceType.EMAIL,
        title=title,
        content=content,
        status=SourceStatus.NEW,
    )
    db.add(item)
    db.flush()
    return item


def _classify(
    db: Session,
    item: core_models.SourceItem,
    relevance: Relevance = Relevance.WORK_RELATED,
    sensitivity: Sensitivity = Sensitivity.PUBLIC,
    status: SuggestionStatus = SuggestionStatus.CONFIRMED,
) -> ClassificationResult:
    """Persist a classification for ``item`` (CONFIRMED by default)."""

    result = ClassificationResult(
        source_item_id=item.id,
        relevance=relevance,
        business_category=BusinessCategory.PROJECT,
        sensitivity=sensitivity,
        confidence=0.9,
        reasons=["reason"],
        evidence_spans=[],
        status=status,
    )
    db.add(result)
    db.flush()
    return result


def _fixed_extractor(
    summary: str = "Milestone approved.",
    evidence_text: str = "The project milestone was approved.",
    suggested_entities: list[EntitySuggestion] | None = None,
):
    """A deterministic extractor stub returning a fixed KnowledgeOutput."""

    def _extract(content: str, context) -> KnowledgeOutput:
        return KnowledgeOutput(
            summary=summary,
            key_points=["Milestone approved"],
            suggested_entities=suggested_entities or [],
            evidence_text=evidence_text,
            knowledge_type="FACT",
        )

    return _extract


def _count_knowledge(db: Session) -> int:
    return db.execute(select(func.count()).select_from(KnowledgeItem)).scalar_one()


# ---------------------------------------------------------------------------
# Privacy gate (Requirement 6.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relevance",
    [
        Relevance.PERSONAL,
        Relevance.IRRELEVANT,
        Relevance.SPAM,
        Relevance.SYSTEM_NOTIFICATION,
    ],
)
def test_privacy_gate_refuses_and_persists_nothing(
    db_session: Session, relevance: Relevance
) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    _classify(db_session, item, relevance=relevance)
    service = KnowledgeService(db_session)

    before = _count_knowledge(db_session)
    with pytest.raises(HTTPException) as exc:
        service.extract(org.id, item.id, extractor=_fixed_extractor())

    assert exc.value.status_code == 409  # Requirement 6.3
    assert _count_knowledge(db_session) == before  # nothing persisted


# ---------------------------------------------------------------------------
# Sensitivity gate (Requirements 6.4, 6.5)
# ---------------------------------------------------------------------------


def test_sensitivity_gate_refuses_without_acknowledgment(db_session: Session) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    _classify(db_session, item, sensitivity=Sensitivity.HIGHLY_SENSITIVE)
    service = KnowledgeService(db_session)

    before = _count_knowledge(db_session)
    with pytest.raises(HTTPException) as exc:
        service.extract(
            org.id, item.id, extractor=_fixed_extractor(), acknowledged=False
        )

    assert exc.value.status_code == 409  # Requirement 6.4
    assert _count_knowledge(db_session) == before  # nothing persisted


def test_sensitivity_gate_proceeds_with_acknowledgment(db_session: Session) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    _classify(db_session, item, sensitivity=Sensitivity.HIGHLY_SENSITIVE)
    service = KnowledgeService(db_session)

    knowledge = service.extract(
        org.id, item.id, extractor=_fixed_extractor(), acknowledged=True
    )

    # Requirement 6.5: extraction proceeds with the explicit acknowledgment.
    assert knowledge.id is not None
    assert knowledge.status == SuggestionStatus.SUGGESTED


# ---------------------------------------------------------------------------
# Successful extraction (Requirements 6.2, 6.6)
# ---------------------------------------------------------------------------


def test_extract_persists_suggested_with_evidence(db_session: Session) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    _classify(db_session, item)
    service = KnowledgeService(db_session)

    knowledge = service.extract(org.id, item.id, extractor=_fixed_extractor())

    assert knowledge.status == SuggestionStatus.SUGGESTED  # never auto-confirmed
    assert knowledge.source_item_id == item.id
    assert knowledge.organization_id == org.id
    assert knowledge.evidence_text  # Requirement 6.6: non-empty
    assert knowledge.summary == "Milestone approved."


def test_extract_falls_back_to_content_when_evidence_empty(db_session: Session) -> None:
    """A provider returning empty evidence still yields a non-empty field."""

    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user, content="Non-empty source content.")
    _classify(db_session, item)
    service = KnowledgeService(db_session)

    knowledge = service.extract(
        org.id, item.id, extractor=_fixed_extractor(evidence_text="")
    )

    assert knowledge.evidence_text == "Non-empty source content."  # Requirement 6.6


def test_extract_with_default_mock_provider(db_session: Session) -> None:
    """When no extractor is supplied, the deterministic mock is used."""

    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    _classify(db_session, item)
    service = KnowledgeService(db_session)

    knowledge = service.extract(org.id, item.id)

    assert knowledge.status == SuggestionStatus.SUGGESTED
    assert knowledge.evidence_text


def test_extract_links_entity_from_suggestion(db_session: Session) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    _classify(db_session, item)
    service = KnowledgeService(db_session)

    suggestion = EntitySuggestion(
        name="Project Orion", entity_type=BusinessEntityType.PROJECT, confidence=0.9
    )
    knowledge = service.extract(
        org.id,
        item.id,
        extractor=_fixed_extractor(suggested_entities=[suggestion]),
    )

    assert knowledge.business_entity_id is not None
    entity = db_session.get(BusinessEntity, knowledge.business_entity_id)
    assert entity is not None
    assert entity.name == "Project Orion"
    assert entity.organization_id == org.id


# ---------------------------------------------------------------------------
# Confirmed-classification precondition (Requirement 6.1)
# ---------------------------------------------------------------------------


def test_extract_without_confirmed_classification_is_409(db_session: Session) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    # Only a SUGGESTED classification exists — not yet confirmed.
    _classify(db_session, item, status=SuggestionStatus.SUGGESTED)
    service = KnowledgeService(db_session)

    with pytest.raises(HTTPException) as exc:
        service.extract(org.id, item.id, extractor=_fixed_extractor())
    assert exc.value.status_code == 409


def test_extract_no_classification_at_all_is_409(db_session: Session) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    service = KnowledgeService(db_session)

    with pytest.raises(HTTPException) as exc:
        service.extract(org.id, item.id, extractor=_fixed_extractor())
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# Organization scoping (Requirement 2.3)
# ---------------------------------------------------------------------------


def test_extract_cross_org_is_404(db_session: Session) -> None:
    org_a, user_a = _make_org_and_user(db_session, "Org A", "a@example.com")
    org_b, _user_b = _make_org_and_user(db_session, "Org B", "b@example.com")
    item = _make_item(db_session, org_a, user_a)
    _classify(db_session, item)
    service = KnowledgeService(db_session)

    with pytest.raises(HTTPException) as exc:
        service.extract(org_b.id, item.id, extractor=_fixed_extractor())
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# link_or_create_entity (Requirement 6.7)
# ---------------------------------------------------------------------------


def test_link_or_create_entity_creates_when_no_match(db_session: Session) -> None:
    org, _user = _make_org_and_user(db_session, "Org A", "a@example.com")
    service = KnowledgeService(db_session)

    suggestion = EntitySuggestion(
        name="New Client", entity_type=BusinessEntityType.CLIENT, confidence=0.8
    )
    entity = service.link_or_create_entity(org.id, suggestion)

    assert entity.id is not None
    assert entity.name == "New Client"
    assert entity.entity_type == BusinessEntityType.CLIENT
    assert entity.organization_id == org.id


def test_link_or_create_entity_reuses_existing_match(db_session: Session) -> None:
    org, _user = _make_org_and_user(db_session, "Org A", "a@example.com")
    existing = BusinessEntity(
        organization_id=org.id,
        entity_type=BusinessEntityType.PROJECT,
        name="Project Orion",
    )
    db_session.add(existing)
    db_session.flush()
    service = KnowledgeService(db_session)

    suggestion = EntitySuggestion(
        name="Project Orion", entity_type=BusinessEntityType.PROJECT, confidence=0.9
    )
    entity = service.link_or_create_entity(org.id, suggestion)

    assert entity.id == existing.id  # reused, not duplicated
    total = db_session.execute(
        select(func.count()).select_from(BusinessEntity)
    ).scalar_one()
    assert total == 1


def test_link_or_create_entity_type_mismatch_creates_new(db_session: Session) -> None:
    """Same name but a different type is a distinct entity."""

    org, _user = _make_org_and_user(db_session, "Org A", "a@example.com")
    existing = BusinessEntity(
        organization_id=org.id,
        entity_type=BusinessEntityType.PROJECT,
        name="Orion",
    )
    db_session.add(existing)
    db_session.flush()
    service = KnowledgeService(db_session)

    suggestion = EntitySuggestion(
        name="Orion", entity_type=BusinessEntityType.CLIENT, confidence=0.7
    )
    entity = service.link_or_create_entity(org.id, suggestion)

    assert entity.id != existing.id
    total = db_session.execute(
        select(func.count()).select_from(BusinessEntity)
    ).scalar_one()
    assert total == 2


def test_link_or_create_entity_is_org_scoped(db_session: Session) -> None:
    """A matching name+type in another org is not reused."""

    org_a, _user_a = _make_org_and_user(db_session, "Org A", "a@example.com")
    org_b, _user_b = _make_org_and_user(db_session, "Org B", "b@example.com")
    other = BusinessEntity(
        organization_id=org_b.id,
        entity_type=BusinessEntityType.PROJECT,
        name="Shared Name",
    )
    db_session.add(other)
    db_session.flush()
    service = KnowledgeService(db_session)

    suggestion = EntitySuggestion(
        name="Shared Name", entity_type=BusinessEntityType.PROJECT, confidence=0.9
    )
    entity = service.link_or_create_entity(org_a.id, suggestion)

    # A new entity is created in Org A rather than reusing Org B's row.
    assert entity.id != other.id
    assert entity.organization_id == org_a.id
