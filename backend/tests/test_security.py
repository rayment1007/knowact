"""Unit tests for password hashing, JWT, and auth-cookie helpers.

Covers :mod:`app.security`:

* password hashing round-trip, wrong-password rejection, and that a malformed
  hash returns ``False`` rather than raising;
* JWT ``create_access_token`` / ``decode_access_token`` round-trip, plus
  rejection of expired tokens, invalid signatures, and tokens missing ``sub``;
* ``set_auth_cookie`` / ``clear_auth_cookie`` set and delete an HTTP-only
  cookie with the configured attributes.

References Requirements 1.2 and 1.6.
"""

from __future__ import annotations

from datetime import timedelta

import jwt
import pytest
from fastapi import Response

from app.config import Settings
from app.security import (
    TokenError,
    clear_auth_cookie,
    create_access_token,
    decode_access_token,
    hash_password,
    set_auth_cookie,
    verify_password,
)


def _test_settings() -> Settings:
    """Isolated settings with a fixed secret so token tests are deterministic."""

    return Settings(
        jwt_secret="unit-test-secret-key",
        jwt_algorithm="HS256",
        access_token_expire_minutes=60,
    )


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def test_hash_password_round_trip_verifies() -> None:
    password = "correct horse battery staple"
    hashed = hash_password(password)

    # The hash must not be the plaintext and must verify against it.
    assert hashed != password
    assert verify_password(password, hashed) is True


def test_hash_password_is_salted_unique_per_call() -> None:
    # bcrypt salts each hash, so two hashes of the same password differ but both
    # still verify.
    password = "same-password"
    first = hash_password(password)
    second = hash_password(password)

    assert first != second
    assert verify_password(password, first) is True
    assert verify_password(password, second) is True


def test_verify_password_rejects_wrong_password() -> None:
    hashed = hash_password("the-real-password")
    assert verify_password("a-different-password", hashed) is False


def test_verify_password_malformed_hash_returns_false_not_exception() -> None:
    # A malformed/garbage hash must be treated as a non-match, never raise.
    assert verify_password("any-password", "not-a-valid-bcrypt-hash") is False
    assert verify_password("any-password", "") is False


# ---------------------------------------------------------------------------
# JWT bearer tokens
# ---------------------------------------------------------------------------


def test_create_and_decode_token_round_trip() -> None:
    settings = _test_settings()
    token = create_access_token(
        subject="user-123",
        claims={"organization_id": "org-456"},
        settings=settings,
    )

    claims = decode_access_token(token, settings=settings)

    assert claims["sub"] == "user-123"
    assert claims["organization_id"] == "org-456"
    # Standard timestamp claims are always present.
    assert "exp" in claims
    assert "iat" in claims


def test_reserved_claims_cannot_be_overridden_by_caller() -> None:
    settings = _test_settings()
    # Attempt to spoof the subject via extra claims; the helper must win.
    token = create_access_token(
        subject="real-subject",
        claims={"sub": "attacker-controlled"},
        settings=settings,
    )
    claims = decode_access_token(token, settings=settings)
    assert claims["sub"] == "real-subject"


def test_decode_expired_token_raises_token_error() -> None:
    settings = _test_settings()
    token = create_access_token(
        subject="user-123",
        settings=settings,
        expires_delta=timedelta(minutes=-1),  # already expired
    )

    with pytest.raises(TokenError):
        decode_access_token(token, settings=settings)


def test_decode_token_with_invalid_signature_raises_token_error() -> None:
    settings = _test_settings()
    token = create_access_token(subject="user-123", settings=settings)

    # Verify with a different secret -> signature check must fail.
    other_settings = Settings(
        jwt_secret="a-completely-different-secret",
        jwt_algorithm="HS256",
        access_token_expire_minutes=60,
    )

    with pytest.raises(TokenError):
        decode_access_token(token, settings=other_settings)


def test_decode_token_missing_sub_raises_token_error() -> None:
    settings = _test_settings()

    # Hand-craft a validly-signed token that has no ``sub`` claim.
    token = jwt.encode(
        {"organization_id": "org-1"},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(TokenError):
        decode_access_token(token, settings=settings)


def test_decode_malformed_token_raises_token_error() -> None:
    settings = _test_settings()
    with pytest.raises(TokenError):
        decode_access_token("this.is.not.a.jwt", settings=settings)


# ---------------------------------------------------------------------------
# Auth cookie
# ---------------------------------------------------------------------------


def test_set_auth_cookie_sets_httponly_cookie() -> None:
    settings = _test_settings()
    token = create_access_token(subject="user-123", settings=settings)

    response = Response()
    set_auth_cookie(response, token, settings=settings)

    set_cookie = response.headers.get("set-cookie")
    assert set_cookie is not None
    # Named per settings, carries the token, and is HttpOnly.
    assert f"{settings.auth_cookie_name}=" in set_cookie
    assert token in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Path=/" in set_cookie


def test_clear_auth_cookie_expires_cookie() -> None:
    settings = _test_settings()

    response = Response()
    clear_auth_cookie(response, settings=settings)

    set_cookie = response.headers.get("set-cookie")
    assert set_cookie is not None
    assert f"{settings.auth_cookie_name}=" in set_cookie
    # Deletion is expressed as an immediate expiry / zero max-age.
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()
