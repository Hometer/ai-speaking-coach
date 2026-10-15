"""第二次质检反例的正确行为；临时数据库与供应商替身，无付费调用。"""
import json
from datetime import datetime, timedelta, timezone
import pytest
from app.core.config import settings
from app.core.errors import AppError
from app.models.database import SessionLocal, migrate_db
from app.models.entities import (User, Session, Turn, DrillAttempt, RequestOperation, BudgetLedger,
    BudgetReservation, AsrRiskRecord, ReviewSurvey, ReviewSurveyAnswer, RecoveryAttempt, ProductEvent)
from app.services import session_svc, drill_svc, product_metrics, product_data, review_queue, phrase_card_svc
from app.services.coach import MockCoachProvider
from app.services.practice_materials import material_for

NOW=lambda:datetime.now(timezone.utc)
AUDIO=b'RIFF'+b'\0'*4+b'WAVE'+b'\0'*32
H={'X-Invite-Code':'test-a'}


def source(db, sid='source', uid='user-a', scene='hotel_checkin', target='I booked a room for two nights'):
    if not db.get(User,uid):db.add(User(id=uid))
    when=NOW()-timedelta(minutes=1)
    db.add(Session(id=sid,user_id=uid,status='ENDED',scene_id=scene,started_at=when-timedelta(minutes=2),ended_at=when,
        review=json.dumps({'evidence':[sid+'-t'],'improvements':[{'turn_id':sid+'-t','original':'room',
        'practice_sentence':target,'explanation_cn':'纠错说明，不是中文意图'}]})))
    db.add(Turn(id=sid+'-t',session_id=sid,input_type='voice',eval_result='incomplete',user_text='room',created_at=when))
    db.commit();return target


def drill(db, stage='free_speak'):
    target=source(db)
    db.add(Session(id='drill',user_id='user-a',status='PRACTICE',scene_id='hotel_checkin',practice_type='sentence_practice',
        source_session_id='source',source_turn_id='source-t',drill_stage=stage,drill_target=target,practice_sentence=target))
    db.commit();return target


@pytest.mark.parametrize('delete_all',[False,True])
def test_nested_operations_keep_owner_and_delete_all_text_copies(monkeypatch,delete_all):
    with SessionLocal() as db:
        target=drill(db,'full_text')
        monkeypatch.setattr(session_svc,'transcribe_voice_bytes',lambda data:target)
        session_svc.submit_voice_intent(db,'user-a','drill','drill_voice','drill','nested',AUDIO,
            lambda text:drill_svc.submit_drill_attempt(db,'drill','user-a',text,'voice','nested'))
        rows=db.query(RequestOperation).filter_by(session_id='drill').all()
        assert len(rows)==2
        child=next(r for r in rows if r.scope=='drill_svc');parent=next(r for r in rows if r.scope=='voice')
        assert child.parent_id==parent.id and child.response_json
        if delete_all:session_svc.delete_all_sessions(db,'user-a')
        else:session_svc.delete_session(db,'drill','user-a')
        assert not db.query(RequestOperation).filter(RequestOperation.response_json.contains(target)).first()
        assert not db.query(RequestOperation).filter_by(session_id='drill').first()


def test_source_dispute_revokes_nested_replay(monkeypatch):
    with SessionLocal() as db:
        target=drill(db,'full_text')
        monkeypatch.setattr(session_svc,'transcribe_voice_bytes',lambda data:target)
        send=lambda:session_svc.submit_voice_intent(db,'user-a','drill','drill_voice','drill','nested',AUDIO,
            lambda text:drill_svc.submit_drill_attempt(db,'drill','user-a',text,'voice','nested'))
        send();session_svc.dispute_review_improvement(db,'source','user-a','source-t')
        with pytest.raises(AppError) as error:send()
        assert error.value.code=='SOURCE_INVALID'
        assert all(not r.response_json for r in db.query(RequestOperation).filter_by(session_id='drill').all())


def test_migration_recovers_legacy_nested_session_association():
    with SessionLocal() as db:
        drill(db)
        db.add(RequestOperation(id='legacy',user_id='user-a',scope='drill_svc',object_id='drill',session_id='',
            request_key='k',request_hash='hash',status='done',response_json='{"visible_help":"old"}'))
        db.commit()
    migrate_db();migrate_db()
    with SessionLocal() as db:
        row=db.get(RequestOperation,'legacy')
        assert row.session_id=='drill' and row.parent_id=='legacy'
        session_svc.delete_session(db,'drill','user-a')
        assert db.get(RequestOperation,'legacy') is None


@pytest.mark.parametrize('action',['downgrade','skip','jump'])
def test_changing_drill_stage_rejects_late_result(monkeypatch,action):
    with SessionLocal() as db:
        target=drill(db)
        def semantic(*args):
            with SessionLocal() as other:drill_svc.drill_action(other,'drill','user-a',action)
            return True
        monkeypatch.setattr(drill_svc,'verify_meaning',semantic)
        with pytest.raises(AppError) as error:drill_svc.submit_drill_attempt(db,'drill','user-a',target,'voice','late')
        assert error.value.code=='STALE_OPERATION'
        assert db.query(DrillAttempt).filter_by(attempt_key='late').count()==0
        assert not db.query(DrillAttempt).filter_by(result='independent').first()


@pytest.mark.parametrize('pause',[False,True])
def test_late_risk_asr_cannot_create_confirmation_or_audio(monkeypatch,pause):
    with SessionLocal() as db:
        db.add(User(id='user-a'));db.add(Session(id='active',user_id='user-a',status='WAITING_INPUT',active_since=NOW(),duration_minutes=15));db.commit()
        class ASR:
            def transcribe(self,path):
                with SessionLocal() as other:
                    if pause:session_svc.set_paused(other,'active','user-a',True)
                    else:session_svc.end_session(other,'active','user-a','user_ended')
                return 'I was going to the'
        monkeypatch.setattr(session_svc,'get_asr_provider',lambda:ASR())
        before=set((settings.data_dir/'audio').glob('*'))
        with pytest.raises(AppError) as error:session_svc.submit_turn(db,'active','user-a','voice',audio_bytes=AUDIO,request_key='late')
        assert error.value.code=='STALE_OPERATION'
        db.expire_all()
        assert not db.get(Session,'active').pending_risk and db.query(AsrRiskRecord).count()==0
        assert set((settings.data_dir/'audio').glob('*'))==before
        assert all(r.reserved==0 for r in db.query(BudgetLedger).all())


@pytest.mark.parametrize('kind',['drill','turn'])
def test_stale_result_settles_only_its_own_estimate(monkeypatch,kind):
    with SessionLocal() as db:
        target=drill(db)
        if kind=='drill':
            def semantic(*args):
                with SessionLocal() as other:session_svc.set_paused(other,'drill','user-a',True)
                return True
            monkeypatch.setattr(settings,'provider_mode','real')
            monkeypatch.setattr(drill_svc,'verify_meaning',semantic)
            send=lambda:drill_svc.submit_drill_attempt(db,'drill','user-a',target,'voice','stale')
        else:
            s=db.get(Session,'drill');s.practice_type='interview';s.status='WAITING_INPUT';s.active_since=NOW();db.commit()
            class Coach(MockCoachProvider):
                def decide(self,ctx):
                    with SessionLocal() as other:
                        session_svc.set_paused(other,'drill','user-a',True);session_svc.set_paused(other,'drill','user-a',False)
                    return super().decide(ctx)
            monkeypatch.setattr(session_svc,'get_coach_provider',lambda:Coach())
            send=lambda:session_svc.submit_turn(db,'drill','user-a','text',user_text='just working',request_key='stale')
        with pytest.raises(AppError):send()
        db.expire_all()
        assert all(r.status=='settled' and r.provider_started for r in db.query(BudgetReservation).all())
        assert all(r.reserved==0 for r in db.query(BudgetLedger).all())
        assert db.query(DrillAttempt).count()==0


def test_sentence_reference_mismatch_is_rejected_before_provider(monkeypatch):
    with SessionLocal() as db:
        target=drill(db)
        def provider():raise AssertionError('mismatched reference must never invoke provider')
        monkeypatch.setattr(session_svc,'get_practice_eval_provider',provider)
        with pytest.raises(AppError) as error:session_svc.submit_practice(db,'drill','user-a',AUDIO,'I am an elephant.','wrong')
        assert error.value.code=='REFERENCE_MISMATCH'
        assert db.query(BudgetReservation).count()==0


@pytest.mark.parametrize('state',['PRACTICE','RECHECK','WAITING_INPUT'])
def test_pause_resume_preserves_persistent_task_state(state):
    with SessionLocal() as db:
        db.add(User(id='user-a'));db.add(Session(id='s',user_id='user-a',status=state));db.commit()
        session_svc.set_paused(db,'s','user-a',True)
    with SessionLocal() as db:
        assert session_svc.set_paused(db,'s','user-a',False)['status']==state
        assert db.get(Session,'s').state_version==2


def test_sentence_material_reused_without_scene_or_grammar_fallback():
    with SessionLocal() as db:
        source(db,scene='job_interview',target='I am working.')
        q=review_queue.add_to_queue(db,'user-a','source','source-t')
        created=session_svc.create_sentence_practice(db,'user-a','source','source-t','create')
        s=db.get(Session,created['session_id']);s.drill_stage='free_speak';db.commit()
        card=phrase_card_svc.generate_card(db,'user-a','source','source-t')
        assert q['meaning_zh']==drill_svc.drill_view(s,db)['visible_help']=='我正在工作。'
        assert card['replaceable']=='working（可换成 learning）'
        assert card['example_text']=='I am learning.' and card['explanation_zh']=='我正在工作。'


def test_unrelated_or_unknown_material_keeps_feedback_but_disables_derivation():
    with SessionLocal() as db:
        source(db,scene='job_interview',target='She has a book.')
        view=session_svc.get_session_detail(db,'source','user-a')['review']['improvements'][0]
        assert view['practice_available'] is False and view['practice_sentence']=='She has a book.'
        for call in [lambda:review_queue.add_to_queue(db,'user-a','source','source-t'),
            lambda:session_svc.create_sentence_practice(db,'user-a','source','source-t','create'),
            lambda:phrase_card_svc.generate_card(db,'user-a','source','source-t')]:
            with pytest.raises(AppError) as error:call()
            assert error.value.code=='MATERIAL_NOT_AVAILABLE'


def test_past_window_excludes_later_completion_and_later_sessions():
    with SessionLocal() as db:
        source(db)
        s=db.get(Session,'source');s.started_at=NOW()-timedelta(days=2);db.commit()
        cutoff=NOW()-timedelta(days=1)
        report=product_metrics.report(db,start=cutoff-timedelta(days=2),end=cutoff)
        assert report['metrics']['first_completion']['numerator']==0
        assert report['metrics']['first_completion']['denominator']==1
        assert report['completed_scene_count']==0 and report['first_practice_users']==0


def test_survey_history_uses_answer_at_cutoff_and_cleans_all_versions():
    with SessionLocal() as db:
        source(db)
        product_data.survey(db,'user-a','source','helpful')
        cutoff=NOW()
        product_data.survey(db,'user-a','source','not_helpful')
        report=product_metrics.report(db,start=cutoff-timedelta(days=1),end=cutoff)
        assert report['metrics']['feedback_helpful']['numerator']==1
        assert report['metrics']['feedback_helpful']['denominator']==1
        assert db.query(ReviewSurveyAnswer).count()==2
        session_svc.delete_session(db,'source','user-a')
        assert db.query(ReviewSurveyAnswer).count()==db.query(ReviewSurvey).count()==0


def test_completed_operation_can_be_recovered_once_and_not_counted_in_past(client):
    with SessionLocal() as db:
        source(db)
        db.add(RequestOperation(id='finished',user_id='user-a',session_id='source',scope='turn',object_id='source',
            request_key='original',request_hash='h',status='done',response_json='{"saved":true}',finished_at=NOW()))
        db.commit()
    lookup=client.get('/api/v1/sessions/source/operations/lookup?scope=turn&request_key=original',headers=H)
    assert lookup.status_code==200 and lookup.json()['operation_id']=='finished'
    for _ in range(2):
        result=client.post('/api/v1/sessions/source/recoveries',json={'operation_id':'finished'},headers=H)
        assert result.status_code==200 and result.json()['state']=='succeeded'
    assert client.get('/api/v1/sessions/source/operations/lookup?scope=turn&request_key=original',headers={'X-Invite-Code':'test-b'}).status_code==403
    with SessionLocal() as db:
        assert db.query(RecoveryAttempt).count()==1
        assert product_metrics.report(db)['metrics']['recovery_success']['rate']==1
        row=db.query(RecoveryAttempt).one();row.created_at=NOW()-timedelta(hours=2)
        db.get(Session,'source').started_at=NOW()-timedelta(hours=3)
        db.get(RequestOperation,'finished').created_at=NOW()-timedelta(hours=2,minutes=30)
        db.commit()
        report=product_metrics.report(db,start=NOW()-timedelta(days=1),end=NOW()-timedelta(hours=1))
        assert report['metrics']['recovery_success']['numerator']==0
        assert report['metrics']['recovery_success']['denominator']==1


def test_api_dates_carry_utc_offset_without_changing_calendar_dates(client):
    with SessionLocal() as db:source(db)
    result=client.get('/api/v1/sessions',headers=H).json()[0]
    assert result['started_at'].endswith('+00:00') and result['ended_at'].endswith('+00:00')
    queue=client.post('/api/v1/review-queue',json={'source_session_id':'source','source_turn_id':'source-t'},headers=H).json()
    assert queue['due_at'].endswith('+00:00')
    plan=client.post('/api/v1/training-plan',json={'goal_type':'travel'},headers=H).json()
    assert len(plan['days'][0]['scheduled_date'])==10

@pytest.mark.parametrize('change',[
    {'meaning_zh':'She has a book.'},
    {'replaceable_text':'not present'},
    {'replacement':'a book'},
    {'scene_ids':['unknown_scene']},
])
def test_invalid_practice_material_does_not_replace_a_valid_language_diagnosis(change):
    from app.services.answer_feedback import grounded
    material={'meaning_zh':'她有一本书。','context_zh':'聊到个人物品','scene_ids':['new_friend'],
        'chunk_text':'She has','replaceable_text':'a book','replacement':'a notebook'}
    item={'issue_type':'grammar','source_quote':'She have a book.','explanation_cn':'第三人称单数用 has。',
        'practice_sentence':'She has a book.','facts_preserved':True,'practice_material':{**material,**change}}
    values=grounded('She have a book.',[item])
    assert len(values)==1 and values[0].practice_material is None


def test_old_phrase_card_uses_current_material_without_overwriting_source_history():
    from app.models.entities import PhraseCard
    with SessionLocal() as db:
        source(db,scene='job_interview',target='I am working.')
        old_review=db.get(Session,'source').review
        db.add(PhraseCard(user_id='user-a',source_session_id='source',source_turn_id='source-t',
            suggestion_text='I am working.',replaceable='I am 可换成 experience',example_text='wrong old example'))
        db.commit()
        card=phrase_card_svc.list_cards(db,'user-a')[0]
        assert card['replaceable']=='working（可换成 learning）' and card['example_text']=='I am learning.'
        assert db.get(Session,'source').review==old_review


def test_recovering_voice_child_resolves_to_original_root(monkeypatch,client):
    with SessionLocal() as db:
        target=drill(db,'full_text')
        monkeypatch.setattr(session_svc,'transcribe_voice_bytes',lambda data:target)
        session_svc.submit_voice_intent(db,'user-a','drill','drill_voice','drill','voice-key',AUDIO,
            lambda text:drill_svc.submit_drill_attempt(db,'drill','user-a',text,'voice','voice-key'))
        root=db.query(RequestOperation).filter_by(scope='voice').one().id
    result=client.get('/api/v1/sessions/drill/operations/lookup?scope=drill_svc&request_key=voice-key',headers=H)
    assert result.status_code==200 and result.json()['operation_id']==root

@pytest.mark.parametrize('sentence',['Hello.','Hi.'])
def test_greeting_material_keeps_a_word_as_chunk(sentence):
    material=material_for({'practice_sentence':sentence},'new_friend')
    assert material.chunk_text in ('Hello','Hi')


def test_migration_restores_legacy_pause_from_saved_turn_result():
    with SessionLocal() as db:
        db.add(User(id='user-a'));db.add(Session(id='paused-old',user_id='user-a',status='PAUSED'))
        db.add(Turn(id='paused-snapshot',session_id='paused-old',input_type='text',result_status='PRACTICE'))
        db.commit()
    migrate_db()
    with SessionLocal() as db:
        assert session_svc.set_paused(db,'paused-old','user-a',False)['status']=='PRACTICE'


def test_stage_controls_increment_even_with_another_tabs_cached_state():
    with SessionLocal() as db:drill(db)
    with SessionLocal() as first, SessionLocal() as second:
        a=first.get(Session,'drill');b=second.get(Session,'drill')
        assert a.state_version==b.state_version==0
        drill_svc.drill_action(first,'drill','user-a','downgrade')
        drill_svc.drill_action(second,'drill','user-a','skip')
    with SessionLocal() as db:assert db.get(Session,'drill').state_version==2


def test_active_legacy_daily_review_uses_sentence_meaning():
    from app.models.entities import ReviewQueueItem
    from app.services.daily_review_svc import current_item
    with SessionLocal() as db:
        target=source(db,scene='job_interview',target='I am working.')
        item=ReviewQueueItem(user_id='user-a',source_session_id='source',source_turn_id='source-t',suggestion_sentence=target,
            meaning_zh='旧版整个场景目标',due_at=NOW()-timedelta(hours=1))
        db.add(item);db.flush()
        session=Session(id='daily-old',user_id='user-a',practice_type='daily_review',status='WAITING_INPUT',daily_review_items=json.dumps([item.id]))
        db.add(session);db.commit()
        assert current_item(db,session,'user-a')['item']['meaning_zh']=='我正在工作。'


def test_unprepared_old_daily_task_preserves_due_and_does_not_add_failure():
    from app.models.entities import ReviewQueueItem, DailyReviewAttempt
    from app.services.daily_review_svc import current_item
    with SessionLocal() as db:
        target=source(db,scene='job_interview',target='She has a book.')
        due=NOW()-timedelta(hours=1)
        item=ReviewQueueItem(user_id='user-a',source_session_id='source',source_turn_id='source-t',suggestion_sentence=target,due_at=due)
        db.add(item);db.flush()
        session=Session(id='daily-old',user_id='user-a',practice_type='daily_review',status='WAITING_INPUT',daily_review_items=json.dumps([item.id]))
        db.add(session);db.commit()
        result=current_item(db,session,'user-a')
        assert result['done'] and result['notice']
        assert session.end_reason=='material_not_available'
        assert item.due_at.replace(tzinfo=timezone.utc)==due
        assert db.query(DailyReviewAttempt).count()==0 and item.status=='pending'


def test_offset_window_filters_events_by_utc_in_sqlite():
    with SessionLocal() as db:
        source(db)
        db.add(ProductEvent(id='after-cutoff',user_id='user-a',action='scene_select',request_key='after',scene_id='job_interview',created_at=NOW()))
        db.commit()
        end=(NOW()-timedelta(hours=1)).astimezone(timezone(timedelta(hours=8)))
        data=product_metrics.report(db,start=end-timedelta(days=1),end=end)
        assert data['metrics']['scene_start']['denominator']==0
        assert data['started_scene_count']==0
        assert data['window']['end'].endswith('+00:00')
