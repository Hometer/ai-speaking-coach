"""幂等请求的请求摘要与首次响应。"""
import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.core.errors import AppError
from app.models.entities import IntentResult


def _digest(input_type: str, answer: str) -> str:
    return hashlib.sha256(f"{input_type}\0{answer}".encode("utf-8")).hexdigest()


def replay(db: DbSession, user_id: str, scope: str, object_id: str, key: str, input_type: str, answer: str) -> dict[str, Any] | None:
    if not key:
        return None
    row = db.query(IntentResult).filter_by(scope=scope, object_id=object_id, request_key=key).first()
    if row is None:
        return None
    if row.user_id != user_id or row.request_hash != _digest(input_type, answer):
        raise AppError("IDEMPOTENCY_CONFLICT", "同一请求键内容不一致", status_code=409)
    return json.loads(row.response_json)


def remember(db: DbSession, user_id: str, scope: str, object_id: str, key: str, input_type: str, answer: str, response: dict[str, Any]) -> None:
    if not key:
        return
    db.add(IntentResult(
        user_id=user_id, scope=scope, object_id=object_id, request_key=key,
        request_hash=_digest(input_type, answer),
        response_json=json.dumps(response, ensure_ascii=False),
    ))
    db.commit()
