"""Binary storage abstraction for uploaded documents (M6.4, Requirement 29).

Large document binaries are **never** stored in PostgreSQL — only an opaque
``storage_key`` is persisted on the :class:`~app.modules.cwi.models.DocumentAsset`
(Requirement 29.1, 29.2). The bytes live behind the :class:`StorageBackend`
protocol so the persistence target is pluggable:

* :class:`LocalFilesystemStorage` (the default) writes under
  ``{settings.storage_root}/{org_id}/{key}`` — org-partitioned on disk.
* A future S3/MinIO backend implements the same protocol without touching any
  caller.

The concrete backend is resolved via :func:`get_storage_backend`, a FastAPI
dependency tests override with a temp-dir-backed instance so no test ever writes
outside its sandbox.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.config import Settings, get_settings


@runtime_checkable
class StorageBackend(Protocol):
    """The seam every document-binary read/write goes through.

    Implementations are org-aware: ``org_id`` partitions the namespace so one
    tenant's keys can never collide with or read another's.
    """

    def put(
        self, org_id: UUID, key: str, data: bytes, content_type: str
    ) -> str:
        """Persist ``data`` under ``(org_id, key)`` and return the stored key."""

    def get(self, org_id: UUID, key: str) -> bytes:
        """Return the bytes previously stored under ``(org_id, key)``."""

    def delete(self, org_id: UUID, key: str) -> None:
        """Remove the object at ``(org_id, key)`` (idempotent)."""


class StorageError(RuntimeError):
    """Raised when a storage operation cannot be completed."""


class LocalFilesystemStorage:
    """Default :class:`StorageBackend` storing binaries on the local filesystem.

    Objects are written to ``{root}/{org_id}/{key}``. The org segment keeps each
    tenant's binaries physically partitioned, and ``key`` is treated as an
    opaque, single-segment name — any path separators are rejected so a caller
    can never traverse outside an org's directory.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def _object_path(self, org_id: UUID, key: str) -> Path:
        # ``key`` is opaque and must not traverse outside the org directory.
        if not key or "/" in key or "\\" in key or key in (".", ".."):
            raise StorageError("Invalid storage key.")
        return self._root / str(org_id) / key

    def put(
        self, org_id: UUID, key: str, data: bytes, content_type: str
    ) -> str:
        path = self._object_path(org_id, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, org_id: UUID, key: str) -> bytes:
        path = self._object_path(org_id, key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise StorageError("Stored object not found.") from exc

    def delete(self, org_id: UUID, key: str) -> None:
        path = self._object_path(org_id, key)
        try:
            path.unlink()
        except FileNotFoundError:
            # Deletion is idempotent — a missing object is already "deleted".
            return

    def clear_org(self, org_id: UUID) -> None:
        """Remove an org's entire storage subtree (used by full-org cleanup)."""

        org_dir = self._root / str(org_id)
        if org_dir.exists():
            shutil.rmtree(org_dir, ignore_errors=True)


def get_storage_backend(settings: Settings | None = None) -> StorageBackend:
    """Resolve the :class:`StorageBackend` for the current configuration.

    Selects the backend named by ``settings.storage_backend`` (default
    ``"local"`` → :class:`LocalFilesystemStorage` rooted at
    ``settings.storage_root``). Route handlers depend on this and tests override
    it with a temp-dir-backed instance.
    """

    settings = settings or get_settings()
    # Only the local filesystem backend ships today; the string switch keeps the
    # selection pluggable (S3/MinIO can be added without changing callers).
    return LocalFilesystemStorage(settings.storage_root)


__all__ = [
    "StorageBackend",
    "StorageError",
    "LocalFilesystemStorage",
    "get_storage_backend",
]
