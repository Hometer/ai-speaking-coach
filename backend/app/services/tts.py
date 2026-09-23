"""语音合成（TTS）provider：抽象 + mock（静音 wav）+ 阿里云百炼（有限重试）。

真实 provider 带有限重试：
- 只对连接超时、临时网络错误、空音频等可恢复错误重试，最多自动重试 1 次（符合单工具上限）。
- 鉴权失败、参数错误、额度不足等确定性错误不重试。
- 每次重试重新创建 TTS 客户端。
- 结果用 TTSResult 返回（audio / error_type / attempts），不回显正文或密钥。
"""
import io
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.config import settings
from app.core.errors import AppError


@dataclass
class TTSResult:
    audio: bytes | None = None
    error_type: str = ""  # "" 成功；"recoverable" 可恢复失败；"fatal" 确定性失败
    attempts: int = 0


def _silent_wav(duration: float = 0.4, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * duration))
    return buf.getvalue()


def _classify_error(message: str) -> str:
    """把异常信息归类为 recoverable（可重试）或 fatal（确定性错误，不重试）。"""
    low = (message or "").lower()
    fatal_markers = (
        "auth", "api key", "apikey", "invalid", "parameter", "model not", "voice not",
        "budget", "quota", "forbidden", "unauthorized", "not found", "insufficient",
    )
    recoverable_markers = (
        "timeout", "timed out", "connection", "websocket", "network", "established",
        "reset", "refused", "unreachable", "temporary", "busy", "too many", "rate limit",
    )
    for m in fatal_markers:
        if m in low:
            return "fatal"
    for m in recoverable_markers:
        if m in low:
            return "recoverable"
    return "fatal"  # 未知错误默认不重试（保守）


class TTSProvider(ABC):
    ext: str = "mp3"

    @abstractmethod
    def synthesize_result(self, text: str) -> TTSResult:
        """带有限重试的合成；失败返回 TTSResult(audio=None, error_type=...)，不抛错。"""

    def synthesize(self, text: str) -> bytes:
        """兼容旧调用：成功返回音频，失败抛 AppError。"""
        r = self.synthesize_result(text)
        if r.audio is None:
            raise AppError("TTS_ERROR", "语音合成失败", status_code=502)
        return r.audio


class MockTTSProvider(TTSProvider):
    ext = "wav"

    def synthesize_result(self, text: str) -> TTSResult:
        return TTSResult(audio=_silent_wav(), attempts=1)


class DashScopeTTSProvider(TTSProvider):
    ext = "mp3"

    def __init__(self, api_key: str, model: str, voice: str):
        self.api_key = api_key
        self.model = model
        self.voice = voice

    def _once(self, text: str) -> bytes:
        """单次合成，每次重建客户端。可恢复错误与确定性错误都以异常形式抛出，由外层分类。"""
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer

        dashscope.api_key = self.api_key
        synthesizer = SpeechSynthesizer(model=self.model, voice=self.voice, language_hints=["en"])
        audio = synthesizer.call(text)
        if not audio:
            raise AppError("TTS_EMPTY", "语音合成为空", status_code=502)
        return audio

    def synthesize_result(self, text: str) -> TTSResult:
        last_type = ""
        attempts = 0
        max_attempts = max(1, settings.max_tool_retry + 1)  # 初始 1 次 + 自动重试 1 次
        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            try:
                audio = self._once(text)
                return TTSResult(audio=audio, attempts=attempt)
            except AppError as exc:
                if exc.code == "TTS_EMPTY":
                    last_type = "recoverable"  # 空音频按可恢复错误重试
                else:
                    last_type = _classify_error(str(exc))
            except Exception as exc:  # noqa: BLE001
                last_type = _classify_error(str(exc))
            if last_type == "fatal":
                break
        return TTSResult(audio=None, error_type=last_type or "recoverable", attempts=attempts)


def get_tts_provider() -> TTSProvider:
    if settings.provider_mode == "mock":
        return MockTTSProvider()
    if settings.provider_mode == "real":
        if not settings.dashscope_api_key:
            raise AppError("MISSING_API_KEY", "未配置 DASHSCOPE_API_KEY", status_code=500)
        return DashScopeTTSProvider(settings.dashscope_api_key, settings.model_tts, settings.tts_voice)
    raise AppError("CONFIG_ERROR", "未知的 PROVIDER_MODE，应为 mock 或 real", status_code=500)
