"""Shared pytest fixtures for the backend test suite.

These fixtures make the auth/security tests fully **self-contained**: they run
against an ephemeral in-memory SQLite database with no live PostgreSQL server
required. The application's domain models use PostgreSQL-specific column types
(``JSONB`` and ``UUID``) plus a partial unique index, none of which SQLite
understands natively, so this module registers small SQLite compilation
variants for those types before creating the schema.

The key pieces:

* :func:`_register_sqlite_type_compilers` teaches SQLite how to emit DDL for the
  PostgreSQL ``JSONB`` and ``UUID`` column types (as ``JSON`` and ``CHAR(32)``).
  The ORM value handling for both types already works on non-PostgreSQL
  backends, so only the DDL rendering needs a variant.
* ``engine`` — a process-wide in-memory SQLite engine using a shared
  ``StaticPool`` so every connection sees the same database.
* ``db_session`` — a function-scoped session wrapped in a transaction that is
  rolled back after each test, keeping tests isolated without recreating the
  schema every time.
* ``client`` — a FastAPI ``TestClient`` with ``get_db`` overridden to use the
  test session, so routes exercise the same ephemeral database.
* ``seeded_user`` — a convenience fixture creating one organization and one
  user with a known password for login/route tests.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Hermetic test environment (MUST run before any app module imports settings)
# ---------------------------------------------------------------------------
# The test suite must be fully deterministic and offline regardless of the
# developer's local ``backend/.env`` (which may set ``AI_PROVIDER=llm`` with a
# real key, ``EMBEDDING_PROVIDER=openai``, etc.). Environment variables take
# precedence over ``.env`` values in pydantic-settings, so forcing the mock
# providers here guarantees no test ever hits a live LLM / embedding API and
# that AI-dependent assertions stay reproducible. This runs at import time,
# before ``app.main``/``app.config`` are imported below.
os.environ["AI_PROVIDER"] = "mock"
os.environ["EMBEDDING_PROVIDER"] = "mock"

from collections.abc import Generator
from typing import Any

import pytest

from app.config import get_settings

# Drop any Settings instance cached from a prior read so the forced mock
# provider env vars above take effect for the whole suite.
get_settings.cache_clear()
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.engine import Engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Importing the models registers them on ``Base.metadata`` so ``create_all``
# builds every table. The CWI models must be imported too because the schema
# references them via foreign keys.
from app.core import models as core_models  # noqa: F401
from app.database import Base, get_db
from app.main import create_app
from app.modules.cwi import models as cwi_models  # noqa: F401
from app.security import hash_password

# A known password used by the seeded test user.
TEST_PASSWORD = "test-password-123"

# Password hashing with bcrypt is deliberately slow (~200ms/call). The seeded
# user's password value is only exercised by login/security tests via the
# plaintext ``TEST_PASSWORD``; the stored hash itself is never re-verified in
# the DB-backed tests. Computing it once at import time (rather than per-test or
# per-Hypothesis-example) keeps the suite fast without weakening any assertion.
TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD)


# ---------------------------------------------------------------------------
# SQLite compatibility for PostgreSQL column types
# ---------------------------------------------------------------------------


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element: Any, compiler: Any, **kw: Any) -> str:
    """Render PostgreSQL ``JSONB`` as SQLite ``JSON`` for DDL.

    SQLAlchemy's JSON value handling (serialize/deserialize) already works on
    SQLite; only the emitted column type needs a variant.
    """

    return "JSON"


@compiles(UUID, "sqlite")
def _compile_uuid_sqlite(element: Any, compiler: Any, **kw: Any) -> str:
    """Render PostgreSQL ``UUID`` as SQLite ``CHAR(32)`` for DDL.

    The ``UUID(as_uuid=True)`` type stores values as 32-char hex strings on
    backends without a native UUID type, so ``CHAR(32)`` matches its storage.
    """

    return "CHAR(32)"


def _strip_postgres_server_defaults() -> None:
    """Drop PostgreSQL-only column server defaults so SQLite DDL is valid.

    Several JSONB columns declare ``server_default=text("'{}'::jsonb")`` (or the
    ``'[]'::jsonb`` list variant). The ``::jsonb`` cast is PostgreSQL syntax that
    SQLite cannot parse, and dropping it is safe for tests because the ORM-level
    ``default=dict`` / ``default=list`` still populates these columns on insert.
    Only defaults containing a ``::`` cast are removed; other defaults (booleans,
    timestamps) are left untouched.
    """

    for table in Base.metadata.tables.values():
        for column in table.columns:
            default = column.server_default
            if default is None:
                continue
            arg = getattr(default, "arg", None)
            arg_text = getattr(arg, "text", "") or str(arg)
            if "::" in arg_text:
                column.server_default = None


# ---------------------------------------------------------------------------
# Engine / schema
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def engine() -> Generator[Engine, None, None]:
    """Create an in-memory SQLite engine shared across the session."""

    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    # Enforce foreign keys on SQLite (off by default) so scoping/FK behavior in
    # tests matches production intent.
    @event.listens_for(eng, "connect")
    def _fk_pragma(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    _strip_postgres_server_defaults()
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        Base.metadata.drop_all(eng)
        eng.dispose()


@pytest.fixture()
def db_session(engine: Engine) -> Generator[Session, None, None]:
    """Yield a transaction-scoped session that is rolled back after each test."""

    connection = engine.connect()
    transaction = connection.begin()
    TestingSessionLocal = sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
        future=True,
    )
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """A ``TestClient`` whose ``get_db`` dependency uses the test session."""

    app = create_app()

    def _override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def seeded_user(db_session: Session) -> dict[str, Any]:
    """Create one organization and one user with a known password.

    Returns a dict with the created ``organization``, ``user``, the plaintext
    ``password`` for login, and the user's ``email``.
    """

    org = core_models.Organization(name="Test Organization")
    db_session.add(org)
    db_session.flush()

    user = core_models.User(
        organization_id=org.id,
        email="tester@example.com",
        full_name="Test User",
        password_hash=TEST_PASSWORD_HASH,
        role="ADMIN",
    )
    db_session.add(user)
    db_session.flush()

    return {
        "organization": org,
        "user": user,
        "email": user.email,
        "password": TEST_PASSWORD,
    }
