"""音频转码：用 ffmpeg 把浏览器录音转成 ASR/评测可用的 WAV/PCM（16kHz 单声道）。"""
import shutil
import subprocess
from pathlib import Path

from app.core.config import settings
from app.core.errors import AppError
from app.services.deadline import remaining


def has_ffmpeg() -> bool:
    return shutil.which(settings.ffmpeg_bin) is not None


def transcode_to_wav_file(src: Path) -> Path:
    """转成 16kHz 单声道 16bit WAV，返回文件路径。"""
    _require_ffmpeg()
    dest = src.with_suffix(".asr.wav")  # WAV 输入也必须与输出不同，禁止覆盖源文件
    cmd = [
        settings.ffmpeg_bin, "-y", "-i", str(src),
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        "-f", "wav", str(dest),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=remaining())
    except subprocess.TimeoutExpired:
        raise AppError("TRANSCODE_TIMEOUT", "音频转码超时", status_code=500)
    if proc.returncode != 0 or not dest.exists():
        raise AppError("TRANSCODE_FAILED", "音频无法转码，请重录或改用文字", status_code=422)
    try:
        import wave
        from app.services.provider_usage import set_audio_seconds
        with wave.open(str(dest), "rb") as audio:
            set_audio_seconds(audio.getnframes()/audio.getframerate())
    except (OSError, wave.Error):
        pass
    return dest


def transcode_to_pcm_bytes(src: Path) -> bytes:
    """转成 16kHz 单声道 16bit 原始 PCM 字节（供 base64 编码）。"""
    _require_ffmpeg()
    cmd = [
        settings.ffmpeg_bin, "-y", "-i", str(src),
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        "-f", "s16le", "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=remaining())
    except subprocess.TimeoutExpired:
        raise AppError("TRANSCODE_TIMEOUT", "音频转码超时", status_code=500)
    if proc.returncode != 0 or not proc.stdout:
        raise AppError("TRANSCODE_FAILED", "音频无法转码，请重录或改用文字", status_code=422)
    from app.services.provider_usage import set_audio_seconds
    set_audio_seconds(len(proc.stdout)/32000)
    return proc.stdout


def _require_ffmpeg() -> None:
    if not has_ffmpeg():
        raise AppError("FFMPEG_MISSING", "音频转码组件缺失，请先安装 ffmpeg", status_code=500)


def speech_seconds(src: Path) -> float | None:
    """20ms RMS 门限的可追溯本地语音活动时长，不是流利度/发音分数。"""
    try:
        from array import array
        import math
        pcm = transcode_to_pcm_bytes(src)
        samples = array("h", pcm)
        frames = [samples[i:i+320] for i in range(0, len(samples), 320)]
        return round(sum(len(frame)/16000 for frame in frames if frame and math.sqrt(sum(x*x for x in frame)/len(frame)) >= 200), 3)
    except (AppError, OSError, ValueError):
        return None
