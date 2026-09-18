"""API 路由。"""
from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse

from app.schemas.turn import TextTurnRequest, TurnResponse
from app.services.turn import handle_text_turn, handle_voice_turn, resolve_audio_path

router = APIRouter(prefix="/api/v1")

_AUDIO_MEDIA = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "webm": "audio/webm",
    "mp4": "audio/mp4",
    "ogg": "audio/ogg",
}


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
