"""Regression tests for ``get_db``'s real transaction semantics.

The rest of the suite overrides ``get_db`` with a rollback-per-test fixture
(see ``conftest.py``), so no other test exercises what ``get_db`` actually does
at the request boundary. This module deliberately does **not** use that
override: it points ``app.database.SessionLocal`` at the in-memory SQLite test
engine and drives the ``get_db`` generator by hand to prove that

1. exhausting the generator after a write commits (the row is visible from a
   separate session), and
2. throwing into the generator (a simulated request error) rolls the write back
   (the row is not visible from a separate session).

This is the behavior that was missing: ``get_db`` previously only closed the
session, so every API write was silently discarded when the session closed.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app import database
from app.core import models as core_models


@pytest.fixture()
def session_local_on_test_engine(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    """Point ``app.database.SessionLocal`` at the SQLite test engine.

    ``get_db`` builds its session from the module-level ``SessionLocal``, so
    repointing that factory lets us exercise the real dependency against the
    hermetic in-memory database instead of live PostgreSQL. A separate factory
    is returned for opening independent verification sessions.
    """

    testing_session_local = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
        future=True,
    )
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    yield testing_session_local


def test_get_db_commits_on_success(
    session_local_on_test_engine: sessionmaker[Session],
) -> None:
    """Exhausting the generator after a write persists the row (commit path)."""

    gen = database.get_db()
    db = next(gen)
    org = core_models.Organization(name="Committed Org")
    db.add(org)
    db.flush()
    org_id = org.id

    # Exhaust the generator so the ``yield db; db.commit()`` path runs.
    with pytest.raises(StopIteration):
        next(gen)

    verify = session_local_on_test_engine()
    try:
        found = verify.get(core_models.Organization, org_id)
        assert found is not None
        assert found.name == "Committed Org"
    finally:
        verify.close()
        # Keep the shared in-memory DB clean for other tests.
        cleanup = session_local_on_test_engine()
        try:
            obj = cleanup.get(core_models.Organization, org_id)
            if obj is not None:
                cleanup.delete(obj)
                cleanup.commit()
        finally:
            cleanup.close()


def test_get_db_rolls_back_on_error(
    session_local_on_test_engine: sessionmaker[Session],
) -> None:
    """Throwing into the generator after a write discards the row (rollback)."""

    gen = database.get_db()
    db = next(gen)
    org = core_models.Organization(name="Rolled Back Org")
    db.add(org)
    db.flush()
    org_id = org.id

    # Simulate the request handler raising: get_db must roll back and re-raise.
    with pytest.raises(RuntimeError, match="boom"):
        gen.throw(RuntimeError("boom"))

    verify = session_local_on_test_engine()
    try:
        found = verify.get(core_models.Organization, org_id)
        assert found is None
    finally:
        verify.close()
