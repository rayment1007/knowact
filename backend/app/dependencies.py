"""Request dependencies: authentication, organization scoping, and AI provider.

This module wires the cross-cutting concerns that routes and services depend on:

Authentication (JWT in an **HTTP-only cookie**)
-----------------------------------------------
Authentication uses a JWT delivered as an HTTP-only cookie (see
:mod:`app.security`) — there is **no** ``Authorization`` header scheme.
:func:`get_current_user` reads the token from the request cookie
(``Settings.auth_cookie_name``), decodes it with
:func:`app.security.decode_access_token`, and resolves the
:class:`~app.core.models.User` (and, on request, their
:class:`~app.core.models.Organization`) from the database. Any missing,
malformed, expired, or otherwise invalid token — or a token whose ``sub`` does
not resolve to a known user — yields a ``401 Unauthorized`` response
(Requirements 1.5, 2.2). :func:`require_auth` is a thin, semantically-named
wrapper for routes that only need to assert "an authenticated user exists".

Organization scoping (multi-tenancy)
------------------------------------
Every domain row carries an ``organization_id`` (see
:class:`app.database.OrganizationScopedMixin`). Tenant isolation is enforced at
the **service layer**: services filter every query by the caller's
``organization_id`` using :func:`scope_query` (or read it via
:func:`get_current_organization_id`). The convention for cross-tenant access is
important and deliberate:

    **Cross-organization row access surfaces as ``404 Not Found``, never
    ``403``.** A row that belongs to another organization must be
    indistinguishable from a row that does not exist, so the system never
    reveals the existence of another tenant's data (Requirement 2.3). Services
    therefore scope their lookup by ``organization_id`` and raise
    :func:`not_found` (a 404 ``HTTPException``) when the scoped query returns
    nothing. Writes are likewise permitted only when the target row's
    ``organization_id`` equals the user's (Requirement 2.4); a scoped lookup
    that finds nothing is treated as a 404 rather than a permission error.

AI provider selection (stubs — completed in task 7.3)
-----------------------------------------------------
:func:`get_ai_provider` and :func:`call_with_fallback` establish the signatures
and the mode-dependent failure policy now, but the concrete provider wiring is
implemented in task 7.3 (the ``ai_provider`` module arrives in task 7.x). The
imports of the provider classes are performed **lazily inside the functions** so
this module stays import-safe today: importing ``app.dependencies`` does not
require the not-yet-existent ``app.core.services.ai_provider`` module. The stubs
raise :class:`NotImplementedError` only when *called*, never at import time.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, TypeVar
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Query, Session

from app.config import Settings, get_settings
from app.core.models import Organization, User
from app.database import get_db
from app.security import TokenError, decode_access_token

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids runtime import
    # The AI provider module is introduced in task 7.x. Import it only for type
    # checkers so runtime import of this module never depends on it.
    from app.core.services.ai_provider import AIProvider

logger = logging.getLogger(__name__)

# Reusable 401 for any authentication failure. The detail is intentionally
# generic so we never disclose *why* auth failed (missing vs expired vs unknown
# user), which avoids leaking information to unauthenticated callers.
_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated.",
)


# ---------------------------------------------------------------------------
# Authentication dependencies (HTTP-only cookie)
# ---------------------------------------------------------------------------


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    """Resolve the authenticated :class:`User` from the auth cookie.

    Reads the JWT from the HTTP-only cookie (``Settings.auth_cookie_name``),
    decodes and validates it, and loads the corresponding user (whose
    ``organization_id`` identifies their tenant) from the database.

    Args:
        request: The incoming request (source of the auth cookie).
        db: The request-scoped database session.
        settings: Application settings (for JWT verification / cookie name).

    Returns:
        The authenticated :class:`User`.

    Raises:
        HTTPException: ``401 Unauthorized`` if the cookie is missing, malformed,
            expired, or its ``sub`` does not resolve to a known user
            (Requirements 1.5, 2.2).
    """

    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        raise _UNAUTHENTICATED

    try:
        claims = decode_access_token(token, settings=settings)
    except TokenError as exc:
        # Expired / invalid signature / malformed / missing subject.
        raise _UNAUTHENTICATED from exc

    subject = claims.get("sub")
    try:
        user_id = UUID(str(subject))
    except (ValueError, TypeError) as exc:
        # ``sub`` is present (decode_access_token guarantees it) but not a UUID.
        raise _UNAUTHENTICATED from exc

    user = db.get(User, user_id)
    if user is None:
        # Token is well-formed but the referenced user no longer exists.
        raise _UNAUTHENTICATED

    return user


def require_auth(user: User = Depends(get_current_user)) -> User:
    """Assert that the request is authenticated and return the current user.

    A thin, intention-revealing wrapper around :func:`get_current_user` for
    routes that simply need to guard access without additional context. Behaves
    identically to :func:`get_current_user` (returns the user, or raises
    ``401``); use whichever name reads better at the call site.
    """

    return user


def get_current_organization(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Organization:
    """Resolve the authenticated user's :class:`Organization`.

    Loads the tenant root for the current user. This is available for routes or
    services that need the organization object itself (e.g. to display the org
    name); most scoping only needs :func:`get_current_organization_id`.

    Raises:
        HTTPException: ``401 Unauthorized`` if the user's organization cannot be
            resolved (a data-integrity anomaly, treated as an auth failure so no
            tenant information leaks).
    """

    organization = db.get(Organization, user.organization_id)
    if organization is None:  # pragma: no cover - integrity safeguard
        raise _UNAUTHENTICATED
    return organization


def get_current_organization_id(user: User = Depends(get_current_user)) -> UUID:
    """Return the current user's ``organization_id`` for query scoping.

    This is the lightweight scoping primitive services and routes use to filter
    every query by tenant (Requirement 2.2). Pair it with :func:`scope_query`.
    """

    return user.organization_id


# ---------------------------------------------------------------------------
# Organization-scoping helpers (service layer)
# ---------------------------------------------------------------------------


def scope_query(query: Query, model: Any, organization_id: UUID) -> Query:
    """Constrain a SQLAlchemy ``Query`` to a single organization.

    Central helper so tenant filtering is expressed identically everywhere::

        q = scope_query(db.query(SourceItem), SourceItem, org_id)

    Args:
        query: The base ORM query to constrain.
        model: The org-scoped model class (must expose ``organization_id``).
        organization_id: The tenant to restrict the query to.

    Returns:
        The query filtered by ``model.organization_id == organization_id``.
    """

    return query.filter(model.organization_id == organization_id)


def scope_select(statement: Any, model: Any, organization_id: UUID) -> Any:
    """``select()``-style counterpart of :func:`scope_query` (SQLAlchemy 2.0).

    For services written against the 2.0 ``select()`` API::

        stmt = scope_select(select(SourceItem), SourceItem, org_id)
    """

    return statement.where(model.organization_id == organization_id)


def not_found(detail: str = "Not found.") -> HTTPException:
    """Build the canonical ``404`` used for cross-organization access.

    Cross-tenant row access MUST be indistinguishable from a genuinely missing
    row (Requirement 2.3), so services raise this 404 — never a 403 — when an
    organization-scoped lookup returns nothing::

        item = scope_query(db.query(SourceItem), SourceItem, org_id).get(item_id)
        if item is None:
            raise not_found("Source item not found.")
    """

    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


# ---------------------------------------------------------------------------
# AI provider selection (STUBS — completed in task 7.3)
# ---------------------------------------------------------------------------

T = TypeVar("T")


def get_ai_provider(settings: Settings | None = None) -> "AIProvider":
    """Resolve the primary :class:`AIProvider` from configuration.

    Selection policy (Requirements 11.2, 11.3):

    * Default to the deterministic ``MockAIProvider`` — this keeps the whole
      system runnable locally with no external API key (Requirement 11.2).
    * Use ``LLMProvider`` **only** when ``settings.ai_provider == "llm"`` *and*
      ``settings.llm_api_key`` is present; otherwise fall back to the mock
      (Requirement 11.3).

    The concrete provider classes live in ``app.core.services.ai_provider`` and
    are imported lazily *inside* this function so ``app.dependencies`` stays
    cheap to import and free of a hard module-load dependency on the provider
    layer.
    """

    settings = settings or get_settings()

    # Lazy import keeps this module lightweight to import.
    from app.core.services.ai_provider import LLMProvider, MockAIProvider

    if settings.ai_provider == "llm" and settings.llm_api_key:
        return LLMProvider(settings)
    return MockAIProvider()


def call_with_fallback(
    primary: "AIProvider",
    settings: Settings,
    op: Callable[["AIProvider"], T],
) -> T:
    """Invoke an :class:`AIProvider` operation applying the failure policy.

    ``op(provider)`` performs the actual call (classify / extract / brief / …).
    When the primary provider fails, behavior is **mode-dependent**
    (Requirement 11.6):

    * ``mode == "DEVELOPMENT"``: fall back to a deterministic ``MockAIProvider``
      and emit a clear ``logger.warning`` so the developer knows output is
      degraded. This keeps local development unblocked when the LLM misbehaves.
    * ``mode == "PRODUCTION"``: **no silent fallback.** The failure surfaces as a
      retriable ``503`` (``HTTPException``); the request is never quietly
      downgraded to the mock provider. On such unrecoverable failure the calling
      service must leave the source item in its prior state.

    Only an :class:`~app.core.services.ai_provider.AIProviderError` triggers the
    fallback policy, and only when ``primary`` is an ``LLMProvider`` — a failure
    from the deterministic mock (or any non-provider error) is not something a
    mock fallback could fix, so it propagates unchanged. This function performs
    **no persistence**; it never mutates the source item, so an unrecoverable
    failure leaves the caller free to abort with the item in its prior state
    (Requirement 11.6).
    """

    # Lazy import mirrors get_ai_provider and keeps this module import-safe.
    from app.core.services.ai_provider import (
        AIProviderError,
        LLMProvider,
        MockAIProvider,
    )

    try:
        return op(primary)
    except AIProviderError as exc:
        # A mock failure can't be repaired by falling back to the mock, and a
        # non-LLM primary has no meaningful degraded alternative.
        if not isinstance(primary, LLMProvider):
            raise

        if settings.mode == "DEVELOPMENT":
            logger.warning(
                "LLMProvider failed (%s); falling back to MockAIProvider "
                "because mode=DEVELOPMENT. Output is degraded/deterministic.",
                exc,
            )
            return op(MockAIProvider())

        # PRODUCTION: no silent fallback — surface a retriable 503.
        logger.error("LLMProvider failed in PRODUCTION mode: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI provider temporarily unavailable; please retry.",
        ) from exc


__all__ = [
    "get_current_user",
    "require_auth",
    "get_current_organization",
    "get_current_organization_id",
    "scope_query",
    "scope_select",
    "not_found",
    "get_ai_provider",
    "call_with_fallback",
]
