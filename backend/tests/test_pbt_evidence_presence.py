"""Property-based test P12: evidence presence on confirmed knowledge.

Property 12 (evidence presence): for *any* generated source item — including
empty, whitespace-only, and unicode content — that carries a **CONFIRMED**
classification with a non-excluded relevance and a non-highly-sensitive
sensitivity, running :meth:`KnowledgeService.extract` followed by
:meth:`KnowledgeService.confirm` always yields a **CONFIRMED**
:class:`KnowledgeItem` whose ``evidence_text`` is non-empty (Requirements 6.6,
7.5). The only path to a ``CONFIRMED`` knowledge item is extract-then-confirm,
so proving this pair preserves the invariant proves no confirmed item can ever
lack evidence.

The guarantee has two independent sources, both exercised here by driving the
extractor with a generated ``evidence_seed`` that is frequently empty or
whitespace-only:

* **Provider evidence** — when the extractor returns usable evidence, that text
  is stored.
* **Content fallback (Requirement 6.6)** — when the extractor returns nothing
  usable, the service falls back to the source item's content, which is
  non-empty by the create-payload contract. Because this test builds
  :class:`SourceItem` rows directly, it upholds that contract by ensuring each
  item's ``content`` is non-empty (substituting a sentinel only when a generated
  value strips to nothing), so the fallback branch is genuinely exercised while
  staying faithful to production behavior.

The property is exercised end-to-end against the SQLite ``db_session`` fixture.
Hypothesis runs many examples within one function body while ``db_session`` is
rolled back only once at teardown, so each example creates a *fresh*,
uniquely-identified org/user/item to avoid cross-example contamination.

**Validates: Requirements 6.6, 7.5**
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import models as core_models
from app.core.models import (
    BusinessCategory,
    ClassificationResult,
    Relevance,
    Sensitivity,
    SourceStatus,
    SourceType,
    SuggestionStatus,
)
from app.core.services.ai_provider import KnowledgeOutput
from app.core.services.knowledge_service import KnowledgeService
from app.security import hash_password

# bcrypt hashing is ~200ms/call; the stored hash is never re-verified here, so
# hash the throwaway password once per module instead of per Hypothesis example.
_PW_HASH = hash_password("pw")


# ---------------------------------------------------------------------------
# Helpers (mirroring tests/test_knowledge_service.py patterns)
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
        title="Generated item",
        content=content,
        status=SourceStatus.NEW,
    )
    db.add(item)
    db.flush()
    return item


def _classify_confirmed(
    db: Session,
    item: core_models.SourceItem,
    sensitivity: Sensitivity,
) -> ClassificationResult:
    """Persist a CONFIRMED, non-excluded classification for ``item``."""

    result = ClassificationResult(
        source_item_id=item.id,
        relevance=Relevance.WORK_RELATED,  # only non-excluded relevance
        business_category=BusinessCategory.PROJECT,
        sensitivity=sensitivity,
        confidence=0.9,
        reasons=["reason"],
        evidence_spans=[],
        status=SuggestionStatus.CONFIRMED,
    )
    db.add(result)
    db.flush()
    return result


def _seeded_extractor(evidence_text: str):
    """A deterministic extractor stub returning a fixed ``evidence_text``.

    The seed is frequently empty/whitespace so the service's content fallback
    (Requirement 6.6) is exercised alongside the provider-evidence path.
    """

    def _extract(content: str, context) -> KnowledgeOutput:
        return KnowledgeOutput(
            summary="Generated summary.",
            key_points=["point"],
            suggested_entities=[],
            evidence_text=evidence_text,
            knowledge_type="FACT",
        )

    return _extract


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Arbitrary text (unicode, empty, whitespace) used both for source content and
# to seed the extractor's evidence. The evidence-presence invariant must hold
# regardless of these values.
_text_strategy = st.one_of(
    st.text(max_size=200),
    st.text(alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x2FFF), max_size=80),
    st.sampled_from(["", " ", "\t", "\n  \n", "   \u3000  ", "milestone approved", "机密文件"]),
)

# Non-highly-sensitive axis values: the sensitivity gate never trips, so
# extraction proceeds without an acknowledgment.
_non_highly_sensitive = st.sampled_from(
    [Sensitivity.PUBLIC, Sensitivity.INTERNAL, Sensitivity.CONFIDENTIAL]
)


# ---------------------------------------------------------------------------
# Property P12
# ---------------------------------------------------------------------------


# The db_session fixture is function-scoped (one rolled-back transaction per
# test), while Hypothesis drives many examples through this single function. We
# create a fresh, uniquely-identified org/user/item per example so examples
# never collide on the users' unique-email constraint.
@settings(
    max_examples=100,
    deadline=None,  # per-example DB writes + password hashing exceed the default deadline
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    content_seed=_text_strategy,
    evidence_seed=_text_strategy,
    sensitivity=_non_highly_sensitive,
)
def test_confirmed_knowledge_always_has_evidence(
    db_session: Session,
    content_seed: str,
    evidence_seed: str,
    sensitivity: Sensitivity,
) -> None:
    """extract-then-confirm yields non-empty evidence_text (Requirements 6.6, 7.5)."""

    # Fresh tenant + item per example (unique email avoids the users' unique
    # constraint across examples sharing the function-scoped session).
    suffix = db_session.execute(
        select(func.count()).select_from(core_models.User)
    ).scalar_one()
    org, user = _make_org_and_user(
        db_session, f"Org {suffix}", f"actor{suffix}@example.com"
    )

    # SourceItem content is non-empty by the create-payload contract; uphold it
    # here (falling back to a sentinel when a generated value strips to nothing)
    # so the service's content fallback is faithful to production.
    content = content_seed if content_seed.strip() else "non-empty source content"
    item = _make_item(db_session, org, user, content=content)
    _classify_confirmed(db_session, item, sensitivity)

    service = KnowledgeService(db_session)

    # evidence_seed is frequently empty/whitespace, forcing the fallback path.
    suggested = service.extract(
        org.id, item.id, extractor=_seeded_extractor(evidence_seed)
    )

    confirmed = service.confirm(org.id, suggested.id, actor_id=user.id)

    # The only path to CONFIRMED is extract-then-confirm; the resulting item
    # must always carry non-empty evidence (Requirements 6.6, 7.5).
    assert confirmed.status == SuggestionStatus.CONFIRMED
    assert confirmed.evidence_text is not None
    assert confirmed.evidence_text.strip()
