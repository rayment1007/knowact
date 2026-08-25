"""Gmail transport abstraction (the injectable Gmail client).

Gmail reads are expressed behind the :class:`GmailClient` protocol so the rest
of the CWI code never talks to Gmail directly. This is the seam that keeps
Gmail ingestion **default-safe and fully testable**:

* Tests inject :class:`FakeGmailClient`, which performs **no network I/O**
  whatsoever — it returns canned :class:`GmailMessage` values. No CWI test ever
  makes a real Gmail call (Requirement 34).
* Production uses :class:`HttpGmailClient`, a thin wrapper over Gmail's real
  HTTP endpoints, selected only when live Google credentials are present. Until
  the phase that first needs live Gmail traffic it raises a clear error rather
  than making a partial/unsafe request, so no accidental network call happens
  in local/mock runs.

The concrete client is resolved via :func:`get_gmail_client`, a FastAPI
dependency that routes override in tests with a pre-seeded fake.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Protocol, runtime_checkable

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


def _log_gmail_error(operation: str, exc: Exception) -> None:
    """Log the underlying Gmail API failure for diagnosis (secret-free).

    Provider response bodies can contain message or recipient data, so only a
    status code and exception class are recorded.
    """

    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        logger.error(
            "Gmail %s failed: HTTP %s",
            operation,
            exc.response.status_code,
        )
    else:
        logger.error("Gmail %s failed: %s", operation, type(exc).__name__)

#: Base URL for the Gmail REST API v1, scoped to the authorized mailbox.
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

#: Short, fixed timeout for every outbound Gmail call.
_HTTP_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class GmailMessage:
    """A single Gmail message projected into the fields ingestion needs.

    Deliberately minimal and provider-neutral: it carries only the metadata and
    plain-text body the pipeline consumes. Raw MIME / attachment binaries are
    never represented here — attachments are summarized by ``has_attachments``.
    """

    gmail_message_id: str
    gmail_thread_id: str
    sender: str
    subject: str
    body_text: str
    received_at: datetime
    recipients: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    has_attachments: bool = False


@dataclass(frozen=True)
class GmailDraftMessage:
    """The outbound message payload handed to the transport for draft/send.

    Provider-neutral and minimal: the backend-resolved recipients, the subject,
    the plain-text body, and the thread it belongs to. Recipients are resolved
    and validated by the backend — the transport never derives them
    (Requirement 32.2).
    """

    to_recipients: list[str]
    subject: str
    body_text: str
    thread_id: str | None = None


class GmailClientError(Exception):
    """Raised when a Gmail draft/send operation fails.

    The message is safe to surface to users: it must never contain a token or
    key.
    """


@runtime_checkable
class GmailClient(Protocol):
    """The seam every Gmail read/draft/send goes through.

    A production implementation performs real HTTP; the test implementation is
    a deterministic fake. No CWI code depends on the concrete type.
    """

    def list_messages(
        self,
        *,
        access_token: str,
        label_ids: list[str] | None = None,
        max_results: int = 100,
    ) -> list[GmailMessage]:
        """Return messages for the authorized mailbox (newest first)."""

    def create_draft(
        self,
        *,
        access_token: str,
        message: GmailDraftMessage,
        idempotency_key: str,
    ) -> str:
        """Create a Gmail draft (``users.drafts.create``) and return its id."""

    def update_draft(
        self,
        *,
        access_token: str,
        draft_id: str,
        message: GmailDraftMessage,
    ) -> None:
        """Update an existing Gmail draft in place (``users.drafts.update``)."""

    def send_draft(
        self,
        *,
        access_token: str,
        message: GmailDraftMessage,
        idempotency_key: str,
        draft_id: str | None = None,
    ) -> str:
        """Send a draft (``users.drafts.send``) and return the sent message id.

        ``idempotency_key`` is a local correlation key. The fake transport
        honors it deterministically, but Gmail's HTTP send endpoint does not
        provide the same idempotency guarantee; callers must treat an ambiguous
        production failure as non-retriable until the Sent mailbox is checked.
        """


class FakeGmailClient:
    """A deterministic, offline :class:`GmailClient` for tests / local dev.

    Returns the canned messages it was constructed with; nothing touches the
    network. The ``access_token`` is accepted and ignored, so a test can drive
    the full sync/ingest path with no real Gmail call.
    """

    def __init__(self, messages: list[GmailMessage] | None = None) -> None:
        self._messages = list(messages or [])
        # Recorded for assertions in tests.
        self.list_calls: int = 0
        # Draft/send state — deterministic and idempotent by key.
        self._draft_counter: int = 0
        self._sent_counter: int = 0
        self._drafts_by_key: dict[str, str] = {}
        self._sent_by_key: dict[str, str] = {}
        self.create_draft_calls: int = 0
        self.update_draft_calls: int = 0
        self.send_calls: int = 0
        self.fail_next_create_draft: bool = False
        self.fail_next_send: bool = False

    def set_messages(self, messages: list[GmailMessage]) -> None:
        """Replace the canned message set (useful across sync passes)."""

        self._messages = list(messages)

    def list_messages(
        self,
        *,
        access_token: str,
        label_ids: list[str] | None = None,
        max_results: int = 100,
    ) -> list[GmailMessage]:
        self.list_calls += 1
        messages = self._messages
        if label_ids:
            wanted = set(label_ids)
            messages = [
                m for m in messages if wanted.intersection(set(m.labels))
            ]
        return list(messages[:max_results])

    def create_draft(
        self,
        *,
        access_token: str,
        message: GmailDraftMessage,
        idempotency_key: str,
    ) -> str:
        # A repeated create with the same key resolves to the same draft id.
        existing = self._drafts_by_key.get(idempotency_key)
        if existing is not None:
            return existing
        if self.fail_next_create_draft:
            self.fail_next_create_draft = False
            raise GmailClientError("Simulated Gmail draft create failure.")
        self.create_draft_calls += 1
        self._draft_counter += 1
        draft_id = f"draft-{idempotency_key[:12]}-{self._draft_counter}"
        self._drafts_by_key[idempotency_key] = draft_id
        return draft_id

    def update_draft(
        self,
        *,
        access_token: str,
        draft_id: str,
        message: GmailDraftMessage,
    ) -> None:
        self.update_draft_calls += 1

    def send_draft(
        self,
        *,
        access_token: str,
        message: GmailDraftMessage,
        idempotency_key: str,
        draft_id: str | None = None,
    ) -> str:
        # Test-only deterministic behavior: repeated keys resolve to one result.
        existing = self._sent_by_key.get(idempotency_key)
        if existing is not None:
            return existing
        if self.fail_next_send:
            self.fail_next_send = False
            raise GmailClientError("Simulated Gmail send failure.")
        self.send_calls += 1
        self._sent_counter += 1
        sent_id = f"sent-{idempotency_key[:12]}-{self._sent_counter}"
        self._sent_by_key[idempotency_key] = sent_id
        return sent_id


def _b64url_decode(data: str) -> bytes:
    """Decode a base64url string (Gmail body/attachment payloads), padding-safe."""

    if not data:
        return b""
    # Gmail uses URL-safe base64 without padding; restore it before decoding.
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _b64url_encode(raw: bytes) -> str:
    """Encode bytes into an unpadded base64url string for a Gmail ``raw`` field."""

    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _header(headers: list[dict], name: str) -> str:
    """Return the (case-insensitive) header value from a payload header list."""

    target = name.lower()
    for header in headers or []:
        if str(header.get("name", "")).lower() == target:
            return str(header.get("value", ""))
    return ""


def _split_recipients(value: str) -> list[str]:
    """Split a comma-separated recipient header into trimmed addresses."""

    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _extract_plain_text(payload: dict) -> str:
    """Walk a Gmail message payload and return its best plain-text body.

    Prefers ``text/plain`` parts; falls back to the top-level body when the
    message is not multipart. Never raises — a body that cannot be decoded
    yields an empty string.
    """

    mime_type = str(payload.get("mimeType", ""))
    body = payload.get("body") or {}
    parts = payload.get("parts") or []

    if mime_type == "text/plain" and body.get("data"):
        return _b64url_decode(str(body["data"])).decode("utf-8", errors="replace")

    if parts:
        # Prefer a text/plain part anywhere in the tree.
        for part in parts:
            if str(part.get("mimeType", "")) == "text/plain":
                text = _extract_plain_text(part)
                if text:
                    return text
        # Otherwise recurse into multipart containers.
        for part in parts:
            if str(part.get("mimeType", "")).startswith("multipart/"):
                text = _extract_plain_text(part)
                if text:
                    return text

    if not parts and body.get("data"):
        return _b64url_decode(str(body["data"])).decode("utf-8", errors="replace")

    return ""


def _has_attachments(payload: dict) -> bool:
    """Return ``True`` if any part of the payload is an attachment."""

    for part in payload.get("parts") or []:
        if part.get("filename"):
            return True
        body = part.get("body") or {}
        if body.get("attachmentId"):
            return True
        if _has_attachments(part):
            return True
    return False


class HttpGmailClient:
    """A production :class:`GmailClient` over Gmail's real REST API v1.

    Selected only when Google client credentials are configured (see
    :func:`get_gmail_client`); tests inject an ``httpx.MockTransport`` so no real
    network call ever occurs. The passed ``access_token`` is used solely as the
    Bearer credential and is **never** placed in an exception message or log —
    every failure raises :class:`GmailClientError` with a fixed, secret-free
    message (mirroring :class:`HttpGoogleOAuthClient`).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        # Tests inject an ``httpx.MockTransport`` so no real network call occurs.
        self._transport = transport

    # -- Internal helpers ---------------------------------------------------

    def _client(self, access_token: str) -> httpx.Client:
        """Build a short-timeout Bearer-authorized client (test-injectable)."""

        return httpx.Client(
            base_url=GMAIL_API_BASE,
            timeout=_HTTP_TIMEOUT_SECONDS,
            transport=self._transport,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    @staticmethod
    def _build_raw_message(message: GmailDraftMessage) -> str:
        """Build a base64url-encoded RFC 2822 MIME message from a draft."""

        mime = MIMEText(message.body_text or "", _subtype="plain", _charset="utf-8")
        if message.to_recipients:
            mime["To"] = ", ".join(message.to_recipients)
        mime["Subject"] = message.subject or ""
        return _b64url_encode(mime.as_bytes())

    @staticmethod
    def _to_message_body(message: GmailDraftMessage) -> dict:
        """Build the ``{"raw": ..., "threadId": ...}`` message body for Gmail."""

        body: dict[str, str] = {"raw": HttpGmailClient._build_raw_message(message)}
        if message.thread_id:
            body["threadId"] = message.thread_id
        return body

    def _to_gmail_message(self, detail: dict) -> GmailMessage:
        """Map a ``format=full`` message resource into a :class:`GmailMessage`."""

        payload = detail.get("payload") or {}
        headers = payload.get("headers") or []
        internal_ms = int(detail.get("internalDate", 0) or 0)
        received_at = datetime.fromtimestamp(
            internal_ms / 1000, tz=timezone.utc
        )
        return GmailMessage(
            gmail_message_id=str(detail.get("id", "")),
            gmail_thread_id=str(detail.get("threadId", "")),
            sender=_header(headers, "From"),
            subject=_header(headers, "Subject"),
            body_text=_extract_plain_text(payload),
            received_at=received_at,
            recipients=_split_recipients(_header(headers, "To")),
            labels=list(detail.get("labelIds") or []),
            has_attachments=_has_attachments(payload),
        )

    # -- Public API ---------------------------------------------------------

    def list_messages(
        self,
        *,
        access_token: str,
        label_ids: list[str] | None = None,
        max_results: int = 100,
    ) -> list[GmailMessage]:
        try:
            with self._client(access_token) as http:
                params: list[tuple[str, str]] = [
                    ("maxResults", str(max_results))
                ]
                for label in label_ids or []:
                    params.append(("labelIds", label))
                listing = http.get("/messages", params=params)
                listing.raise_for_status()
                refs = listing.json().get("messages") or []

                messages: list[GmailMessage] = []
                for ref in refs[:max_results]:
                    message_id = str(ref.get("id", ""))
                    if not message_id:
                        continue
                    detail_resp = http.get(
                        f"/messages/{message_id}", params={"format": "full"}
                    )
                    detail_resp.raise_for_status()
                    messages.append(self._to_gmail_message(detail_resp.json()))

                # Newest first by received timestamp.
                messages.sort(key=lambda m: m.received_at, reverse=True)
                return messages[:max_results]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            _log_gmail_error("list_messages", exc)
            raise GmailClientError(
                "Gmail messages could not be listed."
            ) from exc

    def create_draft(
        self,
        *,
        access_token: str,
        message: GmailDraftMessage,
        idempotency_key: str,
    ) -> str:
        try:
            with self._client(access_token) as http:
                resp = http.post(
                    "/drafts",
                    json={"message": self._to_message_body(message)},
                )
                resp.raise_for_status()
                return str(resp.json()["id"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            _log_gmail_error("create_draft", exc)
            raise GmailClientError(
                "The Gmail draft could not be created."
            ) from exc

    def update_draft(
        self,
        *,
        access_token: str,
        draft_id: str,
        message: GmailDraftMessage,
    ) -> None:
        try:
            with self._client(access_token) as http:
                resp = http.put(
                    f"/drafts/{draft_id}",
                    json={"message": self._to_message_body(message)},
                )
                resp.raise_for_status()
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            _log_gmail_error("update_draft", exc)
            raise GmailClientError(
                "The Gmail draft could not be updated."
            ) from exc

    def send_draft(
        self,
        *,
        access_token: str,
        message: GmailDraftMessage,
        idempotency_key: str,
        draft_id: str | None = None,
    ) -> str:
        try:
            with self._client(access_token) as http:
                if draft_id:
                    resp = http.post("/drafts/send", json={"id": draft_id})
                else:
                    resp = http.post(
                        "/messages/send",
                        json=self._to_message_body(message),
                    )
                resp.raise_for_status()
                return str(resp.json()["id"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            _log_gmail_error("send_draft", exc)
            raise GmailClientError(
                "The Gmail message could not be sent."
            ) from exc


def get_gmail_client(settings: Settings | None = None) -> GmailClient:
    """Resolve the :class:`GmailClient` for the current configuration.

    Returns the production :class:`HttpGmailClient` when real Google client
    credentials are configured; otherwise a :class:`FakeGmailClient` so local /
    mock development runs with no network dependency. Route handlers depend on
    this function and tests override it with a pre-seeded fake, so no network
    I/O ever occurs under test.
    """

    settings = settings or get_settings()
    if settings.google_oauth_client_id and settings.google_oauth_client_secret:
        return HttpGmailClient(settings)
    return FakeGmailClient()


__all__ = [
    "GMAIL_API_BASE",
    "GmailMessage",
    "GmailDraftMessage",
    "GmailClientError",
    "GmailClient",
    "FakeGmailClient",
    "HttpGmailClient",
    "get_gmail_client",
]
