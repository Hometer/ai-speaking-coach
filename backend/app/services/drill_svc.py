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
from app.core.config import settings
from app.models.entities import DrillAttempt, EventLog, ReviewQueueItem, Session, Turn
from app.services.prompts.scenarios import SCENARIOS
from app.services.recall_judge import judge_recall, judge_transfer
from app.services.transfer_eval import verify_new_condition
from app.services.semantic_eval import verify_meaning
from app.services.budget import BudgetGuard
from app.services.generation_call import reset_usage_attempts
from app.services import provider_usage as provider_usage_svc
from app.services import events as product_events
from app.services import intent_result
from app.services.operations import durable, fence, require_active, bump_version
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


def _material(session: Session, db: DbSession | None):
    if db is None or not session.source_session_id:
        return None
    from app.services.practice_materials import existing_material
    return existing_material(db, session.source_session_id, session.source_turn_id, session.drill_target)


def _visible_help(session: Session, stage: str, db: DbSession | None = None) -> str:
    material = _material(session, db)
    if stage == "full_text":
        return session.drill_target
    if stage in ("fill_blanks", "structure"):
        if material is None:
            return "复练材料尚未准备好，请先查看原回顾。"
        if stage == "fill_blanks":
            return session.drill_target.replace(material.replaceable_text, "___", 1)
        return material.chunk_text + " · ___"
    if stage == "free_speak":
        return material.meaning_zh if material else session.drill_meaning
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


def _finish_clock(session: Session) -> None:
    if session.status == "ENDED":
        from app.services.session_svc import _active_elapsed_seconds
        if session.ended_at is None:
            session.ended_at = datetime.now(timezone.utc)
        session.active_seconds = _active_elapsed_seconds(session)
        session.active_since = None


def drill_view(session: Session, db: DbSession | None = None) -> dict[str, Any]:
    stage = session.drill_stage or "full_text"
    material = None
    available = not session.source_invalid
    if db is not None and session.source_session_id and not session.source_invalid:
        from app.services.practice_materials import existing_material
        material = existing_material(db, session.source_session_id, session.source_turn_id, session.drill_target)
        available = material is not None
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
    recording = None
    if db is not None:
        attempt = db.query(DrillAttempt).filter_by(drill_session_id=session.id).filter(DrillAttempt.input_type == "voice").order_by(DrillAttempt.id.desc()).first()
        if attempt and (stage not in ("free_speak", "transfer") or attempt.stage == stage):
            reveal = stage not in ("free_speak", "transfer") and not session.source_invalid
            recording = {"recording_url": attempt.recording_url, "stage": attempt.stage,
                "reference_version": attempt.reference_version,
                "reference_sentence": attempt.reference_sentence if reveal else "",
                "demo_audio_url": attempt.demo_audio_url if reveal else ""}
    return {
        'recording': recording,
        'material_available' : available,
        "drill_stage": stage,
        "stage_label": STAGE_LABEL.get(stage, ""),
        "drill_target": session.drill_target if stage not in ("free_speak", "transfer") else "",
        "drill_meaning": material.meaning_zh if material else session.drill_meaning if available else '',
        "drill_chunk": material.chunk_text if material and stage not in ("free_speak", "transfer") else "",
        "visible_help": material.meaning_zh if material and stage=='free_speak' else _visible_help(session, stage, db) if available else '复练材料尚未准备好，请先查看原回顾。',
        "completed": stage == "ended",
        "outcome": outcome,
    }


def _record(db: DbSession, session: Session, user_id: str, text: str, result: str, reason: str, input_type: str = "text", help_shown: bool = False, attempt_key: str = "", recording_url: str = "") -> None:
    import hashlib
    opening = db.query(Turn).filter_by(session_id=session.id, input_type="coach", seq=0).first()
    db.add(
        DrillAttempt(
            recording_url=recording_url,
            reference_sentence=session.drill_target if recording_url else "",
            reference_version="ref:" + hashlib.sha256(session.drill_target.encode()).hexdigest() if recording_url else "",
            demo_audio_url=opening.reply_audio_url if recording_url and opening else "",
            user_id=user_id,
            source_session_id=session.source_session_id,
            source_turn_id=session.source_turn_id,
            drill_session_id=session.id,
            stage=session.drill_stage,
            visible_help=_visible_help(session, session.drill_stage, db),
            answer_text=text.strip(),
            input_type=input_type,
            help_shown=help_shown,
            attempt_key=attempt_key,
            result=result,
            ended_reason=reason,
        )
    )


def _write_back_queue(db: DbSession, user_id: str, source_turn_id: str, result: str) -> None:
    """脱稿/换题成功写回原待复练项（沿用跨日规则：独立成功 +1，间隔 24h 才掌握）。"""
    if result != "transfer":
        return
    item = (
        db.query(ReviewQueueItem)
        .filter(ReviewQueueItem.user_id == user_id, ReviewQueueItem.source_turn_id == source_turn_id)
        .first()
    )
    if item is None:
        return
    from app.services.review_queue import STATUS_AWAITING_RETEST, STATUS_MASTERED, _resolve_due, _schedule_due

    if item.status not in ("pending", STATUS_AWAITING_RETEST):
        return

    # 首次与后续写回都须到期；脱稿练习本身可以继续，但不能提前增加跨日掌握次数。
    if item.due_at is None:
        return
    due = item.due_at
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) < due:
        return
    _resolve_due(db, item, "independent")
    item.last_practiced_at = datetime.now(timezone.utc)
    item.retest_result = "independent"
    item.success_count = (item.success_count or 0) + 1
    if item.success_count >= 2:
        item.status = STATUS_MASTERED
    else:
        item.status = STATUS_AWAITING_RETEST
        _schedule_due(db, item, datetime.now(timezone.utc) + timedelta(hours=24))
    db.commit()


@durable("drill_svc", key_arg="attempt_key")
def submit_drill_attempt(
    db: DbSession, session_id: str, user_id: str, text: str, input_type: str = "text", attempt_key: str = "", recording_url: str = ""
) -> dict[str, Any]:
    session = _require_session(db, session_id, user_id)
    if session.practice_type not in ("sentence_practice", "drill"):
        raise AppError("INVALID_STATE", "该会话不是脱稿练习", status_code=409)
    text = (text or "").strip()
    replayed = intent_result.replay(db, user_id, "drill_attempt", session_id, attempt_key, input_type, text)
    if replayed is not None:
        return replayed
    if session.source_invalid:
        raise AppError("SOURCE_INVALID", "来源内容已失效，请重新选择练习", status_code=409)

    # 幂等检查放最前（仅次来源校验）：同键返回首次结果；同键异内容 409
    if attempt_key:
        existing = (
            db.query(DrillAttempt)
            .filter(DrillAttempt.drill_session_id == session_id, DrillAttempt.attempt_key == attempt_key)
            .first()
        )
        if existing is not None:
            if existing.answer_text != text:
                raise AppError("IDEMPOTENCY_CONFLICT", "同一请求键内容不一致", status_code=409)
            view = drill_view(session, db)
            return {
                "success": existing.result in ("independent", "transfer", "reference", "hinted"),
                "result": existing.result,
                "stage": existing.stage,
                "next_stage": session.drill_stage,
                **view,
            }

    if session.status == "ENDED" or session.drill_stage == "ended":
        raise AppError("SESSION_ENDED", "脱稿练习已结束", status_code=409)

    require_active(session)
    if not drill_view(session, db)['material_available']:
        raise AppError('MATERIAL_NOT_AVAILABLE', '复练材料尚未准备好，请先查看原回顾', 409)
    operation_version = session.state_version
    stage = session.drill_stage or "full_text"
    transfer_guard = None
    if stage in ("transfer", "free_speak") and input_type == "voice" and settings.provider_mode == "real":
        transfer_guard = BudgetGuard(db)
        transfer_guard.reserve(settings.nominal_cost_per_turn, session_id, user_id)
        reset_usage_attempts()
        transfer_guard.mark_called()

    if stage == "free_speak":
        # 脱稿说：只看中文意图，语音 + 相关 + 非照抄才独立
        result = judge_recall(text, session.drill_target, input_type, help_shown=False, meaning_verified=verify_meaning(session.drill_target, text) if input_type == "voice" else None)
        success = result == "independent"
    elif stage == "transfer":
        # 词重合只能作为线索，不能代替新条件核对；Mock/不确定均保持 pending。
        verified = verify_new_condition(_transfer_question(session.scene_id), session.drill_target, text) if input_type == "voice" else None
        result = judge_transfer(text, session.drill_target, input_type, help_shown=False, new_condition_verified=verified)
        success = result == "transfer"
    else:
        # 有参考阶段（看全文/补词/看结构）：照着说，词重叠判定完成
        success = _judge(text, session.drill_target)
        result = STAGE_RESULT[stage] if success else "failed"

    if transfer_guard is not None:
        transfer_guard.settle(settings.nominal_cost_per_turn, session_id, user_id)
        provider_usage_svc.record_llm_attempts(db, user_id, session_id)
        transfer_guard = None
    fence(db, session, operation_version)
    reason = "completed" if success else "error"
    _record(db, session, user_id, text, result, reason, input_type=input_type, help_shown=False, attempt_key=attempt_key, recording_url=recording_url)
    db.add(EventLog(session_id=session.id, turn_id="", action_type="drill", basis=result))

    if success:
        if stage == "transfer":
            session.drill_stage = "ended"
            session.status = "ENDED"
            session.end_reason = "goal_reached"
            _finish_clock(session)
            _write_back_queue(db, user_id, session.source_turn_id, "transfer")
        else:
            session.drill_stage = STAGE_ORDER[STAGE_ORDER.index(stage) + 1]
            if stage == "free_speak":
                _write_back_queue(db, user_id, session.source_turn_id, "independent")
    try:
        db.commit()
    except Exception:
        db.rollback()
        if transfer_guard is not None:
            transfer_guard.release(settings.nominal_cost_per_turn, session_id, user_id)
        raise
    if transfer_guard is not None:
        transfer_guard.settle(settings.nominal_cost_per_turn, session_id, user_id)
        try:
            provider_usage_svc.record_llm_attempts(db, user_id, session_id)
        except Exception:
            db.rollback()

    if session.status == "ENDED":
        product_events.record(db, session.id, "session_end", session.id, basis=session.end_reason or "")
    _finish_clock(session)
    db.commit()
    view = drill_view(session, db)
    response = {
        "success": success,
        "result": result,
        "stage": stage,
        "next_stage": session.drill_stage,
        **view,
    }
    intent_result.remember(db, user_id, "drill_attempt", session_id, attempt_key, input_type, text, response)
    return response


def drill_action(db: DbSession, session_id: str, user_id: str, action: str) -> dict[str, Any]:
    session = _require_session(db, session_id, user_id)
    if session.source_invalid:
        raise AppError("SOURCE_INVALID", "来源内容已失效，请重新选择练习", status_code=409)
    if session.drill_stage == "ended":
        raise AppError("SESSION_ENDED", "脱稿练习已结束", status_code=409)
    if action != "end":
        require_active(session)
    stage = session.drill_stage or "full_text"
    if action == "repeat":
        last = db.query(DrillAttempt).filter_by(drill_session_id=session.id).order_by(DrillAttempt.id.desc()).first()
        if last is None or last.stage not in STAGE_ORDER:
            raise AppError("INVALID_STATE", "没有可重录的阶段", 409)
        session.drill_stage = last.stage
    elif action == "skip":
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
    bump_version(db, session.id)
    _finish_clock(session)
    db.commit()
    if session.status == "ENDED":
        product_events.record(db, session.id, "session_end", session.id, basis=session.end_reason or "")
    return drill_view(session, db)
