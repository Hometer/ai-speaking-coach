"""学习记忆：默认关闭，开启后生成候选弱项，随源练习删除传播；按来源/版本/到期管理。"""
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.core.errors import AppError
from app.models.entities import Memory, MemorySource, Session, Turn, User

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


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def _recompute(db: DbSession, m: Memory) -> list[MemorySource]:
    sources = db.query(MemorySource).filter_by(memory_id=m.id).all()
    # 旧数据只恢复确实存下的那一个来源，绝不还原虚假的“第二个来源”。
    if not sources and m.source_session_id and m.status != "invalid":
        src = db.get(Session, m.source_session_id)
        if src is not None:
            source = MemorySource(memory_id=m.id, session_id=src.id, turn_id=m.source_turn_id or "",
                expires_at=min(_aware(src.started_at) + timedelta(days=30), _aware(m.expires_at) if m.expires_at else _now()))
            db.add(source)
            db.flush()
            sources = [source]
    valid = []
    for source in sources:
        session = db.get(Session, source.session_id)
        turn = db.get(Turn, source.turn_id) if source.turn_id else None
        good = session is not None and session.user_id == m.user_id and _aware(source.expires_at) > _now()
        if source.turn_id:
            good = good and turn is not None and turn.session_id == source.session_id and turn.eval_result != "revised" and turn.revision_version == source.revision_version
        if good and session.review:
            import json
            review = json.loads(session.review)
            good = not review.get("stale") and not any(i.get("disputed") and i.get("turn_id") == source.turn_id for i in review.get("improvements", []))
        if good:
            valid.append(source)
        else:
            db.delete(source)
    m.evidence_count = len({source.session_id for source in valid})
    m.status = "usable" if m.evidence_count >= 2 else "candidate" if valid else "invalid"
    if valid:
        m.source_session_id, m.source_turn_id = valid[0].session_id, valid[0].turn_id
        m.expires_at = min(_aware(m.created_at) + timedelta(days=30), max(_aware(v.expires_at) for v in valid))
    return valid


def generate_candidates(db: DbSession, user_id: str, session_id: str, review: dict[str, Any]) -> int:
    source = db.get(Session, session_id)
    if source is None or source.user_id != user_id or review.get("stale"):
        return 0
    count = 0
    for imp in review.get("improvements", []):
        text = (imp.get("explanation_cn") or imp.get("suggestion") or "").strip()
        if not text or imp.get("disputed"):
            continue
        turn_id = imp.get("turn_id") or ""
        turn = db.get(Turn, turn_id) if turn_id else None
        if turn_id and (turn is None or turn.session_id != session_id or turn.eval_result == "revised"):
            continue
        existing = db.query(Memory).filter_by(user_id=user_id, scene_id=source.scene_id).all()
        m = next((m for m in existing if _dedup_key(m.text) == _dedup_key(text) and m.status != "invalid" and (m.expires_at is None or _aware(m.expires_at) > _now())), None)
        if m is None:
            m = Memory(user_id=user_id, text=text, scene_id=source.scene_id, source_session_id=session_id,
                source_turn_id=turn_id, expires_at=_now() + timedelta(days=30))
            db.add(m)
            db.flush()
        if not db.query(MemorySource).filter_by(memory_id=m.id, session_id=session_id, turn_id=turn_id).first():
            db.add(MemorySource(memory_id=m.id, session_id=session_id, turn_id=turn_id,
                revision_version=turn.revision_version if turn else 0,
                expires_at=min(_aware(source.started_at) + timedelta(days=30), _now() + timedelta(days=30))))
            db.flush()
        _recompute(db, m)
        count += 1
    db.commit()
    return count


def list_memories(db: DbSession, user_id: str) -> list[Memory]:
    memories = db.query(Memory).filter_by(user_id=user_id).order_by(Memory.created_at.desc()).all()
    for m in memories:
        _recompute(db, m)
    db.commit()
    return memories


def get_usable_memories(db: DbSession, user_id: str, scene_id: str = "") -> list[Memory]:
    return [m for m in list_memories(db, user_id) if m.status == "usable" and _aware(m.expires_at) > _now() and (not scene_id or m.scene_id == scene_id)][:5]


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
    db.query(MemorySource).filter_by(memory_id=m.id).delete()
    db.delete(m)
    db.commit()


def clear_memories(db: DbSession, user_id: str) -> None:
    ids = [m.id for m in db.query(Memory).filter_by(user_id=user_id).all()]
    db.query(MemorySource).filter(MemorySource.memory_id.in_(ids)).delete()
    db.query(Memory).filter(Memory.user_id == user_id).delete()
    db.commit()


def invalidate_by_turn(db: DbSession, turn_id: str) -> None:
    """只撤回被修订/争议的来源，保留合并记忆中其余独立证据。"""
    affected = {s.memory_id for s in db.query(MemorySource).filter_by(turn_id=turn_id).all()}
    affected.update(m.id for m in db.query(Memory).filter_by(source_turn_id=turn_id).all())
    db.query(MemorySource).filter_by(turn_id=turn_id).delete()
    for mid in affected:
        m = db.get(Memory, mid)
        if m.source_turn_id == turn_id:
            m.source_session_id = ""
            m.source_turn_id = ""
        _recompute(db, m)
        if m.status == "invalid":
            db.delete(m)


def delete_by_session(db: DbSession, session_id: str) -> None:
    affected = {s.memory_id for s in db.query(MemorySource).filter_by(session_id=session_id).all()}
    affected.update(m.id for m in db.query(Memory).filter_by(source_session_id=session_id).all())
    db.query(MemorySource).filter_by(session_id=session_id).delete()
    for mid in affected:
        m = db.get(Memory, mid)
        if m is None:
            continue
        if m.source_session_id == session_id:
            m.source_session_id = ""
            m.source_turn_id = ""
        _recompute(db, m)
        if m.status == "invalid":
            db.delete(m)
    db.commit()
