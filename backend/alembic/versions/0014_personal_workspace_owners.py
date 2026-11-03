"""Add personal owners; quarantine legacy rows with ambiguous provenance.

This migration never uses ActionItem.owner_id (assignee) or a sole member of an
organization as ownership evidence. No source content is deleted. A NULL owner
means inaccessible pending an explicit, separately reviewed recovery mapping.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_personal_workspace_owners"
down_revision = "0013_sync_preferences"
branch_labels = None
depends_on = None


def backfill_sql(table, candidates):
    # Keep NULL and cross-organization candidate provenance in the aggregate:
    # filtering it out would incorrectly turn an inconsistent history into a
    # unanimous ownership claim. COUNT(DISTINCT) alone ignores NULL.
    return f"""
    WITH candidates AS ({candidates}), resolved AS (
      SELECT c.id, MIN(c.owner_id::text)::uuid AS owner_id
      FROM candidates c
      JOIN {table} target ON target.id = c.id
      LEFT JOIN users u ON u.id = c.owner_id
      GROUP BY c.id
      HAVING COUNT(DISTINCT c.owner_id) = 1
         AND BOOL_AND(c.owner_id IS NOT NULL
           AND c.origin_org IS NOT NULL
           AND c.origin_org = target.organization_id
           AND COALESCE(u.organization_id = target.organization_id, FALSE))
    )
    UPDATE {table} target SET workspace_owner_id = r.owner_id
    FROM resolved r WHERE target.id = r.id AND target.workspace_owner_id IS NULL
    """


KNOWLEDGE_CANDIDATES = """SELECT k.id, s.created_by AS owner_id, s.organization_id AS origin_org
FROM knowledge_items k LEFT JOIN source_items s ON s.id = k.source_item_id
WHERE k.source_item_id IS NOT NULL
UNION ALL
SELECT p.result_id, p.user_id, p.organization_id FROM source_proposals p
WHERE p.target = 'knowledge' AND p.status = 'APPROVED' AND p.result_id IS NOT NULL
UNION ALL
SELECT a.target_id, a.actor_id, a.organization_id FROM audit_logs a
WHERE a.target_type = 'KnowledgeItem' AND a.action_type = 'CONFIRM_KNOWLEDGE'
"""

ACTION_CANDIDATES = """SELECT a.target_id AS id, a.actor_id AS owner_id, a.organization_id AS origin_org
FROM audit_logs a WHERE a.target_type = 'ActionItem' AND a.action_type = 'CREATE_ACTION'
UNION ALL
SELECT p.result_id, p.user_id, p.organization_id FROM source_proposals p
WHERE p.target = 'action' AND p.status = 'APPROVED' AND p.result_id IS NOT NULL
UNION ALL
SELECT a.id, k.workspace_owner_id, k.organization_id
FROM action_items a LEFT JOIN knowledge_items k ON k.id = a.knowledge_item_id
WHERE a.knowledge_item_id IS NOT NULL
UNION ALL
SELECT task.id, log.actor_id, log.organization_id
FROM action_items task JOIN audit_logs log
  ON task.id::text = log.detail->>'action_item_id'
WHERE log.target_type = 'EmailTaskSuggestion' AND log.action_type = 'CONFIRM_EMAIL_TASK'
"""

ENTITY_CANDIDATES = """SELECT a.target_id AS id, a.actor_id AS owner_id, a.organization_id AS origin_org
FROM audit_logs a WHERE a.target_type = 'BusinessEntity' AND a.action_type = 'CREATE_BUSINESS_ENTITY'
UNION ALL
SELECT k.business_entity_id, k.workspace_owner_id, k.organization_id
FROM knowledge_items k WHERE k.business_entity_id IS NOT NULL
UNION ALL
SELECT a.business_entity_id, a.workspace_owner_id, a.organization_id
FROM action_items a WHERE a.business_entity_id IS NOT NULL
UNION ALL
SELECT d.business_entity_id, d.decided_by, d.organization_id
FROM decision_records d WHERE d.business_entity_id IS NOT NULL
UNION ALL
SELECT e.business_entity_id, e.user_id, e.organization_id
FROM email_drafts e WHERE e.business_entity_id IS NOT NULL
UNION ALL
SELECT e.business_entity_id, c.user_id, c.organization_id
FROM email_task_suggestions e
LEFT JOIN email_message_records m ON m.id = e.email_message_record_id
LEFT JOIN integration_connections c ON c.id = m.integration_connection_id
WHERE e.business_entity_id IS NOT NULL
"""


def upgrade():
    for table in ("business_entities", "knowledge_items", "action_items"):
        op.add_column(table, sa.Column("workspace_owner_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(f"fk_{table}_workspace_owner", table, "users", ["workspace_owner_id"], ["id"])
        op.create_index(f"ix_{table}_workspace_owner_id", table, ["workspace_owner_id"])
    for table, candidates in (("knowledge_items", KNOWLEDGE_CANDIDATES),
                              ("action_items", ACTION_CANDIDATES),
                              ("business_entities", ENTITY_CANDIDATES)):
        op.execute(sa.text(backfill_sql(table, candidates)))


def downgrade():
    # Downgrade removes the access boundary, so do this only with application
    # traffic stopped and a compatible previous application release restored.
    for table in ("action_items", "knowledge_items", "business_entities"):
        op.drop_index(f"ix_{table}_workspace_owner_id", table_name=table)
        op.drop_constraint(f"fk_{table}_workspace_owner", table, type_="foreignkey")
        op.drop_column(table, "workspace_owner_id")
