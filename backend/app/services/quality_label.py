"""最小质检工作台：筛选脱敏会话 + 标记 + 版本统计。"""
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.core.errors import AppError
from app.models.entities import QualityLabel, Session, Turn

ISSUE_TYPES = [
    "mis_correction",
    "wrong_completion",
    "irrelevant_followup",
    "help_skip",
    "insufficient_evidence",
    "inappropriate",
]

CONCLUSIONS = ["confirmed", "disputed", "unclear"]


def _mask(text: str) -> str:
    """默认脱敏：仅保留首尾各 1 字符与长度，不返回完整正文。"""
    t = (text or "").strip()
    if not t:
        return ""
    if len(t) <= 2:
        return "*" * len(t)
    return f"{t[0]}{'*' * (len(t) - 2)}{t[-1]}[{len(t)}]"


def list_sessions(db: DbSession, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """筛选会话（默认脱敏：文本掩码，不含录音/密钥）。"""
    filters = filters or {}
    q = db.query(Session)
    if filters.get("scene_id"):
        q = q.filter(Session.scene_id == filters["scene_id"])
    if filters.get("status"):
        q = q.filter(Session.status == filters["status"])
    sessions = q.order_by(Session.started_at.desc()).limit(50).all()

    result: list[dict[str, Any]] = []
    for s in sessions:
        turns = db.query(Turn).filter(Turn.session_id == s.id).order_by(Turn.seq).all()
        result.append(
            {
                "session_id": s.id,
                "scene_id": s.scene_id,
                "status": s.status,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "turns": [
                    {
                        "turn_id": t.id,
                        "input_type": t.input_type,
                        "user_text": _mask(t.revised_text or t.user_text or t.transcript),
                        "reply_text": _mask(t.reply_text),
                        "eval_result": t.eval_result,
                        "help_type": t.help_type,
                    }
                    for t in turns
                ],
            }
        )
    return result


def add_label(
    db: DbSession,
    reviewer_id: str,
    session_id: str,
    turn_id: str,
    issue_type: str,
    conclusion: str,
) -> dict[str, Any]:
    """新增质检标签：校验问题类型、结论与来源会话/回合存在性。"""
    if issue_type not in ISSUE_TYPES:
        raise AppError("INVALID_INPUT", "未知的问题类型", status_code=422)
    if conclusion not in CONCLUSIONS:
        raise AppError("INVALID_INPUT", "未知的结论类型", status_code=422)
    session = db.get(Session, session_id)
    if session is None:
        raise AppError("NOT_FOUND", "会话不存在", status_code=404)
    if turn_id:
        turn = db.get(Turn, turn_id)
        if turn is None or turn.session_id != session_id:
            raise AppError("NOT_FOUND", "回合不存在或不属于该会话", status_code=404)

    label = QualityLabel(
        session_id=session_id,
        turn_id=turn_id,
        issue_type=issue_type,
        conclusion=conclusion,
        reviewer_id=reviewer_id,
        review_version="v1",
    )
    db.add(label)
    db.commit()
    db.refresh(label)
    return {
        "id": label.id,
        "session_id": label.session_id,
        "turn_id": label.turn_id,
        "issue_type": label.issue_type,
        "conclusion": label.conclusion,
    }


def stats(db: DbSession) -> dict[str, Any]:
    labels = db.query(QualityLabel).all()
    by_type: dict[str, int] = {}
    for l in labels:
        by_type[l.issue_type] = by_type.get(l.issue_type, 0) + 1
    return {"total": len(labels), "by_type": by_type, "review_version": "v1"}
