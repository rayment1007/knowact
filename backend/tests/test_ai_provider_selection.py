"""Unit tests for AI provider selection and the fallback policy.

Covers :func:`app.dependencies.get_ai_provider` and
:func:`app.dependencies.call_with_fallback`:

* selection resolves to ``MockAIProvider`` by default and only to
  ``LLMProvider`` when ``AI_PROVIDER=llm`` *and* an API key is present
  (Requirements 11.2, 11.3);
* ``call_with_fallback`` falls back to the mock with a logged warning in
  DEVELOPMENT and raises a retriable ``503`` in PRODUCTION, without mutating
  anything on unrecoverable failure (Requirement 11.6).
"""

from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException, status

from app.config import Settings
from app.core.services.ai_provider import (
    AIProviderError,
    ClassificationOutput,
    LLMProvider,
    MockAIProvider,
)
from app.dependencies import call_with_fallback, get_ai_provider


# ---------------------------------------------------------------------------
# get_ai_provider selection (Requirements 11.2, 11.3)
# ---------------------------------------------------------------------------


def test_get_ai_provider_defaults_to_mock() -> None:
    """With the default mock configuration, selection returns the mock."""

    settings = Settings(ai_provider="mock", llm_api_key=None)
    provider = get_ai_provider(settings)
    assert isinstance(provider, MockAIProvider)


def test_get_ai_provider_mock_even_with_key() -> None:
    """A key present but provider=mock still resolves to the mock."""

    settings = Settings(ai_provider="mock", llm_api_key="sk-present")
    assert isinstance(get_ai_provider(settings), MockAIProvider)


def test_get_ai_provider_llm_without_key_falls_back_to_mock() -> None:
    """provider=llm but no key must not select the LLM provider."""

    settings = Settings(ai_provider="llm", llm_api_key=None)
    assert isinstance(get_ai_provider(settings), MockAIProvider)


def test_get_ai_provider_llm_with_key_selects_llm() -> None:
    """provider=llm and a key present resolves to the LLM provider."""

    settings = Settings(ai_provider="llm", llm_api_key="sk-present")
    assert isinstance(get_ai_provider(settings), LLMProvider)


# ---------------------------------------------------------------------------
# call_with_fallback policy (Requirement 11.6)
# ---------------------------------------------------------------------------


def _classify_op(provider: object) -> ClassificationOutput:
    return provider.classify_source_item("Quarterly project milestone review", "Update")


def _make_failing_llm(settings: Settings) -> LLMProvider:
    """Build an :class:`LLMProvider` whose every operation fails offline.

    The real :class:`LLMProvider` would otherwise attempt a network call. We
    replace its client so the adapter raises :class:`AIProviderError` (exactly
    what a real API error/timeout produces) without touching the network — this
    keeps the fallback-policy tests hermetic while preserving their intent.
    """

    provider = LLMProvider(settings)
    provider._client = None
    provider._init_error = RuntimeError("network disabled in tests")
    return provider


def test_call_with_fallback_returns_primary_result_on_success() -> None:
    """When the primary succeeds, its result is returned unchanged."""

    settings = Settings(mode="DEVELOPMENT")
    primary = MockAIProvider()
    result = call_with_fallback(primary, settings, _classify_op)
    assert isinstance(result, ClassificationOutput)


def test_call_with_fallback_development_falls_back_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DEVELOPMENT: an LLM failure falls back to the mock and logs a warning."""

    settings = Settings(mode="DEVELOPMENT", ai_provider="llm", llm_api_key="sk-present")
    primary = _make_failing_llm(settings)  # every op raises AIProviderError

    with caplog.at_level(logging.WARNING):
        result = call_with_fallback(primary, settings, _classify_op)

    # Fallback produced a real, deterministic mock result...
    assert isinstance(result, ClassificationOutput)
    # ...and the degradation was surfaced to the logs.
    assert any(
        record.levelno == logging.WARNING and "MockAIProvider" in record.getMessage()
        for record in caplog.records
    )


def test_call_with_fallback_production_raises_503() -> None:
    """PRODUCTION: an LLM failure surfaces as a retriable 503, no silent fallback."""

    settings = Settings(mode="PRODUCTION", ai_provider="llm", llm_api_key="sk-present")
    primary = _make_failing_llm(settings)

    with pytest.raises(HTTPException) as exc_info:
        call_with_fallback(primary, settings, _classify_op)

    assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


def test_call_with_fallback_non_llm_error_propagates() -> None:
    """A provider error from a non-LLM primary is not swallowed by fallback."""

    settings = Settings(mode="DEVELOPMENT")

    def _boom(_provider: object) -> None:
        raise AIProviderError("mock exploded")

    with pytest.raises(AIProviderError):
        call_with_fallback(MockAIProvider(), settings, _boom)
