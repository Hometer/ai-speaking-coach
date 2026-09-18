"""练习回顾生成：确定性聚合（mock）+ 真实 LLM 结构化（待验）。"""
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.models.entities import Turn


def _improvement(t: Turn) -> dict[str, Any]:
    original = t.user_text or t.transcript
    return {
        "original": original,
        "suggestion": "Complete the sentence with more detail about what you did and the result.",
        "explanation_cn": "表达可以更完整：补充做了什么、结果如何。",
        "practice_sentence": "I have X years of experience in Y, and I am good at Z.",
        "turn_id": t.id,
    }


def _ratings(goal_status: str, good_count: int) -> dict[str, str]:
    if goal_status == "independent":
        return {"vocabulary": "adequate", "grammar": "adequate", "communication": "complete"}
    if good_count >= 1:
        return {"vocabulary": "adequate", "grammar": "needs_work", "communication": "in_progress"}
    return {"vocabulary": "needs_work", "grammar": "needs_work", "communication": "in_progress"}


def build_review(db: DbSession, session, end_reason: str) -> dict[str, Any]:
    """根据会话真实数据生成结构化回顾（mock 为确定性聚合）。"""
    turns = (
        db.query(Turn).filter(Turn.session_id == session.id).order_by(Turn.seq).all()
    )
    user_turns = [t for t in turns if t.input_type in ("voice", "text")]

    latest_eval = ""
    for t in reversed(user_turns):
        if t.eval_result:
            latest_eval = t.eval_result
            break
    goal_status = latest_eval or "not_started"
    if end_reason == "goal_reached":
        goal_status = "independent"

    evidence = [t.id for t in user_turns if t.eval_result == "independent"]
    if not evidence:
        evidence = [t.id for t in user_turns if t.eval_result]

    good_fragments = [
        t.user_text or t.transcript for t in user_turns if t.eval_result == "independent"
    ]

    improvements = [
        _improvement(t)
        for t in user_turns
        if t.eval_result in ("incomplete", "hinted") and (t.user_text or t.transcript)
    ][:3]

    breakdown = {"voice": 0, "text": 0, "practice": 0, "hinted": 0}
    for t in turns:
        if t.input_type in breakdown:
            breakdown[t.input_type] += 1
    breakdown["hinted"] = sum(1 for t in user_turns if t.hint_used)

    return {
        "goal_status": goal_status,
        "evidence": evidence,
        "good_fragments": good_fragments,
        "improvements": improvements,
        "ratings": _ratings(goal_status, len(good_fragments)),
        "next_suggestion": (
            "Practice introducing one more experience and answer one follow-up "
            "question on your own."
        ),
        "end_reason": end_reason,
        "input_breakdown": breakdown,
    }
