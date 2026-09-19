"""短句跟读评测：示范 + 用户录音 + 评测（mock 固定结果 + 讯飞真实接入）。"""
import base64
import hashlib
import json
import time
import urllib.parse
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.errors import AppError
from app.services.audio_util import transcode_to_pcm_bytes


class PracticeEvalProvider(ABC):
    @abstractmethod
    def evaluate(self, audio_path: Path, reference_text: str) -> dict[str, Any]:
        """返回评测结果（服务支持范围内的维度）。"""


class MockPracticeEvalProvider(PracticeEvalProvider):
    def evaluate(self, audio_path: Path, reference_text: str) -> dict[str, Any]:
        return {
            "score": 85,
            "dimension": "word/phrase",
            "notes": [{"word": "experience", "suggestion": "stress the second syllable"}],
            "mock": True,
        }


class XfyunPracticeEvalProvider(PracticeEvalProvider):
    """科大讯飞英文短句评测（ISE WebAPI）。"""

    def __init__(self, appid: str, api_key: str, api_secret: str):
        self.appid = appid
        self.api_key = api_key
        self.api_secret = api_secret

    def evaluate(self, audio_path: Path, reference_text: str) -> dict[str, Any]:
        import requests

        pcm = transcode_to_pcm_bytes(audio_path)
        audio_b64 = base64.b64encode(pcm).decode()

        cur_time = str(int(time.time()))
        param = {
            "category": "read_sentence",
            "ent": "en_vip",
            "aue": "raw",
            "auf": "audio/L16;rate=16000",
            "rstcd": "utf8",
            "tte": "utf8",
            "result_level": "complete",
        }
        param_b64 = base64.b64encode(json.dumps(param).encode()).decode()
        checksum = hashlib.md5((self.api_key + cur_time + param_b64).encode()).hexdigest()

        headers = {
            "X-Appid": self.appid,
            "X-CurTime": cur_time,
            "X-Param": param_b64,
            "X-CheckSum": checksum,
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        }
        body = urllib.parse.urlencode({"audio": audio_b64, "text": reference_text}).encode("utf-8")

        try:
            resp = requests.post(
                "https://ise-api.xfyun.cn/v2/open-ise",
                headers=headers,
                data=body,
                timeout=15,
            )
        except requests.RequestException as exc:
            raise AppError("PRACTICE_EVAL_ERROR", f"评测请求失败：{str(exc)[:100]}", status_code=502)

        try:
            data = resp.json()
        except ValueError:
            raise AppError("PRACTICE_EVAL_ERROR", "评测返回格式异常", status_code=502)

        code = data.get("code")
        if str(code) != "0":
            msg = data.get("desc") or data.get("message") or f"code={code}"
            raise AppError("PRACTICE_EVAL_ERROR", f"评测失败：{str(msg)[:100]}", status_code=502)

        return _parse_ise_result(data)


def _parse_ise_result(data: dict[str, Any]) -> dict[str, Any]:
    d = data.get("data") or {}
    total = d.get("total_score")
    if total is None and isinstance(d, dict):
        # 部分返回把分数嵌套在 reading 里，做一次浅层兜底
        for v in d.values():
            if isinstance(v, dict) and v.get("total_score") is not None:
                total = v["total_score"]
                break
    return {
        "score": round(float(total), 1) if total is not None else None,
        "dimension": "word/phrase",
        "notes": [],
    }


def get_practice_eval_provider() -> PracticeEvalProvider:
    if settings.provider_mode == "mock":
        return MockPracticeEvalProvider()
    if settings.provider_mode == "real":
        if not (settings.xf_appid and settings.xf_api_key and settings.xf_api_secret):
            raise AppError("MISSING_API_KEY", "未配置讯飞 APPID/APIKey/APISecret", status_code=500)
        return XfyunPracticeEvalProvider(
            settings.xf_appid, settings.xf_api_key, settings.xf_api_secret
        )
    raise AppError("CONFIG_ERROR", "未知的 PROVIDER_MODE，应为 mock 或 real", status_code=500)
