"""Complementary edge-case unit tests for classification (Requirements 4 and 5).

The core contract is already exercised by ``test_classification_service.py``
(classify -> SUGGESTED, confirm + override, reject-as-signal, single-active
invariant) and ``test_classification_routes.py`` (the same over HTTP). This
module adds *complementary* service-level edge cases that harden the same
requirements without duplicating those assertions:

* **Partial override (Requirement 5.2).** Overriding a single axis leaves the
  other AI-suggested axes intact.
* **Empty / no override (Requirement 5.2).** Confirming with no override (or an
  all-``None`` override) keeps every AI-suggested value verbatim.
* **Single-active invariant under churn (Requirement 4.5).** Repeated
  reject -> reclassify cycles, and repeated classify calls, never leave more
  than one non-rejected result while retaining every rejected row (Requirement
  5.3).

All tests stay pure/service-level against the SQLite ``db_session`` fixture,
mirroring the style of ``test_classification_service.py``.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import models as core_models
from app.core.models import (
    BusinessCategory,
    ClassificationResult,
    Relevance,
    Sensitivity,
    SuggestionStatus,
    SourceStatus,
    SourceType,
)
from app.core.schemas import ClassificationOverride
from app.core.services.ai_provider import ClassificationOutput, EvidenceSpan
from app.core.services.classification_service import ClassificationService
from app.security import hash_password


# ---------------------------------------------------------------------------
# Fixtures / helpers (kept local, mirroring test_classification_service.py)
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
) -> core_models.SourceItem:
    item = core_models.SourceItem(
        organization_id=org.id,
        created_by=user.id,
        source_type=SourceType.EMAIL,
        title="Project Orion milestone update",
        content="The project milestone was approved. Please follow up.",
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


def _count_total(db: Session, item_id) -> int:
    stmt = (
        select(func.count())
        .select_from(ClassificationResult)
        .where(ClassificationResult.source_item_id == item_id)
    )
    return db.execute(stmt).scalar_one()


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
# Override persistence edge cases (Requirement 5.2)
# ---------------------------------------------------------------------------


def test_confirm_single_axis_override_leaves_other_axes_intact(
    db_session: Session,
) -> None:
    """Overriding one axis must not disturb the AI values on the other axes."""

    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    service = ClassificationService(db_session)
    service.classify(
        org.id,
        item.id,
        classifier=_fixed_classifier(
            relevance=Relevance.WORK_RELATED,
            business_category=BusinessCategory.PROJECT,
            sensitivity=Sensitivity.PUBLIC,
        ),
    )

    # Override only the sensitivity axis.
    result = service.confirm(
        org.id,
        item.id,
        actor_id=user.id,
        override=ClassificationOverride(sensitivity=Sensitivity.CONFIDENTIAL),
    )

    assert result.sensitivity == Sensitivity.CONFIDENTIAL  # overridden
    # Untouched axes keep the AI-suggested values (Requirement 5.2).
    assert result.relevance == Relevance.WORK_RELATED
    assert result.business_category == BusinessCategory.PROJECT
    assert result.status == SuggestionStatus.CONFIRMED


def test_confirm_without_override_keeps_all_ai_values(db_session: Session) -> None:
    """Confirming with no override keeps every AI-suggested axis verbatim."""

    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    service = ClassificationService(db_session)
    service.classify(
        org.id,
        item.id,
        classifier=_fixed_classifier(
            relevance=Relevance.WORK_RELATED,
            business_category=BusinessCategory.TASK,
            sensitivity=Sensitivity.INTERNAL,
        ),
    )

    result = service.confirm(org.id, item.id, actor_id=user.id)

    assert result.relevance == Relevance.WORK_RELATED
    assert result.business_category == BusinessCategory.TASK
    assert result.sensitivity == Sensitivity.INTERNAL
    assert result.status == SuggestionStatus.CONFIRMED


def test_confirm_empty_override_object_keeps_all_ai_values(
    db_session: Session,
) -> None:
    """An all-``None`` override object is equivalent to no override."""

    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    service = ClassificationService(db_session)
    service.classify(
        org.id,
        item.id,
        classifier=_fixed_classifier(
            relevance=Relevance.PERSONAL,
            business_category=BusinessCategory.CLIENT,
            sensitivity=Sensitivity.PUBLIC,
        ),
    )

    result = service.confirm(
        org.id,
        item.id,
        actor_id=user.id,
        override=ClassificationOverride(),
    )

    assert result.relevance == Relevance.PERSONAL
    assert result.business_category == BusinessCategory.CLIENT
    assert result.sensitivity == Sensitivity.PUBLIC
    assert result.status == SuggestionStatus.CONFIRMED


def test_confirm_override_all_axes_records_only_changed_axes_in_audit(
    db_session: Session,
) -> None:
    """The audit ``overridden`` detail lists exactly the axes the human changed."""

    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    service = ClassificationService(db_session)
    service.classify(
        org.id,
        item.id,
        classifier=_fixed_classifier(
            relevance=Relevance.WORK_RELATED,
            business_category=BusinessCategory.PROJECT,
            sensitivity=Sensitivity.PUBLIC,
        ),
    )

    # Override two axes to new values; leave business_category matching the AI value.
    service.confirm(
        org.id,
        item.id,
        actor_id=user.id,
        override=ClassificationOverride(
            relevance=Relevance.IRRELEVANT,
            business_category=BusinessCategory.PROJECT,  # unchanged
            sensitivity=Sensitivity.CONFIDENTIAL,
        ),
    )

    audit = db_session.execute(select(core_models.AuditLog)).scalars().all()[-1]
    overridden = audit.detail["overridden"]
    # Only the genuinely changed axes are recorded (business_category matched AI).
    assert set(overridden) == {"relevance", "sensitivity"}
    assert overridden["relevance"] == Relevance.IRRELEVANT.value
    assert overridden["sensitivity"] == Sensitivity.CONFIDENTIAL.value


# ---------------------------------------------------------------------------
# Single-active invariant under churn (Requirements 4.5, 5.3)
# ---------------------------------------------------------------------------


def test_repeated_reject_reclassify_cycles_keep_single_active(
    db_session: Session,
) -> None:
    """Many reject -> reclassify cycles retain history but keep one active row."""

    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    service = ClassificationService(db_session)

    cycles = 4
    for _ in range(cycles):
        service.classify(org.id, item.id, classifier=_fixed_classifier())
        # Exactly one active immediately after each classify.
        assert _count_active(db_session, item.id) == 1
        service.reject(org.id, item.id, actor_id=user.id)
        # Nothing active after a reject.
        assert _count_active(db_session, item.id) == 0

    # One final active suggestion after the cycles.
    service.classify(org.id, item.id, classifier=_fixed_classifier())

    assert _count_active(db_session, item.id) == 1
    # Every prior rejected row is retained as a negative signal (Requirement 5.3).
    assert _count_total(db_session, item.id) == cycles + 1
    rejected = db_session.execute(
        select(func.count())
        .select_from(ClassificationResult)
        .where(ClassificationResult.source_item_id == item.id)
        .where(ClassificationResult.status == SuggestionStatus.REJECTED)
    ).scalar_one()
    assert rejected == cycles


def test_classify_repeatedly_never_yields_two_active(db_session: Session) -> None:
    """Back-to-back classify calls always collapse to a single active row."""

    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    service = ClassificationService(db_session)

    categories = [
        BusinessCategory.TASK,
        BusinessCategory.PROJECT,
        BusinessCategory.CLIENT,
    ]
    for category in categories:
        service.classify(
            org.id,
            item.id,
            classifier=_fixed_classifier(business_category=category),
        )
        # Invariant holds after every classify (Requirement 4.5).
        assert _count_active(db_session, item.id) == 1

    # The single active row reflects the most recent classification.
    active = db_session.execute(
        select(ClassificationResult)
        .where(ClassificationResult.source_item_id == item.id)
        .where(ClassificationResult.status != SuggestionStatus.REJECTED)
    ).scalar_one()
    assert active.business_category == BusinessCategory.CLIENT
    # Prior suggestions were retired to REJECTED, not deleted.
    assert _count_total(db_session, item.id) == len(categories)


def test_confirm_then_reclassify_retires_confirmed_result(
    db_session: Session,
) -> None:
    """Re-classifying a confirmed item retires it and keeps a single active row."""

    org, user = _make_org_and_user(db_session, "Org A", "a@example.com")
    item = _make_item(db_session, org, user)
    service = ClassificationService(db_session)

    service.classify(org.id, item.id, classifier=_fixed_classifier())
    confirmed = service.confirm(org.id, item.id, actor_id=user.id)
    assert confirmed.status == SuggestionStatus.CONFIRMED

    # A fresh classify must retire even a CONFIRMED active result (Requirement 4.5).
    service.classify(org.id, item.id, classifier=_fixed_classifier())

    db_session.refresh(confirmed)
    assert confirmed.status == SuggestionStatus.REJECTED
    assert _count_active(db_session, item.id) == 1
    assert _count_total(db_session, item.id) == 2
