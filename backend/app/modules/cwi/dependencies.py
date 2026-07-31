"""FastAPI dependencies for the Connected Workspace Intelligence routers.

The single :func:`google_oauth_client` dependency resolves the injectable
:class:`~app.modules.cwi.services.google_oauth.GoogleOAuthClient`. Both the
Google sign-in routes and the integration routes depend on it, so a test can
override this one callable (``app.dependency_overrides[google_oauth_client]``)
to inject a deterministic fake — guaranteeing no real OAuth/network call ever
occurs under test while production transparently gets the HTTP client when
Google credentials are configured.
"""

from __future__ import annotations

from fastapi import Depends

from app.config import Settings, get_settings
from app.modules.cwi.services.calendar_client import (
    CalendarClient,
    get_calendar_client,
)
from app.modules.cwi.services.embedding import (
    EmbeddingProvider,
    get_embedding_provider,
)
from app.modules.cwi.services.gmail_client import GmailClient, get_gmail_client
from app.modules.cwi.services.google_oauth import (
    GoogleOAuthClient,
    get_google_oauth_client,
)
from app.modules.cwi.services.storage import StorageBackend, get_storage_backend


def google_oauth_client(
    settings: Settings = Depends(get_settings),
) -> GoogleOAuthClient:
    """Resolve the Google OAuth transport for the current configuration."""

    return get_google_oauth_client(settings)


def gmail_client(
    settings: Settings = Depends(get_settings),
) -> GmailClient:
    """Resolve the Gmail transport for the current configuration.

    Tests override this callable
    (``app.dependency_overrides[gmail_client]``) to inject a deterministic
    :class:`~app.modules.cwi.services.gmail_client.FakeGmailClient`, so no real
    Gmail/network call ever occurs under test.
    """

    return get_gmail_client(settings)


def calendar_client(
    settings: Settings = Depends(get_settings),
) -> CalendarClient:
    """Resolve the Google Calendar transport for the current configuration.

    Tests override this callable
    (``app.dependency_overrides[calendar_client]``) to inject a deterministic
    :class:`~app.modules.cwi.services.calendar_client.FakeCalendarClient`, so no
    real Calendar/network call ever occurs under test.
    """

    return get_calendar_client(settings)


def storage_backend(
    settings: Settings = Depends(get_settings),
) -> StorageBackend:
    """Resolve the document :class:`StorageBackend` for the configuration.

    Tests override this callable
    (``app.dependency_overrides[storage_backend]``) with a temp-dir-backed
    :class:`~app.modules.cwi.services.storage.LocalFilesystemStorage`, so no
    test ever writes document binaries outside its sandbox.
    """

    return get_storage_backend(settings)


def embedding_provider(
    settings: Settings = Depends(get_settings),
) -> EmbeddingProvider:
    """Resolve the :class:`EmbeddingProvider` for the current configuration.

    Tests override this callable
    (``app.dependency_overrides[embedding_provider]``) to inject the
    deterministic
    :class:`~app.modules.cwi.services.embedding.FakeEmbeddingProvider`, so no
    real embeddings API is ever called under test.
    """

    return get_embedding_provider(settings)


__all__ = [
    "google_oauth_client",
    "gmail_client",
    "calendar_client",
    "storage_backend",
    "embedding_provider",
]
