"""今日复练组合（REQ-29，P1-2）。

从待复练队列选已到期、来源有效的 3~5 条组成一次连续练习，逐条回忆→提示→完成/跳过/延后/争议，
结果写回同一套队列并按间隔档位（1/3/7/14）推进。沿用 AC07-08 跨日约束，不另建第二套队列。
"""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.core.errors import AppError
from app.models.entities import DailyReviewAttempt, EventLog, ReviewQueueItem, Session, Turn
from app.services.review_queue import STATUS_AWAITING_RETEST, STATUS_DISPUTED, STATUS_MASTERED, STATUS_PENDING

INTERVALS = [1, 3, 7, 14]
DAILY_LIMIT = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt):
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9\s']", " ", (text or "").lower())


def _judge(answer: str, target: str) -> bool:
    a = _norm(answer)
    if len(a.split()) < 2:
        return False
    t = _norm(target)
    t_words = set(t.split())
    if not t_words:
        return True
    return len(set(a.split()) & t_words) / len(t_words) >= 0.4


def _next_interval(days: int) -> int:
    idx = INTERVALS.index(days) if days in INTERVALS else 0
    return INTERVALS[min(idx + 1, len(INTERVALS) - 1)]


def _source_valid(db: DbSession, item: ReviewQueueItem) -> bool:
    src = db.get(Session, item.source_session_id)
    turn = db.get(Turn, item.source_turn_id)
    return bool(
        src is not None and src.user_id == item.user_id
        and turn is not None and turn.session_id == src.id and turn.eval_result != "revised"
    )


def due_items(db: DbSession, user_id: str, limit: int = DAILY_LIMIT) -> list[ReviewQueueItem]:
    now = _now()
    items = (
        db.query(ReviewQueueItem)
        .filter(
            ReviewQueueItem.user_id == user_id,
            ReviewQueueItem.status.in_([STATUS_PENDING, STATUS_AWAITING_RETEST]),
            ReviewQueueItem.due_at.isnot(None),
        )
        .all()
    )
    valid = [i for i in items if _aware(i.due_at) <= now and _source_valid(db, i)]
    valid.sort(key=lambda i: (_aware(i.due_at), 1 if i.retest_result == "hinted" else 0))
    return valid[:limit]


def _item_dict(i: ReviewQueueItem) -> dict[str, Any]:
    return {
        "id": i.id,
        "source_turn_id": i.source_turn_id,
        "suggestion_sentence": i.suggestion_sentence,
        "status": i.status,
        "success_count": i.success_count,
        "interval_days": i.interval_days,
        "due_at": i.due_at.isoformat() if i.due_at else None,
    }


def due_today(db: DbSession, user_id: str) -> dict[str, Any]:
    items = due_items(db, user_id)
    return {"count": len(items), "items": [_item_dict(i) for i in items]}


def start_daily_review(db: DbSession, user_id: str) -> dict[str, Any]:
    items = due_items(db, user_id)
    if not items:
        return {"session_id": None, "items": []}
    ids = [i.id for i in items]
    existing = (
        db.query(Session)
        .filter(
            Session.user_id == user_id,
            Session.practice_type == "daily_review",
            Session.status != "ENDED",
        )
        .first()
    )
    if existing is not None and set(json.loads(existing.daily_review_items or "[]")) == set(ids):
        return {"session_id": existing.id, "items": [_item_dict(i) for i in items]}
    session = Session(
        id=uuid.uuid4().hex,
        user_id=user_id,
        status="WAITING_INPUT",
        goal_text="Today's review",
        difficulty="standard",
        practice_type="daily_review",
        daily_review_items=json.dumps(ids),
        daily_review_index=0,
        active_since=_now(),
    )
    db.add(session)
    db.commit()
    return {"session_id": session.id, "items": [_item_dict(i) for i in items]}


def current_item(db: DbSession, session: Session, user_id: str) -> dict[str, Any]:
    ids = json.loads(session.daily_review_items or "[]")
    idx = session.daily_review_index
    if idx >= len(ids):
        return {"done": True, "index": idx, "total": len(ids)}
    item = db.get(ReviewQueueItem, ids[idx])
    if item is None or item.user_id != user_id or item.status not in (STATUS_PENDING, STATUS_AWAITING_RETEST) or not _source_valid(db, item) or item.due_at is None or _aware(item.due_at) > _now():
        session.status = "ENDED"
        session.end_reason = "source_invalid"
        db.commit()
        return {"done": True, "index": idx, "total": len(ids)}
    return {"done": False, "index": idx, "total": len(ids), "item": _item_dict(item)}


def _advance(db: DbSession, session: Session) -> None:
    session.daily_review_index += 1
    ids = json.loads(session.daily_review_items or "[]")
    if session.daily_review_index >= len(ids):
        session.status = "ENDED"
        session.end_reason = "goal_reached"


def submit_attempt(
    db: DbSession, session_id: str, user_id: str, answer: str, help_used: bool = False
) -> dict[str, Any]:
    session = db.get(Session, session_id)
    if session is None or session.user_id != user_id:
        raise AppError("FORBIDDEN", "无权访问该会话", status_code=403)
    if session.practice_type != "daily_review" or session.status == "ENDED":
        raise AppError("INVALID_STATE", "今日复练已结束或不存在", status_code=409)

    cur = current_item(db, session, user_id)
    if cur.get("done"):
        raise AppError("SESSION_ENDED", "今日复练已结束", status_code=409)
    item = db.get(ReviewQueueItem, cur["item"]["id"])

    success = _judge(answer, item.suggestion_sentence)
    result = "completed" if success else "failed"
    if help_used and success:
        result = "completed"

    db.add(
        DailyReviewAttempt(
            user_id=user_id,
            session_id=session.id,
            queue_item_id=item.id,
            attempt_answer=answer.strip(),
            help_used=help_used,
            result=result,
        )
    )
    db.add(EventLog(session_id=session.id, turn_id="", action_type="daily_review", basis=result))

    if success:
        # 写回同一套队列：无帮助独立成功→下一档；有帮助→回 1 天
        item.last_practiced_at = _now()
        if help_used:
            item.interval_days = 1
            item.retest_result = "hinted"
        else:
            item.success_count = (item.success_count or 0) + 1
            item.interval_days = _next_interval(item.interval_days)
            item.retest_result = "independent"
            if item.success_count >= 2:
                item.status = STATUS_MASTERED
            else:
                item.status = STATUS_AWAITING_RETEST
        item.due_at = _now() + timedelta(days=item.interval_days)
        _advance(db, session)
    db.commit()

    nxt = current_item(db, session, user_id)
    return {"success": success, "result": result, "help_used": help_used, "next": nxt}


def control_action(db: DbSession, session_id: str, user_id: str, action: str) -> dict[str, Any]:
    session = db.get(Session, session_id)
    if session is None or session.user_id != user_id:
        raise AppError("FORBIDDEN", "无权访问该会话", status_code=403)
    if session.practice_type != "daily_review" or session.status == "ENDED":
        raise AppError("INVALID_STATE", "今日复练已结束或不存在", status_code=409)
    if action == "end":
        session.status = "ENDED"
        session.end_reason = "user_ended"
        db.commit()
        return {"next": {"done": True, "index": session.daily_review_index, "total": len(json.loads(session.daily_review_items or "[]"))}}
    cur = current_item(db, session, user_id)
    if cur.get("done"):
        raise AppError("SESSION_ENDED", "今日复练已结束", status_code=409)
    item = db.get(ReviewQueueItem, cur["item"]["id"])
    if action == "skip":
        db.add(DailyReviewAttempt(user_id=user_id, session_id=session.id, queue_item_id=item.id, result="skipped"))
        _advance(db, session)
    elif action == "defer":
        db.add(DailyReviewAttempt(user_id=user_id, session_id=session.id, queue_item_id=item.id, result="deferred"))
        item.due_at = _now() + timedelta(days=item.interval_days)
        _advance(db, session)
    elif action == "dispute":
        db.add(DailyReviewAttempt(user_id=user_id, session_id=session.id, queue_item_id=item.id, result="disputed"))
        item.status = STATUS_DISPUTED
        _advance(db, session)
    else:
        raise AppError("INVALID_INPUT", "未知的复练操作", status_code=422)
    db.commit()
    return {"next": current_item(db, session, user_id)}


def summary(db: DbSession, session_id: str, user_id: str) -> dict[str, Any]:
    session = db.get(Session, session_id)
    if session is None or session.user_id != user_id:
        raise AppError("FORBIDDEN", "无权访问该会话", status_code=403)
    attempts = (
        db.query(DailyReviewAttempt).filter(DailyReviewAttempt.session_id == session_id).all()
    )
    completed = sum(1 for a in attempts if a.result == "completed")
    helped = sum(1 for a in attempts if a.result == "completed" and a.help_used)
    skipped = sum(1 for a in attempts if a.result == "skipped")
    deferred = sum(1 for a in attempts if a.result == "deferred")
    return {
        "session_id": session.id,
        "status": session.status,
        "completed": completed,
        "helped": helped,
        "skipped": skipped,
        "deferred": deferred,
        "total": len(attempts),
    }
