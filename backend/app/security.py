"""Security utilities: password hashing, JWT, and HTTP-only cookie helpers.

This module centralizes the security primitives the application relies on:

1. **Password hashing** — passwords are stored only as strong adaptive bcrypt
   hashes (via ``passlib``). Plaintext passwords are never persisted, and no
   helper here returns or logs a stored hash.
2. **JWT tokens** — signed with ``Settings.jwt_secret`` using
   ``Settings.jwt_algorithm`` (HS256) and expiring after
   ``Settings.access_token_expire_minutes``. The payload is deliberately
   minimal: the user id in the standard ``sub`` claim (enough to resolve the
   user on the next request) plus standard ``exp``/``iat`` timestamps. Callers
   may pass a small set of extra claims (e.g. ``organization_id``) but should
   keep the payload lean.
3. **HTTP-only cookie** — the signed JWT is delivered to the browser as an
   HTTP-only cookie (:func:`set_auth_cookie`) so client-side JavaScript can
   never read it, mitigating token theft via XSS. The browser returns it
   automatically on same-site requests; :func:`clear_auth_cookie` removes it on
   logout. Cookie name / ``Secure`` / ``SameSite`` come from ``Settings``.

Requirements: 1.6.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Response
from passlib.context import CryptContext

from app.config import Settings, get_settings

# Single shared password-hashing context. bcrypt is a strong adaptive hash and
# transparently salts each password. ``deprecated="auto"`` lets us migrate
# schemes in the future without breaking existing hashes.
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class TokenError(Exception):
    """Raised when a JWT cannot be decoded, is expired, or fails validation."""


# --- Password hashing --------------------------------------------------------


def hash_password(password: str) -> str:
    """Return a strong adaptive (bcrypt) hash for ``password``.

    The returned value is a salted hash suitable for persistence; the original
    plaintext password is never stored.
    """

    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Return ``True`` if ``password`` matches the stored ``password_hash``.

    Verification is constant-time within ``passlib`` and never raises for a
    simple mismatch; malformed hashes are treated as a non-match rather than an
    error, so this helper never leaks details about a stored hash.
    """

    try:
        return _pwd_context.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


# --- JWT bearer tokens -------------------------------------------------------


def create_access_token(
    subject: str,
    *,
    claims: dict[str, Any] | None = None,
    settings: Settings | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a signed JWT access token for ``subject`` (the user id).

    Args:
        subject: The user identifier stored in the standard ``sub`` claim.
        claims: Optional additional claims to embed (e.g. ``organization_id``).
            Keep this minimal — just enough to resolve the user. Reserved
            claims (``sub``, ``exp``, ``iat``) are always controlled by this
            helper and cannot be overridden.
        settings: Settings to use; defaults to the process settings.
        expires_delta: Optional explicit lifetime; defaults to
            ``settings.access_token_expire_minutes``.

    Returns:
        The encoded JWT as a string.
    """

    settings = settings or get_settings()

    now = datetime.now(timezone.utc)
    expire = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.access_token_expire_minutes)
    )

    payload: dict[str, Any] = {}
    if claims:
        # Copy caller-provided claims first so reserved claims below always win.
        payload.update(claims)
    payload.update(
        {
            "sub": str(subject),
            "iat": now,
            "exp": expire,
        }
    )

    return jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(
    token: str,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Decode and validate a JWT access token, returning its claims.

    Signature and expiry are verified. The presence of a ``sub`` claim is
    required so callers can always resolve the user.

    Args:
        token: The encoded JWT bearer token.
        settings: Settings to use; defaults to the process settings.

    Returns:
        The decoded claims dictionary.

    Raises:
        TokenError: If the token is expired, has an invalid signature, is
            otherwise malformed, or is missing the ``sub`` claim.
    """

    settings = settings or get_settings()

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Token is invalid.") from exc

    if not claims.get("sub"):
        raise TokenError("Token is missing the subject ('sub') claim.")

    return claims


# --- HTTP-only auth cookie ---------------------------------------------------


def set_auth_cookie(
    response: Response,
    token: str,
    *,
    settings: Settings | None = None,
) -> None:
    """Attach the signed JWT to ``response`` as an HTTP-only cookie.

    The cookie is marked ``HttpOnly`` (unreadable by JavaScript), scoped to the
    site root (``path="/"``), and configured with the ``Secure`` / ``SameSite``
    attributes from ``Settings``. Its ``max_age`` matches the token lifetime so
    the cookie and the JWT expire together.

    Args:
        response: The FastAPI/Starlette response to set the cookie on.
        token: The signed JWT access token.
        settings: Settings to use; defaults to the process settings.
    """

    settings = settings or get_settings()

    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )


def clear_auth_cookie(
    response: Response,
    *,
    settings: Settings | None = None,
) -> None:
    """Remove the auth cookie from the client (used on logout).

    Deletes the cookie by name using the same ``path`` / ``Secure`` /
    ``SameSite`` attributes it was set with, so browsers reliably clear it.
    """

    settings = settings or get_settings()

    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/",
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
    )
