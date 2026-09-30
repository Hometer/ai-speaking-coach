"""七日训练计划：确定性规则生成（不调模型），按水平与最近表现个性化。"""
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

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
_LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _local_date(value: datetime | None = None):
    value = value or _now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_LOCAL_TZ).date()


def _recent_independent_rate(db: DbSession, user_id: str) -> float | None:
    """最近可评估子目标的独立率；无历史返回 None。"""
    session_ids = [s.id for s in db.query(Session).filter(Session.user_id == user_id).all()]
    if not session_ids:
        return None
    evals = db.query(Evaluation).filter(Evaluation.session_id.in_(session_ids)).all()
    evaluable = [e for e in evals if e.input_type == "voice" and e.result in ("independent", "hinted", "incomplete")]
    if not evaluable:
        return None
    return sum(1 for e in evaluable if e.result == "independent") / len(evaluable)


def _generate_days(db: DbSession, user_id: str, goal_type: str, level: str, available_minutes: int) -> list[dict[str, Any]]:
    scene_ids = list(GOAL_SCENE_MAP.get(goal_type, ["job_interview", "new_friend", "hobby"]))
    scenes = {s["scene_id"]: s for s in get_approved_scenes()}
    due = queue_svc.get_due_item(db, user_id) if available_minutes >= 10 else None
    rate = _recent_independent_rate(db, user_id)

    # 个性化调整：入门水平把低阻力场景提前；最近独立率低时写调整原因
    if level == "beginner":
        low = [s for s in _LOW_FRICTION if s in scene_ids]
        rest = [s for s in scene_ids if s not in low]
        scene_ids = low + rest

    days: list[dict[str, Any]] = []
    start_date = _local_date()
    for day in range(1, 8):
        # 近期表现低时第二天复习第一天的主场景，实际改变安排而非只改说明。
        scene_index = 0 if rate is not None and rate < 0.3 and day == 2 else (day - 1) % len(scene_ids)
        scene_id = scene_ids[scene_index]
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
                "scheduled_date": (start_date + timedelta(days=day - 1)).isoformat(),
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
    for d in days:
        if not d.get("scheduled_date"):
            d["scheduled_date"] = (_local_date(plan.created_at) + timedelta(days=int(d.get("day", 1)) - 1)).isoformat()
    if db is not None:
        # 只认明确绑定计划和具体日期的有效练习；旧计划不倒推完成证据。
        completed_days: set[int] = set()
        for s in db.query(Session).filter(
            Session.user_id == plan.user_id,
            Session.plan_id == plan.id,
            Session.status == "ENDED",
            Session.review != "",
            Session.ended_at.isnot(None),
        ).all():
            day = next((d for d in days if d.get("day") == s.plan_day), None)
            if day and day.get("scene_id") == s.scene_id and _local_date(s.ended_at).isoformat() == day.get("scheduled_date"):
                completed_days.add(s.plan_day)
        for d in days:
            if d.get("status") not in ("pending", "completed"):
                continue
            d["status"] = "completed" if d.get("day") in completed_days else "pending"
    return {
        "id": plan.id,
        "goal_type": plan.goal_type,
        "available_minutes": plan.available_minutes or 5,
        "days": days,
        "status": plan.status,
    }


def create_or_renew_plan(db: DbSession, user_id: str, goal_type: str, available_minutes: int = 5) -> dict[str, Any]:
    """创建/重新生成计划：已有活动计划则替换。"""
    user = db.get(User, user_id)
    level = user.level if user else "basic"
    if available_minutes not in (5, 10, 15):
        raise AppError("INVALID_INPUT", "可用时间应为 5、10 或 15 分钟", status_code=422)
    if goal_type not in GOAL_SCENE_MAP:
        raise AppError("INVALID_INPUT", "未知的训练目标", status_code=422)
    existing = (
        db.query(TrainingPlan)
        .filter(TrainingPlan.user_id == user_id, TrainingPlan.status.in_(["active", "paused"]))
        .first()
    )
    if existing and existing.status == "active" and existing.goal_type == goal_type and existing.available_minutes == available_minutes:
        return _to_dict(existing, db)
    days = _generate_days(db, user_id, goal_type, level, available_minutes)
    if existing:
        existing.status = "superseded"
    plan = TrainingPlan(user_id=user_id, goal_type=goal_type, available_minutes=available_minutes, days_json=json.dumps(days, ensure_ascii=False))
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
    if status == "paused" and plan.status == "active":
        plan.paused_at = _now()
    elif status == "active" and plan.status == "paused" and plan.paused_at:
        shift = max(0, (_local_date() - _local_date(plan.paused_at)).days)
        if shift:
            days = json.loads(plan.days_json)
            for d in days:
                if d.get("status") == "pending":
                    old = date.fromisoformat(d.get("scheduled_date") or (_local_date(plan.created_at) + timedelta(days=int(d.get("day", 1)) - 1)).isoformat())
                    d["scheduled_date"] = (old + timedelta(days=shift)).isoformat()
            plan.days_json = json.dumps(days, ensure_ascii=False)
        plan.paused_at = None
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
    goal_type, available_minutes = plan.goal_type, plan.available_minutes or 5
    plan.status = "superseded"
    db.commit()
    return create_or_renew_plan(db, user_id, goal_type, available_minutes)


def bind_day(db: DbSession, user_id: str, scene_id: str, plan_id: int | None, plan_day: int | None) -> tuple[int | None, int | None]:
    """只有从具体计划日入口创建的练习才计入该日，普通练习不自动认领。"""
    if plan_id is None and plan_day is None:
        return None, None
    if plan_id is None or plan_day is None:
        raise AppError("INVALID_PLAN_DAY", "计划入口缺少计划编号或日期", status_code=422)
    plan = db.get(TrainingPlan, plan_id)
    if plan is None or plan.user_id != user_id or plan.status != "active":
        raise AppError("INVALID_PLAN", "训练计划不存在或已暂停", status_code=409)
    days = _to_dict(plan, db)["days"]
    today = _local_date().isoformat()
    day = next((d for d in days if d["day"] == plan_day), None)
    if day is None or day["scheduled_date"] != today or day["scene_id"] != scene_id or day["status"] != "pending":
        raise AppError("INVALID_PLAN_DAY", "该场景今天不在计划待练日", status_code=409)
    return plan.id, day["day"]
