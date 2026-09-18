"""语音合成（TTS）provider：抽象 + mock（生成静音 wav）+ 阿里云百炼。"""
import io
import wave
from abc import ABC, abstractmethod

from app.core.config import settings
from app.core.errors import AppError


class TTSProvider(ABC):
    ext: str = "mp3"  # 输出音频后缀

    @abstractmethod
    def synthesize(self, text: str) -> bytes:
        """把回复文本合成为音频字节。"""


def _silent_wav(duration: float = 0.4, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * duration))
    return buf.getvalue()


class MockTTSProvider(TTSProvider):
    ext = "wav"

    def synthesize(self, text: str) -> bytes:
        return _silent_wav()


class DashScopeTTSProvider(TTSProvider):
    ext = "mp3"

    def __init__(self, api_key: str, model: str, voice: str):
        self.api_key = api_key
        self.model = model
        self.voice = voice

    def synthesize(self, text: str) -> bytes:
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer

        dashscope.api_key = self.api_key
        try:
            synthesizer = SpeechSynthesizer(
                model=self.model,
                voice=self.voice,
                language_hints=["en"],
            )
            audio = synthesizer.call(text)
        except Exception as exc:  # noqa: BLE001
            raise AppError("TTS_ERROR", f"语音合成失败：{str(exc)[:120]}", status_code=502)
        if not audio:
            raise AppError("TTS_ERROR", "语音合成为空", status_code=502)
        return audio


def get_tts_provider() -> TTSProvider:
    if settings.provider_mode == "mock":
        return MockTTSProvider()
    if settings.provider_mode == "real":
        if not settings.dashscope_api_key:
            raise AppError("MISSING_API_KEY", "未配置 DASHSCOPE_API_KEY", status_code=500)
        return DashScopeTTSProvider(settings.dashscope_api_key, settings.model_tts, settings.tts_voice)
    raise AppError("CONFIG_ERROR", "未知的 PROVIDER_MODE，应为 mock 或 real", status_code=500)
