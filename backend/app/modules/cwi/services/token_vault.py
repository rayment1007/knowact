"""Symmetric encryption of OAuth tokens at rest (the Token Vault).

The :class:`TokenVault` is the single place OAuth access/refresh tokens are
encrypted before persistence and decrypted for a transient outbound Google
call. It uses Fernet (authenticated symmetric encryption) with a key supplied
via ``settings.token_encryption_key``.

Security contract (Requirements 25.1, 25.4):

* **Only ciphertext is persisted.** :meth:`encrypt` returns ``bytes`` stored in
  the ``LargeBinary`` token columns; plaintext exists only transiently in
  memory during an outbound call.
* **No silent no-op when unconfigured.** If ``token_encryption_key`` is unset,
  the vault raises :class:`TokenVaultError` on first use rather than storing
  plaintext or a reversible encoding.
* **Plaintext is never logged.** This module never logs token values; callers
  must not either.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings, get_settings


class TokenVaultError(Exception):
    """Raised when the vault is misconfigured or a value cannot be decrypted.

    The message never contains token plaintext or key material.
    """


class TokenVault:
    """Encrypts/decrypts OAuth tokens with a Fernet key from settings.

    The key is read from ``settings.token_encryption_key``. Construction is
    cheap and does not validate the key; the key is validated lazily on first
    :meth:`encrypt`/:meth:`decrypt` so importing/constructing the service never
    fails merely because a local environment has no key configured — but any
    *use* without a valid key raises a clear :class:`TokenVaultError`.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._fernet: Fernet | None = None

    def _cipher(self) -> Fernet:
        """Return the lazily-built Fernet cipher, or raise if misconfigured."""

        if self._fernet is not None:
            return self._fernet

        key = self._settings.token_encryption_key
        if not key:
            raise TokenVaultError(
                "Token encryption key is not configured "
                "(set token_encryption_key); refusing to store tokens in the "
                "clear."
            )
        try:
            # Fernet accepts a 32-byte urlsafe-base64 key as str or bytes.
            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except (ValueError, TypeError) as exc:
            raise TokenVaultError(
                "Token encryption key is invalid; expected a urlsafe base64 "
                "32-byte Fernet key."
            ) from exc
        return self._fernet

    def encrypt(self, plaintext: str) -> bytes:
        """Encrypt a token string, returning ciphertext bytes for storage.

        Raises:
            TokenVaultError: if the vault has no valid key configured.
        """

        return self._cipher().encrypt(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        """Decrypt stored ciphertext back to the token string.

        Raises:
            TokenVaultError: if the vault is unconfigured or the ciphertext is
                invalid/tampered (never leaks key or plaintext detail).
        """

        try:
            return self._cipher().decrypt(ciphertext).decode("utf-8")
        except InvalidToken as exc:
            raise TokenVaultError(
                "Stored token could not be decrypted (invalid ciphertext or "
                "wrong key)."
            ) from exc


__all__ = ["TokenVault", "TokenVaultError"]
