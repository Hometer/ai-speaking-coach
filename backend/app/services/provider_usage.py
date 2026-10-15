"""记录可核对的供应商用量，不自行宣称账单金额。"""
import contextvars
from sqlalchemy.orm import Session as DbSession

_audio_seconds = contextvars.ContextVar("input_audio_seconds", default=None)

def set_audio_seconds(seconds):
    _audio_seconds.set(seconds)


from app.core.config import settings
from app.models.entities import ProviderUsageEvent
from app.services.generation_call import get_usage_attempts


def record_llm_attempts(db: DbSession, user_id: str, session_id: str, turn_id: str = "") -> None:
    if settings.provider_mode != "real":
        return
    for attempt in get_usage_attempts():
        db.add(ProviderUsageEvent(
            user_id=user_id, session_id=session_id, turn_id=turn_id,
            component="llm", model=attempt["model"], status=attempt["status"],
            source=attempt["source"], input_tokens=attempt["input_tokens"],
            output_tokens=attempt["output_tokens"],
        ))
    db.commit()


def record_asr(db: DbSession, user_id: str, session_id: str, turn_id: str, audio_size: int, succeeded: bool = True) -> None:
    if settings.provider_mode != "real":
        return
    db.add(ProviderUsageEvent(
        user_id=user_id, session_id=session_id, turn_id=turn_id,
        component="asr", model=settings.model_asr, status="ok" if succeeded else "failed",
        source="local_upload_bytes", input_bytes=audio_size, audio_seconds=_audio_seconds.get(),
    ))
    db.commit()


def record_tts(db: DbSession, user_id: str, session_id: str, turn_id: str, text: str, attempts: int, succeeded: bool) -> None:
    if settings.provider_mode != "real":
        return
    db.add(ProviderUsageEvent(
        user_id=user_id, session_id=session_id, turn_id=turn_id,
        component="tts", model=settings.model_tts,
        status="ok" if succeeded else "failed", source="local_text_chars",
        text_chars=len(text), attempts=attempts,
    ))
    db.commit()


def record_ise(db: DbSession, user_id: str, session_id: str, turn_id: str, audio_size: int, text: str, succeeded: bool) -> None:
    if settings.provider_mode != "real":
        return
    db.add(ProviderUsageEvent(
        user_id=user_id, session_id=session_id, turn_id=turn_id,
        component="ise", model="xfyun_open_ise", status="ok" if succeeded else "failed",
        source="local_upload_bytes", input_bytes=audio_size, audio_seconds=_audio_seconds.get(), text_chars=len(text),
    ))
    db.commit()
