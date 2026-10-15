"""按真实观察窗口回算内测指标。样本/退出保留，未知不冒充达标。"""
from datetime import datetime,timedelta,timezone
from app.models.entities import Session,Turn,ReviewDueEvent
from app.services.completion import valid_completion


def aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def calculate(db, user_id=None, now=None, cohort_start=None, exclude_users=None):
    now=aware(now or datetime.now(timezone.utc))
    sessions=db.query(Session).filter(Session.started_at <= now).all()
    if exclude_users:
        sessions=[s for s in sessions if s.user_id not in exclude_users]
    if user_id:
        sessions=[s for s in sessions if s.user_id==user_id]
    ordinary=[s for s in sessions if s.practice_type=='interview']
    completed=[s for s in ordinary if s.ended_at is not None and aware(s.ended_at)<=now and valid_completion(db,s)]
    first={}
    for s in sorted(completed,key=lambda s:aware(s.ended_at)):
        first.setdefault(s.user_id,s)
    if cohort_start:
        first={uid:s for uid,s in first.items() if aware(s.ended_at)>=cohort_start}
    eligible={uid:s for uid,s in first.items() if aware(s.ended_at)+timedelta(days=7)<=now}
    active=set();full=set()
    for uid,initial in eligible.items():
        start,end=aware(initial.ended_at)+timedelta(hours=24),aware(initial.ended_at)+timedelta(days=7)
        later=[s for s in ordinary if s.user_id==uid and s.id!=initial.id and start<=aware(s.started_at)<=end and s.initiation=='self']
        for s in later:
            turns=db.query(Turn).filter(Turn.session_id==s.id,Turn.input_type=='voice',
                Turn.created_at>=start,Turn.created_at<=end,
                Turn.eval_result.in_(['independent','hinted','incomplete'])).all()
            if turns:
                active.add(uid)
            if len(turns)>=3 and sum(t.speech_seconds or 0 for t in turns)>=60:
                full.add(uid)
        if db.query(ReviewDueEvent).filter(ReviewDueEvent.user_id==uid,ReviewDueEvent.valid==True,ReviewDueEvent.result=='independent',ReviewDueEvent.resolved_at>=start,ReviewDueEvent.resolved_at<=end,ReviewDueEvent.due_at<=ReviewDueEvent.resolved_at).first():
            active.add(uid)
    return {'version':'v1','observation_days':7,'eligible_users':len(eligible),'active_review_users':len(active),
        'active_review_rate':len(active)/len(eligible) if eligible else None,'full_scene_review_users':len(full),
        'full_scene_review_rate':len(full)/len(eligible) if eligible else None,'first_practice_users':len(first),
        'completed_scene_count':len(completed),'started_scene_count':len(ordinary),
        'pending_observation_users':len(first)-len(eligible),'speech_measurement':'energy_v1_pending_real_calibration'}
