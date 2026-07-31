"""Unit tests for the real :class:`LLMProvider` adapter (task M5.0).

These tests **never** touch the network. Each constructs an
:class:`~app.core.services.ai_provider.LLMProvider` and replaces its OpenAI
client with an in-memory fake whose ``chat.completions.parse`` (or ``create``)
returns canned structured responses. They assert that:

* every method maps the fake response onto the correct real output schema;
* confidence is clamped to ``[0.0, 1.0]``;
* ``entity_ref`` strings are repaired — valid, in-context ids are preserved
  while malformed or unknown ids become ``None`` (the LLM never fabricates ids);
* ``EntityBriefOutput.entity_id`` is set by the provider (from context or a
  deterministic ``uuid5`` fallback), not by the model;
* any client failure surfaces as :class:`AIProviderError`;
* ``LLMProvider`` conforms to the runtime-checkable :class:`AIProvider` protocol.
"""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from app.config import Settings
from app.core.models import (
    BusinessCategory,
    BusinessEntityType,
    Relevance,
    Sensitivity,
)
from app.core.services.ai_provider import (
    AIProvider,
    AIProviderError,
    ClassificationOutput,
    ClientContext,
    ContextActionItem,
    ContextKnowledgeItem,
    DailyBriefOutput,
    EntityBriefOutput,
    EntityContext,
    KnowledgeOutput,
    LLMProvider,
    MeetingSummaryOutput,
    OrgContext,
    _LLMBriefLine,
    _LLMClassification,
    _LLMDailyBrief,
    _LLMEntityBrief,
    _LLMEntitySuggestion,
    _LLMEvidenceSpan,
    _LLMKnowledge,
    _LLMMeetingSummary,
)


# ---------------------------------------------------------------------------
# In-memory fake OpenAI client (no network)
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, parsed=None, content=None) -> None:
        self.parsed = parsed
        self.content = content


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeCompletion:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]


class _FakeParseCompletions:
    """Exposes ``.parse`` returning a canned parsed object (or raising)."""

    def __init__(self, parsed=None, raise_exc: Exception | None = None) -> None:
        self._parsed = parsed
        self._raise = raise_exc
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise is not None:
            raise self._raise
        return _FakeCompletion(_FakeMessage(parsed=self._parsed))


class _FakeCreateOnlyCompletions:
    """Exposes only ``.create`` (no ``.parse``) returning JSON content."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeCompletion(_FakeMessage(content=self._content))


class _FakeChat:
    def __init__(self, completions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions) -> None:
        self.chat = _FakeChat(completions)


def _provider(parsed=None, raise_exc: Exception | None = None) -> LLMProvider:
    """Build an ``LLMProvider`` whose client is a fake ``.parse`` completions API."""

    provider = LLMProvider(Settings(ai_provider="llm", llm_api_key="sk-test"))
    provider._client = _FakeClient(_FakeParseCompletions(parsed=parsed, raise_exc=raise_exc))
    return provider


def _provider_create_only(content: str) -> LLMProvider:
    """Build an ``LLMProvider`` whose client only supports ``.create`` (JSON mode)."""

    provider = LLMProvider(Settings(ai_provider="llm", llm_api_key="sk-test"))
    provider._client = _FakeClient(_FakeCreateOnlyCompletions(content))
    return provider


# ---------------------------------------------------------------------------
# classify_source_item
# ---------------------------------------------------------------------------


def test_classify_maps_response_and_clamps_confidence() -> None:
    parsed = _LLMClassification(
        relevance=Relevance.WORK_RELATED,
        business_category=BusinessCategory.PROJECT,
        sensitivity=Sensitivity.INTERNAL,
        confidence=1.7,  # out of range -> must clamp to 1.0
        reasons=["Mentions the project milestone."],
        evidence_spans=[_LLMEvidenceSpan(text="milestone", start=10, end=19)],
    )
    result = _provider(parsed=parsed).classify_source_item("body", "title")

    assert isinstance(result, ClassificationOutput)
    assert result.relevance is Relevance.WORK_RELATED
    assert result.business_category is BusinessCategory.PROJECT
    assert result.sensitivity is Sensitivity.INTERNAL
    assert result.confidence == 1.0
    assert result.reasons == ["Mentions the project milestone."]
    assert result.evidence_spans[0].text == "milestone"
    assert result.evidence_spans[0].start == 10


def test_classify_clamps_negative_confidence_to_zero() -> None:
    parsed = _LLMClassification(
        relevance=Relevance.SPAM,
        business_category=BusinessCategory.OTHER,
        sensitivity=Sensitivity.PUBLIC,
        confidence=-3.0,
    )
    result = _provider(parsed=parsed).classify_source_item("body", "title")
    assert result.confidence == 0.0


def test_classify_raises_ai_provider_error_on_api_failure() -> None:
    provider = _provider(raise_exc=RuntimeError("simulated API timeout"))
    with pytest.raises(AIProviderError):
        provider.classify_source_item("body", "title")


def test_classify_raises_ai_provider_error_when_client_unavailable() -> None:
    provider = LLMProvider(Settings(ai_provider="llm", llm_api_key="sk-test"))
    provider._client = None
    provider._init_error = RuntimeError("no client")
    with pytest.raises(AIProviderError):
        provider.classify_source_item("body", "title")


def test_classify_uses_create_fallback_when_parse_absent() -> None:
    content = (
        '{"relevance": "WORK_RELATED", "business_category": "MEETING", '
        '"sensitivity": "PUBLIC", "confidence": 0.42, "reasons": [], '
        '"evidence_spans": []}'
    )
    result = _provider_create_only(content).classify_source_item("body", "title")
    assert result.business_category is BusinessCategory.MEETING
    assert result.confidence == 0.42


# ---------------------------------------------------------------------------
# extract_knowledge
# ---------------------------------------------------------------------------


def test_extract_knowledge_maps_and_clamps_entity_confidence() -> None:
    parsed = _LLMKnowledge(
        summary="Client agreed to the retainer.",
        key_points=["retainer", "next quarter"],
        suggested_entities=[
            _LLMEntitySuggestion(
                name="Acme Corp", entity_type=BusinessEntityType.CLIENT, confidence=2.0
            ),
            _LLMEntitySuggestion(
                name="Beta LLP", entity_type=BusinessEntityType.PARTNER, confidence=-1.0
            ),
        ],
        evidence_text="Client agreed to the retainer.",
        knowledge_type="FACT",
    )
    context = EntityContext(organization_id=uuid4())
    result = _provider(parsed=parsed).extract_knowledge("content", context)

    assert isinstance(result, KnowledgeOutput)
    assert result.summary == "Client agreed to the retainer."
    assert result.suggested_entities[0].confidence == 1.0
    assert result.suggested_entities[1].confidence == 0.0


# ---------------------------------------------------------------------------
# generate_daily_brief — entity_ref repair
# ---------------------------------------------------------------------------


def test_daily_brief_repairs_entity_refs() -> None:
    org_id = uuid4()
    valid_entity = uuid4()
    unknown_entity = uuid4()  # valid UUID but NOT present in the context
    context = OrgContext(
        organization_id=org_id,
        confirmed_knowledge=[
            ContextKnowledgeItem(
                id=uuid4(),
                summary="Known item",
                evidence_text="evidence",
                business_entity_id=valid_entity,
            )
        ],
    )
    parsed = _LLMDailyBrief(
        headline="Today",
        priorities=[
            _LLMBriefLine(text="valid ref", entity_ref=str(valid_entity)),
            _LLMBriefLine(text="garbage ref", entity_ref="not-a-uuid"),
            _LLMBriefLine(text="unknown ref", entity_ref=str(unknown_entity)),
            _LLMBriefLine(text="null ref", entity_ref=None),
        ],
    )
    result = _provider(parsed=parsed).generate_daily_brief(context)

    assert isinstance(result, DailyBriefOutput)
    assert result.headline == "Today"
    # Valid, in-context id preserved; everything else dropped to None.
    assert result.priorities[0].entity_ref == valid_entity
    assert result.priorities[1].entity_ref is None
    assert result.priorities[2].entity_ref is None
    assert result.priorities[3].entity_ref is None


def test_daily_brief_raises_ai_provider_error_on_failure() -> None:
    provider = _provider(raise_exc=RuntimeError("boom"))
    with pytest.raises(AIProviderError):
        provider.generate_daily_brief(OrgContext(organization_id=uuid4()))


# ---------------------------------------------------------------------------
# generate_entity_brief — entity_id is set by the provider, not the model
# ---------------------------------------------------------------------------


def test_entity_brief_uses_context_entity_id() -> None:
    org_id = uuid4()
    entity_id = uuid4()
    context = EntityContext(
        organization_id=org_id,
        entity_id=entity_id,
        entity_type=BusinessEntityType.CLIENT,
        entity_name="Acme",
        open_actions=[
            ContextActionItem(id=uuid4(), title="Call back", status="OPEN")
        ],
    )
    parsed = _LLMEntityBrief(
        summary="Recent activity for Acme.",
        recent_knowledge=[_LLMBriefLine(text="k", entity_ref=str(entity_id))],
        open_actions=[_LLMBriefLine(text="a", entity_ref="bogus")],
    )
    result = _provider(parsed=parsed).generate_entity_brief(context)

    assert isinstance(result, EntityBriefOutput)
    assert result.entity_id == entity_id  # provider-assigned, not from the model
    # The entity's own id is a valid ref target and is preserved.
    assert result.recent_knowledge[0].entity_ref == entity_id
    # Bogus ref repaired to None.
    assert result.open_actions[0].entity_ref is None


def test_entity_brief_derives_deterministic_id_when_absent() -> None:
    org_id = uuid4()
    context = EntityContext(
        organization_id=org_id, entity_id=None, entity_name="Acme"
    )
    parsed = _LLMEntityBrief(summary="No id supplied.")
    result = _provider(parsed=parsed).generate_entity_brief(context)

    expected = uuid5(NAMESPACE_URL, f"entity:{org_id}:Acme")
    assert result.entity_id == expected


# ---------------------------------------------------------------------------
# generate_meeting_summary
# ---------------------------------------------------------------------------


def test_meeting_summary_maps_response() -> None:
    parsed = _LLMMeetingSummary(
        summary="Discussed retirement planning.",
        key_points=["retirement", "insurance review"],
        action_items=[],
        partner_need_signals=["retirement", "insurance"],
        evidence_text="Client asked about retirement planning.",
    )
    client = ClientContext(organization_id=uuid4(), client_name="Jane")
    result = _provider(parsed=parsed).generate_meeting_summary("notes", client)

    assert isinstance(result, MeetingSummaryOutput)
    assert result.summary == "Discussed retirement planning."
    assert result.partner_need_signals == ["retirement", "insurance"]
    assert result.evidence_text == "Client asked about retirement planning."


def test_meeting_summary_raises_ai_provider_error_on_failure() -> None:
    provider = _provider(raise_exc=RuntimeError("kaboom"))
    with pytest.raises(AIProviderError):
        provider.generate_meeting_summary("notes", ClientContext(organization_id=uuid4()))


# ---------------------------------------------------------------------------
# Protocol conformance + selection
# ---------------------------------------------------------------------------


def test_llm_provider_satisfies_ai_provider_protocol() -> None:
    provider = LLMProvider(Settings(ai_provider="llm", llm_api_key="sk-test"))
    assert isinstance(provider, AIProvider)
