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
