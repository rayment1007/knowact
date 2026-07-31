"""Route/service tests for the Action Center (Requirement 8).

Exercises the real FastAPI app against an ephemeral SQLite database (see
``conftest.py``) with cookie-based auth:

* ``POST /api/actions`` (manual) creates an action in status ``OPEN`` with
  ``ai_generated=False`` (Requirements 8.2, 8.5);
* ``POST /api/actions`` referencing a confirmed knowledge item creates an
  ``OPEN`` action with ``ai_generated=True``, inherited evidence, and exactly
  one ``CREATE_ACTION`` audit row in the same transaction (Requirements 8.1,
  8.5, 13.1);
* referencing a non-confirmed suggestion yields ``409``;
* ``PATCH /api/actions/{id}`` marks an action ``DONE`` (Requirement 8.4);
* ``GET /api/actions`` lists actions, filterable by status and business entity
  (Requirement 8.3);
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


def _make_entity(db: Session, org_id: Any, name: str) -> core_models.BusinessEntity:
    entity = core_models.BusinessEntity(
        organization_id=org_id,
        entity_type=core_models.BusinessEntityType.PROJECT,
        name=name,
    )
    db.add(entity)
    db.flush()
    return entity


def _make_knowledge(
    db: Session,
    org_id: Any,
    *,
    status: core_models.SuggestionStatus = core_models.SuggestionStatus.CONFIRMED,
    business_entity_id: Any = None,
    evidence_text: str = "confirmed evidence",
) -> core_models.KnowledgeItem:
    knowledge = core_models.KnowledgeItem(
        organization_id=org_id,
        business_entity_id=business_entity_id,
        summary="A recommendation",
        evidence_text=evidence_text,
        knowledge_type="FACT",
        status=status,
    )
    db.add(knowledge)
    db.flush()
    return knowledge


# ---------------------------------------------------------------------------
# Manual create -> OPEN + ai_generated False
# ---------------------------------------------------------------------------


def test_manual_create_is_open_and_human_originated(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])

    resp = client.post(
        "/api/actions",
        json={"title": "Call the client back", "description": "Follow up on quote"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "OPEN"
    assert body["ai_generated"] is False
    assert body["title"] == "Call the client back"
    assert body["organization_id"] == str(seeded_user["organization"].id)


def test_manual_create_still_writes_one_create_action_audit(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])

    created = client.post("/api/actions", json={"title": "Manual task"}).json()

    audit = client.get(
        "/api/audit",
        params={"action_type": "CREATE_ACTION", "target_id": created["id"]},
    ).json()
    assert len(audit) == 1
    assert audit[0]["target_type"] == "ActionItem"
    assert audit[0]["detail"]["ai_generated"] is False


def test_create_requires_authentication(client: TestClient) -> None:
    resp = client.post("/api/actions", json={"title": "No auth"})
    assert resp.status_code == 401


def test_create_invalid_payload_returns_422(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])

    resp = client.post("/api/actions", json={"title": ""})

    assert resp.status_code == 422
    assert client.get("/api/actions").json() == []


# ---------------------------------------------------------------------------
# Create from confirmed suggestion -> OPEN + ai_generated True + one audit row
# ---------------------------------------------------------------------------


def test_create_from_confirmed_suggestion_is_ai_generated_with_evidence(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    org_id = seeded_user["organization"].id
    entity = _make_entity(db_session, org_id, "Orion")
    knowledge = _make_knowledge(
        db_session,
        org_id,
        business_entity_id=entity.id,
        evidence_text="Client asked about estate planning.",
    )

    resp = client.post(
        "/api/actions",
        json={
            "title": "Prepare estate planning proposal",
            "knowledge_item_id": str(knowledge.id),
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "OPEN"
    assert body["ai_generated"] is True
    # Evidence and business entity are inherited from the confirmed suggestion.
    assert body["evidence_text"] == "Client asked about estate planning."
    assert body["business_entity_id"] == str(entity.id)
    assert body["knowledge_item_id"] == str(knowledge.id)

    # Exactly one CREATE_ACTION audit row in the same transaction.
    audit = client.get(
        "/api/audit",
        params={"action_type": "CREATE_ACTION", "target_id": body["id"]},
    ).json()
    assert len(audit) == 1
    assert audit[0]["detail"]["ai_generated"] is True
    assert audit[0]["actor_id"] == str(seeded_user["user"].id)


def test_create_from_unconfirmed_suggestion_returns_409(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    org_id = seeded_user["organization"].id
    knowledge = _make_knowledge(
        db_session, org_id, status=core_models.SuggestionStatus.SUGGESTED
    )

    resp = client.post(
        "/api/actions",
        json={"title": "Too early", "knowledge_item_id": str(knowledge.id)},
    )

    assert resp.status_code == 409
    assert client.get("/api/actions").json() == []


# ---------------------------------------------------------------------------
# Update status -> DONE
# ---------------------------------------------------------------------------


def test_patch_marks_action_done(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    created = client.post("/api/actions", json={"title": "Finish report"}).json()

    resp = client.patch(f"/api/actions/{created['id']}", json={"status": "DONE"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "DONE"


def test_patch_updates_fields_without_touching_others(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    created = client.post(
        "/api/actions", json={"title": "Draft", "description": "keep me"}
    ).json()

    resp = client.patch(
        f"/api/actions/{created['id']}", json={"status": "IN_PROGRESS"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "IN_PROGRESS"
    # description untouched by a status-only patch.
    assert body["description"] == "keep me"


# ---------------------------------------------------------------------------
# List with filters
# ---------------------------------------------------------------------------


def test_list_filters_by_status_and_entity(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    org_id = seeded_user["organization"].id
    entity = _make_entity(db_session, org_id, "Alpha")

    open_action = client.post(
        "/api/actions",
        json={"title": "Open one", "business_entity_id": str(entity.id)},
    ).json()
    done_action = client.post("/api/actions", json={"title": "Will be done"}).json()
    client.patch(f"/api/actions/{done_action['id']}", json={"status": "DONE"})

    open_only = client.get("/api/actions", params={"status": "OPEN"}).json()
    assert [a["id"] for a in open_only] == [open_action["id"]]

    done_only = client.get("/api/actions", params={"status": "DONE"}).json()
    assert [a["id"] for a in done_only] == [done_action["id"]]

    by_entity = client.get(
        "/api/actions", params={"business_entity_id": str(entity.id)}
    ).json()
    assert [a["id"] for a in by_entity] == [open_action["id"]]

    all_actions = client.get("/api/actions").json()
    assert {a["id"] for a in all_actions} == {open_action["id"], done_action["id"]}


def test_list_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/actions").status_code == 401


# ---------------------------------------------------------------------------
# Cross-organization access -> 404
# ---------------------------------------------------------------------------


def test_cross_org_action_access_returns_404(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    created = client.post("/api/actions", json={"title": "Owned action"}).json()

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

    assert (
        client.patch(
            f"/api/actions/{created['id']}", json={"status": "DONE"}
        ).status_code
        == 404
    )
    assert client.get("/api/actions").json() == []
