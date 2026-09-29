"""百炼文本调用的统一超时、有限重试和安全错误出口。"""
import time
from typing import Any

from app.core.config import settings
from app.core.errors import AppError

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

# 最近一次成功调用的用量（请求内供事件埋点读取；mock 模式不经过此函数）
_last_usage: dict[str, int] = {}


def _retryable_exception(exc: Exception) -> bool:
    return isinstance(exc, (TimeoutError, ConnectionError)) or any(
        part in type(exc).__name__.lower() for part in ("timeout", "connection")
    )


def _record_usage(response: Any) -> None:
    global _last_usage
    u = getattr(response, "usage", None)
    if u is None:
        return
    _last_usage = {
        "in": getattr(u, "input_tokens", 0) or 0,
        "out": getattr(u, "output_tokens", 0) or 0,
    }


def get_last_usage() -> str:
    """返回最近一次成功 LLM 调用的用量摘要（空字符串表示无真实用量）。"""
    if not _last_usage:
        return ""
    return f"llm_in={_last_usage.get('in', 0)}/llm_out={_last_usage.get('out', 0)}"


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
            _record_usage(response)
            return response
        if status in RETRYABLE_STATUSES and attempt + 1 < attempts:
            time.sleep(0.2 * (attempt + 1))
            continue
        raise AppError("LLM_ERROR", "模型服务调用失败", status_code=502)
    raise AppError("LLM_UNAVAILABLE", "模型服务暂不可用", status_code=503)
