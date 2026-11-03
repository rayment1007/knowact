"""Personal workspace predicates shared by Core and connected-workspace paths.

Legacy rows with an unknown workspace owner deliberately match nobody. The
request's user id must come from authentication, never from a client payload.
"""

from uuid import UUID

from sqlalchemy import and_

from app.core.models import (
    ActionItem, AuditLog, Brief, BusinessEntity, DecisionRecord, KnowledgeItem,
    SourceItem,
)


def owner_predicate(model, user_id: UUID):
    if user_id is None:
        raise ValueError("A personal workspace owner is required.")
    if model in (BusinessEntity, KnowledgeItem, ActionItem):
        column = model.workspace_owner_id
    elif model is SourceItem:
        column = model.created_by
    elif model is DecisionRecord:
        column = model.decided_by
    elif model is Brief:
        column = model.generated_for
    elif model is AuditLog:
        column = model.actor_id
    else:
        raise ValueError(f"No personal ownership policy for {model.__name__}.")
    return column == user_id


def scope_owned_select(statement, model, org_id: UUID, user_id: UUID):
    return statement.where(and_(model.organization_id == org_id, owner_predicate(model, user_id)))


class PersonalScope:
    """Service scoping; callers must provide the authenticated workspace owner."""

    owner_id: UUID

    def _scope(self, statement, model, org_id: UUID):
        return scope_owned_select(statement, model, org_id, self.owner_id)

    def _require_actor(self, actor_id: UUID):
        if actor_id != self.owner_id:
            raise ValueError("Actor must be the authenticated workspace owner.")

    def _require_entity(self, org_id: UUID, entity_id: UUID | None):
        if entity_id is not None:
            from sqlalchemy import select
            from app.dependencies import not_found
            if self.db.scalar(self._scope(select(BusinessEntity), BusinessEntity, org_id)
                              .where(BusinessEntity.id == entity_id)) is None:
                raise not_found("Project / entity not found.")
