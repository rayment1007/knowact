"""Property-based test P19: citation grounding (no fabricated citations).

Property 19: for all Copilot answers, every returned ``Citation.source_id``
corresponds to a record that was present in the bounded evidence context
supplied to the LLM for that request; any citation not matching the evidence set
is rejected. When the evidence set is empty/insufficient, the response has
``insufficient_evidence == true`` and asserts it cannot find enough confirmed
evidence.

Each Hypothesis example seeds a fresh organization with a random number of
confirmed :class:`~app.core.models.KnowledgeItem`s (the bounded evidence), then
drives :class:`CopilotService.ask` through a **fake OpenAI transport** whose
structured output cites a mix of real evidence ids and *fabricated* ids. It then
asserts every returned citation is grounded in the evidence set, that no
fabricated id survives, and that an empty evidence set yields
``insufficient_evidence``. Embeddings use the deterministic
:class:`FakeEmbeddingProvider` — no network, no paid API.

**Validates: Requirements 30.7, 31.2 / Property 19**
"""

from __future__ import annotations

from uuid import UUID, uuid4

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

from app.config import Settings
from app.core import models as core_models
from app.core.models import SuggestionStatus
from app.core.services.ai_provider import LLMProvider, _LLMCopilotAnswer
from app.modules.cwi.services.copilot_service import CopilotService
from app.modules.cwi.services.embedding import FakeEmbeddingProvider
from app.security import hash_password

_PW_HASH = hash_password("pw")
_DIM = 32


# ---------------------------------------------------------------------------
# In-memory fake OpenAI transport (no network) — returns a canned parsed object
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, parsed) -> None:
        self.parsed = parsed
        self.content = None


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeCompletion:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]


class _FakeParseCompletions:
    def __init__(self, parsed) -> None:
        self._parsed = parsed

    def parse(self, **kwargs):
        return _FakeCompletion(_FakeMessage(parsed=self._parsed))


class _FakeChat:
    def __init__(self, completions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, parsed) -> None:
        self.chat = _FakeChat(_FakeParseCompletions(parsed))


def _llm_settings() -> Settings:
    return Settings(
        ai_provider="llm",
        llm_api_key="sk-test",
        embedding_provider="mock",
        embedding_dimension=_DIM,
    )


def _make_service(db: Session, parsed: _LLMCopilotAnswer) -> CopilotService:
    """A CopilotService whose provider is a fake-transport LLMProvider."""

    provider = LLMProvider(_llm_settings())
    provider._client = _FakeClient(parsed)
    return CopilotService(
        db,
        provider,
        FakeEmbeddingProvider(dimension=_DIM),
        settings=_llm_settings(),
    )


def _seed_org(db: Session) -> tuple[core_models.Organization, core_models.User]:
    suffix = uuid4().hex
    org = core_models.Organization(name=f"Org-{suffix}")
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


def _seed_confirmed_knowledge(db: Session, org_id) -> UUID:
    suffix = uuid4().hex
    item = core_models.KnowledgeItem(
        organization_id=org_id,
        summary=f"Confirmed fact {suffix}",
        key_points=["fact"],
        evidence_text=f"Evidence {suffix}",
        knowledge_type="FACT",
        status=SuggestionStatus.CONFIRMED,
    )
    db.add(item)
    db.flush()
    return item.id


class _AskRequest:
    """A minimal ask request object (mirrors CopilotAskRequest attributes)."""

    def __init__(self, question: str) -> None:
        self.question = question
        self.intent = "ASK"
        self.business_entity_id = None


@settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    n_evidence=st.integers(min_value=0, max_value=5),
    n_cited_real=st.integers(min_value=0, max_value=5),
    n_fabricated=st.integers(min_value=0, max_value=4),
)
def test_citations_are_always_grounded_in_the_evidence_set(
    db_session: Session,
    n_evidence: int,
    n_cited_real: int,
    n_fabricated: int,
) -> None:
    """Every returned citation is in evidence; fabricated ids are rejected.

    **Validates: Requirements 30.7, 31.2 / Property 19**
    """

    org, user = _seed_org(db_session)

    # The bounded evidence: N confirmed, org-scoped knowledge items.
    evidence_ids = [
        _seed_confirmed_knowledge(db_session, org.id) for _ in range(n_evidence)
    ]

    # The LLM cites a subset of the real evidence ids PLUS fabricated ids that
    # were never in the evidence set. A grounded backend must drop the latter.
    cited_real = evidence_ids[:n_cited_real]
    fabricated = [uuid4() for _ in range(n_fabricated)]
    cited_source_ids = [str(i) for i in cited_real] + [str(i) for i in fabricated]

    parsed = _LLMCopilotAnswer(
        answer="Here is what the evidence shows.",
        cited_source_ids=cited_source_ids,
        insufficient_evidence=False,
        proposed_artifact=None,
    )
    service = _make_service(db_session, parsed)

    response = service.ask(org.id, user.id, _AskRequest("What do we know?"))

    if n_evidence == 0:
        # Empty evidence → honest refusal, never a guess (Property 19).
        assert response.insufficient_evidence is True
        assert response.citations == []
        return

    evidence_set = set(evidence_ids)
    returned_ids = [c.source_id for c in response.citations]

    # 1. Every returned citation is present in the bounded evidence set.
    for source_id in returned_ids:
        assert source_id in evidence_set

    # 2. No fabricated id ever survives.
    fabricated_set = set(fabricated)
    assert not (set(returned_ids) & fabricated_set)

    # 3. The grounded, in-evidence subset the model cited is preserved (deduped).
    assert set(returned_ids) == set(cited_real)

    # 4. No duplicate citations.
    assert len(returned_ids) == len(set(returned_ids))
