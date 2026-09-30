"""质检工作台：多维筛选脱敏会话 + 标记 + 版本统计。"""
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.core.errors import AppError
from app.models.entities import EventLog, QualityLabel, QualityLabelChange, Session, Turn

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
    if filters.get("version"):
        q = q.filter(Session.scene_version == filters["version"])
    if filters.get("issue_type"):
        q = q.join(QualityLabel, QualityLabel.session_id == Session.id).filter(
            QualityLabel.issue_type == filters["issue_type"]
        )
    sessions = q.order_by(Session.started_at.desc()).limit(50).all()

    result: list[dict[str, Any]] = []
    for s in sessions:
        turns = db.query(Turn).filter(Turn.session_id == s.id).order_by(Turn.seq).all()
        has_turn_filter = bool(filters.get("input_type") or filters.get("help_type") or filters.get("eval_result"))
        if has_turn_filter:
            turns = [
                t for t in turns
                if (not filters.get("input_type") or t.input_type == filters["input_type"])
                and (not filters.get("help_type") or t.help_type == filters["help_type"])
                and (not filters.get("eval_result") or t.eval_result == filters["eval_result"])
            ]
            if not turns:
                continue  # 剔除零匹配回合的会话
        errors = [
            e.error for e in db.query(EventLog).filter(EventLog.session_id == s.id, EventLog.error != "").all()
        ]
        result.append(
            {
                "session_id": s.id,
                "scene_id": s.scene_id,
                "scene_version": s.scene_version,
                "status": s.status,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "error_codes": sorted(set(errors))[:5],
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
    """新增/更新质检标签：校验问题类型、结论与来源会话/回合存在性；重复更新递增版本。"""
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

    existing = (
        db.query(QualityLabel)
        .filter(
            QualityLabel.session_id == session_id,
            QualityLabel.turn_id == turn_id,
            QualityLabel.issue_type == issue_type,
        )
        .first()
    )
    if existing is not None:
        # 覆盖式更新：写审计记录 + 递增 review_version，可追溯每次改动
        old_version = existing.review_version or "v1"
        try:
            new_version = f"v{int(old_version.lstrip('v') or '1') + 1}"
        except ValueError:
            new_version = "v2"
        db.add(
            QualityLabelChange(
                label_id=existing.id,
                reviewer_id=reviewer_id,
                old_conclusion=existing.conclusion,
                new_conclusion=conclusion,
                old_review_version=old_version,
                new_review_version=new_version,
            )
        )
        existing.conclusion = conclusion
        existing.reviewer_id = reviewer_id
        existing.review_version = new_version
        label = existing
    else:
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
        "review_version": label.review_version,
    }


def label_history(db: DbSession, label_id: int) -> list[dict[str, Any]]:
    """返回某个标签的变更审计历史。"""
    label = db.get(QualityLabel, label_id)
    if label is None:
        raise AppError("NOT_FOUND", "标签不存在", status_code=404)
    changes = (
        db.query(QualityLabelChange)
        .filter(QualityLabelChange.label_id == label_id)
        .order_by(QualityLabelChange.created_at.asc())
        .all()
    )
    return [
        {
            "id": c.id,
            "reviewer_id": c.reviewer_id,
            "old_conclusion": c.old_conclusion,
            "new_conclusion": c.new_conclusion,
            "old_review_version": c.old_review_version,
            "new_review_version": c.new_review_version,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in changes
    ]


def stats(db: DbSession) -> dict[str, Any]:
    labels = db.query(QualityLabel).all()
    by_type: dict[str, int] = {}
    by_version: dict[str, int] = {}
    for l in labels:
        by_type[l.issue_type] = by_type.get(l.issue_type, 0) + 1
        by_version[l.review_version or "v1"] = by_version.get(l.review_version or "v1", 0) + 1
    return {"total": len(labels), "by_type": by_type, "by_version": by_version}
