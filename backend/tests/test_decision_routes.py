"""Route/service tests for Decision Memory (Requirement 9).

Exercises the real FastAPI app against an ephemeral SQLite database (see
``conftest.py``) with cookie-based auth:

* ``POST /api/decisions`` records a decision with its statement, rationale,
  evidence, owner, and date, and writes exactly one ``CREATE_DECISION`` audit
  row in the same transaction (Requirements 9.1, 9.2, 9.4);
* ``GET /api/decisions`` lists decisions newest-first, filterable by business
  entity (Requirement 9.3);
* unauthenticated requests yield ``401``, cross-organization access ``404``,
  and an invalid payload ``422`` (Requirements 2.2, 2.3).
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


def _make_entity(db: Session, org_id: Any, name: str) -> core_models.BusinessEntity:
    entity = core_models.BusinessEntity(
        organization_id=org_id,
        entity_type=core_models.BusinessEntityType.PROJECT,
        name=name,
    )
    db.add(entity)
    db.flush()
    return entity


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "title": "Adopt new CRM",
        "decision": "We will migrate to Acme CRM next quarter.",
        "rationale": "Better reporting and lower total cost of ownership.",
        "evidence_text": "Vendor comparison memo dated 2024-03-01.",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Create -> persisted with rationale/evidence/owner/date + one audit row
# ---------------------------------------------------------------------------


def test_create_persists_decision_with_owner_and_evidence(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])

    resp = client.post("/api/decisions", json=_valid_payload())

    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Adopt new CRM"
    assert body["decision"] == "We will migrate to Acme CRM next quarter."
    assert body["rationale"] == "Better reporting and lower total cost of ownership."
    assert body["evidence_text"] == "Vendor comparison memo dated 2024-03-01."
    # Owner is the authenticated user (Requirement 9.1).
    assert body["decided_by"] == str(seeded_user["user"].id)
    assert body["organization_id"] == str(seeded_user["organization"].id)
    # A decided-at date is always recorded.
    assert body["decided_at"] is not None


def test_create_writes_exactly_one_create_decision_audit(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])

    created = client.post("/api/decisions", json=_valid_payload()).json()

    audit = client.get(
        "/api/audit",
        params={"action_type": "CREATE_DECISION", "target_id": created["id"]},
    ).json()
    assert len(audit) == 1
    assert audit[0]["target_type"] == "DecisionRecord"
    assert audit[0]["actor_id"] == str(seeded_user["user"].id)


def test_create_links_business_entity_and_preserves_evidence(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    org_id = seeded_user["organization"].id
    entity = _make_entity(db_session, org_id, "Orion")

    resp = client.post(
        "/api/decisions",
        json=_valid_payload(
            business_entity_id=str(entity.id),
            evidence_text="AI-suggested summary of the client thread.",
        ),
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["business_entity_id"] == str(entity.id)
    # Originating evidence text is persisted with the record (Requirement 9.4).
    assert body["evidence_text"] == "AI-suggested summary of the client thread."


def test_create_requires_authentication(client: TestClient) -> None:
    resp = client.post("/api/decisions", json=_valid_payload())
    assert resp.status_code == 401


def test_create_invalid_payload_returns_422(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])

    # Missing required rationale/evidence and empty decision.
    resp = client.post(
        "/api/decisions", json={"title": "x", "decision": ""}
    )

    assert resp.status_code == 422
    # Nothing persisted.
    assert client.get("/api/decisions").json() == []


# ---------------------------------------------------------------------------
# List with entity filter, newest first
# ---------------------------------------------------------------------------


def test_list_returns_decisions_newest_first(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])

    first = client.post(
        "/api/decisions",
        json=_valid_payload(title="First", decided_at="2024-01-01T09:00:00+00:00"),
    ).json()
    second = client.post(
        "/api/decisions",
        json=_valid_payload(title="Second", decided_at="2024-06-01T09:00:00+00:00"),
    ).json()

    listed = client.get("/api/decisions").json()
    assert [d["id"] for d in listed] == [second["id"], first["id"]]


def test_list_filters_by_business_entity(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    org_id = seeded_user["organization"].id
    entity = _make_entity(db_session, org_id, "Alpha")

    with_entity = client.post(
        "/api/decisions",
        json=_valid_payload(title="Scoped", business_entity_id=str(entity.id)),
    ).json()
    without_entity = client.post(
        "/api/decisions", json=_valid_payload(title="Unscoped")
    ).json()

    by_entity = client.get(
        "/api/decisions", params={"business_entity_id": str(entity.id)}
    ).json()
    assert [d["id"] for d in by_entity] == [with_entity["id"]]

    all_decisions = client.get("/api/decisions").json()
    assert {d["id"] for d in all_decisions} == {
        with_entity["id"],
        without_entity["id"],
    }


def test_list_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/decisions").status_code == 401


# ---------------------------------------------------------------------------
# Cross-organization access -> isolated (404-equivalent: never leaks)
# ---------------------------------------------------------------------------


def test_cross_org_decisions_are_isolated(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    client.post("/api/decisions", json=_valid_payload(title="Owned decision"))

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

    # The other tenant sees none of the first org's decisions.
    assert client.get("/api/decisions").json() == []
