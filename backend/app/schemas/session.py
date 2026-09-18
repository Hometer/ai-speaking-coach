"""会话相关请求/响应结构。"""
from typing import Literal

from pydantic import BaseModel, Field

Level = Literal["beginner", "basic", "intermediate"]
Difficulty = Literal["simple", "standard"]


class SessionCreateRequest(BaseModel):
    level: Level = "basic"
    difficulty: Difficulty = "standard"


class SessionCreatedResponse(BaseModel):
    session_id: str
    status: str
    goal_text: str
    reply_text: str
    reply_audio_url: str


class TurnItem(BaseModel):
    turn_id: str
    seq: int
    input_type: str
    user_text: str = ""
    transcript: str = ""
    reply_text: str = ""
    action_type: str = ""
    hint_used: bool = False
    eval_result: str = ""


class SessionDetailResponse(BaseModel):
    session_id: str
    status: str
    goal_text: str
    difficulty: str
    end_reason: str | None = None
    turns: list[TurnItem] = []


class EndRequest(BaseModel):
    reason: str = "user_ended"


class EndSummary(BaseModel):
    session_id: str
    status: str
    end_reason: str
    goal_status: str
    turn_count: int
    latest_eval: str = ""


class EndResponse(BaseModel):
    summary: EndSummary


class PracticeResponse(BaseModel):
    session_id: str
    status: str
    eval: dict = Field(default_factory=dict)
    recheck_reply_text: str
    recheck_audio_url: str
