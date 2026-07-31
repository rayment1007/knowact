"""Property-based test P4: the sensitivity gate blocks unacknowledged extraction.

Property 4 (sensitivity gate): for *any* source item whose **CONFIRMED**
classification carries ``sensitivity == HIGHLY_SENSITIVE``, calling
:meth:`KnowledgeService.extract` with ``acknowledged=False`` must refuse with
``409 Conflict`` **and persist no** :class:`KnowledgeItem` (Requirements 6.4,
6.5). To isolate the sensitivity gate from the privacy gate (Requirement 6.3),
every generated item uses the only non-excluded relevance, ``WORK_RELATED`` — so
the *only* thing that can refuse extraction is the acknowledgment requirement.

As a paired check that the gate is specifically about acknowledgment (rather
than blocking highly-sensitive content outright), each example also verifies
that ``acknowledged=True`` on the same kind of item *does* create a
``SUGGESTED`` knowledge item.

The service is exercised end-to-end against the function-scoped in-memory
``db_session`` fixture; because Hypothesis drives many examples through that one
fixture, ``HealthCheck.function_scoped_fixture`` is suppressed and a fresh
org/user/item is created per example (with unique identifiers) so examples never
collide.

**Validates: Requirements 6.4, 6.5**
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import models as core_models
from app.core.models import (
    BusinessCategory,
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

# bcrypt hashing is ~200ms/call; the stored hash is never re-verified here, so
# hash the throwaway password once per module instead of per Hypothesis example.
_PW_HASH = hash_password("pw")


# ---------------------------------------------------------------------------
# Helpers (mirroring tests/test_knowledge_service.py) — fresh, unique rows per
# example so many Hypothesis iterations can share the one function-scoped
# db_session without colliding on unique columns (e.g. user email).
# ---------------------------------------------------------------------------


def _make_org_and_user(
    db: Session,
) -> tuple[core_models.Organization, core_models.User]:
    suffix = uuid4().hex
    org = core_models.Organization(name=f"Org {suffix}")
    db.add(org)
    db.flush()
    user = core_models.User(
        organization_id=org.id,
        email=f"user-{suffix}@example.com",
        full_name="Actor",
        password_hash=_PW_HASH,
        role="ADMIN",
    )
    db.add(user)
    db.flush()
    return org, user


def _make_item(
    db: Session,
    org: core_models.Organization,
    user: core_models.User,
    content: str,
) -> core_models.SourceItem:
    item = core_models.SourceItem(
        organization_id=org.id,
        created_by=user.id,
        source_type=SourceType.EMAIL,
        title="Highly sensitive update",
        content=content,
        status=SourceStatus.NEW,
    )
    db.add(item)
    db.flush()
    return item


def _classify_highly_sensitive(
    db: Session,
    item: core_models.SourceItem,
    business_category: BusinessCategory,
) -> ClassificationResult:
    """Persist a CONFIRMED, WORK_RELATED, HIGHLY_SENSITIVE classification.

    Relevance is fixed to ``WORK_RELATED`` (the only non-excluded value) so the
    privacy gate can never fire — leaving the sensitivity gate as the sole
    control under test.
    """

    result = ClassificationResult(
        source_item_id=item.id,
        relevance=Relevance.WORK_RELATED,
        business_category=business_category,
        sensitivity=Sensitivity.HIGHLY_SENSITIVE,
        confidence=0.9,
        reasons=["reason"],
        evidence_spans=[],
        status=SuggestionStatus.CONFIRMED,
    )
    db.add(result)
    db.flush()
    return result


def _fixed_extractor():
    """A deterministic extractor stub returning a fixed KnowledgeOutput."""

    def _extract(content: str, context) -> KnowledgeOutput:
        return KnowledgeOutput(
            summary="Sensitive milestone approved.",
            key_points=["Milestone approved"],
            suggested_entities=[
                EntitySuggestion(
                    name="Project Orion",
                    entity_type=core_models.BusinessEntityType.PROJECT,
                    confidence=0.9,
                )
            ],
            evidence_text="The sensitive project milestone was approved.",
            knowledge_type="FACT",
        )

    return _extract


def _count_knowledge_for_item(db: Session, item_id) -> int:
    return db.execute(
        select(func.count())
        .select_from(KnowledgeItem)
        .where(KnowledgeItem.source_item_id == item_id)
    ).scalar_one()


# A generator over source content plus the business-category axis (which the
# sensitivity gate is indifferent to) so we exercise the gate across a wide
# space of otherwise-valid inputs.
_content_strategy = st.one_of(
    st.text(min_size=1),
    st.text(alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x2FFF), min_size=1),
    st.sampled_from(
        [
            "Confidential salary details for the executive team.",
            "Board decision approved under NDA.",
            "Internal do not forward: acquisition terms.",
            "The project milestone was approved. Please follow up.",
        ]
    ),
)


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    content=_content_strategy,
    business_category=st.sampled_from(list(BusinessCategory)),
)
def test_sensitivity_gate_refuses_unacknowledged_and_persists_nothing(
    db_session: Session,
    content: str,
    business_category: BusinessCategory,
) -> None:
    """HIGHLY_SENSITIVE + acknowledged=False → 409 and no KnowledgeItem.

    **Validates: Requirements 6.4, 6.5**
    """

    org, user = _make_org_and_user(db_session)
    item = _make_item(db_session, org, user, content=content)
    _classify_highly_sensitive(db_session, item, business_category)
    service = KnowledgeService(db_session)

    # acknowledged=False must refuse with 409 and leave nothing behind.
    with pytest.raises(HTTPException) as exc:
        service.extract(
            org.id, item.id, extractor=_fixed_extractor(), acknowledged=False
        )
    assert exc.value.status_code == 409  # Requirement 6.4
    assert _count_knowledge_for_item(db_session, item.id) == 0  # nothing persisted

    # Paired check (Requirement 6.5): the gate is about acknowledgment, not an
    # outright block — acknowledged=True on the same item DOES create a
    # SUGGESTED knowledge item.
    knowledge = service.extract(
        org.id, item.id, extractor=_fixed_extractor(), acknowledged=True
    )
    assert knowledge.id is not None
    assert knowledge.status == SuggestionStatus.SUGGESTED
    assert _count_knowledge_for_item(db_session, item.id) == 1
