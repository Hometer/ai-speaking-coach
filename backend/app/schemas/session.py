"""会话相关请求/响应结构。"""
from typing import Literal

from pydantic import BaseModel, Field

Level = Literal["beginner", "basic", "intermediate"]
Difficulty = Literal["simple", "standard"]


class SessionCreateRequest(BaseModel):
    level: Level = "basic"
    difficulty: Difficulty = "standard"
    duration_minutes: float = Field(default=5.0, ge=3.0, le=15.0)
    scene_id: str = "job_interview"


class SessionCreatedResponse(BaseModel):
    session_id: str
    status: str
    goal_text: str
    scene_id: str = "job_interview"
    scene_label: str = ""
    reply_text: str
    reply_audio_url: str


class TurnItem(BaseModel):
    turn_id: str
    seq: int
    input_type: str
    user_text: str = ""
    transcript: str = ""
    reply_text: str = ""
    reply_audio_url: str = ""
    practice_sentence: str = ""
    action_type: str = ""
    hint_used: bool = False
    eval_result: str = ""
    revised_text: str = ""


class SessionDetailResponse(BaseModel):
    session_id: str
    status: str
    goal_text: str
    difficulty: str
    end_reason: str | None = None
    practice_type: str = "interview"
    scene_id: str = "job_interview"
    scene_label: str = ""
    source_invalid: bool = False
    review: dict | None = None
    turns: list[TurnItem] = []


class SessionListItem(BaseModel):
    session_id: str
    status: str
    difficulty: str
    started_at: str | None = None
    ended_at: str | None = None
    turn_count: int = 0
    goal_status: str = ""
    end_reason: str | None = None
    scene_id: str = "job_interview"
    scene_label: str = ""


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
    review: dict | None = None


class PracticeResponse(BaseModel):
    session_id: str
    status: str
    eval: dict = Field(default_factory=dict)
    recheck_reply_text: str
    recheck_audio_url: str


class ReviseTurnRequest(BaseModel):
    revised_text: str = Field(..., min_length=1)


class HelpRequest(BaseModel):
    help_type: str = Field(..., description="repeat/explain/organize/translate")
    help_level: int = Field(default=0, ge=0, le=3)


class PracticeSentenceRequest(BaseModel):
    source_session_id: str
    turn_id: str


class ScenarioListItem(BaseModel):
    scene_id: str
    series: str
    series_label: str
    label: str
    version: str
    goal_zh: str
    user_role: str
    coach_role: str


class ScenarioDetail(ScenarioListItem):
    goal_text: str
    levels: list[str] = []
    openings: dict[str, str] = {}
