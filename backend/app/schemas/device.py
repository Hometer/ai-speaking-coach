"""设备预检相关请求结构。"""
from pydantic import BaseModel, Field
from typing import Literal


class DeviceCheckRequest(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    summary: str = Field(default="", max_length=255)
