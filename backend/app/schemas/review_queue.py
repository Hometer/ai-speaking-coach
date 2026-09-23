"""待复练队列请求/响应结构。"""
from pydantic import BaseModel, Field


class AddToQueueRequest(BaseModel):
    source_session_id: str
    source_turn_id: str


class RetestRequest(BaseModel):
    answer: str = Field(..., min_length=1)
    hint_used: bool = False


class QueueItemResponse(BaseModel):
    id: int
    source_session_id: str
    source_turn_id: str
    target_ability: str
    suggestion_sentence: str
    status: str
    success_count: int
    due_at: str | None = None
    last_practiced_at: str | None = None
    retest_result: str = ""


class DailyReviewAttemptRequest(BaseModel):
    answer: str = Field(default="", max_length=2000)
    help_used: bool = False


class DailyReviewActionRequest(BaseModel):
    action: str = Field(..., description="skip/defer/dispute/end")


class PhraseCardRequest(BaseModel):
    source_session_id: str
    source_turn_id: str
