"""语块卡（REQ-31，P1-4）。

从有效建议生成轻量语块卡（确定性，不扩写用户经历/职位/成绩/数字）：来源原句、建议表达、
主要语块、中文解释、适用情境、可替换部分、新例句。来源失效/删除时传播失效。
"""
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.core.errors import AppError
from app.models.entities import EventLog, PhraseCard, Session
from app.services.prompts.scenarios import SCENARIOS


def _keywords(scene_id: str) -> list[str]:
    materials = SCENARIOS.get(scene_id, {}).get("help_materials") or {}
    raw = materials.get("keywords", "") or ""
    return [w.strip() for w in raw.replace("/", " ").split() if w.strip()]


def _pick_chunk(suggestion: str, kws: list[str]) -> str:
    low = suggestion.lower()
    for kw in kws:
        if kw.lower() in low:
            return kw
    words = suggestion.split()
    return " ".join(words[:2]) if words else suggestion


def _to_dict(c: PhraseCard) -> dict[str, Any]:
    return {
        "id": c.id,
        "source_session_id": c.source_session_id,
        "source_turn_id": c.source_turn_id,
        "original_text": c.original_text,
        "suggestion_text": c.suggestion_text,
        "chunk_text": c.chunk_text,
        "explanation_zh": c.explanation_zh,
        "context_zh": c.context_zh,
        "replaceable": c.replaceable,
        "example_text": c.example_text,
        "status": c.status,
    }


def _build(db: DbSession, user_id: str, source_session_id: str, source_turn_id: str) -> PhraseCard:
    import json

    source = db.get(Session, source_session_id)
    if source is None or source.user_id != user_id:
        raise AppError("FORBIDDEN", "无权使用该来源", status_code=403)
    review = json.loads(source.review) if source.review else {}
    imp = next((i for i in review.get("improvements", []) if i.get("turn_id") == source_turn_id), None)
    if imp is None or imp.get("disputed") or review.get("stale"):
        raise AppError("NOT_FOUND", "改进建议不存在或已失效", status_code=404)
    scene = SCENARIOS.get(source.scene_id, {})
    kws = _keywords(source.scene_id)
    suggestion = imp.get("practice_sentence") or ""
    chunk = _pick_chunk(suggestion, kws)
    variations = scene.get("variations") or []
    refs = scene.get("reference_expressions") or []
    other_ref = next((r for r in refs if r != suggestion), (refs[0] if refs else ""))
    # 可替换部分：展示主要语块本身与可替换的同场景词，而不是把整句词替换成错误句。
    replace_kws = [k for k in kws if k.lower() != chunk.lower()][:2]
    replaceable = f"{chunk}（可换成 {' / '.join(replace_kws)}）" if replace_kws else chunk
    return PhraseCard(
        user_id=user_id,
        source_session_id=source_session_id,
        source_turn_id=source_turn_id,
        original_text=imp.get("original", ""),
        suggestion_text=suggestion,
        chunk_text=chunk,
        explanation_zh=imp.get("explanation_cn", "") or scene.get("goal_zh", ""),
        context_zh=variations[0] if variations else scene.get("label", ""),
        replaceable=replaceable,
        example_text=other_ref,
    )


def generate_card(db: DbSession, user_id: str, source_session_id: str, source_turn_id: str) -> dict[str, Any]:
    card = _build(db, user_id, source_session_id, source_turn_id)
    existing = (
        db.query(PhraseCard)
        .filter(PhraseCard.user_id == user_id, PhraseCard.source_turn_id == source_turn_id)
        .first()
    )
    if existing is not None:
        return _to_dict(existing)
    db.add(card)
    db.add(EventLog(session_id=source_session_id, turn_id=source_turn_id, action_type="phrase_card", basis="generated"))
    db.commit()
    db.refresh(card)
    return _to_dict(card)


def list_cards(db: DbSession, user_id: str) -> list[dict[str, Any]]:
    cards = (
        db.query(PhraseCard)
        .filter(PhraseCard.user_id == user_id, PhraseCard.status == "active")
        .order_by(PhraseCard.created_at.desc())
        .all()
    )
    return [_to_dict(c) for c in cards]


def invalidate_by_source(db: DbSession, source_session_id: str) -> None:
    for c in db.query(PhraseCard).filter(PhraseCard.source_session_id == source_session_id).all():
        c.status = "invalid"


def invalidate_by_turn(db: DbSession, source_turn_id: str) -> None:
    for c in db.query(PhraseCard).filter(PhraseCard.source_turn_id == source_turn_id).all():
        c.status = "invalid"
