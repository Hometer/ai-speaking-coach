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
    request_key: str = Field(default="", description="幂等键")


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
    level: str = "basic"
    current_help_level: int = 0
    end_reason: str | None = None
    practice_type: str = "interview"
    scene_id: str = "job_interview"
    scene_label: str = ""
    source_invalid: bool = False
    review: dict | None = None
    drill: dict | None = None  # 脱稿阶梯视图（sentence_practice 会话）
    turns: list[TurnItem] = []


class SessionListItem(BaseModel):
    session_id: str
    status: str
    difficulty: str
    level: str = "basic"
    practice_type: str = "interview"
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
    recording_url: str = ""
    recheck_reply_text: str
    recheck_audio_url: str


class ReviseTurnRequest(BaseModel):
    revised_text: str = Field(..., min_length=1)


class HelpRequest(BaseModel):
    help_type: Literal["repeat", "explain", "organize", "translate"]
    source_text: str = Field(default="", max_length=2000)  # translate 必填（用户的中文原意）


class DrillAttemptRequest(BaseModel):
    text: str = Field(default="", max_length=2000)
    input_type: str = Field(default="text", description="voice/text")
    attempt_key: str = Field(default="", description="幂等键")


class DrillActionRequest(BaseModel):
    action: Literal["skip", "downgrade", "jump", "end"]


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
