"""练习回顾生成：确定性聚合（mock）+ 真实 LLM 结构化（待验）。"""
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.models.entities import Turn


def _improvement(t: Turn, scene: dict) -> dict[str, Any]:
    original = t.user_text or t.transcript
    refs = scene.get("reference_expressions") or []
    goal_zh = scene.get("goal_zh", "本次目标")
    return {
        "original": original,
        "suggestion": f"Complete the sentence '{original}' with more detail about {goal_zh}.",
        "explanation_cn": f"围绕「{goal_zh}」把表达补得更完整。",
        "practice_sentence": refs[0] if refs else "",
        "turn_id": t.id,
    }


def _ratings(goal_status: str, good_count: int) -> dict[str, str]:
    if goal_status == "independent":
        return {"vocabulary": "adequate", "grammar": "adequate", "communication": "complete"}
    if good_count >= 1:
        return {"vocabulary": "adequate", "grammar": "needs_work", "communication": "in_progress"}
    return {"vocabulary": "needs_work", "grammar": "needs_work", "communication": "in_progress"}


def dedupe_improvements(review: dict[str, Any]) -> dict[str, Any]:
    """同一原句的改进建议只展示一次，不同原句保留各自证据。"""
    seen: set[str] = set()
    unique = []
    for item in review.get("improvements", []):
        key = " ".join(str(item.get("original", "")).split()).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return {**review, "improvements": unique[:3]}


def build_review(db: DbSession, session, end_reason: str) -> dict[str, Any]:
    """根据会话真实数据生成结构化回顾（mock 为确定性聚合，按场景）。"""
    from app.services.prompts.scenarios import SCENARIOS

    scene = SCENARIOS.get(getattr(session, "scene_id", ""), {})
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
        _improvement(t, scene)
        for t in user_turns
        if t.eval_result in ("incomplete", "hinted") and (t.user_text or t.transcript)
    ][:3]

    breakdown = {"voice": 0, "text": 0, "practice": 0, "hinted": 0}
    for t in turns:
        if t.input_type in breakdown:
            breakdown[t.input_type] += 1
    breakdown["hinted"] = sum(1 for t in user_turns if t.hint_used)

    return dedupe_improvements({
        "goal_status": goal_status,
        "evidence": evidence,
        "good_fragments": good_fragments,
        "improvements": improvements,
        "ratings": _ratings(goal_status, len(good_fragments)),
        "next_suggestion": f"Practice {scene.get('goal_zh', 'this scene')} again on your own.",
        "end_reason": end_reason,
        "input_breakdown": breakdown,
    })
