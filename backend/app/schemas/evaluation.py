"""独立评估结果结构。"""
from typing import Literal

from pydantic import BaseModel

EvalResult = Literal["independent", "hinted", "incomplete", "insufficient_evidence"]


class EvaluationOutput(BaseModel):
    result: EvalResult
    evidence_turn_ids: list[str] = []
    standard_version: str = "v1"
