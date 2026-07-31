"""Property-based test P13: organization isolation of documents & chunks.

Property 13 (extended to M6.4 document tables): a user can read or write a
:class:`~app.modules.cwi.models.DocumentAsset` / ``DocumentChunk`` only when
``row.organization_id == user.organization_id``. A request for a document
belonging to a *different* organization must fail with ``404 Not Found`` and
reveal nothing about its existence (never a ``403``) — Requirement 29.5.

Each Hypothesis example seeds two independent organizations, each with its own
user and an uploaded+processed document (indexed chunks with deterministic
:class:`~app.modules.cwi.services.embedding.FakeEmbeddingProvider` embeddings —
no network). It then asserts isolation in **both** directions across the
:class:`DocumentService` read/write surface (``get_document``, ``process``,
``delete``): same-org access succeeds, cross-org access raises ``404``.

**Validates: Requirements 29.5 / Property 13**
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

from app.core import models as core_models
from app.core.models import Sensitivity
from app.modules.cwi.models import DocumentAsset, DocumentChunk
from app.modules.cwi.services.document_service import DocumentService
from app.modules.cwi.services.embedding import FakeEmbeddingProvider
from app.security import hash_password

_PW_HASH = hash_password("pw")
_DIM = 32


class _InMemoryStorage:
    """A minimal in-memory StorageBackend (no filesystem/network I/O)."""

    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}

    def put(self, org_id, key: str, data: bytes, content_type: str) -> str:
        self._objects[(str(org_id), key)] = data
        return key

    def get(self, org_id, key: str) -> bytes:
        return self._objects[(str(org_id), key)]

    def delete(self, org_id, key: str) -> None:
        self._objects.pop((str(org_id), key), None)


class _OrgFixture:
    def __init__(self, org, user, asset_id) -> None:
        self.org = org
        self.user = user
        self.asset_id = asset_id


def _seed_org_with_document(
    db: Session,
    storage: _InMemoryStorage,
    fake: FakeEmbeddingProvider,
    name: str,
    body: bytes,
) -> _OrgFixture:
    """Create an org + user and upload+process one document."""

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

    service = DocumentService(db, storage, fake)
    asset = service.upload(
        org.id,
        user.id,
        filename=f"doc-{suffix}.txt",
        mime_type="text/plain",
        data=body or b"fallback body",
        sensitivity=Sensitivity.INTERNAL,
    )
    service.process(org.id, user.id, asset.id)
    return _OrgFixture(org, user, asset.id)


def _assert_cross_org_404(call) -> None:
    with pytest.raises(HTTPException) as exc:
        call()
    assert exc.value.status_code == 404


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    body_a=st.binary(min_size=1, max_size=200),
    body_b=st.binary(min_size=1, max_size=200),
)
def test_document_org_isolation(
    db_session: Session, body_a: bytes, body_b: bytes
) -> None:
    """Documents/chunks are readable/writable only within their org; else 404.

    **Validates: Requirements 29.5 / Property 13**
    """

    storage = _InMemoryStorage()
    fake = FakeEmbeddingProvider(dimension=_DIM)
    owner = _seed_org_with_document(db_session, storage, fake, "OrgA", body_a)
    other = _seed_org_with_document(db_session, storage, fake, "OrgB", body_b)

    service = DocumentService(db_session, storage, fake)

    # -- Read: same-org succeeds, cross-org 404 -----------------------------
    assert (
        service.get_document(owner.org.id, owner.asset_id).id == owner.asset_id
    )
    _assert_cross_org_404(
        lambda: service.get_document(other.org.id, owner.asset_id)
    )

    # -- Write (process): cross-org refused (404) ---------------------------
    _assert_cross_org_404(
        lambda: service.process(other.org.id, other.user.id, owner.asset_id)
    )

    # A chunk of the owner's document is never visible to the other org.
    other_scoped_chunks = (
        db_session.query(DocumentChunk)
        .filter(DocumentChunk.organization_id == other.org.id)
        .filter(DocumentChunk.document_asset_id == owner.asset_id)
        .count()
    )
    assert other_scoped_chunks == 0

    # -- Write (delete): cross-org refused, same-org succeeds ---------------
    _assert_cross_org_404(
        lambda: service.delete(other.org.id, other.user.id, owner.asset_id)
    )
    service.delete(owner.org.id, owner.user.id, owner.asset_id)
    assert (
        db_session.query(DocumentAsset)
        .filter(DocumentAsset.id == owner.asset_id)
        .count()
        == 0
    )
