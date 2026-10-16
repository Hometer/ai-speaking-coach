"""第三次质检基线 890651b 的独立离线反例；仅临时库，不调用供应商。"""
import json,sys,tempfile,subprocess,os
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
ROOT=Path(os.environ.get('AI_AUDIT_ROOT',Path.cwd())).resolve()
assert subprocess.check_output(['git','rev-parse','--short','HEAD'],cwd=ROOT,text=True).strip()=='890651b','仅适用于原始质检基线'
sys.path.insert(0,str(ROOT/'backend'))
from app.core.config import settings
BASE=Path(tempfile.mkdtemp(prefix='speaking_q3_probe_'))
settings.provider_mode='mock';settings.seed_demo_invites=True;settings.qa_invite_codes=['qa-a']
settings.dashscope_api_key='';settings.xf_api_key='';settings.xf_api_secret='';settings.xf_appid=''
settings.database_url='sqlite:///'+str(BASE/'test.db');settings.data_dir=BASE/'data'
settings.budget_session=3.;settings.budget_user_day=10.;settings.budget_project_day=100.
from app.main import app
from app.models.database import Base,engine,SessionLocal
from app.models.entities import (User,Session,Turn,ReviewQueueItem,ReviewDueEvent,DrillAttempt,AsrRiskRecord,BudgetLedger,BudgetReservation,TrainingPlan,RequestOperation)
from app.core.auth import seed_invites
from app.core.errors import AppError
from app.services import session_svc as ss,drill_svc as ds,review_queue as qs,recall_service,training_plan as tp,cleanup,help_svc
from app.services.asr_risk import assess_risk
NOW=lambda:datetime.now(timezone.utc)
AUDIO=b'RIFF'+b'\0'*4+b'WAVE'+b'\0'*32
RESULT={}
def reset():
 Base.metadata.drop_all(engine);Base.metadata.create_all(engine);seed_invites()
 for child in settings.data_dir.rglob('*'):
  if child.is_file():child.unlink()
def source(db,scene='job_interview',target='Yesterday I went to work.'):
 db.add(User(id='user-a'))
 db.add(Session(id='source',user_id='user-a',status='ENDED',scene_id=scene,started_at=NOW()-timedelta(minutes=3),ended_at=NOW()-timedelta(minutes=1),review=json.dumps({'evidence':['origin'],'improvements':[{'turn_id':'origin','original':'Yesterday I go to work.','practice_sentence':target,'explanation_cn':'过去时纠错'}]})))
 db.add(Turn(id='origin',session_id='source',seq=1,input_type='voice',user_text='Yesterday I go to work.',eval_result='incomplete'))
 db.commit();return target

def case_delete_reference():
 reset()
 with SessionLocal() as db:
  target=source(db);sp=ss.create_sentence_practice(db,'user-a','source','origin','make');sid=sp['session_id']
  ss.submit_practice(db,sid,'user-a',AUDIO,target,'read')
  ss.delete_session(db,'source','user-a')
  detail=ss.get_session_detail(db,sid,'user-a');values=[t['practice_eval'].get('reference_sentence') for t in detail['turns'] if t['practice_eval']]
  assert detail['source_invalid'] and target in values
  return {'source_deleted':db.get(Session,'source') is None,'reference_in_derived_evaluation':values,'source_invalid':True}

def case_delete_upload():
 reset()
 with SessionLocal() as db:
  s=ss.create_session(db,'user-a','basic','easy',15,'hotel_checkin','make');sid=s['session_id']
  class ASR:
   def transcribe(self,path):return 'I have a reservation under the name Li.'
  with patch.object(ss,'get_asr_provider',lambda:ASR()):ss.submit_turn(db,sid,'user-a','voice',audio_bytes=AUDIO,request_key='say')
  before=list((settings.data_dir/'uploads').glob('*'));assert before
  ss.delete_session(db,sid,'user-a')
  remaining=[p.name for p in before if p.exists()]
  assert remaining
  return {'sessions_after_delete':db.query(Session).count(),'raw_recordings_remaining':len(remaining)}

def case_queue_pending():
 reset()
 with SessionLocal() as db:
  source(db);i=qs.add_to_queue(db,'user-a','source','origin')['id'];item=db.get(ReviewQueueItem,i);due=NOW()-timedelta(hours=1);item.due_at=due;db.query(ReviewDueEvent).filter_by(queue_item_id=i).update({'due_at':due});db.commit()
  with patch.object(recall_service,'verify_meaning',lambda *_:None):r=qs.submit_retest(db,'user-a',i,'Yesterday I travelled to my office.','voice','pending')
  db.refresh(item);assert r['retest_result']=='pending' and item.due_at.replace(tzinfo=timezone.utc)>NOW()
  return {'result':r['retest_result'],'old_due':due.isoformat(),'new_due':r['due_at'],'due_resolved_as':db.query(ReviewDueEvent).filter_by(queue_item_id=i).order_by(ReviewDueEvent.id).first().result}

def case_drill_material():
 reset()
 with SessionLocal() as db:
  source(db);sid=ss.create_sentence_practice(db,'user-a','source','origin','make')['session_id'];s=db.get(Session,sid);s.drill_stage='structure';db.commit();view=ds.drill_view(s,db)
  assert 'experience' in view['visible_help'] and 'work' not in view['visible_help']
  return {'reference':s.drill_target,'shown_structure':view['visible_help'],'shown_chunk':view['drill_chunk']}

def case_plan_pause():
 reset();now=NOW()
 with SessionLocal() as db:
  db.add(User(id='user-a'));db.commit()
  with patch.object(tp,'_now',lambda:now):plan=tp.create_or_renew_plan(db,'user-a','workplace',5)
  day=plan['days'][0];sid='completed'
  db.add(Session(id=sid,user_id='user-a',status='ENDED',practice_type='interview',scene_id=day['scene_id'],plan_id=plan['id'],plan_day=1,started_at=now-timedelta(minutes=3),ended_at=now,review=json.dumps({'evidence':['answer']})))
  db.add(Turn(id='answer',session_id=sid,input_type='voice',eval_result='independent'));db.commit()
  with patch.object(tp,'_now',lambda:now):before=tp.get_plan(db,'user-a')['days'][0];tp.pause_plan(db,'user-a')
  with patch.object(tp,'_now',lambda:now+timedelta(days=1)):after=tp.resume_plan(db,'user-a')['days'][0]
  assert before['status']=='completed' and after['status']=='pending'
  return {'before':before,'after':after,'original_completed_session_retained':db.get(Session,sid) is not None}

def case_expired_risk():
 reset()
 with SessionLocal() as db:
  sid=ss.create_session(db,'user-a','basic','easy',15,'hotel_checkin','make')['session_id']
  class ASR:
   def transcribe(self,path):return 'and'
  with patch.object(ss,'get_asr_provider',lambda:ASR()):hold=ss.submit_turn(db,sid,'user-a','voice',audio_bytes=AUDIO,request_key='risk')
  assert hold['risk_hold'];ss.set_paused(db,sid,'user-a',True);risk=db.query(AsrRiskRecord).one();risk.created_at=NOW()-timedelta(minutes=20);db.commit();cleanup.cleanup_old_recordings();db.refresh(risk);assert not risk.recording_url;ss.set_paused(db,sid,'user-a',False)
  r=ss.confirm_turn(db,sid,'user-a',risk.turn_id,'I have a reservation under the name Li.','confirm')
  turn=db.get(Turn,r['turn_id']);assert turn.input_type=='voice' and turn.eval_result=='independent'
  return {'recording_removed':True,'confirmation_result':turn.eval_result,'recorded_modality':turn.input_type,'new_voice_turns':db.query(Turn).filter_by(session_id=sid,input_type='voice').count()}

def case_tts_budget():
 reset();calls=[]
 with SessionLocal() as db:
  db.add(User(id='user-a'));db.add(Session(id='s',user_id='user-a',status='WAITING_INPUT'));db.add(Turn(id='t',session_id='s',reply_text='A question',audio_status='failed'));db.add(BudgetLedger(scope='session',scope_key='s',spent=3.));db.commit()
  from types import SimpleNamespace
  class TTS:
   ext='wav'
   def synthesize_result(self,text):calls.append(text);return SimpleNamespace(audio=None,attempts=1)
  with patch.object(ss,'get_tts_provider',lambda:TTS()):
   ss.synthesize_turn_audio(db,'s','user-a','t',True,'retry1');ss.synthesize_turn_audio(db,'s','user-a','t',True,'retry2')
  assert len(calls)==2 and db.query(BudgetReservation).count()==0
  return {'session_budget':3.,'already_spent':3.,'tts_calls_after_exhaustion':len(calls),'reservations':0}

def case_translate_late_budget():
 reset();calls=[]
 with SessionLocal() as db:
  sid=ss.create_session(db,'user-a','basic','easy',15,'hotel_checkin','make')['session_id'];spent=db.query(BudgetLedger).filter_by(scope='session',scope_key=sid).one().spent
  def translated(text):
   calls.append(text)
   with SessionLocal() as other:ss.set_paused(other,sid,'user-a',True)
   return 'I would like breakfast.'
  with patch.object(help_svc,'_translate',translated):
   try:help_svc.request_help(db,sid,'user-a','translate','我想吃早餐','translate')
   except AppError as e:code=e.code
   else:raise AssertionError('Expected stale rejection')
  db.expire_all();reservation=db.query(BudgetReservation).order_by(BudgetReservation.created_at.desc()).first();after=db.query(BudgetLedger).filter_by(scope='session',scope_key=sid).one().spent
  assert calls and reservation.status=='released' and not reservation.provider_started and after==spent
  return {'rejected_as':code,'translation_called':len(calls),'reservation_status':reservation.status,'provider_started':reservation.provider_started,'spent_increase':after-spent}

def case_drill_end_time():
 reset()
 with SessionLocal() as db:
  source(db);sid=ss.create_sentence_practice(db,'user-a','source','origin','make')['session_id'];s=db.get(Session,sid);s.drill_stage='transfer';db.commit()
  with patch.object(ds,'verify_new_condition',lambda *_:True):r=ds.submit_drill_attempt(db,sid,'user-a','In my school project I built a tool that saved time.','voice','pass')
  db.refresh(s);assert r['result']=='transfer' and s.status=='ENDED' and s.ended_at is None
  return {'result':r['result'],'status':s.status,'ended_at':s.ended_at,'end_reason':s.end_reason}

def case_drill_risk_bypass():
 reset()
 with SessionLocal() as db:
  target=source(db);sid=ss.create_sentence_practice(db,'user-a','source','origin','make')['session_id'];transcript=target+' 噪声'*30
  risk,reason=assess_risk(transcript);assert risk=='high'
  with patch.object(ss,'transcribe_voice_bytes',lambda _:transcript):r=ss.submit_voice_intent(db,'user-a',sid,'drill_voice',sid,'risk',AUDIO,lambda text:ds.submit_drill_attempt(db,sid,'user-a',text,'voice','risk'))
  assert not r.get('risk_hold') and r['next_stage']=='fill_blanks' and db.query(AsrRiskRecord).count()==0
  return {'assessed_risk':risk,'reason':reason,'actual_result':r['result'],'next_stage':r['next_stage'],'risk_records':0}

def case_plan_delete_missing():
 reset()
 from fastapi.testclient import TestClient
 with TestClient(app,raise_server_exceptions=False) as client:
  headers={'X-Invite-Code':'test-a'}
  made=client.post('/api/v1/training-plan',json={'goal_type':'travel','available_minutes':5},headers=headers)
  assert made.status_code==200
  removed=client.delete('/api/v1/training-plan',headers=headers)
  kept=client.get('/api/v1/training-plan',headers=headers)
  assert removed.status_code==405 and kept.json()['id']==made.json()['id']
  return {'delete_status':removed.status_code,'plan_still_present':True,'delete_declared':any('delete' in value for path,value in app.openapi()['paths'].items() if 'training-plan' in path)}

def case_single_sentence_audio_not_saved():
 reset()
 with SessionLocal() as db:
  source(db);sid=ss.create_sentence_practice(db,'user-a','source','origin','make')['session_id']
  for _ in range(3):ds.drill_action(db,sid,'user-a','skip')
  before=set((settings.data_dir/'audio').glob('*'))
  r=ss.submit_voice_intent(db,'user-a',sid,'drill_voice',sid,'say',AUDIO,lambda text:ds.submit_drill_attempt(db,sid,'user-a',text,'voice','say'))
  detail=ss.get_session_detail(db,sid,'user-a');own=[t['recording_url'] for t in detail['turns'] if t.get('recording_url')]
  attempts=db.query(DrillAttempt).filter_by(drill_session_id=sid,input_type='voice').count()
  assert attempts==1 and not own and not set((settings.data_dir/'audio').glob('*'))-before
  return {'voice_attempts':attempts,'own_recordings_in_detail':own,'new_audio_files':0,'practice_evaluations':sum(bool(t['practice_eval']) for t in detail['turns']),'result':r['result']}

def case_invalid_word_evidence():
 reset()
 from app.services.practice import parse_result
 xml='<xml><read_sentence total_score="4"><word beg_pos="bad" end_pos="-1" total_score="999">unrelated</word><word total_score="nan">ghost</word></read_sentence></xml>'
 result=parse_result([{'code':0,'data':xml}]);items=result['word_items']
 assert any(i['word']=='unrelated' and i['score']==999 and i['beg_pos']=='bad' for i in items)
 import math
 assert any(not math.isfinite(i['score']) for i in items)
 return {'whole_score':result['score'],'invalid_word_items':[{'word':i['word'],'score':str(i['score']),'begin':i['beg_pos'],'end':i['end_pos']} for i in items]}

def case_follow_practice_loses_scene():
 reset()
 from app.services.coach import MockCoachProvider
 contexts=[]
 class Coach(MockCoachProvider):
  def decide(self,context):contexts.append(context.copy());return super().decide(context)
 with SessionLocal() as db:
  db.add(User(id='user-a',level='beginner'));db.add(Session(id='hotel',user_id='user-a',status='PRACTICE',scene_id='hotel_checkin',level='beginner',goal_text='Confirm a reservation.'))
  target='I have a reservation under the name Li.'
  db.add(Turn(id='ref',session_id='hotel',seq=1,input_type='coach',action_type='suggest_practice',practice_sentence=target));db.commit()
  with patch.object(ss,'get_coach_provider',lambda:Coach()):r=ss.submit_practice(db,'hotel','user-a',AUDIO,target,'read')
  assert 'experience' in r['recheck_reply_text'] and 'scene_id' not in contexts[0] and 'level' not in contexts[0]
  s=db.get(Session,'hotel');s.status='PRACTICE';db.commit()
  with patch.object(ss,'get_coach_provider',lambda:Coach()):skipped=ss.skip_practice(db,'hotel','user-a','skip')
  assert 'experience' in skipped['reply_text']
  return {'selected_scene':'hotel_checkin','selected_level':'beginner','practice_context_keys':sorted(contexts[0]),'practice_reply':r['recheck_reply_text'],'skip_context_keys':sorted(contexts[1]),'skip_reply':skipped['reply_text']}

for key,fn in [('delete_reference',case_delete_reference),('delete_upload',case_delete_upload),('queue_pending',case_queue_pending),('drill_material',case_drill_material),('plan_pause',case_plan_pause),('expired_risk',case_expired_risk),('tts_budget',case_tts_budget),('translate_late_budget',case_translate_late_budget),('drill_end_time',case_drill_end_time),('drill_risk_bypass',case_drill_risk_bypass),('plan_delete_missing',case_plan_delete_missing),('single_sentence_audio_not_saved',case_single_sentence_audio_not_saved),('invalid_word_evidence',case_invalid_word_evidence),('follow_practice_loses_scene',case_follow_practice_loses_scene)]:
 try:RESULT[key]={'reproduced':True,'facts':fn()}
 except Exception as e:
  import traceback
  RESULT[key]={'reproduced':False,'exception':type(e).__name__,'detail':str(e)};traceback.print_exc()
Path('/tmp/speaking_q3_probe.json').write_text(json.dumps(RESULT,ensure_ascii=False,indent=2,default=str))
print(json.dumps(RESULT,ensure_ascii=False,indent=2,default=str));print('temporary_database',BASE)
assert all(r['reproduced'] for r in RESULT.values())
