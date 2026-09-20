"""学习记忆：默认关闭，开启后生成候选弱项，随源练习删除传播。"""
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.core.errors import AppError
from app.models.entities import Memory, User


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


def generate_candidates(
    db: DbSession, user_id: str, session_id: str, review: dict[str, Any]
) -> int:
    """从回顾的改进建议生成候选记忆（仅记忆开启时调用）。"""
    created = 0
    for imp in review.get("improvements", []):
        if imp.get("disputed"):
            continue
        text = (imp.get("explanation_cn") or imp.get("suggestion") or "").strip()
        if not text:
            continue
        db.add(Memory(user_id=user_id, text=text, source_session_id=session_id))
        created += 1
    if created:
        db.commit()
    return created


def list_memories(db: DbSession, user_id: str) -> list[Memory]:
    return (
        db.query(Memory).filter(Memory.user_id == user_id).order_by(Memory.created_at.desc()).all()
    )


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
    db.query(Memory).filter(Memory.source_session_id == session_id).delete()
