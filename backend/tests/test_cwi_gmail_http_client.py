"""Unit tests for the production :class:`HttpGmailClient`.

These tests exercise the REAL client but perform NO network I/O: every HTTP
call is served by an in-memory ``httpx.MockTransport`` injected via the
``transport`` param (mirroring the OAuth HTTP client tests). They verify that:

* ``list_messages`` calls the right endpoints/params and maps a ``format=full``
  message resource into a :class:`GmailMessage` (headers, plain-text body,
  labels, attachments, aware ``received_at``), newest first.
* ``create_draft`` / ``update_draft`` / ``send_draft`` hit the right verbs +
  endpoints, encode a base64url MIME message, and return the expected ids.
* Any HTTP failure is surfaced as a secret-free :class:`GmailClientError`.
"""

from __future__ import annotations

import base64
from datetime import timezone
from email import message_from_bytes

import httpx
import pytest

from app.config import Settings
from app.modules.cwi.services.gmail_client import (
    GmailClientError,
    GmailDraftMessage,
    HttpGmailClient,
)

_ACCESS_TOKEN = "ya29.super-secret-access-token"

_SETTINGS = Settings(
    google_oauth_client_id="cid.apps.googleusercontent.com",
    google_oauth_client_secret="secret",
)


def _client(handler) -> HttpGmailClient:
    """A client whose outbound HTTP is served by an in-memory mock transport."""

    return HttpGmailClient(_SETTINGS, transport=httpx.MockTransport(handler))


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_raw(raw: str) -> "object":
    padded = raw + "=" * (-len(raw) % 4)
    return message_from_bytes(base64.urlsafe_b64decode(padded.encode("ascii")))


# ---------------------------------------------------------------------------
# list_messages
# ---------------------------------------------------------------------------


def test_list_messages_maps_full_message_newest_first() -> None:
    seen_params: dict[str, list[str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/messages"):
            seen_params["maxResults"] = request.url.params.get_list("maxResults")
            seen_params["labelIds"] = request.url.params.get_list("labelIds")
            return httpx.Response(
                200,
                json={"messages": [{"id": "m1"}, {"id": "m2"}]},
            )
        if path.endswith("/messages/m1"):
            assert request.url.params.get("format") == "full"
            return httpx.Response(
                200,
                json={
                    "id": "m1",
                    "threadId": "t1",
                    "internalDate": "1000",  # oldest
                    "labelIds": ["INBOX"],
                    "payload": {
                        "mimeType": "text/plain",
                        "headers": [
                            {"name": "From", "value": "alice@example.com"},
                            {"name": "To", "value": "me@example.com, x@e.com"},
                            {"name": "Subject", "value": "Hello"},
                        ],
                        "body": {"data": _b64url("plain body one")},
                    },
                },
            )
        if path.endswith("/messages/m2"):
            return httpx.Response(
                200,
                json={
                    "id": "m2",
                    "threadId": "t2",
                    "internalDate": "5000",  # newest
                    "labelIds": ["INBOX", "IMPORTANT"],
                    "payload": {
                        "mimeType": "multipart/mixed",
                        "headers": [
                            {"name": "From", "value": "bob@example.com"},
                            {"name": "To", "value": "me@example.com"},
                            {"name": "Subject", "value": "Report"},
                        ],
                        "parts": [
                            {
                                "mimeType": "text/plain",
                                "body": {"data": _b64url("body two")},
                            },
                            {
                                "mimeType": "application/pdf",
                                "filename": "report.pdf",
                                "body": {"attachmentId": "att1"},
                            },
                        ],
                    },
                },
            )
        raise AssertionError(f"unexpected path: {path}")

    client = _client(handler)
    messages = client.list_messages(
        access_token=_ACCESS_TOKEN, label_ids=["INBOX"], max_results=25
    )

    assert seen_params["maxResults"] == ["25"]
    assert seen_params["labelIds"] == ["INBOX"]

    # Newest first: m2 (internalDate 5000) precedes m1 (1000).
    assert [m.gmail_message_id for m in messages] == ["m2", "m1"]

    newest = messages[0]
    assert newest.gmail_thread_id == "t2"
    assert newest.sender == "bob@example.com"
    assert newest.subject == "Report"
    assert newest.body_text == "body two"
    assert newest.labels == ["INBOX", "IMPORTANT"]
    assert newest.has_attachments is True
    assert newest.received_at.tzinfo is not None
    assert newest.received_at.astimezone(timezone.utc).timestamp() == 5.0

    oldest = messages[1]
    assert oldest.recipients == ["me@example.com", "x@e.com"]
    assert oldest.body_text == "plain body one"
    assert oldest.has_attachments is False


def test_list_messages_empty_listing_returns_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = _client(handler)
    assert client.list_messages(access_token=_ACCESS_TOKEN) == []


def test_list_messages_http_error_raises_secret_free_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _client(handler)
    with pytest.raises(GmailClientError) as exc_info:
        client.list_messages(access_token=_ACCESS_TOKEN)
    assert _ACCESS_TOKEN not in str(exc_info.value)


# ---------------------------------------------------------------------------
# create_draft / update_draft / send_draft
# ---------------------------------------------------------------------------


def test_create_draft_posts_raw_mime_and_returns_id() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/drafts")
        assert request.headers["Authorization"] == f"Bearer {_ACCESS_TOKEN}"
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "draft-123"})

    client = _client(handler)
    draft_id = client.create_draft(
        access_token=_ACCESS_TOKEN,
        message=GmailDraftMessage(
            to_recipients=["a@example.com"],
            subject="Subj",
            body_text="Hi there",
            thread_id="thread-9",
        ),
        idempotency_key="k1",
    )

    assert draft_id == "draft-123"
    body = captured["body"]["message"]
    assert body["threadId"] == "thread-9"
    mime = _decode_raw(body["raw"])
    assert mime["To"] == "a@example.com"
    assert mime["Subject"] == "Subj"
    assert mime.get_payload(decode=True).decode("utf-8") == "Hi there"


def test_update_draft_puts_to_draft_endpoint() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path.endswith("/drafts/draft-77")
        captured["hit"] = True
        return httpx.Response(200, json={"id": "draft-77"})

    client = _client(handler)
    client.update_draft(
        access_token=_ACCESS_TOKEN,
        draft_id="draft-77",
        message=GmailDraftMessage(
            to_recipients=["a@example.com"], subject="S", body_text="B"
        ),
    )
    assert captured["hit"] is True


def test_send_draft_with_draft_id_posts_drafts_send() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/drafts/send")
        import json

        assert json.loads(request.content) == {"id": "draft-5"}
        return httpx.Response(200, json={"id": "sent-1"})

    client = _client(handler)
    sent_id = client.send_draft(
        access_token=_ACCESS_TOKEN,
        message=GmailDraftMessage(
            to_recipients=["a@example.com"], subject="S", body_text="B"
        ),
        idempotency_key="k1",
        draft_id="draft-5",
    )
    assert sent_id == "sent-1"


def test_send_draft_without_draft_id_posts_messages_send() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/messages/send")
        import json

        assert "raw" in json.loads(request.content)
        return httpx.Response(200, json={"id": "sent-2"})

    client = _client(handler)
    sent_id = client.send_draft(
        access_token=_ACCESS_TOKEN,
        message=GmailDraftMessage(
            to_recipients=["a@example.com"], subject="S", body_text="B"
        ),
        idempotency_key="k1",
    )
    assert sent_id == "sent-2"


def test_create_draft_http_error_raises_secret_free_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "insufficient scope"})

    client = _client(handler)
    with pytest.raises(GmailClientError) as exc_info:
        client.create_draft(
            access_token=_ACCESS_TOKEN,
            message=GmailDraftMessage(
                to_recipients=["a@example.com"], subject="S", body_text="B"
            ),
            idempotency_key="k1",
        )
    assert _ACCESS_TOKEN not in str(exc_info.value)
