"""Agent 教练决策：结构化输出（mock 规则 + 百炼待验）。"""
from abc import ABC, abstractmethod
from typing import Any

from app.core.config import settings
from app.core.errors import AppError
from app.core.jsonutil import extract_json_object
from app.schemas.coach import CoachDecision
from app.services.prompts.coach import COACH_DECISION_FORMAT, COACH_SYSTEM_PROMPT_V2


class CoachProvider(ABC):
    @abstractmethod
    def opening(self, goal_text: str, difficulty: str) -> CoachDecision:
        """会话创建时的首个问题。"""

    @abstractmethod
    def decide(self, context: dict[str, Any]) -> CoachDecision:
        """根据当前表现与上下文选择教学行动。"""


class MockCoachProvider(CoachProvider):
    """确定性的 mock 教练。决策只依赖 context 中的内容，供测试与离线演示。"""

    def opening(self, goal_text: str, difficulty: str) -> CoachDecision:
        return CoachDecision(
            action_type="follow_up",
            reply_text="Welcome. Tell me about one experience you are proud of.",
            follow_up_question="What experience are you most proud of?",
        )

    def decide(self, context: dict[str, Any]) -> CoachDecision:
        practice = context.get("practice") or {}
        if practice:
            if not practice.get("valid"):
                return CoachDecision(
                    action_type="follow_up",
                    reply_text="I couldn't assess that recording. Let's continue with your experience.",
                )
            score = practice.get("score")
            if score is not None and score < 60:
                return CoachDecision(
                    action_type="suggest_practice",
                    reply_text="Let's try that sentence once more, a bit slower.",
                    practice_sentence=practice.get("reference_sentence", ""),
                )
            return CoachDecision(
                action_type="follow_up",
                reply_text="Good practice. Now tell me more about your experience.",
            )

        result = context.get("eval_result", "")
        subgoal = context.get("subgoal", "")
        if result == "insufficient_evidence":
            return CoachDecision(
                action_type="hint",
                reply_text="No problem. Try starting with 'I have...' and tell me one thing.",
            )
        if result == "incomplete":
            return CoachDecision(
                action_type="suggest_practice",
                reply_text="Let's practice a useful sentence. Repeat after me.",
                practice_sentence="I have three years of experience in software development.",
            )
        if result == "hinted":
            return CoachDecision(
                action_type="recheck",
                reply_text="Nice. Now try to say it again in your own words without the hint.",
            )
        if result == "independent":
            if subgoal == "intro":
                return CoachDecision(
                    action_type="follow_up",
                    reply_text="Great. What was the most challenging part of that experience?",
                    follow_up_question="What was the most challenging part?",
                )
            return CoachDecision(
                action_type="follow_up",
                reply_text="Excellent, you expressed both clearly on your own.",
            )
        return CoachDecision(action_type="follow_up", reply_text="Can you tell me a bit more?")


class DashScopeCoachProvider(CoachProvider):
    """通义千问教练决策（结构化输出）。真实接入待验。"""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def _call(self, user_msg: str) -> dict[str, Any]:
        import dashscope

        dashscope.api_key = self.api_key
        resp = dashscope.Generation.call(
            model=self.model,
            messages=[
                {"role": "system", "content": COACH_SYSTEM_PROMPT_V2 + "\n" + COACH_DECISION_FORMAT},
                {"role": "user", "content": user_msg},
            ],
            result_format="message",
        )
        if getattr(resp, "status_code", None) != 200:
            code = getattr(resp, "code", "")
            message = getattr(resp, "message", "未知错误")
            raise AppError("COACH_ERROR", f"模型调用失败：{code} {message}".strip(), status_code=502)
        content = resp.output.choices[0].message.content
        data = extract_json_object(content)
        if data is None:
            raise AppError("COACH_PARSE_ERROR", "教练决策返回格式异常", status_code=502)
        return data

    def _to_decision(self, data: dict[str, Any]) -> CoachDecision:
        try:
            decision = CoachDecision(
                action_type=data.get("action_type", "follow_up"),
                reply_text=str(data.get("reply_text", "")).strip(),
                follow_up_question=str(data.get("follow_up_question", "")).strip(),
                practice_sentence=str(data.get("practice_sentence", "")).strip(),
                needs_evaluation=bool(data.get("needs_evaluation", True)),
            )
        except Exception as exc:  # noqa: BLE001
            raise AppError("COACH_PARSE_ERROR", "教练决策字段不合法", status_code=502) from exc
        if not decision.reply_text:
            raise AppError("COACH_EMPTY", "教练回复为空", status_code=502)
        return decision

    def opening(self, goal_text: str, difficulty: str) -> CoachDecision:
        data = self._call(f"Start the session. Goal: {goal_text}. Difficulty: {difficulty}. Give the opening.")
        return self._to_decision(data)

    def decide(self, context: dict[str, Any]) -> CoachDecision:
        data = self._call(
            "Session context as JSON:\n"
            + __import__("json").dumps(context, ensure_ascii=False)
            + "\nChoose the next teaching action. The follow-up must be based on what the user actually said."
        )
        return self._to_decision(data)


def get_coach_provider() -> CoachProvider:
    if settings.provider_mode == "mock":
        return MockCoachProvider()
    if settings.provider_mode == "real":
        if not settings.dashscope_api_key:
            raise AppError("MISSING_API_KEY", "未配置 DASHSCOPE_API_KEY", status_code=500)
        return DashScopeCoachProvider(settings.dashscope_api_key, settings.model_llm)
    raise AppError("CONFIG_ERROR", "未知的 PROVIDER_MODE，应为 mock 或 real", status_code=500)
