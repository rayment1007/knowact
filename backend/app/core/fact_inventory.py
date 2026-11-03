"""Aggregate legacy ownership/provenance inventory; never converts knowledge.

Run from backend with an explicitly selected read-only database URL in
KNOWACT_INVENTORY_DATABASE_URL. No .env/database default is used by this CLI.
"""
import json
import os

from sqlalchemy import create_engine, exists, func, select, text
from sqlalchemy.orm import Session

from app.core.models import ActionItem, BusinessEntity, KnowledgeItem, SourceItem
from app.modules.cwi.models import SourceProposal


def legacy_inventory(db: Session) -> dict:
    """Return counts only: no identifiers, titles, source text or credentials."""
    result = {"schema_required": "0015_structured_fact_memory", "legacy": {},
              "automatic_fact_conversion": False}
    for model in (BusinessEntity, KnowledgeItem, ActionItem):
        base = select(func.count()).select_from(model)
        total = db.scalar(base)
        unresolved = db.scalar(base.where(model.workspace_owner_id.is_(None)))
        result["legacy"][model.__tablename__] = {
            "total": total, "owned": total - unresolved, "unresolved_owner": unresolved,
        }
    owned_source = exists(select(SourceItem.id).where(
        SourceItem.id == KnowledgeItem.source_item_id,
        SourceItem.organization_id == KnowledgeItem.organization_id,
        SourceItem.created_by == KnowledgeItem.workspace_owner_id,
    ))
    owned_knowledge = exists(select(KnowledgeItem.id).where(
        KnowledgeItem.id == ActionItem.knowledge_item_id,
        KnowledgeItem.organization_id == ActionItem.organization_id,
        KnowledgeItem.workspace_owner_id == ActionItem.workspace_owner_id,
    ))
    for model, target, direct in (
        (KnowledgeItem, "knowledge", owned_source),
        (ActionItem, "action", owned_knowledge),
    ):
        approved_proposal = exists(select(SourceProposal.id).where(
            SourceProposal.organization_id == model.organization_id,
            SourceProposal.user_id == model.workspace_owner_id,
            SourceProposal.result_id == model.id,
            SourceProposal.target == target,
            SourceProposal.status == "APPROVED",
        ))
        own = model.workspace_owner_id.is_not(None)
        result["legacy"][model.__tablename__].update({
            "owned_with_direct_provenance": db.scalar(select(func.count()).select_from(model).where(own, direct)),
            "owned_with_approved_source_proposal": db.scalar(select(func.count()).select_from(model).where(own, approved_proposal)),
            "owned_without_either_link": db.scalar(select(func.count()).select_from(model).where(own, ~direct, ~approved_proposal)),
        })
    return result


def main():
    database_url = os.environ.get("KNOWACT_INVENTORY_DATABASE_URL")
    if not database_url:
        raise SystemExit("Set KNOWACT_INVENTORY_DATABASE_URL explicitly; no default database is selected.")
    engine = create_engine(database_url, echo=False)
    try:
        with engine.connect() as connection:
            if connection.dialect.name != "postgresql":
                raise SystemExit("The inventory CLI requires PostgreSQL for an enforced read-only transaction.")
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                with Session(bind=connection) as session:
                    report = legacy_inventory(session)
                print(json.dumps(report, indent=2))
    except Exception:
        # Driver exceptions may contain host/user details; do not print them.
        raise SystemExit("Inventory failed; check database access and schema. No writes were requested.") from None
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
