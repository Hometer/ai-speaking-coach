"""去重的产品事件，只保存动作、状态和必要数值。"""
from sqlalchemy.exc import IntegrityError
from app.models.entities import EventLog

MEDIA_ACTIONS = {'demo_play','recording_play','record_attempt','record_start','record_cancel','record_retry','review_view'}


def record(db, session_id, action, key='', turn_id='', basis=''):
    event_key=f'event:{action}:{key}' if key else None
    if event_key and db.query(EventLog).filter_by(session_id=session_id,request_key=event_key).first():
        return
    db.add(EventLog(session_id=session_id,turn_id=turn_id,action_type=action,request_key=event_key,basis=basis))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
