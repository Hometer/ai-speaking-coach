"""练习回顾生成：确定性聚合（mock）+ 真实 LLM 结构化（待验）。"""
from typing import Any

from sqlalchemy.orm import Session as DbSession

import json
from app.core.config import settings
from app.models.entities import Turn, Evaluation
from app.services.answer_feedback import grounded, VERSION


def _improvements(db, turns):
    result = []
    for turn in turns:
        if turn.eval_result in ("revised", "insufficient_evidence"):
            continue
        evaluation = db.query(Evaluation).filter_by(turn_id=turn.id, revision_version=turn.revision_version).order_by(Evaluation.id.desc()).first()
        if not evaluation or evaluation.result == "revised":
            continue
        try:
            items = grounded(turn.revised_text or turn.user_text or turn.transcript, json.loads(evaluation.feedback_json or "[]"))
        except (ValueError, TypeError):
            items = []
        for item in items:
            result.append({"original":turn.revised_text or turn.user_text or turn.transcript,
                "suggestion":item.practice_sentence,"explanation_cn":item.explanation_cn,
                "practice_sentence":item.practice_sentence,"source_quote":item.source_quote,
                "issue_type":item.issue_type,"turn_id":turn.id,"revision_version":turn.revision_version,
                "feedback_version":VERSION,"feedback_source":evaluation.feedback_origin})
    return result[:2]


def _ratings(goal_status: str, good_count: int) -> dict[str, str]:
    # 语法/词汇不因"目标独立"自动推断达标；无可靠证据不评级（REQ-08）。
    communication = "complete" if goal_status == "independent" else "in_progress"
    return {"vocabulary": "not_evaluated", "grammar": "not_evaluated", "communication": communication}


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
    return {**review, "improvements": unique[:2]}


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

    improvements = _improvements(db, user_turns)

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
        "feedback_version": VERSION,
        "feedback_status": "available" if improvements else "not_evaluated",
        "ratings": _ratings(goal_status, len(good_fragments)),
        "next_suggestion": f"Practice {scene.get('goal_zh', 'this scene')} again on your own.",
        "end_reason": end_reason,
        "input_breakdown": breakdown,
    })
