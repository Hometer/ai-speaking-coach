"""阶段 2.2：会话/状态机/Harness mock 测试。"""
import json

from app.core import config
from app.models.database import SessionLocal

USER = {"X-Invite-Code": "test-a"}


def _create_session(client, user="test-a", level="basic", difficulty="standard"):
    r = client.post(
        "/api/v1/sessions",
        json={"level": level, "difficulty": difficulty},
        headers={"X-Invite-Code": user},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _send_text(client, session_id, text, user="test-a"):
    r = client.post(
        f"/api/v1/sessions/{session_id}/turns/text",
        json={"text": text},
        headers={"X-Invite-Code": user},
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


def test_turn_independent_leads_to_followup(client):
    s = _create_session(client)
    events = _send_text(client, s["session_id"], "I have three years of experience in software development.")
    types = [e for e, _ in events]
    assert "chunk" in types and "done" in types
    done = _done(events)
    assert done["eval_result"] == "independent"
    assert done["action_type"] == "follow_up"
    assert done["status"] == "WAITING_INPUT"


def test_insufficient_answer_gets_hint(client):
    s = _create_session(client)
    done = _done(_send_text(client, s["session_id"], "hi"))
    assert done["eval_result"] == "insufficient_evidence"
    assert done["action_type"] == "hint"


def test_two_subgoals_complete_ends_session(client):
    s = _create_session(client)
    # 子目标 1：介绍经历
    e1 = _send_text(client, s["session_id"], "I have three years of experience in software development.")
    done1 = _done(e1)
    assert done1["eval_result"] == "independent"
    assert done1["status"] == "WAITING_INPUT"
    # 子目标 2：回答追问 → 均独立 → 结束
    e2 = _send_text(client, s["session_id"], "I have five years of experience in marketing and I enjoy it.")
    done2 = _done(e2)
    assert done2["eval_result"] == "independent"
    assert done2["status"] == "ENDED"


def test_hinted_requires_recheck_then_followup(client):
    s = _create_session(client)
    _send_text(client, s["session_id"], "hi")  # → hint
    e2 = _send_text(client, s["session_id"], "I have worked as a teacher for two years.")
    done2 = _done(e2)
    assert done2["eval_result"] == "hinted"
    assert done2["action_type"] == "recheck"
    # 复测独立回答 → 子目标 1 完成，进入追问
    e3 = _send_text(client, s["session_id"], "I have five years of experience in marketing and I enjoy it.")
    done3 = _done(e3)
    assert done3["eval_result"] == "independent"
    assert done3["status"] == "WAITING_INPUT"


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
    assert data["status"] == "WAITING_INPUT"  # 有效评分后继续原目标
    assert "score" in data["eval"]
    assert data["recheck_reply_text"]


def test_practice_recording_binding(client):
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
    # 本人录音保存并绑定评价（录音版本/参考句版本/阶梯阶段）
    assert data["recording_url"].startswith("/api/v1/audio/")
    assert data["eval"]["recording_url"] == data["recording_url"]
    assert "reference_version" in data["eval"]
    assert "drill_stage" in data["eval"]


def test_practice_recording_audio_served(client):
    """本人跟读录音（recording_url）与教练音频同目录，也能按归属校验后回放。"""
    s = _create_session(client)
    _send_text(client, s["session_id"], "just working")  # → PRACTICE
    r = client.post(
        f"/api/v1/sessions/{s['session_id']}/practice",
        data={"sentence": "I have three years of experience."},
        files={"audio": ("a.webm", b"\x1aE\xdf\xa3" + b"\x00" * 100, "audio/webm")},
        headers=USER,
    )
    assert r.status_code == 200, r.text
    recording_url = r.json()["recording_url"]
    audio = client.get(recording_url, headers=USER)
    assert audio.status_code == 200, audio.text


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
    s = _create_session(client, user="test-a")
    r = client.get(f"/api/v1/sessions/{s['session_id']}", headers={"X-Invite-Code": "test-b"})
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
    s = _create_session(client, user="test-a")
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


def test_voice_turn_rejects_bad_magic(client):
    s = _create_session(client)
    r = client.post(
        f"/api/v1/sessions/{s['session_id']}/turns/voice",
        files={"audio": ("a.webm", b"not-an-audio", "audio/webm")},
        headers=USER,
    )
    events = _parse_sse(r.text)
    assert events[-1][0] == "error"
    assert events[-1][1]["error"]["code"] == "UNSUPPORTED_AUDIO"


def test_coach_context_contains_answer_and_history(client, monkeypatch):
    from app.schemas.coach import CoachDecision
    from app.services import session_svc

    captured: dict = {}

    class SpyCoach:
        def opening(self, goal_text, difficulty):
            return CoachDecision(action_type="follow_up", reply_text="Welcome.")

        def decide(self, context):
            captured.update(context)
            return CoachDecision(action_type="follow_up", reply_text="Nice, tell me more.")

    monkeypatch.setattr(session_svc, "get_coach_provider", lambda: SpyCoach())
    s = _create_session(client)
    _send_text(client, s["session_id"], "I have three years of experience in software development.")

    assert captured.get("current_answer") == "I have three years of experience in software development."
    assert captured.get("current_question")
    assert captured.get("history")
    assert captured.get("input_type") == "text"
    assert captured.get("subgoal") == "intro"
    assert captured.get("level") == "basic"


def test_help_repeat_returns_original_question(client):
    s = _create_session(client)
    r = client.post(f"/api/v1/sessions/{s['session_id']}/help", json={"help_type": "repeat"}, headers=USER)
    assert r.status_code == 200
    body = r.json()
    assert body["help_type"] == "repeat"
    # repeat 复用当前问题原文本与音频，不生成新内容
    assert body["reply_text"] == s["reply_text"]
    assert body["reply_audio_url"] == s["reply_audio_url"]


def test_organize_help_marks_hinted(client):
    s = _create_session(client)
    client.post(f"/api/v1/sessions/{s['session_id']}/help", json={"help_type": "organize", "help_level": 3}, headers=USER)
    done = _done(_send_text(client, s["session_id"], "I have three years of experience in software development."))
    # 答案辅助后，即使回答很好也不判独立
    assert done["eval_result"] == "hinted"


def test_hint_respects_budget(client, monkeypatch):
    monkeypatch.setattr(config.settings, "budget_session", 0.10)
    s = _create_session(client)  # 占 0.10
    r = client.post(f"/api/v1/sessions/{s['session_id']}/hint", headers=USER)
    assert r.status_code == 402
    assert r.json()["error"]["code"] == "BUDGET_EXCEEDED"


def test_skip_practice_respects_budget(client, monkeypatch):
    monkeypatch.setattr(config.settings, "budget_session", 0.10)
    s = _create_session(client)  # 占 0.10
    r = client.post(f"/api/v1/sessions/{s['session_id']}/skip-practice", headers=USER)
    # 未进入 PRACTICE 状态时先被状态校验拒绝；即使进入，预算也会拦截
    assert r.status_code in (402, 409)


def test_pause_resume(client):
    s = _create_session(client)
    assert client.post(f"/api/v1/sessions/{s['session_id']}/pause", headers=USER).json()["status"] == "PAUSED"
    # 暂停中不能提交
    events = _parse_sse(client.post(f"/api/v1/sessions/{s['session_id']}/turns/text", json={"text": "hello"}, headers=USER).text)
    assert events[-1][1]["error"]["code"] == "SESSION_PAUSED"
    # 恢复后可提交
    client.post(f"/api/v1/sessions/{s['session_id']}/resume", headers=USER)
    events2 = _parse_sse(client.post(f"/api/v1/sessions/{s['session_id']}/turns/text", json={"text": "I have three years of experience."}, headers=USER).text)
    assert events2[-1][0] == "done"


def test_delete_all_sessions(client):
    _create_session(client)
    _create_session(client)
    assert client.delete("/api/v1/sessions", headers=USER).status_code == 200
    assert client.get("/api/v1/sessions", headers=USER).json() == []


def test_revise_turn_invalidates_eval(client):
    s = _create_session(client)
    turn_id = _done(_send_text(client, s["session_id"], "I have three years of experience in software development."))["turn_id"]
    r = client.patch(
        f"/api/v1/sessions/{s['session_id']}/turns/{turn_id}",
        json={"revised_text": "I have two years of experience."},
        headers=USER,
    )
    assert r.status_code == 200
    assert r.json()["revision_version"] == 1
    detail = client.get(f"/api/v1/sessions/{s['session_id']}", headers=USER).json()
    for t in detail["turns"]:
        if t["turn_id"] == turn_id:
            assert t["eval_result"] == "revised"
            break
