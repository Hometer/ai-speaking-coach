"""待复练队列：回顾建议 -> 跨日队列 -> 到期复测 -> 掌握 / 争议 / 失效。"""
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.core.errors import AppError
from app.models.entities import IntentResult, ReviewDueEvent, ReviewQueueItem, Session
from app.services.recall_judge import judge_recall
from app.services import intent_result
from app.services.operations import durable
from app.services import recall_service

STATUS_PENDING = "pending"            # 待练
STATUS_AWAITING_RETEST = "awaiting_retest"  # 待复测（跟读已完成）
STATUS_MASTERED = "mastered"          # 已掌握
STATUS_DISPUTED = "disputed"          # 有争议
STATUS_INVALID = "invalid"            # 已失效

DUE_HOURS = 24  # 复测最早间隔（第二次独立成功至少 24h 后）


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_due(db: DbSession, item: ReviewQueueItem, result: str, now: datetime | None = None) -> None:
    now = now or _now()
    event = db.query(ReviewDueEvent).filter_by(queue_item_id=item.id, valid=True, resolved_at=None).order_by(ReviewDueEvent.id.desc()).first()
    if event is None:
        return  # 旧记录无快照，不倒填虚假到期证据
    due = event.due_at.replace(tzinfo=timezone.utc) if event.due_at.tzinfo is None else event.due_at
    if due > now:
        event.valid = False
    else:
        event.resolved_at = now
        event.result = result


def _schedule_due(db: DbSession, item: ReviewQueueItem, due_at: datetime) -> None:
    item.due_at = due_at
    item.help_shown = False
    item.attempt_stage = "recall"
    item.question_text = ""
    item.question_version = (item.question_version or 0) + 1
    db.add(ReviewDueEvent(user_id=item.user_id, queue_item_id=item.id, due_at=due_at))


def _to_dict(item: ReviewQueueItem, reveal: bool = False) -> dict[str, Any]:
    return {
        "id": item.id,
        "attempt_stage": item.attempt_stage,
        "question_text": item.question_text,
        "question_version": item.question_version,
        "source_session_id": item.source_session_id,
        "source_turn_id": item.source_turn_id,
        "target_ability": item.target_ability,
        "suggestion_sentence": item.suggestion_sentence if (reveal or item.help_shown) else "",
        "meaning_zh": item.meaning_zh,
        "help_shown": item.help_shown,
        "status": item.status,
        "success_count": item.success_count,
        "due_at": item.due_at.isoformat() if item.due_at else None,
        "last_practiced_at": item.last_practiced_at.isoformat() if item.last_practiced_at else None,
        "retest_result": item.retest_result,
    }


def add_to_queue(db: DbSession, user_id: str, source_session_id: str, source_turn_id: str) -> dict[str, Any]:
    """用户确认后把回顾建议加入队列。幂等：同一来源 turn 只创建一个有效项。"""
    from app.services.prompts.scenarios import SCENARIOS

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

    scene = SCENARIOS.get(source.scene_id or "", {})
    meaning_zh = scene.get("goal_zh", "") or imp.get("explanation_cn", "")

    item = ReviewQueueItem(
        user_id=user_id,
        source_session_id=source_session_id,
        source_turn_id=source_turn_id,
        target_ability="expression",
        suggestion_sentence=sentence,
        meaning_zh=meaning_zh,
        status=STATUS_PENDING,
        due_at=_now() + timedelta(hours=DUE_HOURS),
    )
    db.add(item)
    db.flush()
    _schedule_due(db, item, item.due_at)
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


def reveal_item(db: DbSession, user_id: str, item_id: int) -> dict[str, Any]:
    """用户显式看答案：服务端落 help_shown，之后复测不再计独立。"""
    item = db.get(ReviewQueueItem, item_id)
    if item is None or item.user_id != user_id:
        raise AppError("NOT_FOUND", "队列项不存在", status_code=404)
    item.help_shown = True
    db.commit()
    return _to_dict(item, reveal=True)


@durable("queue_retest", key_arg="attempt_key", object_arg="item_id")
def submit_retest(
    db: DbSession,
    user_id: str,
    item_id: int,
    answer: str,
    input_type: str = "text",
    attempt_key: str = "",
) -> dict[str, Any]:
    """复测提交（确定性）：语音 + 无帮助 + 相关 + 非照抄才独立；两次独立（间隔 >=24h）才掌握。"""
    item = db.get(ReviewQueueItem, item_id)
    if item is None or item.user_id != user_id:
        raise AppError("NOT_FOUND", "队列项不存在", status_code=404)

    answer = (answer or "").strip()
    replayed = intent_result.replay(db, user_id, "queue_retest", str(item_id), attempt_key, input_type, answer)
    if replayed is not None:
        return replayed

    # 幂等检查放最前（仅次归属校验）：同键同内容返回首次结果，同键异内容 409
    if attempt_key:
        if item.last_attempt_key == attempt_key:
            if item.last_attempt_answer == answer:
                return _to_dict(item)
            raise AppError("IDEMPOTENCY_CONFLICT", "同一请求键内容不一致", status_code=409)

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

    result = recall_service.evaluate(db, item, answer, input_type, request_key=attempt_key)

    if result == "recall_completed":
        db.commit()
        response = _to_dict(item)
        response["retest_result"] = result
        intent_result.remember(db, user_id, "queue_retest", str(item_id), attempt_key, input_type, answer, response)
        return response

    _resolve_due(db, item, result)

    item.last_practiced_at = _now()
    item.retest_result = result
    if attempt_key:
        item.last_attempt_key = attempt_key
        item.last_attempt_answer = answer

    if result == "independent":
        item.success_count = (item.success_count or 0) + 1
        if item.success_count >= 2:
            item.status = STATUS_MASTERED
        else:
            item.status = STATUS_AWAITING_RETEST
            _schedule_due(db, item, _now() + timedelta(hours=DUE_HOURS))
    else:
        # 文字完成/有帮助/失败/待判断：不升级成功次数，回到待复测并顺延
        item.status = STATUS_AWAITING_RETEST
        _schedule_due(db, item, _now() + timedelta(hours=DUE_HOURS))

    db.commit()
    response = _to_dict(item)
    intent_result.remember(db, user_id, "queue_retest", str(item_id), attempt_key, input_type, answer, response)
    return response


def mark_disputed(db: DbSession, user_id: str, item_id: int) -> dict[str, Any]:
    item = db.get(ReviewQueueItem, item_id)
    if item is None or item.user_id != user_id:
        raise AppError("NOT_FOUND", "队列项不存在", status_code=404)
    item.status = STATUS_DISPUTED
    for event in db.query(ReviewDueEvent).filter_by(queue_item_id=item.id).all():
        event.valid = False
    db.commit()
    return _to_dict(item)


def skip_item(db: DbSession, user_id: str, item_id: int) -> dict[str, Any]:
    """跳过 / 延后：顺延 24h。"""
    item = db.get(ReviewQueueItem, item_id)
    if item is None or item.user_id != user_id:
        raise AppError("NOT_FOUND", "队列项不存在", status_code=404)
    _resolve_due(db, item, "skipped")
    _schedule_due(db, item, _now() + timedelta(hours=DUE_HOURS))
    db.commit()
    return _to_dict(item)


def delete_item(db: DbSession, user_id: str, item_id: int) -> None:
    item = db.get(ReviewQueueItem, item_id)
    if item is None or item.user_id != user_id:
        raise AppError("NOT_FOUND", "队列项不存在", status_code=404)
    db.query(ReviewDueEvent).filter_by(queue_item_id=item.id).delete()
    db.query(IntentResult).filter(IntentResult.object_id == str(item.id), IntentResult.scope.in_(["queue_retest", "queue_voice_asr", "queue_voice_result"])).delete()
    from app.models.entities import RecallEvidence, RequestOperation
    db.query(RecallEvidence).filter_by(queue_item_id=item.id).delete()
    db.query(RequestOperation).filter(RequestOperation.user_id == user_id,
        RequestOperation.object_id == str(item.id), RequestOperation.scope.in_(["queue_retest", "voice"])).delete()
    db.delete(item)
    db.commit()


def invalidate_by_turn(db: DbSession, turn_id: str) -> None:
    """源建议失效时，关联队列项标失效。"""
    for item in db.query(ReviewQueueItem).filter(ReviewQueueItem.source_turn_id == turn_id).all():
        item.status = STATUS_INVALID
        for event in db.query(ReviewDueEvent).filter_by(queue_item_id=item.id).all():
            event.valid = False
