"""七日训练计划：确定性规则生成（不调模型），按水平与最近表现个性化。"""
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.core.errors import AppError
from app.models.entities import Evaluation, Session, TrainingPlan, User
from app.services import review_queue as queue_svc
from app.services.prompts.scenarios import get_approved_scenes

# 目标类型 → 场景系列（只组合已审核场景）
GOAL_SCENE_MAP = {
    "job_prep": ["job_interview", "new_colleague", "meeting_opinion"],   # 求职准备
    "workplace": ["new_colleague", "meeting_opinion", "job_interview"],  # 职场表达
    "travel": ["hotel_checkin", "restaurant_order"],                      # 旅行准备
    "daily": ["new_friend", "hobby"],                                    # 日常开口
}

# 入门水平优先的低阻力场景（自我介绍/兴趣）
_LOW_FRICTION = ["new_friend", "hobby", "new_colleague"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _recent_independent_rate(db: DbSession, user_id: str) -> float | None:
    """最近可评估子目标的独立率；无历史返回 None。"""
    session_ids = [s.id for s in db.query(Session).filter(Session.user_id == user_id).all()]
    if not session_ids:
        return None
    evals = db.query(Evaluation).filter(Evaluation.session_id.in_(session_ids)).all()
    evaluable = [e for e in evals if e.result in ("independent", "hinted", "incomplete")]
    if not evaluable:
        return None
    return sum(1 for e in evaluable if e.result == "independent") / len(evaluable)


def _generate_days(db: DbSession, user_id: str, goal_type: str, level: str) -> list[dict[str, Any]]:
    scene_ids = list(GOAL_SCENE_MAP.get(goal_type, ["job_interview", "new_friend", "hobby"]))
    scenes = {s["scene_id"]: s for s in get_approved_scenes()}
    due = queue_svc.get_due_item(db, user_id)
    rate = _recent_independent_rate(db, user_id)

    # 个性化调整：入门水平把低阻力场景提前；最近独立率低时写调整原因
    if level == "beginner":
        low = [s for s in _LOW_FRICTION if s in scene_ids]
        rest = [s for s in scene_ids if s not in low]
        scene_ids = low + rest

    days: list[dict[str, Any]] = []
    for day in range(1, 8):
        scene_id = scene_ids[(day - 1) % len(scene_ids)]
        reason = ""
        if level == "beginner" and day == 1:
            reason = "入门水平：第一天先安排低阻力的自我介绍场景"
        elif rate is None and day == 1:
            reason = "暂无练习历史，使用审核过的默认计划"
        elif rate is not None and rate < 0.3:
            reason = "最近独立完成率较低，优先复习基础表达"
        days.append(
            {
                "day": day,
                "scene_id": scene_id,
                "scene_label": scenes.get(scene_id, {}).get("label", scene_id),
                "status": "pending",
                "retest_item_id": due["id"] if (due and day == 1) else None,
                "adjustment_reason": reason,
            }
        )
    return days


def _to_dict(plan: TrainingPlan, db: DbSession | None = None) -> dict[str, Any]:
    days = json.loads(plan.days_json) if plan.days_json else []
    if db is not None:
        # 完成关联：统计计划创建后各场景「结束且有回顾」的会话数，
        # 按 day 升序给对应场景的待练日逐一点亮（一练只点亮最早的同场景待练日）。
        scene_done: dict[str, int] = {}
        plan_created = plan.created_at
        if plan_created is not None and plan_created.tzinfo is None:
            plan_created = plan_created.replace(tzinfo=timezone.utc)
        for s in db.query(Session).filter(
            Session.user_id == plan.user_id,
            Session.status == "ENDED",
            Session.review != "",
            Session.ended_at.isnot(None),
        ).all():
            ended = s.ended_at
            if ended.tzinfo is None:
                ended = ended.replace(tzinfo=timezone.utc)
            if plan_created is None or ended >= plan_created:
                scene_done[s.scene_id] = scene_done.get(s.scene_id, 0) + 1
        for d in sorted(days, key=lambda x: x.get("day", 0)):
            if d.get("status") not in ("pending", "completed"):
                continue
            if d.get("status") == "completed":
                continue
            if scene_done.get(d.get("scene_id"), 0) > 0:
                d["status"] = "completed"
                scene_done[d["scene_id"]] -= 1
    return {
        "id": plan.id,
        "goal_type": plan.goal_type,
        "days": days,
        "status": plan.status,
    }


def create_or_renew_plan(db: DbSession, user_id: str, goal_type: str) -> dict[str, Any]:
    """创建/重新生成计划：已有活动计划则替换。"""
    user = db.get(User, user_id)
    level = user.level if user else "basic"
    days = _generate_days(db, user_id, goal_type, level)
    existing = (
        db.query(TrainingPlan)
        .filter(TrainingPlan.user_id == user_id, TrainingPlan.status.in_(["active", "paused"]))
        .first()
    )
    if existing:
        existing.goal_type = goal_type
        existing.days_json = json.dumps(days, ensure_ascii=False)
        existing.status = "active"
        existing.updated_at = _now()
        plan = existing
    else:
        plan = TrainingPlan(user_id=user_id, goal_type=goal_type, days_json=json.dumps(days, ensure_ascii=False))
        db.add(plan)
    db.commit()
    db.refresh(plan)
    return _to_dict(plan, db)


def get_plan(db: DbSession, user_id: str) -> dict[str, Any] | None:
    plan = (
        db.query(TrainingPlan)
        .filter(TrainingPlan.user_id == user_id, TrainingPlan.status.in_(["active", "paused"]))
        .first()
    )
    return _to_dict(plan, db) if plan else None


def _set_status(db: DbSession, user_id: str, status: str) -> dict[str, Any]:
    plan = (
        db.query(TrainingPlan)
        .filter(TrainingPlan.user_id == user_id, TrainingPlan.status.in_(["active", "paused"]))
        .first()
    )
    if plan is None:
        raise AppError("NOT_FOUND", "暂无训练计划", status_code=404)
    plan.status = status
    plan.updated_at = _now()
    db.commit()
    return _to_dict(plan, db)


def pause_plan(db: DbSession, user_id: str) -> dict[str, Any]:
    return _set_status(db, user_id, "paused")


def resume_plan(db: DbSession, user_id: str) -> dict[str, Any]:
    return _set_status(db, user_id, "active")


def skip_day(db: DbSession, user_id: str, day: int) -> dict[str, Any]:
    plan = (
        db.query(TrainingPlan)
        .filter(TrainingPlan.user_id == user_id, TrainingPlan.status.in_(["active", "paused"]))
        .first()
    )
    if plan is None:
        raise AppError("NOT_FOUND", "暂无训练计划", status_code=404)
    days = json.loads(plan.days_json)
    for d in days:
        if d.get("day") == day:
            d["status"] = "skipped"
    plan.days_json = json.dumps(days, ensure_ascii=False)
    plan.updated_at = _now()
    db.commit()
    return _to_dict(plan, db)


def restart_plan(db: DbSession, user_id: str) -> dict[str, Any]:
    plan = (
        db.query(TrainingPlan)
        .filter(TrainingPlan.user_id == user_id, TrainingPlan.status.in_(["active", "paused"]))
        .first()
    )
    if plan is None:
        raise AppError("NOT_FOUND", "暂无训练计划", status_code=404)
    days = json.loads(plan.days_json)
    for d in days:
        d["status"] = "pending"
    plan.days_json = json.dumps(days, ensure_ascii=False)
    plan.updated_at = _now()
    db.commit()
    return _to_dict(plan, db)
