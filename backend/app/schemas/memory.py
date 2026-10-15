"""学习记忆相关请求/响应结构。"""
from pydantic import BaseModel, Field


class MemoryToggleRequest(BaseModel):
    enabled: bool


class MemorySettingsResponse(BaseModel):
    enabled: bool


class MemoryUpdateRequest(BaseModel):
    text: str = Field(..., min_length=1)


class MemoryItem(BaseModel):
    id: int
    text: str
    source_session_id: str = ""
    status: str = "candidate"
    evidence_count: int = 0
    expires_at: str | None = None
    scene_id: str = ""
    version: str = "v1"
    usage_reason: str = "同场景、来源有效且未到期；至少两个独立会话重复出现才用于后续练习"
