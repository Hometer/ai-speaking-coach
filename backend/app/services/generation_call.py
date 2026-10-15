"""百炼文本调用的统一超时、有限重试和安全错误出口。"""
import contextvars
import time
from typing import Any

from app.core.config import settings
from app.core.errors import AppError
from app.services.deadline import remaining, bounded

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

# 按请求上下文隔离的最近一次成功调用用量（避免并发串号）
_last_usage: "contextvars.ContextVar[dict[str, int]]" = contextvars.ContextVar(
    "llm_last_usage", default={}
)
_usage_attempts: "contextvars.ContextVar[tuple[dict[str, Any], ...]]" = contextvars.ContextVar(
    "llm_usage_attempts", default=()
)


def reset_usage_attempts() -> None:
    _last_usage.set({})
    _usage_attempts.set(())


def get_usage_attempts() -> list[dict[str, Any]]:
    return [dict(item) for item in _usage_attempts.get()]


def _append_attempt(model: str, status: str, response: Any = None) -> None:
    usage = getattr(response, "usage", None)
    _usage_attempts.set(_usage_attempts.get() + ({
        "component": "llm", "model": model, "status": status,
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "source": "provider_response" if usage is not None else "unreported",
    },))


def _retryable_exception(exc: Exception) -> bool:
    return isinstance(exc, (TimeoutError, ConnectionError)) or any(
        part in type(exc).__name__.lower() for part in ("timeout", "connection")
    )


def _record_usage(response: Any) -> None:
    u = getattr(response, "usage", None)
    if u is None:
        return
    _last_usage.set({
        "in": getattr(u, "input_tokens", 0) or 0,
        "out": getattr(u, "output_tokens", 0) or 0,
    })


def get_last_usage() -> str:
    """返回当前请求上下文最近一次成功 LLM 调用的用量摘要（空字符串表示无真实用量）。"""
    usage = _last_usage.get()
    if not usage:
        return ""
    return f"llm_in={usage.get('in', 0)}/llm_out={usage.get('out', 0)}"


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
            response = bounded(lambda: dashscope.Generation.call(
                model=model,
                messages=messages,
                result_format="message",
                api_key=api_key,
                request_timeout=remaining(settings.llm_timeout_seconds),
            ))
        except Exception as exc:
            _append_attempt(model, "exception")
            if attempt + 1 < attempts and _retryable_exception(exc):
                time.sleep(0.2 * (attempt + 1))
                continue
            raise AppError("LLM_UNAVAILABLE", "模型服务暂不可用", status_code=503) from exc
        status = getattr(response, "status_code", None)
        _append_attempt(model, "ok" if status == 200 else f"http_{status or 'unknown'}", response)
        if status == 200:
            _record_usage(response)
            return response
        if status in RETRYABLE_STATUSES and attempt + 1 < attempts:
            time.sleep(0.2 * (attempt + 1))
            continue
        raise AppError("LLM_ERROR", "模型服务调用失败", status_code=502)
    raise AppError("LLM_UNAVAILABLE", "模型服务暂不可用", status_code=503)
