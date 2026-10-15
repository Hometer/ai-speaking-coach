"""独立评估结果结构。"""
from typing import Literal

from pydantic import BaseModel, Field, StrictBool, ConfigDict

EvalResult = Literal["independent", "hinted", "incomplete", "insufficient_evidence", "text_completed"]


class AnswerFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    issue_type: Literal["grammar", "vocabulary", "expression"]
    source_quote: str = Field(min_length=1, max_length=2000)
    explanation_cn: str = Field(min_length=1, max_length=600)
    practice_sentence: str = Field(min_length=1, max_length=2000)
    facts_preserved: StrictBool


class EvaluationOutput(BaseModel):
    result: EvalResult
    evidence_turn_ids: list[str] = []
    standard_version: str = "v1"
    feedback: list[AnswerFeedback] = Field(default_factory=list, max_length=2)
