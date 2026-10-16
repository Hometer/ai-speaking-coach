"""第三次质检：验证正确行为，所有供应商由固定离线替身代替。"""
import json
from datetime import timedelta
from types import SimpleNamespace
from pathlib import Path
import pytest
from app.core.config import settings
from app.core.errors import AppError
from app.models.database import SessionLocal, migrate_db
from app.models.entities import (User, Session, Turn, DrillAttempt, AsrRiskRecord, BudgetLedger,
    BudgetReservation, RequestOperation, IntentResult, ReviewQueueItem, ReviewDueEvent, TrainingPlan)
from app.services import session_svc as ss, drill_svc as ds, review_queue as qs, training_plan as tp, cleanup, help_svc, recall_service
from app.services.practice import parse_result
from app.services.coach import MockCoachProvider
from app.services.prompts.scenarios import SCENARIOS
from .test_second_audit_repairs import NOW, AUDIO, H, source, drill


def make(db, stage='full_text'):
    target = drill(db, stage)
    db.add(Turn(id='opening', session_id='drill', seq=0, input_type='coach', action_type='suggest_practice', practice_sentence=target,
                reply_text=target, reply_audio_url=ss._save_audio(AUDIO, 'wav')))
    db.commit()
    return target


def asr(monkeypatch, text, callback=None):
    class ASR:
        def transcribe(self, path):
            if callback: callback(path)
            return text
    monkeypatch.setattr(ss, 'get_asr_provider', lambda: ASR())


def send(db, target, key='v'):
    return ss.submit_voice_intent(db, 'user-a', 'drill', 'drill_voice', 'drill', key, AUDIO,
        lambda text: ds.submit_drill_attempt(db, 'drill', 'user-a', text, 'voice', key))


@pytest.mark.parametrize('action', ['delete', 'dispute', 'revise'])
def test_reference_revoked_everywhere_but_own_answer_preserved(action):
    with SessionLocal() as db:
        target=make(db)
        ss.submit_practice(db,'drill','user-a',AUDIO,target,'p')
        own=Turn(id='own',session_id='drill',seq=10,input_type='voice',user_text='My new answer')
        db.add(own);db.commit()
        if action=='delete':ss.delete_session(db,'source','user-a')
        elif action=='dispute':ss.dispute_review_improvement(db,'source','user-a','source-t')
        else:ss.revise_turn(db,'source','user-a','source-t','I booked a room yesterday.')
        db.expire_all()
        detail=ss.get_session_detail(db,'drill','user-a')
        assert detail['source_invalid']
        assert all(not t['practice_eval'] and not t['practice_sentence'] for t in detail['turns'])
        assert db.get(Turn,'own').user_text=='My new answer'
        assert not db.query(RequestOperation).filter(RequestOperation.session_id=='drill', RequestOperation.response_json.contains(target)).first()
        assert not db.query(IntentResult).filter_by(object_id='drill').first()


@pytest.mark.parametrize('outcome', ['ok','error','delete'])
def test_raw_upload_and_transcode_removed_on_all_exits(monkeypatch,outcome):
    with SessionLocal() as db:
        db.add(User(id='user-a'));db.add(Session(id='s',user_id='user-a',status='WAITING_INPUT',scene_id='hotel_checkin',active_since=NOW(),duration_minutes=15));db.commit()
        paths=[]
        def during(path):
            paths.extend([path,path.with_suffix('.wav')]);path.with_suffix('.wav').write_bytes(AUDIO)
            if outcome=='error':raise AppError('ASR_ERROR','转写失败',502)
            if outcome=='delete':
                with SessionLocal() as other:ss.delete_session(other,'s','user-a')
                assert not path.exists()
        asr(monkeypatch,'I have a reservation under the name Li.',during)
        if outcome=='ok':ss.submit_turn(db,'s','user-a','voice',audio_bytes=AUDIO,request_key='voice')
        else:
            with pytest.raises(AppError):ss.submit_turn(db,'s','user-a','voice',audio_bytes=AUDIO,request_key='voice')
        assert paths and all(not p.exists() for p in paths)


@pytest.mark.parametrize('scope',['session','user','project'])
def test_tts_retry_stops_before_provider_for_each_budget_scope(monkeypatch,scope):
    from app.services.budget import _day_key
    with SessionLocal() as db:
        db.add(User(id='user-a'));db.add(Session(id='s',user_id='user-a',status='WAITING_INPUT'))
        db.add(Turn(id='t',session_id='s',reply_text='Question?',audio_status='failed'))
        keys={'session':'s','user':_day_key('user-a'),'project':_day_key('all')}
        cap={'session':settings.budget_session,'user':settings.budget_user_day,'project':settings.budget_project_day}[scope]
        db.add(BudgetLedger(scope=scope,scope_key=keys[scope],spent=cap));db.commit()
        monkeypatch.setattr(ss,'get_tts_provider',lambda:pytest.fail('must stop before initialization'))
        for key in ('retry1','retry2'):
            with pytest.raises(AppError) as e:ss.synthesize_turn_audio(db,'s','user-a','t',True,key)
            assert e.value.code=='BUDGET_EXCEEDED'
        assert db.query(BudgetReservation).count()==0


def test_tts_failed_attempt_settled_cache_free_and_same_intent_replays(monkeypatch):
    with SessionLocal() as db:
        make(db)
        db.add(Turn(id='t',session_id='drill',seq=1,reply_text='Question?',audio_status='failed'));db.commit()
        calls=[]
        class TTS:
            ext='wav'
            def synthesize_result(self,text):calls.append(text);return SimpleNamespace(audio=None,attempts=2)
        monkeypatch.setattr(ss,'get_tts_provider',lambda:TTS())
        one=ss.synthesize_turn_audio(db,'drill','user-a','t',True,'r')
        assert ss.synthesize_turn_audio(db,'drill','user-a','t',True,'r')==one
        rows=db.query(BudgetReservation).all()
        assert len(calls)==1 and len(rows)==1 and rows[0].provider_started and rows[0].status=='settled'
        t=db.get(Turn,'t');t.audio_status='ok';t.reply_audio_url=ss._save_audio(AUDIO,'wav');db.commit()
        ss.synthesize_turn_audio(db,'drill','user-a','t',True,'cached')
        assert len(calls)==1 and db.query(BudgetReservation).count()==1


@pytest.mark.parametrize('expired', ['cleared','aged','missing'])
def test_expired_ordinary_risk_cannot_form_voice_evidence(monkeypatch,expired):
    with SessionLocal() as db:
        db.add(User(id='user-a'));db.add(Session(id='s',user_id='user-a',status='WAITING_INPUT',scene_id='hotel_checkin',active_since=NOW(),duration_minutes=15));db.commit()
        asr(monkeypatch,'and')
        r=ss.submit_turn(db,'s','user-a','voice',audio_bytes=AUDIO,request_key='r')
        risk=db.query(AsrRiskRecord).one()
        if expired=='cleared':risk.recording_url=''
        elif expired=='aged':risk.created_at=NOW()-timedelta(minutes=30)
        else:(settings.data_dir/'audio'/Path(risk.recording_url).name).unlink()
        db.commit()
        with pytest.raises(AppError) as e:ss.confirm_turn(db,'s','user-a',r['turn_id'],'I have a reservation under the name Li.')
        assert e.value.code=='AUDIO_EXPIRED' and not db.query(Turn).filter_by(input_type='voice').first()
        # 过期只禁止新的口语结论，文字降级仍可用。
        result=ss.confirm_turn(db,'s','user-a',r['turn_id'],'I have a reservation under the name Li.','text_fallback')
        assert db.get(Turn,result['turn_id']).input_type=='text'


@pytest.mark.parametrize('scope',['drill_voice','queue_voice','daily_voice'])
def test_all_voice_entries_hold_risk_confirm_owned_context_and_no_second_asr(monkeypatch,scope):
    with SessionLocal() as db:
        target=make(db)
        session_id='drill';object_id='drill';key='risk'
        if scope!='drill_voice':
            item=qs.add_to_queue(db,'user-a','source','source-t');q=db.get(ReviewQueueItem,item['id']);q.due_at=NOW()-timedelta(hours=1);db.commit()
            if scope=='queue_voice':session_id='source';object_id=str(q.id)
            else:
                from app.services.daily_review_svc import start_daily_review
                session_id=start_daily_review(db,'user-a','start')['session_id'];object_id=session_id
        callbacks={'drill_voice':lambda text:ds.submit_drill_attempt(db,session_id,'user-a',text,'voice',key),
                   'queue_voice':lambda text:qs.submit_retest(db,'user-a',int(object_id),text,'voice',key),
                   'daily_voice':lambda text:pytest.fail('unconfirmed daily may not submit')}
        asr(monkeypatch,target+' 噪声'*30)
        r=ss.submit_voice_intent(db,'user-a',session_id,scope,object_id,key,AUDIO,callbacks[scope])
        assert r['risk_hold'] and db.query(DrillAttempt).count()==0
        assert ss.pending_voice_risk(db,'user-b',scope,object_id) is None
        risk_id=r['risk_id']
        monkeypatch.setattr(ss,'get_asr_provider',lambda:pytest.fail('confirmation must reuse original ASR'))
        result=ss.confirm_voice_risk(db,'user-a',session_id,risk_id,target,'confirm')
        assert not result.get('risk_hold')
        assert ss.confirm_voice_risk(db,'user-a',session_id,risk_id,target,'confirm')==result
        assert not ss.pending_voice_risk(db,'user-a',scope,object_id)
        if scope=='drill_voice':assert result['recording']['recording_url']


def test_risk_confirmation_rejects_changed_stage_and_expired_audio(monkeypatch):
    with SessionLocal() as db:
        target=make(db);asr(monkeypatch,'and')
        r=send(db,target)
        ds.drill_action(db,'drill','user-a','skip')
        with pytest.raises(AppError) as e:ss.confirm_voice_risk(db,'user-a','drill',r['risk_id'],target,'c')
        assert e.value.code=='STALE_OPERATION' and db.query(DrillAttempt).filter_by(attempt_key='risk:'+str(r['risk_id'])).count()==0
        ss.discard_voice_risk(db,'user-a',r['risk_id'])
        r=send(db,target,'new');risk=db.get(AsrRiskRecord,r['risk_id']);risk.created_at=NOW()-timedelta(minutes=30);db.commit()
        with pytest.raises(AppError) as e:ss.confirm_voice_risk(db,'user-a','drill',r['risk_id'],target,'expired')
        assert e.value.code=='AUDIO_EXPIRED'


def test_drill_own_recording_versions_cleanup_authorization_and_reference_revocation(monkeypatch,client):
    with SessionLocal() as db:
        target=make(db);asr(monkeypatch,target)
        r=send(db,target);recording=r['recording'];url=recording['recording_url']
        assert recording['stage']=='full_text' and recording['reference_sentence']==target and recording['demo_audio_url']
        assert client.get(url,headers=H).status_code==200
        assert client.get(url,headers={'X-Invite-Code':'test-b'}).status_code in (403,404)
        assert client.get(url).status_code==401
        db.expire_all();assert ds.drill_view(db.get(Session,'drill'),db)['recording']==recording
        ds.drill_action(db,'drill','user-a','repeat')
        assert db.get(Session,'drill').drill_stage=='full_text'
        attempt=db.query(DrillAttempt).filter_by(attempt_key='v').one();attempt.created_at=NOW()-timedelta(minutes=30);db.commit()
        cleanup.cleanup_old_recordings();db.expire_all()
        assert not db.get(DrillAttempt,attempt.id).recording_url and not (settings.data_dir/'audio'/Path(url).name).exists()
        assert url not in ''.join(r.response_json for r in db.query(RequestOperation).all())
        assert url not in ''.join(r.response_json for r in db.query(IntentResult).all())


def test_translation_late_result_keeps_attempt_charge(monkeypatch):
    with SessionLocal() as db:
        make(db)
        session=db.get(Session,'drill');session.status='WAITING_INPUT';session.current_question_id='opening';session.current_question_text='May I have your name?';db.commit()
        def translate(text):
            with SessionLocal() as other:ss.set_paused(other,'drill','user-a',True)
            return 'I booked a room.'
        monkeypatch.setattr(help_svc,'_translate',translate)
        with pytest.raises(AppError) as e:help_svc.request_help(db,'drill','user-a','translate','我订了房间','translate')
        assert e.value.code=='STALE_OPERATION'
        r=db.query(BudgetReservation).one();assert r.provider_started and r.status=='settled'
        assert db.query(BudgetLedger).filter_by(scope='session',scope_key='drill').one().spent==settings.nominal_cost_per_turn


def test_queue_pending_preserves_due_and_retry_still_available(monkeypatch):
    with SessionLocal() as db:
        source(db);q=qs.add_to_queue(db,'user-a','source','source-t');item=db.get(ReviewQueueItem,q['id'])
        due=NOW()-timedelta(hours=1);item.due_at=due;db.query(ReviewDueEvent).filter_by(queue_item_id=item.id).update({'due_at':due});db.commit()
        monkeypatch.setattr(recall_service,'verify_meaning',lambda *_:None)
        result=qs.submit_retest(db,'user-a',item.id,'I reserved a hotel room.','voice','p')
        db.refresh(item)
        assert result['retest_result']=='pending' and item.due_at.replace(tzinfo=due.tzinfo)==due
        assert item.success_count==0 and db.query(ReviewDueEvent).filter_by(queue_item_id=item.id).one().resolved_at is None
        assert qs.submit_retest(db,'user-a',item.id,'I reserved a hotel room.','voice','p')==result
        assert qs.submit_retest(db,'user-a',item.id,'I booked a hotel room.','voice','new')['retest_result']=='pending'


def test_plan_resume_preserves_completed_day_and_source_revision_can_revoke(monkeypatch):
    now=NOW()
    with SessionLocal() as db:
        db.add(User(id='user-a'));db.commit();p=tp.create_or_renew_plan(db,'user-a','workplace',5);d=p['days'][0]
        db.add(Session(id='completed',user_id='user-a',status='ENDED',practice_type='interview',scene_id=d['scene_id'],plan_id=p['id'],plan_day=1,ended_at=now,review=json.dumps({'evidence':['answer']})))
        db.add(Turn(id='answer',session_id='completed',input_type='voice',eval_result='independent'));db.commit()
        tp.pause_plan(db,'user-a');monkeypatch.setattr(tp,'_now',lambda:now+timedelta(days=1))
        result=tp.resume_plan(db,'user-a');assert result['days'][0]['status']=='completed' and result['days'][0]['scheduled_date']==d['scheduled_date']
        assert result['days'][1]['scheduled_date']!=p['days'][1]['scheduled_date']
        ss.revise_turn(db,'completed','user-a','answer','Corrected answer')
        assert tp.get_plan(db,'user-a')['days'][0]['status']=='pending'


def test_material_hints_only_use_actual_reference():
    with SessionLocal() as db:
        make(db);s=db.get(Session,'drill')
        s.drill_stage='fill_blanks';assert ds.drill_view(s,db)['visible_help']=='I booked a room for ___'
        s.drill_stage='structure';view=ds.drill_view(s,db)
        assert 'experience' not in view['visible_help'] and 'I booked a room for' in view['visible_help']
        s.drill_stage='free_speak';view=ds.drill_view(s,db);assert not view['drill_chunk'] and not view['drill_target']


def test_delete_plan_api_preserves_sessions_and_queue_isolates_users(client):
    with SessionLocal() as db:
        source(db);qs.add_to_queue(db,'user-a','source','source-t');first=tp.create_or_renew_plan(db,'user-a','workplace',5)
        tp.restart_plan(db,'user-a');s=db.get(Session,'source');s.plan_id=first['id'];s.plan_day=1
        db.add(User(id='user-b'));db.commit();tp.create_or_renew_plan(db,'user-b','travel',5)
    assert client.delete('/api/v1/training-plan').status_code==401
    r=client.delete('/api/v1/training-plan',headers=H);assert r.status_code==200 and r.json()['deleted']==2
    assert client.get('/api/v1/training-plan',headers=H).json() is None
    with SessionLocal() as db:
        assert db.query(TrainingPlan).filter_by(user_id='user-b').count()==1
        assert db.get(Session,'source').plan_id is None and db.query(ReviewQueueItem).count()==1
    assert client.delete('/api/v1/training-plan',headers=H).json()['deleted']==0


def test_transfer_finalizes_clock_and_end_time_once(monkeypatch):
    with SessionLocal() as db:
        make(db,'transfer');s=db.get(Session,'drill');s.active_since=NOW()-timedelta(seconds=20);db.commit()
        monkeypatch.setattr(ds,'verify_new_condition',lambda *_:True)
        r=ds.submit_drill_attempt(db,'drill','user-a','I reserved three nights under Wang.','voice','x')
        assert r['result']=='transfer' and s.ended_at and s.active_since is None and s.active_seconds>=20
        ended=s.ended_at;ds.submit_drill_attempt(db,'drill','user-a','I reserved three nights under Wang.','voice','x');assert s.ended_at==ended


@pytest.mark.parametrize('word', [
    '<word total_score="999" beg_pos="0" end_pos="5">hello</word>',
    '<word total_score="NaN" beg_pos="0" end_pos="5">hello</word>',
    '<word total_score="2" beg_pos="bad" end_pos="-1">hello</word>',
    '<word total_score="2">hello</word>',
    '<word total_score="2" beg_pos="0" end_pos="5">other</word>',
    '<word total_score="2" beg_pos="0" end_pos="500">hello</word>',
])
def test_unreliable_words_downgrade_to_valid_sentence(word):
    xml='<xml_result><read_chapter total_score="4">'+word+'</read_chapter></xml_result>'
    r=parse_result([{'code':0,'data':{'data':xml,'status':2}}],'hello world')
    assert r['score']==80 and r['word_items']==[]


@pytest.mark.parametrize('score',['NaN','inf','-1','999'])
def test_invalid_whole_score_not_replaced_by_protocol_status(score):
    xml=f'<xml_result><read_chapter total_score="{score}"><word total_score="4" beg_pos="0" end_pos="5">hello</word></read_chapter></xml_result>'
    with pytest.raises(AppError):parse_result([{'code':0,'data':{'data':xml,'status':2}}],'hello')


@pytest.mark.parametrize('scene',list(SCENARIOS))
@pytest.mark.parametrize('branch',['practice','unscored','skip','hint'])
def test_all_teaching_branches_keep_scene_level_and_current_question(monkeypatch,scene,branch):
    with SessionLocal() as db:
        db.add(User(id='user-a'));q=SCENARIOS[scene]['openings']['beginner']
        db.add(Session(id='s',user_id='user-a',scene_id=scene,level='beginner',difficulty='easy',status='PRACTICE',practice_type='sentence_practice',practice_sentence='Hello.',current_question_id='old',current_question_text=q));db.commit()
        seen=[]
        class Coach(MockCoachProvider):
            def decide(self,ctx):seen.append(ctx);return super().decide(ctx)
        monkeypatch.setattr(ss,'get_coach_provider',lambda:Coach())
        if branch=='skip':ss.skip_practice(db,'s','user-a','skip')
        elif branch=='hint':ss.request_hint(db,'s','user-a','hint')
        else:
            class Eval:
                def evaluate(self,*args):return {'score':85 if branch=='practice' else None,'word_items':[]}
            monkeypatch.setattr(ss,'get_practice_eval_provider',lambda:Eval());ss.submit_practice(db,'s','user-a',AUDIO,'Hello.','p')
        ctx=seen[0];assert ctx['scene_id']==scene and ctx['level']=='beginner' and ctx['current_question']==q
        assert ctx['scene_context']==SCENARIOS[scene]['coach_context'] and 'coach_persona' in ctx and 'subgoal' in ctx
        s=db.get(Session,'s');assert s.current_question_text==q
        if branch!='hint':assert s.current_question_id!='old'


def test_new_columns_migrate_idempotently_without_record_loss():
    with SessionLocal() as db:make(db)
    migrate_db();migrate_db()
    with SessionLocal() as db:assert db.query(Session).count()==2 and db.query(Turn).count()==2


def test_pending_queue_risk_deleted_with_item(monkeypatch):
    with SessionLocal() as db:
        source(db);item=qs.add_to_queue(db,'user-a','source','source-t');q=db.get(ReviewQueueItem,item['id']);q.due_at=NOW()-timedelta(hours=1);db.commit()
        asr(monkeypatch,'and')
        r=ss.submit_voice_intent(db,'user-a','source','queue_voice',str(q.id),'r',AUDIO,lambda text:pytest.fail('risk cannot submit'))
        url=r['recording_url'];qs.delete_item(db,'user-a',q.id)
        assert not db.get(AsrRiskRecord,r['risk_id']) and not (settings.data_dir/'audio'/Path(url).name).exists()
        assert ss.pending_voice_risk(db,'user-a','queue_voice',str(q.id)) is None


def test_ordinary_risk_cannot_be_applied_to_new_question(monkeypatch):
    with SessionLocal() as db:
        db.add(User(id='user-a'));db.add(Session(id='s',user_id='user-a',status='WAITING_INPUT',current_question_id='first',current_question_text='Question one',active_since=NOW(),duration_minutes=15));db.commit()
        asr(monkeypatch,'and');r=ss.submit_turn(db,'s','user-a','voice',audio_bytes=AUDIO,request_key='risk')
        s=db.get(Session,'s');s.current_question_id='second';s.current_question_text='Question two';db.commit()
        with pytest.raises(AppError) as e:ss.confirm_turn(db,'s','user-a',r['turn_id'],'I have a reservation.')
        assert e.value.code=='STALE_OPERATION' and not db.query(Turn).filter_by(input_type='voice').first()


def test_wav_upload_transcodes_to_distinct_owned_file_and_both_are_removed(tmp_path):
    from app.services.audio_util import transcode_to_wav_file
    from app.services.tts import _silent_wav
    original=tmp_path/'owned.wav';original.write_bytes(_silent_wav())
    transcoded=transcode_to_wav_file(original)
    assert transcoded!=original and transcoded.is_file() and original.is_file()
    ss._remove_upload(original)
    assert not original.exists() and not transcoded.exists()
