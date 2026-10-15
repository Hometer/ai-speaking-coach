"""独立评估器：与生成分离，输出四态（mock 规则 + 百炼待验）。"""
import json
import re
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
        from app.services.answer_feedback import mock_feedback
        result = self._completion(context)
        if result.result != "insufficient_evidence":
            result.feedback = mock_feedback((context.get("answer") or context.get("user_answer") or "").strip())
        return result

    def _completion(self, context: dict[str, Any]) -> EvaluationOutput:
        answer = (context.get("answer") or context.get("user_answer") or "").strip()
        hint_used = bool(context.get("hint_used", False))
        if len(answer) < 3:
            return EvaluationOutput(result="insufficient_evidence")
        if hint_used:
            return EvaluationOutput(result="hinted")
        if context.get("scene_id") == "hotel_checkin" and context.get("subgoal") == "confirm_booking":
            named_booking = bool(re.search(r"\b(?:under (?:the )?name|my name is)\s+[a-z][a-z'-]+\b", answer, re.I))
            nights = bool(re.search(r"\b(?:one|two|three|four|five|\d+)\s+nights?\b", answer, re.I))
            return EvaluationOutput(result="independent" if named_booking or nights else "incomplete")
        points = context.get("acceptable_points") or ["i have", "experience"]
        lowered = answer.lower()
        matches = sum(1 for kw in points if kw.lower() in lowered)
        # 需要实质内容：至少命中两个要点，或一个要点且足够长（非极简短语）
        if matches >= 2 or (matches >= 1 and len(answer) >= 25):
            return EvaluationOutput(result="independent")
        return EvaluationOutput(result="incomplete")


class DashScopeEvaluatorProvider(EvaluatorProvider):
    """通义千问独立评估（结构化输出）。真实接入待验。"""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def evaluate(self, context: dict[str, Any]) -> EvaluationOutput:
        from app.services.generation_call import call_generation

        resp = call_generation(
            api_key=self.api_key,
            model=self.model,
            messages=[
                {"role": "system", "content": EVAL_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
        )
        try:
            content = resp.output.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise AppError("EVAL_PARSE_ERROR", "评估返回格式异常", status_code=502) from exc
        data = extract_json_object(content)
        if data is None:
            raise AppError("EVAL_PARSE_ERROR", "评估返回格式异常", status_code=502)
        try:
            from app.services.answer_feedback import grounded
            return EvaluationOutput(
                result=data.get("result", "insufficient_evidence"),
                evidence_turn_ids=list(data.get("evidence_turn_ids", [])),
                feedback=grounded(context.get("answer", ""), data.get("feedback", [])),
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
