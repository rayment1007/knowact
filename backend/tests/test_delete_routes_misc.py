"""Delete-endpoint tests for the remaining core resources (delete feature).

Covers the audit-row-written and org-isolation (cross-tenant → 404) guarantees,
plus the simple cascades, for knowledge (detaches referencing actions), action,
and decision.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import models as core_models
from app.security import hash_password


def _login(client: TestClient, email: str, password: str) -> Any:
    return client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )


def _second_org_login(client: TestClient, db_session: Session) -> None:
    org = core_models.Organization(name="Other Org")
    db_session.add(org)
    db_session.flush()
    user = core_models.User(
        organization_id=org.id,
        email="other-misc@example.com",
        full_name="Other",
        password_hash=hash_password("pw2"),
        role="ADMIN",
    )
    db_session.add(user)
    db_session.flush()
    client.cookies.clear()
    assert _login(client, "other-misc@example.com", "pw2").status_code == 200


def _audit_count(db_session: Session, action_type: str) -> int:
    return (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == action_type)
        .count()
    )


# ---------------------------------------------------------------------------
# Knowledge — deleting detaches (NULLs) referencing actions.
# ---------------------------------------------------------------------------


def test_delete_knowledge_detaches_actions_and_audits(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    org_id = seeded_user["organization"].id

    knowledge = core_models.KnowledgeItem(
        organization_id=org_id,
        summary="Fact",
        key_points=[],
        evidence_text="e",
        knowledge_type="FACT",
        status=core_models.SuggestionStatus.CONFIRMED,
    )
    db_session.add(knowledge)
    db_session.flush()
    action = core_models.ActionItem(
        organization_id=org_id,
        knowledge_item_id=knowledge.id,
        title="Do it",
        status=core_models.ActionStatus.OPEN,
    )
    db_session.add(action)
    db_session.flush()
    knowledge_id, action_id = knowledge.id, action.id

    assert client.delete(f"/api/knowledge/{knowledge_id}").status_code == 204

    db_session.expire_all()
    assert db_session.get(core_models.KnowledgeItem, knowledge_id) is None
    kept = db_session.get(core_models.ActionItem, action_id)
    assert kept is not None and kept.knowledge_item_id is None
    assert _audit_count(db_session, "DELETE_KNOWLEDGE") == 1


def test_cross_org_knowledge_delete_404(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    knowledge = core_models.KnowledgeItem(
        organization_id=seeded_user["organization"].id,
        summary="Fact",
        key_points=[],
        evidence_text="e",
        knowledge_type="FACT",
        status=core_models.SuggestionStatus.SUGGESTED,
    )
    db_session.add(knowledge)
    db_session.flush()
    kid = knowledge.id

    _second_org_login(client, db_session)
    assert client.delete(f"/api/knowledge/{kid}").status_code == 404
    assert db_session.get(core_models.KnowledgeItem, kid) is not None


# ---------------------------------------------------------------------------
# Action / Decision — simple delete + audit.
# ---------------------------------------------------------------------------


def test_delete_action_and_decision_audit(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    org_id = seeded_user["organization"].id
    user_id = seeded_user["user"].id

    action = core_models.ActionItem(
        organization_id=org_id, title="A", status=core_models.ActionStatus.OPEN
    )
    decision = core_models.DecisionRecord(
        organization_id=org_id,
        title="D",
        decision="Go",
        rationale="because",
        evidence_text="e",
        decided_by=user_id,
        decided_at=datetime.now(timezone.utc),
    )
    db_session.add_all([action, decision])
    db_session.flush()
    action_id, decision_id = action.id, decision.id

    assert client.delete(f"/api/actions/{action_id}").status_code == 204
    assert client.delete(f"/api/decisions/{decision_id}").status_code == 204

    db_session.expire_all()
    assert db_session.get(core_models.ActionItem, action_id) is None
    assert db_session.get(core_models.DecisionRecord, decision_id) is None
    assert _audit_count(db_session, "DELETE_ACTION") == 1
    assert _audit_count(db_session, "DELETE_DECISION") == 1


def test_cross_org_action_and_decision_delete_404(
    client: TestClient, seeded_user: dict[str, Any], db_session: Session
) -> None:
    _login(client, seeded_user["email"], seeded_user["password"])
    org_id = seeded_user["organization"].id
    user_id = seeded_user["user"].id

    action = core_models.ActionItem(
        organization_id=org_id, title="A", status=core_models.ActionStatus.OPEN
    )
    decision = core_models.DecisionRecord(
        organization_id=org_id,
        title="D",
        decision="Go",
        rationale="because",
        evidence_text="e",
        decided_by=user_id,
        decided_at=datetime.now(timezone.utc),
    )
    db_session.add_all([action, decision])
    db_session.flush()
    action_id, decision_id = action.id, decision.id

    _second_org_login(client, db_session)
    assert client.delete(f"/api/actions/{action_id}").status_code == 404
    assert client.delete(f"/api/decisions/{decision_id}").status_code == 404

    db_session.expire_all()
    assert db_session.get(core_models.ActionItem, action_id) is not None
    assert db_session.get(core_models.DecisionRecord, decision_id) is not None
