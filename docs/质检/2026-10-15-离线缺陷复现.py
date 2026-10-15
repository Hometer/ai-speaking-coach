"""PRD v0.10 离线审计复现。仅使用临时 SQLite、Mock provider 与故障注入。
从任意目录用项目 .venv/bin/python 运行；不会访问现有业务数据库或调用供应商。
断言为“已发现缺陷仍可复现”，不表示产品验收通过。
仅适用于历史基线 cbca61a。修复后的行为回归见 backend/tests/test_audit_repairs.py、test_product_evidence.py；不要将本脚本的旧缺陷断言作为新版本通过条件。
"""
import sys, pathlib, tempfile, json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT/'backend'))
from app.core.config import settings
root=pathlib.Path(tempfile.mkdtemp(prefix='speaking_repro_20261015_'))
settings.provider_mode='mock'; settings.seed_demo_invites=True
settings.data_dir=root/'data'; settings.database_url=f'sqlite:///{root/"audit.db"}'
from app.main import app
from app.models.database import SessionLocal
from app.models.entities import User, Session, Turn, Memory, ReviewQueueItem, Evaluation
from app.services import session_svc, review_queue, memory, daily_review_svc, drill_svc, progress, training_plan
from app.services.coach import MockCoachProvider
from app.services.recall_judge import judge_recall
results={}
now=datetime.now(timezone.utc)
target='I booked a room for two nights'
wrong='I never booked a room for two nights.'
results['recall_semantics']={'target':target,'contradictory_answer':wrong,'result':judge_recall(wrong,target,'voice',False),'synonym_answer':'My reservation is for a couple of evenings.','synonym_result':judge_recall('My reservation is for a couple of evenings.',target,'voice',False)}
with SessionLocal() as db:
 db.add(User(id='audit-u')); db.add(Session(id='source',user_id='audit-u',scene_id='hotel_checkin',status='ENDED',review=json.dumps({'improvements':[{'turn_id':'source-turn','original':'room','practice_sentence':target,'explanation_cn':'说明预订两晚'}]})))
 db.add(Turn(id='source-turn',session_id='source',seq=1,input_type='voice',user_text='room',eval_result='incomplete')); db.commit()
 item=ReviewQueueItem(user_id='audit-u',source_session_id='source',source_turn_id='source-turn',suggestion_sentence=target,due_at=now-timedelta(hours=1))
 db.add(item); db.commit()
 first=review_queue.submit_retest(db,'audit-u',item.id,wrong,'voice','a1')
 with patch.object(review_queue,'_now',return_value=now+timedelta(hours=25)):
  second=review_queue.submit_retest(db,'audit-u',item.id,wrong,'voice','a2')
 results['queue_false_mastery']={'first':{k:first[k] for k in ('status','success_count','retest_result')},'second':{k:second[k] for k in ('status','success_count','retest_result')}}
 helped=ReviewQueueItem(user_id='audit-u',source_session_id='source',source_turn_id='source-turn',suggestion_sentence=target,due_at=now-timedelta(hours=1)); db.add(helped);db.commit()
 review_queue.reveal_item(db,'audit-u',helped.id)
 first=review_queue.submit_retest(db,'audit-u',helped.id,target,'voice','h1')
 with patch.object(review_queue,'_now',return_value=now+timedelta(hours=25)):
  second=review_queue.submit_retest(db,'audit-u',helped.id,target,'voice','h2')
 results['help_persists_next_day']={k:second[k] for k in ('help_shown','suggestion_sentence','retest_result','success_count')}
 # Multiple sources merge into one row but the second source is never stored.
 db.add(Session(id='memory-source2',user_id='audit-u',scene_id='hotel_checkin',status='ENDED'));db.commit()
 review={'improvements':[{'turn_id':'source-turn','explanation_cn':'practice booking details'}]}
 memory.generate_candidates(db,'audit-u','source',review); memory.generate_candidates(db,'audit-u','memory-source2',review)
 before=[(m.status,m.evidence_count) for m in memory.get_usable_memories(db,'audit-u','hotel_checkin')]
 session_svc.delete_session(db,'memory-source2','audit-u')
 after=[(m.status,m.evidence_count) for m in memory.get_usable_memories(db,'audit-u','hotel_checkin')]
 results['merged_memory_source_delete']={'before':before,'after_deleting_second_source':after}
 # Already finished sentence practice is reused forever.
 sp=session_svc.create_sentence_practice(db,'audit-u','source','source-turn')
 session_svc.end_session(db,sp['session_id'],'audit-u','user_ended')
 again=session_svc.create_sentence_practice(db,'audit-u','source','source-turn')
 results['sentence_cannot_restart']={'same_id':sp['session_id']==again['session_id'],'new_request_status':again['status']}
 # Pausing does not protect drill attempt submission.
 db.add(Session(id='paused-drill',user_id='audit-u',status='PAUSED',practice_type='sentence_practice',drill_stage='free_speak',drill_target=target,source_session_id='source',source_turn_id='source-turn'));db.commit()
 dr=drill_svc.submit_drill_attempt(db,'paused-drill','audit-u',target,'voice','paused-attempt')
 results['paused_drill_advances']={'status':db.get(Session,'paused-drill').status,'result':dr['result'],'next_stage':dr['next_stage']}
 # Practice late response overwrites terminal status set in another request.
 db.add(User(id='late-u'));db.add(Session(id='late-practice',user_id='late-u',status='PRACTICE',scene_id='hotel_checkin'));db.commit()
 ended_during=[]
 class LateEval:
  def evaluate(self,path,sentence):
   with SessionLocal() as other:
    session_svc.end_session(other,'late-practice','late-u','user_ended'); ended_during.append(other.get(Session,'late-practice').status)
   return {'score':90,'dimension':'sentence','word_items':[]}
 audio=b'RIFF'+b'\x00'*4+b'WAVE'+b'\x00'*32
 with patch.object(session_svc,'get_practice_eval_provider',return_value=LateEval()):
  late=session_svc.submit_practice(db,'late-practice','late-u',audio,target,'late-key')
 db.expire_all()
 results['late_practice_reopens_ended']={'state_during_evaluation':ended_during[-1],'state_after_late_result':db.get(Session,'late-practice').status,'response_status':late['status']}
 # Progress counts empty and stale reviews as completed ordinary sessions.
 db.add(User(id='progress-u')); db.add(Session(id='empty-ended',user_id='progress-u',status='ENDED',scene_id='hotel_checkin',review='{}')); db.commit()
 results['invalid_review_counts_completed']={'no_user_turns':db.query(Turn).filter_by(session_id='empty-ended').count(),'completed_count':progress.get_progress(db,'progress-u')['completed_count']}
 # Record a plan completion with no user answer.
 plan=training_plan.create_or_renew_plan(db,'plan-u','travel',5)
 planned=session_svc.create_session(db,'plan-u','basic','standard',scene_id='hotel_checkin',plan_id=plan['id'],plan_day=1)
 session_svc.end_session(db,planned['session_id'],'plan-u','user_ended')
 results['empty_practice_completes_plan']={'user_turns':db.query(Turn).filter(Turn.session_id==planned['session_id'],Turn.input_type.in_(['voice','text'])).count(),'day_status':training_plan.get_plan(db,'plan-u')['days'][0]['status']}

from app.services import budget
from app.models.entities import BudgetLedger
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
with SessionLocal() as db:
 first=session_svc.create_session(db,'level-u','beginner','simple')['session_id']
 second=session_svc.create_session(db,'level-u','intermediate','standard')['session_id']
 captured=[]
 class Capture(MockCoachProvider):
  def decide(self,c):captured.append(c);return super().decide(c)
 with patch.object(session_svc,'get_coach_provider',return_value=Capture()):
  session_svc.submit_turn(db,second,'level-u','text',user_text='hello',request_key='capture')
 results['selected_level_ignored']={'session_level':db.get(Session,second).level,'coach_level':captured[-1]['level']}
 # Concurrent same-key create calls both run coach before unique constraint fires.
 barrier=Barrier(2); calls=[]
 class SlowCoach(MockCoachProvider):
  def opening(self,scene,level):
   calls.append('opening');barrier.wait(timeout=5);return super().opening(scene,level)
 db.add(User(id='concurrent-u'));db.commit()
 def run_create():
  with SessionLocal() as other:
   try:
    r=session_svc.create_session(other,'concurrent-u','basic','standard',request_key='same-intent')
    return {'ok':True,'session_id':r['session_id']}
   except Exception as e:return {'ok':False,'exception':type(e).__name__}
 with patch.object(session_svc,'get_coach_provider',return_value=SlowCoach()):
  with ThreadPoolExecutor(max_workers=2) as pool:
   answers=list(pool.map(lambda _:run_create(),range(2)))
 results['concurrent_create_duplicate_external_calls']={'provider_opening_calls':len(calls),'request_outcomes':answers}
 # settle/release are read-modify-write, not atomically incremented.
 guard=budget.BudgetGuard(db);guard.reserve(.1,'settle-s','settle-u');guard.reserve(.1,'settle-s','settle-u')
 a=SessionLocal();b=SessionLocal();ga=budget.BudgetGuard(a);gb=budget.BudgetGuard(b)
 # Preload rows in both ORM identity maps, exactly as concurrent settle calls can do.
 keys=[('session','settle-s'),('user',budget._day_key('settle-u')),('project',budget._day_key('all'))]
 rows_a=[ga._row(*key) for key in keys];rows_b=[gb._row(*key) for key in keys]
 ga.settle(.1,'settle-s','settle-u');gb.settle(.1,'settle-s','settle-u');a.close();b.close();db.expire_all()
 ledger=db.query(BudgetLedger).filter_by(scope='session',scope_key='settle-s').one()
 results['settle_lost_update']={'reserved_two_calls':.2,'settled_two_calls_expected':.2,'observed_spent':ledger.spent,'remaining_reserved':ledger.reserved}

with SessionLocal() as db:
 turn = db.query(Turn).filter_by(session_id='late-practice', input_type='practice').one()
 detail = session_svc.get_session_detail(db, 'late-practice', 'late-u')
 saved = next(t for t in detail['turns'] if t['turn_id'] == turn.id)
 results['practice_score_missing_on_reload'] = {'database_has_evaluation': bool(turn.practice_eval), 'detail_has_evaluation': 'practice_eval' in saved}

assert results['recall_semantics']['result'] == 'independent'
assert results['queue_false_mastery']['second']['status'] == 'mastered'
assert results['help_persists_next_day']['retest_result'] == 'hinted'
assert results['merged_memory_source_delete']['after_deleting_second_source'] == [('usable', 2)]
assert results['sentence_cannot_restart']['new_request_status'] == 'ENDED'
assert results['paused_drill_advances']['result'] == 'independent'
assert results['late_practice_reopens_ended']['state_after_late_result'] == 'WAITING_INPUT'
assert results['invalid_review_counts_completed']['completed_count'] == 1
assert results['empty_practice_completes_plan']['day_status'] == 'completed'
assert results['selected_level_ignored']['coach_level'] != results['selected_level_ignored']['session_level']
assert results['concurrent_create_duplicate_external_calls']['provider_opening_calls'] == 2
assert results['settle_lost_update']['observed_spent'] == .1
assert results['practice_score_missing_on_reload'] == {'database_has_evaluation': True, 'detail_has_evaluation': False}
print(json.dumps(results, ensure_ascii=False, indent=2))
print(f"Reproduced {len(results)} audit probes. These are defects, not passing product acceptance checks.")
print('ISOLATED_REPRO_ROOT=' + str(root))
