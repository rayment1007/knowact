"""Property-based tests for :class:`app.core.services.ai_provider.MockAIProvider`.

These tests exercise the deterministic, rule-based default provider with
Hypothesis, configured for a minimum of 100 iterations per generated property
(Requirement 21.3).

Properties implemented here
---------------------------
* **P2 — Deterministic mock (Requirement 11.4).** For any generated
  ``(content, title)`` input, calling :meth:`MockAIProvider.classify_source_item`
  twice with the *same* input must yield *identical* output. Determinism is the
  guarantee that makes the mock provider suitable for reproducible tests and
  offline development, and it is what lets the fallback policy substitute the
  mock without changing observable behavior.

Only the property named by task 7.4 (P2) is implemented in this file.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.services.ai_provider import ClassificationOutput, MockAIProvider

# Minimum iterations mandated for property-based tests (Requirement 21.3).
MIN_ITERATIONS = 100

# A single provider instance is sufficient: the mock is stateless and pure, so
# reusing it also guards against any accidental hidden state between calls.
_PROVIDER = MockAIProvider()


def _text_strategy() -> st.SearchStrategy[str]:
    """Strategy generating diverse, realistic ``content``/``title`` strings.

    The pool deliberately mixes:

    * arbitrary unicode text (``st.text``) including control/whitespace chars,
    * empty and whitespace-only strings (empty-ish inputs),
    * classification keywords drawn from the mock's lexicons so a meaningful
      fraction of examples actually trigger non-default relevance / category /
      sensitivity branches (rather than every example falling through to the
      defaults), exercising the determinism guarantee across real code paths.
    """

    keywords = st.sampled_from(
        [
            # relevance noise signals
            "unsubscribe",
            "no-reply",
            "newsletter",
            "birthday lunch",
            # business_category signals
            "we will proceed",
            "this is a blocker risk",
            "partner referral",
            "project milestone",
            "meeting agenda minutes",
            "please send the action",
            "client portfolio",
            # sensitivity signals
            "confidential",
            "salary and passport",
            "internal use only",
            # unicode / whitespace / empties
            "café résumé — naïve façade 日本語 🚀",
            "   ",
            "",
            "\t\n  \r",
        ]
    )
    # Build strings by joining a few fragments, each either arbitrary unicode
    # text or a lexicon-bearing keyword phrase.
    fragment = st.one_of(st.text(max_size=120), keywords)
    return st.lists(fragment, max_size=6).map(" ".join)


@settings(max_examples=MIN_ITERATIONS)
@given(content=_text_strategy(), title=_text_strategy())
def test_p2_classify_source_item_is_deterministic(content: str, title: str) -> None:
    """P2: identical input yields identical ``classify_source_item`` output.

    **Validates: Requirements 11.4, 21.3**
    """

    first = _PROVIDER.classify_source_item(content, title)
    second = _PROVIDER.classify_source_item(content, title)

    # Both calls must return the structured output type ...
    assert isinstance(first, ClassificationOutput)
    assert isinstance(second, ClassificationOutput)

    # ... and be identical field-for-field. ``model_dump()`` gives a complete,
    # order-sensitive structural comparison across every axis, the confidence,
    # the reasons list, and the evidence spans.
    assert first.model_dump() == second.model_dump()
