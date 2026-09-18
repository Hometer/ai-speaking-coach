"""阶段 2.3：回顾 / 历史 / 记忆 / 清理 mock 测试。"""
import json
import os
import time

from app.core import config

USER = {"X-User-Id": "u1"}


def _create_session(client, user="u1"):
    r = client.post("/api/v1/sessions", json={"level": "basic", "difficulty": "standard"}, headers={"X-User-Id": user})
    assert r.status_code == 200, r.text
    return r.json()


def _send_text(client, session_id, text, user="u1"):
    r = client.post(f"/api/v1/sessions/{session_id}/turns/text", json={"text": text}, headers={"X-User-Id": user})
    assert r.status_code == 200, r.text
    return r.text


def _end(client, session_id, user="u1"):
    r = client.post(f"/api/v1/sessions/{session_id}/end", json={"reason": "user_ended"}, headers={"X-User-Id": user})
    assert r.status_code == 200, r.text
    return r.json()


def test_end_generates_review(client):
    s = _create_session(client)
    _send_text(client, s["session_id"], "just working")  # incomplete
    data = _end(client, s["session_id"])
    review = data["review"]
    assert review["goal_status"] == "incomplete"
    assert review["improvements"]  # 至少一条改进建议
    assert "ratings" in review
    assert review["input_breakdown"]["text"] == 1
    assert review["end_reason"] == "user_ended"
    # 幂等：再次 end 返回相同回顾
    data2 = _end(client, s["session_id"])
    assert data2["review"] == review


def test_history_list_only_own(client):
    a = _create_session(client, user="u1")
    _create_session(client, user="u2")
    r = client.get("/api/v1/sessions", headers={"X-User-Id": "u1"})
    assert r.status_code == 200
    ids = [x["session_id"] for x in r.json()]
    assert a["session_id"] in ids
    assert len(ids) == 1


def test_session_detail_includes_review(client):
    s = _create_session(client)
    _send_text(client, s["session_id"], "just working")
    _end(client, s["session_id"])
    r = client.get(f"/api/v1/sessions/{s['session_id']}", headers=USER)
    assert r.status_code == 200
    detail = r.json()
    assert detail["review"] is not None
    assert detail["turns"]


def test_delete_session_cascades(client):
    s = _create_session(client)
    _send_text(client, s["session_id"], "just working")
    _end(client, s["session_id"])
    r = client.delete(f"/api/v1/sessions/{s['session_id']}", headers=USER)
    assert r.status_code == 200, r.text
    r2 = client.get(f"/api/v1/sessions/{s['session_id']}", headers=USER)
    assert r2.status_code == 404


def test_memory_default_off(client):
    s = _create_session(client)
    _send_text(client, s["session_id"], "just working")
    _end(client, s["session_id"])
    r = client.get("/api/v1/users/me/memories", headers=USER)
    assert r.status_code == 200
    assert r.json() == []


def test_memory_enabled_generates_candidates(client):
    client.post("/api/v1/users/me/memory", json={"enabled": True}, headers=USER)
    s = _create_session(client)
    _send_text(client, s["session_id"], "just working")
    _end(client, s["session_id"])
    r = client.get("/api/v1/users/me/memories", headers=USER)
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_memory_clear_and_delete(client):
    client.post("/api/v1/users/me/memory", json={"enabled": True}, headers=USER)
    s = _create_session(client)
    _send_text(client, s["session_id"], "just working")
    _end(client, s["session_id"])
    mems = client.get("/api/v1/users/me/memories", headers=USER).json()
    mid = mems[0]["id"]
    # 删除单条
    client.delete(f"/api/v1/users/me/memories/{mid}", headers=USER)
    assert client.get("/api/v1/users/me/memories", headers=USER).json() == []
    # 再生成并清空
    s2 = _create_session(client)
    _send_text(client, s2["session_id"], "just working")
    _end(client, s2["session_id"])
    client.delete("/api/v1/users/me/memories", headers=USER)
    assert client.get("/api/v1/users/me/memories", headers=USER).json() == []


def test_delete_source_session_propagates_to_memory(client):
    client.post("/api/v1/users/me/memory", json={"enabled": True}, headers=USER)
    s = _create_session(client)
    _send_text(client, s["session_id"], "just working")
    _end(client, s["session_id"])
    assert len(client.get("/api/v1/users/me/memories", headers=USER).json()) >= 1
    client.delete(f"/api/v1/sessions/{s['session_id']}", headers=USER)
    assert client.get("/api/v1/users/me/memories", headers=USER).json() == []


def test_cleanup_old_uploads():
    from app.services.cleanup import cleanup_old_uploads

    uploads = config.settings.data_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    old = uploads / "old.webm"
    old.write_bytes(b"x")
    recent = uploads / "recent.webm"
    recent.write_bytes(b"x")
    old_time = time.time() - 3600
    os.utime(old, (old_time, old_time))

    cleanup_old_uploads(max_age_minutes=10)
    assert not old.exists()
    assert recent.exists()
