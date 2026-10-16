"""短句跟读评测：示范 + 用户录音 + 评测（mock 固定结果 + 讯飞流式评测）。

讯飞评测使用 WebSocket 流式协议（wss://ise-api.xfyun.cn/v2/open-ise）：
- 握手鉴权：hmac-sha256 签名（host/date/request-line），authorization base64 编码；
- 参数帧：cmd="ssb"，data.status=0，携带参考文本与业务参数；
- 音频帧：cmd="auw"，aus=1（首帧）/2（中间）/4（末帧），data.status=1 携带 base64 音频；
- 结束：aus=4 时 data.status=2；
- 结果：服务端回传 JSON 帧，code=0 表示成功，data 内含 total_score。
"""
import base64
import hashlib
import hmac
import json
import math
import re
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from email.utils import formatdate
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from app.core.config import settings
from app.core.errors import AppError
from app.services.deadline import remaining
from app.services.audio_util import transcode_to_pcm_bytes

ISE_HOST = "ise-api.xfyun.cn"
ISE_PATH = "/v2/open-ise"
_AUDIO_CHUNK = 6400  # 每帧音频字节数（约 200ms @16kHz 16bit）


def build_auth_url(host: str, path: str, api_key: str, api_secret: str) -> str:
    """按讯飞官方规则生成带鉴权参数的 wss URL。"""
    date_str = formatdate(timeval=None, localtime=False, usegmt=True)
    request_line = f"GET {path} HTTP/1.1"
    signature_origin = f"host: {host}\ndate: {date_str}\n{request_line}"
    signature = base64.b64encode(
        hmac.new(api_secret.encode(), signature_origin.encode(), hashlib.sha256).digest()
    ).decode()
    authorization_origin = (
        f'api_key="{api_key}",algorithm="hmac-sha256",'
        f'headers="host date request-line",signature="{signature}"'
    )
    authorization = base64.b64encode(authorization_origin.encode()).decode()
    query = urlencode({"authorization": authorization, "date": date_str, "host": host})
    return f"wss://{host}{path}?{query}"


def _business(text: str, cmd: str, aus: int | None = None) -> dict[str, Any]:
    b: dict[str, Any] = {
        "sub": "ise",
        "ent": "en_vip",
        "category": "read_sentence",
        "aue": "raw",
        "auf": "audio/L16;rate=16000",
        "tte": "utf-8",
        "cmd": cmd,
        "text": text,
    }
    if aus is not None:
        b["aus"] = aus
    return b


def build_first_frame(app_id: str, text: str) -> dict[str, Any]:
    """业务参数帧（cmd=ssb，status=0）。"""
    return {
        "common": {"app_id": app_id},
        "business": _business(text, "ssb"),
        "data": {"status": 0, "data": ""},
    }


def build_audio_frame(app_id: str, text: str, aus: int, status: int, audio_b64: str) -> dict[str, Any]:
    """音频帧（cmd=auw）。"""
    return {
        "common": {"app_id": app_id},
        "business": _business(text, "auw", aus=aus),
        "data": {"status": status, "data": audio_b64},
    }


def _parse_ise_xml(xml_text: str) -> float | None:
    """解析讯飞返回的 XML 结果，提取 total_score（5 分制）。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    for node in root.iter():
        if node.tag.lower().split("}")[-1] not in ("read_chapter", "sentence", "read_sentence", "rec_paper"):
            continue
        ts = node.get("total_score")
        if ts is not None:
            try:
                value = float(ts)
                return value if math.isfinite(value) and 0 <= value <= 5 else None
            except ValueError:
                pass
    return None


def _extract_word_items(xml_text: str, reference_text: str | None = None) -> list[dict[str, Any]]:
    """从评测 XML 提取词级节点（含原始位置），低分优先，最多三处。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items: list[dict[str, Any]] = []
    for node in root.iter():
        if node.tag.lower().split("}")[-1] != "word":
            continue
        ts = node.get("total_score")
        if ts is None:
            continue
        word_text = "".join(node.itertext()).strip()
        if not word_text:
            continue
        try:
            score = float(ts)
            begin = int(node.get("beg_pos") or node.get("start") or "")
            end = int(node.get("end_pos") or node.get("end") or "")
            if not math.isfinite(score) or not 0 <= score <= 5 or begin < 0 or end <= begin:
                continue
            if reference_text is not None:
                # 只接受当前参考中可精确对应的字符范围；不猜供应商位置单位。
                span = reference_text[begin:end]
                norm = lambda x: re.sub(r"[^a-z']", "", x.casefold())
                if end > len(reference_text) or not norm(word_text) or norm(span) != norm(word_text):
                    continue
        except (ValueError, TypeError):
            continue
        items.append(
            {
                "word": word_text,
                "score": score,  # 5 分制原始
                "beg_pos": node.get("beg_pos") or node.get("start") or "",
                "end_pos": node.get("end_pos") or node.get("end") or "",
            }
        )
    items.sort(key=lambda x: x["score"])
    return items[:3]


def _xml_text_from_data(data: Any) -> str | None:
    """从返回帧 data 中提取 XML 文本（支持 base64 或原始字符串）。"""
    if data is None:
        return None
    if isinstance(data, str):
        if data.lstrip().startswith("<"):
            return data
        try:
            decoded = base64.b64decode(data).decode("utf-8", errors="ignore")
            if decoded.lstrip().startswith("<"):
                return decoded
        except Exception:  # noqa: BLE001
            return None
        return None
    if isinstance(data, dict):
        return _xml_text_from_data(data.get("data"))
    if isinstance(data, list):
        for v in data:
            xml = _xml_text_from_data(v)
            if xml:
                return xml
    return None


def _extract_ise_score(data: Any) -> float | None:
    """从返回帧的 data 中提取 total_score（讯飞 5 分制）。"""
    if data is None:
        return None
    if isinstance(data, (int, float)):
        return float(data)
    if isinstance(data, str):
        try:
            xml_text = base64.b64decode(data).decode("utf-8", errors="ignore")
            if xml_text.lstrip().startswith("<"):
                s = _parse_ise_xml(xml_text)
                if s is not None:
                    return s
        except Exception:  # noqa: BLE001
            pass
        if data.lstrip().startswith("<"):
            return _parse_ise_xml(data)
        return None
    if isinstance(data, dict):
        # 协议状态/耗时等数字不是整句分数。
        if "data" in data:
            return _extract_ise_score(data["data"])
        if "total_score" in data:
            try:
                return float(data["total_score"])
            except (ValueError, TypeError):
                return None
    return None


def parse_result(frames: list[dict[str, Any]], reference_text: str | None = None) -> dict[str, Any]:
    """解析服务端回传帧，返回 {score, dimension, notes, word_items}（score 为百分制）。"""
    error: str | None = None
    score: float | None = None
    word_items: list[dict[str, Any]] = []
    for f in frames:
        code = f.get("code")
        if code is not None and str(code) != "0":
            error = str(f.get("message") or f.get("desc") or f"code={code}")
        elif code in (0, "0"):
            s = _extract_ise_score(f.get("data"))
            if s is not None:
                score = s
            xml_text = _xml_text_from_data(f.get("data"))
            if xml_text:
                items = _extract_word_items(xml_text, reference_text)
                word_items = items
    if error:
        raise AppError("PRACTICE_EVAL_ERROR", "发音评测暂不可用，请稍后重试", status_code=502)
    if score is None or not math.isfinite(score) or not 0 <= score <= 5:
        raise AppError("PRACTICE_EVAL_EMPTY", "评测未返回分数", status_code=502)
    # 讯飞英文评测为 5 分制，换算为百分制展示
    return {"score": round(score * 20, 1), "dimension": "word/phrase" if word_items else "sentence", "notes": [], "word_items": word_items}


class PracticeEvalProvider(ABC):
    @abstractmethod
    def evaluate(self, audio_path: Path, reference_text: str) -> dict[str, Any]:
        """返回评测结果（服务支持范围内的维度）。"""


class MockPracticeEvalProvider(PracticeEvalProvider):
    def evaluate(self, audio_path: Path, reference_text: str) -> dict[str, Any]:
        # mock 只返回整句总分；无真实词级证据，word_items 恒为空（零容忍无证据词级结论）
        return {
            "score": 85,
            "dimension": "sentence",
            "word_items": [],
            "mock": True,
        }


class XfyunPracticeEvalProvider(PracticeEvalProvider):
    """科大讯飞英文短句评测（WebSocket 流式协议）。"""

    def __init__(self, appid: str, api_key: str, api_secret: str):
        self.appid = appid
        self.api_key = api_key
        self.api_secret = api_secret

    def evaluate(self, audio_path: Path, reference_text: str) -> dict[str, Any]:
        import websocket

        pcm = transcode_to_pcm_bytes(audio_path)
        if not pcm:
            raise AppError("PRACTICE_EVAL_ERROR", "音频为空，请重录", status_code=422)

        url = build_auth_url(ISE_HOST, ISE_PATH, self.api_key, self.api_secret)
        try:
            ws = websocket.create_connection(url, timeout=remaining())
        except Exception as exc:  # noqa: BLE001
            raise AppError("PRACTICE_EVAL_ERROR", "发音评测连接失败，请稍后重试", status_code=502) from exc

        try:
            ws.send(json.dumps(build_first_frame(self.appid, reference_text)))
            chunks = [pcm[i : i + _AUDIO_CHUNK] for i in range(0, len(pcm), _AUDIO_CHUNK)]
            if not chunks:
                chunks = [b""]
            n = len(chunks)
            for i, chunk in enumerate(chunks):
                if i == 0:
                    aus, status = 1, 1
                else:
                    aus, status = 2, 1
                ws.send(
                    json.dumps(
                        build_audio_frame(
                            self.appid, reference_text, aus, status,
                            base64.b64encode(chunk).decode(),
                        )
                    )
                )
            # 结束帧（aus=4，status=2，空数据）
            ws.send(json.dumps(build_audio_frame(self.appid, reference_text, 4, 2, "")))

            frames: list[dict[str, Any]] = []
            while True:
                try:
                    ws.settimeout(remaining())
                    msg = ws.recv()
                except websocket.WebSocketTimeoutException:
                    break
                except websocket.WebSocketConnectionClosedException:
                    break
                if not msg:
                    break
                try:
                    frames.append(json.loads(msg))
                except json.JSONDecodeError:
                    continue
            return parse_result(frames, reference_text)
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass


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
