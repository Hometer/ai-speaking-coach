"""学习记忆相关请求/响应结构。"""
from pydantic import BaseModel


class MemoryToggleRequest(BaseModel):
    enabled: bool


class MemoryItem(BaseModel):
    id: int
    text: str
    source_session_id: str = ""
