"""语音识别（ASR）provider：抽象 + mock + 阿里云百炼（真实接入）。"""
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import settings
from app.core.errors import AppError
from app.services.audio_util import transcode_to_wav_file


class ASRProvider(ABC):
    @abstractmethod
    def transcribe(self, audio_path: Path) -> str:
        """把音频文件转写为文本。"""


class MockASRProvider(ASRProvider):
    def transcribe(self, audio_path: Path) -> str:
        return (
            "Hello, my name is Li Ming. I have three years of experience "
            "in software development."
        )


class DashScopeASRProvider(ASRProvider):
    """阿里云百炼 paraformer-realtime-v2 本地文件识别。"""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def transcribe(self, audio_path: Path) -> str:
        import dashscope
        from dashscope.audio.asr import Recognition, RecognitionCallback

        dashscope.api_key = self.api_key
        wav_path = transcode_to_wav_file(audio_path)
        try:
            recognition = Recognition(
                model=self.model,
                callback=RecognitionCallback(),
                format="wav",
                sample_rate=16000,
            )
            result = recognition.call(str(wav_path))
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AppError("ASR_ERROR", "语音识别暂不可用，请重试或改用文字", status_code=502) from exc

        text = _extract_text(result)
        if not text:
            raise AppError("ASR_EMPTY", "未识别到内容，请重说或改用文字", status_code=422)
        return text


def _extract_text(result) -> str:
    try:
        sentence = result.get_sentence()
    except Exception:  # noqa: BLE001
        return ""
    if isinstance(sentence, list):
        parts = []
        for item in sentence:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")).strip())
            else:
                parts.append(str(item).strip())
        return " ".join(p for p in parts if p).strip()
    if isinstance(sentence, dict):
        return str(sentence.get("text", "")).strip()
    return str(sentence or "").strip()


def get_asr_provider() -> ASRProvider:
    if settings.provider_mode == "mock":
        return MockASRProvider()
    if settings.provider_mode == "real":
        if not settings.dashscope_api_key:
            raise AppError("MISSING_API_KEY", "未配置 DASHSCOPE_API_KEY", status_code=500)
        return DashScopeASRProvider(settings.dashscope_api_key, settings.model_asr)
    raise AppError("CONFIG_ERROR", "未知的 PROVIDER_MODE，应为 mock 或 real", status_code=500)
