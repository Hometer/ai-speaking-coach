"""REQ-32 识别不确定性保护 测试。"""
import json

from app.services.asr_risk import assess_risk

USER = {"X-Invite-Code": "test-a"}


def test_assess_risk_empty():
    level, reasons = assess_risk("")
    assert level == "high" and "empty" in reasons


def test_assess_risk_short_exempt():
    assert assess_risk("Yes, please.")[0] == "low"
    assert assess_risk("water")[0] == "low"
    assert assess_risk("No thanks")[0] == "low"


def test_assess_risk_truncated():
    level, reasons = assess_risk("I have three years of experience in")
    assert level == "mid"
    assert "truncated" in reasons


def test_assess_risk_garbage():
    level, reasons = assess_risk("@@@###$$$%%%")
    assert level == "high"
    assert "garbage" in reasons


def test_assess_risk_normal():
    level, _ = assess_risk("I have three years of experience in software development.")
    assert level == "low"


def _sse_events(body):
    events = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event, data = None, None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if event and data is not None:
            events.append((event, data))
    return events


def test_voice_risk_hold_and_confirm(client, monkeypatch):
    from app.services import session_svc

    s = client.post("/api/v1/sessions", json={"level": "basic", "difficulty": "standard"}, headers=USER)
    sid = s.json()["session_id"]

    class TruncatedASR:
        def transcribe(self, audio_path):
            return "I have three years of experience in"

    monkeypatch.setattr(session_svc, "get_asr_provider", lambda: TruncatedASR())
    r = client.post(
        f"/api/v1/sessions/{sid}/turns/voice",
        files={"audio": ("a.webm", b"\x1aE\xdf\xa3" + b"\x00" * 100, "audio/webm")},
        headers=USER,
    )
    assert r.status_code == 200, r.text
    events = _sse_events(r.text)
    assert any(e == "risk_hold" for e, _ in events), "应有 risk_hold 事件"
    risk = next(d for e, d in events if e == "risk_hold")
    assert risk["risk_level"] == "mid"
    assert risk["transcript"] == "I have three years of experience in"
    assert risk.get("recording_url")  # 有听回录音
    assert any(e == "done" for e, _ in events)

    # 听回：录音可访问（非 404）
    rec = client.get(risk["recording_url"], headers=USER)
    assert rec.status_code == 200, rec.text
    # 他人不可访问
    assert client.get(risk["recording_url"], headers={"X-Invite-Code": "test-b"}).status_code in (403, 404)

    # 刷新恢复：pending_risk 持久化
    detail = client.get(f"/api/v1/sessions/{sid}", headers=USER).json()
    assert detail.get("pending_risk") and detail["pending_risk"]["turn_id"] == risk["turn_id"]

    # 确认（修订后继续）
    r2 = client.post(
        f"/api/v1/sessions/{sid}/turns/{risk['turn_id']}/confirm",
        json={"confirmed_text": "I have three years of experience in software development.", "action": "revise"},
        headers=USER,
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body.get("turn_id")
    assert body.get("eval_result") in ("independent", "hinted", "incomplete", "insufficient_evidence")
    assert client.get(risk["recording_url"], headers=USER).status_code == 404


def test_text_fallback_is_not_voice_independent(client, monkeypatch):
    from app.services import session_svc

    s = client.post("/api/v1/sessions", json={"level": "basic", "difficulty": "standard"}, headers=USER)
    sid = s.json()["session_id"]

    class TruncatedASR:
        def transcribe(self, audio_path):
            return "I have three years of experience in"

    monkeypatch.setattr(session_svc, "get_asr_provider", lambda: TruncatedASR())
    r = client.post(
        f"/api/v1/sessions/{sid}/turns/voice",
        files={"audio": ("a.webm", b"\x1aE\xdf\xa3" + b"\x00" * 100, "audio/webm")},
        headers=USER,
    )
    risk = next(d for e, d in _sse_events(r.text) if e == "risk_hold")
    # 改用文字 → 文字完成，不是语音独立
    r2 = client.post(
        f"/api/v1/sessions/{sid}/turns/{risk['turn_id']}/confirm",
        json={"confirmed_text": "I have three years of experience in software development.", "action": "text_fallback"},
        headers=USER,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["eval_result"] == "text_completed"


def test_pending_risk_recording_is_removed_on_rerecord_and_expiry(client, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app.core.config import settings
    from app.models.database import SessionLocal
    from app.models.entities import AsrRiskRecord, Session
    from app.services.cleanup import cleanup_old_recordings
    from app.services import session_svc

    class TruncatedASR:
        def transcribe(self, audio_path):
            return "I have three years of experience in"

    monkeypatch.setattr(session_svc, "get_asr_provider", lambda: TruncatedASR())
    sid = client.post("/api/v1/sessions", json={"level": "basic"}, headers=USER).json()["session_id"]

    def hold():
        r = client.post(
            f"/api/v1/sessions/{sid}/turns/voice",
            files={"audio": ("a.webm", b"\x1aE\xdf\xa3" + b"\x00" * 100, "audio/webm")},
            headers=USER,
        )
        return next(d for e, d in _sse_events(r.text) if e == "risk_hold")

    first = hold()
    filename = first["recording_url"].split("/")[-1]
    path = settings.data_dir / "audio" / filename
    assert path.is_file()
    assert client.post(f"/api/v1/sessions/{sid}/turns/{first['turn_id']}/discard-risk", headers=USER).status_code == 200
    assert not path.exists()
    assert client.get(f"/api/v1/sessions/{sid}", headers=USER).json()["pending_risk"] is None

    second = hold()
    path2 = settings.data_dir / "audio" / second["recording_url"].split("/")[-1]
    with SessionLocal() as db:
        risk = db.query(AsrRiskRecord).filter_by(turn_id=second["turn_id"]).first()
        risk.created_at = datetime.now(timezone.utc) - timedelta(days=2)
        db.commit()
    assert cleanup_old_recordings() >= 1
    assert not path2.exists()
    with SessionLocal() as db:
        assert db.get(Session, sid).pending_risk
    assert not client.get(f"/api/v1/sessions/{sid}", headers=USER).json()["pending_risk"].get("recording_url")


def test_turn_intent_key_replays_after_end_and_rejects_changed_content(client):
    sid = client.post("/api/v1/sessions", json={"level": "basic"}, headers=USER).json()["session_id"]
    payload = {"text": "I have three years of software experience.", "request_key": "turn-intent-1"}
    first = client.post(f"/api/v1/sessions/{sid}/turns/text", json=payload, headers=USER)
    assert first.status_code == 200
    client.post(f"/api/v1/sessions/{sid}/end", json={"reason": "user_ended"}, headers=USER)
    replay = client.post(f"/api/v1/sessions/{sid}/turns/text", json=payload, headers=USER)
    assert replay.status_code == 200
    assert replay.text.count("event: done") == 1
    conflict = client.post(f"/api/v1/sessions/{sid}/turns/text", json={"text": "Different", "request_key": "turn-intent-1"}, headers=USER)
    assert conflict.status_code == 200  # SSE 错误使用 error 事件
    assert "IDEMPOTENCY_CONFLICT" in conflict.text


def test_create_session_intent_key_checks_payload(client):
    req = {"level": "basic", "difficulty": "standard", "scene_id": "job_interview", "request_key": "create-intent-1"}
    first = client.post("/api/v1/sessions", json=req, headers=USER)
    assert first.status_code == 200
    assert client.post("/api/v1/sessions", json=req, headers=USER).json()["session_id"] == first.json()["session_id"]
    conflict = client.post("/api/v1/sessions", json={**req, "scene_id": "hotel_checkin"}, headers=USER)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_qa_recording_requires_separate_consent_and_revocation(client, monkeypatch):
    from app.core import config
    from app.core.config import settings
    from app.models.database import SessionLocal
    from app.models.entities import QaRecording
    from app.services import session_svc

    config.settings.qa_invite_codes = ["qa-a"]
    sid = client.post("/api/v1/sessions", json={"level": "basic"}, headers=USER).json()["session_id"]

    class TruncatedASR:
        def transcribe(self, audio_path):
            return "I have three years of experience in"

    monkeypatch.setattr(session_svc, "get_asr_provider", lambda: TruncatedASR())

    def send_voice():
        return client.post(
            f"/api/v1/sessions/{sid}/turns/voice",
            files={"audio": ("a.webm", b"\x1aE\xdf\xa3" + b"\x00" * 100, "audio/webm")},
            headers=USER,
        )

    first = send_voice()
    first_risk = next(d for e, d in _sse_events(first.text) if e == "risk_hold")
    with SessionLocal() as db:
        assert db.query(QaRecording).filter_by(session_id=sid).count() == 0
    client.post(f"/api/v1/sessions/{sid}/turns/{first_risk['turn_id']}/discard-risk", headers=USER)

    granted = client.post(f"/api/v1/sessions/{sid}/qa-recording-consent", json={"granted": True}, headers=USER)
    assert granted.status_code == 200 and granted.json()["granted"] is True
    second = send_voice()
    assert any(e == "risk_hold" for e, _ in _sse_events(second.text))
    with SessionLocal() as db:
        recording = db.query(QaRecording).filter_by(session_id=sid).one()
        recording_id, path = recording.id, settings.data_dir / "qa_audio" / recording.filename
    assert path.is_file()
    assert client.get(f"/api/v1/qa/recordings/{recording_id}", headers=USER).status_code == 403
    assert client.get(f"/api/v1/qa/recordings/{recording_id}", headers={"X-Invite-Code": "qa-a"}).status_code == 200
    log = client.get(f"/api/v1/qa/recordings/{recording_id}/access-log", headers={"X-Invite-Code": "qa-a"}).json()
    assert any(row["action"] == "view" and row["outcome"] == "allowed" for row in log)
    revoked = client.post(f"/api/v1/sessions/{sid}/qa-recording-consent", json={"granted": False}, headers=USER)
    assert revoked.json()["granted"] is False
    assert not path.exists()
    assert client.get(f"/api/v1/qa/recordings/{recording_id}", headers={"X-Invite-Code": "qa-a"}).status_code == 403
