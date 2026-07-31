"""Property-based test P18: retrieval permission & sensitivity filtering.

Property 18 (retrieval permission & sensitivity filtering): for all retrieval
queries, every returned ``DocumentChunk`` satisfies ``organization_id ==
caller.org``, is permitted for the authenticated user, respects the source-type
filter, and does not exceed the allowed ``sensitivity`` ceiling;
``HIGHLY_SENSITIVE`` content is never returned to an unauthorized/unacknowledged
caller (Requirements 29.4, 29.5).

Each Hypothesis example seeds two independent organizations, each with a mix of
documents at random sensitivities (indexed with deterministic
:class:`~app.modules.cwi.services.embedding.FakeEmbeddingProvider` embeddings —
no network). It then runs :class:`RetrievalService.retrieve` for org A under a
random filter (sensitivity ceiling, HIGHLY_SENSITIVE authorization, and an
optional source-type restriction) and asserts every returned chunk satisfies the
filter and belongs to org A — never org B, never above the ceiling.

**Validates: Requirements 29.4, 29.5 / Property 18**
"""

from __future__ import annotations

from uuid import uuid4

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

from app.core import models as core_models
from app.core.models import Sensitivity, SourceType
from app.modules.cwi.models import (
    DocumentAsset,
    DocumentChunk,
    DocumentProcessingStatus,
)
from app.modules.cwi.services.embedding import FakeEmbeddingProvider
from app.modules.cwi.services.retrieval_service import (
    RetrievalFilters,
    RetrievalService,
)
from app.security import hash_password

_PW_HASH = hash_password("pw")
_DIM = 32

_SENSITIVITIES = [
    Sensitivity.PUBLIC,
    Sensitivity.INTERNAL,
    Sensitivity.CONFIDENTIAL,
    Sensitivity.HIGHLY_SENSITIVE,
]
_SENSITIVITY_RANK = {s: i for i, s in enumerate(_SENSITIVITIES)}


def _seed_org(db: Session, name: str) -> tuple[core_models.Organization, core_models.User]:
    """Create an org + user with a unique email."""

    suffix = uuid4().hex
    org = core_models.Organization(name=f"{name}-{suffix}")
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


def _seed_indexed_document(
    db: Session,
    org: core_models.Organization,
    user: core_models.User,
    sensitivity: Sensitivity,
    fake: FakeEmbeddingProvider,
) -> DocumentAsset:
    """Insert one INDEXED document with a single embedded chunk."""

    suffix = uuid4().hex
    asset = DocumentAsset(
        organization_id=org.id,
        uploaded_by=user.id,
        filename=f"doc-{suffix}.txt",
        mime_type="text/plain",
        storage_key=suffix,
        checksum=suffix,
        processing_status=DocumentProcessingStatus.INDEXED,
        sensitivity=sensitivity,
        source_deleted=False,
    )
    db.add(asset)
    db.flush()
    text = f"content-{suffix}"
    embedding = fake.embed([text])[0]
    db.add(
        DocumentChunk(
            organization_id=org.id,
            document_asset_id=asset.id,
            chunk_index=0,
            text=text,
            page_number=None,
            metadata_json={"filename": asset.filename},
            embedding=embedding,
        )
    )
    db.flush()
    return asset


def _make_source_types(restrict: bool) -> frozenset[SourceType] | None:
    """Build the source-type filter: either unrestricted or excluding DOCUMENT."""

    if restrict:
        # A permission set that excludes DOCUMENT — no document chunk qualifies.
        return frozenset({SourceType.MEETING_NOTE, SourceType.EMAIL})
    return None


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    sensitivities_a=st.lists(
        st.sampled_from(_SENSITIVITIES), min_size=1, max_size=6
    ),
    sensitivities_b=st.lists(
        st.sampled_from(_SENSITIVITIES), min_size=1, max_size=4
    ),
    ceiling=st.sampled_from(_SENSITIVITIES),
    allow_highly_sensitive=st.booleans(),
    restrict_source=st.booleans(),
)
def test_retrieval_permission_and_sensitivity_filtering(
    db_session: Session,
    sensitivities_a: list[Sensitivity],
    sensitivities_b: list[Sensitivity],
    ceiling: Sensitivity,
    allow_highly_sensitive: bool,
    restrict_source: bool,
) -> None:
    """Every returned chunk is org-scoped, permitted, and within the ceiling.

    **Validates: Requirements 29.4, 29.5 / Property 18**
    """

    fake = FakeEmbeddingProvider(dimension=_DIM)
    org_a, user_a = _seed_org(db_session, "OrgA")
    org_b, user_b = _seed_org(db_session, "OrgB")

    for sensitivity in sensitivities_a:
        _seed_indexed_document(db_session, org_a, user_a, sensitivity, fake)
    org_b_asset_ids = {
        _seed_indexed_document(db_session, org_b, user_b, sensitivity, fake).id
        for sensitivity in sensitivities_b
    }

    service = RetrievalService(db_session, fake)
    filters = RetrievalFilters(
        source_types=_make_source_types(restrict_source),
        max_sensitivity=ceiling,
        allow_highly_sensitive=allow_highly_sensitive,
    )

    results = service.retrieve(
        org_a.id, user_a.id, query="content", filters=filters, k=50
    )

    if restrict_source:
        # DOCUMENT is not in the permitted source types → nothing is returned.
        assert results == []
        return

    ceiling_rank = _SENSITIVITY_RANK[ceiling]
    for chunk in results:
        # Org isolation: never another org's content (Property 13/18).
        assert chunk.source_id not in org_b_asset_ids
        # Source-type filter honored.
        assert chunk.source_type == SourceType.DOCUMENT
        # Sensitivity ceiling honored.
        assert _SENSITIVITY_RANK[chunk.sensitivity] <= ceiling_rank
        # HIGHLY_SENSITIVE only when explicitly authorized/acknowledged.
        if chunk.sensitivity == Sensitivity.HIGHLY_SENSITIVE:
            assert allow_highly_sensitive
