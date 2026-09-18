"""回复生成（LLM）provider：抽象 + mock + 阿里云百炼。"""
from abc import ABC, abstractmethod

from app.core.config import settings
from app.core.errors import AppError
from app.services.prompts.coach import COACH_SYSTEM_PROMPT


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, user_text: str) -> str:
        """根据用户表达生成教练回复。"""


class MockLLMProvider(LLMProvider):
    def generate(self, user_text: str) -> str:
        return "That sounds like a great project. What was the most challenging part for you?"


class DashScopeLLMProvider(LLMProvider):
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def generate(self, user_text: str) -> str:
        import dashscope

        dashscope.api_key = self.api_key
        resp = dashscope.Generation.call(
            model=self.model,
            messages=[
                {"role": "system", "content": COACH_SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            result_format="message",
        )
        if getattr(resp, "status_code", None) == 200:
            try:
                content = resp.output.choices[0].message.content
            except (AttributeError, IndexError, TypeError):
                raise AppError("LLM_PARSE_ERROR", "模型返回格式异常", status_code=502)
            if not content or not content.strip():
                raise AppError("LLM_EMPTY", "模型返回为空", status_code=502)
            return content.strip()
        code = getattr(resp, "code", "")
        message = getattr(resp, "message", "未知错误")
        raise AppError("LLM_ERROR", f"模型调用失败：{code} {message}".strip(), status_code=502)


def get_llm_provider() -> LLMProvider:
    if settings.provider_mode == "mock":
        return MockLLMProvider()
    if settings.provider_mode == "real":
        if not settings.dashscope_api_key:
            raise AppError("MISSING_API_KEY", "未配置 DASHSCOPE_API_KEY", status_code=500)
        return DashScopeLLMProvider(settings.dashscope_api_key, settings.model_llm)
    raise AppError("CONFIG_ERROR", "未知的 PROVIDER_MODE，应为 mock 或 real", status_code=500)
