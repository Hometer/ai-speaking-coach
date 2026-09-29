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
        self.model_asr = os.getenv("MODEL_ASR", "paraformer-realtime-v2")
        self.model_llm = os.getenv("MODEL_LLM", "qwen-plus")
        self.model_tts = os.getenv("MODEL_TTS", "cosyvoice-v2")
        self.tts_voice = os.getenv("TTS_VOICE", "loongabby_v2")
        self.max_audio_mb = float(os.getenv("MAX_AUDIO_MB", "10"))
        self.data_dir = (PROJECT_ROOT / "data").resolve()
        self.database_url = os.getenv(
            "DATABASE_URL", f"sqlite:///{self.data_dir / 'ai_speaking.db'}"
        )
        # 预算与运行上限（金额是设计预算，未实测报价）
        self.budget_session = float(os.getenv("BUDGET_SESSION", "3.00"))
        self.budget_user_day = float(os.getenv("BUDGET_USER_DAY", "10.00"))
        self.budget_project_day = float(os.getenv("BUDGET_PROJECT_DAY", "100.00"))
        self.budget_end_reserve = float(os.getenv("BUDGET_END_RESERVE", "0.30"))
        self.max_tools_per_turn = int(os.getenv("MAX_TOOLS_PER_TURN", "3"))
        self.max_tool_retry = int(os.getenv("MAX_TOOL_RETRY", "1"))
        self.llm_timeout_seconds = int(os.getenv("LLM_TIMEOUT_SECONDS", "15"))
        self.max_teach_adjust = int(os.getenv("MAX_TEACH_ADJUST", "2"))
        self.nominal_cost_per_turn = float(os.getenv("NOMINAL_COST_PER_TURN", "0.10"))
        self.upload_retention_minutes = float(os.getenv("UPLOAD_RETENTION_MINUTES", "10"))
        self.upload_max_age_minutes = float(os.getenv("UPLOAD_MAX_AGE_MINUTES", "1440"))
        self.session_retention_days = int(os.getenv("SESSION_RETENTION_DAYS", "30"))
        self.ffmpeg_bin = os.getenv("FFMPEG_BIN", "ffmpeg")
        self.xf_appid = os.getenv("XF_APPID", "")
        self.xf_api_key = os.getenv("XF_API_KEY", "")
        self.xf_api_secret = os.getenv("XF_API_SECRET", "")
        # 质检员邀请码（逗号分隔，默认空=无质检员）
        self.qa_invite_codes = [c.strip() for c in os.getenv("QA_INVITE_CODES", "").split(",") if c.strip()]


settings = Settings()
