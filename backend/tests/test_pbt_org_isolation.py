"""Property-based test P11: organization isolation is absolute.

Property 11 (org isolation): a user can read or write a row only when
``row.organization_id == user.organization_id``. A request for a row belonging
to a *different* organization must fail with ``404 Not Found`` and reveal
nothing about the row's existence (never a ``403``, which would confirm the row
exists) — Requirements 2.2, 2.3, 2.4.

To exercise the org-scoping helper (:func:`app.dependencies.scope_select`) as it
is actually used across the codebase, each Hypothesis example seeds **two**
fully independent organizations (``org_a`` and ``org_b``), each with its own
user and a representative row per service, and then asserts isolation in **both
directions** across a representative set of Core Engine services:

* **Source items** — :class:`IngestionService` (read via ``get``, write via
  ``dismiss``).
* **Knowledge** — :class:`KnowledgeService` (read via ``get_detail``, write via
  ``reject``).
* **Action items** — :class:`ActionService` (write via ``update_status``, and a
  cross-tenant ``delete`` refusal).
* **Decision records** — :class:`DecisionService` (cross-tenant ``delete``
  refusal, plus scoped ``list`` visibility).

For every seeded row the test asserts:

* the owning org can read it (same-org read returns the same id) — Req 2.2;
* the *other* org's read raises ``404`` and reveals nothing — Req 2.3;
* the *other* org's write raises ``404`` (write refused cross-tenant) — Req 2.4;
* the owning org's write succeeds — Req 2.4.

Because Hypothesis drives many examples through the one function-scoped
``db_session`` fixture, ``HealthCheck.function_scoped_fixture`` is suppressed and
every row uses unique identifiers so examples never collide. Per Requirement
21.3 the property runs a minimum of 100 examples.

**Validates: Requirements 2.2, 2.3, 2.4**
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

from app.core import models as core_models
from app.core.models import (
    ActionItem,
    ActionStatus,
    DecisionRecord,
    KnowledgeItem,
    SourceItem,
    SourceStatus,
    SourceType,
    SuggestionStatus,
)
from app.core.services.action_service import ActionService
from app.core.services.decision_service import DecisionService
from app.core.services.ingestion_service import IngestionService
from app.core.services.knowledge_service import KnowledgeService
from app.security import hash_password

# bcrypt hashing is ~200ms/call; the stored hash is never re-verified here, so
# hash the throwaway password once per module instead of per Hypothesis example.
_PW_HASH = hash_password("pw")

# A fixed tz-aware timestamp for the decision record — the value is irrelevant
# to the isolation property, so it need not be generated.
_DECIDED_AT = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)


class _OrgFixture:
    """A seeded organization plus one representative row per service under test."""

    def __init__(
        self,
        org: core_models.Organization,
        user: core_models.User,
        source_item: SourceItem,
        knowledge: KnowledgeItem,
        action: ActionItem,
        decision: DecisionRecord,
    ) -> None:
        self.org = org
        self.user = user
        self.source_item = source_item
        self.knowledge = knowledge
        self.action = action
        self.decision = decision


def _seed_org(db: Session, content: str, name: str) -> _OrgFixture:
    """Create an org + user and one row per service under test, all org-scoped.

    Every identifier is made unique (via ``uuid4``) so the many Hypothesis
    examples sharing the one function-scoped ``db_session`` never collide on a
    unique column (e.g. user email).
    """

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

    source_item = SourceItem(
        organization_id=org.id,
        created_by=user.id,
        source_type=SourceType.EMAIL,
        title="Subject",
        content=content or "content",
        status=SourceStatus.NEW,
    )
    db.add(source_item)
    db.flush()

    knowledge = KnowledgeItem(
        organization_id=org.id,
        source_item_id=source_item.id,
        summary=content or "summary",
        key_points=["point"],
        evidence_text=content or "evidence",
        knowledge_type="FACT",
        status=SuggestionStatus.SUGGESTED,
    )
    db.add(knowledge)
    db.flush()

    action = ActionItem(
        organization_id=org.id,
        title=content or "Action",
        status=ActionStatus.OPEN,
    )
    db.add(action)
    db.flush()

    decision = DecisionRecord(
        organization_id=org.id,
        title=content or "Decision",
        decision="We will proceed.",
        rationale=content or "rationale",
        evidence_text=content or "evidence",
        decided_by=user.id,
        decided_at=_DECIDED_AT,
    )
    db.add(decision)
    db.flush()

    return _OrgFixture(org, user, source_item, knowledge, action, decision)


def _assert_cross_org_404(call) -> None:
    """A cross-tenant access must raise ``404`` and reveal nothing (Req 2.3)."""

    with pytest.raises(HTTPException) as exc:
        call()
    # 404 (not 403): the row's existence is never revealed to another tenant.
    assert exc.value.status_code == 404


def _check_isolation(
    db: Session,
    owner: _OrgFixture,
    other: _OrgFixture,
) -> None:
    """Assert every service isolates ``owner``'s rows from ``other`` (both dirs).

    Reads and writes succeed only when the caller's ``organization_id`` matches
    the row's, and cross-tenant access always yields ``404``.
    """

    ingestion = IngestionService(db)
    knowledge_service = KnowledgeService(db)
    action_service = ActionService(db)
    decision_service = DecisionService(db)

    owner_org = owner.org.id
    other_org = other.org.id

    # -- Source items (IngestionService): read + write ----------------------
    assert ingestion.get(owner_org, owner.source_item.id).id == owner.source_item.id
    _assert_cross_org_404(lambda: ingestion.get(other_org, owner.source_item.id))
    # Write (Req 2.4): cross-tenant dismiss refused; same-tenant dismiss allowed.
    _assert_cross_org_404(
        lambda: ingestion.dismiss(other_org, owner.source_item.id, other.user.id)
    )
    dismissed = ingestion.dismiss(owner_org, owner.source_item.id, owner.user.id)
    assert dismissed.status == SourceStatus.DISMISSED

    # -- Knowledge (KnowledgeService): read + write -------------------------
    read_knowledge, _, _ = knowledge_service.get_detail(owner_org, owner.knowledge.id)
    assert read_knowledge.id == owner.knowledge.id
    _assert_cross_org_404(
        lambda: knowledge_service.get_detail(other_org, owner.knowledge.id)
    )
    _assert_cross_org_404(
        lambda: knowledge_service.reject(other_org, owner.knowledge.id, other.user.id)
    )
    rejected = knowledge_service.reject(owner_org, owner.knowledge.id, owner.user.id)
    assert rejected.status == SuggestionStatus.REJECTED

    # -- Action items (ActionService): write + cross-tenant delete refusal --
    _assert_cross_org_404(
        lambda: action_service.update_status(
            other_org, owner.action.id, other.user.id, ActionStatus.DONE
        )
    )
    _assert_cross_org_404(
        lambda: action_service.delete(other_org, owner.action.id, other.user.id)
    )
    updated = action_service.update_status(
        owner_org, owner.action.id, owner.user.id, ActionStatus.DONE
    )
    assert updated.status == ActionStatus.DONE
    # The cross-tenant delete really was refused, not silently applied.
    assert db.get(ActionItem, owner.action.id) is not None

    # -- Decision records (DecisionService): scoped list + delete refusal ---
    _assert_cross_org_404(
        lambda: decision_service.delete(other_org, owner.decision.id, other.user.id)
    )
    assert db.get(DecisionRecord, owner.decision.id) is not None
    other_visible = {row.id for row in decision_service.list(other_org)}
    assert owner.decision.id not in other_visible
    owner_visible = {row.id for row in decision_service.list(owner_org)}
    assert owner.decision.id in owner_visible


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    content_a=st.text(min_size=0, max_size=200),
    content_b=st.text(min_size=0, max_size=200),
)
def test_org_isolation_across_services(
    db_session: Session,
    content_a: str,
    content_b: str,
) -> None:
    """Rows are readable/writable only within their own org; cross-org → 404.

    **Validates: Requirements 2.2, 2.3, 2.4**
    """

    org_a = _seed_org(db_session, content_a, "OrgA")
    org_b = _seed_org(db_session, content_b, "OrgB")

    # Isolation must hold symmetrically: neither org can reach the other's rows.
    _check_isolation(db_session, owner=org_a, other=org_b)
    _check_isolation(db_session, owner=org_b, other=org_a)
