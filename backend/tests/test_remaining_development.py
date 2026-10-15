"""剩余 PRD 开发项：反馈来源、产品分母、问卷和恢复；全部临时库/供应商替身。"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as O
import pytest
from app.core.config import settings
from app.models.database import SessionLocal, migrate_db
from app.models.entities import (User, Session, Turn, Evaluation, ProductEvent, ReviewSurvey,
    RequestOperation, RecoveryAttempt, DeviceCheck, DrillAttempt, ReviewQueueItem, ReviewDueEvent, DailyReviewAttempt)
from app.services import product_metrics, session_svc, product_data
from app.services.answer_feedback import grounded, mock_feedback
from app.services.evaluator import DashScopeEvaluatorProvider

H={'X-Invite-Code':'test-a'}
OTHER={'X-Invite-Code':'test-b'}
QA={'X-Invite-Code':'qa-a'}


def call(client,path,body=None,headers=H):
    response=client.post('/api/v1'+path,json=body,headers=headers)
    assert response.status_code==200,response.text
    return response.json()


def create(client,**extra):
    return call(client,'/sessions',{'level':'basic','difficulty':'standard',**extra})['session_id']


def completed(db,sid='s',uid='user-a',kind='voice',when=None):
    when=when or datetime.now(timezone.utc)-timedelta(minutes=2)
    if not db.get(User,uid):db.add(User(id=uid))
    s=Session(id=sid,user_id=uid,status='ENDED',started_at=when-timedelta(minutes=5),ended_at=when,review=json.dumps({'evidence':[sid+'-t']}))
    db.add(s);db.add(Turn(id=sid+'-t',session_id=sid,input_type=kind,eval_result='incomplete',created_at=when));db.commit()
    return s


def test_feedback_is_specific_and_unknown_answers_do_not_get_template():
    first=mock_feedback('Yesterday I go to work.')[0]
    second=mock_feedback('She have a book.')[0]
    assert first.practice_sentence=='Yesterday I went to work.'
    assert second.practice_sentence=='She has a book.'
    assert first.explanation_cn!=second.explanation_cn
    assert mock_feedback('I work')==[] and mock_feedback('Yes, please.')==[]


@pytest.mark.parametrize('change',[{'source_quote':'made up'}, {'facts_preserved':'true'},
    {'facts_preserved':False}, {'practice_sentence':'I earned 500 dollars.'}, {'practice_sentence':'I work for Google.'}])
def test_unreliable_feedback_is_rejected(change):
    item={'issue_type':'grammar','source_quote':'I work','explanation_cn':'说明','practice_sentence':'I worked.','facts_preserved':True}
    assert grounded('I work',[{**item,**change}])==[]


def test_real_evaluator_parses_feedback_without_extra_call(monkeypatch):
    from app.services import generation_call
    calls=[]
    value={'result':'incomplete','evidence_turn_ids':[],'feedback':[f.model_dump() for f in mock_feedback('She have a book.')]}
    def provider(**kwargs):calls.append(kwargs);return O(output=O(choices=[O(message=O(content=json.dumps(value)))]))
    monkeypatch.setattr(generation_call,'call_generation',provider)
    result=DashScopeEvaluatorProvider('fixture','fixture').evaluate({'answer':'She have a book.'})
    assert len(calls)==1 and result.feedback[0].practice_sentence=='She has a book.'


def test_review_feedback_persists_and_revision_invalidates(client):
    sid=create(client)
    r=client.post(f'/api/v1/sessions/{sid}/turns/text',json={'text':'Yesterday I go to work.'},headers=H)
    assert r.status_code==200
    view=call(client,f'/sessions/{sid}/end',{'reason':'user_ended'})['review']
    item=view['improvements'][0]
    assert item['source_quote']=='Yesterday I go to work.' and item['feedback_source']=='mock_fixture'
    assert item['practice_sentence']=='Yesterday I went to work.' and item['revision_version']==0
    with SessionLocal() as db:
        assert json.loads(db.query(Evaluation).filter_by(turn_id=item['turn_id']).one().feedback_json)
    result=client.patch(f"/api/v1/sessions/{sid}/turns/{item['turn_id']}",json={'revised_text':'Yesterday I went to work.'},headers=H)
    assert result.status_code==200
    assert client.get(f'/api/v1/sessions/{sid}',headers=H).json()['review']['improvements']==[]


def test_correct_short_answer_has_no_fake_personal_diagnosis(client):
    sid=create(client,scene_id='hotel_checkin')
    client.post(f'/api/v1/sessions/{sid}/turns/text',json={'text':'Yes, please.'},headers=H)
    review=call(client,f'/sessions/{sid}/end',{'reason':'user_ended'})['review']
    assert review['improvements']==[] and review['feedback_status']=='not_evaluated'
    assert review['ratings']['grammar']=='not_evaluated'


def test_selection_tracking_is_owned_whitelisted_and_idempotent(client):
    body={'action':'scene_select','scene_id':'hotel_checkin','request_key':'selection'}
    first=call(client,'/users/me/product-events',body)
    assert call(client,'/users/me/product-events',body)==first
    assert client.post('/api/v1/users/me/product-events',json={**body,'scene_id':'hobby'},headers=H).status_code==409
    assert client.post('/api/v1/users/me/product-events',json={**body,'action':'mastered'},headers=H).status_code==422
    assert client.post('/api/v1/sessions',json={'selection_id':first['event_id'],'scene_id':'hotel_checkin'},headers=OTHER).status_code==409
    create(client,scene_id='hotel_checkin',selection_id=first['event_id'])
    with SessionLocal() as db:
        assert db.query(ProductEvent).count()==1
        assert product_metrics.report(db)['metrics']['scene_start']['rate']==1


def test_surveys_unknown_response_edit_and_ownership(client):
    with SessionLocal() as db:completed(db)
    assert call(client,'/sessions/s/review-survey',{'answer':'shown'})['answer'] is None
    call(client,'/sessions/s/review-survey',{'answer':'uncertain'})
    call(client,'/sessions/s/review-survey',{'answer':'shown'})
    assert client.post('/api/v1/sessions/s/review-survey',json={'answer':'helpful'},headers=OTHER).status_code==403
    with SessionLocal() as db:
        values=product_metrics.report(db)['metrics']
        assert values['feedback_helpful']['rate'] is None and values['feedback_helpful']['unknown']==1
        assert values['survey_response']['rate']==1 and db.query(ReviewSurvey).count()==1
    call(client,'/sessions/s/review-survey',{'answer':'helpful'})
    with SessionLocal() as db:assert product_metrics.report(db)['metrics']['feedback_helpful']['rate']==1


def test_empty_ended_stays_in_first_completion_denominator():
    with SessionLocal() as db:
        completed(db)
        db.add(User(id='abandoned'));db.add(Session(id='empty',user_id='abandoned',status='ENDED',review='{}'))
        db.commit()
        metric=product_metrics.report(db)['metrics']['first_completion']
        assert metric['numerator']==1 and metric['denominator']==2


def test_stage_metrics_keep_cancellations_and_separate_transfer_denominators():
    with SessionLocal() as db:
        completed(db,'source')
        for sid in ('drill1','drill2'):
            db.add(Session(id=sid,user_id='user-a',practice_type='sentence_practice',status='PRACTICE',drill_stage='free_speak',source_session_id='source'))
            for action in ('stage_view','stage_start'):
                db.add(ProductEvent(id=uuid.uuid4().hex,user_id='user-a',action=action,request_key=sid,session_id=sid,stage='free_speak'))
        db.add(DrillAttempt(user_id='user-a',drill_session_id='drill1',stage='free_speak',input_type='voice',result='independent'))
        db.commit()
        report=product_metrics.report(db)['metrics']
        assert report['drill_from_started']['denominator']==2 and report['drill_from_started']['rate']==.5
        assert report['transfer_eligible']['denominator']==1 and report['transfer_accepted']['denominator']==0
        session_svc.delete_session(db,'source','user-a')
        assert product_metrics.report(db)['metrics']['drill_from_started']['denominator']==0


def test_recovery_pending_uncertain_and_saved_result(client):
    sid=create(client)
    with SessionLocal() as db:
        db.add(RequestOperation(id='interrupted-op',user_id='user-a',scope='turn',session_id=sid,object_id=sid,request_key='pending',request_hash='fixture',status='running'))
        db.commit()
    result=call(client,f'/sessions/{sid}/recoveries',{'operation_id':'interrupted-op'})
    assert result['state']=='pending'
    assert call(client,f'/sessions/{sid}/recoveries',{'operation_id':'interrupted-op'})==result
    assert client.post(f'/api/v1/sessions/{sid}/recoveries',json={'operation_id':'interrupted-op'},headers=OTHER).status_code==403
    with SessionLocal() as db:
        op=db.get(RequestOperation,'interrupted-op');op.status='uncertain';db.commit()
        assert product_metrics.report(db)['metrics']['recovery_success']['numerator']==0
        op.status='done';op.response_json='{"saved":true}';db.commit()
        metric=product_metrics.report(db)['metrics']['recovery_success']
        assert metric['numerator']==metric['denominator']==1 and db.query(RecoveryAttempt).count()==1


def test_device_success_requires_real_voice_fact_and_owned_link(client):
    one=call(client,'/device-check',{'status':'ok','summary':'ok'})['device_check_id']
    two=call(client,'/device-check',{'status':'ok','summary':'ok'})['device_check_id']
    first=create(client,device_check_id=one);create(client,device_check_id=two)
    with SessionLocal() as db:
        from app.models.entities import EventLog
        for s in db.query(Session).filter(Session.device_check_id.in_([one,two])).all():db.add(EventLog(session_id=s.id,action_type='record_attempt'))
        db.add(Turn(id='voice',session_id=first,input_type='voice',eval_result='incomplete'));db.commit()
        metric=product_metrics.report(db)['metrics']['device_record_success']
        assert metric['numerator']==1 and metric['denominator']==2
    assert client.post('/api/v1/sessions',json={'device_check_id':one},headers=OTHER).status_code==409


def test_word_dispute_is_per_evaluation_and_no_word_case_rejected(client):
    with SessionLocal() as db:
        completed(db)
        db.add(Turn(id='word',session_id='s',input_type='practice',practice_eval=json.dumps({'word_items':[{'word':'book','score':50}]})))
        db.add(Turn(id='total',session_id='s',input_type='practice',practice_eval=json.dumps({'score':85,'word_items':[]})))
        db.commit()
    call(client,'/sessions/s/turns/word/pronunciation-dispute')
    call(client,'/sessions/s/turns/word/pronunciation-dispute')
    assert client.post('/api/v1/sessions/s/turns/total/pronunciation-dispute',headers=H).status_code==409
    with SessionLocal() as db:
        metric=product_metrics.report(db)['metrics']['word_dispute']
        assert metric['numerator']==metric['denominator']==1
        session_svc.delete_session(db,'s','user-a')
        assert product_metrics.report(db)['metrics']['word_dispute']['denominator']==0


def test_metrics_permissions_empty_samples_and_time_validation(client,monkeypatch):
    monkeypatch.setattr(settings,'qa_invite_codes',['qa-a'])
    path='/api/v1/qa/learning-metrics'
    assert client.get(path,headers=H).status_code==403
    result=client.get(path,headers=QA)
    assert result.status_code==200,result.text
    assert len(result.json()['metrics'])==17
    assert all(m['rate'] is None for m in result.json()['metrics'].values())
    assert client.get(path+'?start=2026-10-16T00:00:00Z&end=2026-10-15T00:00:00Z',headers=QA).status_code==422


def test_new_fields_migrate_without_erasing_existing_records():
    with SessionLocal() as db:completed(db)
    migrate_db();migrate_db()
    with SessionLocal() as db:assert db.get(Session,'s').selection_id=='' and db.get(Turn,'s-t')


def test_daily_completion_keeps_abandoned_user_and_due_window_denominators():
    now=datetime.now(timezone.utc)
    with SessionLocal() as db:
        for uid,success in [('user-a',True),('user-b',False)]:
            source=completed(db,'source-'+uid,uid)
            item=ReviewQueueItem(user_id=uid,source_session_id=source.id,source_turn_id=source.id+'-t',suggestion_sentence='fixture',due_at=now-timedelta(hours=1))
            db.add(item);db.flush()
            db.add(ReviewDueEvent(user_id=uid,queue_item_id=item.id,due_at=now-timedelta(hours=1),resolved_at=now if success else None,result='text_completed' if success else ''))
            sid='daily-'+uid
            db.add(Session(id=sid,user_id=uid,practice_type='daily_review',status='ENDED',end_reason='goal_reached' if success else 'user_ended',daily_review_items=json.dumps([item.id])))
            if success:db.add(DailyReviewAttempt(user_id=uid,session_id=sid,queue_item_id=item.id,result='text_completed'))
            db.commit()
        report=product_metrics.report(db)
        assert report['metrics']['daily_completion']['numerator']==1 and report['metrics']['daily_completion']['denominator']==2
        assert report['metrics']['due_completion']['numerator']==1 and report['metrics']['due_completion']['denominator']==2
        assert report['counts']['due_text']==1 and report['counts']['due_independent']==0


def test_transfer_rates_deduplicate_eligible_users_across_sessions():
    with SessionLocal() as db:
        completed(db,'source')
        for sid in ('one','two'):
            db.add(Session(id=sid,user_id='user-a',practice_type='sentence_practice',source_session_id='source'))
            db.add(DrillAttempt(user_id='user-a',drill_session_id=sid,stage='free_speak',input_type='voice',result='independent'))
        db.add(ProductEvent(id='accept',user_id='user-a',session_id='one',action='stage_start',stage='transfer',request_key='accept'))
        db.add(DrillAttempt(user_id='user-a',drill_session_id='one',stage='transfer',input_type='voice',result='transfer'))
        db.commit()
        metrics=product_metrics.report(db)['metrics']
        assert metrics['transfer_eligible']['denominator']==1 and metrics['transfer_accepted']['rate']==1


def test_staff_choices_are_excluded_from_participant_metrics(client,monkeypatch):
    monkeypatch.setattr(settings,'qa_invite_codes',['qa-a'])
    call(client,'/users/me/product-events',{'action':'scene_select','scene_id':'hobby','request_key':'staff'},headers=QA)
    create(client)
    call(client,'/users/me/product-events',{'action':'scene_select','scene_id':'hobby','request_key':'learner'})
    with SessionLocal() as db:
        assert product_metrics.report(db)['metrics']['scene_start']['denominator']==1


def test_viewing_active_history_does_not_count_as_viewing_completed_review(client):
    from app.models.entities import EventLog
    sid=create(client)
    path=f'/api/v1/sessions/{sid}/events'
    assert client.post(path,json={'action':'review_view','request_key':'active'},headers=H).status_code==409
    with SessionLocal() as db:
        s=completed(db,'completed')
        db.add(EventLog(session_id=s.id,action_type='review_view',created_at=s.ended_at-timedelta(seconds=1)))
        db.commit()
        assert product_metrics.report(db)['metrics']['review_view']['numerator']==0
        db.add(EventLog(session_id=s.id,action_type='review_view',created_at=s.ended_at+timedelta(seconds=1)))
        db.commit()
        assert product_metrics.report(db)['metrics']['review_view']['numerator']==1
