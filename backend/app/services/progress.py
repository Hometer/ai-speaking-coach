"""进步中心：事实型指标（后端计算，前端不自行拼接权威结论）。"""
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.models.entities import DrillAttempt, Evaluation, ReviewQueueItem, Session, Turn


def get_progress(db: DbSession, user_id: str) -> dict[str, Any]:
    sessions = db.query(Session).filter(Session.user_id == user_id).all()
    session_ids = [s.id for s in sessions]
    session_scene = {s.id: s.scene_id for s in sessions}

    # 完成练习数：已结束且有回顾（AC10-15，任意结束不算完成）
    completed_count = sum(1 for s in sessions if s.status == "ENDED" and s.review)

    # 独立完成率：独立评估数 / 可评估子目标尝试数（independent/hinted/incomplete）
    evals = (
        db.query(Evaluation).filter(Evaluation.session_id.in_(session_ids)).all()
        if session_ids
        else []
    )
    independent_count = sum(1 for e in evals if e.result == "independent" and e.input_type == "voice")
    text_completed_count = sum(1 for e in evals if e.result == "text_completed")
    # 可评估分母只统计语音子目标（文字回答不参与口语独立率）
    evaluable = [
        e for e in evals
        if e.input_type == "voice" and e.result in ("independent", "hinted", "incomplete")
    ]
    independent_rate = round(independent_count / len(evaluable), 3) if evaluable else 0.0

    # 帮助使用率：使用答案辅助的有效回答占比
    user_turns = (
        db.query(Turn)
        .filter(Turn.session_id.in_(session_ids), Turn.input_type.in_(("voice", "text")))
        .all()
        if session_ids
        else []
    )
    helped = sum(1 for t in user_turns if t.hint_used or t.help_type in ("organize", "translate"))
    help_usage_rate = round(helped / len(user_turns), 3) if user_turns else 0.0

    # 到期复练完成率：分母为有效到期项目（due 已过且非失效/争议），分子为其中独立完成
    queue = db.query(ReviewQueueItem).filter(ReviewQueueItem.user_id == user_id).all()
    mastered_count = sum(1 for q in queue if q.status == "mastered")
    now = datetime.now(timezone.utc)
    due_items = [
        q for q in queue
        if q.status in ("pending", "awaiting_retest", "mastered")
        and ((q.due_at is not None and q.due_at <= now) or q.last_practiced_at is not None)
    ]
    due_done = sum(1 for q in due_items if q.retest_result == "independent")
    due_retest_done_rate = round(due_done / len(due_items), 3) if due_items else 0.0

    # 语音可评估率：有效语音评测数 / 提交语音数
    voice_turns = [t for t in user_turns if t.input_type == "voice"]
    voice_evaluable = sum(1 for t in voice_turns if t.eval_result)
    voice_evaluable_rate = round(voice_evaluable / len(voice_turns), 3) if voice_turns else 0.0

    # v0.8 新增：脱稿完成数 / 迁移成功数（按 DrillAttempt 结果计数）
    drill_attempts = db.query(DrillAttempt).filter(DrillAttempt.user_id == user_id).all()
    invalid_drill_ids = {s.id for s in sessions if s.source_invalid}
    valid_drill_attempts = [a for a in drill_attempts if a.drill_session_id not in invalid_drill_ids]
    drill_completed_count = sum(1 for a in valid_drill_attempts if a.result == "independent")
    transfer_completed_count = sum(1 for a in valid_drill_attempts if a.result == "transfer")

    # 样本数（供人工核对分母）
    samples = {
        "evaluable_subgoals": len(evaluable),
        "voice_turns": len(voice_turns),
        "due_items": len(due_items),
    }

    # 按场景独立率（仅语音）
    by_scene: dict[str, dict[str, Any]] = {}
    for e in evals:
        if e.input_type != "voice":
            continue
        scene = session_scene.get(e.session_id, "")
        bucket = by_scene.setdefault(scene, {"independent": 0, "evaluable": 0})
        bucket["evaluable"] += 1 if e.result in ("independent", "hinted", "incomplete") else 0
        bucket["independent"] += 1 if e.result == "independent" else 0
    by_scene = {
        k: {
            "independent_rate": round(v["independent"] / v["evaluable"], 3) if v["evaluable"] else 0.0,
            "evaluable": v["evaluable"],
        }
        for k, v in by_scene.items()
    }

    # 最近 7 天独立率趋势
    trend: list[dict[str, Any]] = []
    for i in range(6, -1, -1):
        day = (now - timedelta(days=i)).date()
        day_evals = [
            e for e in evals
            if e.input_type == "voice" and e.created_at is not None and e.created_at.date() == day
            and e.result in ("independent", "hinted", "incomplete")
        ]
        day_ind = sum(1 for e in day_evals if e.result == "independent")
        trend.append({
            "date": day.isoformat(),
            "independent_rate": round(day_ind / len(day_evals), 3) if day_evals else 0.0,
            "evaluable": len(day_evals),
        })

    # 已掌握列表
    mastered_list = [
        {
            "id": q.id,
            "suggestion_sentence": q.suggestion_sentence,
            "target_ability": q.target_ability,
            "last_practiced_at": q.last_practiced_at.isoformat() if q.last_practiced_at else None,
        }
        for q in queue if q.status == "mastered"
    ]

    return {
        "completed_count": completed_count,
        "independent_rate": independent_rate,
        "text_completed_count": text_completed_count,
        "help_usage_rate": help_usage_rate,
        "due_retest_done_rate": due_retest_done_rate,
        "mastered_count": mastered_count,
        "voice_evaluable_rate": voice_evaluable_rate,
        "drill_completed_count": drill_completed_count,
        "transfer_completed_count": transfer_completed_count,
        "samples": samples,
        "by_scene": by_scene,
        "trend": trend,
        "mastered_list": mastered_list,
        "calc_version": "v4",
    }
