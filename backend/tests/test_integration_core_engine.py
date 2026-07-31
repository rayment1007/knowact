"""End-to-end Core Engine integration test (Task 24.1, Requirement 21.1).

Exercises the whole Core Engine pipeline over HTTP against the real FastAPI app
(ephemeral SQLite, see ``conftest.py``) using the cookie-based auth session — no
service is called directly. The single happy-path flow is:

    login → collect (POST source item) → classify → confirm classification →
    extract knowledge → confirm knowledge → create action

and it asserts the human-in-the-loop status transitions at each hop
(``SUGGESTED`` → ``CONFIRMED``; source item → ``CLASSIFIED``; action → ``OPEN``),
that confirmed knowledge carries non-empty ``evidence_text``, and that the three
expected :class:`~app.core.models.AuditLog` rows were written in the same
transactions as the state changes they record (``CONFIRM_CLASSIFICATION``,
``CONFIRM_KNOWLEDGE``, ``CREATE_ACTION``).

This is a deterministic integration test (not a property test): the classify
step's AI output is normalized by confirming with an explicit override
(``WORK_RELATED`` / ``PROJECT`` / ``INTERNAL``) so extraction always clears the
privacy and sensitivity gates regardless of the mock provider's heuristics.

_Requirements: 21.1_
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def _login(client: TestClient, email: str, password: str) -> Any:
    return client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )


def test_core_engine_pipeline_end_to_end(
    client: TestClient, seeded_user: dict[str, Any]
) -> None:
    """login → collect → classify → confirm → extract → confirm → act (Req 21.1)."""

    _login(client, seeded_user["email"], seeded_user["password"])
    actor_id = str(seeded_user["user"].id)

    # -- 1. Collect: a new source item is persisted in status NEW -----------
    collect = client.post(
        "/api/source-items",
        json={
            "source_type": "EMAIL",
            "title": "Project Orion milestone",
            "content": (
                "The Orion project milestone is on track for next sprint; the "
                "client asked us to prepare an estate planning proposal."
            ),
        },
    )
    assert collect.status_code == 201, collect.text
    item = collect.json()
    assert item["status"] == "NEW"
    item_id = item["id"]

    # -- 2. Classify: an AI classification is persisted as SUGGESTED --------
    classify = client.post(f"/api/source-items/{item_id}/classify")
    assert classify.status_code == 201, classify.text
    classification = classify.json()
    assert classification["status"] == "SUGGESTED"
    assert classification["source_item_id"] == item_id

    # -- 3. Confirm classification: SUGGESTED → CONFIRMED, item → CLASSIFIED -
    # An explicit override normalizes the axes so extraction clears the
    # privacy/sensitivity gates deterministically.
    confirm_cls = client.post(
        f"/api/source-items/{item_id}/confirm-classification",
        json={
            "relevance": "WORK_RELATED",
            "business_category": "PROJECT",
            "sensitivity": "INTERNAL",
        },
    )
    assert confirm_cls.status_code == 200, confirm_cls.text
    confirmed_classification = confirm_cls.json()
    assert confirmed_classification["status"] == "CONFIRMED"
    assert confirmed_classification["relevance"] == "WORK_RELATED"

    # The source item is now CLASSIFIED, and its detail view reflects the
    # confirmed classification.
    detail = client.get(f"/api/source-items/{item_id}")
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    assert detail_body["source_item"]["status"] == "CLASSIFIED"
    assert detail_body["classification"]["status"] == "CONFIRMED"

    # -- 4. Extract: a SUGGESTED knowledge item with evidence is created ----
    extract = client.post(f"/api/source-items/{item_id}/extract")
    assert extract.status_code == 201, extract.text
    knowledge = extract.json()
    assert knowledge["status"] == "SUGGESTED"
    assert knowledge["source_item_id"] == item_id
    assert knowledge["evidence_text"]  # non-empty on the suggestion
    knowledge_id = knowledge["id"]

    # -- 5. Confirm knowledge: SUGGESTED → CONFIRMED, evidence retained -----
    confirm_k = client.post(f"/api/knowledge/{knowledge_id}/confirm")
    assert confirm_k.status_code == 200, confirm_k.text
    confirmed_knowledge = confirm_k.json()
    assert confirmed_knowledge["status"] == "CONFIRMED"
    # evidence_text is present on the confirmed knowledge (Requirement 7.5).
    assert confirmed_knowledge["evidence_text"]

    # -- 6. Create action from the confirmed knowledge: OPEN, AI-generated --
    create_action = client.post(
        "/api/actions",
        json={
            "title": "Prepare estate planning proposal",
            "knowledge_item_id": knowledge_id,
        },
    )
    assert create_action.status_code == 201, create_action.text
    action = create_action.json()
    assert action["status"] == "OPEN"
    assert action["ai_generated"] is True
    assert action["knowledge_item_id"] == knowledge_id
    # Evidence flows through from the confirmed suggestion.
    assert action["evidence_text"]
    action_id = action["id"]

    # -- 7. Audit trail: exactly the three expected rows were written -------
    confirm_cls_audit = client.get(
        "/api/audit",
        params={"action_type": "CONFIRM_CLASSIFICATION", "target_id": item_id},
    ).json()
    assert len(confirm_cls_audit) == 1
    assert confirm_cls_audit[0]["target_type"] == "SourceItem"
    assert confirm_cls_audit[0]["actor_id"] == actor_id

    confirm_k_audit = client.get(
        "/api/audit",
        params={"action_type": "CONFIRM_KNOWLEDGE", "target_id": knowledge_id},
    ).json()
    assert len(confirm_k_audit) == 1
    assert confirm_k_audit[0]["target_type"] == "KnowledgeItem"
    assert confirm_k_audit[0]["actor_id"] == actor_id

    create_action_audit = client.get(
        "/api/audit",
        params={"action_type": "CREATE_ACTION", "target_id": action_id},
    ).json()
    assert len(create_action_audit) == 1
    assert create_action_audit[0]["target_type"] == "ActionItem"
    assert create_action_audit[0]["actor_id"] == actor_id
