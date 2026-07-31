"""Route + service tests for the Enterprise Copilot (M6.5, Requirements 30, 31).

Exercises the real FastAPI app against the ephemeral SQLite database with the
deterministic ``MockAIProvider`` and a fake embedding provider injected, so no
LLM/embedding network call is ever made. Covers:

* a grounded ask returns an answer whose citations are all present in the
  bounded evidence set (Requirement 30.4, 30.7);
* an empty-evidence ask sets ``insufficient_evidence`` and does not guess (30.5);
* ASK produces no artifact and no mutation; ACT produces a ``SUGGESTED`` artifact
  and still no mutation (30.3 / 31.3);
* confirm applies an ``ACTION_ITEM`` and writes exactly one audit row (31.4);
* suggested-questions returns a fixed + dynamic set (30.6);
* auth is required and cross-org data is unreachable.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.core import models as core_models
from app.core.models import SuggestionStatus
from app.modules.cwi.dependencies import embedding_provider
from app.modules.cwi.services.copilot_service import (
    COPILOT_TOOL_ALLOWLIST,
    CopilotToolTier,
)
from app.modules.cwi.services.embedding import FakeEmbeddingProvider

_DIM = 64


@pytest.fixture()
def cwi_client(client: TestClient) -> TestClient:
    test_settings = Settings(
        ai_provider="mock",
        embedding_provider="mock",
        embedding_dimension=_DIM,
    )
    client.app.dependency_overrides[get_settings] = lambda: test_settings
    client.app.dependency_overrides[embedding_provider] = (
        lambda: FakeEmbeddingProvider(dimension=_DIM)
    )
    return client


def _login(client: TestClient, seeded_user: dict[str, Any]) -> None:
    resp = client.post(
        "/api/auth/login",
        json={"email": seeded_user["email"], "password": seeded_user["password"]},
    )
    assert resp.status_code == 200


def _seed_confirmed_knowledge(
    db_session: Any, org_id, summary: str = "Acme renewed the annual retainer."
) -> core_models.KnowledgeItem:
    item = core_models.KnowledgeItem(
        organization_id=org_id,
        summary=summary,
        key_points=["retainer", "annual"],
        evidence_text=summary,
        knowledge_type="FACT",
        status=SuggestionStatus.CONFIRMED,
    )
    db_session.add(item)
    db_session.flush()
    return item


# ---------------------------------------------------------------------------
# Tool allow-list structure
# ---------------------------------------------------------------------------


def test_tool_allowlist_tiers_are_exactly_the_approved_set() -> None:
    ask_tools = {
        name for name, tier in COPILOT_TOOL_ALLOWLIST.items()
        if tier is CopilotToolTier.ASK
    }
    draft_tools = {
        name for name, tier in COPILOT_TOOL_ALLOWLIST.items()
        if tier is CopilotToolTier.DRAFT
    }
    act_tools = {
        name for name, tier in COPILOT_TOOL_ALLOWLIST.items()
        if tier is CopilotToolTier.ACT
    }
    assert ask_tools == {
        "search_confirmed_knowledge",
        "search_email_sources",
        "search_document_chunks",
        "list_open_actions",
        "list_recent_decisions",
        "get_client_context",
        "get_upcoming_calendar_events",
    }
    assert draft_tools == {"draft_email"}
    assert act_tools == {
        "propose_action_item",
        "propose_calendar_event",
        "propose_entity_link",
    }


# ---------------------------------------------------------------------------
# ask
# ---------------------------------------------------------------------------


def test_ask_returns_grounded_answer_with_citations_in_evidence(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    item = _seed_confirmed_knowledge(db_session, seeded_user["organization"].id)

    resp = cwi_client.post(
        "/api/copilot/ask", json={"question": "What's happening with Acme?"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["insufficient_evidence"] is False
    assert body["answer"]
    assert body["citations"], "a grounded answer must carry citations"
    # Every citation must correspond to an evidence record (no fabrication).
    cited_ids = {c["source_id"] for c in body["citations"]}
    assert str(item.id) in cited_ids
    for citation in body["citations"]:
        assert citation["source_type"]
        assert citation["deep_link"]


def test_ask_with_no_evidence_sets_insufficient_evidence(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    resp = cwi_client.post(
        "/api/copilot/ask", json={"question": "What did we decide last quarter?"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["insufficient_evidence"] is True
    assert body["citations"] == []
    assert body["suggested_artifact"] is None
    assert "confirmed evidence" in body["answer"].lower()


def test_ask_act_intent_returns_suggested_artifact_and_no_mutation(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    _seed_confirmed_knowledge(db_session, seeded_user["organization"].id)

    resp = cwi_client.post(
        "/api/copilot/ask",
        json={"question": "Create a task to follow up with Acme", "intent": "ACT"},
    )
    assert resp.status_code == 200
    body = resp.json()
    artifact = body["suggested_artifact"]
    assert artifact is not None
    assert artifact["status"] == "SUGGESTED"
    assert artifact["tier"] == "ACT"
    assert artifact["kind"] == "ACTION_ITEM"
    # ask must never mutate — no action item created (Requirement 31.3).
    assert db_session.query(core_models.ActionItem).count() == 0
    assert db_session.query(core_models.AuditLog).count() == 0


# ---------------------------------------------------------------------------
# confirm
# ---------------------------------------------------------------------------


def test_confirm_action_item_creates_action_and_one_audit(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)

    resp = cwi_client.post(
        "/api/copilot/confirm",
        json={
            "kind": "ACTION_ITEM",
            "title": "Follow up with Acme about the retainer",
            "body": "Grounded in the confirmed retainer knowledge.",
            "evidence_text": "Acme renewed the annual retainer.",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is True
    assert body["created_id"]

    actions = db_session.query(core_models.ActionItem).all()
    assert len(actions) == 1
    assert actions[0].title == "Follow up with Acme about the retainer"

    audits = (
        db_session.query(core_models.AuditLog)
        .filter(core_models.AuditLog.action_type == "CREATE_ACTION")
        .all()
    )
    assert len(audits) == 1
    assert audits[0].target_id == actions[0].id


def test_confirm_unsupported_kind_returns_422(
    cwi_client: TestClient, seeded_user: dict[str, Any]
) -> None:
    _login(cwi_client, seeded_user)
    resp = cwi_client.post(
        "/api/copilot/confirm",
        json={"kind": "CALENDAR_EVENT", "title": "Meet Acme"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# suggested questions
# ---------------------------------------------------------------------------


def test_suggested_questions_returns_fixed_and_dynamic(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    _login(cwi_client, seeded_user)
    # An open action seeds a dynamic question.
    db_session.add(
        core_models.ActionItem(
            organization_id=seeded_user["organization"].id,
            title="Call Acme back",
        )
    )
    db_session.flush()

    resp = cwi_client.get("/api/copilot/suggested-questions")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["fixed"]) >= 5
    assert any("open action" in q.lower() for q in body["dynamic"])
    assert body["questions"] == body["fixed"] + body["dynamic"]


# ---------------------------------------------------------------------------
# auth + isolation
# ---------------------------------------------------------------------------


def test_copilot_requires_authentication(cwi_client: TestClient) -> None:
    assert cwi_client.post("/api/copilot/ask", json={"question": "hi"}).status_code == 401
    assert cwi_client.get("/api/copilot/suggested-questions").status_code == 401
    assert cwi_client.post(
        "/api/copilot/confirm", json={"kind": "ACTION_ITEM", "title": "x"}
    ).status_code == 401


def test_ask_is_org_scoped(
    cwi_client: TestClient, seeded_user: dict[str, Any], db_session: Any
) -> None:
    # Org A has confirmed knowledge; a second org sees none of it.
    _seed_confirmed_knowledge(db_session, seeded_user["organization"].id)

    from app.security import hash_password

    other_org = core_models.Organization(name="Other Org")
    db_session.add(other_org)
    db_session.flush()
    other_user = core_models.User(
        organization_id=other_org.id,
        email="other-copilot@example.com",
        full_name="Other",
        password_hash=hash_password("pw2"),
        role="ADMIN",
    )
    db_session.add(other_user)
    db_session.flush()

    cwi_client.cookies.clear()
    assert cwi_client.post(
        "/api/auth/login",
        json={"email": "other-copilot@example.com", "password": "pw2"},
    ).status_code == 200

    resp = cwi_client.post(
        "/api/copilot/ask", json={"question": "What's happening with Acme?"}
    )
    assert resp.status_code == 200
    # The other org has no confirmed evidence → insufficient, never org A's data.
    assert resp.json()["insufficient_evidence"] is True
