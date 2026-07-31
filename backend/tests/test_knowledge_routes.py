"""Route/service tests for the knowledge hub and extraction (Requirements 6, 7).

Exercises the real FastAPI app against an ephemeral SQLite database (see
``conftest.py``) with cookie-based auth:

* ``POST /api/source-items/{id}/extract`` extracts a ``SUGGESTED`` knowledge
  item from a source item with a CONFIRMED classification (Requirement 6.2), and
  refuses gated content with ``409`` (Requirements 6.3, 6.4);
* ``POST /api/knowledge/{id}/confirm`` sets status ``CONFIRMED`` and writes
  exactly one ``CONFIRM_KNOWLEDGE`` audit row (Requirements 7.1, 13.1);
* ``POST /api/knowledge/{id}/reject`` sets status ``REJECTED`` (Requirement 7.2);
* ``GET /api/knowledge`` lists items, filterable by business entity and status
  (Requirement 7.3);
* ``GET /api/knowledge/{id}`` returns the item plus linked actions and decisions
  with evidence (Requirements 7.4, 7.5);
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


def _create_item(client: TestClient, **overrides: Any) -> dict[str, Any]:
    payload = {
        "source_type": "EMAIL",
        "title": "Project Orion status",
        "content": "The Orion project milestone is on track for next sprint.",
    }
    payload.update(overrides)
    return client.post("/api/source-items", json=payload).json()


def _confirm_classification(
    db: Session,
    item_id: str,
    *,
    relevance: core_models.Relevance = core_models.Relevance.WORK_RELATED,
    business_category: core_models.BusinessCategory = core_models.BusinessCategory.PROJECT,
    sensitivity: core_models.Sensitivity = core_models.Sensitivity.INTERNAL,
) -> None:
    """Attach a CONFIRMED classification to a source item directly."""

    db.add(
        core_models.ClassificationResult(
            source_item_id=UUID(item_id),
            relevance=relevance,
            business_category=business_category,
            sensitivity=sensitivity,
            confidence=0.9,
            status=core_models.SuggestionStatus.CONFIRMED,
        )
    )
    db.flush()


# ---------------------------------------------------------------------------
# Extract (POST /api/source-items/{id}/extract)
# ---------------------------------------------------------------------------


def test_extract_creates_suggested_knowledge(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    item = _create_item(client)
    _confirm_classification(db_session, item["id"])

    resp = client.post(f"/api/source-items/{item['id']}/extract")

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "SUGGESTED"
    assert body["source_item_id"] == item["id"]
    assert body["evidence_text"]  # non-empty (Requirement 6.6)


def test_extract_refuses_gated_relevance_with_409(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    item = _create_item(client)
    # PERSONAL relevance is excluded from the knowledge base (Requirement 6.3).
    _confirm_classification(
        db_session, item["id"], relevance=core_models.Relevance.PERSONAL
    )

    resp = client.post(f"/api/source-items/{item['id']}/extract")

    assert resp.status_code == 409
    # Nothing persisted.
    assert client.get("/api/knowledge").json() == []


def test_extract_refuses_highly_sensitive_without_ack(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    item = _create_item(client)
    _confirm_classification(
        db_session,
        item["id"],
        sensitivity=core_models.Sensitivity.HIGHLY_SENSITIVE,
    )

    # Without acknowledgment -> 409 (Requirement 6.4).
    refused = client.post(f"/api/source-items/{item['id']}/extract")
    assert refused.status_code == 409
    assert client.get("/api/knowledge").json() == []

    # With acknowledgment -> proceeds (Requirement 6.5).
    ok = client.post(
        f"/api/source-items/{item['id']}/extract", json={"acknowledged": True}
    )
    assert ok.status_code == 201
    assert ok.json()["status"] == "SUGGESTED"


def test_extract_requires_authentication(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    item = _create_item(client)
    client.cookies.clear()

    resp = client.post(f"/api/source-items/{item['id']}/extract")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Confirm -> CONFIRMED + one CONFIRM_KNOWLEDGE audit row
# ---------------------------------------------------------------------------


def _extract_knowledge(
    client: TestClient, db_session: Session, **item_overrides: Any
) -> dict[str, Any]:
    item = _create_item(client, **item_overrides)
    _confirm_classification(db_session, item["id"])
    return client.post(f"/api/source-items/{item['id']}/extract").json()


def test_confirm_sets_confirmed_and_writes_single_audit(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    knowledge = _extract_knowledge(client, db_session)

    resp = client.post(f"/api/knowledge/{knowledge['id']}/confirm")

    assert resp.status_code == 200
    assert resp.json()["status"] == "CONFIRMED"

    # Exactly one CONFIRM_KNOWLEDGE audit row for this target.
    audit = client.get(
        "/api/audit",
        params={"action_type": "CONFIRM_KNOWLEDGE", "target_id": knowledge["id"]},
    ).json()
    assert len(audit) == 1
    assert audit[0]["target_type"] == "KnowledgeItem"
    assert audit[0]["actor_id"] == str(seeded_user["user"].id)


def test_reject_sets_rejected(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    knowledge = _extract_knowledge(client, db_session)

    resp = client.post(f"/api/knowledge/{knowledge['id']}/reject")

    assert resp.status_code == 200
    assert resp.json()["status"] == "REJECTED"


# ---------------------------------------------------------------------------
# List (GET /api/knowledge) with filters
# ---------------------------------------------------------------------------


def test_list_knowledge_filters_by_entity_and_status(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    org_id = seeded_user["organization"].id

    # Two entities and one knowledge item linked to each.
    entity_a = core_models.BusinessEntity(
        organization_id=org_id,
        entity_type=core_models.BusinessEntityType.PROJECT,
        name="Entity A",
    )
    entity_b = core_models.BusinessEntity(
        organization_id=org_id,
        entity_type=core_models.BusinessEntityType.PROJECT,
        name="Entity B",
    )
    db_session.add_all([entity_a, entity_b])
    db_session.flush()

    k_a = core_models.KnowledgeItem(
        organization_id=org_id,
        business_entity_id=entity_a.id,
        summary="Knowledge A",
        evidence_text="evidence a",
        knowledge_type="FACT",
        status=core_models.SuggestionStatus.CONFIRMED,
    )
    k_b = core_models.KnowledgeItem(
        organization_id=org_id,
        business_entity_id=entity_b.id,
        summary="Knowledge B",
        evidence_text="evidence b",
        knowledge_type="FACT",
        status=core_models.SuggestionStatus.SUGGESTED,
    )
    db_session.add_all([k_a, k_b])
    db_session.flush()

    by_entity = client.get(
        "/api/knowledge", params={"business_entity_id": str(entity_a.id)}
    ).json()
    assert [k["id"] for k in by_entity] == [str(k_a.id)]

    confirmed = client.get(
        "/api/knowledge", params={"status": "CONFIRMED"}
    ).json()
    assert [k["id"] for k in confirmed] == [str(k_a.id)]


# ---------------------------------------------------------------------------
# Detail (GET /api/knowledge/{id}) with linked actions/decisions
# ---------------------------------------------------------------------------


def test_detail_includes_linked_actions_and_decisions(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    org_id = seeded_user["organization"].id

    entity = core_models.BusinessEntity(
        organization_id=org_id,
        entity_type=core_models.BusinessEntityType.PROJECT,
        name="Orion",
    )
    db_session.add(entity)
    db_session.flush()

    knowledge = core_models.KnowledgeItem(
        organization_id=org_id,
        business_entity_id=entity.id,
        summary="Orion knowledge",
        evidence_text="evidence text",
        knowledge_type="FACT",
        status=core_models.SuggestionStatus.CONFIRMED,
    )
    db_session.add(knowledge)
    db_session.flush()

    # Action linked directly via knowledge_item_id.
    action_direct = core_models.ActionItem(
        organization_id=org_id,
        knowledge_item_id=knowledge.id,
        title="Follow up on Orion",
        status=core_models.ActionStatus.OPEN,
    )
    # Action linked via shared business entity.
    action_entity = core_models.ActionItem(
        organization_id=org_id,
        business_entity_id=entity.id,
        title="Review Orion budget",
        status=core_models.ActionStatus.OPEN,
    )
    decision = core_models.DecisionRecord(
        organization_id=org_id,
        business_entity_id=entity.id,
        title="Proceed with Orion",
        decision="Approved",
        rationale="On track",
        evidence_text="decision evidence",
        decided_by=seeded_user["user"].id,
    )
    db_session.add_all([action_direct, action_entity, decision])
    db_session.flush()

    resp = client.get(f"/api/knowledge/{knowledge.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["knowledge_item"]["evidence_text"] == "evidence text"
    action_ids = {a["id"] for a in body["linked_actions"]}
    assert action_ids == {str(action_direct.id), str(action_entity.id)}
    assert [d["id"] for d in body["linked_decisions"]] == [str(decision.id)]


# ---------------------------------------------------------------------------
# Auth / cross-organization
# ---------------------------------------------------------------------------


def test_knowledge_list_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/knowledge").status_code == 401


def test_cross_org_knowledge_access_returns_404(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    knowledge = _extract_knowledge(client, db_session)

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

    assert client.get(f"/api/knowledge/{knowledge['id']}").status_code == 404
    assert (
        client.post(f"/api/knowledge/{knowledge['id']}/confirm").status_code == 404
    )
    assert (
        client.post(f"/api/knowledge/{knowledge['id']}/reject").status_code == 404
    )
    assert client.get("/api/knowledge").json() == []
