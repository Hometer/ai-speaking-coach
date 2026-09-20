"""阶段 2.1 旧无状态接口已移除；本文件验证旧接口不可用与音频访问控制。"""
import pytest
from fastapi.testclient import TestClient

from app.core import config
from app.main import app

config.settings.provider_mode = "mock"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "data_dir", tmp_path / "data")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_old_turn_text_removed(client):
    r = client.post("/api/v1/turn/text", json={"text": "hello"})
    assert r.status_code == 404


def test_old_turn_voice_removed(client):
    r = client.post(
        "/api/v1/turn/voice",
        files={"audio": ("a.webm", b"\x1aE\xdf\xa3" + b"\x00" * 100, "audio/webm")},
    )
    assert r.status_code == 404


def test_audio_requires_invite(client):
    r = client.get("/api/v1/audio/whatever.mp3")
    assert r.status_code == 401


def test_audio_ownership_denied(client):
    # user-a 建会话拿到音频 URL
    s = client.post(
        "/api/v1/sessions",
        json={"level": "basic", "difficulty": "standard"},
        headers={"X-Invite-Code": "test-a"},
    ).json()
    audio_url = s["reply_audio_url"]
    # user-b 访问该音频 → 403
    assert client.get(audio_url, headers={"X-Invite-Code": "test-b"}).status_code == 403
    # user-a 访问 → 200
    assert client.get(audio_url, headers={"X-Invite-Code": "test-a"}).status_code == 200
