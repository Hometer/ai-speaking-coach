"""进步中心：事实型指标（后端计算，前端不自行拼接权威结论）。"""
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.models.entities import DrillAttempt, Evaluation, ReviewQueueItem, Session, Turn


def get_progress(db: DbSession, user_id: str) -> dict[str, Any]:
    sessions = db.query(Session).filter(Session.user_id == user_id).all()
    session_ids = [s.id for s in sessions]

    # 完成练习数：已生成回顾或已结束的会话
    completed_count = sum(1 for s in sessions if s.review or s.status == "ENDED")

    # 独立完成率：独立评估数 / 有效评估总数（independent/hinted/incomplete）
    evals = (
        db.query(Evaluation).filter(Evaluation.session_id.in_(session_ids)).all()
        if session_ids
        else []
    )
    independent_count = sum(1 for e in evals if e.result == "independent")
    evaluable = [e for e in evals if e.result in ("independent", "hinted", "incomplete")]
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

    # 到期复练完成率 + 已掌握项目数
    queue = db.query(ReviewQueueItem).filter(ReviewQueueItem.user_id == user_id).all()
    mastered_count = sum(1 for q in queue if q.status == "mastered")
    retested = [q for q in queue if q.retest_result]
    due_retest_done_rate = (
        round(sum(1 for q in retested if q.retest_result == "independent") / len(retested), 3)
        if retested
        else 0.0
    )

    # 语音可评估率：有效语音评测数 / 提交语音数
    voice_turns = [t for t in user_turns if t.input_type == "voice"]
    voice_evaluable = sum(1 for t in voice_turns if t.eval_result)
    voice_evaluable_rate = round(voice_evaluable / len(voice_turns), 3) if voice_turns else 0.0

    # v0.8 新增：脱稿完成数 / 迁移成功数（按 DrillAttempt 结果计数）
    drill_attempts = db.query(DrillAttempt).filter(DrillAttempt.user_id == user_id).all()
    drill_completed_count = sum(1 for a in drill_attempts if a.result == "independent")
    transfer_completed_count = sum(1 for a in drill_attempts if a.result == "transfer")

    return {
        "completed_count": completed_count,
        "independent_rate": independent_rate,
        "help_usage_rate": help_usage_rate,
        "due_retest_done_rate": due_retest_done_rate,
        "mastered_count": mastered_count,
        "voice_evaluable_rate": voice_evaluable_rate,
        "drill_completed_count": drill_completed_count,
        "transfer_completed_count": transfer_completed_count,
        "calc_version": "v2",
    }
