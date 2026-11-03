"""Personal Core data must not leak through connected-workspace consumers."""
from datetime import date

import pytest

from app.core.models import ActionItem, AuditLog, BusinessEntity, DecisionRecord, KnowledgeItem, SourceItem, User
from app.modules.cwi.services.copilot_service import CopilotService
from app.core.services.ai_provider import MockAIProvider
from app.modules.cwi.services.embedding import FakeEmbeddingProvider
from tests.test_cwi_calendar_routes import cwi_client, fake_calendar, _connect_calendar, _login


@pytest.fixture()
def private_records(db_session, seeded_user):
    user = seeded_user['user']
    other = User(organization_id=user.organization_id, email='phase1-private@example.com',
        full_name='Other', password_hash='unused', role='MEMBER')
    db_session.add(other); db_session.flush()
    source = SourceItem(organization_id=user.organization_id, created_by=user.id,
        source_type='MANUAL', title='Own source', content='Original')
    db_session.add(source); db_session.flush()
    records = {}
    for label, owner in [('Own', user.id), ('Private', other.id), ('Unresolved', None)]:
        entity = BusinessEntity(organization_id=user.organization_id, workspace_owner_id=owner,
            entity_type='CLIENT', name=label + ' marker client', description=label + ' marker details')
        db_session.add(entity); db_session.flush()
        knowledge = KnowledgeItem(organization_id=user.organization_id, workspace_owner_id=owner,
            business_entity_id=entity.id, source_item_id=source.id, summary=label + ' marker fact',
            knowledge_type='FACT', evidence_text=label + ' marker evidence', status='CONFIRMED')
        action = ActionItem(organization_id=user.organization_id, workspace_owner_id=owner,
            owner_id=user.id, business_entity_id=entity.id, title=label + ' marker task', due_date=date(2020, 1, 1))
        # An assignee equal to the caller must not override the workspace owner.
        db_session.add_all([knowledge, action]); db_session.flush()
        records[label] = (entity, knowledge, action)
    for actor, label in [(user.id, 'Own'), (other.id, 'Private')]:
        db_session.add(DecisionRecord(organization_id=user.organization_id, decided_by=actor,
            title=label + ' marker decision', decision=label, rationale='Rationale', evidence_text='Evidence'))
    db_session.flush()
    return user, other, source, records


def test_workspace_lists_counts_search_provenance_and_activity_are_personal(client, seeded_user, db_session, private_records):
    user, other, source, records = private_records
    _login(client, seeded_user)
    for section in ['knowledge', 'actions']:
        result = client.get('/api/workspace/items', params={'section': section})
        assert result.status_code == 200, result.text
        assert result.json()['total'] == 1
        assert result.json()['items'][0]['title'].startswith('Own')
    summary = client.get('/api/workspace/summary').json()
    assert summary['knowledge'] == summary['open_actions'] == 1
    search = client.get('/api/workspace/search', params={'q': 'marker'}).json()
    assert search['total'] == 2
    assert all(item['title'].startswith('Own') for item in search['items'])
    linked = client.get(f'/api/workspace/source/source/{source.id}/links').json()['items']
    assert [item['id'] for item in linked] == [str(records['Own'][1].id)]
    for label in ['Private', 'Unresolved']:
        _, knowledge, action = records[label]
        assert client.get(f'/api/workspace/knowledge/{knowledge.id}/origin').status_code == 404
        assert client.get(f'/api/workspace/actions/{action.id}/context').status_code == 404
        # Even a historical/corrupt audit with this actor must not resolve a foreign target title.
        db_session.add(AuditLog(organization_id=user.organization_id, actor_id=user.id,
            action_type='CREATE_ACTION', target_type='ActionItem', target_id=action.id))
    db_session.flush()
    activity = client.get('/api/workspace/activity').json()['items']
    assert all(item['path'] is None and 'marker' not in item['title'] for item in activity)


def test_copilot_all_core_contexts_and_daily_counts_are_personal(db_session, private_records):
    user, other, _, records = private_records
    service = CopilotService(db_session, MockAIProvider(), FakeEmbeddingProvider(dimension=64))
    evidence = service._gather_evidence(user.organization_id, user.id, 'marker', None)
    assert len(evidence) == 4  # own knowledge, action, decision, client
    assert all(item.title.startswith('Own') for item in evidence)
    questions = service.suggested_questions(user.organization_id, user.id)
    assert any('1 open action' in item for item in questions.dynamic)
    assert all('Private' not in item and 'Unresolved' not in item for item in questions.dynamic)


def test_calendar_cannot_schedule_another_users_or_unresolved_action(cwi_client, fake_calendar, seeded_user, private_records):
    _login(cwi_client, seeded_user)
    connection_id = _connect_calendar(cwi_client)
    _, _, _, records = private_records
    for label in ['Private', 'Unresolved']:
        action = records[label][2]
        result = cwi_client.post(f'/api/actions/{action.id}/add-to-calendar', json={
            'connection_id': connection_id, 'google_calendar_id': 'primary', 'all_day': True, 'all_day_date': '2026-11-03'})
        assert result.status_code == 404, result.text
    assert fake_calendar.create_calls == 0


def test_copilot_rejects_foreign_project_reference_before_ai(cwi_client, seeded_user, private_records, monkeypatch):
    _login(cwi_client, seeded_user)
    _, _, _, records = private_records
    def unexpected(*args, **kwargs):
        raise AssertionError('Foreign project must be rejected before evidence or AI work')
    monkeypatch.setattr(CopilotService, '_gather_evidence', unexpected)
    result = cwi_client.post('/api/copilot/ask', json={
        'question': 'What changed?', 'business_entity_id': str(records['Private'][0].id)})
    assert result.status_code == 404, result.text
