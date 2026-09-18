"""阶段 2.2：会话/状态机/Harness mock 测试。"""
import json

from app.core import config
from app.models.database import SessionLocal

USER = {"X-User-Id": "u1"}


def _create_session(client, user="u1", level="basic", difficulty="standard"):
    r = client.post(
        "/api/v1/sessions",
        json={"level": level, "difficulty": difficulty},
        headers={"X-User-Id": user},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _send_text(client, session_id, text, user="u1"):
    r = client.post(
        f"/api/v1/sessions/{session_id}/turns/text",
        json={"text": text},
        headers={"X-User-Id": user},
    )
    assert r.status_code == 200, r.text
    return _parse_sse(r.text)


def _parse_sse(body):
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


def _done(events):
    return events[-1][1]


def test_create_session_returns_goal_and_opening(client):
    s = _create_session(client)
    assert s["status"] == "WAITING_INPUT"
    assert s["goal_text"]
    assert s["reply_text"]
    assert s["reply_audio_url"].startswith("/api/v1/audio/")


def test_turn_independent_leads_to_recheck(client):
    s = _create_session(client)
    events = _send_text(client, s["session_id"], "I have three years of experience in software development.")
    types = [e for e, _ in events]
    assert "chunk" in types and "done" in types
    done = _done(events)
    assert done["eval_result"] == "independent"
    assert done["action_type"] == "recheck"
    assert done["status"] == "RECHECK"


def test_insufficient_answer_gets_hint(client):
    s = _create_session(client)
    done = _done(_send_text(client, s["session_id"], "hi"))
    assert done["eval_result"] == "insufficient_evidence"
    assert done["action_type"] == "hint"


def test_hinted_then_independent_recheck_ends_session(client):
    s = _create_session(client)
    _send_text(client, s["session_id"], "hi")  # → hint
    e2 = _send_text(client, s["session_id"], "I have worked as a teacher for two years.")
    done2 = _done(e2)
    assert done2["eval_result"] == "hinted"
    assert done2["action_type"] == "recheck"
    e3 = _send_text(client, s["session_id"], "I have five years of experience in marketing and I enjoy it.")
    done3 = _done(e3)
    assert done3["eval_result"] == "independent"
    assert done3["status"] == "ENDED"


def test_incomplete_answer_suggests_practice(client):
    s = _create_session(client)
    done = _done(_send_text(client, s["session_id"], "just working"))
    assert done["eval_result"] == "incomplete"
    assert done["action_type"] == "suggest_practice"
    assert done["status"] == "PRACTICE"
    assert done["practice_sentence"]


def test_practice_flow(client):
    s = _create_session(client)
    _send_text(client, s["session_id"], "just working")  # → PRACTICE
    r = client.post(
        f"/api/v1/sessions/{s['session_id']}/practice",
        data={"sentence": "I have three years of experience."},
        files={"audio": ("a.webm", b"\x1aE\xdf\xa3" + b"\x00" * 100, "audio/webm")},
        headers=USER,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "RECHECK"
    assert "score" in data["eval"]
    assert data["recheck_reply_text"]


def test_practice_invalid_state_rejected(client):
    s = _create_session(client)
    r = client.post(
        f"/api/v1/sessions/{s['session_id']}/practice",
        data={"sentence": "x"},
        files={"audio": ("a.webm", b"\x1aE\xdf\xa3" + b"\x00" * 100, "audio/webm")},
        headers=USER,
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INVALID_STATE"


def test_cross_user_denied(client):
    s = _create_session(client, user="u1")
    r = client.get(f"/api/v1/sessions/{s['session_id']}", headers={"X-User-Id": "u2"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_end_session_returns_summary(client):
    s = _create_session(client)
    _send_text(client, s["session_id"], "I have three years of experience in software development.")
    r = client.post(
        f"/api/v1/sessions/{s['session_id']}/end",
        json={"reason": "user_ended"},
        headers=USER,
    )
    assert r.status_code == 200, r.text
    summary = r.json()["summary"]
    assert summary["status"] == "ENDED"
    assert summary["end_reason"] == "user_ended"
    assert summary["turn_count"] >= 1


def test_turn_after_end_rejected(client):
    s = _create_session(client)
    client.post(f"/api/v1/sessions/{s['session_id']}/end", json={"reason": "user_ended"}, headers=USER)
    r = client.post(
        f"/api/v1/sessions/{s['session_id']}/turns/text",
        json={"text": "hello"},
        headers=USER,
    )
    events = _parse_sse(r.text)
    assert events[-1][0] == "error"
    assert events[-1][1]["error"]["code"] == "SESSION_ENDED"


def test_persistence_across_session(client):
    s = _create_session(client, user="u1")
    _send_text(client, s["session_id"], "I have three years of experience in software development.")
    db = SessionLocal()
    try:
        from app.models.entities import Session as SessionModel

        sess = db.get(SessionModel, s["session_id"])
        assert sess is not None
        assert sess.status in ("RECHECK", "WAITING_INPUT", "ENDED")
        assert len(sess.turns) >= 1
    finally:
        db.close()


def test_budget_exceeded(client, monkeypatch):
    monkeypatch.setattr(config.settings, "budget_session", 0.25)
    s = _create_session(client)  # 会话创建占 0.10
    _send_text(client, s["session_id"], "I have three years of experience in software development.")  # 回合 0.10
    r = client.post(
        f"/api/v1/sessions/{s['session_id']}/turns/text",
        json={"text": "more"},
        headers=USER,
    )
    events = _parse_sse(r.text)
    assert events[-1][0] == "error"
    assert events[-1][1]["error"]["code"] == "BUDGET_EXCEEDED"
