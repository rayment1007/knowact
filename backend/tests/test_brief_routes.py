"""Route tests for briefs and business entities (Requirement 10).

Exercises the real FastAPI app against an ephemeral SQLite database (see
``conftest.py``) with cookie-based auth, mirroring the other route tests:

* ``GET /api/brief/daily`` generates and persists a ``DAILY`` brief for the
  caller's organization, grounded in confirmed context (Requirements 10.1,
  10.3);
* ``POST`` / ``GET /api/business-entities`` create and list org-scoped entities;
* ``GET /api/business-entities/{id}/brief`` produces an ``ENTITY`` brief scoped
  to that entity (Requirement 10.5);
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


def _entity_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "name": "Acme Corp",
        "entity_type": "CLIENT",
        "description": "Key client account",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Daily brief
# ---------------------------------------------------------------------------


def test_daily_brief_returns_brief_with_headline_and_sections(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])

    resp = client.get("/api/brief/daily")

    assert resp.status_code == 200
    body = resp.json()
    assert body["scope"] == "DAILY"
    assert body["scope_ref_id"] is None
    assert body["generated_for"] == str(seeded_user["user"].id)
    assert body["organization_id"] == str(seeded_user["organization"].id)
    content = body["content"]
    # The brief carries a headline plus the required sections (Requirement 10.3).
    assert content["headline"]
    for section in ("priorities", "recommended_actions", "follow_ups", "risks"):
        assert section in content
        assert isinstance(content[section], list)


def test_daily_brief_grounded_in_confirmed_knowledge(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    org_id = seeded_user["organization"].id

    # One CONFIRMED and one SUGGESTED knowledge item: only the confirmed one
    # should ground the brief (Requirement 10.2).
    db_session.add(
        core_models.KnowledgeItem(
            organization_id=org_id,
            summary="Confirmed insight about roadmap",
            key_points=[],
            evidence_text="evidence",
            knowledge_type="INSIGHT",
            status=core_models.SuggestionStatus.CONFIRMED,
        )
    )
    db_session.add(
        core_models.KnowledgeItem(
            organization_id=org_id,
            summary="Suggested but unconfirmed insight",
            key_points=[],
            evidence_text="evidence",
            knowledge_type="INSIGHT",
            status=core_models.SuggestionStatus.SUGGESTED,
        )
    )
    db_session.flush()

    resp = client.get("/api/brief/daily")

    assert resp.status_code == 200
    priorities = resp.json()["content"]["priorities"]
    texts = {p["text"] for p in priorities}
    assert "Confirmed insight about roadmap" in texts
    assert "Suggested but unconfirmed insight" not in texts


def test_daily_brief_requires_authentication(client: TestClient) -> None:
    resp = client.get("/api/brief/daily")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Business entities (create / list)
# ---------------------------------------------------------------------------


def test_create_and_list_business_entities(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])

    created = client.post("/api/business-entities", json=_entity_payload())
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Acme Corp"
    assert body["entity_type"] == "CLIENT"
    # Org is derived from the session, not the payload.
    assert body["organization_id"] == str(seeded_user["organization"].id)

    listed = client.get("/api/business-entities")
    assert listed.status_code == 200
    names = {e["name"] for e in listed.json()}
    assert names == {"Acme Corp"}


def test_create_business_entity_invalid_payload_returns_422(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])

    resp = client.post(
        "/api/business-entities",
        json={"name": "", "entity_type": "NOT_A_TYPE"},
    )

    assert resp.status_code == 422
    assert client.get("/api/business-entities").json() == []


def test_business_entities_require_authentication(client: TestClient) -> None:
    assert client.get("/api/business-entities").status_code == 401
    assert (
        client.post("/api/business-entities", json=_entity_payload()).status_code
        == 401
    )


# ---------------------------------------------------------------------------
# Entity brief
# ---------------------------------------------------------------------------


def test_entity_brief_returns_entity_scoped_brief(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    entity = client.post("/api/business-entities", json=_entity_payload()).json()

    resp = client.get(f"/api/business-entities/{entity['id']}/brief")

    assert resp.status_code == 200
    body = resp.json()
    assert body["scope"] == "ENTITY"
    assert body["scope_ref_id"] == entity["id"]
    assert body["generated_for"] == str(seeded_user["user"].id)
    assert body["content"]  # non-empty structured content


def test_entity_brief_requires_authentication(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    entity = client.post("/api/business-entities", json=_entity_payload()).json()
    client.cookies.clear()

    resp = client.get(f"/api/business-entities/{entity['id']}/brief")
    assert resp.status_code == 401


def test_entity_brief_cross_org_returns_404(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    # Entity owned by the seeded user's org.
    _login(client, seeded_user["email"], seeded_user["password"])
    entity = client.post("/api/business-entities", json=_entity_payload()).json()

    # A second organization + user.
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

    # The other org cannot see the first org's entity or its brief.
    assert (
        client.get(f"/api/business-entities/{entity['id']}/brief").status_code
        == 404
    )
    assert client.get("/api/business-entities").json() == []
