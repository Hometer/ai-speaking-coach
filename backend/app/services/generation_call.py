"""百炼文本调用的统一超时、有限重试和安全错误出口。"""
import time
from typing import Any

from app.core.config import settings
from app.core.errors import AppError

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


def _retryable_exception(exc: Exception) -> bool:
    return isinstance(exc, (TimeoutError, ConnectionError)) or any(
        part in type(exc).__name__.lower() for part in ("timeout", "connection")
    )


def call_generation(*, api_key: str, model: str, messages: list[dict[str, str]]) -> Any:
    if not api_key:
        raise AppError("MISSING_API_KEY", "未配置模型服务密钥", status_code=500)
    try:
        import dashscope
    except Exception as exc:  # SDK 初始化失败仍给统一错误结构
        raise AppError("LLM_UNAVAILABLE", "模型服务暂不可用", status_code=503) from exc

    attempts = max(1, settings.max_tool_retry + 1)
    for attempt in range(attempts):
        try:
            response = dashscope.Generation.call(
                model=model,
                messages=messages,
                result_format="message",
                api_key=api_key,
                request_timeout=settings.llm_timeout_seconds,
            )
        except Exception as exc:
            if attempt + 1 < attempts and _retryable_exception(exc):
                time.sleep(0.2 * (attempt + 1))
                continue
            raise AppError("LLM_UNAVAILABLE", "模型服务暂不可用", status_code=503) from exc
        status = getattr(response, "status_code", None)
        if status == 200:
            return response
        if status in RETRYABLE_STATUSES and attempt + 1 < attempts:
            time.sleep(0.2 * (attempt + 1))
            continue
        raise AppError("LLM_ERROR", "模型服务调用失败", status_code=502)
    raise AppError("LLM_UNAVAILABLE", "模型服务暂不可用", status_code=503)
