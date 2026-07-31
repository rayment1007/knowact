"""Database layer: engine, session factory, declarative base, and helpers.

This module owns the SQLAlchemy plumbing that the rest of the backend builds
on. It intentionally contains **no domain models** — those are defined in
``app/core/models.py`` and ``app/modules/cwi/models.py``.

Key exports:

* ``engine`` — the process-wide SQLAlchemy engine, built from
  ``Settings.database_url``.
* ``SessionLocal`` — a configured ``sessionmaker`` producing ``Session``
  instances bound to ``engine``.
* ``Base`` — the declarative base every ORM model inherits from; its
  ``metadata`` is what Alembic targets when generating migrations.
* ``get_db`` — a FastAPI dependency that yields a request-scoped session and
  always closes it.

Multi-tenancy convention
------------------------
KnowAct is multi-tenant: **every domain row carries an
``organization_id``** so tenants never see each other's data (see design
"Multi-Tenancy & Scoping" and Requirement 2.1). To make that convention
explicit and consistent, domain models should inherit
``OrganizationScopedMixin`` (defined below), which contributes the
``organization_id`` foreign-key column. Isolation itself is enforced at the
service layer by filtering every query on ``organization_id``; the mixin only
guarantees the column is present and uniformly defined.
"""

from __future__ import annotations

from collections.abc import Generator
from uuid import UUID

from sqlalchemy import ForeignKey, create_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)

from app.config import Settings, get_settings

# ---------------------------------------------------------------------------
# Engine and session factory
# ---------------------------------------------------------------------------

_settings: Settings = get_settings()

# ``pool_pre_ping`` transparently recycles connections that the database has
# dropped, which avoids stale-connection errors in long-running processes.
engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    future=True,
)

# ``expire_on_commit=False`` keeps ORM instances usable after the request's
# transaction commits (e.g. when serializing a response), which is the common
# expectation in a FastAPI request/response cycle.
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
    future=True,
)


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Declarative base for all ORM models.

    ``Base.metadata`` is the single source of truth for the database schema and
    is what Alembic autogenerate targets (wired up in task 2.3).
    """


# ---------------------------------------------------------------------------
# Multi-tenancy convention helper
# ---------------------------------------------------------------------------


class OrganizationScopedMixin:
    """Mixin contributing the tenant-scoping ``organization_id`` column.

    Every domain model in the system belongs to exactly one organization and
    MUST carry an ``organization_id`` (Requirement 2.1). Inheriting this mixin
    documents and enforces that convention with a single, consistent column
    definition::

        class SourceItem(OrganizationScopedMixin, Base):
            __tablename__ = "source_items"
            ...

    The column is indexed because virtually every service-layer query filters
    by ``organization_id`` for tenant isolation. This mixin deliberately does
    not define any concrete model — domain models are introduced in task 2.1.
    """

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )


# ---------------------------------------------------------------------------
# Session dependency
# ---------------------------------------------------------------------------


def get_db() -> Generator[Session, None, None]:
    """Yield a request-scoped database session, committing on success.

    Intended for use as a FastAPI dependency::

        @router.get("/things")
        def list_things(db: Session = Depends(get_db)):
            ...

    The request boundary is the single commit point for the system. Core Engine
    services deliberately only ``add``/``flush`` within the request transaction
    and never commit themselves; this dependency commits once when the handler
    returns successfully, so a state change and its audit row are persisted
    atomically. If the handler raises — including an ``HTTPException`` for a 404
    or a 409/safety refusal — the transaction is rolled back, leaving no partial
    writes. The session is always closed in the ``finally`` block.
    """

    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
