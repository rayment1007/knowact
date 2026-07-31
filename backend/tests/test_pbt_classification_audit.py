"""Property-based test P6: audit completeness on classification confirm.

Property 6 (audit completeness — classification): for *any* generated source
item and *any* optional human override, confirming its classification writes
**exactly one** ``CONFIRM_CLASSIFICATION`` audit row for that item, and that row
lives in the *same* transaction as the confirm mutation — rolling back the
caller's savepoint removes it. This proves the confirm/audit pair is atomic and
that no confirmation ever produces zero, duplicate, or orphaned audit entries
(Requirements 5.4, 13.1).

The property is exercised end-to-end against the SQLite ``db_session`` fixture:
each Hypothesis example creates a *fresh* org/user/source item (Hypothesis runs
many examples within one function body, while ``db_session`` is rolled back only
once at teardown), so the audit count is always scoped to the specific item's
``target_id`` to prove "exactly one" without per-example cross-contamination.

**Validates: Requirements 5.4, 13.1**
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import models as core_models
from app.core.models import (
    AuditLog,
    BusinessCategory,
    Relevance,
    Sensitivity,
    SourceStatus,
    SourceType,
)
from app.core.schemas import ClassificationOverride
from app.core.services.classification_service import (
    CONFIRM_CLASSIFICATION,
    ClassificationService,
)
from app.security import hash_password

# bcrypt hashing is ~200ms/call; the stored hash is never re-verified here, so
# hash the throwaway password once per module instead of per Hypothesis example.
_PW_HASH = hash_password("pw")


# ---------------------------------------------------------------------------
# Helpers (mirroring tests/test_classification_service.py patterns)
# ---------------------------------------------------------------------------


def _make_org_and_user(
    db: Session, org_name: str, email: str
) -> tuple[core_models.Organization, core_models.User]:
    org = core_models.Organization(name=org_name)
    db.add(org)
    db.flush()
    user = core_models.User(
        organization_id=org.id,
        email=email,
        full_name="Actor",
        password_hash=_PW_HASH,
        role="ADMIN",
    )
    db.add(user)
    db.flush()
    return org, user


def _make_item(
    db: Session,
    org: core_models.Organization,
    user: core_models.User,
    title: str,
    content: str,
) -> core_models.SourceItem:
    item = core_models.SourceItem(
        organization_id=org.id,
        created_by=user.id,
        source_type=SourceType.EMAIL,
        title=title,
        content=content,
        status=SourceStatus.NEW,
    )
    db.add(item)
    db.flush()
    return item


def _count_confirm_audit_for_item(db: Session, item_id) -> int:
    """Count CONFIRM_CLASSIFICATION audit rows scoped to a single item."""

    stmt = (
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action_type == CONFIRM_CLASSIFICATION)
        .where(AuditLog.target_id == item_id)
    )
    return db.execute(stmt).scalar_one()


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Arbitrary text (unicode, empty, whitespace) for the source item's fields; the
# audit-completeness property must hold regardless of item content.
_text_strategy = st.one_of(
    st.text(max_size=200),
    st.text(alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x2FFF), max_size=80),
    st.sampled_from(["", " ", "\t", "project milestone approved", "confidential salary"]),
)


@st.composite
def _overrides(draw: st.DrawFn) -> ClassificationOverride | None:
    """An optional human override; each axis is independently present or not."""

    if draw(st.booleans()):
        return None
    return ClassificationOverride(
        relevance=draw(st.one_of(st.none(), st.sampled_from(list(Relevance)))),
        business_category=draw(
            st.one_of(st.none(), st.sampled_from(list(BusinessCategory)))
        ),
        sensitivity=draw(st.one_of(st.none(), st.sampled_from(list(Sensitivity)))),
    )


# ---------------------------------------------------------------------------
# Property P6
# ---------------------------------------------------------------------------


# The single ``db_session`` is intentionally shared across examples and rolled
# back once at teardown; each example creates its own fresh org/user/item and
# scopes every assertion to that item's ``target_id``, so the function-scoped
# fixture is safe here (suppress the corresponding Hypothesis health check).
# ``deadline=None``: each example does real DB work plus a bcrypt password hash
# for the fresh user, which can exceed Hypothesis's default per-example deadline
# on some machines without indicating any correctness problem.
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(title=_text_strategy, content=_text_strategy, override=_overrides())
def test_confirm_writes_exactly_one_audit_in_same_transaction(
    db_session: Session,
    title: str,
    content: str,
    override: ClassificationOverride | None,
) -> None:
    """confirm writes exactly one CONFIRM_CLASSIFICATION audit row, atomically.

    For any source item + optional override: after ``classify`` then
    ``confirm``, exactly one ``CONFIRM_CLASSIFICATION`` audit row exists for the
    item (``target_id == item.id``), and it shares the confirm transaction —
    rolling back the caller's savepoint removes it (Requirements 5.4, 13.1).
    """

    # Fresh tenant + item per example (unique email avoids the users unique
    # constraint across examples that share the function-scoped session).
    suffix = db_session.execute(
        select(func.count()).select_from(core_models.User)
    ).scalar_one()
    org, user = _make_org_and_user(
        db_session, f"Org {suffix}", f"actor{suffix}@example.com"
    )
    item = _make_item(db_session, org, user, title=title, content=content)

    service = ClassificationService(db_session)
    service.classify(org.id, item.id)

    # No confirm audit row exists before confirming.
    assert _count_confirm_audit_for_item(db_session, item.id) == 0

    # Confirm inside a savepoint so we can prove the audit row is part of the
    # same transaction as the state change.
    savepoint = db_session.begin_nested()
    service.confirm(org.id, item.id, actor_id=user.id, override=override)

    # Exactly one CONFIRM_CLASSIFICATION audit row for this item (Req 5.4/13.1).
    assert _count_confirm_audit_for_item(db_session, item.id) == 1

    # Rolling back the caller's savepoint removes the audit row together with
    # the confirm mutation, proving they share one transaction (Req 5.4/13.1).
    savepoint.rollback()
    assert _count_confirm_audit_for_item(db_session, item.id) == 0
