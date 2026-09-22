"""七日训练计划：确定性规则生成（不调模型）。"""
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.core.errors import AppError
from app.models.entities import TrainingPlan
from app.services import review_queue as queue_svc
from app.services.prompts.scenarios import get_approved_scenes

# 目标类型 → 场景系列（只组合已审核场景）
GOAL_SCENE_MAP = {
    "job_prep": ["job_interview", "new_colleague", "meeting_opinion"],   # 求职准备
    "workplace": ["new_colleague", "meeting_opinion", "job_interview"],  # 职场表达
    "travel": ["hotel_checkin", "restaurant_order"],                      # 旅行准备
    "daily": ["new_friend", "hobby"],                                    # 日常开口
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _generate_days(db: DbSession, user_id: str, goal_type: str) -> list[dict[str, Any]]:
    scene_ids = GOAL_SCENE_MAP.get(goal_type, ["job_interview", "new_friend", "hobby"])
    scenes = {s["scene_id"]: s for s in get_approved_scenes()}
    due = queue_svc.get_due_item(db, user_id)
    days: list[dict[str, Any]] = []
    for day in range(1, 8):
        scene_id = scene_ids[(day - 1) % len(scene_ids)]
        days.append(
            {
                "day": day,
                "scene_id": scene_id,
                "scene_label": scenes.get(scene_id, {}).get("label", scene_id),
                "status": "pending",
                "retest_item_id": due["id"] if (due and day == 1) else None,
                "adjustment_reason": "",
            }
        )
    return days


def _to_dict(plan: TrainingPlan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "goal_type": plan.goal_type,
        "days": json.loads(plan.days_json) if plan.days_json else [],
        "status": plan.status,
    }


def create_or_renew_plan(db: DbSession, user_id: str, goal_type: str) -> dict[str, Any]:
    """创建/重新生成计划：已有活动计划则替换。"""
    days = _generate_days(db, user_id, goal_type)
    existing = (
        db.query(TrainingPlan)
        .filter(TrainingPlan.user_id == user_id, TrainingPlan.status == "active")
        .first()
    )
    if existing:
        existing.goal_type = goal_type
        existing.days_json = json.dumps(days, ensure_ascii=False)
        existing.updated_at = _now()
        plan = existing
    else:
        plan = TrainingPlan(user_id=user_id, goal_type=goal_type, days_json=json.dumps(days, ensure_ascii=False))
        db.add(plan)
    db.commit()
    db.refresh(plan)
    return _to_dict(plan)


def get_plan(db: DbSession, user_id: str) -> dict[str, Any] | None:
    plan = (
        db.query(TrainingPlan)
        .filter(TrainingPlan.user_id == user_id, TrainingPlan.status == "active")
        .first()
    )
    return _to_dict(plan) if plan else None


def skip_day(db: DbSession, user_id: str, day: int) -> dict[str, Any]:
    plan = (
        db.query(TrainingPlan)
        .filter(TrainingPlan.user_id == user_id, TrainingPlan.status == "active")
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
    return _to_dict(plan)


def restart_plan(db: DbSession, user_id: str) -> dict[str, Any]:
    plan = (
        db.query(TrainingPlan)
        .filter(TrainingPlan.user_id == user_id, TrainingPlan.status == "active")
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
    return _to_dict(plan)
