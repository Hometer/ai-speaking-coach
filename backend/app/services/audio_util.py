"""音频转码：用 ffmpeg 把浏览器录音转成 ASR/评测可用的 WAV/PCM（16kHz 单声道）。"""
import shutil
import subprocess
from pathlib import Path

from app.core.config import settings
from app.core.errors import AppError


def has_ffmpeg() -> bool:
    return shutil.which(settings.ffmpeg_bin) is not None


def transcode_to_wav_file(src: Path) -> Path:
    """转成 16kHz 单声道 16bit WAV，返回文件路径。"""
    _require_ffmpeg()
    dest = src.with_suffix(".wav")
    cmd = [
        settings.ffmpeg_bin, "-y", "-i", str(src),
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        "-f", "wav", str(dest),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise AppError("TRANSCODE_TIMEOUT", "音频转码超时", status_code=500)
    if proc.returncode != 0 or not dest.exists():
        raise AppError("TRANSCODE_FAILED", "音频无法转码，请重录或改用文字", status_code=422)
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
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise AppError("TRANSCODE_TIMEOUT", "音频转码超时", status_code=500)
    if proc.returncode != 0 or not proc.stdout:
        raise AppError("TRANSCODE_FAILED", "音频无法转码，请重录或改用文字", status_code=422)
    return proc.stdout


def _require_ffmpeg() -> None:
    if not has_ffmpeg():
        raise AppError("FFMPEG_MISSING", "音频转码组件缺失，请先安装 ffmpeg", status_code=500)
