"""待复练队列请求/响应结构。"""
from pydantic import BaseModel, Field


class AddToQueueRequest(BaseModel):
    source_session_id: str
    source_turn_id: str


class RetestRequest(BaseModel):
    answer: str = Field(..., min_length=1)
    attempt_key: str = Field(default="", description="幂等键")
    hint_used: bool = False  # 已废弃：帮助是否已用由服务端 help_shown 决定


class QueueItemResponse(BaseModel):
    material_available: bool = True
    attempt_stage: str = "recall"
    question_text: str = ""
    question_version: int = 1
    id: int
    source_session_id: str
    source_turn_id: str
    target_ability: str
    suggestion_sentence: str = ""  # 未看答案时为空
    meaning_zh: str = ""
    help_shown: bool = False
    status: str
    success_count: int
    due_at: str | None = None
    last_practiced_at: str | None = None
    retest_result: str = ""


class DailyReviewAttemptRequest(BaseModel):
    answer: str = Field(default="", max_length=2000)
    attempt_key: str = Field(default="", description="幂等键")
    help_used: bool = False  # 已废弃：帮助是否已用由服务端 help_shown 决定


class DailyReviewActionRequest(BaseModel):
    action: str = Field(..., description="skip/defer/dispute/end")


class PhraseCardRequest(BaseModel):
    source_session_id: str
    source_turn_id: str
