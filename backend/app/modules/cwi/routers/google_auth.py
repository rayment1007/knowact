"""Google Sign-In via OpenID Connect (Requirement 23).

Two routes implement identity-only Google sign-in, deliberately **separate**
from Gmail/Calendar authorization:

* ``GET /api/auth/google/start`` — begin OIDC sign-in requesting ONLY
  ``openid email profile`` (Requirements 23.1, 23.3). Returns the Google
  authorization URL plus a signed ``state``.
* ``GET /api/auth/google/callback`` — verify the identity token, upsert the
  :class:`~app.core.models.User`, and establish the EXISTING JWT / HTTP-only
  cookie session (Requirement 23.2). Verification failure yields ``401`` and
  **no** session is established (Requirement 23.6).

Signing in **never** creates an ``IntegrationConnection`` and **never** stores a
Gmail or Calendar token (Requirement 23.4). The existing email-and-password
login (:mod:`app.core.routers.auth`) is untouched (Requirement 23.5).
"""

from __future__ import annotations

import secrets
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.models import Organization, User
from app.database import get_db
from app.modules.cwi.dependencies import google_oauth_client
from app.modules.cwi.schemas import AuthorizationRedirectResponse
from app.modules.cwi.services.google_oauth import (
    GoogleIdentity,
    GoogleOAuthClient,
    GoogleOAuthError,
)
from app.security import (
    clear_auth_cookie,
    create_access_token,
    hash_password,
    set_auth_cookie,
)

router = APIRouter(prefix="/auth/google", tags=["auth-google"])
logger = logging.getLogger(__name__)

# ``purpose`` claim distinguishing a sign-in state from an integration-authz
# state so neither can be replayed as the other.
_SIGNIN_STATE_PURPOSE = "cwi_signin"
_SIGNIN_STATE_COOKIE = "knowact_google_signin_state"
_SIGNIN_STATE_TTL_SECONDS = 10 * 60


def _signin_redirect_uri(settings: Settings) -> str:
    base = settings.google_oauth_redirect_base.rstrip("/")
    return f"{base}/api/auth/google/callback"


def _frontend_url(settings: Settings, path: str) -> str:
    """Build an absolute SPA URL under the configured frontend base."""

    base = settings.frontend_base_url.rstrip("/")
    return f"{base}{path}"


def _encode_signin_state(settings: Settings, nonce: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "purpose": _SIGNIN_STATE_PURPOSE,
        "nonce": nonce,
        "iat": now,
        "exp": now + timedelta(seconds=_SIGNIN_STATE_TTL_SECONDS),
    }
    return jwt.encode(
        payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )


def _decode_signin_state(settings: Settings, state: str) -> str | None:
    try:
        claims = jwt.decode(
            state, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.InvalidTokenError:
        return None
    nonce = claims.get("nonce")
    if claims.get("purpose") != _SIGNIN_STATE_PURPOSE or not isinstance(
        nonce, str
    ):
        return None
    return nonce


def _clear_signin_state_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=_SIGNIN_STATE_COOKIE,
        path="/api/auth/google",
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
    )


def _signin_failure(settings: Settings) -> RedirectResponse:
    response = RedirectResponse(
        url=_frontend_url(settings, "/login?error=google_signin_failed"),
        status_code=status.HTTP_302_FOUND,
    )
    clear_auth_cookie(response, settings=settings)
    _clear_signin_state_cookie(response, settings)
    return response


def _upsert_google_user(db: Session, identity: GoogleIdentity) -> User:
    """Find or create the platform :class:`User` for a verified Google identity.

    An existing user (matched by email) is signed in and has their display name
    refreshed. A brand-new identity provisions its own single-user organization
    with an unusable random password hash (Google users authenticate via Google,
    never a password). No Gmail/Calendar token is stored here (Requirement 23.4).
    """

    normalized_email = identity.email.strip().casefold()
    user = db.execute(
        select(User).where(User.email == normalized_email)
    ).scalar_one_or_none()

    if user is not None:
        if identity.full_name and user.full_name != identity.full_name:
            user.full_name = identity.full_name
            db.add(user)
            db.flush()
        return user

    # New identity: provision a workspace for them.
    org = Organization(name=identity.full_name or normalized_email)
    db.add(org)
    db.flush()

    user = User(
        organization_id=org.id,
        email=normalized_email,
        full_name=identity.full_name or normalized_email,
        # Random, unusable password: Google users never log in with a password.
        password_hash=hash_password(secrets.token_urlsafe(32)),
        role="ADMIN",
    )
    db.add(user)
    db.flush()
    return user


@router.get("/start", response_model=AuthorizationRedirectResponse)
def google_signin_start(
    response: Response,
    settings: Settings = Depends(get_settings),
    oauth: GoogleOAuthClient = Depends(google_oauth_client),
) -> AuthorizationRedirectResponse:
    """Begin Google OIDC sign-in (openid email profile only).

    Returns the Google authorization URL and a signed ``state``. No session is
    established and no service scope is requested (Requirements 23.1, 23.3).
    """

    nonce = uuid4().hex
    state = _encode_signin_state(settings, nonce)
    response.set_cookie(
        key=_SIGNIN_STATE_COOKIE,
        value=nonce,
        max_age=_SIGNIN_STATE_TTL_SECONDS,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/api/auth/google",
    )
    url = oauth.build_signin_url(
        redirect_uri=_signin_redirect_uri(settings), state=state
    )
    return AuthorizationRedirectResponse(authorization_url=url, state=state)


@router.get("/callback")
def google_signin_callback(
    request: Request,
    code: str,
    state: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    oauth: GoogleOAuthClient = Depends(google_oauth_client),
) -> RedirectResponse:
    """Complete Google sign-in and redirect the browser back to the SPA.

    Verifies the ``state`` and the Google identity token, upserts the user, and
    sets the HTTP-only JWT cookie on a ``302`` redirect to the SPA home
    (Requirement 23.2). On any verification failure the browser is redirected to
    ``/login?error=google_signin_failed`` and **no** session is established
    (Requirement 23.6). No ``IntegrationConnection`` is created and no
    Gmail/Calendar token is stored (Requirement 23.4).
    """

    state_nonce = _decode_signin_state(settings, state)
    cookie_nonce = request.cookies.get(_SIGNIN_STATE_COOKIE)
    if state_nonce is None:
        logger.warning("Google sign-in failed: invalid_or_expired_state")
        return _signin_failure(settings)
    if cookie_nonce is None:
        logger.warning("Google sign-in failed: missing_state_cookie")
        return _signin_failure(settings)
    if not secrets.compare_digest(state_nonce, cookie_nonce):
        logger.warning("Google sign-in failed: state_cookie_mismatch")
        return _signin_failure(settings)

    try:
        identity = oauth.exchange_signin_code(
            code=code, redirect_uri=_signin_redirect_uri(settings)
        )
    except GoogleOAuthError as exc:
        # Never log exception text, URLs, response bodies, codes or tokens.
        cause = exc.__cause__
        response = getattr(cause, "response", None)
        provider_error = "unknown"
        if response is not None:
            try:
                value = response.json().get("error")
                if value in ("invalid_client", "invalid_grant", "redirect_uri_mismatch", "access_denied"):
                    provider_error = value
            except (ValueError, AttributeError, TypeError):
                pass
        logger.warning("Google sign-in failed: google_exchange cause=%s http_status=%s provider_error=%s",
            type(cause).__name__ if cause else "verification", getattr(response, "status_code", None), provider_error)
        return _signin_failure(settings)

    if (
        not identity.email
        or not identity.email_verified
        or not settings.is_auth_email_allowed(identity.email)
    ):
        logger.warning("Google sign-in failed: identity_not_verified_or_not_allowed")
        return _signin_failure(settings)

    user = _upsert_google_user(db, identity)

    token = create_access_token(
        subject=str(user.id),
        claims={"organization_id": str(user.organization_id)},
        settings=settings,
    )

    redirect = RedirectResponse(
        url=_frontend_url(settings, "/"),
        status_code=status.HTTP_302_FOUND,
    )
    set_auth_cookie(redirect, token, settings=settings)
    _clear_signin_state_cookie(redirect, settings)
    return redirect
