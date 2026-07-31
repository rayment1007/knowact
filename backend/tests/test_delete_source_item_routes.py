"""Route/service tests for permanent source item deletion (delete feature).

Exercises ``DELETE /api/source-items/{id}`` against the ephemeral SQLite
database with cookie auth. Covers the two hardest guarantees for this resource:

* **null-FK cascade correctness** — deleting a source item deletes its
  ``ClassificationResult`` rows but only NULLs ``source_item_id`` on the derived
  ``KnowledgeItem`` / ``ActionItem`` rows (which are retained);
* **audit-row-written** — exactly one ``DELETE_SOURCE_ITEM`` audit row;
* **org-isolation** — a cross-tenant delete yields ``404`` and deletes nothing.
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


def _payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "source_type": "EMAIL",
        "title": "Quarterly budget review",
        "content": "Please review the attached Q3 budget before Friday.",
    }
    payload.update(overrides)
    return payload


def _second_org_user(db_session: Session) -> dict[str, Any]:
    org = core_models.Organization(name="Other Org")
    db_session.add(org)
    db_session.flush()
    user = core_models.User(
        organization_id=org.id,
        email="other@example.com",
        full_name="Other",
        password_hash=hash_password("pw2"),
        role="ADMIN",
    )
    db_session.add(user)
    db_session.flush()
    return {"organization": org, "user": user, "email": user.email, "password": "pw2"}


def test_delete_removes_item_and_classification_nulls_derived_fks(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    org_id = seeded_user["organization"].id

    item = client.post("/api/source-items", json=_payload()).json()
    item_id = UUID(item["id"])

    # A classification for the item (deleted on cascade).
    classification = core_models.ClassificationResult(
        source_item_id=item_id,
        relevance=core_models.Relevance.WORK_RELATED,
        business_category=core_models.BusinessCategory.CLIENT,
        sensitivity=core_models.Sensitivity.INTERNAL,
        confidence=0.9,
        status=core_models.SuggestionStatus.CONFIRMED,
    )
    # Derived knowledge referencing the source item (retained, nulled).
    knowledge = core_models.KnowledgeItem(
        organization_id=org_id,
        source_item_id=item_id,
        summary="A confirmed fact.",
        key_points=["k"],
        evidence_text="evidence",
        knowledge_type="FACT",
        status=core_models.SuggestionStatus.CONFIRMED,
    )
    db_session.add_all([classification, knowledge])
    db_session.flush()
    knowledge_id = knowledge.id

    resp = client.delete(f"/api/source-items/{item['id']}")
    assert resp.status_code == 204

    # Source item gone; its classification gone.
    assert db_session.get(core_models.SourceItem, item_id) is None
    assert (
        db_session.query(core_models.ClassificationResult)
        .filter_by(source_item_id=item_id)
        .count()
        == 0
    )

    # Derived knowledge retained, with source_item_id NULLed.
    db_session.expire_all()
    kept_k = db_session.get(core_models.KnowledgeItem, knowledge_id)
    assert kept_k is not None and kept_k.source_item_id is None

    # Exactly one DELETE_SOURCE_ITEM audit row for the item.
    audits = (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == "DELETE_SOURCE_ITEM")
        .all()
    )
    assert len(audits) == 1
    assert audits[0].target_id == item_id
    assert audits[0].organization_id == org_id


def test_delete_requires_authentication(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    item = client.post("/api/source-items", json=_payload()).json()
    client.cookies.clear()
    assert client.delete(f"/api/source-items/{item['id']}").status_code == 401


def test_cross_org_delete_returns_404_and_persists_item(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    item = client.post("/api/source-items", json=_payload()).json()
    item_id = UUID(item["id"])

    other = _second_org_user(db_session)
    client.cookies.clear()
    assert _login(client, other["email"], other["password"]).status_code == 200

    assert client.delete(f"/api/source-items/{item['id']}").status_code == 404
    # The item still exists — a cross-tenant delete revealed/changed nothing.
    assert db_session.get(core_models.SourceItem, item_id) is not None
