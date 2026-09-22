"""设备预检相关请求结构。"""
from pydantic import BaseModel, Field


class DeviceCheckRequest(BaseModel):
    status: str = Field(default="ok", description="ok / degraded")
    summary: str = Field(default="", max_length=255)
