"""PRD 13.2 可回算分子/分母；客户端动作与后端成功事实分开。"""
import json
from datetime import datetime, timedelta, timezone
from app.core.errors import AppError
from app.models.entities import (Session, Turn, ProductEvent, ReviewSurvey, RecoveryAttempt,
    DeviceCheck, DrillAttempt, DailyReviewAttempt, ReviewDueEvent, ReviewQueueItem, EventLog, Invite)
from app.core.config import settings
from app.services.completion import valid_completion
from app.services.learning_metrics import calculate, aware
from app.services.product_data import recovery_status


def report(db, start=None, end=None):
    now = datetime.now(timezone.utc)
    end = min(aware(end), now) if end else now
    start = aware(start) if start else end-timedelta(days=30)
    if start >= end or end-start > timedelta(days=366):
        raise AppError("INVALID_WINDOW", "观察起止时间不合法，最长为366天", 422)
    within = lambda value: value is not None and start <= aware(value) <= end
    excluded = {i.user_id for i in db.query(Invite).filter(Invite.code.in_(settings.qa_invite_codes)).all()}
    sessions = db.query(Session).filter(Session.started_at <= end,Session.user_id.notin_(excluded)).all()
    by_id = {s.id:s for s in sessions}
    ordinary = [s for s in sessions if s.practice_type == "interview"]
    completed = {s.id:s for s in ordinary if valid_completion(db,s) and within(s.ended_at)}
    events = db.query(ProductEvent).filter(ProductEvent.created_at >= start, ProductEvent.created_at <= end,ProductEvent.user_id.notin_(excluded)).all()
    metrics = {}
    def metric(key,label,numerator,denominator,basis,unknown=0):
        metrics[key] = {"label":label,"numerator":numerator,"denominator":denominator,
            "rate":round(numerator/denominator,6) if denominator else None,"unknown":unknown,"basis":basis}
    selections = {e.id:e for e in events if e.action == "scene_select"}
    selecting = {e.user_id for e in selections.values()}
    starters = {s.user_id for s in ordinary if s.selection_id in selections and s.user_id == selections[s.selection_id].user_id}
    metric("scene_start","场景选择后开始率",len(starters),len(selecting),"窗口内选择场景的唯一用户；需关联同场景成功创建的普通会话",sum(within(s.started_at) and not s.selection_id for s in ordinary))
    first = {}
    for s in sorted(ordinary,key=lambda s:aware(s.started_at)):
        first.setdefault(s.user_id,s)
    cohort = [s for s in first.values() if within(s.started_at)]
    metric("first_completion","首练完成率",sum(valid_completion(db,s) for s in cohort),len(cohort),"窗口内开始首个普通场景的唯一用户；失败、空结束和退出保留分母")
    viewed = {e.session_id for e in db.query(EventLog).filter(EventLog.action_type=="review_view",EventLog.created_at<=end).all()
        if e.session_id in completed and aware(e.created_at)>=aware(completed[e.session_id].ended_at)}
    metric("review_view","回顾查看率",len(set(completed)&viewed),len(completed),"窗口内有效完成的普通会话；同一会话只计一次")
    surveys = [s for s in db.query(ReviewSurvey).all() if s.session_id in completed and within(s.shown_at)]
    replied = [s for s in surveys if s.answer and s.answered_at and aware(s.answered_at)<=end]
    judged = [s for s in replied if s.answer != "uncertain"]
    metric("feedback_helpful","反馈有帮助率",sum(s.answer=="helpful" for s in judged),len(judged),"有帮助 /（有帮助+帮助不大）；无法判断单列",sum(s.answer=="uncertain" for s in replied))
    metric("survey_response","问卷回复率",len(replied),len(surveys),"已回复（含无法判断）的会话 / 展示问卷的有效完成会话")
    north = calculate(db,now=end,cohort_start=start,exclude_users=excluded)
    metric("active_review","7日主动有效复练率",north['active_review_users'],north['eligible_users'],"首练在窗口内完成且已满7日的唯一用户；24小时后至第7天的自主有效复练",north['pending_observation_users'])
    metric("full_scene_review","完整场景复练率",north['full_scene_review_users'],north['eligible_users'],"与北极星同用户窗口；至少3个非跟读有效语音回合、有效说话累计60秒",north['pending_observation_users'])
    queue = {q.id:q for q in db.query(ReviewQueueItem).filter(ReviewQueueItem.status.notin_(['invalid','disputed']),ReviewQueueItem.user_id.notin_(excluded)).all()}
    dues = [d for d in db.query(ReviewDueEvent).filter(ReviewDueEvent.valid==True).all() if d.queue_item_id in queue and within(d.due_at)]
    due_done = [d for d in dues if d.resolved_at and aware(d.due_at)<=aware(d.resolved_at)<=end and d.result in ('independent','hinted','text_completed')]
    metric("due_completion","到期复测完成率",len(due_done),len(dues),"有效到期项目；独立、提示、文字完成计完成，跳过/失败/退出仍在分母")
    valid_drills = {s.id for s in sessions if s.practice_type=='sentence_practice' and not s.source_invalid}
    attempts = [a for a in db.query(DrillAttempt).all() if a.drill_session_id in valid_drills and within(a.created_at)]
    def stage_ids(stage,action):
        return {e.session_id for e in events if e.stage==stage and e.action==action and e.session_id in valid_drills}
    free_view,free_start = stage_ids('free_speak','stage_view'),stage_ids('free_speak','stage_start')
    free_attempt = {a.drill_session_id for a in attempts if a.stage=='free_speak' and a.input_type=='voice' and a.result not in ('pending','insufficient_evidence')}
    free_done = {a.drill_session_id for a in attempts if a.stage=='free_speak' and a.result=='independent'}
    metric("drill_from_shown","脱稿完成率（展示）",len(free_done&free_view),len(free_view),"展示脱稿阶段的会话；同会话去重",len(free_done-free_view))
    metric("drill_from_started","脱稿完成率（开始）",len(free_done&free_start),len(free_start),"实际开始脱稿的会话，取消/故障保留分母",len(free_done-free_start))
    metric("drill_from_attempted","脱稿完成率（有效尝试）",len(free_done&free_attempt),len(free_attempt),"有可判断语音尝试的会话，失败尝试保留分母")
    transfer_start = stage_ids('transfer','stage_start')
    transfer_done = {a.drill_session_id for a in attempts if a.stage=='transfer' and a.result=='transfer'}
    transfer_users = {by_id[sid].user_id for sid in transfer_done}
    accepting_users = {by_id[sid].user_id for sid in transfer_start}
    eligible_users = {by_id[sid].user_id for sid in free_done}
    metric("transfer_accepted","换题完成率（接受任务）",len(transfer_users&accepting_users),len(accepting_users),"窗口内开始换题的唯一用户；取消/失败/退出保留分母",len(transfer_users-accepting_users))
    metric("transfer_eligible","换题完成率（符合资格）",len(transfer_users&eligible_users),len(eligible_users),"窗口内无帮助完成脱稿、符合换题条件的全部唯一用户；未接受任务仍在分母")
    daily = [s for s in sessions if s.practice_type=='daily_review' and within(s.started_at)]
    daily_users = {s.user_id for s in daily}
    finished_users=set()
    for s in daily:
        try:
            wanted = set(json.loads(s.daily_review_items or '[]'))
        except (TypeError,ValueError):
            wanted=set()
        done={a.queue_item_id for a in db.query(DailyReviewAttempt).filter_by(session_id=s.id).all() if a.result in ('independent','hinted','text_completed') and aware(a.created_at)<=end and a.queue_item_id in queue}
        if wanted and wanted<=done and s.status=='ENDED' and s.end_reason=='goal_reached':
            finished_users.add(s.user_id)
    metric("daily_completion","今日复练完成率",len(finished_users),len(daily_users),"开始过组合任务的唯一用户；所选项目均完成；跳过/退出/失败不移走分母")
    words=set()
    for t in db.query(Turn).filter(Turn.input_type=='practice',Turn.practice_eval!='').all():
        if t.session_id not in by_id or by_id[t.session_id].source_invalid or not within(t.created_at):continue
        try:
            if json.loads(t.practice_eval).get('word_items'):words.add(t.id)
        except (TypeError,ValueError):pass
    disputes={e.turn_id for e in events if e.action=='word_dispute'}
    metric("word_dispute","词级反馈争议率",len(words&disputes),len(words),"有原始词级项目的评测回合；重复争议只算一次，无词级不入分母")
    recoveries = [r for r in db.query(RecoveryAttempt).all() if within(r.created_at) and r.session_id in by_id]
    states=[recovery_status(db,r)['state'] for r in recoveries]
    metric("recovery_success","处理中恢复成功率",states.count('succeeded'),len(states),"每个未决操作只计一次；结果快照可恢复才成功，pending/failed/uncertain不算成功",states.count('pending')+states.count('unresolved'))
    checks={c.id:c for c in db.query(DeviceCheck).filter_by(status='ok').all() if within(c.created_at) and c.user_id not in excluded}
    recording_started={e.session_id for e in db.query(EventLog).filter_by(action_type='record_attempt').all() if within(e.created_at)}
    linked={s.device_check_id for s in ordinary if s.device_check_id in checks and s.id in recording_started}
    valid_voice={t.session_id for t in db.query(Turn).filter(Turn.input_type=='voice',Turn.eval_result.in_(['independent','hinted','incomplete']),Turn.created_at<=end).all()}
    recorded={s.device_check_id for s in ordinary if s.device_check_id in linked and s.id in valid_voice}
    metric("device_record_success","预检后录音成功率",len(recorded),len(linked),"通过预检且发起录音的预检记录；权限失败/取消/退出仍在分母，只有文字且未录音不入分母",sum(within(s.started_at) and s.id in valid_voice and (s.device_check_id is None or s.id not in recording_started) for s in ordinary))
    return {**north,"version":"product-metrics-v2","window":{"start":start.isoformat(),"end":end.isoformat(),"scope":"新采集动作不回填历史；有分子分母仅表示事实比例，真实产品目标待内测验收"},"metrics":metrics,
        "counts":{"drill_shown":len(free_view),"drill_started":len(free_start),"drill_valid_attempts":len(free_attempt),"due_independent":sum(d.result=='independent' for d in due_done),"due_hinted":sum(d.result=='hinted' for d in due_done),"due_text":sum(d.result=='text_completed' for d in due_done),"recovery_pending":states.count('pending'),"recovery_failed":states.count('failed'),"recovery_unresolved":states.count('unresolved')}}
