"""会话生命周期与状态机（Harness 维护状态，模型不可改写）。"""
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func, or_
from sqlalchemy.orm import Session as DbSession

from app.core.config import settings
from app.core.errors import AppError
from app.models.entities import AsrRiskRecord, ConversationQualityMark, Evaluation, EventLog, Session, Turn, User
from app.schemas.coach import CoachDecision
from app.services import memory as memory_svc
from app.services import qa_recording as qa_recording_svc
from app.services import provider_usage as provider_usage_svc
from app.services import intent_result
from app.services.budget import BudgetGuard, ToolBudget
from app.services.cleanup import cleanup_old_uploads
from app.services.coach import get_coach_provider
from app.services.evaluator import get_evaluator_provider
from app.services.generation_call import get_last_usage, reset_usage_attempts
from app.services.practice import get_practice_eval_provider
from app.services.asr import get_asr_provider
from app.services.asr_risk import assess_risk
from app.services.review import build_review, dedupe_improvements
from app.services.tts import get_tts_provider
from app.services.turn import _validate_audio
from app.services.prompts.scenarios import SCENARIOS, get_approved_scenes, get_scene

S_PREPARING = "PREPARING"
S_WAITING = "WAITING_INPUT"
S_PROCESSING = "PROCESSING"
S_PRACTICE = "PRACTICE"
S_RECHECK = "RECHECK"
S_PAUSED = "PAUSED"
S_ENDED = "ENDED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _turn_request_hash(input_type: str, user_text: str | None, audio_bytes: bytes | None, pre_transcribed: str | None) -> str:
    payload = (audio_bytes if audio_bytes is not None else (pre_transcribed if pre_transcribed is not None else user_text or "").encode("utf-8"))
    return hashlib.sha256(input_type.encode("ascii") + b"\0" + payload).hexdigest()


def _active_elapsed_seconds(session: Session) -> float:
    """累计非暂停时长（秒）：暂停区间不计入（F-07 精确非暂停计时）。"""
    extra = 0.0
    if session.active_since is not None:
        since = session.active_since
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        extra = (datetime.now(timezone.utc) - since).total_seconds()
    return float(session.active_seconds or 0.0) + max(0.0, extra)


def _save_audio(audio_bytes: bytes, ext: str) -> str:
    name = f"{uuid.uuid4().hex}.{ext}"
    audio_dir = settings.data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / name).write_bytes(audio_bytes)
    return f"/api/v1/audio/{name}"


def transcribe_voice_bytes(audio_bytes: bytes) -> str:
    """把上传音频转写为文本（校验格式 + 落临时文件 + ASR），供语音端点复用。"""
    from app.services.turn import _validate_audio

    fmt = _validate_audio(audio_bytes)
    uploads_dir = settings.data_dir / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    audio_path = uploads_dir / f"{uuid.uuid4().hex}.{fmt}"
    audio_path.write_bytes(audio_bytes)
    try:
        return get_asr_provider().transcribe(audio_path)
    finally:
        audio_path.unlink(missing_ok=True)


def submit_voice_intent(
    db: DbSession, user_id: str, session_id: str, scope: str, object_id: str,
    request_key: str, audio_bytes: bytes, submit: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """语音入口先重放，再 ASR；转写结果落库供业务提交失败后的同键重试复用。"""
    raw_hash = hashlib.sha256(audio_bytes).hexdigest()
    result = intent_result.replay(db, user_id, f"{scope}_result", object_id, request_key, "audio", raw_hash)
    if result is not None:
        return result
    cached = intent_result.replay(db, user_id, f"{scope}_asr", object_id, request_key, "audio", raw_hash)
    if cached is None:
        _validate_audio(audio_bytes)
        guard = BudgetGuard(db)
        guard.reserve(settings.nominal_cost_per_turn, session_id, user_id)
        try:
            transcript = transcribe_voice_bytes(audio_bytes)
        except Exception:
            # 供应商失败也可能计费，保守消耗预留；无效格式在预留前已拦截。
            guard.settle(settings.nominal_cost_per_turn, session_id, user_id)
            try:
                provider_usage_svc.record_asr(db, user_id, session_id, "", len(audio_bytes), False)
            except Exception:
                db.rollback()
            raise
        guard.settle(settings.nominal_cost_per_turn, session_id, user_id)
        intent_result.remember(db, user_id, f"{scope}_asr", object_id, request_key, "audio", raw_hash, {"transcript": transcript})
        try:
            provider_usage_svc.record_asr(db, user_id, session_id, "", len(audio_bytes))
        except Exception:
            db.rollback()
    else:
        transcript = cached["transcript"]
    response = submit(transcript)
    intent_result.remember(db, user_id, f"{scope}_result", object_id, request_key, "audio", raw_hash, response)
    return response


def _synth_and_save(tts, text: str) -> tuple[str, str, int]:
    """合成音频并落盘，返回 (audio_url, audio_status, attempts)。"""
    result = tts.synthesize_result(text)
    if result.audio is None:
        return "", "failed", result.attempts
    return _save_audio(result.audio, tts.ext), "ok", result.attempts


def _record_generated_audio_usage(db: DbSession, user_id: str, session_id: str, turn_id: str, text: str, attempts: int, succeeded: bool) -> None:
    """业务结果已提交后记录原始用量；记录故障不让客户端重放已成功的动作。"""
    try:
        provider_usage_svc.record_llm_attempts(db, user_id, session_id, turn_id)
        provider_usage_svc.record_tts(db, user_id, session_id, turn_id, text, attempts, succeeded)
    except Exception:
        db.rollback()


def _record_failed_llm_usage(db: DbSession, user_id: str, session_id: str) -> None:
    try:
        provider_usage_svc.record_llm_attempts(db, user_id, session_id)
    except Exception:
        db.rollback()


def _get_or_create_user(db: DbSession, user_id: str, level: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        user = User(id=user_id, level=level)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _require_session(db: DbSession, session_id: str, user_id: str) -> Session:
    session = db.get(Session, session_id)
    if session is None:
        raise AppError("SESSION_NOT_FOUND", "会话不存在", status_code=404)
    if session.user_id != user_id:
        raise AppError("FORBIDDEN", "无权访问该会话", status_code=403)
    return session


def _next_seq(db: DbSession, session_id: str) -> int:
    max_seq = db.query(func.max(Turn.seq)).filter(Turn.session_id == session_id).scalar()
    return (max_seq or 0) + 1


def _last_action_type(db: DbSession, session_id: str) -> str:
    last = (
        db.query(Turn)
        .filter(Turn.session_id == session_id)
        .order_by(Turn.seq.desc())
        .first()
    )
    return last.action_type if last else ""


def _has_practice(db: DbSession, session_id: str) -> bool:
    return (
        db.query(Turn)
        .filter(Turn.session_id == session_id, Turn.input_type == "practice")
        .first()
        is not None
    )


def _has_answer_help_for_question(db: DbSession, session_id: str, question_id: str) -> bool:
    """当前问题是否已给出过答案帮助（organize/translate）；有则后续回答不判独立。"""
    if not question_id:
        return False
    return (
        db.query(Turn)
        .filter(
            Turn.session_id == session_id,
            Turn.question_id == question_id,
            Turn.is_answer_help.is_(True),
        )
        .first()
        is not None
    )


def _log_event(
    db: DbSession,
    session_id: str,
    turn_id: str = "",
    action_type: str = "",
    basis: str = "",
    input_type: str = "",
    tool_result: str = "",
    error: str = "",
    request_key: str | None = None,
    duration_ms: int = 0,
    usage: str = "",
) -> None:
    # 请求去重：同一 request_key 的事件只记一次
    if request_key:
        dup = (
            db.query(EventLog)
            .filter(EventLog.session_id == session_id, EventLog.request_key == request_key)
            .first()
        )
        if dup is not None:
            return
    db.add(
        EventLog(
            session_id=session_id,
            turn_id=turn_id,
            action_type=action_type,
            basis=basis,
            input_type=input_type,
            tool_result=tool_result,
            error=error,
            request_key=request_key,
            duration_ms=duration_ms,
            usage=usage,
        )
    )


_DIRECT_QUESTION_PATTERNS = [
    "what about you", "what do you do", "and you", "how about you",
    "what's your", "what is your", "do you", "are you",
]
_ASK_EXPLAIN_PATTERNS = [
    "what do you mean", "explain", "what does", "can you explain", "什么意思",
]


def _mark_conversation_quality(
    db: DbSession, session: Session, turn_id: str, text: str, action: str, subgoal: str
) -> None:
    """对话节奏质检标记（REQ-34）：确定性信号提示，问号不一律解释为困惑。"""
    lowered = " ".join((text or "").lower().split())
    direct = lowered.endswith("?") or any(p in lowered for p in _DIRECT_QUESTION_PATTERNS)
    ask_explain = any(p in lowered for p in _ASK_EXPLAIN_PATTERNS)
    gave_info = bool(lowered) and not direct
    db.add(
        ConversationQualityMark(
            user_id=session.user_id,
            session_id=session.id,
            turn_id=turn_id,
            user_direct_question=direct,
            user_ask_explanation=ask_explain,
            user_gave_info=gave_info,
            coach_action=action or "",
            subgoal_id=subgoal or "",
            prompt_version="v6",
            scenario_version=session.scene_version or "",
        )
    )


def _scene_subgoal_keys(session: Session) -> list[str]:
    """从场景配置读取子目标 key 列表；缺失时回退面试默认。"""
    scene = SCENARIOS.get(session.scene_id, {})
    keys = scene.get("subgoals") or ["intro", "followup"]
    return list(keys)


def _load_subgoals(session: Session) -> dict[str, Any]:
    try:
        subgoals = json.loads(session.subgoals) if session.subgoals else {}
    except json.JSONDecodeError:
        subgoals = {}
    if not isinstance(subgoals, dict):
        subgoals = {}
    for key in _scene_subgoal_keys(session):
        subgoals.setdefault(key, {"status": "", "evidence": []})
    return subgoals


def _current_subgoal(session: Session, subgoals: dict[str, Any]) -> str:
    """返回第一个未独立完成的子目标。"""
    keys = _scene_subgoal_keys(session)
    for key in keys:
        if subgoals.get(key, {}).get("status") != "independent":
            return key
    return keys[-1]


def _all_subgoals_done(session: Session, subgoals: dict[str, Any]) -> bool:
    return all(
        subgoals.get(key, {}).get("status") == "independent"
        for key in _scene_subgoal_keys(session)
    )


def _last_coach_text(db: DbSession, session_id: str) -> str:
    last = (
        db.query(Turn)
        .filter(Turn.session_id == session_id)
        .order_by(Turn.seq.desc())
        .first()
    )
    return last.reply_text if last else ""


def _recent_history(db: DbSession, session_id: str, limit: int = 5) -> list[dict[str, str]]:
    turns = (
        db.query(Turn)
        .filter(Turn.session_id == session_id)
        .order_by(Turn.seq.desc())
        .limit(limit)
        .all()
    )
    history: list[dict[str, str]] = []
    for t in reversed(turns):
        if t.input_type == "coach":
            history.append({"who": "coach", "text": t.reply_text})
        elif t.input_type == "practice":
            history.append({"who": "user", "text": f"(跟读：{t.practice_sentence})"})
        else:
            u = t.revised_text or t.user_text or t.transcript
            if u:
                history.append({"who": "user", "text": u})
            if t.reply_text:
                history.append({"who": "coach", "text": t.reply_text})
    return history


def finalize_session(db: DbSession, session: Session) -> None:
    """幂等收尾：生成回顾并保存，按记忆开关生成候选记忆。"""
    if session.review:
        return
    review = build_review(db, session, session.end_reason or "user_ended")
    session.review = json.dumps(review, ensure_ascii=False)
    db.commit()
    if memory_svc.is_memory_enabled(db, session.user_id):
        memory_svc.generate_candidates(db, session.user_id, session.id, review)


def create_session(
    db: DbSession,
    user_id: str,
    level: str,
    difficulty: str,
    duration_minutes: float = 5.0,
    scene_id: str = "job_interview",
    request_key: str = "",
    plan_id: int | None = None,
    plan_day: int | None = None,
) -> dict[str, Any]:
    _get_or_create_user(db, user_id, level)
    scene = get_scene(scene_id)
    goal_text = scene["goal_text"]
    request_payload = json.dumps({
        "level": level, "difficulty": difficulty, "duration_minutes": duration_minutes,
        "scene_id": scene_id, "plan_id": plan_id, "plan_day": plan_day,
    }, sort_keys=True)
    replayed = intent_result.replay(db, user_id, "create_session", user_id, request_key, "create", request_payload)
    if replayed is not None:
        return replayed

    # 幂等：同一请求键返回首次结果
    if request_key:
        existing = (
            db.query(Session)
            .filter(Session.user_id == user_id, Session.request_key == request_key)
            .first()
        )
        if existing:
            if (existing.level, existing.difficulty, existing.duration_minutes, existing.scene_id) != (level, difficulty, duration_minutes, scene_id):
                raise AppError("IDEMPOTENCY_CONFLICT", "同一请求键内容不一致", status_code=409)
            if plan_id is not None and (existing.plan_id, existing.plan_day) != (plan_id, plan_day):
                raise AppError("IDEMPOTENCY_CONFLICT", "同一请求键内容不一致", status_code=409)
            opening = (
                db.query(Turn)
                .filter(Turn.session_id == existing.id, Turn.input_type == "coach")
                .order_by(Turn.seq)
                .first()
            )
            return {
                "session_id": existing.id,
                "status": existing.status,
                "goal_text": existing.goal_text,
                "scene_id": existing.scene_id,
                "scene_label": scene["label"],
                "reply_text": opening.reply_text if opening else "",
                "reply_audio_url": opening.reply_audio_url if opening else "",
            }

    from app.services.training_plan import bind_day

    bound_plan_id, bound_plan_day = bind_day(db, user_id, scene_id, plan_id, plan_day)
    session_id = uuid.uuid4().hex
    guard = BudgetGuard(db)
    guard.reserve(settings.nominal_cost_per_turn, session_id, user_id)

    session = Session(
        id=session_id,
        user_id=user_id,
        status=S_PREPARING,
        goal_text=goal_text,
        difficulty=difficulty,
        level=level,
        scene_id=scene_id,
        scene_version=scene["version"],
        subgoals="{}",
        duration_minutes=duration_minutes,
        rechecked=False,
        request_key=request_key or None,
        plan_id=bound_plan_id,
        plan_day=bound_plan_day,
        active_since=_now(),
    )
    db.add(session)
    audio_url = ""
    reset_usage_attempts()
    try:
        coach = get_coach_provider()
        decision = coach.opening(scene, level)
        tts = get_tts_provider()
        audio_url, _, tts_attempts = _synth_and_save(tts, decision.reply_text)
        session.status = S_WAITING
        opening_turn_id = uuid.uuid4().hex
        session.current_question_id = opening_turn_id
        session.current_question_text = decision.reply_text
        session.current_question_audio_url = audio_url
        db.add(
            Turn(
                id=opening_turn_id,
                session_id=session_id,
                seq=0,
                input_type="coach",
                reply_text=decision.reply_text,
                reply_audio_url=audio_url,
                action_type="follow_up",
                question_id=opening_turn_id,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        guard.release(settings.nominal_cost_per_turn, session_id, user_id)
        if audio_url:
            _delete_audio_files({os.path.basename(audio_url)})
        _record_failed_llm_usage(db, user_id, session_id)
        raise
    guard.settle(settings.nominal_cost_per_turn, session_id, user_id)
    _record_generated_audio_usage(db, user_id, session_id, opening_turn_id, decision.reply_text, tts_attempts, bool(audio_url))

    response = {
        "session_id": session_id,
        "status": session.status,
        "goal_text": goal_text,
        "scene_id": scene_id,
        "scene_label": scene["label"],
        "reply_text": decision.reply_text,
        "reply_audio_url": audio_url,
    }
    intent_result.remember(db, user_id, "create_session", user_id, request_key, "create", request_payload, response)
    return response


def create_sentence_practice(
    db: DbSession, user_id: str, source_session_id: str, source_turn_id: str
) -> dict[str, Any]:
    source = db.get(Session, source_session_id)
    if source is None or source.user_id != user_id:
        raise AppError("FORBIDDEN", "无权使用该来源", status_code=403)
    if source.status != S_ENDED:
        raise AppError("INVALID_STATE", "源会话尚未结束", status_code=409)
    review = json.loads(source.review) if source.review else {}
    imp = next(
        (i for i in review.get("improvements", []) if i.get("turn_id") == source_turn_id),
        None,
    )
    if imp is None:
        raise AppError("NOT_FOUND", "改进建议不存在或已失效", status_code=404)
    if imp.get("disputed") or review.get("stale"):
        raise AppError("INVALID_STATE", "该建议已争议或失效，不能练这一句", status_code=409)
    sentence = imp.get("practice_sentence") or ""
    goal = imp.get("explanation_cn") or imp.get("suggestion") or ""
    if not sentence:
        raise AppError("INVALID_STATE", "该建议没有可练的句子", status_code=409)

    # 幂等：同一来源 turn_id 只创建一个有效记录
    existing = (
        db.query(Session)
        .filter(
            Session.user_id == user_id,
            Session.practice_type == "sentence_practice",
            Session.source_turn_id == source_turn_id,
        )
        .first()
    )
    if existing:
        return {
            "session_id": existing.id,
            "status": existing.status,
            "goal_text": existing.goal_text,
            "reply_text": existing.practice_sentence,
            "reply_audio_url": "",
        }

    session_id = uuid.uuid4().hex
    guard = BudgetGuard(db)
    guard.reserve(settings.nominal_cost_per_turn, session_id, user_id)
    session = Session(
        id=session_id,
        user_id=user_id,
        status=S_PREPARING,
        goal_text="Practice this sentence and see evidence-based feedback.",
        difficulty="standard",
        scene_id=source.scene_id,
        scene_version=source.scene_version,
        subgoals="{}",
        duration_minutes=5.0,
        practice_type="sentence_practice",
        source_session_id=source_session_id,
        source_turn_id=source_turn_id,
        practice_sentence=sentence,
        practice_goal=goal,
        drill_stage="full_text",
        drill_target=sentence,
        drill_meaning=goal,
        rechecked=False,
        active_since=_now(),
    )
    db.add(session)
    audio_url = ""
    reset_usage_attempts()
    try:
        tts = get_tts_provider()
        audio_url, _, tts_attempts = _synth_and_save(tts, sentence)
        opening_text = f"Let's practice this sentence: {sentence}"
        opening_turn_id = uuid.uuid4().hex
        db.add(
            Turn(
                id=opening_turn_id,
                session_id=session_id,
                seq=0,
                input_type="coach",
                reply_text=opening_text,
                reply_audio_url=audio_url,
                action_type="suggest_practice",
                practice_sentence=sentence,
            )
        )
        session.status = S_PRACTICE
        db.commit()
    except Exception:
        db.rollback()
        guard.release(settings.nominal_cost_per_turn, session_id, user_id)
        if audio_url:
            _delete_audio_files({os.path.basename(audio_url)})
        _record_failed_llm_usage(db, user_id, session_id)
        raise
    guard.settle(settings.nominal_cost_per_turn, session_id, user_id)
    _record_generated_audio_usage(db, user_id, session_id, opening_turn_id, sentence, tts_attempts, bool(audio_url))
    return {
        "session_id": session_id,
        "status": S_PRACTICE,
        "goal_text": session.goal_text,
        "reply_text": opening_text,
        "reply_audio_url": audio_url,
    }


def submit_turn(
    db: DbSession,
    session_id: str,
    user_id: str,
    input_type: str,
    user_text: str | None = None,
    audio_bytes: bytes | None = None,
    request_key: str = "",
    pre_transcribed: str | None = None,
) -> dict[str, Any]:
    session = _require_session(db, session_id, user_id)
    request_hash = _turn_request_hash(input_type, user_text, audio_bytes, pre_transcribed)
    # 先重放后检查会话状态：结束/暂停后重复请求也应拿到首次结果。
    if request_key:
        existing = (
            db.query(Turn)
            .filter(Turn.session_id == session_id, Turn.request_key == request_key)
            .first()
        )
        if existing:
            if existing.request_hash and existing.request_hash != request_hash:
                raise AppError("IDEMPOTENCY_CONFLICT", "同一请求键内容不一致", status_code=409)
            return _turn_done(db, session, existing)
        risk_replay = db.query(AsrRiskRecord).filter_by(session_id=session_id, request_key=request_key).first()
        if risk_replay is not None:
            if risk_replay.request_hash != request_hash:
                raise AppError("IDEMPOTENCY_CONFLICT", "同一请求键内容不一致", status_code=409)
            if risk_replay.resolved_at is None and session.pending_risk:
                return {"risk_hold": True, **json.loads(session.pending_risk)}
            confirmed = db.query(Turn).filter_by(session_id=session_id, request_key=f"confirm:{risk_replay.turn_id}").first()
            if confirmed is not None:
                return _turn_done(db, session, confirmed)
            raise AppError("INVALID_STATE", "该录音已处理，请刷新查看结果", status_code=409)
    if session.status == S_ENDED:
        raise AppError("SESSION_ENDED", "会话已结束", status_code=409)
    if session.status == S_PAUSED:
        raise AppError("SESSION_PAUSED", "会话已暂停，请先恢复", status_code=409)
    if session.status not in (S_WAITING, S_PRACTICE, S_RECHECK):
        raise AppError("INVALID_STATE", "当前状态不能提交回合", status_code=409)
    if session.pending_risk and not request_key.startswith("confirm:"):
        raise AppError("RISK_CONFIRM_REQUIRED", "请先确认或重录待核对的语音", status_code=409)

    turn_start = time.perf_counter()

    # 时长校验（F-07）：精确累计非暂停时长，到时停止新提交并收尾
    if _active_elapsed_seconds(session) > session.duration_minutes * 60:
        session.status = S_ENDED
        session.end_reason = "time_limit"
        session.ended_at = _now()
        db.commit()
        finalize_session(db, session)
        raise AppError("TIME_LIMIT", "练习时间已到，本次练习结束", status_code=409)

    guard = BudgetGuard(db)
    guard.reserve(settings.nominal_cost_per_turn, session_id, user_id)
    tool_budget = ToolBudget()
    reset_usage_attempts()
    risk_recording_url = ""
    risk_persisted = False
    asr_called = False
    asr_completed = False

    try:
        turn_id = uuid.uuid4().hex
        transcript = ""
        if input_type == "voice":
            if pre_transcribed is not None:
                transcript = pre_transcribed
                text_for_eval = pre_transcribed
            else:
                if not audio_bytes:
                    raise AppError("EMPTY_AUDIO", "音频内容为空")
                fmt = _validate_audio(audio_bytes)
                uploads_dir = settings.data_dir / "uploads"
                uploads_dir.mkdir(parents=True, exist_ok=True)
                audio_path = uploads_dir / f"{turn_id}.{fmt}"
                audio_path.write_bytes(audio_bytes)
                asr = get_asr_provider()
                asr_called = True
                transcript = asr.transcribe(audio_path)
                asr_completed = True
                text_for_eval = transcript
                # 识别风险判定（REQ-32）：中/高先确认，不评估、不教练、不写独立证据
                risk_level, reasons = assess_risk(transcript)
                if risk_level in ("mid", "high"):
                    recording_url = _save_audio(audio_bytes, fmt)  # 保存本人录音供听回
                    risk_recording_url = recording_url
                    db.add(
                        AsrRiskRecord(
                            user_id=user_id,
                            session_id=session_id,
                            turn_id=turn_id,
                            risk_level=risk_level,
                            reason_codes=";".join(reasons),
                            original_text=transcript,
                            recording_url=recording_url,
                            request_key=request_key,
                            request_hash=request_hash,
                            action="",
                            asr_rule_version="v1",
                        )
                    )
                    session.pending_risk = json.dumps(
                        {
                            "turn_id": turn_id,
                            "risk_level": risk_level,
                            "reason_codes": reasons,
                            "transcript": transcript,
                            "recording_url": recording_url,
                        },
                        ensure_ascii=False,
                    )
                    db.commit()
                    risk_persisted = True
                    guard.settle(settings.nominal_cost_per_turn, session_id, user_id)
                    try:
                        provider_usage_svc.record_asr(db, user_id, session_id, turn_id, len(audio_bytes))
                    except Exception:
                        db.rollback()
                    try:
                        qa_recording_svc.capture_if_consented(db, user_id, session_id, turn_id, audio_bytes, fmt)
                    except Exception:
                        db.rollback()  # 质检副本失败不破坏已保存的练习回合
                    return {
                        "risk_hold": True,
                        "risk_level": risk_level,
                        "reason_codes": reasons,
                        "turn_id": turn_id,
                        "transcript": transcript,
                        "recording_url": recording_url,
                    }
        else:
            text_for_eval = user_text or ""

        hint_used = _last_action_type(db, session_id) == "hint" or _has_answer_help_for_question(
            db, session_id, session.current_question_id
        )

        # 当前子目标 + 题目（绑定当前有效问题，不靠“最后一条回复”猜）
        subgoals = _load_subgoals(session)
        subgoal = _current_subgoal(session, subgoals)
        question = session.current_question_text or _last_coach_text(db, session_id)
        question_id = session.current_question_id

        # 独立评估（与生成分离；上下文告知正在评估哪个子目标）
        tool_budget.consume()
        evaluator = get_evaluator_provider()
        eval_out = evaluator.evaluate(
            {
                "question": question,
                "answer": text_for_eval,
                "hint_used": hint_used,
                "input_type": input_type,
                "goal": session.goal_text,
                "scene_id": session.scene_id,
                "subgoal": subgoal,
                "subgoal_standard": SCENARIOS.get(session.scene_id, {}).get("subgoal_standard", {}).get(subgoal, ""),
                "acceptable_points": SCENARIOS.get(session.scene_id, {}).get("acceptable_points", []),
            }
        )

        # 文字输入不构成口语独立证据（AC10-01）：把 independent 降级为 text_completed
        if input_type == "text" and eval_out.result == "independent":
            eval_out.result = "text_completed"

        # 更新子目标（先算到局部变量，提交前统一应用）
        # 多意图：仅当场景配置显式声明 co_complete 时，一次回答可同时完成多个子目标
        # （如「认识新同事」的自我介绍 + 反问）；其余场景保持每回合只推进一个子目标，
        # 「认识新朋友」等两轮规则不受影响。
        new_subgoals = dict(subgoals)
        if eval_out.result == "independent":
            new_subgoals[subgoal] = {"status": "independent", "evidence": [turn_id]}
            co_complete = SCENARIOS.get(session.scene_id, {}).get("co_complete") or {}
            lowered = text_for_eval.lower()
            for key, phrases in co_complete.items():
                if key == subgoal:
                    continue
                if new_subgoals.get(key, {}).get("status") == "independent":
                    continue
                if any(phrase in lowered for phrase in phrases):
                    new_subgoals[key] = {"status": "independent", "evidence": [turn_id]}
        else:
            new_subgoals[subgoal] = {"status": eval_out.result, "evidence": []}

        # 教练决策（丰富上下文）
        tool_budget.consume()
        coach = get_coach_provider()
        user = db.get(User, session.user_id)
        memories = []
        if memory_svc.is_memory_enabled(db, user_id):
            memories = [m.text for m in memory_svc.get_usable_memories(db, user_id, session.scene_id)]
        decision = coach.decide(
            {
                "goal": session.goal_text,
                "difficulty": session.difficulty,
                "level": user.level if user else "basic",
                "scene_context": SCENARIOS.get(session.scene_id, {}).get("coach_context", "an English conversation coach"),
                "scene_id": session.scene_id,
                "coach_persona": SCENARIOS.get(session.scene_id, {}).get("coach_persona", ""),
                "current_question": question,
                "current_answer": text_for_eval,
                "input_type": input_type,
                "hint_used": hint_used,
                "eval_result": eval_out.result,
                "subgoal": subgoal,
                "subgoal_standard": SCENARIOS.get(session.scene_id, {}).get("subgoal_standard", {}).get(subgoal, ""),
                "history": _recent_history(db, session_id),
                "rechecked": session.rechecked,
                "memories": memories,
            }
        )

    except Exception:
        db.rollback()
        guard.release(settings.nominal_cost_per_turn, session_id, user_id)
        if risk_recording_url and not risk_persisted:
            _delete_audio_files({os.path.basename(risk_recording_url)})
        if asr_called and not asr_completed and audio_bytes:
            try:
                provider_usage_svc.record_asr(db, user_id, session_id, "", len(audio_bytes), False)
            except Exception:
                db.rollback()
        try:
            provider_usage_svc.record_llm_attempts(db, user_id, session_id)
        except Exception:
            db.rollback()
        raise

    # 状态迁移（先算到局部变量）
    both_done = _all_subgoals_done(session, new_subgoals)
    new_status = S_WAITING
    if decision.action_type == "suggest_practice":
        new_status = S_PRACTICE
    elif decision.action_type == "recheck":
        new_status = S_RECHECK
    if both_done:
        new_status = S_ENDED

    # 终态再校验（F-03）：外部调用期间会话可能已被并发结束/删除
    try:
        db.refresh(session)
    except Exception:
        db.rollback()
        guard.release(settings.nominal_cost_per_turn, session_id, user_id)
        raise AppError("SESSION_NOT_FOUND", "会话已被删除", status_code=404)
    if session.status == S_ENDED or session.status == S_PAUSED:
        code = "SESSION_ENDED" if session.status == S_ENDED else "SESSION_PAUSED"
        msg = "会话已结束" if session.status == S_ENDED else "会话已暂停"
        db.rollback()
        guard.release(settings.nominal_cost_per_turn, session_id, user_id)
        raise AppError(code, msg, status_code=409)

    # 统一应用变更
    db.add(
        Evaluation(
            session_id=session_id,
            turn_id=turn_id,
            result=eval_out.result,
            subgoal=subgoal,
            input_type=input_type,
            question=question,
            evidence_turn_ids=",".join(eval_out.evidence_turn_ids),
            standard_version=eval_out.standard_version,
        )
    )
    session.subgoals = json.dumps(new_subgoals, ensure_ascii=False)
    if decision.action_type == "recheck":
        session.rechecked = True
    if both_done:
        session.end_reason = "goal_reached"
        session.ended_at = _now()
    session.status = new_status
    # 新问题（追问/复测）才更新当前问题；hint/跟读不改变当前问题
    if decision.action_type in ("follow_up", "recheck"):
        session.current_question_id = turn_id
        session.current_question_text = decision.reply_text
        session.current_question_audio_url = ""  # 音频在 synthesize_turn_audio 阶段补齐

    turn = Turn(
        id=turn_id,
        session_id=session_id,
        seq=_next_seq(db, session_id),
        input_type=input_type,
        transcript=transcript,
        user_text=text_for_eval,
        reply_text=decision.reply_text,
        reply_audio_url="",
        action_type=decision.action_type,
        audio_status="pending",
        hint_used=hint_used,
        practice_sentence=decision.practice_sentence,
        eval_result=eval_out.result,
        question_id=question_id,
        request_key=request_key or None,
        request_hash=request_hash,
        result_status=new_status,
    )
    db.add(turn)
    _log_event(db, session_id, turn_id=turn_id, action_type=decision.action_type, basis=eval_out.result, input_type=input_type, tool_result=eval_out.result, request_key=request_key or None, duration_ms=int((time.perf_counter() - turn_start) * 1000), usage=get_last_usage())
    _mark_conversation_quality(db, session, turn_id, text_for_eval, decision.action_type, subgoal)
    db.commit()
    guard.settle(settings.nominal_cost_per_turn, session_id, user_id)
    try:
        provider_usage_svc.record_llm_attempts(db, user_id, session_id, turn_id)
        if input_type == "voice" and audio_bytes and pre_transcribed is None:
            provider_usage_svc.record_asr(db, user_id, session_id, turn_id, len(audio_bytes))
    except Exception:
        db.rollback()  # 用量旁路故障不应把已成功的回合伪装成可重试失败
    if input_type == "voice" and audio_bytes and pre_transcribed is None:
        try:
            qa_recording_svc.capture_if_consented(db, user_id, session_id, turn_id, audio_bytes, fmt)
        except Exception:
            db.rollback()
    # 统一收尾（自动达标时）
    if new_status == S_ENDED:
        finalize_session(db, session)

    return {
        "turn_id": turn_id,
        "transcript": transcript,
        "reply_text": decision.reply_text,
        "reply_audio_url": "",
        "audio_status": "pending",
        "action_type": decision.action_type,
        "eval_result": eval_out.result,
        "status": new_status,
        "hint_used": hint_used,
        "practice_sentence": decision.practice_sentence,
    }


def confirm_turn(
    db: DbSession, session_id: str, user_id: str, turn_id: str, confirmed_text: str, action: str = "confirm"
) -> dict[str, Any]:
    """用户确认或修订可疑转写后继续同题（REQ-32）。"""
    risk = (
        db.query(AsrRiskRecord)
        .filter(
            AsrRiskRecord.session_id == session_id,
            AsrRiskRecord.turn_id == turn_id,
            AsrRiskRecord.user_id == user_id,
        )
        .first()
    )
    if risk is None:
        raise AppError("NOT_FOUND", "待确认的转写不存在", status_code=404)
    if risk.resolved_at is not None:
        if risk.confirmed_text != confirmed_text or risk.action != action:
            raise AppError("IDEMPOTENCY_CONFLICT", "同一请求键内容不一致", status_code=409)
        session = _require_session(db, session_id, user_id)
        existing = db.query(Turn).filter_by(session_id=session_id, request_key=f"confirm:{turn_id}").first()
        if existing is None:
            raise AppError("INVALID_STATE", "该转写已处理", status_code=409)
        return _turn_done(db, session, existing)

    if action == "text_fallback":
        partial = submit_turn(
            db, session_id, user_id, "text",
            user_text=confirmed_text,
            request_key=f"confirm:{turn_id}",
        )
    else:
        partial = submit_turn(
            db, session_id, user_id, "voice",
            pre_transcribed=confirmed_text,
            request_key=f"confirm:{turn_id}",
        )
    # 提交成功后写确认版本并清空待确认状态
    risk = (
        db.query(AsrRiskRecord)
        .filter(
            AsrRiskRecord.session_id == session_id,
            AsrRiskRecord.turn_id == turn_id,
            AsrRiskRecord.user_id == user_id,
        )
        .first()
    )
    recording_url = risk.recording_url if risk is not None else ""
    if risk is not None:
        risk.confirmed_text = confirmed_text
        risk.action = action or "confirm"
        risk.resolved_at = _now()
        risk.recording_url = ""
    session = _require_session(db, session_id, user_id)
    session.pending_risk = ""
    db.commit()
    if recording_url:
        _delete_audio_files({os.path.basename(recording_url)})
    return synthesize_turn_audio(db, session_id, user_id, partial["turn_id"])


def discard_pending_risk(db: DbSession, session_id: str, user_id: str, turn_id: str) -> dict[str, bool]:
    """重录前丢弃上一段待确认音频；保留风险审计，但撤销播放引用。"""
    session = _require_session(db, session_id, user_id)
    risk = db.query(AsrRiskRecord).filter_by(session_id=session_id, user_id=user_id, turn_id=turn_id).first()
    if risk is None or risk.resolved_at is not None:
        raise AppError("NOT_FOUND", "待确认的转写不存在", status_code=404)
    recording_url = risk.recording_url
    risk.recording_url = ""
    risk.action = "rerecord"
    risk.resolved_at = _now()
    session.pending_risk = ""
    db.commit()
    if recording_url:
        _delete_audio_files({os.path.basename(recording_url)})
    return {"discarded": True}


def _turn_done(db: DbSession, session: Session, turn: Turn) -> dict[str, Any]:
    return {
        "turn_id": turn.id,
        "transcript": turn.transcript,
        "reply_text": turn.reply_text,
        "reply_audio_url": turn.reply_audio_url,
        "audio_status": turn.audio_status,
        "action_type": turn.action_type,
        "eval_result": turn.eval_result,
        "status": turn.result_status or session.status,
        "hint_used": turn.hint_used,
        "practice_sentence": turn.practice_sentence,
    }


def synthesize_turn_audio(
    db: DbSession, session_id: str, user_id: str, turn_id: str, retry_failed: bool = False
) -> dict[str, Any]:
    """为已落库的回合补充语音。TTS 失败不毁文字回合；幂等（已有音频直接返回）。"""
    session = _require_session(db, session_id, user_id)
    turn = db.get(Turn, turn_id)
    if turn is None or turn.session_id != session_id:
        raise AppError("TURN_NOT_FOUND", "回合不存在", status_code=404)

    # 已有音频且成功 → 幂等返回
    if turn.audio_status == "ok" and turn.reply_audio_url:
        return _turn_done(db, session, turn)
    if turn.audio_status == "failed" and not retry_failed:
        return _turn_done(db, session, turn)

    text = turn.practice_sentence or turn.reply_text
    if not text:
        turn.audio_status = "failed"
        db.commit()
        return _turn_done(db, session, turn)

    tts = get_tts_provider()
    tts_result = tts.synthesize_result(text)
    audio_url = _save_audio(tts_result.audio, tts.ext) if tts_result.audio is not None else ""
    audio_status = "ok" if audio_url else "failed"
    turn.reply_audio_url = audio_url
    turn.audio_status = audio_status
    if session.current_question_id == turn_id:
        session.current_question_audio_url = audio_url
    try:
        db.commit()
    except Exception:
        db.rollback()
        if audio_url:
            _delete_audio_files({os.path.basename(audio_url)})
        raise
    try:
        provider_usage_svc.record_tts(db, user_id, session_id, turn_id, text, tts_result.attempts, bool(audio_url))
    except Exception:
        db.rollback()
    return _turn_done(db, session, turn)


def submit_practice(
    db: DbSession, session_id: str, user_id: str, audio_bytes: bytes, sentence: str, request_key: str = ""
) -> dict[str, Any]:
    session = _require_session(db, session_id, user_id)
    request_payload = hashlib.sha256(audio_bytes).hexdigest() + "\0" + sentence
    replayed = intent_result.replay(db, user_id, "practice", session_id, request_key, "voice", request_payload)
    if replayed is not None:
        return replayed
    if session.status != S_PRACTICE:
        raise AppError("INVALID_STATE", "当前状态不能提交跟读", status_code=409)

    fmt = _validate_audio(audio_bytes)
    guard = BudgetGuard(db)
    guard.reserve(settings.nominal_cost_per_turn, session_id, user_id)
    audio_path = None
    recording_url = ""
    audio_url = ""
    reset_usage_attempts()
    try:
        uploads_dir = settings.data_dir / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        audio_path = uploads_dir / f"{uuid.uuid4().hex}.{fmt}"
        audio_path.write_bytes(audio_bytes)

        # 保存本人录音供回放/对比（会话内）
        recording_url = _save_audio(audio_bytes, fmt)

        provider = get_practice_eval_provider()
        try:
            eval_result = provider.evaluate(audio_path, sentence)
        except AppError as exc:
            # 评测失败：保留本人录音供对比，无评分，不冒充结果（AC10-06）
            eval_result = {"score": None, "dimension": "sentence", "word_items": [], "error_code": exc.code}
            eval_result["recording_url"] = recording_url
            eval_result["reference_version"] = session.scene_version or ""
            eval_result["drill_stage"] = session.drill_stage or ""
            turn = Turn(
                id=uuid.uuid4().hex,
                session_id=session_id,
                seq=_next_seq(db, session_id),
                input_type="practice",
                practice_sentence=sentence,
                practice_eval=json.dumps(eval_result, ensure_ascii=False),
                reply_text="",
                reply_audio_url="",
                action_type="follow_up",
                audio_status="failed",
                recording_url=recording_url,
            )
            db.add(turn)
            _log_event(db, session_id, turn_id=turn.id, action_type="practice", basis="follow_up", input_type="practice", tool_result="unscored", error=exc.code)
            session.status = S_WAITING
            db.commit()
            guard.settle(settings.nominal_cost_per_turn, session_id, user_id)
            if audio_path is not None:
                audio_path.unlink(missing_ok=True)
            response = {
                "session_id": session_id,
                "status": S_WAITING,
                "eval": eval_result,
                "recording_url": recording_url,
                "recheck_reply_text": "",
                "recheck_audio_url": "",
            }
            intent_result.remember(db, user_id, "practice", session_id, request_key, "voice", request_payload, response)
            try:
                provider_usage_svc.record_ise(db, user_id, session_id, turn.id, len(audio_bytes), sentence, False)
                qa_recording_svc.capture_if_consented(db, user_id, session_id, turn.id, audio_bytes, fmt)
            except Exception:
                db.rollback()
            return response
        # 评价成功：绑定录音版本 / 参考句版本 / 阶梯阶段
        eval_result["recording_url"] = recording_url
        eval_result["reference_version"] = session.scene_version or ""
        eval_result["drill_stage"] = session.drill_stage or ""

        score = eval_result.get("score")
        valid = score is not None

        coach = get_coach_provider()
        decision = coach.decide(
            {
                "goal": session.goal_text,
                "difficulty": session.difficulty,
                "subgoal": _current_subgoal(session, _load_subgoals(session)),
                "practice": {
                    "score": score,
                    "valid": valid,
                    "reference_sentence": sentence,
                },
                "history": _recent_history(db, session_id),
            }
        )
        tts = get_tts_provider()
        tts_result = tts.synthesize_result(decision.reply_text)
        audio_url = _save_audio(tts_result.audio, tts.ext) if tts_result.audio is not None else ""

        turn = Turn(
            id=uuid.uuid4().hex,
            session_id=session_id,
            seq=_next_seq(db, session_id),
            input_type="practice",
            practice_sentence=sentence,
            practice_eval=json.dumps(eval_result, ensure_ascii=False),
            reply_text=decision.reply_text,
            reply_audio_url=audio_url,
            action_type=decision.action_type,
            recording_url=recording_url,
        )
        db.add(turn)
        _log_event(db, session_id, turn_id=turn.id, action_type="practice", basis=decision.action_type, input_type="practice", tool_result="scored" if valid else "unscored")

        # 状态迁移
        new_status = S_WAITING
        if decision.action_type == "suggest_practice":
            new_status = S_PRACTICE
        elif decision.action_type == "recheck":
            session.rechecked = True
            new_status = S_RECHECK
        session.status = new_status
        db.commit()
    except Exception:
        db.rollback()
        guard.release(settings.nominal_cost_per_turn, session_id, user_id)
        if audio_path is not None:
            audio_path.unlink(missing_ok=True)
        if recording_url:
            (settings.data_dir / "audio" / os.path.basename(recording_url)).unlink(missing_ok=True)
        if audio_url:
            _delete_audio_files({os.path.basename(audio_url)})
        _record_failed_llm_usage(db, user_id, session_id)
        raise
    guard.settle(settings.nominal_cost_per_turn, session_id, user_id)
    response = {
        "session_id": session_id,
        "status": new_status,
        "eval": eval_result,
        "recording_url": recording_url,
        "recheck_reply_text": decision.reply_text,
        "recheck_audio_url": audio_url,
    }
    intent_result.remember(db, user_id, "practice", session_id, request_key, "voice", request_payload, response)
    try:
        provider_usage_svc.record_ise(db, user_id, session_id, turn.id, len(audio_bytes), sentence, True)
        provider_usage_svc.record_llm_attempts(db, user_id, session_id, turn.id)
        provider_usage_svc.record_tts(db, user_id, session_id, turn.id, decision.reply_text, tts_result.attempts, bool(audio_url))
        qa_recording_svc.capture_if_consented(db, user_id, session_id, turn.id, audio_bytes, fmt)
    except Exception:
        db.rollback()
    return response


def _review_of(session: Session) -> dict[str, Any]:
    if session.review:
        try:
            review = dedupe_improvements(json.loads(session.review))
            if review.get("stale"):
                return {**review, "goal_status": "revised", "evidence": [], "good_fragments": [], "improvements": [], "ratings": {}}
            return review
        except json.JSONDecodeError:
            return {}
    return {}


def _end_result(db: DbSession, session: Session) -> dict[str, Any]:
    return {"summary": _summary(db, session), "review": _review_of(session)}


def _summary(db: DbSession, session: Session) -> dict[str, Any]:
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
    if session.review and _review_of(session).get("stale"):
        goal_status = "revised"
    elif session.end_reason == "goal_reached":
        goal_status = "independent"
    return {
        "session_id": session.id,
        "status": session.status,
        "end_reason": session.end_reason,
        "goal_status": goal_status,
        "turn_count": len(user_turns),
        "latest_eval": latest_eval,
    }


def end_session(db: DbSession, session_id: str, user_id: str, reason: str) -> dict[str, Any]:
    session = _require_session(db, session_id, user_id)
    if session.status != S_ENDED:
        session.status = S_ENDED
        session.end_reason = reason or "user_ended"
        session.ended_at = _now()
        db.commit()
    finalize_session(db, session)  # 幂等：已生成则跳过
    cleanup_old_uploads()
    return _end_result(db, session)


def get_session_detail(db: DbSession, session_id: str, user_id: str) -> dict[str, Any]:
    session = _require_session(db, session_id, user_id)
    turns = (
        db.query(Turn).filter(Turn.session_id == session.id).order_by(Turn.seq).all()
    )
    detail: dict[str, Any] = {
        "session_id": session.id,
        "status": session.status,
        "goal_text": session.goal_text,
        "difficulty": session.difficulty,
        "level": session.level or "basic",
        "pending_risk": json.loads(session.pending_risk) if session.pending_risk else None,
        "qa_recording_consent": qa_recording_svc.consent_status(db, user_id, session_id)["granted"],
        "current_help_level": (
            db.query(Turn)
            .filter(
                Turn.session_id == session.id,
                Turn.question_id == session.current_question_id,
                Turn.help_type == "organize",
            )
            .count()
        ),
        "end_reason": session.end_reason,
        "practice_type": session.practice_type,
        "scene_id": session.scene_id,
        "scene_label": SCENARIOS.get(session.scene_id, {}).get("label", ""),
        "source_invalid": session.source_invalid,
        "review": _review_of(session),
        "turns": [
            {
                "turn_id": t.id,
                "seq": t.seq,
                "input_type": t.input_type,
                "user_text": t.user_text,
                "transcript": t.transcript,
                "reply_text": t.reply_text,
                "reply_audio_url": t.reply_audio_url,
                "practice_sentence": t.practice_sentence,
                "action_type": t.action_type,
                "recording_url": t.recording_url,
                "hint_used": t.hint_used,
                "eval_result": t.eval_result,
                "revised_text": t.revised_text,
            }
            for t in turns
        ],
    }
    if session.practice_type == "sentence_practice":
        from app.services import drill_svc
        detail["drill"] = drill_svc.drill_view(session, db)
    return detail


def _count_user_turns(db: DbSession, session_id: str) -> int:
    return (
        db.query(Turn)
        .filter(Turn.session_id == session_id, Turn.input_type.in_(("voice", "text")))
        .count()
    )


def list_sessions(db: DbSession, user_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    from app.models.entities import ReviewQueueItem

    filters = filters or {}
    allowed_review = {"none", "pending", "awaiting_retest", "mastered", "disputed", "invalid"}
    if filters.get("review_status") and filters["review_status"] not in allowed_review:
        raise AppError("INVALID_INPUT", "未知的复练状态", status_code=422)
    q = db.query(Session).filter(Session.user_id == user_id)
    if filters.get("scene_id"):
        q = q.filter(Session.scene_id == filters["scene_id"])
    if filters.get("series"):
        scene_ids = [
            s["scene_id"] for s in SCENARIOS.values()
            if isinstance(s, dict) and s.get("series") == filters["series"]
        ]
        if scene_ids:
            q = q.filter(Session.scene_id.in_(scene_ids))
    if filters.get("practice_type"):
        q = q.filter(Session.practice_type == filters["practice_type"])
    if filters.get("status") == "completed":
        q = q.filter(Session.status == S_ENDED)
        # 普通练习完成须有回顾；单句/今日复练无 review，结束即完成
        q = q.filter(
            or_(
                Session.review != "",
                Session.practice_type.in_(["sentence_practice", "daily_review"]),
            )
        )
    elif filters.get("status") == "incomplete":
        q = q.filter(or_(Session.status != S_ENDED, (Session.practice_type == "interview") & (Session.review == "")))
    sessions = q.order_by(Session.started_at.desc()).all()
    queue_statuses: dict[str, set[str]] = {}
    for item in db.query(ReviewQueueItem).filter(ReviewQueueItem.user_id == user_id).all():
        queue_statuses.setdefault(item.source_session_id, set()).add(item.status)
    result = []
    for s in sessions:
        statuses = queue_statuses.get(s.id, set())
        wanted = filters.get("review_status")
        if wanted and ((wanted == "none" and statuses) or (wanted != "none" and wanted not in statuses)):
            continue
        review = _review_of(s)
        result.append(
            {
                "session_id": s.id,
                "status": s.status,
                "difficulty": s.difficulty,
                "level": s.level or "basic",
                "practice_type": s.practice_type,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                "turn_count": _count_user_turns(db, s.id),
                "goal_status": review.get("goal_status") or _summary(db, s)["goal_status"],
                "end_reason": s.end_reason,
                "scene_id": s.scene_id,
                "scene_label": SCENARIOS.get(s.scene_id, {}).get("label", ""),
                "review_status": (
                    "disputed" if "disputed" in statuses else
                    "awaiting_retest" if "awaiting_retest" in statuses else
                    "pending" if "pending" in statuses else
                    "mastered" if "mastered" in statuses else
                    "invalid" if "invalid" in statuses else "none"
                ),
            }
        )
    return result


def _collect_and_delete_session(db: DbSession, session: Session) -> set[str]:
    """删除会话及其关联数据（记忆/评估/事件/回合级联），返回需删除的音频文件名。"""
    from app.models.entities import DailyReviewAttempt, DrillAttempt, IntentResult, PhraseCard, ProviderUsageEvent, QualityLabel, ReviewDueEvent, ReviewQueueItem

    memory_svc.delete_by_session(db, session.id)
    qa_recording_svc.remove_session(db, session.id)
    db.query(Evaluation).filter(Evaluation.session_id == session.id).delete()
    db.query(EventLog).filter(EventLog.session_id == session.id).delete()
    db.query(DailyReviewAttempt).filter(DailyReviewAttempt.session_id == session.id).delete()
    db.query(DrillAttempt).filter(DrillAttempt.drill_session_id == session.id).delete()
    db.query(QualityLabel).filter(QualityLabel.session_id == session.id).delete()
    db.query(ProviderUsageEvent).filter(ProviderUsageEvent.session_id == session.id).delete()
    if session.request_key:
        db.query(IntentResult).filter_by(scope="create_session", object_id=session.user_id, request_key=session.request_key).delete()
    db.query(IntentResult).filter(IntentResult.object_id == session.id, IntentResult.scope.in_(["drill_attempt", "daily_attempt", "practice", "drill_voice_asr", "drill_voice_result", "daily_voice_asr", "daily_voice_result"])).delete()
    audio_filenames: set[str] = set()
    for risk in db.query(AsrRiskRecord).filter(AsrRiskRecord.session_id == session.id).all():
        if risk.recording_url:
            audio_filenames.add(os.path.basename(risk.recording_url))
        db.delete(risk)
    if session.pending_risk:
        try:
            pending_url = json.loads(session.pending_risk).get("recording_url", "")
            if pending_url:
                audio_filenames.add(os.path.basename(pending_url))
        except (ValueError, TypeError, AttributeError):
            pass
    for t in db.query(Turn).filter(Turn.session_id == session.id).all():
        if t.reply_audio_url:
            audio_filenames.add(os.path.basename(t.reply_audio_url))
        if t.recording_url:
            audio_filenames.add(os.path.basename(t.recording_url))
        if t.input_type in ("voice", "text"):
            _invalidate_derived_turn(db, session.user_id, t.id)
    # 删除来源时移除只有该来源支撑的派生项；用户在后续练习中自己提交的回答保留。
    for item in db.query(ReviewQueueItem).filter(ReviewQueueItem.source_session_id == session.id).all():
        db.query(IntentResult).filter(IntentResult.object_id == str(item.id), IntentResult.scope.in_(["queue_retest", "queue_voice_asr", "queue_voice_result"])).delete()
        db.query(ReviewDueEvent).filter_by(queue_item_id=item.id).delete()
        db.delete(item)
    for card in db.query(PhraseCard).filter(PhraseCard.source_session_id == session.id).all():
        db.delete(card)
    for attempt in db.query(DrillAttempt).filter(DrillAttempt.source_session_id == session.id).all():
        attempt.visible_help = ""
    for sp in (
        db.query(Session)
        .filter(Session.practice_type == "sentence_practice", Session.source_session_id == session.id)
        .all()
    ):
        sp.source_invalid = True
        sp.practice_sentence = ""
        sp.practice_goal = ""
        sp.drill_target = ""
        sp.drill_meaning = ""
        sp.drill_chunk = ""
        sp.current_question_text = ""
        for turn in db.query(Turn).filter(Turn.session_id == sp.id).all():
            if turn.action_type == "suggest_practice" and turn.practice_sentence:
                turn.reply_text = ""
                if turn.reply_audio_url:
                    audio_filenames.add(os.path.basename(turn.reply_audio_url))
                    turn.reply_audio_url = ""
            turn.practice_sentence = ""
    db.delete(session)  # ORM 级联删除 turns
    return audio_filenames


def _delete_audio_files(filenames: set[str]) -> None:
    audio_dir = settings.data_dir / "audio"
    for fname in filenames:
        if not fname:
            continue
        p = audio_dir / fname
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            continue


def delete_session(db: DbSession, session_id: str, user_id: str) -> None:
    session = _require_session(db, session_id, user_id)
    files = _collect_and_delete_session(db, session)
    db.commit()
    _delete_audio_files(files)


def set_paused(db: DbSession, session_id: str, user_id: str, paused: bool) -> dict[str, Any]:
    session = _require_session(db, session_id, user_id)
    if paused:
        if session.status == S_ENDED:
            raise AppError("SESSION_ENDED", "会话已结束", status_code=409)
        if session.status != S_PAUSED:
            session.active_seconds = _active_elapsed_seconds(session)
            session.active_since = None
            session.status = S_PAUSED
    else:
        if session.status == S_PAUSED:
            session.active_since = _now()
            session.status = S_WAITING
    db.commit()
    return {"session_id": session_id, "status": session.status}


def skip_practice(db: DbSession, session_id: str, user_id: str) -> dict[str, Any]:
    session = _require_session(db, session_id, user_id)
    if session.status != S_PRACTICE:
        raise AppError("INVALID_STATE", "当前状态不能跳过跟读", status_code=409)
    guard = BudgetGuard(db)
    guard.reserve(settings.nominal_cost_per_turn, session_id, user_id)
    audio_url = ""
    reset_usage_attempts()
    try:
        coach = get_coach_provider()
        decision = coach.decide({"skipped_practice": True})
        tts = get_tts_provider()
        audio_url, _, tts_attempts = _synth_and_save(tts, decision.reply_text)
        session.status = S_WAITING
        coach_turn_id = uuid.uuid4().hex
        db.add(
            Turn(
                id=coach_turn_id,
                session_id=session_id,
                seq=_next_seq(db, session_id),
                input_type="coach",
                reply_text=decision.reply_text,
                reply_audio_url=audio_url,
                action_type=decision.action_type,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        guard.release(settings.nominal_cost_per_turn, session_id, user_id)
        if audio_url:
            _delete_audio_files({os.path.basename(audio_url)})
        _record_failed_llm_usage(db, user_id, session_id)
        raise
    guard.settle(settings.nominal_cost_per_turn, session_id, user_id)
    _record_generated_audio_usage(db, user_id, session_id, coach_turn_id, decision.reply_text, tts_attempts, bool(audio_url))
    return {"session_id": session_id, "status": S_WAITING, "reply_text": decision.reply_text, "reply_audio_url": audio_url}


def delete_all_sessions(db: DbSession, user_id: str) -> None:
    from app.models.entities import IntentResult
    sessions = db.query(Session).filter(Session.user_id == user_id).all()
    files: set[str] = set()
    for s in sessions:
        files |= _collect_and_delete_session(db, s)
    db.query(IntentResult).filter_by(user_id=user_id, scope="daily_start").delete()
    db.commit()
    _delete_audio_files(files)
    cleanup_old_uploads()


def request_hint(db: DbSession, session_id: str, user_id: str) -> dict[str, Any]:
    session = _require_session(db, session_id, user_id)
    if session.status == S_ENDED:
        raise AppError("SESSION_ENDED", "会话已结束", status_code=409)
    guard = BudgetGuard(db)
    guard.reserve(settings.nominal_cost_per_turn, session_id, user_id)
    audio_url = ""
    reset_usage_attempts()
    try:
        coach = get_coach_provider()
        decision = coach.decide({"help_requested": True})
        tts = get_tts_provider()
        audio_url, _, tts_attempts = _synth_and_save(tts, decision.reply_text)
        coach_turn_id = uuid.uuid4().hex
        db.add(
            Turn(
                id=coach_turn_id,
                session_id=session_id,
                seq=_next_seq(db, session_id),
                input_type="coach",
                reply_text=decision.reply_text,
                reply_audio_url=audio_url,
                action_type=decision.action_type,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        guard.release(settings.nominal_cost_per_turn, session_id, user_id)
        if audio_url:
            _delete_audio_files({os.path.basename(audio_url)})
        _record_failed_llm_usage(db, user_id, session_id)
        raise
    guard.settle(settings.nominal_cost_per_turn, session_id, user_id)
    _record_generated_audio_usage(db, user_id, session_id, coach_turn_id, decision.reply_text, tts_attempts, bool(audio_url))
    return {"session_id": session_id, "reply_text": decision.reply_text, "reply_audio_url": audio_url, "action_type": decision.action_type}


def _invalidate_derived_turn(db: DbSession, user_id: str, turn_id: str) -> None:
    """修订或争议后，撤销该回合派生的学习材料与进行中的练习。"""
    from app.services import phrase_card_svc, review_queue
    from app.models.entities import ReviewQueueItem

    phrase_card_svc.invalidate_by_turn(db, turn_id)
    review_queue.invalidate_by_turn(db, turn_id)
    invalid_ids = {
        item.id for item in db.query(ReviewQueueItem).filter(ReviewQueueItem.source_turn_id == turn_id).all()
    }
    if invalid_ids:
        for daily in db.query(Session).filter(
            Session.user_id == user_id, Session.practice_type == "daily_review", Session.status != S_ENDED
        ).all():
            if invalid_ids.intersection(json.loads(daily.daily_review_items or "[]")):
                daily.status = S_ENDED
                daily.end_reason = "source_invalid"
    for sp in db.query(Session).filter(
        Session.practice_type == "sentence_practice", Session.source_turn_id == turn_id
    ).all():
        sp.source_invalid = True
        sp.practice_sentence = ""
        sp.drill_target = ""
        sp.drill_meaning = ""


def dispute_review_improvement(db: DbSession, session_id: str, user_id: str, turn_id: str) -> dict[str, Any]:
    session = _require_session(db, session_id, user_id)
    review = json.loads(session.review) if session.review else {}
    found = False
    for imp in review.get("improvements", []):
        if imp.get("turn_id") == turn_id:
            imp["disputed"] = True
            found = True
    if not found:
        raise AppError("NOT_FOUND", "改进建议不存在", status_code=404)
    session.review = json.dumps(review, ensure_ascii=False)
    _invalidate_derived_turn(db, user_id, turn_id)
    db.commit()
    return {"disputed": True}


def revise_turn(db: DbSession, session_id: str, user_id: str, turn_id: str, revised_text: str) -> dict[str, Any]:
    session = _require_session(db, session_id, user_id)
    turn = db.get(Turn, turn_id)
    if turn is None or turn.session_id != session_id:
        raise AppError("TURN_NOT_FOUND", "回合不存在", status_code=404)
    if turn.input_type not in ("voice", "text"):
        raise AppError("INVALID_STATE", "该回合不能修订", status_code=409)
    if not revised_text or not revised_text.strip():
        raise AppError("INVALID_INPUT", "修订文本不能为空", status_code=422)
    turn.revised_text = revised_text.strip()
    turn.revision_version = (turn.revision_version or 0) + 1
    if turn.eval_result:
        turn.eval_result = "revised"  # 旧评价失效，待重算
    # 撤销相关评估与子目标、标回顾待重算（F-06）
    db.query(Evaluation).filter(Evaluation.turn_id == turn_id).update({"result": "revised"})
    subgoals = _load_subgoals(session)
    changed = False
    for sg in subgoals.values():
        if turn_id in sg.get("evidence", []):
            sg["status"] = "revised"
            sg["evidence"] = []
            changed = True
    if changed:
        session.subgoals = json.dumps(subgoals, ensure_ascii=False)
    if session.review:
        try:
            review = json.loads(session.review)
            review["stale"] = True
            session.review = json.dumps(review, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    # 修订使旧文本的派生学习材料失效；用户后续可基于新评价重新生成。
    _invalidate_derived_turn(db, user_id, turn_id)
    # 记忆当前按会话而非回合追溯；修订时保守撤销本会话的候选记忆。
    memory_svc.delete_by_session(db, session_id)
    db.commit()
    return {"turn_id": turn_id, "revised_text": turn.revised_text, "revision_version": turn.revision_version}


def resolve_audio_for_user(db: DbSession, filename: str, user_id: str) -> Path:
    """校验音频归属：找到引用该音频的回合→会话→用户，归属不一致拒绝。"""
    safe_name = os.path.basename(filename)
    if safe_name != filename or not safe_name:
        raise AppError("BAD_FILENAME", "非法的文件名", status_code=400)
    url = f"/api/v1/audio/{safe_name}"
    # 教练合成音频（reply_audio_url）与本人跟读录音（recording_url）都指向同一音频目录，均需归属校验。
    turn = db.query(Turn).filter(or_(Turn.reply_audio_url == url, Turn.recording_url == url)).first()
    if turn is None:
        # 待确认可疑转写保存的本人录音（REQ-32），无 Turn 记录，从 pending_risk 校验
        for s in db.query(Session).filter(Session.pending_risk != "").all():
            try:
                pr = json.loads(s.pending_risk)
            except (ValueError, TypeError):
                continue
            if isinstance(pr, dict) and pr.get("recording_url") == url:
                if s.user_id != user_id:
                    raise AppError("FORBIDDEN", "无权访问该音频", status_code=403)
                session = s
                break
        else:
            raise AppError("NOT_FOUND", "音频不存在", status_code=404)
    else:
        session = db.get(Session, turn.session_id)
        if session is None or session.user_id != user_id:
            raise AppError("FORBIDDEN", "无权访问该音频", status_code=403)
    audio_dir = settings.data_dir / "audio"
    path = (audio_dir / safe_name).resolve()
    if not path.is_file():
        raise AppError("NOT_FOUND", "音频不存在", status_code=404)
    return path
