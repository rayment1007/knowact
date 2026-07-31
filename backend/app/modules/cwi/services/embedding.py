"""Embedding provider abstraction for document retrieval (M6.4, Requirement 29).

Chunk text is turned into vectors behind the :class:`EmbeddingProvider`
protocol so the concrete model is swappable and, crucially, so tests never call
a paid/remote API:

* :class:`OpenAIEmbeddingProvider` — production, uses ``settings.embedding_model``
  (default ``text-embedding-3-small``) via ``settings.openai_api_key``.
* :class:`FakeEmbeddingProvider` — deterministic vectors derived from a hash of
  the text, dimension ``settings.embedding_dimension``. Same text ⇒ same vector,
  so retrieval property tests are reproducible with **no** network I/O.

The concrete provider is resolved via :func:`get_embedding_provider`, selected
by ``settings.embedding_provider`` (default ``"mock"`` → the fake). Route
handlers depend on the injectable so tests always get the fake.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol, runtime_checkable

from app.config import Settings, get_settings


@runtime_checkable
class EmbeddingProvider(Protocol):
    """The seam every embedding computation goes through."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text (order preserved)."""


class FakeEmbeddingProvider:
    """A deterministic, offline :class:`EmbeddingProvider` for tests / local dev.

    Each text is hashed and the digest is expanded into a unit-norm vector of
    ``dimension`` floats. The mapping is a pure function of the text, so the same
    text always yields the same vector (reproducible cosine rankings) and no
    network call is ever made.
    """

    def __init__(self, dimension: int = 1536) -> None:
        self.dimension = dimension
        # Recorded for assertions in tests.
        self.embed_calls: int = 0

    def _vector_for(self, text: str) -> list[float]:
        # Expand a repeated SHA-256 digest into ``dimension`` bytes, map each to
        # a float in [-1, 1], then L2-normalize so cosine similarity is stable.
        raw = bytearray()
        counter = 0
        seed = text.encode("utf-8")
        while len(raw) < self.dimension:
            digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
            raw.extend(digest)
            counter += 1
        components = [
            (raw[i] - 127.5) / 127.5 for i in range(self.dimension)
        ]
        norm = math.sqrt(sum(component * component for component in components))
        if norm == 0.0:
            # Degenerate (only possible for an all-127.5 vector); fall back to a
            # fixed unit vector so the result is always normalized.
            components[0] = 1.0
            norm = 1.0
        return [component / norm for component in components]

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls += 1
        return [self._vector_for(text) for text in texts]


class OpenAIEmbeddingProvider:
    """Production :class:`EmbeddingProvider` backed by the OpenAI embeddings API.

    Constructed only when ``settings.embedding_provider == "openai"`` and an
    ``openai_api_key`` is present (see :func:`get_embedding_provider`); tests
    never construct it. The OpenAI client is imported lazily so importing this
    module never requires the package or a key.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model = settings.embedding_model
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self._settings.openai_api_key)
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._get_client()
        response = client.embeddings.create(model=self._model, input=texts)
        # Preserve input order (the API returns items with an ``index``).
        ordered = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered]


def get_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    """Resolve the :class:`EmbeddingProvider` for the current configuration.

    Uses :class:`OpenAIEmbeddingProvider` only when
    ``settings.embedding_provider == "openai"`` *and* an ``openai_api_key`` is
    configured; otherwise returns the deterministic
    :class:`FakeEmbeddingProvider` (the ``"mock"`` default) so local/mock runs
    and tests need no key and make no network call.
    """

    settings = settings or get_settings()
    if settings.embedding_provider == "openai" and settings.openai_api_key:
        return OpenAIEmbeddingProvider(settings)
    return FakeEmbeddingProvider(dimension=settings.embedding_dimension)


__all__ = [
    "EmbeddingProvider",
    "FakeEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "get_embedding_provider",
]
