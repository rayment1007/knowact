"""Personal workspace regression tests, including two users in the SAME org."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app.core.models import (User, SourceItem, SourceType, KnowledgeItem, ActionItem,
    BusinessEntity, BusinessEntityType, SuggestionStatus, AuditLog)
from app.core.schemas import ActionCreate, ActionUpdate, DecisionCreate
from app.core.services.action_service import ActionService
from app.core.services.knowledge_service import KnowledgeService
from app.core.services.ingestion_service import IngestionService
from app.core.services.decision_service import DecisionService
from app.core.services.brief_service import BriefService
from app.core.services.audit_service import AuditService


@pytest.fixture
def peers(db_session, seeded_user):
    a = seeded_user['user']
    b = User(organization_id=a.organization_id, email='peer@example.test',
             full_name='Peer', password_hash=a.password_hash, role='MEMBER')
    db_session.add(b); db_session.flush()
    return a,b


def knowledge(db, user, **kw):
    item = KnowledgeItem(organization_id=user.organization_id, workspace_owner_id=user.id,
        summary='Private fact', evidence_text='The date is 17 September.', key_points=[],
        knowledge_type='CONTEXT', status=SuggestionStatus.SUGGESTED, **kw)
    db.add(item); db.flush(); return item


def test_service_requires_explicit_owner(db_session):
    with pytest.raises(TypeError): KnowledgeService(db_session)
    with pytest.raises(ValueError): KnowledgeService(db_session, owner_id=None)
    with pytest.raises(TypeError): AuditService(db_session).list(None)


def test_null_and_peer_records_never_read_or_mutate(db_session, peers):
    a,b=peers
    own=knowledge(db_session,a)
    peer=knowledge(db_session,b)
    unknown=knowledge(db_session,a); unknown.workspace_owner_id=None; db_session.flush()
    service=KnowledgeService(db_session,owner_id=a.id)
    assert [x.id for x in service.list_for_entity(a.organization_id)] == [own.id]
    for item in (peer,unknown):
        for mutate in (service.confirm,service.reject,service.delete):
            with pytest.raises(HTTPException) as error:
                mutate(a.organization_id,item.id,a.id)
            assert error.value.status_code == 404
    assert peer.status == SuggestionStatus.SUGGESTED


def test_review_terminal_states_and_idempotent_audits(db_session, peers):
    a,_=peers; service=KnowledgeService(db_session,owner_id=a.id)
    approved=knowledge(db_session,a); rejected=knowledge(db_session,a)
    service.confirm(a.organization_id,approved.id,a.id)
    service.confirm(a.organization_id,approved.id,a.id)
    service.reject(a.organization_id,rejected.id,a.id)
    service.reject(a.organization_id,rejected.id,a.id)
    for fn,item in ((service.reject,approved),(service.confirm,rejected)):
        with pytest.raises(HTTPException) as error: fn(a.organization_id,item.id,a.id)
        assert error.value.status_code == 409
    rows=db_session.scalars(select(AuditLog).where(AuditLog.actor_id==a.id)).all()
    assert sorted(r.action_type for r in rows) == ['CONFIRM_KNOWLEDGE','REJECT_KNOWLEDGE']


def test_empty_evidence_cannot_be_approved(db_session, peers):
    a,_=peers; item=knowledge(db_session,a); item.evidence_text=' '
    db_session.flush()
    with pytest.raises(HTTPException) as error:
        KnowledgeService(db_session,owner_id=a.id).confirm(a.organization_id,item.id,a.id)
    assert error.value.status_code == 409
    assert item.status == SuggestionStatus.SUGGESTED


def test_peer_source_denied_before_extractor_and_peer_context_excluded(db_session, peers):
    a,b=peers
    src=SourceItem(organization_id=b.organization_id,created_by=b.id,
                   source_type=SourceType.MANUAL,title='Private source',content='Secret')
    db_session.add(src); db_session.flush()
    called=[]
    with pytest.raises(HTTPException) as error:
        KnowledgeService(db_session,owner_id=a.id).extract(a.organization_id,src.id,
            extractor=lambda *args:called.append(args))
    assert error.value.status_code==404 and not called
    assert IngestionService(db_session,owner_id=a.id).list_inbox(a.organization_id)==[]
    knowledge(db_session,b).status=SuggestionStatus.CONFIRMED; db_session.flush()
    assert BriefService(db_session,owner_id=a.id)._confirmed_knowledge(a.organization_id)==[]


def test_cross_owner_references_denied_and_action_owner_not_assignee(db_session, peers):
    a,b=peers
    project=BusinessEntity(organization_id=b.organization_id,workspace_owner_id=b.id,
                          name='Private project',entity_type=BusinessEntityType.PROJECT)
    db_session.add(project); db_session.flush()
    action_service=ActionService(db_session,owner_id=a.id)
    for payload in (ActionCreate(title='x',business_entity_id=project.id),
                    ActionCreate(title='x',owner_id=b.id)):
        with pytest.raises(HTTPException) as error:
            action_service.create(a.organization_id,a.id,payload)
        assert error.value.status_code==404
    with pytest.raises(HTTPException):
        DecisionService(db_session,owner_id=a.id).create(a.organization_id,a.id,
            DecisionCreate(title='x',decision='x',rationale='x',evidence_text='x',business_entity_id=project.id))
    item=action_service.create(a.organization_id,a.id,ActionCreate(title='Mine'))
    assert item.workspace_owner_id==a.id and item.owner_id is None
    with pytest.raises(HTTPException):
        ActionService(db_session,owner_id=b.id).update(b.organization_id,item.id,b.id,ActionUpdate(title='Stolen'))
    assert item.title=='Mine'
    assert AuditService(db_session).list(a.organization_id,owner_id=b.id)==[]


def test_legacy_http_routes_enforce_personal_scope(client, db_session, seeded_user, peers):
    from tests.test_cwi_calendar_routes import _login
    a, b = peers
    _login(client, seeded_user)
    source = SourceItem(organization_id=b.organization_id, created_by=b.id,
        source_type=SourceType.MANUAL, title='Peer source', content='Peer content')
    entity = BusinessEntity(organization_id=b.organization_id, workspace_owner_id=b.id,
        entity_type=BusinessEntityType.PROJECT, name='Peer project')
    action = ActionService(db_session, owner_id=b.id).create(b.organization_id, b.id, ActionCreate(title='Peer task'))
    decision = DecisionService(db_session, owner_id=b.id).create(b.organization_id, b.id,
        DecisionCreate(title='Peer decision', decision='Choice', rationale='Why', evidence_text='Evidence'))
    item = knowledge(db_session, b)
    db_session.add_all([source, entity]); db_session.flush()
    for path in ['/api/source-items', '/api/knowledge', '/api/actions', '/api/decisions', '/api/business-entities', '/api/audit']:
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert response.json() == [], (path, response.text)
    for path in [f'/api/source-items/{source.id}', f'/api/knowledge/{item.id}', f'/api/actions/{action.id}', f'/api/decisions/{decision.id}']:
        assert client.get(path).status_code == 404
        assert client.delete(path).status_code == 404
    assert client.get(f'/api/business-entities/{entity.id}/brief').status_code == 404
    assert client.delete(f'/api/business-entities/{entity.id}').status_code == 404


def test_privacy_derived_chunk_cleanup_never_mutates_other_uploads(db_session, peers):
    from app.modules.cwi.models import DocumentAsset, DocumentChunk
    from app.modules.cwi.services.privacy_service import PrivacyService
    from tests.test_cwi_privacy_routes import _seed_connection, _seed_email_record
    a, b = peers
    source = SourceItem(organization_id=a.organization_id, created_by=a.id,
        source_type=SourceType.EMAIL, title='Original', content='Original text')
    db_session.add(source); db_session.flush()
    connection = _seed_connection(db_session, a.organization_id, a.id)
    _seed_email_record(db_session, a.organization_id, connection.id, source_item_id=source.id)
    assets, chunks = [], []
    for user in [a, b]:
        asset = DocumentAsset(organization_id=user.organization_id, uploaded_by=user.id,
            filename='test.txt', mime_type='text/plain', checksum=str(user.id), storage_key=str(user.id))
        db_session.add(asset); db_session.flush()
        chunk = DocumentChunk(organization_id=user.organization_id, document_asset_id=asset.id,
            chunk_index=0, text='Content', metadata_json={'source_item_id': str(source.id)})
        db_session.add(chunk); db_session.flush()
        assets.append(asset); chunks.append(chunk.id)
    result = PrivacyService(db_session).delete_email_data(a.organization_id, a.id)
    assert result.deleted_chunks == result.marked_documents_source_deleted == 1
    assert db_session.get(DocumentChunk, chunks[0]) is None
    assert db_session.get(DocumentChunk, chunks[1]) is not None
    db_session.refresh(assets[0]); db_session.refresh(assets[1])
    assert assets[0].source_deleted is True and assets[1].source_deleted is False
