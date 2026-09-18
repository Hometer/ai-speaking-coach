"""语音识别（ASR）provider：抽象 + mock + 阿里云百炼（真实接入待验）。"""
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import settings
from app.core.errors import AppError


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
    """阿里云百炼 paraformer 录音文件识别。真实接入方式待冒烟验证。"""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def transcribe(self, audio_path: Path) -> str:
        # 待验：paraformer-v2 录音文件识别通常需要文件公网 URL（或 OSS 中转），
        # 本地直传的接入方式在真实冒烟时验证并固定。此处如实报"待验"。
        raise AppError(
            "ASR_NOT_READY",
            "语音识别真实接入待验：请先以 mock 模式验收，提供 Key 后完成真实冒烟再启用。",
            status_code=501,
        )


def get_asr_provider() -> ASRProvider:
    if settings.provider_mode == "mock":
        return MockASRProvider()
    if settings.provider_mode == "real":
        if not settings.dashscope_api_key:
            raise AppError("MISSING_API_KEY", "未配置 DASHSCOPE_API_KEY", status_code=500)
        return DashScopeASRProvider(settings.dashscope_api_key, settings.model_asr)
    raise AppError("CONFIG_ERROR", "未知的 PROVIDER_MODE，应为 mock 或 real", status_code=500)
