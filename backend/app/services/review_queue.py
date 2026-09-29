"""待复练队列：回顾建议 -> 跨日队列 -> 到期复测 -> 掌握 / 争议 / 失效。"""
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.core.errors import AppError
from app.models.entities import ReviewQueueItem, Session

STATUS_PENDING = "pending"            # 待练
STATUS_AWAITING_RETEST = "awaiting_retest"  # 待复测（跟读已完成）
STATUS_MASTERED = "mastered"          # 已掌握
STATUS_DISPUTED = "disputed"          # 有争议
STATUS_INVALID = "invalid"            # 已失效

DUE_HOURS = 24  # 复测最早间隔（第二次独立成功至少 24h 后）


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_dict(item: ReviewQueueItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "source_session_id": item.source_session_id,
        "source_turn_id": item.source_turn_id,
        "target_ability": item.target_ability,
        "suggestion_sentence": item.suggestion_sentence,
        "status": item.status,
        "success_count": item.success_count,
        "due_at": item.due_at.isoformat() if item.due_at else None,
        "last_practiced_at": item.last_practiced_at.isoformat() if item.last_practiced_at else None,
        "retest_result": item.retest_result,
    }


def add_to_queue(db: DbSession, user_id: str, source_session_id: str, source_turn_id: str) -> dict[str, Any]:
    """用户确认后把回顾建议加入队列。幂等：同一来源 turn 只创建一个有效项。"""
    source = db.get(Session, source_session_id)
    if source is None or source.user_id != user_id:
        raise AppError("FORBIDDEN", "无权使用该来源", status_code=403)
    review = json.loads(source.review) if source.review else {}
    imp = next(
        (i for i in review.get("improvements", []) if i.get("turn_id") == source_turn_id),
        None,
    )
    if imp is None or imp.get("disputed") or review.get("stale"):
        raise AppError("NOT_FOUND", "改进建议不存在或已失效", status_code=404)
    sentence = imp.get("practice_sentence") or ""
    if not sentence:
        raise AppError("INVALID_STATE", "该建议没有可练句子", status_code=409)

    existing = (
        db.query(ReviewQueueItem)
        .filter(
            ReviewQueueItem.user_id == user_id,
            ReviewQueueItem.source_turn_id == source_turn_id,
        )
        .first()
    )
    if existing:
        return _to_dict(existing)

    item = ReviewQueueItem(
        user_id=user_id,
        source_session_id=source_session_id,
        source_turn_id=source_turn_id,
        target_ability="expression",
        suggestion_sentence=sentence,
        status=STATUS_PENDING,
        due_at=_now() + timedelta(hours=DUE_HOURS),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _to_dict(item)


def list_queue(db: DbSession, user_id: str) -> list[dict[str, Any]]:
    items = (
        db.query(ReviewQueueItem)
        .filter(ReviewQueueItem.user_id == user_id, ReviewQueueItem.status != STATUS_INVALID)
        .order_by(ReviewQueueItem.created_at.desc())
        .all()
    )
    return [_to_dict(i) for i in items]


def get_due_item(db: DbSession, user_id: str) -> dict[str, Any] | None:
    """首页只推荐一项到期复练。"""
    now = _now()
    item = (
        db.query(ReviewQueueItem)
        .filter(
            ReviewQueueItem.user_id == user_id,
            ReviewQueueItem.status.in_([STATUS_PENDING, STATUS_AWAITING_RETEST]),
            ReviewQueueItem.due_at <= now,
        )
        .order_by(ReviewQueueItem.due_at)
        .first()
    )
    return _to_dict(item) if item else None


def submit_retest(db: DbSession, user_id: str, item_id: int, answer: str, hint_used: bool = False) -> dict[str, Any]:
    """复测提交（确定性）：独立成功推进，两次独立成功（间隔 >=24h）才掌握。"""
    item = db.get(ReviewQueueItem, item_id)
    if item is None or item.user_id != user_id:
        raise AppError("NOT_FOUND", "队列项不存在", status_code=404)
    if item.status not in (STATUS_PENDING, STATUS_AWAITING_RETEST):
        raise AppError("INVALID_STATE", "该队列项当前不能复测", status_code=409)

    # 首次与后续复测都只能在到期后提交；不能靠直接调用接口提前增加成功次数。
    if item.due_at is None:
        raise AppError("NOT_DUE", "复测尚未安排到期时间", status_code=409)
    due = item.due_at
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    if _now() < due:
        raise AppError("NOT_DUE", "复测尚未到期，请稍后再试", status_code=409)

    answer = (answer or "").strip()
    independent = len(answer) >= 8 and not hint_used
    item.last_practiced_at = _now()
    item.retest_result = "independent" if independent else "hinted"

    if independent:
        item.success_count = (item.success_count or 0) + 1
        if item.success_count >= 2:
            item.status = STATUS_MASTERED
        else:
            item.status = STATUS_AWAITING_RETEST
            item.due_at = _now() + timedelta(hours=DUE_HOURS)
    else:
        item.status = STATUS_AWAITING_RETEST
        item.due_at = _now() + timedelta(hours=DUE_HOURS)

    db.commit()
    return _to_dict(item)


def mark_disputed(db: DbSession, user_id: str, item_id: int) -> dict[str, Any]:
    item = db.get(ReviewQueueItem, item_id)
    if item is None or item.user_id != user_id:
        raise AppError("NOT_FOUND", "队列项不存在", status_code=404)
    item.status = STATUS_DISPUTED
    db.commit()
    return _to_dict(item)


def skip_item(db: DbSession, user_id: str, item_id: int) -> dict[str, Any]:
    """跳过 / 延后：顺延 24h。"""
    item = db.get(ReviewQueueItem, item_id)
    if item is None or item.user_id != user_id:
        raise AppError("NOT_FOUND", "队列项不存在", status_code=404)
    item.due_at = _now() + timedelta(hours=DUE_HOURS)
    db.commit()
    return _to_dict(item)


def delete_item(db: DbSession, user_id: str, item_id: int) -> None:
    item = db.get(ReviewQueueItem, item_id)
    if item is None or item.user_id != user_id:
        raise AppError("NOT_FOUND", "队列项不存在", status_code=404)
    db.delete(item)
    db.commit()


def invalidate_by_turn(db: DbSession, turn_id: str) -> None:
    """源建议失效时，关联队列项标失效。"""
    for item in db.query(ReviewQueueItem).filter(ReviewQueueItem.source_turn_id == turn_id).all():
        item.status = STATUS_INVALID
