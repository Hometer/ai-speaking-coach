"""质检录音特别授权请求。"""
from pydantic import BaseModel


class QaConsentRequest(BaseModel):
    granted: bool
