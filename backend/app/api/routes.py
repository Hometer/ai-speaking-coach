"""API 路由。"""
import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.core.auth import get_user_id
from app.core.errors import AppError, error_body
from app.models.database import SessionLocal
from app.schemas.memory import MemoryItem, MemoryToggleRequest, MemoryUpdateRequest
from app.schemas.session import (
    EndRequest,
    EndResponse,
    PracticeResponse,
    ReviseTurnRequest,
    SessionCreateRequest,
    SessionCreatedResponse,
    SessionDetailResponse,
    SessionListItem,
)
from app.schemas.turn import TextTurnRequest
from app.services import memory as memory_svc
from app.services import session_svc

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


# ===== 音频播放（校验归属）=====


@router.get("/audio/{filename}")
async def get_audio(filename: str, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        path = session_svc.resolve_audio_for_user(db, filename, user_id)
    finally:
        db.close()
    media_type = _AUDIO_MEDIA.get(path.suffix.lstrip("."), "application/octet-stream")
    return FileResponse(path, media_type=media_type)


# ===== 会话 =====


@router.post("/sessions", response_model=SessionCreatedResponse)
async def create_session(
    req: SessionCreateRequest,
    user_id: str = Depends(get_user_id),
):
    db = SessionLocal()
    try:
        return session_svc.create_session(db, user_id, req.level, req.difficulty, req.duration_minutes)
    finally:
        db.close()


@router.post("/sessions/{session_id}/turns/text")
async def text_turn_stream(
    session_id: str,
    req: TextTurnRequest,
    user_id: str = Depends(get_user_id),
):
    def gen():
        db = SessionLocal()
        try:
            result = session_svc.submit_turn(db, session_id, user_id, "text", user_text=req.text)
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
    user_id: str = Depends(get_user_id),
):
    data = await audio.read()

    def gen():
        db = SessionLocal()
        try:
            result = session_svc.submit_turn(db, session_id, user_id, "voice", audio_bytes=data)
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
    user_id: str = Depends(get_user_id),
):
    data = await audio.read()
    db = SessionLocal()
    try:
        return session_svc.submit_practice(db, session_id, user_id, data, sentence)
    finally:
        db.close()


@router.post("/sessions/{session_id}/end", response_model=EndResponse)
async def end_session(
    session_id: str,
    req: EndRequest,
    user_id: str = Depends(get_user_id),
):
    db = SessionLocal()
    try:
        return session_svc.end_session(db, session_id, user_id, req.reason)
    finally:
        db.close()


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def session_detail(
    session_id: str,
    user_id: str = Depends(get_user_id),
):
    db = SessionLocal()
    try:
        return session_svc.get_session_detail(db, session_id, user_id)
    finally:
        db.close()


@router.get("/sessions", response_model=list[SessionListItem])
async def list_sessions(
    user_id: str = Depends(get_user_id),
):
    db = SessionLocal()
    try:
        return session_svc.list_sessions(db, user_id)
    finally:
        db.close()


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user_id: str = Depends(get_user_id),
):
    db = SessionLocal()
    try:
        session_svc.delete_session(db, session_id, user_id)
        return {"deleted": True}
    finally:
        db.close()


@router.delete("/sessions")
async def delete_all_sessions(
    user_id: str = Depends(get_user_id),
):
    db = SessionLocal()
    try:
        session_svc.delete_all_sessions(db, user_id)
        return {"deleted": True}
    finally:
        db.close()


@router.post("/sessions/{session_id}/pause")
async def pause_session(
    session_id: str,
    user_id: str = Depends(get_user_id),
):
    db = SessionLocal()
    try:
        return session_svc.set_paused(db, session_id, user_id, True)
    finally:
        db.close()


@router.post("/sessions/{session_id}/resume")
async def resume_session(
    session_id: str,
    user_id: str = Depends(get_user_id),
):
    db = SessionLocal()
    try:
        return session_svc.set_paused(db, session_id, user_id, False)
    finally:
        db.close()


@router.post("/sessions/{session_id}/skip-practice")
async def skip_practice(
    session_id: str,
    user_id: str = Depends(get_user_id),
):
    db = SessionLocal()
    try:
        return session_svc.skip_practice(db, session_id, user_id)
    finally:
        db.close()


@router.patch("/sessions/{session_id}/turns/{turn_id}")
async def revise_turn(
    session_id: str,
    turn_id: str,
    req: ReviseTurnRequest,
    user_id: str = Depends(get_user_id),
):
    db = SessionLocal()
    try:
        return session_svc.revise_turn(db, session_id, user_id, turn_id, req.revised_text)
    finally:
        db.close()


@router.post("/sessions/{session_id}/hint")
async def request_hint(
    session_id: str,
    user_id: str = Depends(get_user_id),
):
    db = SessionLocal()
    try:
        return session_svc.request_hint(db, session_id, user_id)
    finally:
        db.close()


@router.post("/sessions/{session_id}/review/dispute")
async def dispute_review(
    session_id: str,
    turn_id: str,
    user_id: str = Depends(get_user_id),
):
    db = SessionLocal()
    try:
        return session_svc.dispute_review_improvement(db, session_id, user_id, turn_id)
    finally:
        db.close()


# ===== 学习记忆 =====


@router.post("/users/me/memory")
async def toggle_memory(
    req: MemoryToggleRequest,
    user_id: str = Depends(get_user_id),
):
    db = SessionLocal()
    try:
        enabled = memory_svc.set_memory_enabled(db, user_id, req.enabled)
        return {"enabled": enabled}
    finally:
        db.close()


@router.get("/users/me/memories", response_model=list[MemoryItem])
async def list_memories(
    user_id: str = Depends(get_user_id),
):
    db = SessionLocal()
    try:
        mems = memory_svc.list_memories(db, user_id)
        return [
            {
                "id": m.id,
                "text": m.text,
                "source_session_id": m.source_session_id,
                "status": m.status,
                "evidence_count": m.evidence_count,
            }
            for m in mems
        ]
    finally:
        db.close()


@router.patch("/users/me/memories/{memory_id}")
async def update_memory(
    memory_id: int,
    req: MemoryUpdateRequest,
    user_id: str = Depends(get_user_id),
):
    db = SessionLocal()
    try:
        m = memory_svc.update_memory_text(db, user_id, memory_id, req.text)
        return {"id": m.id, "text": m.text, "status": m.status}
    finally:
        db.close()


@router.delete("/users/me/memories/{memory_id}")
async def delete_memory(
    memory_id: int,
    user_id: str = Depends(get_user_id),
):
    db = SessionLocal()
    try:
        memory_svc.delete_memory(db, user_id, memory_id)
        return {"deleted": True}
    finally:
        db.close()


@router.delete("/users/me/memories")
async def clear_memories(
    user_id: str = Depends(get_user_id),
):
    db = SessionLocal()
    try:
        memory_svc.clear_memories(db, user_id)
        return {"deleted": True}
    finally:
        db.close()
