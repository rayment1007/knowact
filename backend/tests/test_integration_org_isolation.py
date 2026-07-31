"""Org-isolation integration test (Task 24.3, Requirement 2.3).

Asserts that organization isolation holds end-to-end *over HTTP* — exercising
the route + dependency (auth/session + org-scoping) layer, not just the service
helpers covered by the P11 property test. A user authenticated for organization
A creates a representative row per Core Engine surface, then a user
authenticated for organization B is refused with ``404 Not Found`` (never
``403``, which would leak existence) for every one of those rows:

* a source item (``GET /api/source-items/{id}``),
* a knowledge item (``GET /api/knowledge/{id}``),
* a business entity (``GET /api/business-entities/{id}/brief``),
* an action item (``PATCH /api/actions/{id}``),
* a decision record (``DELETE /api/decisions/{id}``).

The list endpoints are also asserted empty for org B, and cross-tenant writes
(dismiss / confirm / update / delete) are refused with ``404`` — proving the
row's existence is never revealed to another tenant.

Seed data ships a single organization, so this test seeds a **second** fully
independent org + user directly on the test session (mirroring the cross-org
pattern in ``test_source_item_routes.py``) and logs in as that user over HTTP.

_Requirements: 2.3_
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import models as core_models
from app.security import hash_password

_OTHER_EMAIL = "org-b@example.com"
_OTHER_PASSWORD = "org-b-pass-123"


def _login(client: TestClient, email: str, password: str) -> Any:
    return client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )


def _seed_second_org(db: Session) -> core_models.User:
    """Create an independent organization B with its own user (org A is seeded_user)."""

    org_b = core_models.Organization(name="Organization B")
    db.add(org_b)
    db.flush()
    user_b = core_models.User(
        organization_id=org_b.id,
        email=_OTHER_EMAIL,
        full_name="Org B User",
        password_hash=hash_password(_OTHER_PASSWORD),
        role="ADMIN",
    )
    db.add(user_b)
    db.flush()
    return user_b


def test_org_b_receives_404_for_org_a_rows(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    """A user in org B gets 404 for every org A row across services (Req 2.3)."""

    org_a_id = seeded_user["organization"].id

    # -- As org A (seeded_user): create one row per surface ----------------
    _login(client, seeded_user["email"], seeded_user["password"])

    source_item = client.post(
        "/api/source-items",
        json={
            "source_type": "EMAIL",
            "title": "Org A private note",
            "content": "Confidential org A content.",
        },
    ).json()

    entity = client.post(
        "/api/business-entities",
        json={
            "entity_type": "PROJECT",
            "name": "Org A Project",
            "description": "Private to org A.",
        },
    ).json()

    action = client.post(
        "/api/actions",
        json={"title": "Org A action", "business_entity_id": entity["id"]},
    ).json()

    decision = client.post(
        "/api/decisions",
        json={
            "title": "Org A decision",
            "decision": "We will proceed.",
            "rationale": "Org A rationale.",
            "evidence_text": "Org A evidence.",
        },
    ).json()

    # A knowledge item seeded directly for org A (extraction requires a
    # confirmed classification; a direct insert keeps this test focused on the
    # isolation of the read/write routes).
    knowledge = core_models.KnowledgeItem(
        organization_id=org_a_id,
        summary="Org A knowledge",
        key_points=["secret"],
        evidence_text="org A evidence",
        knowledge_type="FACT",
        status=core_models.SuggestionStatus.SUGGESTED,
    )
    db_session.add(knowledge)
    db_session.flush()

    # -- Switch to org B ----------------------------------------------------
    _seed_second_org(db_session)
    client.cookies.clear()
    _login(client, _OTHER_EMAIL, _OTHER_PASSWORD)

    # Every org A row is invisible to org B: 404, never 403.
    assert (
        client.get(f"/api/source-items/{source_item['id']}").status_code == 404
    )
    assert client.get(f"/api/knowledge/{knowledge.id}").status_code == 404
    assert (
        client.get(f"/api/business-entities/{entity['id']}/brief").status_code
        == 404
    )

    # Cross-tenant writes are refused with 404 too (existence never revealed).
    assert (
        client.post(
            f"/api/source-items/{source_item['id']}/dismiss"
        ).status_code
        == 404
    )
    assert (
        client.post(f"/api/knowledge/{knowledge.id}/confirm").status_code == 404
    )
    assert (
        client.patch(
            f"/api/actions/{action['id']}", json={"status": "DONE"}
        ).status_code
        == 404
    )
    assert client.delete(f"/api/decisions/{decision['id']}").status_code == 404

    # And org B's list views contain none of org A's rows.
    assert client.get("/api/source-items").json() == []
    assert client.get("/api/knowledge").json() == []
    assert client.get("/api/actions").json() == []
    assert client.get("/api/decisions").json() == []
    assert client.get("/api/business-entities").json() == []

    # The org A rows are all still present (refusal, not deletion).
    db_session.expire_all()
    assert db_session.get(core_models.KnowledgeItem, knowledge.id) is not None
