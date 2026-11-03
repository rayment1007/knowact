"""Real PostgreSQL test of migration 0014; opt-in, disposable local DB only.

The minimal legacy tables cover every input to this migration. This does not
claim to test the entire historical migration chain or pgvector installation.
All DDL/data are rolled back, including the private test schema.
"""
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.fixture()
def migration_db():
    url = os.environ.get('KNOWACT_TEST_POSTGRES_URL')
    if not url:
        pytest.skip('Set KNOWACT_TEST_POSTGRES_URL for disposable local PostgreSQL')
    parsed = sa.engine.make_url(url)
    assert parsed.host == '127.0.0.1' and parsed.port == 55439
    assert parsed.username == 'knowact_test' and parsed.database == 'postgres'
    engine = sa.create_engine(url)
    with engine.connect() as db:
        transaction = db.begin()
        schema = 'phase1_' + uuid4().hex
        db.execute(sa.text(f'CREATE SCHEMA {schema}'))
        db.execute(sa.text(f'SET LOCAL search_path TO {schema}'))
        tables = {
            'users': 'id uuid PRIMARY KEY, organization_id uuid NOT NULL',
            'source_items': 'id uuid PRIMARY KEY, organization_id uuid NOT NULL, created_by uuid',
            'knowledge_items': 'id uuid PRIMARY KEY, organization_id uuid NOT NULL, source_item_id uuid, business_entity_id uuid',
            'action_items': 'id uuid PRIMARY KEY, organization_id uuid NOT NULL, owner_id uuid, knowledge_item_id uuid, business_entity_id uuid',
            'business_entities': 'id uuid PRIMARY KEY, organization_id uuid NOT NULL',
            'audit_logs': 'id uuid PRIMARY KEY, organization_id uuid NOT NULL, actor_id uuid, target_id uuid, target_type text, action_type text, detail jsonb',
            'source_proposals': 'id uuid PRIMARY KEY, organization_id uuid NOT NULL, user_id uuid, result_id uuid, target text, status text',
            'decision_records': 'id uuid PRIMARY KEY, organization_id uuid NOT NULL, business_entity_id uuid, decided_by uuid',
            'email_drafts': 'id uuid PRIMARY KEY, organization_id uuid NOT NULL, business_entity_id uuid, user_id uuid',
            'email_task_suggestions': 'id uuid PRIMARY KEY, business_entity_id uuid, email_message_record_id uuid',
            'email_message_records': 'id uuid PRIMARY KEY, integration_connection_id uuid',
            'integration_connections': 'id uuid PRIMARY KEY, organization_id uuid NOT NULL, user_id uuid',
        }
        for name, columns in tables.items():
            db.execute(sa.text(f'CREATE TABLE {name} ({columns})'))
        path = Path(__file__).resolve().parents[1] / 'alembic/versions/0014_personal_workspace_owners.py'
        spec = importlib.util.spec_from_file_location('owners_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.op = Operations(MigrationContext.configure(db))
        try:
            yield db, migration
        finally:
            transaction.rollback()
    engine.dispose()


def insert(db, table, **values):
    values.setdefault('id', uuid4())
    columns = ', '.join(values)
    binds = ', '.join(':' + key for key in values)
    db.execute(sa.text(f'INSERT INTO {table} ({columns}) VALUES ({binds})'), values)
    return values['id']


def test_backfill_unanimous_provenance_quarantine_and_roundtrip(migration_db):
    db, migration = migration_db
    org, elsewhere = uuid4(), uuid4()
    a = insert(db, 'users', organization_id=org)
    b = insert(db, 'users', organization_id=org)
    outsider = insert(db, 'users', organization_id=elsewhere)
    source = insert(db, 'source_items', organization_id=org, created_by=a)
    project = insert(db, 'business_entities', organization_id=org)
    shared_project = insert(db, 'business_entities', organization_id=org)
    owned = insert(db, 'knowledge_items', organization_id=org, source_item_id=source, business_entity_id=project)
    conflict = insert(db, 'knowledge_items', organization_id=org, source_item_id=source, business_entity_id=shared_project)
    foreign = insert(db, 'knowledge_items', organization_id=org)
    orphan = insert(db, 'knowledge_items', organization_id=org, source_item_id=uuid4())
    unknown = insert(db, 'knowledge_items', organization_id=org)
    draft_only = insert(db, 'knowledge_items', organization_id=org)
    for row, person in [(owned, a), (conflict, b), (foreign, outsider), (orphan, a)]:
        insert(db, 'source_proposals', organization_id=org, user_id=person,
               result_id=row, target='knowledge', status='APPROVED')
    insert(db, 'source_proposals', organization_id=org, user_id=a,
           result_id=draft_only, target='knowledge', status='SUGGESTED')
    derived = insert(db, 'action_items', organization_id=org, knowledge_item_id=owned)
    assignee_only = insert(db, 'action_items', organization_id=org, owner_id=a)
    bad_derived = insert(db, 'action_items', organization_id=org, knowledge_item_id=unknown)
    manual = insert(db, 'action_items', organization_id=org)
    mail_task = insert(db, 'action_items', organization_id=org)
    for row in [manual, bad_derived]:
        insert(db, 'audit_logs', organization_id=org, actor_id=a, target_id=row,
               target_type='ActionItem', action_type='CREATE_ACTION')
    insert(db, 'audit_logs', organization_id=org, actor_id=a, target_id=uuid4(),
           target_type='EmailTaskSuggestion', action_type='CONFIRM_EMAIL_TASK',
           detail='{"action_item_id":"' + str(mail_task) + '"}')
    insert(db, 'decision_records', organization_id=org, decided_by=b, business_entity_id=shared_project)
    before = {name: db.scalar(sa.text(f'SELECT count(*) FROM {name}'))
              for name in ['knowledge_items', 'action_items', 'business_entities', 'source_items']}
    migration.upgrade()
    def owners(table):
        return dict(db.execute(sa.text(f'SELECT id, workspace_owner_id FROM {table}')).all())
    assert owners('knowledge_items') == {owned: a, conflict: None, foreign: None, orphan: None, unknown: None, draft_only: None}
    assert owners('action_items') == {derived: a, assignee_only: None, bad_derived: None, manual: a, mail_task: a}
    assert owners('business_entities') == {project: a, shared_project: None}
    for name, count in before.items():
        assert db.scalar(sa.text(f'SELECT count(*) FROM {name}')) == count
    # Re-running recovery SQL never overwrites an already assigned owner.
    db.execute(sa.text(migration.backfill_sql('knowledge_items', migration.KNOWLEDGE_CANDIDATES)))
    assert owners('knowledge_items')[owned] == a
    migration.downgrade()
    assert 'workspace_owner_id' not in {col['name'] for col in sa.inspect(db).get_columns('knowledge_items')}
    migration.upgrade()
    assert owners('action_items')[mail_task] == a
