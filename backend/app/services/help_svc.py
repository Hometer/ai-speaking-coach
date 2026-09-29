"""四类求助（REQ-05）确定性编排：repeat / explain / organize / translate。

- repeat / explain / organize 为确定性逻辑：不调用 LLM、不重新合成 TTS、不 reserve/settle 模型费用。
- translate 用独立翻译调用（专用 Prompt，有限重试），并记录为答案帮助；其余类型的答案帮助判定见 HELP_ANSWER_TYPES。
- 每条帮助绑定当前有效 question_id；organize 的层级由后端按「同题已实际展示的帮助记录」计算，不信任前端。
- 帮助按 Turn 持久化（question_id / is_answer_help / help_type / help_level / reply_text），刷新与重启可恢复。
"""
import uuid
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.core.config import settings
from app.core.errors import AppError
from app.models.entities import Session, Turn
from app.services.budget import BudgetGuard
from app.services.prompts.scenarios import SCENARIOS
from app.services.session_svc import _log_event, _next_seq, _require_session

# 答案帮助：会取消后续「无辅助独立完成」的判定
ANSWER_HELP_TYPES = ("organize", "translate")

_TRANSLATE_SYSTEM_PROMPT = (
    "You translate a Chinese sentence into simple, natural English for an English learner to practice speaking.\n"
    "Rules:\n"
    "1. Preserve the user's original meaning exactly.\n"
    "2. Do NOT add any experience, job title, achievement, number, or fact the user did not say.\n"
    "3. Use short, simple English.\n"
    "4. If the meaning is unclear, output a short clarifying question in Chinese instead.\n"
    "Output ONLY the English translation (or the Chinese clarifying question), no markdown, no quotes, no commentary."
)


def _current_question(session: Session) -> tuple[str, str, str]:
    """返回当前有效问题的 (id, 文本, 音频 url)。"""
    return (
        session.current_question_id or "",
        session.current_question_text or "",
        session.current_question_audio_url or "",
    )


def _organize_content(materials: dict, level: int) -> str:
    if level <= 0:
        return materials.get("keywords", "")
    if level == 1:
        return materials.get("half", "")
    return materials.get("full", "")


def _prior_organize_count(db: DbSession, session_id: str, question_id: str) -> int:
    return (
        db.query(Turn)
        .filter(
            Turn.session_id == session_id,
            Turn.question_id == question_id,
            Turn.help_type == "organize",
        )
        .count()
    )


def _has_answer_help(db: DbSession, session_id: str, question_id: str) -> bool:
    """当前问题是否已经给出过答案帮助（关键词/半句/完整示例/翻译）。"""
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


def _translate(source_text: str) -> str:
    if settings.provider_mode != "real":
        return "I'd like to say that."  # mock：确定性占位，不编造用户事实
    from app.services.generation_call import call_generation

    resp = call_generation(
        api_key=settings.dashscope_api_key,
        model=settings.model_llm,
        messages=[
            {"role": "system", "content": _TRANSLATE_SYSTEM_PROMPT},
            {"role": "user", "content": source_text},
        ],
    )
    try:
        content = resp.output.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise AppError("TRANSLATE_ERROR", "翻译返回格式异常", status_code=502) from exc
    if not content or not content.strip():
        raise AppError("TRANSLATE_ERROR", "翻译返回为空", status_code=502)
    return content.strip()


def _record_help(
    db: DbSession,
    session: Session,
    help_type: str,
    help_level: int,
    reply_text: str,
    reply_audio_url: str,
) -> Turn:
    qid, _, _ = _current_question(session)
    turn = Turn(
        id=uuid.uuid4().hex,
        session_id=session.id,
        seq=_next_seq(db, session.id),
        input_type="coach",
        reply_text=reply_text,
        reply_audio_url=reply_audio_url,
        action_type="help",
        help_type=help_type,
        help_level=help_level,
        question_id=qid,
        is_answer_help=help_type in ANSWER_HELP_TYPES,
    )
    db.add(turn)
    _log_event(
        db,
        session.id,
        turn_id=turn.id,
        action_type="help",
        basis=help_type,
        input_type="coach",
        tool_result=help_type,
    )
    db.commit()
    db.refresh(turn)
    return turn


def request_help(
    db: DbSession,
    session_id: str,
    user_id: str,
    help_type: str,
    source_text: str = "",
) -> dict[str, Any]:
    session = _require_session(db, session_id, user_id)
    if session.status == "ENDED":
        raise AppError("SESSION_ENDED", "会话已结束", status_code=409)
    if session.status == "PAUSED":
        raise AppError("SESSION_PAUSED", "会话已暂停，请先恢复", status_code=409)

    qid, qtext, qaudio = _current_question(session)
    if not qid:
        raise AppError("INVALID_STATE", "当前没有可求助的问题", status_code=409)
    scene = SCENARIOS.get(session.scene_id, {})
    materials = scene.get("help_materials") or {}

    if help_type == "repeat":
        # 复用已有音频，不调用 LLM / TTS，不计模型费用
        turn = _record_help(db, session, "repeat", 0, qtext, qaudio)
        return {
            "session_id": session.id,
            "help_type": "repeat",
            "help_level": 0,
            "reply_text": qtext,
            "reply_audio_url": qaudio,
            "is_answer_help": False,
            "question_id": qid,
        }

    if help_type == "explain":
        reply = materials.get("explain_zh", "请围绕刚才的问题说一句简单英文。")
        simple = materials.get("simple_question", "")
        if simple:
            reply = f"{reply}\n（英文简化：{simple}）"
        turn = _record_help(db, session, "explain", 0, reply, "")
        return {
            "session_id": session.id,
            "help_type": "explain",
            "help_level": 0,
            "reply_text": reply,
            "reply_audio_url": "",
            "is_answer_help": False,
            "question_id": qid,
        }

    if help_type == "organize":
        # 层级由后端按同题已实际展示的记录计算，不信任前端；上限 2（关键词/半句/完整）
        level = min(_prior_organize_count(db, session.id, qid), 2)
        content = _organize_content(materials, level)
        if level >= 2 and not content:
            content = materials.get("full", "")
        turn = _record_help(db, session, "organize", level, content, "")
        return {
            "session_id": session.id,
            "help_type": "organize",
            "help_level": level,
            "reply_text": content,
            "reply_audio_url": "",
            "is_answer_help": True,
            "question_id": qid,
        }

    if help_type == "translate":
        text = (source_text or "").strip()
        if not text:
            raise AppError("INVALID_INPUT", "请先输入你想表达的中文", status_code=422)
        # 幂等：同题同中文已翻译过则直接返回缓存，不重复调用/计费/记录
        existing = (
            db.query(Turn)
            .filter(
                Turn.session_id == session.id,
                Turn.question_id == qid,
                Turn.help_type == "translate",
                Turn.user_text == text,
            )
            .first()
        )
        if existing is not None:
            return {
                "session_id": session.id,
                "help_type": "translate",
                "help_level": 0,
                "reply_text": existing.reply_text,
                "reply_audio_url": "",
                "is_answer_help": True,
                "question_id": qid,
            }
        # 真实翻译调用才计费；其余三类求助不 reserve/settle
        guard = BudgetGuard(db)
        guard.reserve(settings.nominal_cost_per_turn, session.id, user_id)
        try:
            translated = _translate(text)
            turn = _record_help(db, session, "translate", 0, translated, "")
            turn.user_text = text  # 记录用户中文原意，不参与答案评估
            db.commit()
        except Exception:
            db.rollback()
            guard.release(settings.nominal_cost_per_turn, session.id, user_id)
            raise
        guard.settle(settings.nominal_cost_per_turn, session.id, user_id)
        return {
            "session_id": session.id,
            "help_type": "translate",
            "help_level": 0,
            "reply_text": translated,
            "reply_audio_url": "",
            "is_answer_help": True,
            "question_id": qid,
        }

    raise AppError("INVALID_INPUT", "未知的求助类型", status_code=422)
