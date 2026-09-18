"""配置读取：.env 与环境变量。本文件不记录、不打印任何密钥。"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录：backend/app/core/config.py -> core -> app -> backend -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")


class Settings:
    def __init__(self):
        self.provider_mode = os.getenv("PROVIDER_MODE", "mock").strip().lower()
        self.dashscope_api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
        self.model_asr = os.getenv("MODEL_ASR", "paraformer-v2")
        self.model_llm = os.getenv("MODEL_LLM", "qwen-plus")
        self.model_tts = os.getenv("MODEL_TTS", "cosyvoice-v1")
        self.tts_voice = os.getenv("TTS_VOICE", "Lois")
        self.max_audio_mb = float(os.getenv("MAX_AUDIO_MB", "10"))
        self.data_dir = (PROJECT_ROOT / "data").resolve()


settings = Settings()
