"""回合相关请求/响应模型。"""
from pydantic import BaseModel, Field


class TextTurnRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000, description="用户输入的文字")
    request_key: str = Field(default="", description="幂等键")


class TurnResponse(BaseModel):
    request_id: str
    transcript: str = ""
    reply_text: str
    reply_audio_url: str
