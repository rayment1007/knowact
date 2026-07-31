"""Property-based test P1: classification confidence is bounded.

Property 1 (confidence bounds): for *any* source item, the confidence returned
by :meth:`MockAIProvider.classify_source_item` must lie within the closed
interval ``[0.0, 1.0]`` (Requirement 4.3). The mock provider is pure and
deterministic, so we can exercise it directly over a wide space of generated
``content`` / ``title`` strings — including unicode, whitespace-only,
empty-ish, and keyword-bearing text — with no database or network.

**Validates: Requirements 4.3**
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.services.ai_provider import MockAIProvider

# Keyword-bearing fragments drawn from the mock provider's lexicons so the
# generator regularly produces text that actually triggers scoring (rather than
# always falling through to the default/low-confidence path).
_KEYWORD_FRAGMENTS = [
    "unsubscribe",
    "notification",
    "newsletter",
    "birthday lunch",
    "decision approved",
    "risk blocker overdue",
    "partner referral",
    "training webinar",
    "project milestone",
    "meeting agenda minutes",
    "action follow up",
    "client portfolio",
    "confidential salary",
    "internal do not forward",
    "account number passport",
]

# A strategy over arbitrary text: mixes fully arbitrary unicode text (which
# stresses whitespace/empty/unicode handling) with keyword-bearing fragments
# (which stress the scoring branches).
_text_strategy = st.one_of(
    st.text(),
    st.text(alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x2FFF)),
    st.sampled_from(_KEYWORD_FRAGMENTS),
    st.lists(st.sampled_from(_KEYWORD_FRAGMENTS), max_size=4).map(" ".join),
    st.sampled_from(["", " ", "\t", "\n  \n", "   \u3000  "]),
)


@settings(max_examples=100)
@given(content=_text_strategy, title=_text_strategy)
def test_confidence_within_bounds(content: str, title: str) -> None:
    """classify(...).confidence is always within [0.0, 1.0] (Requirement 4.3)."""

    provider = MockAIProvider()
    result = provider.classify_source_item(content=content, title=title)

    assert 0.0 <= result.confidence <= 1.0
