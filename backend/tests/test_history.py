"""阶段 2.3：回顾 / 历史 / 记忆 / 清理 mock 测试。"""
import json
import os
import time

from app.core import config

USER = {"X-Invite-Code": "test-a"}


def _create_session(client, user="test-a"):
    r = client.post("/api/v1/sessions", json={"level": "basic", "difficulty": "standard"}, headers={"X-Invite-Code": user})
    assert r.status_code == 200, r.text
    return r.json()


def _send_text(client, session_id, text, user="test-a"):
    r = client.post(f"/api/v1/sessions/{session_id}/turns/text", json={"text": text}, headers={"X-Invite-Code": user})
    assert r.status_code == 200, r.text
    return r.text


def _end(client, session_id, user="test-a"):
    r = client.post(f"/api/v1/sessions/{session_id}/end", json={"reason": "user_ended"}, headers={"X-Invite-Code": user})
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


def test_review_keeps_distinct_originals(client):
    s = _create_session(client)
    _send_text(client, s["session_id"], "heii I")
    _send_text(client, s["session_id"], "heiil")
    review = _end(client, s["session_id"])["review"]
    # 不同原句各自保留证据，不因模板相同而合并
    assert len(review["improvements"]) == 2
    originals = {imp["original"] for imp in review["improvements"]}
    assert originals == {"heii I", "heiil"}
    assert review["input_breakdown"]["text"] == 2


def test_request_hint(client):
    s = _create_session(client)
    r = client.post(f"/api/v1/sessions/{s['session_id']}/hint", headers=USER)
    assert r.status_code == 200
    assert r.json()["action_type"] == "hint"


def test_dispute_review_improvement(client):
    s = _create_session(client)
    _send_text(client, s["session_id"], "just working")
    review = _end(client, s["session_id"])["review"]
    imp = review["improvements"][0]
    r = client.post(f"/api/v1/sessions/{s['session_id']}/review/dispute", params={"turn_id": imp["turn_id"]}, headers=USER)
    assert r.status_code == 200
    detail = client.get(f"/api/v1/sessions/{s['session_id']}", headers=USER).json()
    assert any(d.get("disputed") for d in detail["review"]["improvements"])


def test_saved_review_dedups_duplicate_original(client):
    from app.models.database import SessionLocal
    from app.models.entities import Session

    s = _create_session(client)
    _send_text(client, s["session_id"], "heii I")
    review = _end(client, s["session_id"])["review"]
    imp = review["improvements"][0]
    # 保存一份含重复原句的旧回顾，展示时应合并
    with SessionLocal() as db:
        session = db.get(Session, s["session_id"])
        session.review = json.dumps({**review, "improvements": [imp, dict(imp)]})
        db.commit()

    detail = client.get(f"/api/v1/sessions/{s['session_id']}", headers=USER).json()
    assert len(detail["review"]["improvements"]) == 1


def test_history_list_only_own(client):
    a = _create_session(client, user="test-a")
    _create_session(client, user="test-b")
    r = client.get("/api/v1/sessions", headers={"X-Invite-Code": "test-a"})
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


def test_create_sentence_practice(client):
    s = _create_session(client)
    _send_text(client, s["session_id"], "just working")
    review = _end(client, s["session_id"])["review"]
    imp = review["improvements"][0]
    r = client.post(
        "/api/v1/sessions/practice-sentence",
        json={"source_session_id": s["session_id"], "turn_id": imp["turn_id"]},
        headers=USER,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "PRACTICE"
    # 幂等：重复创建返回同一会话
    r2 = client.post(
        "/api/v1/sessions/practice-sentence",
        json={"source_session_id": s["session_id"], "turn_id": imp["turn_id"]},
        headers=USER,
    )
    assert r2.json()["session_id"] == data["session_id"]


def test_source_dispute_invalidates_sentence_practice(client):
    s = _create_session(client)
    _send_text(client, s["session_id"], "just working")
    review = _end(client, s["session_id"])["review"]
    imp = review["improvements"][0]
    sp = client.post(
        "/api/v1/sessions/practice-sentence",
        json={"source_session_id": s["session_id"], "turn_id": imp["turn_id"]},
        headers=USER,
    ).json()
    client.post(f"/api/v1/sessions/{s['session_id']}/review/dispute", params={"turn_id": imp["turn_id"]}, headers=USER)
    detail = client.get(f"/api/v1/sessions/{sp['session_id']}", headers=USER).json()
    assert detail["source_invalid"] is True


def test_delete_all_removes_all_records(client):
    from app.models.database import SessionLocal
    from app.models.entities import EventLog, Evaluation, Turn

    s = _create_session(client)
    _send_text(client, s["session_id"], "I have three years of experience in software development.")
    _end(client, s["session_id"])
    assert client.delete("/api/v1/sessions", headers=USER).status_code == 200
    db = SessionLocal()
    try:
        assert db.query(Turn).filter(Turn.session_id == s["session_id"]).count() == 0
        assert db.query(Evaluation).filter(Evaluation.session_id == s["session_id"]).count() == 0
        assert db.query(EventLog).filter(EventLog.session_id == s["session_id"]).count() == 0
    finally:
        db.close()


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
