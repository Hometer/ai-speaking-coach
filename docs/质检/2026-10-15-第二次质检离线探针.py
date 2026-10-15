"""仅复现 8bb0258 的第二次质检反例；断言缺陷仍存在，不是修复验收测试。
运行：在项目根目录执行 .venv/bin/python docs/质检/2026-10-15-第二次质检离线探针.py
全部使用临时库/供应商替身，不读写原项目业务数据，不调用付费服务。
"""
import sys, json, tempfile, pathlib, traceback, subprocess
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
ROOT=pathlib.Path.cwd()
if not (ROOT/'backend/app').is_dir():raise SystemExit('请从项目根目录运行')
if subprocess.check_output(['git','rev-parse','--short=7','HEAD'],text=True,cwd=ROOT).strip()!='8bb0258':raise SystemExit('本探针仅适用于基线 8bb0258；新版本应改写为行为回归，不能按缺陷仍存在来验收')
sys.path.insert(0,str(ROOT/'backend'))
from app.core.config import settings
TMP=pathlib.Path(tempfile.mkdtemp(prefix='speaking50_audit_'))
# 限定供应商替身测试；不使用本地真实凭证。
settings.dashscope_api_key='';settings.xf_appid='';settings.xf_api_key='';settings.xf_api_secret=''
settings.provider_mode='mock';settings.seed_demo_invites=True;settings.qa_invite_codes=['qa-a'];settings.data_dir=TMP/'data';settings.database_url=f'sqlite:///{TMP / "audit.db"}'
from app.main import app
from app.models.database import Base,engine,SessionLocal,init_db
from app.models.entities import *
from app.core.auth import seed_invites
from app.core.errors import AppError
from app.services import session_svc,product_metrics,product_data,drill_svc,review_queue,daily_review_svc,phrase_card_svc
RESULT=[]
NOW=lambda:datetime.now(timezone.utc)
AUDIO=b'RIFF'+b'\0'*4+b'WAVE'+b'\0'*32

def reset():
    settings.provider_mode='mock'
    Base.metadata.drop_all(engine);init_db();seed_invites()

def run(name,fn):
    reset()
    try:
        details=fn();RESULT.append({'probe':name,'reproduced':True,'actual':details})
    except Exception as e:
        RESULT.append({'probe':name,'reproduced':False,'error':type(e).__name__+': '+str(e)});traceback.print_exc()

def completed(db,sid='source',uid='user-a',when=None):
    when=when or NOW()-timedelta(minutes=1)
    if not db.get(User,uid):db.add(User(id=uid))
    s=Session(id=sid,user_id=uid,status='ENDED',scene_id='job_interview',started_at=when-timedelta(minutes=5),ended_at=when,review=json.dumps({'evidence':[sid+'-t'],'improvements':[{'turn_id':sid+'-t','original':'She have a book.','practice_sentence':'She has a book.','explanation_cn':'主语 She 是第三人称单数，这里的 have 应使用 has。'}]}))
    db.add(s);db.add(Turn(id=sid+'-t',session_id=sid,user_text='She have a book.',input_type='text',eval_result='incomplete',created_at=when));db.commit();return s

def derived_context():
    with SessionLocal() as db:
        completed(db)
        q=review_queue.add_to_queue(db,'user-a','source','source-t')
        sp=session_svc.create_sentence_practice(db,'user-a','source','source-t','create')
        card=phrase_card_svc.generate_card(db,'user-a','source','source-t')
        s=db.get(Session,sp['session_id']);s.drill_stage='free_speak';db.commit()
        view=drill_svc.drill_view(s,db)
        assert q['meaning_zh']!='她有一本书' and 'She has a book.' not in q['suggestion_sentence']
        return {'target':s.drill_target,'queue_prompt':q['meaning_zh'],'free_speak_prompt':view['visible_help'],'phrase_replacement':card['replaceable']}

def window_leak():
    with SessionLocal() as db:
        s=completed(db,when=NOW())
        s.started_at=NOW()-timedelta(days=2);db.commit()
        end=NOW()-timedelta(days=1)
        data=product_metrics.report(db,start=end-timedelta(days=2),end=end)
        m=data['metrics']['first_completion']
        assert m['numerator']==1
        return {'first_completion':m,'completion_after_window':True,'north_completed_count':data['completed_scene_count']}

def late_risk():
    with SessionLocal() as db:
        db.add(User(id='user-a'));db.add(Session(id='risk',user_id='user-a',status='WAITING_INPUT',active_since=NOW(),duration_minutes=30));db.commit()
        class ASR:
            def transcribe(self,p):
                with SessionLocal() as other:session_svc.end_session(other,'risk','user-a','user_ended')
                return 'I was going to the'
        with patch.object(session_svc,'get_asr_provider',lambda:ASR()):
            r=session_svc.submit_turn(db,'risk','user-a','voice',audio_bytes=AUDIO,request_key='risk')
        db.expire_all();s=db.get(Session,'risk')
        assert s.status=='ENDED' and s.pending_risk and r.get('risk_hold')
        return {'status':s.status,'returned_risk_hold':r['risk_hold'],'pending_after_end':bool(s.pending_risk),'risk_rows':db.query(AsrRiskRecord).count()}

def drill_wrong_stage():
    with SessionLocal() as db:
        completed(db)
        db.add(Session(id='drill',user_id='user-a',status='PRACTICE',practice_type='sentence_practice',drill_stage='free_speak',drill_target='She has a book.',source_session_id='source',source_turn_id='source-t'));db.commit()
        def semantic(*args):
            with SessionLocal() as other:drill_svc.drill_action(other,'drill','user-a','downgrade')
            return True
        with patch.object(drill_svc,'verify_meaning',semantic):
            r=drill_svc.submit_drill_attempt(db,'drill','user-a','She has a book.','voice','late')
        db.expire_all();a=db.query(DrillAttempt).filter_by(attempt_key='late').one()
        assert a.stage=='structure' and a.result=='independent'
        return {'submitted_stage':'free_speak','changed_to':'structure','stored_stage':a.stage,'stored_result':a.result,'next_stage':r['next_stage']}

def drill_budget():
    with SessionLocal() as db:
        completed(db)
        db.add(Session(id='drill',user_id='user-a',status='PRACTICE',practice_type='sentence_practice',drill_stage='free_speak',drill_target='She has a book.'));db.commit()
        def semantic(*args):
            with SessionLocal() as other:session_svc.set_paused(other,'drill','user-a',True)
            return True
        settings.provider_mode='real'
        try:
            with patch.object(drill_svc,'verify_meaning',semantic):
                try:drill_svc.submit_drill_attempt(db,'drill','user-a','She has a book.','voice','pause')
                except AppError as e:code=e.code
        finally:settings.provider_mode='mock'
        db.expire_all();res=db.query(BudgetReservation).one();led=db.query(BudgetLedger).filter_by(scope='project').one()
        assert res.status=='reserved' and led.reserved>0
        return {'error':code,'operation':db.query(RequestOperation).one().status,'reservation':res.status,'reserved_project':led.reserved}

def turn_stale_budget():
    from app.services.coach import MockCoachProvider
    with SessionLocal() as db:
        db.add(User(id='user-a'));db.add(Session(id='turn',user_id='user-a',status='WAITING_INPUT',active_since=NOW(),duration_minutes=15));db.commit()
        class Coach(MockCoachProvider):
            def decide(self,context):
                with SessionLocal() as other:
                    session_svc.set_paused(other,'turn','user-a',True)
                    session_svc.set_paused(other,'turn','user-a',False)
                return super().decide(context)
        with patch.object(session_svc,'get_coach_provider',lambda:Coach()):
            try:session_svc.submit_turn(db,'turn','user-a','text',user_text='She have a book.',request_key='turn-pause')
            except AppError as e:code=e.code
        db.expire_all();res=db.query(BudgetReservation).one()
        assert code=='STALE_OPERATION' and res.status=='reserved'
        return {'error':code,'reservation':res.status,'saved_user_turns':db.query(Turn).count()}

def practice_sentence_mismatch():
    with SessionLocal() as db:
        completed(db)
        s=session_svc.create_sentence_practice(db,'user-a','source','source-t','create')
        r=session_svc.submit_practice(db,s['session_id'],'user-a',AUDIO,'I am a purple elephant.','mismatch')
        t=db.query(Turn).filter_by(session_id=s['session_id'],input_type='practice').one()
        assert t.practice_sentence=='I am a purple elephant.'
        return {'active_reference':db.get(Session,s['session_id']).practice_sentence,'accepted_reference':t.practice_sentence,'score':r['eval'].get('score')}

def nested_delete():
    with SessionLocal() as db:
        completed(db)
        s=session_svc.create_sentence_practice(db,'user-a','source','source-t','create')
        sid=s['session_id'];x=db.get(Session,sid);x.drill_stage='full_text';x.drill_target='She has a book.';db.commit()
        r=session_svc.submit_voice_intent(db,'user-a',sid,'drill_voice',sid,'voice',AUDIO,lambda text:drill_svc.submit_drill_attempt(db,sid,'user-a',text,'voice','voice'))
        nested=db.query(RequestOperation).filter_by(scope='drill_svc').one()
        nested_id=nested.id
        assert nested.session_id==''
        session_svc.delete_all_sessions(db,'user-a')
        cached=db.get(RequestOperation,nested_id)
        assert cached and cached.response_json and 'visible_help' in cached.response_json
        return {'remaining_operation_scope':cached.scope,'session_association':cached.session_id,'response_contains_learning_text':'She has a book.' in cached.response_json,'remaining_intents':db.query(IntentResult).count(),'session_count':db.query(Session).count()}

def pause_resume_state():
    with SessionLocal() as db:
        db.add(User(id='user-a'));db.add(Session(id='practice',user_id='user-a',status='PRACTICE',practice_sentence='She has a book.'));db.commit()
        session_svc.set_paused(db,'practice','user-a',True)
        resumed=session_svc.set_paused(db,'practice','user-a',False)
        assert resumed['status']=='WAITING_INPUT'
        return {'before_pause':'PRACTICE','after_resume':resumed['status']}

def recovery_already_done():
    with SessionLocal() as db:
        completed(db)
        db.add(RequestOperation(id='done',user_id='user-a',session_id='source',scope='turn',object_id='source',request_key='key',request_hash='hash',status='done',response_json='{"saved":true}'));db.commit()
        try:product_data.recover(db,'user-a','source','done')
        except AppError as e:
            assert e.code=='NOT_PENDING'
            return {'original_result_available':True,'create_recovery_error':e.code,'attempt_count':db.query(RecoveryAttempt).count()}
        raise AssertionError('recovery unexpectedly accepted')

for name,fn in [('derived_context',derived_context),('historical_window',window_leak),('late_asr_risk',late_risk),('drill_stage_race',drill_wrong_stage),('drill_stale_budget',drill_budget),('turn_stale_budget',turn_stale_budget),('practice_reference',practice_sentence_mismatch),('delete_nested_cache',nested_delete),('pause_resume_state',pause_resume_state),('recovery_already_done',recovery_already_done)]:run(name,fn)
(TMP/'results.json').write_text(json.dumps(RESULT,ensure_ascii=False,indent=2))
print(json.dumps({'isolation_dir':str(TMP),'note':'reproduced=True 只表示缺陷复现，不表示验收通过','results':RESULT},ensure_ascii=False,indent=2))

if not all(item["reproduced"] for item in RESULT):raise SystemExit(1)
