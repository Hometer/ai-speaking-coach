"""结束是生命周期事实，有效完成须有本用途的尝试与回顾证据。"""
import json
from app.models.entities import Session, Turn, DrillAttempt, DailyReviewAttempt


def valid_completion(db, session: Session) -> bool:
    if session.status != 'ENDED' or session.source_invalid:
        return False
    if session.practice_type in ('sentence_practice', 'drill'):
        return bool(db.query(DrillAttempt).filter(DrillAttempt.drill_session_id == session.id,
            DrillAttempt.result.in_(['reference','hinted','independent','transfer'])).first() or
            db.query(Turn).filter(Turn.session_id == session.id, Turn.input_type == 'practice', Turn.practice_eval != '').first())
    if session.practice_type == 'daily_review':
        return bool(db.query(DailyReviewAttempt).filter(DailyReviewAttempt.session_id == session.id,
            DailyReviewAttempt.result.in_(['independent','hinted','text_completed'])).first())
    try:
        review = json.loads(session.review or '{}')
    except (ValueError, TypeError):
        return False
    return bool(review and not review.get('stale') and review.get('evidence') and
        db.query(Turn).filter(Turn.session_id == session.id, Turn.input_type.in_(['voice','text']),
            Turn.eval_result.in_(['independent','hinted','incomplete','text_completed'])).first())
