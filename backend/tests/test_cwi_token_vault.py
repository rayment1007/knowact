"""Unit tests for the CWI :class:`TokenVault` (Requirement 25.1).

The vault encrypts OAuth tokens at rest with Fernet. These tests confirm the
round-trip works, that ciphertext is not the plaintext, and — crucially — that
an unconfigured vault RAISES rather than silently storing plaintext.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.modules.cwi.services.token_vault import TokenVault, TokenVaultError

_KEY = Fernet.generate_key().decode()


def _settings(key: str | None) -> Settings:
    return Settings(token_encryption_key=key)


def test_encrypt_decrypt_round_trip() -> None:
    vault = TokenVault(_settings(_KEY))
    ciphertext = vault.encrypt("super-secret-access-token")

    assert isinstance(ciphertext, bytes)
    # Ciphertext must not contain the plaintext.
    assert b"super-secret-access-token" not in ciphertext
    assert vault.decrypt(ciphertext) == "super-secret-access-token"


def test_unconfigured_vault_raises_on_use() -> None:
    vault = TokenVault(_settings(None))
    with pytest.raises(TokenVaultError):
        vault.encrypt("token")
    with pytest.raises(TokenVaultError):
        vault.decrypt(b"anything")


def test_invalid_key_raises() -> None:
    vault = TokenVault(_settings("not-a-valid-fernet-key"))
    with pytest.raises(TokenVaultError):
        vault.encrypt("token")


def test_invalid_ciphertext_raises() -> None:
    vault = TokenVault(_settings(_KEY))
    # Bytes that are not a valid Fernet token must not decrypt.
    with pytest.raises(TokenVaultError):
        vault.decrypt(b"definitely-not-a-valid-fernet-token")
