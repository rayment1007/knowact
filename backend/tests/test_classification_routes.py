"""Route tests for classification endpoints (Requirements 4 and 5).

Exercises the real FastAPI app against an ephemeral SQLite database (see
``conftest.py``) with cookie-based auth, mirroring ``test_source_item_routes.py``:

* ``POST /api/source-items/{id}/classify`` runs AI classification and persists a
  ``SUGGESTED`` result routed through ``call_with_fallback`` (Requirements 4.1,
  4.2, 5.5);
* ``POST /api/source-items/{id}/confirm-classification`` sets the result to
  ``CONFIRMED`` and the item to ``CLASSIFIED``, honoring an optional human
  override (Requirements 5.1, 5.2);
* ``POST /api/source-items/{id}/reject-classification`` sets the result to
  ``REJECTED`` while retaining the row (Requirement 5.3);
* unauthenticated requests yield ``401`` and cross-organization access ``404``
  (Requirements 2.2, 2.3).
"""

from __future__ import annotations

from typing import Any

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
        "title": "Project milestone update",
        "content": "The project sprint deliverable is ready for review.",
    }
    payload.update(overrides)
    return payload


def _create_item(client: TestClient, **overrides: Any) -> dict[str, Any]:
    return client.post("/api/source-items", json=_valid_payload(**overrides)).json()


# ---------------------------------------------------------------------------
# Classify -> SUGGESTED
# ---------------------------------------------------------------------------


def test_classify_returns_suggested_result(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    created = _create_item(client)

    resp = client.post(f"/api/source-items/{created['id']}/classify")

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "SUGGESTED"
    assert body["source_item_id"] == created["id"]
    # Bounded confidence (Requirement 4.3) and evidence-backed axes present.
    assert 0.0 <= body["confidence"] <= 1.0
    assert body["relevance"] is not None
    assert body["business_category"] is not None
    assert body["sensitivity"] is not None


def test_classify_does_not_mark_item_classified(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    # A suggestion never auto-confirms: the item stays NEW until confirmed.
    _login(client, seeded_user["email"], seeded_user["password"])
    created = _create_item(client)

    client.post(f"/api/source-items/{created['id']}/classify")

    detail = client.get(f"/api/source-items/{created['id']}").json()
    assert detail["source_item"]["status"] == "NEW"
    assert detail["classification"]["status"] == "SUGGESTED"


def test_classify_requires_authentication(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    created = _create_item(client)
    client.cookies.clear()

    resp = client.post(f"/api/source-items/{created['id']}/classify")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Confirm -> CONFIRMED + item CLASSIFIED
# ---------------------------------------------------------------------------


def test_confirm_sets_confirmed_and_classified(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    created = _create_item(client)
    client.post(f"/api/source-items/{created['id']}/classify")

    resp = client.post(f"/api/source-items/{created['id']}/confirm-classification")

    assert resp.status_code == 200
    assert resp.json()["status"] == "CONFIRMED"

    detail = client.get(f"/api/source-items/{created['id']}").json()
    assert detail["source_item"]["status"] == "CLASSIFIED"


def test_confirm_writes_audit_row(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    created = _create_item(client)
    client.post(f"/api/source-items/{created['id']}/classify")
    client.post(f"/api/source-items/{created['id']}/confirm-classification")

    audit_rows = (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == "CONFIRM_CLASSIFICATION")
        .all()
    )
    assert len(audit_rows) == 1


def test_confirm_applies_override(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    created = _create_item(client)
    client.post(f"/api/source-items/{created['id']}/classify")

    override = {
        "relevance": "IRRELEVANT",
        "business_category": "RISK",
        "sensitivity": "CONFIDENTIAL",
    }
    resp = client.post(
        f"/api/source-items/{created['id']}/confirm-classification", json=override
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "CONFIRMED"
    assert body["relevance"] == "IRRELEVANT"
    assert body["business_category"] == "RISK"
    assert body["sensitivity"] == "CONFIDENTIAL"


def test_confirm_without_classification_returns_404(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    created = _create_item(client)

    # No classify call, so there is nothing to confirm.
    resp = client.post(f"/api/source-items/{created['id']}/confirm-classification")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Reject -> REJECTED (retained)
# ---------------------------------------------------------------------------


def test_reject_sets_rejected_and_retains_row(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    created = _create_item(client)
    client.post(f"/api/source-items/{created['id']}/classify")

    resp = client.post(f"/api/source-items/{created['id']}/reject-classification")

    assert resp.status_code == 200
    assert resp.json()["status"] == "REJECTED"

    # The row is retained as a negative signal (Requirement 5.3).
    rejected = (
        db_session.query(core_models.ClassificationResult)
        .filter(
            core_models.ClassificationResult.status
            == core_models.SuggestionStatus.REJECTED
        )
        .all()
    )
    assert len(rejected) == 1


# ---------------------------------------------------------------------------
# Cross-organization access -> 404
# ---------------------------------------------------------------------------


def test_cross_org_classify_returns_404(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    created = _create_item(client)

    other_org = core_models.Organization(name="Other Org")
    db_session.add(other_org)
    db_session.flush()
    db_session.add(
        core_models.User(
            organization_id=other_org.id,
            email="other@example.com",
            full_name="Other User",
            password_hash=hash_password("other-pass-123"),
            role="ADMIN",
        )
    )
    db_session.flush()

    client.cookies.clear()
    _login(client, "other@example.com", "other-pass-123")

    assert (
        client.post(f"/api/source-items/{created['id']}/classify").status_code == 404
    )
    assert (
        client.post(
            f"/api/source-items/{created['id']}/confirm-classification"
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/source-items/{created['id']}/reject-classification"
        ).status_code
        == 404
    )
