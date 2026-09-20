"""独立评估器：与生成分离，输出四态（mock 规则 + 百炼待验）。"""
import json
from abc import ABC, abstractmethod
from typing import Any

from app.core.config import settings
from app.core.errors import AppError
from app.core.jsonutil import extract_json_object
from app.schemas.evaluation import EvaluationOutput
from app.services.prompts.coach import EVAL_SYSTEM_PROMPT


class EvaluatorProvider(ABC):
    @abstractmethod
    def evaluate(self, context: dict[str, Any]) -> EvaluationOutput:
        """独立判断完成状态。"""


class MockEvaluatorProvider(EvaluatorProvider):
    """确定性的 mock 评估器。规则：空/过短=证据不足；用过提示=提示下；含 'I have' 或足够长=独立；否则=未完成。"""

    def evaluate(self, context: dict[str, Any]) -> EvaluationOutput:
        answer = (context.get("answer") or context.get("user_answer") or "").strip()
        hint_used = bool(context.get("hint_used", False))
        if len(answer) < 3:
            return EvaluationOutput(result="insufficient_evidence")
        if hint_used:
            return EvaluationOutput(result="hinted")
        if "I have" in answer or "experience" in answer or len(answer) >= 25:
            return EvaluationOutput(result="independent")
        return EvaluationOutput(result="incomplete")


class DashScopeEvaluatorProvider(EvaluatorProvider):
    """通义千问独立评估（结构化输出）。真实接入待验。"""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def evaluate(self, context: dict[str, Any]) -> EvaluationOutput:
        import dashscope

        dashscope.api_key = self.api_key
        resp = dashscope.Generation.call(
            model=self.model,
            messages=[
                {"role": "system", "content": EVAL_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            result_format="message",
        )
        if getattr(resp, "status_code", None) != 200:
            code = getattr(resp, "code", "")
            message = getattr(resp, "message", "未知错误")
            raise AppError("EVAL_ERROR", f"评估失败：{code} {message}".strip(), status_code=502)
        content = resp.output.choices[0].message.content
        data = extract_json_object(content)
        if data is None:
            raise AppError("EVAL_PARSE_ERROR", "评估返回格式异常", status_code=502)
        try:
            return EvaluationOutput(
                result=data.get("result", "insufficient_evidence"),
                evidence_turn_ids=list(data.get("evidence_turn_ids", [])),
            )
        except Exception as exc:  # noqa: BLE001
            raise AppError("EVAL_PARSE_ERROR", "评估结果字段不合法", status_code=502) from exc


def get_evaluator_provider() -> EvaluatorProvider:
    if settings.provider_mode == "mock":
        return MockEvaluatorProvider()
    if settings.provider_mode == "real":
        if not settings.dashscope_api_key:
            raise AppError("MISSING_API_KEY", "未配置 DASHSCOPE_API_KEY", status_code=500)
        return DashScopeEvaluatorProvider(settings.dashscope_api_key, settings.model_llm)
    raise AppError("CONFIG_ERROR", "未知的 PROVIDER_MODE，应为 mock 或 real", status_code=500)
