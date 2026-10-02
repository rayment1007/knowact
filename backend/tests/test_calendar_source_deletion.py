from uuid import UUID

from app.modules.cwi.models import CalendarEventLink, CalendarSource
from sqlalchemy import select
from tests.test_cwi_calendar_routes import cwi_client, fake_calendar, _login, _connect_calendar, _make_action


def test_delete_action_preserves_independent_calendar_control(cwi_client, db_session, seeded_user, fake_calendar):
    _login(cwi_client, seeded_user)
    connection_id = _connect_calendar(cwi_client)
    action = _make_action(db_session, seeded_user['user'].organization_id)
    result = cwi_client.post(f'/api/actions/{action.id}/add-to-calendar', json={
        'connection_id': connection_id, 'google_calendar_id': 'primary',
        'all_day': True, 'all_day_date': '2026-10-03',
    })
    assert result.status_code == 200, result.text
    link_id = result.json()['id']
    assert cwi_client.delete(f'/api/actions/{action.id}').status_code == 204
    db_session.expire_all()
    link = db_session.get(CalendarEventLink, UUID(link_id))
    assert link is not None and link.action_item_id is None
    assert fake_calendar.delete_calls == 0
    activity = cwi_client.get('/api/workspace/activity').json()['items']
    deletion = next(item for item in activity if item['label'] == 'Action deleted')
    assert deletion['title'] == 'Item deleted. Activity record only.'
    assert deletion['path'] is None
    cancel = cwi_client.post(f'/api/calendar/links/{link_id}/cancel')
    assert cancel.json()['sync_status'] == 'CANCELLED'
    assert fake_calendar.delete_calls == 1


def test_remove_calendar_copy_preserves_google_and_allows_resync(cwi_client, db_session, seeded_user, fake_calendar):
    _login(cwi_client, seeded_user)
    connection_id = _connect_calendar(cwi_client)
    fake_calendar.source_events = [{'id': 'original-event', 'summary': 'Original',
        'start': {'date': '2026-10-03'}, 'end': {'date': '2026-10-04'}}]
    assert cwi_client.post(f'/api/calendar/{connection_id}/sync-now').status_code == 200
    source_id = db_session.scalar(select(CalendarSource.id))
    response = cwi_client.delete(f'/api/calendar/sources/{source_id}')
    assert response.status_code == 204, response.text
    db_session.expire_all()
    assert db_session.get(CalendarSource, source_id) is None
    assert fake_calendar.delete_calls == 0
    assert cwi_client.get(f'/api/workspace/source/calendar/{source_id}').status_code == 404
    assert cwi_client.post(f'/api/calendar/{connection_id}/sync-now').status_code == 200
    restored = db_session.scalar(select(CalendarSource))
    assert restored.google_event_id == 'original-event' and restored.id != source_id


def test_calendar_removal_rejects_other_owner(cwi_client, db_session, seeded_user, fake_calendar):
    from app.core.models import User
    from app.modules.cwi.models import IntegrationConnection
    _login(cwi_client, seeded_user)
    connection_id = _connect_calendar(cwi_client)
    fake_calendar.source_events = [{'id': 'private', 'summary': 'Private', 'start': {'date': '2026-10-03'}}]
    cwi_client.post(f'/api/calendar/{connection_id}/sync-now')
    source_id = db_session.scalar(select(CalendarSource.id))
    other = User(organization_id=seeded_user['user'].organization_id,
        email='other-calendar@example.com', full_name='Other', password_hash='unused', role='MEMBER')
    db_session.add(other); db_session.flush()
    db_session.get(IntegrationConnection, UUID(connection_id)).user_id = other.id
    db_session.flush()
    assert cwi_client.delete(f'/api/calendar/sources/{source_id}').status_code == 404
    assert db_session.get(CalendarSource, source_id) is not None
    assert fake_calendar.delete_calls == 0
