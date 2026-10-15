"""调用前持久认领用户意图；进程中断后保留待核对，不盲目重复付费动作。"""
import contextvars
import functools
import hashlib
import inspect
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.services import deadline
from app.models.entities import RequestOperation, Session

_stack = contextvars.ContextVar('operation_stack', default=())


def current_id() -> str:
    stack = _stack.get()
    return stack[-1] if stack else ''


def _json(value):
    if isinstance(value, bytes):
        return {'bytes_sha256': hashlib.sha256(value).hexdigest()}
    raise TypeError('unsupported operation input')


def durable(scope: str, key_arg: str = 'request_key', object_arg: str = 'session_id'):
    def decorate(fn):
        signature = inspect.signature(fn)
        @functools.wraps(fn)
        def run(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            values = bound.arguments
            db, user_id = values['db'], values['user_id']
            sid = values.get('session_id', '')
            if sid:
                session = db.get(Session, sid)
                if session is None or session.user_id != user_id:
                    raise AppError('FORBIDDEN', '无权访问该会话', 403)
            key = values.get(key_arg) or uuid.uuid4().hex
            if key_arg in values:
                values[key_arg] = key
            obj = str(values.get(object_arg) or user_id)
            payload = {k: v for k, v in values.items() if k not in ('db', 'submit', key_arg)}
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=_json).encode()).hexdigest()
            row = db.query(RequestOperation).filter_by(user_id=user_id, scope=scope, object_id=obj, request_key=key).first()
            if row is None:
                row = RequestOperation(id=uuid.uuid4().hex, user_id=user_id, scope=scope,
                    object_id=obj, session_id=sid if not _stack.get() else '', request_key=key, request_hash=digest)
                db.add(row)
                try:
                    db.commit()  # 在任何供应商调用之前认领；唯一索引是并发裁判
                except IntegrityError:
                    db.rollback()
                    row = db.query(RequestOperation).filter_by(user_id=user_id, scope=scope, object_id=obj, request_key=key).first()
                    if row is None:
                        raise AppError('REQUEST_IN_PROGRESS', '另一个操作正在处理，请稍候刷新', 409)
                    return _existing(row, digest)
            else:
                return _existing(row, digest)
            op_id = row.id
            token = _stack.set(_stack.get() + (op_id,))
            if sid:
                db.execute(update(Session).where(Session.id == sid).values(in_flight=True))
                db.commit()
            deadline_token = deadline.begin()
            try:
                result = fn(*bound.args, **bound.kwargs)
                saved = db.execute(update(RequestOperation).where(RequestOperation.id == op_id,
                    RequestOperation.status == 'running').values(status='done', response_json=json.dumps(result, ensure_ascii=False)))
                if saved.rowcount != 1:
                    raise AppError('SOURCE_INVALID', '操作已被撤回或来源失效，请刷新查看', 409)
                db.commit()
                return result
            except Exception as exc:
                db.rollback()
                row = db.get(RequestOperation, op_id)
                if row is not None:
                    row.status = 'failed' if isinstance(exc, AppError) else 'uncertain'
                    row.error_code = exc.code if isinstance(exc, AppError) else 'REQUEST_UNCERTAIN'
                    row.error_message = exc.message if isinstance(exc, AppError) else '上次操作结果待核对，请刷新查看，勿重复提交'
                    row.error_status = exc.status_code if isinstance(exc, AppError) else 409
                    db.commit()
                raise
            finally:
                deadline.reset(deadline_token)
                _stack.reset(token)
                if sid and not _stack.get():
                    db.execute(update(Session).where(Session.id == sid).values(in_flight=False))
                    db.commit()
        return run
    return decorate


def _existing(row, digest):
    if row.request_hash != digest:
        raise AppError('IDEMPOTENCY_CONFLICT', '同一请求键内容不一致', 409)
    if row.status == 'done':
        return json.loads(row.response_json)
    if row.status == 'running':
        raise AppError('REQUEST_IN_PROGRESS', '操作仍在处理，请稍候刷新查看', 409)
    raise AppError(row.error_code or 'REQUEST_UNCERTAIN', row.error_message or '上次操作结果待核对，请刷新查看', row.error_status)


def require_active(session):
    if session.status in ('ENDED', 'PAUSED') or session.source_invalid:
        code = 'SOURCE_INVALID' if session.source_invalid else 'SESSION_' + session.status
        raise AppError(code, '练习已结束、暂停或来源失效，请刷新查看', 409)


def fence(db, session, version: int):
    """供应商返回后原子核对状态版本，拒绝暂停/恢复之前的迟到结果。"""
    result = db.execute(update(Session).where(Session.id == session.id, Session.state_version == version,
        Session.status.notin_(['ENDED', 'PAUSED']), Session.source_invalid == False).values(state_version=version + 1))
    if result.rowcount != 1:
        db.rollback()
        raise AppError('STALE_OPERATION', '练习状态已改变，已忽略迟到结果', 409)
    db.refresh(session)


def recover_interrupted(db):
    db.query(RequestOperation).filter_by(status='running').update({'status':'uncertain',
        'error_code':'REQUEST_UNCERTAIN', 'error_message':'服务重启前的操作结果待核对，请查看已保存记录', 'error_status':409})
    db.query(Session).filter_by(in_flight=True).update({'in_flight':False})
    db.commit()
