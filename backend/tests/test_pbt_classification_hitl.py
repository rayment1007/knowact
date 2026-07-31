"""Property-based test P5: human-in-the-loop (classify never auto-confirms).

Property 5 (human-in-the-loop): immediately after generation, an AI-produced
classification has status ``SUGGESTED`` and is *never* ``CONFIRMED`` without an
explicit :meth:`ClassificationService.confirm` call. The classify step is the
AI generation boundary — no matter what the provider returns, persistence must
land in ``SUGGESTED``, leave the source item unclassified, and wait for a human
decision (Requirements 4.2, 5.5).

To prove no provider output path yields an auto-confirmed row, we drive
``classify`` with a *stub classifier* that returns an arbitrary but valid
:class:`ClassificationOutput` (every axis, confidence, and evidence shape
generated across the space), plus arbitrary generated title/content. For every
example the persisted result must be ``SUGGESTED`` (and the item still ``NEW``),
and only an explicit ``confirm`` call transitions it to ``CONFIRMED``.

**Validates: Requirements 4.2, 5.5**
"""

from __future__ import annotations

from uuid import uuid4

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

from app.core import models as core_models
from app.core.models import (
    BusinessCategory,
    Relevance,
    Sensitivity,
    SourceStatus,
    SourceType,
    SuggestionStatus,
)
from app.core.services.ai_provider import ClassificationOutput, EvidenceSpan
from app.core.services.classification_service import ClassificationService
from app.security import hash_password

# bcrypt hashing is ~200ms/call; the stored hash is never re-verified here, so
# hash the throwaway password once per module instead of per Hypothesis example.
_PW_HASH = hash_password("pw")


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Arbitrary source text: arbitrary unicode plus empty/whitespace-only edges so
# the generation input space is broad and does not depend on keyword hits.
_text_strategy = st.one_of(
    st.text(),
    st.text(alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x2FFF)),
    st.sampled_from(["", " ", "\t", "\n  \n", "   \u3000  "]),
)

# An arbitrary but schema-valid ClassificationOutput: any axis value, any
# bounded confidence, and any reasons/evidence shape. This exercises every
# provider output path a real classifier could produce.
_evidence_span_strategy = st.builds(
    EvidenceSpan,
    text=st.text(max_size=40),
    start=st.one_of(st.none(), st.integers(min_value=0, max_value=1000)),
    end=st.one_of(st.none(), st.integers(min_value=0, max_value=1000)),
)

_classification_output_strategy = st.builds(
    ClassificationOutput,
    relevance=st.sampled_from(list(Relevance)),
    business_category=st.sampled_from(list(BusinessCategory)),
    sensitivity=st.sampled_from(list(Sensitivity)),
    confidence=st.floats(min_value=0.0, max_value=1.0),
    reasons=st.lists(st.text(max_size=60), max_size=5),
    evidence_spans=st.lists(_evidence_span_strategy, max_size=4),
)


def _stub_classifier(output: ClassificationOutput):
    """A classifier stub that returns a fixed, pre-generated ``output``."""

    def _classify(content: str, title: str) -> ClassificationOutput:
        return output

    return _classify


def _fresh_item(db: Session, title: str, content: str) -> core_models.SourceItem:
    """Create a distinct org/user/source-item so Hypothesis examples don't interfere.

    Hypothesis reuses the test function across many examples within one
    (function-scoped, rolled-back) ``db_session``, so each example builds its
    own tenant + item with unique identifiers.
    """

    org = core_models.Organization(name=f"Org {uuid4()}")
    db.add(org)
    db.flush()
    user = core_models.User(
        organization_id=org.id,
        email=f"{uuid4()}@example.com",
        full_name="Actor",
        password_hash=_PW_HASH,
        role="ADMIN",
    )
    db.add(user)
    db.flush()
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


# ---------------------------------------------------------------------------
# Property P5
# ---------------------------------------------------------------------------


# The db_session fixture is function-scoped (one rolled-back transaction per
# test), while Hypothesis drives many examples through this single function. We
# create a fresh, uniquely-identified org/user/item per example so examples
# never interfere, which makes suppressing this health check safe.
@settings(
    max_examples=100,
    deadline=None,  # per-example DB writes + password hashing exceed the default deadline
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    title=_text_strategy,
    content=_text_strategy,
    output=_classification_output_strategy,
)
def test_classify_never_auto_confirms(
    db_session: Session,
    title: str,
    content: str,
    output: ClassificationOutput,
) -> None:
    """classify(...) persists SUGGESTED and never CONFIRMED (Requirements 4.2, 5.5)."""

    item = _fresh_item(db_session, title=title, content=content)
    service = ClassificationService(db_session)

    result = service.classify(
        item.organization_id, item.id, classifier=_stub_classifier(output)
    )

    # No provider output path auto-confirms: generation always yields SUGGESTED.
    assert result.status == SuggestionStatus.SUGGESTED
    assert result.status != SuggestionStatus.CONFIRMED
    # The source item stays unclassified until a human confirms.
    assert item.status == SourceStatus.NEW

    # Only an explicit confirm call transitions the suggestion to CONFIRMED.
    confirmed = service.confirm(
        item.organization_id, item.id, actor_id=item.created_by
    )
    assert confirmed.id == result.id
    assert confirmed.status == SuggestionStatus.CONFIRMED
