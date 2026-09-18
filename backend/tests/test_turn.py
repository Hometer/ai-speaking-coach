"""阶段 2.1 mock 自动化测试（离线可跑）。"""
import pytest
from fastapi.testclient import TestClient

from app.core import config
from app.main import app

# 固定 mock 模式，避免测试读取真实 Key
config.settings.provider_mode = "mock"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "data_dir", tmp_path / "data")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _fake_webm() -> bytes:
    # EBML 魔数头 + 少量填充，满足魔数识别
    return b"\x1aE\xdf\xa3" + b"\x00" * 200


def test_text_turn_returns_reply(client):
    r = client.post("/api/v1/turn/text", json={"text": "I am a software engineer."})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["request_id"]
    assert data["reply_text"]
    assert data["reply_audio_url"].startswith("/api/v1/audio/")


def test_voice_turn_returns_transcript_and_reply(client):
    r = client.post(
        "/api/v1/turn/voice",
        files={"audio": ("a.webm", _fake_webm(), "audio/webm")},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["transcript"]
    assert data["reply_text"]
    assert data["reply_audio_url"]


def test_voice_rejects_bad_magic(client):
    r = client.post(
        "/api/v1/turn/voice",
        files={"audio": ("a.webm", b"not-an-audio", "audio/webm")},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "UNSUPPORTED_AUDIO"


def test_voice_rejects_oversize(client, monkeypatch):
    monkeypatch.setattr(config.settings, "max_audio_mb", 0.0)  # 任何非空都超限
    r = client.post(
        "/api/v1/turn/voice",
        files={"audio": ("a.webm", _fake_webm(), "audio/webm")},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_text_validation_error_structure(client):
    r = client.post("/api/v1/turn/text", json={"text": ""})
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "message" in body["error"]


def test_text_too_long(client):
    r = client.post("/api/v1/turn/text", json={"text": "x" * 2001})
    assert r.status_code == 422


def test_audio_not_found(client):
    r = client.get("/api/v1/audio/nope.wav")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_audio_playable_after_turn(client):
    r = client.post("/api/v1/turn/text", json={"text": "hello"})
    url = r.json()["reply_audio_url"]
    r2 = client.get(url)
    assert r2.status_code == 200
    # mock TTS 输出为 wav
    assert r2.content.startswith(b"RIFF")


def test_unhandled_error_is_uniform_and_hides_details(client, monkeypatch):
    import app.api.routes as routes

    def boom(_text):
        raise RuntimeError("secret-stack-detail-should-not-leak")

    monkeypatch.setattr(routes, "handle_text_turn", boom)
    r = client.post("/api/v1/turn/text", json={"text": "hi"})
    assert r.status_code == 500
    body = r.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "secret-stack-detail-should-not-leak" not in r.text
