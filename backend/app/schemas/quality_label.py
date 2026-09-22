"""质检工作台请求结构。"""
from pydantic import BaseModel, Field


class AddLabelRequest(BaseModel):
    session_id: str
    turn_id: str = ""
    issue_type: str = Field(..., description="mis_correction/wrong_completion/irrelevant_followup/help_skip/insufficient_evidence/inappropriate")
    conclusion: str = "confirmed"
