from app.core.fact_inventory import legacy_inventory
from app.core.models import ActionItem, BusinessEntity, KnowledgeItem, SourceItem


def test_inventory_counts_ownership_and_provenance_without_converting(db_session, seeded_user):
    user = seeded_user["user"]
    org = user.organization_id
    source = SourceItem(organization_id=org, created_by=user.id, source_type="MANUAL", title="Do not export", content="Secret source content")
    db_session.add(source); db_session.flush()
    knowledge = KnowledgeItem(organization_id=org, workspace_owner_id=user.id,
        source_item_id=source.id, summary="Do not export this either", evidence_text="Evidence", knowledge_type="CONTEXT")
    db_session.add_all([knowledge,
        KnowledgeItem(organization_id=org, summary="Unknown owner", evidence_text="Evidence", knowledge_type="FACT"),
        BusinessEntity(organization_id=org, name="Unknown owner", entity_type="PROJECT"),
        ActionItem(organization_id=org, workspace_owner_id=user.id, title="Manual task")])
    db_session.flush()
    report = legacy_inventory(db_session)
    assert report["legacy"]["knowledge_items"] == {
        "total": 2, "owned": 1, "unresolved_owner": 1,
        "owned_with_direct_provenance": 1,
        "owned_with_approved_source_proposal": 0,
        "owned_without_either_link": 0,
    }
    assert report["legacy"]["action_items"]["owned_without_either_link"] == 1
    assert report["legacy"]["business_entities"]["unresolved_owner"] == 1
    assert report["automatic_fact_conversion"] is False
    assert "Secret" not in str(report) and "Do not export" not in str(report)
    assert not db_session.new and not db_session.dirty and not db_session.deleted
