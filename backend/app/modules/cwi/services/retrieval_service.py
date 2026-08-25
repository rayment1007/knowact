"""Permission- and sensitivity-filtered document retrieval (M6.4, Req 29.4-29.7).

:class:`RetrievalService` is the only path by which document chunk content
reaches the LLM at query time, and it is deliberately *narrow*: it returns a
**bounded, filtered** slice — never every historical chunk (Requirement 29.6).

Security-critical ordering (Requirement 29.4, 29.5 / Property 18, Property 13)
------------------------------------------------------------------------------
Every one of these is applied as an ordinary **SQL ``WHERE`` predicate, BEFORE
any similarity ranking**, so a chunk that fails a filter is never even loaded
into memory to be ranked:

* ``organization_id == org_id`` on both the chunk and its asset (tenant
  isolation — cross-org content is unreachable);
* the caller's permitted **source types** (a document chunk qualifies only when
  ``DOCUMENT`` is permitted);
* the **sensitivity ceiling** — the asset's ``sensitivity`` must be within the
  caller's allowed set, and ``HIGHLY_SENSITIVE`` is excluded entirely unless the
  caller is explicitly authorized/acknowledged;
* only ``INDEXED``, non-``source_deleted`` assets are eligible.

Only *after* those SQL predicates have narrowed the candidate set does the
service rank the survivors by **cosine similarity** to the query embedding —
computed in **Python** so behavior is identical on PostgreSQL and the SQLite
test database — and return the top ``k``. The connected-business-entity filter,
when supplied, is applied to the already org-scoped survivors before ranking.

Each returned :class:`RetrievedChunk` carries exactly the fields needed to build
a citation: source type, source id, title, evidence excerpt, timestamp, and a
deep-link target (Requirement 29.7).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import Sensitivity, SourceType
from app.dependencies import scope_select
from app.modules.cwi.models import (
    DocumentAsset,
    DocumentChunk,
    DocumentProcessingStatus,
)
from app.modules.cwi.services.embedding import EmbeddingProvider

# Sensitivity ordered from least to most protected. The index is the rank used
# for the ceiling comparison.
_SENSITIVITY_ORDER: tuple[Sensitivity, ...] = (
    Sensitivity.PUBLIC,
    Sensitivity.INTERNAL,
    Sensitivity.CONFIDENTIAL,
    Sensitivity.HIGHLY_SENSITIVE,
)

# A hard cap on the number of results, so retrieval is always bounded even if a
# caller requests a very large ``k`` (Requirement 29.6).
_MAX_K = 50

# Evidence excerpts are bounded so a citation carries a focused snippet.
_EXCERPT_MAX_CHARS = 1000


def _sensitivity_rank(sensitivity: Sensitivity) -> int:
    return _SENSITIVITY_ORDER.index(sensitivity)


def _allowed_sensitivities(
    max_sensitivity: Sensitivity, allow_highly_sensitive: bool
) -> list[Sensitivity]:
    """Return the sensitivities a caller may receive, respecting the ceiling.

    Everything at or below ``max_sensitivity`` is allowed; ``HIGHLY_SENSITIVE``
    is additionally gated behind ``allow_highly_sensitive`` so it is never
    returned to an unauthorized/unacknowledged caller (Requirement 29.5).
    """

    ceiling = _sensitivity_rank(max_sensitivity)
    allowed = [
        sensitivity
        for sensitivity in _SENSITIVITY_ORDER
        if _sensitivity_rank(sensitivity) <= ceiling
    ]
    if not allow_highly_sensitive and Sensitivity.HIGHLY_SENSITIVE in allowed:
        allowed.remove(Sensitivity.HIGHLY_SENSITIVE)
    return allowed


@dataclass
class RetrievalFilters:
    """The permission/relevance filters applied before ranking (Req 29.4).

    Attributes:
        source_types: The source types the caller may retrieve. ``None`` means
            no source-type restriction; when a set is given, document chunks
            qualify only if :attr:`SourceType.DOCUMENT` is included.
        business_entity_id: When set, restrict to chunks tagged with this
            business entity (the "connected business entity when relevant"
            filter).
        max_sensitivity: The sensitivity ceiling; assets above it are excluded.
        allow_highly_sensitive: Whether the caller is authorized/acknowledged to
            receive ``HIGHLY_SENSITIVE`` content. Defaults to ``False``.
    """

    source_types: frozenset[SourceType] | None = None
    business_entity_id: UUID | None = None
    max_sensitivity: Sensitivity = Sensitivity.CONFIDENTIAL
    allow_highly_sensitive: bool = False


@dataclass
class RetrievedChunk:
    """A retrieved chunk with everything needed to build a citation (Req 29.7)."""

    chunk_id: UUID
    document_asset_id: UUID
    source_type: SourceType
    source_id: UUID
    title: str
    evidence_excerpt: str
    timestamp: datetime
    deep_link: str
    chunk_index: int
    page_number: int | None
    sensitivity: Sensitivity
    score: float
    metadata: dict = field(default_factory=dict)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Return the cosine similarity of two equal-length vectors."""

    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class RetrievalService:
    """Filtered, bounded, similarity-ranked document chunk retrieval (Req 29)."""

    def __init__(
        self, db: Session, embedding_provider: EmbeddingProvider
    ) -> None:
        """Bind the service to a session and the injected embedding provider."""

        self.db = db
        self.embedding_provider = embedding_provider

    def retrieve(
        self,
        org_id: UUID,
        user_id: UUID,
        query: str,
        filters: RetrievalFilters | None = None,
        k: int = 10,
    ) -> list[RetrievedChunk]:
        """Return the top-``k`` permitted chunks for ``query`` (Req 29.4-29.7).

        The org / source-type / sensitivity filters are applied as SQL ``WHERE``
        predicates before ranking; the survivors are then ranked by cosine
        similarity to the query embedding (in Python) and the bounded top ``k``
        is returned. Documents are restricted to the current uploader so a
        second user in the same organization cannot retrieve personal files.
        """

        filters = filters or RetrievalFilters()
        bounded_k = max(0, min(k, _MAX_K))
        if bounded_k == 0:
            return []

        # A document chunk's source type is DOCUMENT; if the caller's permitted
        # source types exclude it, no document chunk qualifies.
        if (
            filters.source_types is not None
            and SourceType.DOCUMENT not in filters.source_types
        ):
            return []

        allowed_sensitivities = _allowed_sensitivities(
            filters.max_sensitivity, filters.allow_highly_sensitive
        )
        if not allowed_sensitivities:
            return []

        # --- SQL WHERE predicates (evaluated BEFORE ranking) ---------------
        stmt = (
            scope_select(select(DocumentChunk), DocumentChunk, org_id)
            .join(
                DocumentAsset,
                DocumentAsset.id == DocumentChunk.document_asset_id,
            )
            # Tenant isolation on the asset too (Property 13).
            .where(DocumentAsset.organization_id == org_id)
            .where(DocumentAsset.uploaded_by == user_id)
            # Only indexed, non-deleted documents are retrievable.
            .where(
                DocumentAsset.processing_status
                == DocumentProcessingStatus.INDEXED
            )
            .where(DocumentAsset.source_deleted.is_(False))
            # Sensitivity ceiling + HIGHLY_SENSITIVE gate (Requirement 29.5).
            .where(DocumentAsset.sensitivity.in_(allowed_sensitivities))
        )

        rows = list(self.db.execute(stmt).scalars().all())

        # Load the assets for citation fields (org-scoped map).
        asset_ids = {row.document_asset_id for row in rows}
        assets: dict[UUID, DocumentAsset] = {}
        if asset_ids:
            asset_stmt = scope_select(
                select(DocumentAsset), DocumentAsset, org_id
            ).where(
                DocumentAsset.id.in_(asset_ids),
                DocumentAsset.uploaded_by == user_id,
            )
            assets = {
                asset.id: asset
                for asset in self.db.execute(asset_stmt).scalars().all()
            }

        # Optional business-entity narrowing over the org-scoped survivors,
        # applied before ranking.
        if filters.business_entity_id is not None:
            wanted = str(filters.business_entity_id)
            rows = [
                row
                for row in rows
                if str((row.metadata_json or {}).get("business_entity_id", ""))
                == wanted
            ]

        # --- Similarity ranking (Python; portable across SQLite/PG) --------
        query_vector = self.embedding_provider.embed([query])[0]
        scored: list[tuple[float, DocumentChunk]] = []
        for row in rows:
            asset = assets.get(row.document_asset_id)
            if asset is None or row.embedding is None:
                continue
            score = _cosine_similarity(query_vector, list(row.embedding))
            scored.append((score, row))

        scored.sort(key=lambda pair: (pair[0], str(pair[1].id)), reverse=True)
        top = scored[:bounded_k]

        results: list[RetrievedChunk] = []
        for score, row in top:
            asset = assets[row.document_asset_id]
            excerpt = (row.text or "")[:_EXCERPT_MAX_CHARS]
            results.append(
                RetrievedChunk(
                    chunk_id=row.id,
                    document_asset_id=asset.id,
                    source_type=SourceType.DOCUMENT,
                    source_id=asset.id,
                    title=asset.filename,
                    evidence_excerpt=excerpt,
                    timestamp=asset.created_at,
                    deep_link=f"/documents/{asset.id}#chunk-{row.chunk_index}",
                    chunk_index=row.chunk_index,
                    page_number=row.page_number,
                    sensitivity=asset.sensitivity,
                    score=score,
                    metadata=dict(row.metadata_json or {}),
                )
            )
        return results


__all__ = [
    "RetrievalService",
    "RetrievalFilters",
    "RetrievedChunk",
]
