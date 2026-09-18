"""API 路由。"""
import json
from typing import Any

from fastapi import APIRouter, File, Form, Header, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.core.errors import AppError, error_body
from app.models.database import SessionLocal
from app.schemas.memory import MemoryItem, MemoryToggleRequest
from app.schemas.session import (
    EndRequest,
    EndResponse,
    PracticeResponse,
    SessionCreateRequest,
    SessionCreatedResponse,
    SessionDetailResponse,
    SessionListItem,
)
from app.schemas.turn import TextTurnRequest, TurnResponse
from app.services import memory as memory_svc
from app.services import session_svc
from app.services.turn import handle_text_turn, handle_voice_turn, resolve_audio_path

router = APIRouter(prefix="/api/v1")

_AUDIO_MEDIA = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "webm": "audio/webm",
    "mp4": "audio/mp4",
    "ogg": "audio/ogg",
}


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ===== 阶段 2.1 兼容：无状态快速回合 =====


@router.post("/turn/voice", response_model=TurnResponse)
async def voice_turn(audio: UploadFile = File(...)):
    data = await audio.read()
    return handle_voice_turn(data)


@router.post("/turn/text", response_model=TurnResponse)
async def text_turn(req: TextTurnRequest):
    return handle_text_turn(req.text)


@router.get("/audio/{filename}")
async def get_audio(filename: str):
    path = resolve_audio_path(filename)
    media_type = _AUDIO_MEDIA.get(path.suffix.lstrip("."), "application/octet-stream")
    return FileResponse(path, media_type=media_type)


# ===== 阶段 2.2：会话 =====


@router.post("/sessions", response_model=SessionCreatedResponse)
async def create_session(
    req: SessionCreateRequest,
    x_user_id: str = Header(default="demo-user", alias="X-User-Id"),
):
    db = SessionLocal()
    try:
        return session_svc.create_session(db, x_user_id, req.level, req.difficulty)
    finally:
        db.close()


@router.post("/sessions/{session_id}/turns/text")
async def text_turn_stream(
    session_id: str,
    req: TextTurnRequest,
    x_user_id: str = Header(default="demo-user", alias="X-User-Id"),
):
    def gen():
        db = SessionLocal()
        try:
            result = session_svc.submit_turn(db, session_id, x_user_id, "text", user_text=req.text)
            yield _sse("chunk", {"text": result["reply_text"]})
            yield _sse("done", result)
        except AppError as exc:
            yield _sse("error", error_body(exc.code, exc.message))
        except Exception:
            yield _sse("error", error_body("INTERNAL_ERROR", "服务内部错误，请稍后重试"))
        finally:
            db.close()

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/sessions/{session_id}/turns/voice")
async def voice_turn_stream(
    session_id: str,
    audio: UploadFile = File(...),
    x_user_id: str = Header(default="demo-user", alias="X-User-Id"),
):
    data = await audio.read()

    def gen():
        db = SessionLocal()
        try:
            result = session_svc.submit_turn(db, session_id, x_user_id, "voice", audio_bytes=data)
            yield _sse("chunk", {"text": result["reply_text"]})
            yield _sse("done", result)
        except AppError as exc:
            yield _sse("error", error_body(exc.code, exc.message))
        except Exception:
            yield _sse("error", error_body("INTERNAL_ERROR", "服务内部错误，请稍后重试"))
        finally:
            db.close()

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/sessions/{session_id}/practice", response_model=PracticeResponse)
async def practice(
    session_id: str,
    sentence: str = Form(...),
    audio: UploadFile = File(...),
    x_user_id: str = Header(default="demo-user", alias="X-User-Id"),
):
    data = await audio.read()
    db = SessionLocal()
    try:
        return session_svc.submit_practice(db, session_id, x_user_id, data, sentence)
    finally:
        db.close()


@router.post("/sessions/{session_id}/end", response_model=EndResponse)
async def end_session(
    session_id: str,
    req: EndRequest,
    x_user_id: str = Header(default="demo-user", alias="X-User-Id"),
):
    db = SessionLocal()
    try:
        return session_svc.end_session(db, session_id, x_user_id, req.reason)
    finally:
        db.close()


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def session_detail(
    session_id: str,
    x_user_id: str = Header(default="demo-user", alias="X-User-Id"),
):
    db = SessionLocal()
    try:
        return session_svc.get_session_detail(db, session_id, x_user_id)
    finally:
        db.close()


@router.get("/sessions", response_model=list[SessionListItem])
async def list_sessions(
    x_user_id: str = Header(default="demo-user", alias="X-User-Id"),
):
    db = SessionLocal()
    try:
        return session_svc.list_sessions(db, x_user_id)
    finally:
        db.close()


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    x_user_id: str = Header(default="demo-user", alias="X-User-Id"),
):
    db = SessionLocal()
    try:
        session_svc.delete_session(db, session_id, x_user_id)
        return {"deleted": True}
    finally:
        db.close()


# ===== 阶段 2.3：学习记忆 =====


@router.post("/users/me/memory")
async def toggle_memory(
    req: MemoryToggleRequest,
    x_user_id: str = Header(default="demo-user", alias="X-User-Id"),
):
    db = SessionLocal()
    try:
        enabled = memory_svc.set_memory_enabled(db, x_user_id, req.enabled)
        return {"enabled": enabled}
    finally:
        db.close()


@router.get("/users/me/memories", response_model=list[MemoryItem])
async def list_memories(
    x_user_id: str = Header(default="demo-user", alias="X-User-Id"),
):
    db = SessionLocal()
    try:
        mems = memory_svc.list_memories(db, x_user_id)
        return [
            {"id": m.id, "text": m.text, "source_session_id": m.source_session_id}
            for m in mems
        ]
    finally:
        db.close()


@router.delete("/users/me/memories/{memory_id}")
async def delete_memory(
    memory_id: int,
    x_user_id: str = Header(default="demo-user", alias="X-User-Id"),
):
    db = SessionLocal()
    try:
        memory_svc.delete_memory(db, x_user_id, memory_id)
        return {"deleted": True}
    finally:
        db.close()


@router.delete("/users/me/memories")
async def clear_memories(
    x_user_id: str = Header(default="demo-user", alias="X-User-Id"),
):
    db = SessionLocal()
    try:
        memory_svc.clear_memories(db, x_user_id)
        return {"deleted": True}
    finally:
        db.close()
