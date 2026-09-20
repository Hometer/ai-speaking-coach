"""学习记忆相关请求/响应结构。"""
from pydantic import BaseModel, Field


class MemoryToggleRequest(BaseModel):
    enabled: bool


class MemoryUpdateRequest(BaseModel):
    text: str = Field(..., min_length=1)


class MemoryItem(BaseModel):
    id: int
    text: str
    source_session_id: str = ""
    status: str = "candidate"
    evidence_count: int = 0
