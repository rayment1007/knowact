"""Tests for the ClassificationService (Requirements 4 and 5).

Covers the service's core contract:

* ``classify`` persists a ``ClassificationResult`` in status ``SUGGESTED`` and
  never auto-confirms (Requirements 4.1, 4.2, 5.5);
* ``confirm`` sets the result ``CONFIRMED`` + the source item ``CLASSIFIED`` and
  writes exactly one ``CONFIRM_CLASSIFICATION`` audit row in the same
  transaction (Requirements 5.1, 5.4);
* ``confirm`` applies a human override per axis (Requirement 5.2);
* ``reject`` sets the result ``REJECTED`` and retains it (Requirement 5.3);
* at most one non-rejected result exists per item at any time (Requirement 4.5);
* organization scoping: cross-tenant access yields ``404`` (Requirement 2.3).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import models as core_models
from app.core.models import (
    AuditLog,
    BusinessCategory,
    ClassificationResult,
    Relevance,
    Sensitivity,
    SourceStatus,
    SourceType,
    SuggestionStatus,
)
from app.core.schemas import ClassificationOverride
from app.core.services.ai_provider import (
    ClassificationOutput,
    EvidenceSpan,
)
from app.core.services.classification_service import (
    CONFIRM_CLASSIFICATION,
    ClassificationService,
)
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


def _count_active(db: Session, item_id) -> int:
    stmt = (
        select(func.count())
        .select_from(ClassificationResult)
        .where(ClassificationResult.source_item_id == item_id)
        .where(ClassificationResult.status != SuggestionStatus.REJECTED)
    )
    return db.execute(stmt).scalar_one()


def _count_audit(db: Session) -> int:
    return db.execute(select(func.count()).select_from(AuditLog)).scalar_one()


def _fixed_classifier(
    relevance: Relevance = Relevance.WORK_RELATED,
    business_category: BusinessCategory = BusinessCategory.PROJECT,
    sensitivity: Sensitivity = Sensitivity.PUBLIC,
    confidence: float = 0.8,
):
    """A deterministic classifier stub returning a fixed output."""

    def _classify(content: str, title: str) -> ClassificationOutput:
        return ClassificationOutput(
            relevance=relevance,
            business_category=business_category,
            sensitivity=sensitivity,
            confidence=confidence,
            reasons=["fixed reason"],
            evidence_spans=[EvidenceSpan(text="milestone", start=4, end=13)],
        )

    return _classify


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


def test_classify_persists_suggested_result(db_session: Session) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    service = ClassificationService(db_session)

    result = service.classify(org.id, item.id, classifier=_fixed_classifier())

    assert result.id is not None
    assert result.source_item_id == item.id
    assert result.status == SuggestionStatus.SUGGESTED  # never auto-confirmed
    assert result.business_category == BusinessCategory.PROJECT
    assert 0.0 <= result.confidence <= 1.0
    assert result.reasons == ["fixed reason"]
    # Source item is untouched until a human confirms.
    assert item.status == SourceStatus.NEW


def test_classify_with_default_mock_provider(db_session: Session) -> None:
    """When no classifier is supplied, the deterministic mock is used."""

    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    service = ClassificationService(db_session)

    result = service.classify(org.id, item.id)

    assert result.status == SuggestionStatus.SUGGESTED
    assert 0.0 <= result.confidence <= 1.0
    assert result.reasons  # mock produces evidence-backed reasons


def test_classify_cross_org_is_404(db_session: Session) -> None:
    org_a, user_a = _make_org_and_user(db_session, "Org A", "a@example.com")
    org_b, _user_b = _make_org_and_user(db_session, "Org B", "b@example.com")
    item = _make_item(db_session, org_a, user_a)
    service = ClassificationService(db_session)

    with pytest.raises(HTTPException) as exc:
        service.classify(org_b.id, item.id, classifier=_fixed_classifier())
    assert exc.value.status_code == 404


def test_reclassify_rejects_previous_and_keeps_single_active(
    db_session: Session,
) -> None:
    """Re-classifying retires the old active result and keeps exactly one."""

    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    service = ClassificationService(db_session)

    first = service.classify(
        org.id,
        item.id,
        classifier=_fixed_classifier(business_category=BusinessCategory.TASK),
    )
    second = service.classify(
        org.id,
        item.id,
        classifier=_fixed_classifier(business_category=BusinessCategory.PROJECT),
    )

    db_session.refresh(first)
    assert first.id != second.id
    # Old result retained as a negative signal, new one is active.
    assert first.status == SuggestionStatus.REJECTED
    assert second.status == SuggestionStatus.SUGGESTED
    assert _count_active(db_session, item.id) == 1  # Requirement 4.5


# ---------------------------------------------------------------------------
# confirm
# ---------------------------------------------------------------------------


def test_confirm_sets_confirmed_classified_and_one_audit(db_session: Session) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    service = ClassificationService(db_session)
    service.classify(org.id, item.id, classifier=_fixed_classifier())

    before = _count_audit(db_session)
    result = service.confirm(org.id, item.id, actor_id=user.id)

    assert result.status == SuggestionStatus.CONFIRMED  # Requirement 5.1
    assert item.status == SourceStatus.CLASSIFIED  # Requirement 5.1
    # Exactly one CONFIRM_CLASSIFICATION audit row in the same transaction.
    assert _count_audit(db_session) == before + 1  # Requirement 5.4
    audit = db_session.execute(select(AuditLog)).scalars().all()[-1]
    assert audit.action_type == CONFIRM_CLASSIFICATION
    assert audit.target_type == "SourceItem"
    assert audit.target_id == item.id
    assert audit.organization_id == org.id
    assert audit.actor_id == user.id


def test_confirm_persists_override(db_session: Session) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    service = ClassificationService(db_session)
    service.classify(
        org.id,
        item.id,
        classifier=_fixed_classifier(business_category=BusinessCategory.PROJECT),
    )

    result = service.confirm(
        org.id,
        item.id,
        actor_id=user.id,
        override=ClassificationOverride(business_category=BusinessCategory.CLIENT),
    )

    # Human-selected category replaces the AI-suggested one (Requirement 5.2).
    assert result.business_category == BusinessCategory.CLIENT
    assert result.status == SuggestionStatus.CONFIRMED


def test_confirm_without_classification_is_404(db_session: Session) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    service = ClassificationService(db_session)

    with pytest.raises(HTTPException) as exc:
        service.confirm(org.id, item.id, actor_id=user.id)
    assert exc.value.status_code == 404


def test_confirm_atomic_audit_rolls_back_with_caller(db_session: Session) -> None:
    """The confirm mutation and its audit row are atomic (Requirement 5.4)."""

    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    service = ClassificationService(db_session)
    service.classify(org.id, item.id, classifier=_fixed_classifier())

    savepoint = db_session.begin_nested()
    service.confirm(org.id, item.id, actor_id=user.id)
    assert _count_audit(db_session) == 1
    savepoint.rollback()

    # Rolling back removes both the status change and the audit row together.
    assert _count_audit(db_session) == 0


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------


def test_reject_sets_rejected_and_retains_row(db_session: Session) -> None:
    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    service = ClassificationService(db_session)
    created = service.classify(org.id, item.id, classifier=_fixed_classifier())

    result = service.reject(org.id, item.id, actor_id=user.id)

    assert result.id == created.id
    assert result.status == SuggestionStatus.REJECTED  # Requirement 5.3
    # Row is retained (not deleted) as a negative signal.
    total = db_session.execute(
        select(func.count())
        .select_from(ClassificationResult)
        .where(ClassificationResult.source_item_id == item.id)
    ).scalar_one()
    assert total == 1
    assert _count_active(db_session, item.id) == 0


def test_classify_after_reject_allows_new_active_result(db_session: Session) -> None:
    """A rejected result coexists with a fresh active suggestion (Req 4.5/5.3)."""

    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    service = ClassificationService(db_session)

    service.classify(org.id, item.id, classifier=_fixed_classifier())
    service.reject(org.id, item.id, actor_id=user.id)
    service.classify(org.id, item.id, classifier=_fixed_classifier())

    # Two rows total (one REJECTED, one SUGGESTED); exactly one active.
    total = db_session.execute(
        select(func.count())
        .select_from(ClassificationResult)
        .where(ClassificationResult.source_item_id == item.id)
    ).scalar_one()
    assert total == 2
    assert _count_active(db_session, item.id) == 1


def test_reject_cross_org_is_404(db_session: Session) -> None:
    org_a, user_a = _make_org_and_user(db_session, "Org A", "a@example.com")
    org_b, user_b = _make_org_and_user(db_session, "Org B", "b@example.com")
    item = _make_item(db_session, org_a, user_a)
    service = ClassificationService(db_session)
    service.classify(org_a.id, item.id, classifier=_fixed_classifier())

    with pytest.raises(HTTPException) as exc:
        service.reject(org_b.id, item.id, actor_id=user_b.id)
    assert exc.value.status_code == 404
