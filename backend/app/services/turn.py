"""回合编排：上传音频/文字 -> 转写 -> 生成回复 -> 合成音频。"""
import os
import uuid
from pathlib import Path

from app.core.config import settings
from app.core.errors import AppError
from app.schemas.turn import TurnResponse
from app.services.asr import get_asr_provider
from app.services.llm import get_llm_provider
from app.services.tts import get_tts_provider

# 允许的音频真实格式（通过文件头魔数识别）
_ALLOWED_FORMATS = {"webm", "wav", "mp3", "mp4", "ogg"}


def sniff_audio_type(header: bytes) -> str | None:
    """通过文件头魔数识别音频真实类型，返回规范化格式名。"""
    if len(header) < 12:
        return None
    if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "wav"
    if header[:4] == b"\x1aE\xdf\xa3":
        return "webm"
    if header[:4] == b"OggS":
        return "ogg"
    if header[4:8] == b"ftyp":
        return "mp4"  # mp4 / m4a
    if header[:3] == b"ID3":
        return "mp3"
    if header[0] == 0xFF and (header[1] & 0xE0) == 0xE0:
        return "mp3"
    return None


def _validate_audio(audio_bytes: bytes) -> str:
    """校验大小与真实类型，返回规范化扩展名。"""
    if not audio_bytes:
        raise AppError("EMPTY_AUDIO", "音频内容为空")
    if len(audio_bytes) > settings.max_audio_mb * 1024 * 1024:
        raise AppError("FILE_TOO_LARGE", f"音频超过 {settings.max_audio_mb:g}MB 上限")
    fmt = sniff_audio_type(audio_bytes)
    if fmt not in _ALLOWED_FORMATS:
        raise AppError("UNSUPPORTED_AUDIO", "无法识别的音频格式，请上传 webm/wav/mp3/mp4(m4a)/ogg")
    return fmt


def _run_turn(user_text: str | None, audio_bytes: bytes | None) -> TurnResponse:
    request_id = uuid.uuid4().hex
    transcript = ""

    if audio_bytes is not None:
        fmt = _validate_audio(audio_bytes)
        uploads_dir = settings.data_dir / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        audio_path = uploads_dir / f"{request_id}.{fmt}"
        audio_path.write_bytes(audio_bytes)
        transcript = get_asr_provider().transcribe(audio_path)
        text_for_llm = transcript
    else:
        text_for_llm = user_text or ""

    reply_text = get_llm_provider().generate(text_for_llm)
    tts = get_tts_provider()
    reply_audio = tts.synthesize(reply_text)

    audio_dir = settings.data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    reply_name = f"{request_id}.{tts.ext}"
    (audio_dir / reply_name).write_bytes(reply_audio)

    return TurnResponse(
        request_id=request_id,
        transcript=transcript,
        reply_text=reply_text,
        reply_audio_url=f"/api/v1/audio/{reply_name}",
    )


def handle_voice_turn(audio_bytes: bytes) -> TurnResponse:
    return _run_turn(user_text=None, audio_bytes=audio_bytes)


def handle_text_turn(text: str) -> TurnResponse:
    return _run_turn(user_text=text, audio_bytes=None)


def resolve_audio_path(filename: str) -> Path:
    """安全解析音频路径，防止路径穿越。"""
    safe_name = os.path.basename(filename)
    if safe_name != filename or not safe_name:
        raise AppError("BAD_FILENAME", "非法的文件名", status_code=400)
    audio_dir = settings.data_dir / "audio"
    path = (audio_dir / safe_name).resolve()
    if not str(path).startswith(str(audio_dir.resolve()) + os.sep):
        raise AppError("BAD_FILENAME", "非法的文件名", status_code=400)
    if not path.is_file():
        raise AppError("NOT_FOUND", "音频不存在", status_code=404)
    return path
