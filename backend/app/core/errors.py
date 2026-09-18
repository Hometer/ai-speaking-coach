"""统一错误类型与错误结构。"""
from typing import Any


class AppError(Exception):
    """业务错误，统一转为 {"error": {"code", "message"}}，不泄露堆栈。"""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def error_body(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}
