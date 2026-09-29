"""学习记忆：默认关闭，开启后生成候选弱项，随源练习删除传播；按来源/版本/到期管理。"""
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.core.errors import AppError
from app.models.entities import Memory, Session, User

MEMORY_TTL_DAYS = 30  # 单条记忆最长保留


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_memory_enabled(db: DbSession, user_id: str) -> bool:
    user = db.get(User, user_id)
    return bool(user and user.memory_enabled)


def set_memory_enabled(db: DbSession, user_id: str, enabled: bool) -> bool:
    user = db.get(User, user_id)
    if user is None:
        user = User(id=user_id, level="basic", memory_enabled=enabled)
        db.add(user)
    else:
        user.memory_enabled = bool(enabled)
    db.commit()
    return bool(user.memory_enabled)


def _dedup_key(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def generate_candidates(
    db: DbSession, user_id: str, session_id: str, review: dict[str, Any]
) -> int:
    """从回顾生成候选记忆（仅记忆开启时调用）；同文本跨会话重复出现才升为可用。"""
    scene_id = ""
    src = db.get(Session, session_id)
    if src is not None:
        scene_id = src.scene_id or ""
    created = 0
    for imp in review.get("improvements", []):
        if imp.get("disputed"):
            continue
        text = (imp.get("explanation_cn") or imp.get("suggestion") or "").strip()
        if not text:
            continue
        key = _dedup_key(text)
        existing = (
            db.query(Memory)
            .filter(Memory.user_id == user_id, Memory.source_session_id != session_id)
            .all()
        )
        match = next((m for m in existing if _dedup_key(m.text) == key), None)
        if match:
            match.evidence_count = (match.evidence_count or 0) + 1
            match.expires_at = _now() + timedelta(days=MEMORY_TTL_DAYS)
            if match.evidence_count >= 2:
                match.status = "usable"
            created += 1
        else:
            db.add(
                Memory(
                    user_id=user_id,
                    text=text,
                    source_session_id=session_id,
                    source_turn_id=imp.get("turn_id") or "",
                    scene_id=scene_id,
                    status="candidate",
                    evidence_count=1,
                    version="v1",
                    expires_at=_now() + timedelta(days=MEMORY_TTL_DAYS),
                )
            )
            created += 1
    if created:
        db.commit()
    return created


def list_memories(db: DbSession, user_id: str) -> list[Memory]:
    return (
        db.query(Memory).filter(Memory.user_id == user_id).order_by(Memory.created_at.desc()).all()
    )


def get_usable_memories(db: DbSession, user_id: str, scene_id: str = "") -> list[Memory]:
    """返回未过期、且与当前任务相关（同场景）的可用记忆。"""
    now = _now()
    q = db.query(Memory).filter(Memory.user_id == user_id, Memory.status == "usable")
    q = q.filter((Memory.expires_at.is_(None)) | (Memory.expires_at > now))
    if scene_id:
        q = q.filter(Memory.scene_id == scene_id)
    return q.order_by(Memory.created_at.desc()).all()


def update_memory_text(db: DbSession, user_id: str, memory_id: int, text: str) -> Memory:
    m = db.get(Memory, memory_id)
    if m is None or m.user_id != user_id:
        raise AppError("MEMORY_NOT_FOUND", "记忆不存在", status_code=404)
    m.text = text.strip()
    m.version = str(int((m.version or "v1").lstrip("v") or "1") + 1)
    db.commit()
    return m


def delete_memory(db: DbSession, user_id: str, memory_id: int) -> None:
    m = db.get(Memory, memory_id)
    if m is None or m.user_id != user_id:
        raise AppError("MEMORY_NOT_FOUND", "记忆不存在", status_code=404)
    db.delete(m)
    db.commit()


def clear_memories(db: DbSession, user_id: str) -> None:
    db.query(Memory).filter(Memory.user_id == user_id).delete()
    db.commit()


def delete_by_session(db: DbSession, session_id: str) -> None:
    """源会话删除时保守撤销本会话记忆（跨会话合并的 evidence_count 精确回滚待后续版本化）。"""
    db.query(Memory).filter(Memory.source_session_id == session_id).delete()
    db.commit()
