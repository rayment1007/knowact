"""Idempotent seed data for KnowAct.

This module populates a realistic first-run dataset so the platform can
demonstrate its end-to-end workflows immediately after migrations are applied.

What it seeds
-------------
* One organization: **Apex Advisory Group**.
* Two demo users whose password comes from the ``SEED_USER_PASSWORD``
  environment variable (see below): ``alex@knowact.app`` (Alex Tan, ADMIN) and
  ``sarah@knowact.app`` (Sarah Lim, MEMBER).
* Five business entities: three internal (*Project Orion*, *Client Growth
  Initiative*, *Operations Automation Review*) and two of type ``CLIENT``
  (*Alice Tan*, *Michael Wong*) so the Copilot's client-context tool and the
  per-entity knowledge/action filters have data to work with.
* Seven varied source items left in status ``NEW`` (unclassified) so the demo
  shows the Collect -> Classify triage workflow. Their content is chosen so the
  deterministic ``MockAIProvider`` classifier produces meaningfully different
  results (work vs. noise, decision vs. risk, sensitive vs. public).

Credentials
-----------
There is **no default password**. ``SEED_USER_PASSWORD`` must be set (in the
local ``.env`` or the deployment's environment) before running the seeder;
otherwise :func:`seed` raises :class:`RuntimeError`. This keeps a shared,
well-known password out of the repository and out of deployed environments.

Idempotency
-----------
The script is **idempotent**: it looks up each row by a natural key
(organization name, user email, entity name, or source item title) and only
creates rows that are missing. Re-running ``python -m app.seed`` therefore never
duplicates data. All work happens inside a single transaction that is committed
once at the end (or rolled back on error).

Importing this module does **not** open a database connection; the SQLAlchemy
engine connects lazily. A connection is established only when :func:`run` (or
:func:`main`) is invoked.
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.models import (
    BusinessEntity,
    BusinessEntityType,
    Organization,
    SourceItem,
    SourceStatus,
    SourceType,
    User,
)
from app.database import Base, SessionLocal
from app.security import hash_password

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=Base)


def _demo_password() -> str:
    """Return the configured seed password, or fail loudly.

    Raises:
        RuntimeError: If ``SEED_USER_PASSWORD`` is unset or blank. Seeding must
            never fall back to a hard-coded password.
    """

    password = get_settings().seed_user_password
    if not password or not password.strip():
        raise RuntimeError(
            "SEED_USER_PASSWORD is not set. Choose a password and set it in the "
            "environment (or backend/.env) before running `python -m app.seed`."
        )
    return password


def _get_or_create(
    session: Session,
    model: type[T],
    *,
    defaults: dict[str, Any] | None = None,
    **filters: Any,
) -> tuple[T, bool]:
    """Return an existing row matching ``filters`` or create one.

    Args:
        session: The active session.
        model: The ORM model class.
        defaults: Extra column values applied only when creating a new row.
        **filters: The natural-key columns used to look up an existing row.

    Returns:
        A ``(instance, created)`` tuple where ``created`` is ``True`` only when
        a new row was inserted. New rows are flushed so their primary keys are
        available for subsequent foreign-key references.
    """

    instance = session.query(model).filter_by(**filters).one_or_none()
    if instance is not None:
        return instance, False

    params: dict[str, Any] = dict(filters)
    if defaults:
        params.update(defaults)
    instance = model(**params)
    session.add(instance)
    session.flush()
    return instance, True


# ---------------------------------------------------------------------------
# Individual seed sections
# ---------------------------------------------------------------------------


def _seed_organization(session: Session) -> Organization:
    """Seed the demo organization."""

    org, created = _get_or_create(
        session, Organization, name="Apex Advisory Group"
    )
    logger.info("Organization %r %s", org.name, "created" if created else "exists")
    return org


def _seed_users(session: Session, org: Organization) -> dict[str, User]:
    """Seed the two demo users.

    Both accounts authenticate with the value of ``SEED_USER_PASSWORD``. The
    password is hashed via :func:`app.security.hash_password`; plaintext is
    never persisted.
    """

    password_hash = hash_password(_demo_password())

    specs = [
        {
            "email": "alex@knowact.app",
            "full_name": "Alex Tan",
            "role": "ADMIN",  # Firm administrator
        },
        {
            "email": "sarah@knowact.app",
            "full_name": "Sarah Lim",
            "role": "MEMBER",  # Regular team member
        },
    ]

    users: dict[str, User] = {}
    for spec in specs:
        # Look up by the unique natural key (email) only; hashing on every run
        # would waste work and produce a new salt, so we only hash on create.
        user, created = _get_or_create(
            session,
            User,
            email=spec["email"],
            defaults={
                "organization_id": org.id,
                "full_name": spec["full_name"],
                "password_hash": password_hash,
                "role": spec["role"],
            },
        )
        users[spec["email"]] = user
        logger.info(
            "User %r %s", user.email, "created" if created else "exists"
        )
    return users


def _seed_business_entities(
    session: Session, org: Organization
) -> dict[str, BusinessEntity]:
    """Seed the business entities knowledge and actions are organized around.

    Three internal entities (two projects and one process) plus two entities of
    type ``CLIENT``. The ``CLIENT`` rows give the Copilot's client-context tool
    and the ``business_entity_id`` filters real data to read.
    """

    specs = [
        {
            "name": "Project Orion",
            "entity_type": BusinessEntityType.PROJECT,
            "description": "Flagship client-onboarding platform initiative.",
            "attributes": {"status": "active", "budget_approved": True},
        },
        {
            "name": "Client Growth Initiative",
            "entity_type": BusinessEntityType.PROJECT,
            "description": "Cross-team effort to grow the client base.",
            "attributes": {"status": "active", "target_quarter": "Q2"},
        },
        {
            "name": "Operations Automation Review",
            "entity_type": BusinessEntityType.PROCESS,
            "description": "Review of back-office processes for automation.",
            "attributes": {"status": "in_review"},
        },
        {
            "name": "Alice Tan",
            "entity_type": BusinessEntityType.CLIENT,
            "description": (
                "Pre-retirement business owner. Wants a conservative, "
                "capital-preservation plan and an estate-planning review."
            ),
            "attributes": {
                "priority": "high",
                "tags": ["retirement", "conservative", "estate planning"],
            },
        },
        {
            "name": "Michael Wong",
            "entity_type": BusinessEntityType.CLIENT,
            "description": (
                "Mid-career professional with a young family. Protection "
                "policy is due for renewal; asked about family coverage."
            ),
            "attributes": {
                "priority": "high",
                "tags": ["insurance", "family protection", "renewal"],
            },
        },
    ]

    entities: dict[str, BusinessEntity] = {}
    for spec in specs:
        entity, created = _get_or_create(
            session,
            BusinessEntity,
            organization_id=org.id,
            name=spec["name"],
            entity_type=spec["entity_type"],
            defaults={
                "description": spec["description"],
                "attributes": spec["attributes"],
            },
        )
        entities[spec["name"]] = entity
        logger.info(
            "BusinessEntity %r %s",
            entity.name,
            "created" if created else "exists",
        )
    return entities


def _seed_source_items(
    session: Session, org: Organization, users: dict[str, User]
) -> list[SourceItem]:
    """Seed seven varied, unclassified source items.

    All items are left in status ``NEW`` so the demo can exercise the
    classify/confirm workflow. Content is written so the deterministic
    ``MockAIProvider`` triages them differently: a work follow-up, an irrelevant
    subscription notice, a highly-sensitive system notification, a project
    decision, a delayed-launch risk, an internal risk update, and a partner
    introduction.
    """

    alex = users["alex@knowact.app"]
    sarah = users["sarah@knowact.app"]

    specs = [
        {
            "title": "Follow up with client on portfolio review",
            "source_type": SourceType.EMAIL,
            "created_by": sarah.id,
            "content": (
                "Hi Sarah, please follow up with the client regarding their "
                "portfolio review. They asked us to send the updated summary "
                "and schedule a call next week to discuss their account."
            ),
        },
        {
            "title": "Your Netflix subscription has renewed",
            "source_type": SourceType.EMAIL,
            "created_by": sarah.id,
            "content": (
                "This is a newsletter from Netflix. Your monthly subscription "
                "has renewed automatically. To manage your plan or unsubscribe "
                "from these emails, click here. FYI only."
            ),
        },
        {
            "title": "Password reset request for your account",
            "source_type": SourceType.EMAIL,
            "created_by": alex.id,
            "content": (
                "This is an automated message from no-reply@security. A password "
                "reset was requested for your account. This message is "
                "confidential and contains your account number. Do not reply to "
                "this automated notification."
            ),
        },
        {
            "title": "Budget approved for Project Orion",
            "source_type": SourceType.EMAIL,
            "created_by": alex.id,
            "content": (
                "Team, the leadership committee has approved the budget for "
                "Project Orion. We will proceed with the next milestone. This "
                "decision is final and we have agreed to the revised timeline."
            ),
        },
        {
            "title": "Meeting minutes: launch delay discussion",
            "source_type": SourceType.MEETING_NOTE,
            "created_by": alex.id,
            "content": (
                "Meeting minutes from today's call. The team flagged a risk: "
                "the product launch is now overdue and blocked by an integration "
                "issue. We agreed to escalate and revisit the decision on scope "
                "at the next meeting."
            ),
        },
        {
            "title": "Internal risk update: vendor dependency",
            "source_type": SourceType.DOCUMENT,
            "created_by": alex.id,
            "content": (
                "Internal risk update. There is an ongoing issue with a key "
                "vendor dependency that poses a delivery risk to the Operations "
                "Automation Review. Mitigation options are being evaluated."
            ),
        },
        {
            "title": "Introduction to estate planning specialist",
            "source_type": SourceType.EMAIL,
            "created_by": sarah.id,
            "content": (
                "Hi Sarah, I'd like to make an introduction to a specialist "
                "who handles estate planning. They are a great fit for clients "
                "needing that expertise and are open to working together."
            ),
        },
    ]

    items: list[SourceItem] = []
    for spec in specs:
        item, created = _get_or_create(
            session,
            SourceItem,
            organization_id=org.id,
            title=spec["title"],
            defaults={
                "created_by": spec["created_by"],
                "source_type": spec["source_type"],
                "content": spec["content"],
                "status": SourceStatus.NEW,
            },
        )
        items.append(item)
        logger.info(
            "SourceItem %r %s", item.title, "created" if created else "exists"
        )
    return items


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def seed(session: Session) -> None:
    """Populate all seed data using ``session`` within a single transaction.

    The caller owns the transaction boundary: this function adds/flushes rows
    but does not commit. Use :func:`run` for a self-contained entry point that
    manages its own session and commit.

    Raises:
        RuntimeError: If ``SEED_USER_PASSWORD`` is not configured.
    """

    # Validate configuration before touching the database so a misconfigured
    # run fails immediately instead of half-way through.
    _demo_password()

    org = _seed_organization(session)
    users = _seed_users(session, org)
    _seed_business_entities(session, org)
    _seed_source_items(session, org, users)


def run() -> None:
    """Seed the database using a fresh session, committing on success.

    Rolls back and re-raises on any error so a failed run never leaves partial
    data behind. This opens a database connection, so it must only be called
    when a reachable database with the schema applied is available.
    """

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    session = SessionLocal()
    try:
        seed(session)
        session.commit()
        logger.info("Seed complete.")
    except Exception:
        session.rollback()
        logger.exception("Seeding failed; rolled back.")
        raise
    finally:
        session.close()


def main() -> None:
    """Console entry point (alias for :func:`run`)."""

    run()


if __name__ == "__main__":
    main()
