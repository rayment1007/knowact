"""Route/service tests for source item ingestion (Requirement 3).

Exercises the real FastAPI app against an ephemeral SQLite database (see
``conftest.py``) with cookie-based auth, mirroring ``test_auth_routes.py``:

* ``POST /api/source-items`` collects an item in status ``NEW`` and records the
  creating user/organization (Requirement 3.1);
* ``GET /api/source-items`` returns the org's inbox, filterable by status and
  category (Requirement 3.2);
* ``GET /api/source-items/{id}`` returns the item plus its current
  classification if one exists, else ``None`` (Requirement 3.3);
* ``POST /api/source-items/{id}/dismiss`` sets status ``DISMISSED``
  (Requirement 3.4);
* an invalid payload yields ``422`` and persists nothing (Requirement 3.5);
* unauthenticated requests yield ``401`` and cross-organization access ``404``
  (Requirements 2.2, 2.3).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import models as core_models
from app.security import hash_password


def _login(client: TestClient, email: str, password: str) -> Any:
    return client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "source_type": "EMAIL",
        "title": "Quarterly budget review",
        "content": "Please review the attached Q3 budget before Friday.",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Ingest (POST) -> status NEW
# ---------------------------------------------------------------------------


def test_ingest_creates_item_with_status_new(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])

    resp = client.post("/api/source-items", json=_valid_payload())

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "NEW"
    assert body["title"] == "Quarterly budget review"
    assert body["source_type"] == "EMAIL"
    # Org + creating user are derived from the session, not the payload.
    assert body["organization_id"] == str(seeded_user["organization"].id)
    assert body["created_by"] == str(seeded_user["user"].id)


def test_ingest_requires_authentication(client: TestClient) -> None:
    resp = client.post("/api/source-items", json=_valid_payload())
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# List inbox (GET) with filters
# ---------------------------------------------------------------------------


def test_list_inbox_returns_org_items(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    client.post("/api/source-items", json=_valid_payload(title="One"))
    client.post("/api/source-items", json=_valid_payload(title="Two"))

    resp = client.get("/api/source-items")

    assert resp.status_code == 200
    titles = {item["title"] for item in resp.json()}
    assert titles == {"One", "Two"}


def test_list_inbox_filters_by_status(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    keep = client.post("/api/source-items", json=_valid_payload(title="Keep")).json()
    drop = client.post("/api/source-items", json=_valid_payload(title="Drop")).json()
    client.post(f"/api/source-items/{drop['id']}/dismiss")

    new_items = client.get("/api/source-items", params={"status": "NEW"}).json()
    dismissed = client.get(
        "/api/source-items", params={"status": "DISMISSED"}
    ).json()

    assert [i["id"] for i in new_items] == [keep["id"]]
    assert [i["id"] for i in dismissed] == [drop["id"]]


def test_list_inbox_filters_by_category(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    classified = client.post(
        "/api/source-items", json=_valid_payload(title="Classified")
    ).json()
    client.post("/api/source-items", json=_valid_payload(title="Unclassified"))

    # Attach a CLIENT classification to the first item directly.
    db_session.add(
        core_models.ClassificationResult(
            source_item_id=UUID(classified["id"]),
            relevance=core_models.Relevance.WORK_RELATED,
            business_category=core_models.BusinessCategory.CLIENT,
            sensitivity=core_models.Sensitivity.INTERNAL,
            confidence=0.9,
            status=core_models.SuggestionStatus.SUGGESTED,
        )
    )
    db_session.flush()

    client_items = client.get(
        "/api/source-items", params={"category": "CLIENT"}
    ).json()
    project_items = client.get(
        "/api/source-items", params={"category": "PROJECT"}
    ).json()

    assert [i["id"] for i in client_items] == [classified["id"]]
    assert project_items == []


# ---------------------------------------------------------------------------
# Get single item (+ classification)
# ---------------------------------------------------------------------------


def test_get_item_without_classification(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    created = client.post("/api/source-items", json=_valid_payload()).json()

    resp = client.get(f"/api/source-items/{created['id']}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["source_item"]["id"] == created["id"]
    assert body["classification"] is None


def test_get_item_with_classification(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    created = client.post("/api/source-items", json=_valid_payload()).json()
    db_session.add(
        core_models.ClassificationResult(
            source_item_id=UUID(created["id"]),
            relevance=core_models.Relevance.WORK_RELATED,
            business_category=core_models.BusinessCategory.PROJECT,
            sensitivity=core_models.Sensitivity.INTERNAL,
            confidence=0.75,
            status=core_models.SuggestionStatus.SUGGESTED,
        )
    )
    db_session.flush()

    resp = client.get(f"/api/source-items/{created['id']}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["classification"] is not None
    assert body["classification"]["business_category"] == "PROJECT"
    assert body["classification"]["confidence"] == 0.75


def test_get_item_requires_authentication(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    created = client.post("/api/source-items", json=_valid_payload()).json()
    client.cookies.clear()

    resp = client.get(f"/api/source-items/{created['id']}")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Dismiss -> DISMISSED
# ---------------------------------------------------------------------------


def test_dismiss_sets_status_dismissed(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    created = client.post("/api/source-items", json=_valid_payload()).json()

    resp = client.post(f"/api/source-items/{created['id']}/dismiss")

    assert resp.status_code == 200
    assert resp.json()["status"] == "DISMISSED"


# ---------------------------------------------------------------------------
# Validation (422, no partial write)
# ---------------------------------------------------------------------------


def test_invalid_payload_returns_422_and_persists_nothing(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])

    # Empty title + missing content + bad enum: all invalid.
    resp = client.post(
        "/api/source-items",
        json={"source_type": "NOT_A_TYPE", "title": "", "content": ""},
    )

    assert resp.status_code == 422
    # Nothing was persisted.
    assert client.get("/api/source-items").json() == []


# ---------------------------------------------------------------------------
# Cross-organization access -> 404
# ---------------------------------------------------------------------------


def test_cross_org_get_returns_404(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    # Item owned by the seeded user's org.
    _login(client, seeded_user["email"], seeded_user["password"])
    created = client.post("/api/source-items", json=_valid_payload()).json()

    # A second organization + user in it.
    other_org = core_models.Organization(name="Other Org")
    db_session.add(other_org)
    db_session.flush()
    other_user = core_models.User(
        organization_id=other_org.id,
        email="other@example.com",
        full_name="Other User",
        password_hash=hash_password("other-pass-123"),
        role="ADMIN",
    )
    db_session.add(other_user)
    db_session.flush()

    client.cookies.clear()
    _login(client, "other@example.com", "other-pass-123")

    # The other org cannot see or dismiss the first org's item.
    assert client.get(f"/api/source-items/{created['id']}").status_code == 404
    assert (
        client.post(f"/api/source-items/{created['id']}/dismiss").status_code
        == 404
    )
    assert client.get("/api/source-items").json() == []
