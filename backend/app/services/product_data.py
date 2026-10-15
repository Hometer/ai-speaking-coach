"""产品动作与问卷，成功结论只由业务表计算。"""
import uuid
import json
from datetime import datetime, timedelta, timezone
from sqlalchemy.exc import IntegrityError
from app.core.errors import AppError
from app.models.entities import ProductEvent, ReviewSurvey, RecoveryAttempt, RequestOperation, DeviceCheck, Turn
from app.services.completion import valid_completion
from app.services.session_svc import _require_session
from app.services.prompts.scenarios import get_scene


def event(db, user_id, req):
    values = req.model_dump()
    old = db.query(ProductEvent).filter_by(user_id=user_id, action=req.action, request_key=req.request_key).first()
    if old:
        if any(getattr(old,k)!=v for k,v in values.items()):
            raise AppError("IDEMPOTENCY_CONFLICT", "同一事件键内容不一致", 409)
        return {"recorded":True,"event_id":old.id}
    if req.action == "scene_select":
        get_scene(req.scene_id)
        if req.session_id or req.stage:
            raise AppError("INVALID_EVENT", "选择场景事件参数不合法", 422)
    else:
        session = _require_session(db, req.session_id, user_id)
        if session.source_invalid or session.status in ("ENDED","PAUSED") or session.practice_type != "sentence_practice" or session.drill_stage != req.stage or req.stage not in ("free_speak", "transfer"):
            raise AppError("INVALID_EVENT", "当前练习阶段不匹配", 409)
        if req.scene_id:
            raise AppError("INVALID_EVENT", "阶段事件不接受额外场景", 422)
    old = db.query(ProductEvent).filter_by(user_id=user_id, action=req.action, request_key=req.request_key).first()
    if old is None:
        old = ProductEvent(id=uuid.uuid4().hex, user_id=user_id, **values)
        db.add(old)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            old = db.query(ProductEvent).filter_by(user_id=user_id, action=req.action, request_key=req.request_key).one()
    if any(getattr(old, k) != v for k,v in values.items()):
        raise AppError("IDEMPOTENCY_CONFLICT", "同一事件键内容不一致", 409)
    return {"recorded":True, "event_id":old.id}


def validate_entry(db, user_id, scene_id, selection_id, device_check_id):
    if selection_id:
        selection = db.get(ProductEvent, selection_id)
        if selection is None or selection.user_id != user_id or selection.action != "scene_select" or selection.scene_id != scene_id:
            raise AppError("INVALID_SELECTION", "场景选择记录不匹配", 409)
    if device_check_id is not None:
        device = db.get(DeviceCheck, device_check_id)
        if device is None or device.user_id != user_id:
            raise AppError("INVALID_DEVICE_CHECK", "设备预检记录不匹配", 409)
        created = device.created_at.replace(tzinfo=timezone.utc) if device.created_at.tzinfo is None else device.created_at
        if datetime.now(timezone.utc) - created > timedelta(hours=24):
            raise AppError("DEVICE_CHECK_EXPIRED", "设备预检已过期，请重新检查", 409)


def survey(db, user_id, session_id, answer):
    session = _require_session(db, session_id, user_id)
    if session.practice_type != "interview" or not valid_completion(db, session):
        raise AppError("REVIEW_NOT_AVAILABLE", "当前没有可评价的有效回顾", 409)
    row = db.query(ReviewSurvey).filter_by(session_id=session_id).first()
    if row is None:
        row = ReviewSurvey(session_id=session_id, user_id=user_id)
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            row = db.query(ReviewSurvey).filter_by(session_id=session_id).one()
    if answer != "shown" and row.answer != answer:
        row.answer = answer
        row.version += 1
        row.answered_at = datetime.now(timezone.utc)
        db.commit()
    return {"answer":row.answer or None, "version":row.version}


def recovery_status(db, row):
    op = db.get(RequestOperation, row.operation_id)
    state = "unresolved"
    if op:
        if op.status == "done" and op.response_json:
            state = "succeeded"
        elif op.status == "running":
            state = "pending"
        elif op.status == "failed":
            state = "failed"
    return {"recovery_id":row.id, "state":state, "operation_id":row.operation_id}


def recover(db, user_id, session_id, operation_id):
    _require_session(db, session_id, user_id)
    op = db.get(RequestOperation, operation_id)
    if op is None or op.user_id != user_id or op.session_id != session_id:
        raise AppError("NOT_FOUND", "待恢复操作不存在", 404)
    row = db.query(RecoveryAttempt).filter_by(operation_id=operation_id).first()
    if row is None:
        if op.status not in ("running", "uncertain"):
            raise AppError("NOT_PENDING", "操作已结束，无需创建恢复尝试", 409)
        row = RecoveryAttempt(id=uuid.uuid4().hex, user_id=user_id, session_id=session_id, operation_id=operation_id)
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            row = db.query(RecoveryAttempt).filter_by(operation_id=operation_id).one()
    return recovery_status(db, row)


def word_dispute(db,user_id,session_id,turn_id):
    _require_session(db,session_id,user_id)
    turn=db.get(Turn,turn_id)
    if turn is None or turn.session_id!=session_id or turn.input_type!='practice':
        raise AppError('NOT_FOUND','评测回合不存在',404)
    try:
        evaluation=json.loads(turn.practice_eval or '{}')
    except (ValueError,TypeError):
        evaluation={}
    if not evaluation.get('word_items'):
        raise AppError('NO_WORD_EVIDENCE','本次没有可争议的词级反馈',409)
    old=db.query(ProductEvent).filter_by(user_id=user_id,action='word_dispute',request_key=turn_id).first()
    if old is None:
        db.add(ProductEvent(id=uuid.uuid4().hex,user_id=user_id,action='word_dispute',request_key=turn_id,session_id=session_id,turn_id=turn_id))
        try:
            evaluation['disputed']=True
            turn.practice_eval=json.dumps(evaluation,ensure_ascii=False)
            db.commit()
        except IntegrityError:
            db.rollback()
    return {'disputed':True,'turn_id':turn_id}
