"""短句跟读评测：示范 + 用户录音 + 评测（mock 固定结果 + 讯飞待验）。"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.errors import AppError


class PracticeEvalProvider(ABC):
    @abstractmethod
    def evaluate(self, audio_path: Path, reference_text: str) -> dict[str, Any]:
        """返回评测结果（服务支持范围内的维度）。"""


class MockPracticeEvalProvider(PracticeEvalProvider):
    def evaluate(self, audio_path: Path, reference_text: str) -> dict[str, Any]:
        return {
            "score": 85,
            "dimension": "word/phrase",
            "notes": [{"word": "experience", "suggestion": "stress the second syllable"}],
            "mock": True,
        }


class XfyunPracticeEvalProvider(PracticeEvalProvider):
    """科大讯飞英文短句评测。真实接入待验。"""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def evaluate(self, audio_path: Path, reference_text: str) -> dict[str, Any]:
        raise AppError(
            "PRACTICE_EVAL_NOT_READY",
            "发音评测真实接入待验：请先以 mock 模式验收，提供讯飞 Key 后完成真实冒烟再启用。",
            status_code=501,
        )


def get_practice_eval_provider() -> PracticeEvalProvider:
    if settings.provider_mode == "mock":
        return MockPracticeEvalProvider()
    if settings.provider_mode == "real":
        # 讯飞 Key 单独配置，本阶段未接入，如实报待验
        return XfyunPracticeEvalProvider(settings.dashscope_api_key)
    raise AppError("CONFIG_ERROR", "未知的 PROVIDER_MODE，应为 mock 或 real", status_code=500)
