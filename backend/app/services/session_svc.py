"""会话生命周期与状态机（Harness 维护状态，模型不可改写）。"""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session as DbSession

from app.core.config import settings
from app.core.errors import AppError
from app.models.entities import Evaluation, EventLog, Session, Turn, User
from app.schemas.coach import CoachDecision
from app.services import memory as memory_svc
from app.services.budget import BudgetGuard, ToolBudget
from app.services.cleanup import cleanup_old_uploads
from app.services.coach import get_coach_provider
from app.services.evaluator import get_evaluator_provider
from app.services.practice import get_practice_eval_provider
from app.services.asr import get_asr_provider
from app.services.review import build_review, dedupe_improvements
from app.services.tts import get_tts_provider
from app.services.turn import _validate_audio

S_PREPARING = "PREPARING"
S_WAITING = "WAITING_INPUT"
S_PROCESSING = "PROCESSING"
S_PRACTICE = "PRACTICE"
S_RECHECK = "RECHECK"
S_PAUSED = "PAUSED"
S_ENDED = "ENDED"

GOAL_TEXT = "Introduce one experience and answer one related follow-up question."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _save_audio(audio_bytes: bytes, ext: str) -> str:
    name = f"{uuid.uuid4().hex}.{ext}"
    audio_dir = settings.data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / name).write_bytes(audio_bytes)
    return f"/api/v1/audio/{name}"


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


def _last_help_type(db: DbSession, session_id: str) -> str:
    last = (
        db.query(Turn)
        .filter(Turn.session_id == session_id, Turn.help_type != "")
        .order_by(Turn.seq.desc())
        .first()
    )
    return last.help_type if last else ""


def _has_practice(db: DbSession, session_id: str) -> bool:
    return (
        db.query(Turn)
        .filter(Turn.session_id == session_id, Turn.input_type == "practice")
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
    duration_ms: int = 0,
    usage: str = "",
) -> None:
    db.add(
        EventLog(
            session_id=session_id,
            turn_id=turn_id,
            action_type=action_type,
            basis=basis,
            input_type=input_type,
            tool_result=tool_result,
            error=error,
            duration_ms=duration_ms,
            usage=usage,
        )
    )


def _load_subgoals(session: Session) -> dict[str, Any]:
    try:
        subgoals = json.loads(session.subgoals) if session.subgoals else {}
    except json.JSONDecodeError:
        subgoals = {}
    if not isinstance(subgoals, dict):
        subgoals = {}
    subgoals.setdefault("intro", {"status": "", "evidence": []})
    subgoals.setdefault("followup", {"status": "", "evidence": []})
    return subgoals


def _current_subgoal(subgoals: dict[str, Any]) -> str:
    if subgoals.get("intro", {}).get("status") == "independent":
        return "followup"
    return "intro"


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


def create_session(db: DbSession, user_id: str, level: str, difficulty: str, duration_minutes: float = 5.0) -> dict[str, Any]:
    _get_or_create_user(db, user_id, level)
    session_id = uuid.uuid4().hex
    guard = BudgetGuard(db)
    guard.reserve(settings.nominal_cost_per_turn, session_id, user_id)

    session = Session(
        id=session_id,
        user_id=user_id,
        status=S_PREPARING,
        goal_text=GOAL_TEXT,
        difficulty=difficulty,
        subgoals="{}",
        duration_minutes=duration_minutes,
        rechecked=False,
    )
    db.add(session)

    coach = get_coach_provider()
    decision = coach.opening(GOAL_TEXT, difficulty)
    tts = get_tts_provider()
    audio = tts.synthesize(decision.reply_text)
    audio_url = _save_audio(audio, tts.ext)
    session.status = S_WAITING
    db.add(
        Turn(
            id=uuid.uuid4().hex,
            session_id=session_id,
            seq=0,
            input_type="coach",
            reply_text=decision.reply_text,
            reply_audio_url=audio_url,
            action_type="follow_up",
        )
    )
    db.commit()
    guard.settle(settings.nominal_cost_per_turn, session_id, user_id)

    return {
        "session_id": session_id,
        "status": session.status,
        "goal_text": GOAL_TEXT,
        "reply_text": decision.reply_text,
        "reply_audio_url": audio_url,
    }


def submit_turn(
    db: DbSession,
    session_id: str,
    user_id: str,
    input_type: str,
    user_text: str | None = None,
    audio_bytes: bytes | None = None,
) -> dict[str, Any]:
    session = _require_session(db, session_id, user_id)
    if session.status == S_ENDED:
        raise AppError("SESSION_ENDED", "会话已结束", status_code=409)
    if session.status == S_PAUSED:
        raise AppError("SESSION_PAUSED", "会话已暂停，请先恢复", status_code=409)
    if session.status not in (S_WAITING, S_PRACTICE, S_RECHECK):
        raise AppError("INVALID_STATE", "当前状态不能提交回合", status_code=409)

    # 时长校验（F-07A）：墙钟近似；精确累计非暂停时长由部署阶段定时任务接入
    if session.started_at:
        started = session.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed_min = (datetime.now(timezone.utc) - started).total_seconds() / 60
        if elapsed_min > session.duration_minutes:
            session.status = S_ENDED
            session.end_reason = "time_limit"
            session.ended_at = _now()
            db.commit()
            finalize_session(db, session)
            raise AppError("TIME_LIMIT", "练习时间已到，本次练习结束", status_code=409)

    guard = BudgetGuard(db)
    guard.reserve(settings.nominal_cost_per_turn, session_id, user_id)
    tool_budget = ToolBudget()

    try:
        turn_id = uuid.uuid4().hex
        transcript = ""
        if input_type == "voice":
            if not audio_bytes:
                raise AppError("EMPTY_AUDIO", "音频内容为空")
            fmt = _validate_audio(audio_bytes)
            uploads_dir = settings.data_dir / "uploads"
            uploads_dir.mkdir(parents=True, exist_ok=True)
            audio_path = uploads_dir / f"{turn_id}.{fmt}"
            audio_path.write_bytes(audio_bytes)
            transcript = get_asr_provider().transcribe(audio_path)
            text_for_eval = transcript
        else:
            text_for_eval = user_text or ""

        hint_used = _last_action_type(db, session_id) == "hint" or _last_help_type(db, session_id) == "organize"

        # 当前子目标 + 题目
        subgoals = _load_subgoals(session)
        subgoal = _current_subgoal(subgoals)
        question = _last_coach_text(db, session_id)

        # 独立评估（与生成分离）
        tool_budget.consume()
        evaluator = get_evaluator_provider()
        eval_out = evaluator.evaluate(
            {
                "question": question,
                "answer": text_for_eval,
                "hint_used": hint_used,
                "input_type": input_type,
                "goal": session.goal_text,
            }
        )

        # 更新子目标（先算到局部变量，提交前统一应用）
        new_subgoals = dict(subgoals)
        if eval_out.result == "independent":
            new_subgoals[subgoal] = {"status": "independent", "evidence": [turn_id]}
        else:
            new_subgoals[subgoal] = {"status": eval_out.result, "evidence": []}

        # 教练决策（丰富上下文）
        tool_budget.consume()
        coach = get_coach_provider()
        user = db.get(User, session.user_id)
        memories = []
        if memory_svc.is_memory_enabled(db, user_id):
            memories = [m.text for m in memory_svc.get_usable_memories(db, user_id)]
        decision = coach.decide(
            {
                "goal": session.goal_text,
                "difficulty": session.difficulty,
                "level": user.level if user else "basic",
                "current_question": question,
                "current_answer": text_for_eval,
                "input_type": input_type,
                "hint_used": hint_used,
                "eval_result": eval_out.result,
                "subgoal": subgoal,
                "history": _recent_history(db, session_id),
                "rechecked": session.rechecked,
                "memories": memories,
            }
        )

        tts = get_tts_provider()
        if decision.action_type == "suggest_practice" and decision.practice_sentence:
            audio = tts.synthesize(decision.practice_sentence)
        else:
            audio = tts.synthesize(decision.reply_text)
        audio_url = _save_audio(audio, tts.ext)
    except Exception:
        db.rollback()
        guard.release(settings.nominal_cost_per_turn, session_id, user_id)
        raise

    # 状态迁移（先算到局部变量）
    both_done = (
        new_subgoals.get("intro", {}).get("status") == "independent"
        and new_subgoals.get("followup", {}).get("status") == "independent"
    )
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

    turn = Turn(
        id=turn_id,
        session_id=session_id,
        seq=_next_seq(db, session_id),
        input_type=input_type,
        transcript=transcript,
        user_text=text_for_eval,
        reply_text=decision.reply_text,
        reply_audio_url=audio_url,
        action_type=decision.action_type,
        hint_used=hint_used,
        practice_sentence=decision.practice_sentence,
        eval_result=eval_out.result,
    )
    db.add(turn)
    _log_event(db, session_id, turn_id=turn_id, action_type=decision.action_type, basis=eval_out.result, input_type=input_type, tool_result=eval_out.result)
    db.commit()
    guard.settle(settings.nominal_cost_per_turn, session_id, user_id)

    # 统一收尾（自动达标时）
    if new_status == S_ENDED:
        finalize_session(db, session)

    return {
        "turn_id": turn_id,
        "transcript": transcript,
        "reply_text": decision.reply_text,
        "reply_audio_url": audio_url,
        "action_type": decision.action_type,
        "eval_result": eval_out.result,
        "status": new_status,
        "hint_used": hint_used,
        "practice_sentence": decision.practice_sentence,
    }


def submit_practice(
    db: DbSession, session_id: str, user_id: str, audio_bytes: bytes, sentence: str
) -> dict[str, Any]:
    session = _require_session(db, session_id, user_id)
    if session.status != S_PRACTICE:
        raise AppError("INVALID_STATE", "当前状态不能提交跟读", status_code=409)

    guard = BudgetGuard(db)
    guard.reserve(settings.nominal_cost_per_turn, session_id, user_id)

    fmt = _validate_audio(audio_bytes)
    uploads_dir = settings.data_dir / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    audio_path = uploads_dir / f"{uuid.uuid4().hex}.{fmt}"
    audio_path.write_bytes(audio_bytes)

    provider = get_practice_eval_provider()
    eval_result = provider.evaluate(audio_path, sentence)

    score = eval_result.get("score")
    valid = score is not None

    coach = get_coach_provider()
    decision = coach.decide(
        {
            "goal": session.goal_text,
            "difficulty": session.difficulty,
            "subgoal": _current_subgoal(_load_subgoals(session)),
            "practice": {
                "score": score,
                "valid": valid,
                "reference_sentence": sentence,
            },
            "history": _recent_history(db, session_id),
        }
    )
    tts = get_tts_provider()
    audio = tts.synthesize(decision.reply_text)
    audio_url = _save_audio(audio, tts.ext)

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
    )
    db.add(turn)

    # 状态迁移
    new_status = S_WAITING
    if decision.action_type == "suggest_practice":
        new_status = S_PRACTICE
    elif decision.action_type == "recheck":
        session.rechecked = True
        new_status = S_RECHECK
    session.status = new_status
    db.commit()
    guard.settle(settings.nominal_cost_per_turn, session_id, user_id)

    return {
        "session_id": session_id,
        "status": new_status,
        "eval": eval_result,
        "recheck_reply_text": decision.reply_text,
        "recheck_audio_url": audio_url,
    }


def _review_of(session: Session) -> dict[str, Any]:
    if session.review:
        try:
            return dedupe_improvements(json.loads(session.review))
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
    if session.end_reason == "goal_reached":
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
    return {
        "session_id": session.id,
        "status": session.status,
        "goal_text": session.goal_text,
        "difficulty": session.difficulty,
        "end_reason": session.end_reason,
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
                "hint_used": t.hint_used,
                "eval_result": t.eval_result,
                "revised_text": t.revised_text,
            }
            for t in turns
        ],
    }


def _count_user_turns(db: DbSession, session_id: str) -> int:
    return (
        db.query(Turn)
        .filter(Turn.session_id == session_id, Turn.input_type.in_(("voice", "text")))
        .count()
    )


def list_sessions(db: DbSession, user_id: str) -> list[dict[str, Any]]:
    sessions = (
        db.query(Session)
        .filter(Session.user_id == user_id)
        .order_by(Session.started_at.desc())
        .all()
    )
    result = []
    for s in sessions:
        review = _review_of(s)
        result.append(
            {
                "session_id": s.id,
                "status": s.status,
                "difficulty": s.difficulty,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                "turn_count": _count_user_turns(db, s.id),
                "goal_status": review.get("goal_status") or _summary(db, s)["goal_status"],
                "end_reason": s.end_reason,
            }
        )
    return result


def _collect_and_delete_session(db: DbSession, session: Session) -> set[str]:
    """删除会话及其关联数据（记忆/评估/事件/回合级联），返回需删除的音频文件名。"""
    memory_svc.delete_by_session(db, session.id)
    db.query(Evaluation).filter(Evaluation.session_id == session.id).delete()
    db.query(EventLog).filter(EventLog.session_id == session.id).delete()
    audio_filenames: set[str] = set()
    for t in db.query(Turn).filter(Turn.session_id == session.id).all():
        if t.reply_audio_url:
            audio_filenames.add(os.path.basename(t.reply_audio_url))
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
            session.status = S_PAUSED
    else:
        if session.status == S_PAUSED:
            session.status = S_WAITING
    db.commit()
    return {"session_id": session_id, "status": session.status}


def skip_practice(db: DbSession, session_id: str, user_id: str) -> dict[str, Any]:
    session = _require_session(db, session_id, user_id)
    if session.status != S_PRACTICE:
        raise AppError("INVALID_STATE", "当前状态不能跳过跟读", status_code=409)
    guard = BudgetGuard(db)
    guard.reserve(settings.nominal_cost_per_turn, session_id, user_id)
    try:
        session.status = S_WAITING
        db.commit()
        coach = get_coach_provider()
        decision = coach.decide({"skipped_practice": True})
        tts = get_tts_provider()
        audio = tts.synthesize(decision.reply_text)
        audio_url = _save_audio(audio, tts.ext)
        db.add(
            Turn(
                id=uuid.uuid4().hex,
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
        raise
    guard.settle(settings.nominal_cost_per_turn, session_id, user_id)
    return {"session_id": session_id, "status": S_WAITING, "reply_text": decision.reply_text, "reply_audio_url": audio_url}


def delete_all_sessions(db: DbSession, user_id: str) -> None:
    sessions = db.query(Session).filter(Session.user_id == user_id).all()
    files: set[str] = set()
    for s in sessions:
        files |= _collect_and_delete_session(db, s)
    db.commit()
    _delete_audio_files(files)
    cleanup_old_uploads()


def request_hint(db: DbSession, session_id: str, user_id: str) -> dict[str, Any]:
    session = _require_session(db, session_id, user_id)
    if session.status == S_ENDED:
        raise AppError("SESSION_ENDED", "会话已结束", status_code=409)
    guard = BudgetGuard(db)
    guard.reserve(settings.nominal_cost_per_turn, session_id, user_id)
    try:
        coach = get_coach_provider()
        decision = coach.decide({"help_requested": True})
        tts = get_tts_provider()
        audio = tts.synthesize(decision.reply_text)
        audio_url = _save_audio(audio, tts.ext)
        db.add(
            Turn(
                id=uuid.uuid4().hex,
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
        raise
    guard.settle(settings.nominal_cost_per_turn, session_id, user_id)
    return {"session_id": session_id, "reply_text": decision.reply_text, "reply_audio_url": audio_url, "action_type": decision.action_type}


def request_help(
    db: DbSession, session_id: str, user_id: str, help_type: str, help_level: int = 0
) -> dict[str, Any]:
    session = _require_session(db, session_id, user_id)
    if session.status == S_ENDED:
        raise AppError("SESSION_ENDED", "会话已结束", status_code=409)
    if session.status == S_PAUSED:
        raise AppError("SESSION_PAUSED", "会话已暂停", status_code=409)
    guard = BudgetGuard(db)
    guard.reserve(settings.nominal_cost_per_turn, session_id, user_id)
    try:
        coach = get_coach_provider()
        question = _last_coach_text(db, session_id)
        decision = coach.help(
            {"help_type": help_type, "help_level": help_level, "current_question": question}
        )
        tts = get_tts_provider()
        audio = tts.synthesize(decision.reply_text)
        audio_url = _save_audio(audio, tts.ext)
        db.add(
            Turn(
                id=uuid.uuid4().hex,
                session_id=session_id,
                seq=_next_seq(db, session_id),
                input_type="coach",
                reply_text=decision.reply_text,
                reply_audio_url=audio_url,
                action_type="help",
                help_type=help_type,
                help_level=help_level,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        guard.release(settings.nominal_cost_per_turn, session_id, user_id)
        raise
    guard.settle(settings.nominal_cost_per_turn, session_id, user_id)
    return {
        "session_id": session_id,
        "reply_text": decision.reply_text,
        "reply_audio_url": audio_url,
        "action_type": "help",
        "help_type": help_type,
        "help_level": help_level,
    }


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
    for name in ("intro", "followup"):
        sg = subgoals.get(name, {})
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
    db.commit()
    return {"turn_id": turn_id, "revised_text": turn.revised_text, "revision_version": turn.revision_version}


def resolve_audio_for_user(db: DbSession, filename: str, user_id: str) -> Path:
    """校验音频归属：找到引用该音频的回合→会话→用户，归属不一致拒绝。"""
    safe_name = os.path.basename(filename)
    if safe_name != filename or not safe_name:
        raise AppError("BAD_FILENAME", "非法的文件名", status_code=400)
    url = f"/api/v1/audio/{safe_name}"
    turn = db.query(Turn).filter(Turn.reply_audio_url == url).first()
    if turn is None:
        raise AppError("NOT_FOUND", "音频不存在", status_code=404)
    session = db.get(Session, turn.session_id)
    if session is None or session.user_id != user_id:
        raise AppError("FORBIDDEN", "无权访问该音频", status_code=403)
    audio_dir = settings.data_dir / "audio"
    path = (audio_dir / safe_name).resolve()
    if not path.is_file():
        raise AppError("NOT_FOUND", "音频不存在", status_code=404)
    return path
