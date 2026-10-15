"""今日复练组合（REQ-29，P1-2）Mock 测试。"""
import json
from datetime import datetime, timedelta, timezone

from app.models.database import SessionLocal
from app.models.entities import ReviewQueueItem

USER = {"X-Invite-Code": "test-a"}


def _create(client):
    r = client.post("/api/v1/sessions", json={"level": "basic", "difficulty": "standard"}, headers=USER)
    assert r.status_code == 200, r.text
    return r.json()


def _turn(client, sid, text):
    r = client.post(f"/api/v1/sessions/{sid}/turns/text", json={"text": text}, headers=USER)
    assert r.status_code == 200, r.text
    for block in r.text.split("\n\n"):
        if not block.strip():
            continue
        event, data = None, None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if event == "done" and data is not None:
            return data
    raise AssertionError("无 done")


def _make_queue_item(client):
    s = _create(client)
    _turn(client, s["session_id"], "just working")
    client.post(f"/api/v1/sessions/{s['session_id']}/end", json={"reason": "user_ended"}, headers=USER)
    detail = client.get(f"/api/v1/sessions/{s['session_id']}", headers=USER).json()
    imp = (detail.get("review") or {}).get("improvements", [])[0]
    r = client.post("/api/v1/review-queue", json={"source_session_id": s["session_id"], "source_turn_id": imp["turn_id"]}, headers=USER)
    assert r.status_code == 200, r.text
    return r.json(), imp["practice_sentence"]


def _make_due(turn_id):
    db = SessionLocal()
    try:
        item = db.query(ReviewQueueItem).filter_by(source_turn_id=turn_id).first()
        item.due_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()
    finally:
        db.close()


def test_due_today_only_due_items(client):
    item, _ = _make_queue_item(client)
    r = client.get("/api/v1/review-queue/due-today", headers=USER)
    assert r.json()["count"] == 0  # 未到期
    _make_due(item["source_turn_id"])
    r2 = client.get("/api/v1/review-queue/due-today", headers=USER)
    assert r2.json()["count"] == 1


def test_start_daily_review_idempotent(client):
    item, _ = _make_queue_item(client)
    _make_due(item["source_turn_id"])
    r1 = client.post("/api/v1/review-queue/daily-review/start", headers=USER).json()
    r2 = client.post("/api/v1/review-queue/daily-review/start", headers=USER).json()
    assert r1["session_id"] == r2["session_id"]
    assert len(r1["items"]) == 1


def _voice_attempt(client, session_id, transcript, monkeypatch):
    from app.services import session_svc

    class _ASR:
        def transcribe(self, audio_path):
            return transcript

    monkeypatch.setattr(session_svc, "get_asr_provider", lambda: _ASR())
    r = client.post(
        f"/api/v1/review-queue/daily-review/{session_id}/attempt/voice",
        files={"audio": ("a.webm", b"\x1aE\xdf\xa3" + b"\x00" * 100, "audio/webm")},
        headers=USER,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_submit_attempt_complete_advances(client, monkeypatch):
    item, sentence = _make_queue_item(client)
    _make_due(item["source_turn_id"])
    s = client.post("/api/v1/review-queue/daily-review/start", headers=USER).json()
    r = _voice_attempt(client, s["session_id"], sentence, monkeypatch)
    assert r["success"] is False and r["result"] == "recall_completed"
    with SessionLocal() as db:
        assert db.get(ReviewQueueItem, item["id"]).interval_days == 1
    from app.services import recall_service
    monkeypatch.setattr(recall_service, "verify_new_condition", lambda *args: True)
    r = _voice_attempt(client, s["session_id"], "At university I led a school project and finished it on time.", monkeypatch)
    assert r["success"] is True and r["result"] == "independent"
    assert r["next"]["done"] is True
    db = SessionLocal()
    try:
        q = db.query(ReviewQueueItem).filter_by(source_turn_id=item["source_turn_id"]).first()
        assert q.success_count == 1
        assert q.interval_days == 3  # 1 → 3
    finally:
        db.close()


def test_submit_attempt_with_help_resets_interval(client, monkeypatch):
    item, sentence = _make_queue_item(client)
    _make_due(item["source_turn_id"])
    s = client.post("/api/v1/review-queue/daily-review/start", headers=USER).json()
    # 看答案：服务端留痕
    client.post(f"/api/v1/review-queue/{item['id']}/reveal", headers=USER)
    _voice_attempt(client, s["session_id"], sentence, monkeypatch)
    db = SessionLocal()
    try:
        q = db.query(ReviewQueueItem).filter_by(source_turn_id=item["source_turn_id"]).first()
        assert q.interval_days == 1
        assert q.retest_result == "hinted"
    finally:
        db.close()


def test_skip_and_summary(client):
    item, _ = _make_queue_item(client)
    _make_due(item["source_turn_id"])
    s = client.post("/api/v1/review-queue/daily-review/start", headers=USER).json()
    client.post(
        f"/api/v1/review-queue/daily-review/{s['session_id']}/action",
        json={"action": "skip"},
        headers=USER,
    )
    view = client.get(f"/api/v1/review-queue/daily-review/{s['session_id']}", headers=USER).json()
    assert view["summary"]["skipped"] == 1
    assert view["done"] is True


def test_deleted_source_stops_active_daily_review(client):
    item, sentence = _make_queue_item(client)
    _make_due(item["source_turn_id"])
    started = client.post("/api/v1/review-queue/daily-review/start", headers=USER).json()
    assert started["session_id"]
    assert client.delete(f"/api/v1/sessions/{item['source_session_id']}", headers=USER).status_code == 200
    assert client.get("/api/v1/review-queue", headers=USER).json() == []
    view = client.get(f"/api/v1/review-queue/daily-review/{started['session_id']}", headers=USER).json()
    assert view["done"] is True
    assert view["summary"]["status"] == "ENDED"
    attempt = client.post(
        f"/api/v1/review-queue/daily-review/{started['session_id']}/attempt",
        json={"answer": sentence, "help_used": False}, headers=USER,
    )
    assert attempt.status_code == 409
