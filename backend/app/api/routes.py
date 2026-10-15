"""API 路由（同步 def，由 FastAPI 线程池执行，避免阻塞事件循环）。"""
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.core.auth import get_qa_user_id, get_user_id
from app.core.config import settings
from app.core.errors import AppError, error_body
from app.services import deadline
from app.models.database import SessionLocal
from app.models.entities import DeviceCheck, ReviewQueueItem
from app.schemas.device import DeviceCheckRequest
from app.schemas.memory import MemoryItem, MemorySettingsResponse, MemoryToggleRequest, MemoryUpdateRequest
from app.schemas.session import (
    DrillActionRequest,
    DrillAttemptRequest,
    EndRequest,
    EndResponse,
    HelpRequest,
    PracticeResponse,
    PracticeSentenceRequest,
    ReviseTurnRequest,
    ScenarioDetail,
    ScenarioListItem,
    SessionCreateRequest,
    SessionCreatedResponse,
    SessionDetailResponse,
    SessionListItem,
)
from app.schemas.turn import ConfirmTurnRequest, TextTurnRequest
from app.schemas.review_queue import AddToQueueRequest, DailyReviewActionRequest, DailyReviewAttemptRequest, PhraseCardRequest, QueueItemResponse, RetestRequest
from app.schemas.training_plan import CreatePlanRequest, PlanResponse
from app.schemas.quality_label import AddLabelRequest
from app.schemas.qa_recording import QaConsentRequest
from app.services import memory as memory_svc
from app.services import daily_review_svc
from app.services import drill_svc
from app.services import help_svc
from app.services import phrase_card_svc
from app.services import session_svc
from app.services import review_queue as queue_svc
from app.services import progress as progress_svc
from app.services import training_plan as plan_svc
from app.services import quality_label as qa_svc
from app.services import qa_recording as qa_recording_svc
from app.services.prompts import scenarios as scenarios_mod

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


@router.get("/audio/{filename}")
def get_audio(filename: str, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        path = session_svc.resolve_audio_for_user(db, filename, user_id)
    finally:
        db.close()
    media_type = _AUDIO_MEDIA.get(path.suffix.lstrip("."), "application/octet-stream")
    return FileResponse(path, media_type=media_type)


@router.post("/sessions/{session_id}/qa-recording-consent")
def qa_recording_consent(session_id: str, req: QaConsentRequest, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return qa_recording_svc.set_consent(db, user_id, session_id, req.granted)
    finally:
        db.close()


@router.get("/qa/recordings/{recording_id}")
def qa_recording_audio(recording_id: int, reviewer_id: str = Depends(get_qa_user_id)):
    db = SessionLocal()
    try:
        path = qa_recording_svc.playback_path(db, reviewer_id, recording_id)
    finally:
        db.close()
    return FileResponse(path, media_type=_AUDIO_MEDIA.get(path.suffix.lstrip("."), "application/octet-stream"))


@router.post("/qa/recordings/{recording_id}/complete")
def qa_recording_complete(recording_id: int, reviewer_id: str = Depends(get_qa_user_id)):
    db = SessionLocal()
    try:
        return qa_recording_svc.complete_review(db, reviewer_id, recording_id)
    finally:
        db.close()


@router.get("/qa/recordings/{recording_id}/access-log")
def qa_recording_access_log(recording_id: int, reviewer_id: str = Depends(get_qa_user_id)):
    db = SessionLocal()
    try:
        return qa_recording_svc.access_log(db, recording_id)
    finally:
        db.close()


@router.post("/sessions", response_model=SessionCreatedResponse)
def create_session(req: SessionCreateRequest, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return session_svc.create_session(db, user_id, req.level, req.difficulty, req.duration_minutes, req.scene_id, req.request_key, req.plan_id, req.plan_day)
    finally:
        db.close()


@router.get("/scenarios", response_model=list[ScenarioListItem])
def list_scenarios(user_id: str = Depends(get_user_id)):
    return [
        {
            "scene_id": s["scene_id"],
            "series": s["series"],
            "series_label": scenarios_mod.SERIES.get(s["series"], s["series"]),
            "label": s["label"],
            "version": s["version"],
            "goal_zh": s["goal_zh"],
            "user_role": s["user_role"],
            "coach_role": s["coach_role"],
        }
        for s in scenarios_mod.get_approved_scenes()
    ]


@router.get("/scenarios/{scene_id}", response_model=ScenarioDetail)
def scenario_detail(scene_id: str, user_id: str = Depends(get_user_id)):
    s = scenarios_mod.get_scene(scene_id)
    return {
        "scene_id": s["scene_id"],
        "series": s["series"],
        "series_label": scenarios_mod.SERIES.get(s["series"], s["series"]),
        "label": s["label"],
        "version": s["version"],
        "goal_zh": s["goal_zh"],
        "goal_text": s["goal_text"],
        "user_role": s["user_role"],
        "coach_role": s["coach_role"],
        "levels": list(s["openings"].keys()),
        "openings": s["openings"],
    }


@router.post("/device-check")
def record_device_check(req: DeviceCheckRequest, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        db.add(DeviceCheck(user_id=user_id, summary=req.summary, status=req.status))
        db.commit()
        return {"recorded": True}
    finally:
        db.close()


@router.post("/sessions/practice-sentence", response_model=SessionCreatedResponse)
def create_practice_sentence(req: PracticeSentenceRequest, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return session_svc.create_sentence_practice(db, user_id, req.source_session_id, req.turn_id, req.request_key)
    finally:
        db.close()


@router.post("/sessions/{session_id}/turns/text")
def text_turn_stream(session_id: str, req: TextTurnRequest, user_id: str = Depends(get_user_id)):
    def gen():
        deadline_at = time.monotonic() + 15
        db = SessionLocal()
        try:
            with deadline.until(deadline_at):
                partial = session_svc.submit_turn(db, session_id, user_id, "text", user_text=req.text, request_key=req.request_key)
            yield _sse("chunk", {"text": partial["reply_text"]})
            with deadline.until(deadline_at):
                done = session_svc.synthesize_turn_audio(db, session_id, user_id, partial["turn_id"])
            yield _sse("done", done)
        except AppError as exc:
            yield _sse("error", error_body(exc.code, exc.message))
        except Exception:
            yield _sse("error", error_body("INTERNAL_ERROR", "服务内部错误，请稍后重试"))
        finally:
            db.close()

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/sessions/{session_id}/turns/voice")
def voice_turn_stream(session_id: str, audio: UploadFile = File(...), request_key: str = Form(""), user_id: str = Depends(get_user_id)):
    data = audio.file.read(int(settings.max_audio_mb * 1024 * 1024) + 1)

    def gen():
        deadline_at = time.monotonic() + 15
        db = SessionLocal()
        try:
            with deadline.until(deadline_at):
                partial = session_svc.submit_turn(db, session_id, user_id, "voice", audio_bytes=data, request_key=request_key)
            if partial.get("risk_hold"):
                yield _sse("risk_hold", partial)
                yield _sse("done", partial)  # 以 done 收尾
                return
            yield _sse("chunk", {"text": partial["reply_text"]})
            with deadline.until(deadline_at):
                done = session_svc.synthesize_turn_audio(db, session_id, user_id, partial["turn_id"])
            yield _sse("done", done)
        except AppError as exc:
            yield _sse("error", error_body(exc.code, exc.message))
        except Exception:
            yield _sse("error", error_body("INTERNAL_ERROR", "服务内部错误，请稍后重试"))
        finally:
            db.close()

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/sessions/{session_id}/turns/{turn_id}/discard-risk")
def discard_risk(session_id: str, turn_id: str, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return session_svc.discard_pending_risk(db, session_id, user_id, turn_id)
    finally:
        db.close()


@router.post("/sessions/{session_id}/turns/{turn_id}/confirm")
def confirm_turn(session_id: str, turn_id: str, req: ConfirmTurnRequest, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return session_svc.confirm_turn(db, session_id, user_id, turn_id, req.confirmed_text, req.action)
    finally:
        db.close()


@router.post("/sessions/{session_id}/turns/{turn_id}/audio")
def retry_turn_audio(session_id: str, turn_id: str, user_id: str = Depends(get_user_id)):
    """重新生成本回合语音（受鉴权保护、幂等）。会话结束后也可重试已有文字的音频。"""
    db = SessionLocal()
    try:
        return session_svc.synthesize_turn_audio(db, session_id, user_id, turn_id, retry_failed=True)
    finally:
        db.close()


@router.post("/sessions/{session_id}/drill-attempt")
def drill_attempt(session_id: str, req: DrillAttemptRequest, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return drill_svc.submit_drill_attempt(db, session_id, user_id, req.text, "text", req.attempt_key)
    finally:
        db.close()


@router.post("/sessions/{session_id}/drill-attempt/voice")
def drill_attempt_voice(session_id: str, audio: UploadFile = File(...), attempt_key: str = Form(""), user_id: str = Depends(get_user_id)):
    data = audio.file.read(int(settings.max_audio_mb * 1024 * 1024) + 1)
    db = SessionLocal()
    try:
        session_svc._require_session(db, session_id, user_id)
        return session_svc.submit_voice_intent(
            db, user_id, session_id, "drill_voice", session_id, attempt_key, data,
            lambda text: drill_svc.submit_drill_attempt(db, session_id, user_id, text, "voice", attempt_key),
        )
    finally:
        db.close()


@router.post("/sessions/{session_id}/drill/action")
def drill_action(session_id: str, req: DrillActionRequest, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return drill_svc.drill_action(db, session_id, user_id, req.action)
    finally:
        db.close()


@router.post("/sessions/{session_id}/practice", response_model=PracticeResponse)
def practice(session_id: str, sentence: str = Form(...), audio: UploadFile = File(...), request_key: str = Form(""), user_id: str = Depends(get_user_id)):
    data = audio.file.read(int(settings.max_audio_mb * 1024 * 1024) + 1)
    db = SessionLocal()
    try:
        return session_svc.submit_practice(db, session_id, user_id, data, sentence, request_key)
    finally:
        db.close()


@router.post("/sessions/{session_id}/end", response_model=EndResponse)
def end_session(session_id: str, req: EndRequest, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return session_svc.end_session(db, session_id, user_id, req.reason)
    finally:
        db.close()


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
def session_detail(session_id: str, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return session_svc.get_session_detail(db, session_id, user_id)
    finally:
        db.close()


@router.get("/sessions", response_model=list[SessionListItem])
def list_sessions(user_id: str = Depends(get_user_id), scene_id: str | None = None, series: str | None = None, status: str | None = None, practice_type: str | None = None, review_status: str | None = None):
    db = SessionLocal()
    try:
        filters = {k: v for k, v in {"scene_id": scene_id, "series": series, "status": status, "practice_type": practice_type, "review_status": review_status}.items() if v}
        return session_svc.list_sessions(db, user_id, filters)
    finally:
        db.close()


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        session_svc.delete_session(db, session_id, user_id)
        return {"deleted": True}
    finally:
        db.close()


@router.delete("/sessions")
def delete_all_sessions(user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        session_svc.delete_all_sessions(db, user_id)
        return {"deleted": True}
    finally:
        db.close()


@router.post("/sessions/{session_id}/pause")
def pause_session(session_id: str, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return session_svc.set_paused(db, session_id, user_id, True)
    finally:
        db.close()


@router.post("/sessions/{session_id}/resume")
def resume_session(session_id: str, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return session_svc.set_paused(db, session_id, user_id, False)
    finally:
        db.close()


@router.post("/sessions/{session_id}/skip-practice")
def skip_practice(session_id: str, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return session_svc.skip_practice(db, session_id, user_id)
    finally:
        db.close()


@router.post("/sessions/{session_id}/hint")
def request_hint(session_id: str, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return session_svc.request_hint(db, session_id, user_id)
    finally:
        db.close()


@router.post("/sessions/{session_id}/help")
def request_help(session_id: str, req: HelpRequest, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return help_svc.request_help(db, session_id, user_id, req.help_type, req.source_text)
    finally:
        db.close()


@router.patch("/sessions/{session_id}/turns/{turn_id}")
def revise_turn(session_id: str, turn_id: str, req: ReviseTurnRequest, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return session_svc.revise_turn(db, session_id, user_id, turn_id, req.revised_text)
    finally:
        db.close()


@router.post("/sessions/{session_id}/review/dispute")
def dispute_review(session_id: str, turn_id: str, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return session_svc.dispute_review_improvement(db, session_id, user_id, turn_id)
    finally:
        db.close()


@router.get("/users/me/memory", response_model=MemorySettingsResponse)
def memory_settings(user_id: str = Depends(get_user_id)):
    """读取已持久化的个人设置，刷新页面不改变用户选择。"""
    db = SessionLocal()
    try:
        return {"enabled": memory_svc.is_memory_enabled(db, user_id)}
    finally:
        db.close()


@router.post("/users/me/memory", response_model=MemorySettingsResponse)
def toggle_memory(req: MemoryToggleRequest, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        enabled = memory_svc.set_memory_enabled(db, user_id, req.enabled)
        return {"enabled": enabled}
    finally:
        db.close()


@router.get("/users/me/memories", response_model=list[MemoryItem])
def list_memories(user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        mems = memory_svc.list_memories(db, user_id)
        return [
            {"id": m.id, "text": m.text, "source_session_id": m.source_session_id, "status": m.status, "evidence_count": m.evidence_count, "expires_at": m.expires_at.isoformat() if m.expires_at else None, "scene_id": m.scene_id, "version": m.version}
            for m in mems
        ]
    finally:
        db.close()


@router.patch("/users/me/memories/{memory_id}")
def update_memory(memory_id: int, req: MemoryUpdateRequest, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        m = memory_svc.update_memory_text(db, user_id, memory_id, req.text)
        return {"id": m.id, "text": m.text, "status": m.status}
    finally:
        db.close()


@router.delete("/users/me/memories/{memory_id}")
def delete_memory(memory_id: int, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        memory_svc.delete_memory(db, user_id, memory_id)
        return {"deleted": True}
    finally:
        db.close()


@router.delete("/users/me/memories")
def clear_memories(user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        memory_svc.clear_memories(db, user_id)
        return {"deleted": True}
    finally:
        db.close()


@router.post("/review-queue", response_model=QueueItemResponse)
def add_to_queue(req: AddToQueueRequest, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return queue_svc.add_to_queue(db, user_id, req.source_session_id, req.source_turn_id)
    finally:
        db.close()


@router.get("/review-queue", response_model=list[QueueItemResponse])
def list_queue(user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return queue_svc.list_queue(db, user_id)
    finally:
        db.close()


@router.get("/review-queue/due", response_model=QueueItemResponse | None)
def due_item(user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return queue_svc.get_due_item(db, user_id)
    finally:
        db.close()


@router.get("/review-queue/due-today")
def due_today(user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return daily_review_svc.due_today(db, user_id)
    finally:
        db.close()


@router.post("/review-queue/daily-review/start")
def start_daily_review(request_key: str = Header("", alias="X-Request-Key"), user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return daily_review_svc.start_daily_review(db, user_id, request_key)
    finally:
        db.close()


@router.get("/review-queue/daily-review/{session_id}")
def daily_review_view(session_id: str, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        session = session_svc._require_session(db, session_id, user_id)
        cur = daily_review_svc.current_item(db, session, user_id)
        return {**cur, "summary": daily_review_svc.summary(db, session_id, user_id)}
    finally:
        db.close()


@router.post("/review-queue/daily-review/{session_id}/attempt")
def daily_review_attempt(session_id: str, req: DailyReviewAttemptRequest, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return daily_review_svc.submit_attempt(db, session_id, user_id, req.answer, "text", req.attempt_key)
    finally:
        db.close()


@router.post("/review-queue/daily-review/{session_id}/attempt/voice")
def daily_review_attempt_voice(session_id: str, audio: UploadFile = File(...), attempt_key: str = Form(""), user_id: str = Depends(get_user_id)):
    data = audio.file.read(int(settings.max_audio_mb * 1024 * 1024) + 1)
    db = SessionLocal()
    try:
        session_svc._require_session(db, session_id, user_id)
        return session_svc.submit_voice_intent(
            db, user_id, session_id, "daily_voice", session_id, attempt_key, data,
            lambda text: daily_review_svc.submit_attempt(db, session_id, user_id, text, "voice", attempt_key),
        )
    finally:
        db.close()


@router.post("/review-queue/daily-review/{session_id}/action")
def daily_review_action(session_id: str, req: DailyReviewActionRequest, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return daily_review_svc.control_action(db, session_id, user_id, req.action)
    finally:
        db.close()


@router.post("/phrase-cards")
def create_phrase_card(req: PhraseCardRequest, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return phrase_card_svc.generate_card(db, user_id, req.source_session_id, req.source_turn_id)
    finally:
        db.close()


@router.get("/phrase-cards")
def list_phrase_cards(user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return phrase_card_svc.list_cards(db, user_id)
    finally:
        db.close()


@router.post("/review-queue/{item_id}/retest", response_model=QueueItemResponse)
def retest(item_id: int, req: RetestRequest, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return queue_svc.submit_retest(db, user_id, item_id, req.answer, "text", req.attempt_key)
    finally:
        db.close()


@router.post("/review-queue/{item_id}/retest/voice", response_model=QueueItemResponse)
def retest_voice(item_id: int, audio: UploadFile = File(...), attempt_key: str = Form(""), user_id: str = Depends(get_user_id)):
    data = audio.file.read(int(settings.max_audio_mb * 1024 * 1024) + 1)
    db = SessionLocal()
    try:
        item = db.get(ReviewQueueItem, item_id)
        if item is None or item.user_id != user_id:
            raise AppError("NOT_FOUND", "队列项不存在", status_code=404)
        return session_svc.submit_voice_intent(
            db, user_id, item.source_session_id, "queue_voice", str(item_id), attempt_key, data,
            lambda text: queue_svc.submit_retest(db, user_id, item_id, text, "voice", attempt_key),
        )
    finally:
        db.close()


@router.post("/review-queue/{item_id}/reveal", response_model=QueueItemResponse)
def reveal_queue_item(item_id: int, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return queue_svc.reveal_item(db, user_id, item_id)
    finally:
        db.close()


@router.post("/review-queue/{item_id}/dispute", response_model=QueueItemResponse)
def dispute_queue_item(item_id: int, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return queue_svc.mark_disputed(db, user_id, item_id)
    finally:
        db.close()


@router.post("/review-queue/{item_id}/skip", response_model=QueueItemResponse)
def skip_queue_item(item_id: int, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return queue_svc.skip_item(db, user_id, item_id)
    finally:
        db.close()


@router.delete("/review-queue/{item_id}")
def delete_queue_item(item_id: int, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        queue_svc.delete_item(db, user_id, item_id)
        return {"deleted": True}
    finally:
        db.close()


@router.get("/progress")
def progress(user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return progress_svc.get_progress(db, user_id)
    finally:
        db.close()


@router.post("/training-plan", response_model=PlanResponse)
def create_plan(req: CreatePlanRequest, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return plan_svc.create_or_renew_plan(db, user_id, req.goal_type, req.available_minutes)
    finally:
        db.close()


@router.get("/training-plan", response_model=PlanResponse | None)
def get_plan(user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return plan_svc.get_plan(db, user_id)
    finally:
        db.close()


@router.post("/training-plan/{day}/skip", response_model=PlanResponse)
def skip_plan_day(day: int, user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return plan_svc.skip_day(db, user_id, day)
    finally:
        db.close()


@router.post("/training-plan/restart", response_model=PlanResponse)
def restart_plan(user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return plan_svc.restart_plan(db, user_id)
    finally:
        db.close()


@router.post("/training-plan/pause", response_model=PlanResponse)
def pause_plan(user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return plan_svc.pause_plan(db, user_id)
    finally:
        db.close()


@router.post("/training-plan/resume", response_model=PlanResponse)
def resume_plan(user_id: str = Depends(get_user_id)):
    db = SessionLocal()
    try:
        return plan_svc.resume_plan(db, user_id)
    finally:
        db.close()


@router.get("/qa/sessions")
def qa_sessions(user_id: str = Depends(get_qa_user_id), scene_id: str | None = None, status: str | None = None, version: str | None = None, input_type: str | None = None, help_type: str | None = None, eval_result: str | None = None, issue_type: str | None = None, error_code: str | None = None):
    db = SessionLocal()
    try:
        filters = {k: v for k, v in {"scene_id": scene_id, "status": status, "version": version, "input_type": input_type, "help_type": help_type, "eval_result": eval_result, "issue_type": issue_type, "error_code": error_code}.items() if v}
        return qa_svc.list_sessions(db, filters)
    finally:
        db.close()


@router.post("/qa/labels")
def qa_add_label(req: AddLabelRequest, user_id: str = Depends(get_qa_user_id)):
    db = SessionLocal()
    try:
        return qa_svc.add_label(db, user_id, req.session_id, req.turn_id, req.issue_type, req.conclusion)
    finally:
        db.close()


@router.get("/qa/labels/{label_id}/history")
def qa_label_history(label_id: int, user_id: str = Depends(get_qa_user_id)):
    db = SessionLocal()
    try:
        return qa_svc.label_history(db, label_id)
    finally:
        db.close()


@router.get("/qa/stats")
def qa_stats(user_id: str = Depends(get_qa_user_id)):
    db = SessionLocal()
    try:
        return qa_svc.stats(db)
    finally:
        db.close()


from pydantic import BaseModel, Field, StrictBool
from typing import Literal

class MediaEventRequest(BaseModel):
    action: Literal["demo_play","recording_play","record_start","record_cancel","record_retry","review_view"]
    request_key: str = Field(min_length=1,max_length=64)
    turn_id: str = Field(default="",max_length=64)

class ConversationReviewRequest(BaseModel):
    answered: StrictBool
    repeated_followup: StrictBool
    topic_jump: StrictBool

@router.post("/sessions/{session_id}/events")
def media_event(session_id: str, req: MediaEventRequest, user_id: str = Depends(get_user_id)):
    from app.services.events import record, MEDIA_ACTIONS
    db = SessionLocal()
    try:
        session_svc._require_session(db, session_id, user_id)
        req = req.model_dump()
        turn_id = req.get("turn_id", "")
        if turn_id:
            from app.models.entities import Turn
            turn = db.get(Turn, turn_id)
            if turn is None or turn.session_id != session_id:
                raise AppError("NOT_FOUND", "事件来源不存在", 404)
        record(db, session_id, req["action"], req["request_key"], turn_id)
        return {"recorded": True}
    finally:
        db.close()


@router.get("/qa/learning-metrics")
def qa_learning_metrics(reviewer: str = Depends(get_qa_user_id)):
    from app.services.learning_metrics import calculate
    db = SessionLocal()
    try:
        return calculate(db)
    finally:
        db.close()


@router.patch("/qa/conversation/{turn_id}")
def review_conversation(turn_id: str, req: ConversationReviewRequest, reviewer: str = Depends(get_qa_user_id)):
    db=SessionLocal()
    try:
        return qa_svc.review_conversation(db,reviewer,turn_id,req.model_dump())
    finally:
        db.close()

@router.get("/qa/conversation/{turn_id}/history")
def conversation_history(turn_id: str, reviewer: str = Depends(get_qa_user_id)):
    from app.models.entities import ConversationQualityReview
    db=SessionLocal()
    try:
        return [{"reviewer_id":r.reviewer_id,"conclusions":json.loads(r.conclusion_json),"prompt_version":r.prompt_version,"scenario_version":r.scenario_version,"created_at":r.created_at.isoformat()} for r in db.query(ConversationQualityReview).filter_by(turn_id=turn_id).order_by(ConversationQualityReview.id).all()]
    finally:
        db.close()
