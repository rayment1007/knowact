"""Unit tests for the regulated-advice guardrail framing (Requirements 12.1, 12.2).

These tests assert the operational-support-only stance is baked into the AI
layer at two points:

* the shared :data:`_SYSTEM_PREAMBLE` frames every LLM operation as operational
  support only and explicitly forbids financial / legal / tax / insurance /
  medical / investment advice (Requirement 12.2), and every ``LLMProvider``
  capability applies that preamble to its system prompt (Requirement 12.1);
* the deterministic :class:`MockAIProvider` never emits text that reads as
  regulated *advice* for representative inputs (Requirements 12.1, 12.2).

All checks are deterministic and hermetic — no network. The LLM system prompts
are captured by stubbing :meth:`LLMProvider._complete` so the prompt is observed
without any API call.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.models import BusinessEntityType
from app.core.services.ai_provider import (
    AIProviderError,
    ClientContext,
    EntityContext,
    LLMProvider,
    MockAIProvider,
    OrgContext,
    _SYSTEM_PREAMBLE,
)

# The regulated-advice categories that must be named and disclaimed
# (Requirement 12.2).
_REGULATED_CATEGORIES = [
    "financial",
    "legal",
    "tax",
    "insurance",
    "medical",
    "investment",
]


# ---------------------------------------------------------------------------
# The shared system preamble (Requirements 12.1, 12.2)
# ---------------------------------------------------------------------------


def test_system_preamble_frames_output_as_operational_support_only() -> None:
    """The preamble frames outputs as operational support only (Req 12.1)."""

    lowered = _SYSTEM_PREAMBLE.lower()
    assert "operational support only" in lowered
    # It must forbid presenting the output as advice of any kind.
    assert "never" in lowered
    assert "advice" in lowered


def test_system_preamble_forbids_every_regulated_advice_category() -> None:
    """The preamble explicitly names all regulated categories (Req 12.2)."""

    lowered = _SYSTEM_PREAMBLE.lower()
    for category in _REGULATED_CATEGORIES:
        assert category in lowered, f"preamble must disclaim {category!r} advice"


# ---------------------------------------------------------------------------
# Every LLMProvider capability applies the preamble to its system prompt
# ---------------------------------------------------------------------------


def _capture_system_prompts() -> list[str]:
    """Invoke every LLMProvider capability and return the system prompts used.

    Stubs :meth:`LLMProvider._complete` to record the ``system`` prompt and then
    raise :class:`AIProviderError`; each capability re-raises that error, so the
    call is short-circuited before any network use while the prompt is observed.
    """

    captured: list[str] = []

    provider = LLMProvider(settings=None)

    def _fake_complete(system: str, user: str, response_model: object):
        captured.append(system)
        raise AIProviderError("captured")

    provider._complete = _fake_complete  # type: ignore[method-assign]

    org_id = uuid4()
    org_ctx = OrgContext(organization_id=org_id)
    entity_ctx = EntityContext(
        organization_id=org_id,
        entity_id=uuid4(),
        entity_type=BusinessEntityType.CLIENT,
        entity_name="Acme Corp",
    )
    client_ctx = ClientContext(organization_id=org_id, client_name="Jane Doe")

    operations = [
        lambda: provider.classify_source_item("some content", "a title"),
        lambda: provider.extract_knowledge("some content", entity_ctx),
        lambda: provider.generate_daily_brief(org_ctx),
        lambda: provider.generate_entity_brief(entity_ctx),
        lambda: provider.generate_meeting_summary("meeting notes", client_ctx),
    ]

    for op in operations:
        with pytest.raises(AIProviderError):
            op()

    return captured


def test_all_llm_capabilities_apply_operational_support_preamble() -> None:
    """Every LLM capability prepends the operational-support preamble (Req 12.1)."""

    prompts = _capture_system_prompts()

    # All five capabilities were exercised.
    assert len(prompts) == 5
    for system in prompts:
        assert _SYSTEM_PREAMBLE in system, "capability must apply the preamble"
        assert "operational support only" in system.lower()


# ---------------------------------------------------------------------------
# The mock provider never emits regulated *advice* for representative inputs
# ---------------------------------------------------------------------------


def _collect_strings(value: object, out: list[str]) -> None:
    """Recursively collect every string field from a nested pydantic output."""

    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_strings(item, out)
    elif hasattr(value, "model_dump"):
        _collect_strings(value.model_dump(), out)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_strings(item, out)


def test_mock_outputs_never_read_as_regulated_advice() -> None:
    """Mock outputs are support-only: they never claim to give advice (Req 12.1, 12.2)."""

    provider = MockAIProvider()
    org_id = uuid4()

    outputs = [
        provider.classify_source_item(
            "Please review the investment portfolio and tax filing before the meeting.",
            "Client tax and investment review",
        ),
        provider.extract_knowledge(
            "The client asked about insurance and estate planning options.",
            EntityContext(
                organization_id=org_id,
                entity_id=uuid4(),
                entity_type=BusinessEntityType.CLIENT,
                entity_name="Acme Corp",
            ),
        ),
        provider.generate_daily_brief(OrgContext(organization_id=org_id)),
        provider.generate_entity_brief(
            EntityContext(
                organization_id=org_id,
                entity_id=uuid4(),
                entity_type=BusinessEntityType.CLIENT,
                entity_name="Acme Corp",
            )
        ),
        provider.generate_meeting_summary(
            "Discussed retirement, insurance, and tax. Client wants a will drafted.",
            ClientContext(organization_id=org_id, client_name="Jane Doe"),
        ),
    ]

    strings: list[str] = []
    for output in outputs:
        _collect_strings(output, strings)

    # The mock must never present itself as giving advice of any kind. Note the
    # word "advice" is intentionally banned outright: the mock has no legitimate
    # reason to use it (partner-need signals use bare category nouns like "tax"
    # or "insurance", which are fine — they flag a need, they do not advise).
    for text in strings:
        assert "advice" not in text.lower(), f"mock output reads as advice: {text!r}"
