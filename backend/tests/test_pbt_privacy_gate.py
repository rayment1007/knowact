"""Property-based test P3: the privacy gate is absolute.

Property 3 (privacy gate): for *any* source item whose **CONFIRMED**
classification relevance is one of the excluded values
``{PERSONAL, SPAM, IRRELEVANT, SYSTEM_NOTIFICATION}``, calling
:meth:`KnowledgeService.extract` must refuse with ``409 Conflict`` and persist
**no** :class:`KnowledgeItem` — so extraction can never produce a ``CONFIRMED``
knowledge item from privacy-excluded content (Requirement 6.3).

The property statement is phrased in terms of "category", but the implemented
privacy gate (and Requirement 6.3) keys off the **relevance** axis, so this
test exercises the relevance values in the excluded set.

Each Hypothesis example builds a fresh organization/user/source item with a
unique email (the ``db_session`` fixture is function-scoped and shared across
all examples of a single test, so rows must not collide) and a CONFIRMED
classification whose relevance is drawn from the excluded set, over arbitrary
generated ``title`` / ``content`` text. After the refusal it asserts that zero
``KnowledgeItem`` rows exist for that source item.

**Validates: Requirements 6.3**
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

# The relevance values whose content is excluded from the knowledge base
# entirely (Requirement 6.3). The privacy gate must refuse extraction for every
# one of these.
_EXCLUDED_RELEVANCE = [
    Relevance.PERSONAL,
    Relevance.SPAM,
    Relevance.IRRELEVANT,
    Relevance.SYSTEM_NOTIFICATION,
]


def _make_org_and_user(
    db: Session, email: str
) -> tuple[core_models.Organization, core_models.User]:
    """Create a fresh organization + user (unique email) in ``db``."""

    org = core_models.Organization(name="Privacy Gate Org")
    db.add(org)
    db.flush()
    user = core_models.User(
        organization_id=org.id,
        email=email,
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
    title: str,
    content: str,
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


def _classify_confirmed(
    db: Session,
    item: core_models.SourceItem,
    relevance: Relevance,
) -> ClassificationResult:
    """Persist a CONFIRMED classification with the given relevance."""

    result = ClassificationResult(
        source_item_id=item.id,
        relevance=relevance,
        business_category=BusinessCategory.PROJECT,
        sensitivity=Sensitivity.PUBLIC,
        confidence=0.9,
        reasons=["reason"],
        evidence_spans=[],
        status=SuggestionStatus.CONFIRMED,
    )
    db.add(result)
    db.flush()
    return result


def _always_extractor(content: str, context) -> KnowledgeOutput:
    """An extractor that would always produce output if ever reached.

    The privacy gate must refuse *before* invoking the extractor, so this
    callable exists only to prove that a non-empty extractor result never leads
    to a persisted knowledge item.
    """

    return KnowledgeOutput(
        summary="Should never be persisted.",
        key_points=["nope"],
        suggested_entities=[
            EntitySuggestion(
                name="Ghost Entity",
                entity_type=core_models.BusinessEntityType.PROJECT,
                confidence=0.9,
            )
        ],
        evidence_text="Should never be persisted.",
        knowledge_type="FACT",
    )


def _knowledge_count_for_item(db: Session, item_id) -> int:
    return db.execute(
        select(func.count())
        .select_from(KnowledgeItem)
        .where(KnowledgeItem.source_item_id == item_id)
    ).scalar_one()


# A strategy over arbitrary non-empty text (content must be non-empty per the
# create-payload validation). Mixes arbitrary unicode with plausible prose.
_text_strategy = st.one_of(
    st.text(min_size=1),
    st.text(
        alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x2FFF), min_size=1
    ),
    st.sampled_from(
        [
            "Happy birthday! Lunch on Friday?",
            "You have unsubscribed from this newsletter.",
            "System notification: your password expires soon.",
            "Win a free prize now, click here!",
        ]
    ),
)


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    relevance=st.sampled_from(_EXCLUDED_RELEVANCE),
    title=_text_strategy,
    content=_text_strategy,
)
def test_privacy_gate_never_produces_knowledge(
    db_session: Session,
    relevance: Relevance,
    title: str,
    content: str,
) -> None:
    """Privacy-excluded relevance always refuses (409) and persists nothing.

    **Validates: Requirements 6.3**
    """

    # Fresh org/user/item per example; unique email avoids collisions across the
    # many examples sharing the function-scoped ``db_session``.
    org, user = _make_org_and_user(db_session, f"{uuid4().hex}@example.com")
    item = _make_item(db_session, org, user, title=title, content=content)
    _classify_confirmed(db_session, item, relevance=relevance)
    service = KnowledgeService(db_session)

    before = _knowledge_count_for_item(db_session, item.id)

    with pytest.raises(HTTPException) as exc:
        service.extract(org.id, item.id, extractor=_always_extractor)

    # Requirement 6.3: extraction is refused with 409 Conflict ...
    assert exc.value.status_code == 409
    # ... and no KnowledgeItem is ever persisted for this source item, so no
    # CONFIRMED knowledge item can arise from privacy-excluded content.
    assert _knowledge_count_for_item(db_session, item.id) == before == 0
