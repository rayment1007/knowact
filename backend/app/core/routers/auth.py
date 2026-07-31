"""Authentication routes (Requirement 1).

Authentication uses a JWT delivered as an **HTTP-only cookie** (see
:mod:`app.security`) — there is no ``Authorization`` header scheme. The login
route verifies credentials and sets the signed JWT as an HTTP-only cookie on
the response; the browser returns it automatically on subsequent same-site
requests, and client JavaScript never reads it. Logout clears the cookie.

Routes
------
* ``POST /api/auth/login`` — verify credentials and set the auth cookie.
  Invalid credentials yield ``401`` and no cookie is set (Requirement 1.2).
* ``POST /api/auth/logout`` — clear the auth cookie (Requirement 1.4).
* ``GET /api/auth/me`` — return the current user and organization; never the
  password hash (Requirement 1.3).

All paths are mounted under the ``/api`` prefix by :func:`app.main.create_app`
via ``register_routers``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.models import Organization, User
from app.core.schemas import (
    CurrentUserResponse,
    LoginRequest,
    LoginResponse,
    OrganizationResponse,
    UserResponse,
)
from app.database import get_db
from app.dependencies import get_current_organization, get_current_user
from app.security import (
    clear_auth_cookie,
    create_access_token,
    set_auth_cookie,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Uniform 401 for a failed login. The detail is intentionally generic so we
# never disclose whether the email exists or the password was wrong, which
# avoids leaking account information to unauthenticated callers.
_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid email or password.",
)


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LoginResponse:
    """Authenticate a user and set the JWT auth cookie.

    Verifies the submitted email/password against the ``users`` table. On
    success, issues a signed JWT carrying the user id (``sub``) and the
    ``organization_id`` claim, sets it as an HTTP-only cookie on the response,
    and returns the safe user projection (the token itself is never placed in
    the response body). On any failure — unknown email or bad password —
    responds ``401`` **without** setting a cookie (Requirement 1.2).
    """

    user = db.execute(
        select(User).where(User.email == payload.email)
    ).scalar_one_or_none()

    # Verifying the password even when the user is unknown is unnecessary here;
    # verify_password never raises and returns False for a missing user. We do
    # not distinguish the two cases in the response (see _INVALID_CREDENTIALS).
    if user is None or not verify_password(payload.password, user.password_hash):
        raise _INVALID_CREDENTIALS

    token = create_access_token(
        subject=str(user.id),
        claims={"organization_id": str(user.organization_id)},
        settings=settings,
    )
    set_auth_cookie(response, token, settings=settings)

    return LoginResponse(user=UserResponse.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    settings: Settings = Depends(get_settings),
) -> Response:
    """Clear the auth cookie (Requirement 1.4).

    Deletes the HTTP-only auth cookie so the browser stops sending it. Returns
    ``204 No Content``. Logout is intentionally unauthenticated: calling it
    without a valid session is a harmless no-op that still clears any cookie.
    """

    clear_auth_cookie(response, settings=settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=CurrentUserResponse)
def me(
    user: User = Depends(get_current_user),
    organization: Organization = Depends(get_current_organization),
) -> CurrentUserResponse:
    """Return the current user and organization.

    Protected by :func:`app.dependencies.get_current_user`, which yields ``401``
    for missing/invalid tokens. The response uses :class:`UserResponse`, which
    omits ``password_hash`` (Requirements 1.3, 1.6).
    """

    return CurrentUserResponse(
        user=UserResponse.model_validate(user),
        organization=OrganizationResponse.model_validate(organization),
    )
