"""2026-10-15 审计反例的行为回归；临时库、明确 Mock 语音/供应商替身。"""
import json
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from app.core.errors import AppError
from app.models.database import SessionLocal
from app.models.entities import User, Session, Turn, Memory, BudgetLedger, RequestOperation, ReviewQueueItem
from app.services import session_svc, memory, review_queue, drill_svc, progress, training_plan, budget
from app.services.coach import MockCoachProvider
from app.services.operations import recover_interrupted
from app.services.recall_judge import judge_recall


def source(db, sid='source', user='u'):
    if db.get(User, user) is None:
        db.add(User(id=user))
    target = 'I booked a room for two nights'
    db.add(Session(id=sid, user_id=user, status='ENDED', scene_id='hotel_checkin', review=json.dumps({
        'evidence':[sid+'-turn'], 'improvements':[{'turn_id':sid+'-turn','original':'room','practice_sentence':target,'explanation_cn':'确认预订'}]})))
    db.add(Turn(id=sid+'-turn',session_id=sid,input_type='voice',user_text='room',eval_result='incomplete'))
    db.commit()
    return target


def test_recall_never_infers_semantics_from_overlap():
    target='I booked a room for two nights'
    assert judge_recall('I never booked a room for two nights.',target,'voice',False) == 'pending'
    assert judge_recall(target,target,'voice',False) == 'pending'  # 还缺独立语义证据
    assert judge_recall('My reservation is for a couple of evenings.',target,'voice',False,True) == 'independent'
    assert judge_recall(target,target,'text',False,True) == 'text_completed'
    assert judge_recall(target,target,'voice',True,True) == 'hinted'


def test_semantic_result_requires_boolean_fields(monkeypatch):
    from app.services import semantic_eval
    from app.core.config import settings
    from types import SimpleNamespace as O
    monkeypatch.setattr(settings,'provider_mode','real')
    monkeypatch.setattr(semantic_eval,'call_generation',lambda **kw:O(output=O(choices=[O(message=O(content='{"same_meaning":"yes","sufficient":true}'))])))
    assert semantic_eval.verify_meaning('hello','hello') is None
    monkeypatch.setattr(semantic_eval,'call_generation',lambda **kw:O(output=O(choices=[O(message=O(content='{"same_meaning":true,"sufficient":true}'))])))
    assert semantic_eval.verify_meaning('I booked two nights','My reservation is for a couple of evenings') is True


def test_queue_requires_new_condition_before_mastery(monkeypatch):
    from app.services import recall_service
    with SessionLocal() as db:
        target=source(db)
        item=ReviewQueueItem(user_id='u',source_session_id='source',source_turn_id='source-turn',suggestion_sentence=target,due_at=datetime.now(timezone.utc)-timedelta(hours=1))
        db.add(item);db.commit()
        first=review_queue.submit_retest(db,'u',item.id,target,'voice','recall')
        assert first['retest_result']=='recall_completed' and first['success_count']==0
        assert first['attempt_stage']=='transfer' and first['question_text']
        monkeypatch.setattr(recall_service,'verify_new_condition',lambda *args:True)
        second=review_queue.submit_retest(db,'u',item.id,'My reservation is now for three nights.','voice','transfer')
        assert second['success_count']==1 and second['status']=='awaiting_retest'
        assert second['help_shown'] is False
        assert second['attempt_stage']=='recall'


def test_help_exposure_is_per_due_cycle():
    with SessionLocal() as db:
        target=source(db)
        item=ReviewQueueItem(user_id='u',source_session_id='source',source_turn_id='source-turn',suggestion_sentence=target,due_at=datetime.now(timezone.utc)-timedelta(hours=1))
        db.add(item);db.commit()
        review_queue.reveal_item(db,'u',item.id)
        result=review_queue.submit_retest(db,'u',item.id,target,'voice','helped')
        assert result['retest_result']=='hinted' and result['success_count']==0
        assert result['help_shown'] is False and result['suggestion_sentence']==''


def test_paused_drill_cannot_advance():
    with SessionLocal() as db:
        target=source(db)
        db.add(Session(id='drill',user_id='u',status='PAUSED',practice_type='sentence_practice',drill_stage='free_speak',drill_target=target))
        db.commit()
        with pytest.raises(AppError):
            drill_svc.submit_drill_attempt(db,'drill','u',target,'voice','pause')
        assert db.get(Session,'drill').drill_stage=='free_speak'
        with pytest.raises(AppError):
            drill_svc.drill_action(db,'drill','u','skip')


def test_late_practice_cannot_reopen_session(monkeypatch):
    with SessionLocal() as db:
        source(db)
        db.add(Session(id='late',user_id='u',status='PRACTICE',scene_id='hotel_checkin'))
        db.add(Turn(id="late-reference",session_id="late",input_type="coach",action_type="suggest_practice",practice_sentence="test sentence"))
        db.commit()
        class Eval:
            def evaluate(self,*args):
                with SessionLocal() as other:
                    session_svc.end_session(other,'late','u','user_ended')
                return {'score':90,'word_items':[]}
        monkeypatch.setattr(session_svc,'get_practice_eval_provider',lambda:Eval())
        with pytest.raises(AppError):
            session_svc.submit_practice(db,'late','u',b'RIFF'+b'\0'*4+b'WAVE'+b'\0'*32,'test sentence','late')
        db.expire_all()
        assert db.get(Session,'late').status=='ENDED'
        assert db.query(Turn).filter_by(session_id='late',input_type='practice').count()==0


def test_concurrent_same_intent_calls_provider_once(monkeypatch):
    entered,release=Event(),Event();calls=[]
    class Coach(MockCoachProvider):
        def opening(self,*args):
            calls.append(1);entered.set();assert release.wait(3)
            return super().opening(*args)
    monkeypatch.setattr(session_svc,'get_coach_provider',lambda:Coach())
    with SessionLocal() as db:
        db.add(User(id='concurrent'));db.commit()
    def create():
        with SessionLocal() as db:
            return session_svc.create_session(db,'concurrent','basic','standard',request_key='same')
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(create)
        assert entered.wait(3)
        try:
            with pytest.raises(AppError) as exc:
                create()
            assert exc.value.code=='REQUEST_IN_PROGRESS'
        finally:
            release.set()
        saved=first.result(timeout=3)
    assert len(calls)==1
    assert create()==saved


def test_interrupted_operation_survives_recovery():
    with SessionLocal() as db:
        db.add(RequestOperation(id='interrupted',user_id='u',scope='turn',object_id='s',session_id='s',request_key='k',request_hash='digest',status='running'))
        db.commit();recover_interrupted(db)
        row=db.get(RequestOperation,'interrupted')
        assert row.status=='uncertain' and row.error_code=='REQUEST_UNCERTAIN'


def test_budget_settlements_do_not_overwrite_orm_snapshots():
    with SessionLocal() as a, SessionLocal() as b:
        ga,gb=budget.BudgetGuard(a),budget.BudgetGuard(b)
        ga.reserve(.1,'s','u');gb.reserve(.1,'s','u')
        rows_a=[ga._row('session','s')];rows_b=[gb._row('session','s')]
        ga.settle(.1,'s','u');gb.settle(.1,'s','u')
        a.expire_all();row=a.query(BudgetLedger).filter_by(scope='session',scope_key='s').one()
        assert row.spent==pytest.approx(.2) and row.reserved==pytest.approx(0)
        ga.settle(.1,'s','u')
        assert row.spent==pytest.approx(.2)


def test_budget_release_uses_original_day(monkeypatch):
    with SessionLocal() as db:
        guard=budget.BudgetGuard(db)
        monkeypatch.setattr(budget,'_day_key',lambda key:f'{key}:2026-10-15')
        guard.reserve(.1,'s','u')
        monkeypatch.setattr(budget,'_day_key',lambda key:f'{key}:2026-10-16')
        guard.settle(.1,'s','u')
        row=db.query(BudgetLedger).filter_by(scope='user',scope_key='u:2026-10-15').one()
        assert row.reserved==0 and row.spent==pytest.approx(.1)


def test_merged_memory_recomputes_after_source_delete():
    with SessionLocal() as db:
        source(db);source(db,'second')
        for sid in ('source','second'):
            memory.generate_candidates(db,'u',sid,{'improvements':[{'turn_id':sid+'-turn','explanation_cn':'same advice'}]})
        assert len(memory.get_usable_memories(db,'u','hotel_checkin'))==1
        session_svc.delete_session(db,'second','u')
        assert memory.get_usable_memories(db,'u','hotel_checkin')==[]
        assert memory.list_memories(db,'u')[0].evidence_count==1


def test_each_session_uses_its_selected_level(monkeypatch):
    captured=[]
    class Coach(MockCoachProvider):
        def decide(self,context):captured.append(context['level']);return super().decide(context)
    monkeypatch.setattr(session_svc,'get_coach_provider',lambda:Coach())
    with SessionLocal() as db:
        session_svc.create_session(db,'u','beginner','simple')
        s=session_svc.create_session(db,'u','intermediate','standard')
        session_svc.submit_turn(db,s['session_id'],'u','text',user_text='hello',request_key='level')
        assert captured[-1]=='intermediate'


def test_sentence_restart_creates_new_intent():
    with SessionLocal() as db:
        source(db)
        first=session_svc.create_sentence_practice(db,'u','source','source-turn','intent1')
        session_svc.end_session(db,first['session_id'],'u','user_ended')
        assert session_svc.create_sentence_practice(db,'u','source','source-turn','intent1')==first
        second=session_svc.create_sentence_practice(db,'u','source','source-turn','intent2')
        assert first['session_id']!=second['session_id'] and second['status']!='ENDED'


def test_empty_or_stale_practice_does_not_complete_plan_or_progress():
    with SessionLocal() as db:
        p=training_plan.create_or_renew_plan(db,'u','travel',5)
        s=session_svc.create_session(db,'u','basic','standard',scene_id='hotel_checkin',plan_id=p['id'],plan_day=1)
        session_svc.end_session(db,s['session_id'],'u','user_ended')
        assert training_plan.get_plan(db,'u')['days'][0]['status']=='pending'
        assert progress.get_progress(db,'u')['completed_count']==0


def test_saved_practice_evaluation_is_returned():
    with SessionLocal() as db:
        source(db)
        db.add(Turn(id='practice',session_id='source',input_type='practice',practice_eval=json.dumps({'score':85,'mock':True})))
        db.commit()
        detail=session_svc.get_session_detail(db,'source','u')
        assert next(t for t in detail['turns'] if t['turn_id']=='practice')['practice_eval']['score']==85


@pytest.mark.parametrize('method', ['skip_practice', 'request_hint'])
def test_legacy_provider_actions_also_ignore_late_results(monkeypatch, method):
    with SessionLocal() as db:
        source(db)
        db.add(Session(id='late-help',user_id='u',status='PRACTICE'));db.commit()
        class Coach(MockCoachProvider):
            def decide(self, context):
                with SessionLocal() as other:
                    session_svc.end_session(other,'late-help','u','user_ended')
                return super().decide(context)
        monkeypatch.setattr(session_svc,'get_coach_provider',lambda:Coach())
        with pytest.raises(AppError):
            getattr(session_svc,method)(db,'late-help','u','request')
        db.expire_all()
        assert db.get(Session,'late-help').status=='ENDED'
        assert db.query(Turn).filter_by(session_id='late-help').count()==0


def test_real_usage_commit_does_not_commit_late_recall_evidence(monkeypatch):
    from app.core.config import settings
    from app.models.entities import RecallEvidence
    from app.services import recall_service, daily_review_svc
    with SessionLocal() as db:
        target=source(db)
        item=ReviewQueueItem(user_id='u',source_session_id='source',source_turn_id='source-turn',suggestion_sentence=target,due_at=datetime.now(timezone.utc)-timedelta(hours=1))
        db.add(item);db.commit()
        daily=daily_review_svc.start_daily_review(db,'u')['session_id']
        def late(*args):
            with SessionLocal() as other:
                daily_review_svc.control_action(other,daily,'u','end')
            return True
        monkeypatch.setattr(settings,'provider_mode','real')
        monkeypatch.setattr(recall_service,'verify_meaning',late)
        with pytest.raises(AppError):
            daily_review_svc.submit_attempt(db,daily,'u',target,'voice','late')
        db.expire_all()
        assert db.query(RecallEvidence).count()==0
        assert db.get(ReviewQueueItem,item.id).attempt_stage=='recall'


def test_source_dispute_invalidates_cached_replay_and_memory():
    from app.models.entities import IntentResult
    with SessionLocal() as db:
        source(db)
        memory.generate_candidates(db,'u','source',{'improvements':[{'turn_id':'source-turn','explanation_cn':'advice'}]})
        child=session_svc.create_sentence_practice(db,'u','source','source-turn','create-child')
        session_svc.dispute_review_improvement(db,'source','u','source-turn')
        with pytest.raises(AppError) as exc:
            session_svc.create_sentence_practice(db,'u','source','source-turn','create-child')
        assert exc.value.code=='SOURCE_INVALID'
        assert db.get(Session,child['session_id']).source_invalid
        assert memory.list_memories(db,'u')==[]
