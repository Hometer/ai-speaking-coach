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


def test_delete_source_scrubs_copied_learning_text(client):
    from app.models.database import SessionLocal
    from app.models.entities import PhraseCard, ReviewQueueItem, Session, Turn

    s = _create_session(client)
    _send_text(client, s["session_id"], "just working")
    imp = _end(client, s["session_id"])["review"]["improvements"][0]
    source_id = s["session_id"]
    turn_id = imp["turn_id"]
    queue = client.post("/api/v1/review-queue", json={"source_session_id": source_id, "source_turn_id": turn_id}, headers=USER)
    card = client.post("/api/v1/phrase-cards", json={"source_session_id": source_id, "source_turn_id": turn_id}, headers=USER)
    practice = client.post("/api/v1/sessions/practice-sentence", json={"source_session_id": source_id, "turn_id": turn_id}, headers=USER)
    assert queue.status_code == card.status_code == practice.status_code == 200

    assert client.delete(f"/api/v1/sessions/{source_id}", headers=USER).status_code == 200
    with SessionLocal() as db:
        item = db.get(ReviewQueueItem, queue.json()["id"])
        assert item is None
        saved_card = db.get(PhraseCard, card.json()["id"])
        assert saved_card is None
        sp = db.get(Session, practice.json()["session_id"])
        assert sp.source_invalid and not any((sp.practice_sentence, sp.practice_goal, sp.drill_target, sp.drill_meaning))
        opening = db.query(Turn).filter(Turn.session_id == sp.id).first()
        assert opening.reply_text == opening.practice_sentence == opening.reply_audio_url == ""


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


def test_memory_settings_persist_and_are_user_scoped(client):
    other = {"X-Invite-Code": "test-b"}
    assert client.get("/api/v1/users/me/memory").status_code == 401
    assert client.get("/api/v1/users/me/memory", headers=USER).json() == {"enabled": False}
    client.post("/api/v1/users/me/memory", json={"enabled": True}, headers=USER)
    # A fresh request/database session reads the saved setting, not a UI default.
    assert client.get("/api/v1/users/me/memory", headers=USER).json() == {"enabled": True}
    assert client.get("/api/v1/users/me/memory", headers=other).json() == {"enabled": False}
    client.post("/api/v1/users/me/memory", json={"enabled": False}, headers=USER)
    assert client.get("/api/v1/users/me/memory", headers=USER).json() == {"enabled": False}


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


def test_cleanup_expired_audio_clears_recording_and_reference(client):
    from datetime import datetime, timedelta, timezone

    from app.models.database import SessionLocal
    from app.models.entities import Turn
    from app.services.cleanup import cleanup_expired_audio

    s = _create_session(client)
    _send_text(client, s["session_id"], "just working")
    r = client.post(
        f"/api/v1/sessions/{s['session_id']}/practice",
        data={"sentence": "I have three years of experience."},
        files={"audio": ("a.webm", b"\x1aE\xdf\xa3" + b"\x00" * 100, "audio/webm")},
        headers=USER,
    )
    assert r.status_code == 200, r.text
    recording_url = r.json()["recording_url"]
    path = config.settings.data_dir / "audio" / recording_url.rsplit("/", 1)[-1]
    assert path.exists()
    with SessionLocal() as db:
        turn = db.query(Turn).filter(Turn.recording_url == recording_url).one()
        turn.created_at = datetime.now(timezone.utc) - timedelta(minutes=11)
        db.commit()

    cleanup_expired_audio()
    assert not path.exists()
    with SessionLocal() as db:
        turn = db.query(Turn).filter(Turn.session_id == s["session_id"], Turn.input_type == "practice").one()
        assert turn.recording_url == ""
        assert "recording_url" not in json.loads(turn.practice_eval)


def test_cleanup_expired_sessions_propagates_deletion(client):
    from datetime import datetime, timedelta, timezone

    from app.models.database import SessionLocal
    from app.models.entities import DailyReviewAttempt, DrillAttempt, ReviewQueueItem, Session
    from app.services.cleanup import cleanup_expired_sessions

    s = _create_session(client)
    _send_text(client, s["session_id"], "just working")
    imp = _end(client, s["session_id"])["review"]["improvements"][0]
    queue = client.post(
        "/api/v1/review-queue",
        json={"source_session_id": s["session_id"], "source_turn_id": imp["turn_id"]},
        headers=USER,
    )
    assert queue.status_code == 200
    with SessionLocal() as db:
        source = db.get(Session, s["session_id"])
        source.started_at = datetime.now(timezone.utc) - timedelta(days=31)
        db.add(DailyReviewAttempt(user_id=source.user_id, session_id=source.id, queue_item_id=queue.json()["id"], attempt_answer="private daily answer", result="failed"))
        db.add(DrillAttempt(user_id=source.user_id, source_session_id=source.id, source_turn_id=imp["turn_id"], drill_session_id=source.id, answer_text="private drill answer", visible_help="copied source", result="failed"))
        db.commit()

    assert cleanup_expired_sessions() == 1
    assert client.get(f"/api/v1/sessions/{s['session_id']}", headers=USER).status_code == 404
    with SessionLocal() as db:
        item = db.get(ReviewQueueItem, queue.json()["id"])
        assert item is None
        assert db.query(DailyReviewAttempt).filter(DailyReviewAttempt.session_id == s["session_id"]).count() == 0
        assert db.query(DrillAttempt).filter(DrillAttempt.drill_session_id == s["session_id"]).count() == 0
