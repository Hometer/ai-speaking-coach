"""脱稿阶梯与帮助证据（REQ-28，P1-1）。

五段阶梯：full_text → fill_blanks → structure → free_speak → transfer。
帮助证据决定完成结论：看全文/补关键词=reference，看结构=hinted，脱稿说=independent，换题=transfer。
判定以语义/可理解性为主（确定性宽松匹配，逐字复现只作辅助）；结果写回原待复练项（沿用 AC07-08）。
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.core.errors import AppError
from app.models.entities import DrillAttempt, EventLog, ReviewQueueItem, Session
from app.services.prompts.scenarios import SCENARIOS
from app.services.session_svc import _require_session

STAGE_ORDER = ["full_text", "fill_blanks", "structure", "free_speak", "transfer"]

STAGE_RESULT = {
    "full_text": "reference",
    "fill_blanks": "reference",
    "structure": "hinted",
    "free_speak": "independent",
    "transfer": "transfer",
}

STAGE_LABEL = {
    "full_text": "看全文",
    "fill_blanks": "补关键词",
    "structure": "看结构",
    "free_speak": "脱稿说",
    "transfer": "换题用",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9\s']", " ", (text or "").lower())


def _keywords(scene_id: str) -> list[str]:
    """从场景关键词材料取主要语块词，用于补关键词/看结构。"""
    materials = SCENARIOS.get(scene_id, {}).get("help_materials") or {}
    raw = materials.get("keywords", "") or ""
    return [w.strip() for w in raw.replace("/", " ").split() if w.strip()]


def _blank_keywords(target: str, kws: list[str]) -> str:
    out = target
    for kw in kws:
        out = re.sub(re.escape(kw), "___", out, flags=re.IGNORECASE)
    return out if "___" in out else f"{target.split()[0] if target.split() else ''} ___ …"


def _structure_hint(target: str, kws: list[str]) -> str:
    first = target.split()[0] if target.split() else ""
    return " · ".join([first] + kws[:3]) + " …"


def _transfer_question(scene_id: str) -> str:
    """已审核替代题：场景 variations 派生；无则给通用换题。"""
    variations = SCENARIOS.get(scene_id, {}).get("variations") or []
    return variations[0] if variations else "Try to say the same idea in a new situation."


def _visible_help(session: Session, stage: str) -> str:
    if stage == "full_text":
        return session.drill_target
    if stage == "fill_blanks":
        return _blank_keywords(session.drill_target, _keywords(session.scene_id))
    if stage == "structure":
        return _structure_hint(session.drill_target, _keywords(session.scene_id))
    if stage == "free_speak":
        return session.drill_meaning
    if stage == "transfer":
        return _transfer_question(session.scene_id)
    return ""


def _judge(text: str, target: str) -> bool:
    """宽松语义判定（确定性 v1）：非空 + 与目标句词重叠达到阈值。逐字复现只作辅助。"""
    a = _norm(text)
    if len(a.split()) < 2:
        return False
    t = _norm(target)
    t_words = set(t.split())
    if not t_words:
        return True
    a_words = set(a.split())
    overlap = len(a_words & t_words) / len(t_words)
    return overlap >= 0.4


def drill_view(session: Session, db: DbSession | None = None) -> dict[str, Any]:
    stage = session.drill_stage or "full_text"
    # 结束后的准确结论：换题成功/脱稿成功/仅结束。用于前端不编造"已写回"。
    outcome = ""
    if stage == "ended":
        outcome = "transfer" if session.end_reason == "goal_reached" else "ended"
        if db is not None and outcome != "transfer":
            results = [
                r[0]
                for r in db.query(DrillAttempt.result)
                .filter(DrillAttempt.drill_session_id == session.id)
                .all()
            ]
            if "independent" in results:
                outcome = "independent"
    return {
        "drill_stage": stage,
        "stage_label": STAGE_LABEL.get(stage, ""),
        "drill_target": session.drill_target,
        "drill_meaning": session.drill_meaning,
        "drill_chunk": " · ".join(_keywords(session.scene_id)[:3]),
        "visible_help": _visible_help(session, stage),
        "completed": stage == "ended",
        "outcome": outcome,
    }


def _record(db: DbSession, session: Session, user_id: str, text: str, result: str, reason: str) -> None:
    db.add(
        DrillAttempt(
            user_id=user_id,
            source_session_id=session.source_session_id,
            source_turn_id=session.source_turn_id,
            drill_session_id=session.id,
            stage=session.drill_stage,
            visible_help=_visible_help(session, session.drill_stage),
            answer_text=text.strip(),
            result=result,
            ended_reason=reason,
        )
    )


def _write_back_queue(db: DbSession, user_id: str, source_turn_id: str, result: str) -> None:
    """脱稿/换题成功写回原待复练项（沿用跨日规则：独立成功 +1，间隔 24h 才掌握）。"""
    if result not in ("independent", "transfer"):
        return
    item = (
        db.query(ReviewQueueItem)
        .filter(ReviewQueueItem.user_id == user_id, ReviewQueueItem.source_turn_id == source_turn_id)
        .first()
    )
    if item is None:
        return
    from app.services.review_queue import STATUS_AWAITING_RETEST, STATUS_MASTERED

    if item.status not in ("pending", STATUS_AWAITING_RETEST):
        return

    # 到期约束（AC07-08）：待复测且未到期不推进
    if item.status == STATUS_AWAITING_RETEST and item.due_at is not None:
        due = item.due_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < due:
            return
    item.last_practiced_at = datetime.now(timezone.utc)
    item.retest_result = "independent"
    item.success_count = (item.success_count or 0) + 1
    if item.success_count >= 2:
        item.status = STATUS_MASTERED
    else:
        item.status = STATUS_AWAITING_RETEST
        item.due_at = datetime.now(timezone.utc) + timedelta(hours=24)
    db.commit()


def submit_drill_attempt(
    db: DbSession, session_id: str, user_id: str, text: str
) -> dict[str, Any]:
    session = _require_session(db, session_id, user_id)
    if session.practice_type not in ("sentence_practice", "drill"):
        raise AppError("INVALID_STATE", "该会话不是脱稿练习", status_code=409)
    if session.source_invalid:
        raise AppError("SOURCE_INVALID", "来源内容已失效，请重新选择练习", status_code=409)
    if session.status == "ENDED" or session.drill_stage == "ended":
        raise AppError("SESSION_ENDED", "脱稿练习已结束", status_code=409)

    stage = session.drill_stage or "full_text"
    text = (text or "").strip()
    success = _judge(text, session.drill_target)

    result = STAGE_RESULT[stage] if success else "failed"
    reason = "completed" if success else "error"
    _record(db, session, user_id, text, result, reason)
    db.add(EventLog(session_id=session.id, turn_id="", action_type="drill", basis=result))

    if success:
        if stage == "transfer":
            session.drill_stage = "ended"
            session.status = "ENDED"
            session.end_reason = "goal_reached"
            _write_back_queue(db, user_id, session.source_turn_id, "transfer")
        else:
            session.drill_stage = STAGE_ORDER[STAGE_ORDER.index(stage) + 1]
            if stage == "free_speak":
                _write_back_queue(db, user_id, session.source_turn_id, "independent")
    db.commit()

    view = drill_view(session, db)
    return {
        "success": success,
        "result": result,
        "stage": stage,
        "next_stage": session.drill_stage,
        **view,
    }


def drill_action(db: DbSession, session_id: str, user_id: str, action: str) -> dict[str, Any]:
    session = _require_session(db, session_id, user_id)
    if session.source_invalid:
        raise AppError("SOURCE_INVALID", "来源内容已失效，请重新选择练习", status_code=409)
    if session.drill_stage == "ended":
        raise AppError("SESSION_ENDED", "脱稿练习已结束", status_code=409)
    stage = session.drill_stage or "full_text"
    if action == "skip":
        _record(db, session, user_id, "", "skipped", "skipped")
        session.drill_stage = STAGE_ORDER[STAGE_ORDER.index(stage) + 1] if stage != "transfer" else "ended"
        if session.drill_stage == "ended":
            session.status = "ENDED"
            session.end_reason = "user_ended"
    elif action == "downgrade":
        idx = STAGE_ORDER.index(stage)
        session.drill_stage = STAGE_ORDER[max(0, idx - 1)]
    elif action == "jump":
        # 跳级：用户已能脱稿时可直接进入换题（AC08-02）
        if stage == "transfer":
            raise AppError("INVALID_STATE", "已在换题阶段", status_code=409)
        _record(db, session, user_id, "", "skipped", "jumped")
        session.drill_stage = "transfer"
    elif action == "end":
        session.drill_stage = "ended"
        session.status = "ENDED"
        session.end_reason = "user_ended"
    else:
        raise AppError("INVALID_INPUT", "未知的脱稿操作", status_code=422)
    db.commit()
    return drill_view(session, db)
