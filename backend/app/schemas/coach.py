"""教练决策结构（结构化输出）。"""
from typing import Literal

from pydantic import BaseModel

ActionType = Literal["follow_up", "hint", "suggest_practice", "recheck"]


class CoachDecision(BaseModel):
    action_type: ActionType
    reply_text: str
    follow_up_question: str = ""
    practice_sentence: str = ""
    needs_evaluation: bool = True
