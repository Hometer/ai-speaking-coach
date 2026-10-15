"""事件、观察窗口、对话审核、未知费用与账单去重的离线验收。"""
import json
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from threading import Event
import time
import pytest
from app.core.errors import AppError
from app.core.config import settings
from app.models.database import SessionLocal,migrate_db
from app.models.entities import User,Session,Turn,ConversationQualityMark,ConversationQualityReview,EventLog,ProviderUsageEvent,BudgetLedger,RequestOperation
from app.services import quality_label,billing,learning_metrics,events,deadline,session_svc


def test_quality_unknown_and_manual_review_history(client):
    settings.qa_invite_codes=['qa-a']
    with SessionLocal() as db:
        db.add(User(id='u'));db.add(Session(id='s',user_id='u'));db.add(Turn(id='t',session_id='s'))
        db.add(ConversationQualityMark(user_id='u',session_id='s',turn_id='t',user_direct_question=True,prompt_version='v6',scenario_version='v1'));db.commit()
        view=quality_label.list_sessions(db)[0]['turns'][0]['conversation_quality']
        assert view['answered'] is None and view['repeated_followup'] is None
    body={'answered':False,'repeated_followup':True,'topic_jump':False}
    assert client.patch('/api/v1/qa/conversation/t',json=body,headers={'X-Invite-Code':'test-a'}).status_code==403
    assert client.patch('/api/v1/qa/conversation/t',json=body,headers={'X-Invite-Code':'qa-a'}).status_code==200
    assert client.patch('/api/v1/qa/conversation/t',json={**body,'answered':True},headers={'X-Invite-Code':'qa-a'}).status_code==200
    rows=client.get('/api/v1/qa/conversation/t/history',headers={'X-Invite-Code':'qa-a'}).json()
    assert len(rows)==2 and rows[0]['conclusions']['answered'] is False and rows[1]['conclusions']['answered'] is True
    assert rows[0]['prompt_version']=='v6'


def test_product_event_is_whitelisted_deduplicated_and_owned(client):
    h={'X-Invite-Code':'test-a'}
    created=client.post('/api/v1/sessions',json={'level':'basic','difficulty':'standard'},headers=h).json()
    path=f"/api/v1/sessions/{created['session_id']}/events"
    body={'action':'demo_play','request_key':'one-play'}
    assert client.post(path,json=body,headers=h).status_code==200
    assert client.post(path,json=body,headers=h).status_code==200
    assert client.post(path,json=body,headers={'X-Invite-Code':'test-b'}).status_code==403
    assert client.post(path,json={'action':'fake_mastery','request_key':'x'},headers=h).status_code==422
    with SessionLocal() as db:
        assert db.query(EventLog).filter_by(action_type='demo_play').count()==1


def test_metrics_observation_window_excludes_assigned_and_text():
    now=datetime.now(timezone.utc)
    with SessionLocal() as db:
        for uid in ('a','b','c'):
            db.add(User(id=uid))
            started=now-timedelta(days=8)
            db.add(Session(id=uid+'-first',user_id=uid,status='ENDED',started_at=started,ended_at=started+timedelta(minutes=5),review=json.dumps({'evidence':[uid+'-turn']})))
            db.add(Turn(id=uid+'-turn',session_id=uid+'-first',input_type='voice',eval_result='independent'))
        # a 的自主完整复练；b 的研究员安排；c 只有文字。
        for uid,initiation,kind in [('a','self','voice'),('b','assigned','voice'),('c','self','text')]:
            db.add(Session(id=uid+'-later',user_id=uid,started_at=now-timedelta(days=5),initiation=initiation))
            for i in range(3):db.add(Turn(id=f'{uid}-{i}',session_id=uid+'-later',input_type=kind,eval_result='incomplete',speech_seconds=20,created_at=now-timedelta(days=5)))
        db.commit()
        result=learning_metrics.calculate(db,now=now)
        assert result['eligible_users']==3 and result['active_review_users']==1
        assert result['full_scene_review_users']==1
        assert result['active_review_rate']==pytest.approx(1/3)


def test_metrics_require_the_answer_itself_within_observation_window():
    now=datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.add(User(id='late'))
        db.add(Session(id='first',user_id='late',status='ENDED',started_at=now-timedelta(days=9),ended_at=now-timedelta(days=8),review=json.dumps({'evidence':['first-turn']})))
        db.add(Turn(id='first-turn',session_id='first',input_type='voice',eval_result='independent'))
        db.add(Session(id='later',user_id='late',started_at=now-timedelta(days=5)))
        db.add(Turn(id='late-answer',session_id='later',input_type='voice',eval_result='independent',created_at=now))
        db.commit()
        result=learning_metrics.calculate(db,now=now)
        assert result['eligible_users']==1 and result['active_review_users']==0


def test_fee_estimate_requires_rates_and_measured_units():
    usage=SimpleNamespace(component='asr',model='asr-model',source='local_upload_bytes',audio_seconds=None,attempts=1)
    book={'version':'fixture','rates':{'asr:asr-model':{'source':'offline-fixture','per_second':'0.01','round_seconds':60}}}
    assert billing.estimate(usage,book) is None
    usage.audio_seconds=61
    assert billing.estimate(usage,book)==pytest.approx(1.2)
    assert billing.estimate(usage,None) is None


def test_bill_reconciliation_is_idempotent_and_affects_budget():
    with SessionLocal() as db:
        usage=ProviderUsageEvent(user_id='u',session_id='s',component='llm',status='ok',source='provider_response',input_tokens=10,output_tokens=20)
        db.add(usage);db.commit()
        billing.reconcile(db,usage.id,'0.25','fixture-invoice-line1')
        billing.reconcile(db,usage.id,'0.25','fixture-invoice-line1')
        assert db.query(BudgetLedger).filter_by(scope='session',scope_key='s').one().actual_spent==pytest.approx(.25)
        assert billing.session_costs(db,'s')['billed_cost']==pytest.approx(.25)
        with pytest.raises(AppError):billing.reconcile(db,usage.id,'.3','fixture-invoice-line1')


def test_deadline_discards_late_result_without_database_work():
    release=Event()
    started=time.monotonic()
    token=deadline.begin(.03)
    try:
        with pytest.raises(AppError) as error:
            deadline.bounded(lambda:release.wait(.3))
        assert error.value.code=='PROVIDER_TIMEOUT'
        assert time.monotonic()-started<.2
    finally:
        deadline.reset(token);release.set()


def test_new_schema_migration_is_repeatable_and_preserves_records():
    with SessionLocal() as db:
        db.add(User(id='saved'));db.add(Session(id='saved',user_id='saved'));db.commit()
    migrate_db();migrate_db()
    with SessionLocal() as db:
        assert db.get(Session,'saved').user_id=='saved'


def test_lifecycle_events_cover_start_pause_resume_end(client):
    h={'X-Invite-Code':'test-a'}
    sid=client.post('/api/v1/sessions',json={'level':'basic','difficulty':'standard'},headers=h).json()['session_id']
    client.post(f'/api/v1/sessions/{sid}/pause',headers=h)
    client.post(f'/api/v1/sessions/{sid}/resume',headers=h)
    client.post(f'/api/v1/sessions/{sid}/end',json={'reason':'user_ended'},headers=h)
    with SessionLocal() as db:
        types={e.action_type for e in db.query(EventLog).filter_by(session_id=sid)}
        assert {'session_start','pause','resume','session_end'}<=types


def test_withdrawn_operation_cannot_repopulate_response_snapshot():
    from app.services.operations import durable, current_id
    @durable('withdraw-fixture', object_arg='user_id')
    def withdrawn(db, user_id, request_key=''):
        db.query(RequestOperation).filter_by(id=current_id()).update({'status':'failed','response_json':'','error_code':'SOURCE_INVALID'})
        db.commit()
        return {'content':'withdrawn fixture'}
    with SessionLocal() as db:
        with pytest.raises(AppError) as error:
            withdrawn(db,'u','withdraw')
        assert error.value.code=='SOURCE_INVALID'
        assert db.query(RequestOperation).one().response_json==''


def test_plan_replacement_records_end_and_start_once():
    from app.services import training_plan
    with SessionLocal() as db:
        first=training_plan.create_or_renew_plan(db,'u','travel',5)
        second=training_plan.create_or_renew_plan(db,'u','daily',5)
        assert first['id']!=second['id']
        training_plan.create_or_renew_plan(db,'u','daily',5)
        assert db.query(EventLog).filter_by(action_type='plan_start').count()==2
        assert db.query(EventLog).filter_by(action_type='plan_end').count()==1
