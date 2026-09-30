"""REQ-05 四类求助：确定性编排、数据契约、评估边界、幂等（Mock 回归）。"""
import json

from app.models.database import SessionLocal
from app.models.entities import BudgetLedger, Evaluation, Turn

USER = {"X-Invite-Code": "test-a"}


def _create(client, scene_id="job_interview"):
    r = client.post(
        "/api/v1/sessions",
        json={"level": "basic", "difficulty": "standard", "scene_id": scene_id},
        headers=USER,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _help(client, session_id, help_type, source_text=None):
    body: dict = {"help_type": help_type}
    if source_text is not None:
        body["source_text"] = source_text
    return client.post(f"/api/v1/sessions/{session_id}/help", json=body, headers=USER)


def _turn(client, session_id, text):
    r = client.post(f"/api/v1/sessions/{session_id}/turns/text", json={"text": text}, headers=USER)
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


def _turn_voice(client, session_id, transcript, monkeypatch):
    from app.services import session_svc

    class _ASR:
        def transcribe(self, audio_path):
            return transcript

    monkeypatch.setattr(session_svc, "get_asr_provider", lambda: _ASR())
    r = client.post(
        f"/api/v1/sessions/{session_id}/turns/voice",
        files={"audio": ("a.webm", b"\x1aE\xdf\xa3" + b"\x00" * 100, "audio/webm")},
        headers=USER,
    )
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
    raise AssertionError("未收到 done 事件")


def _session_spent(session_id: str) -> float:
    db = SessionLocal()
    try:
        row = db.query(BudgetLedger).filter_by(scope="session", scope_key=session_id).first()
        return row.spent if row else 0.0
    finally:
        db.close()


def test_repeat_reuses_audio_and_no_cost(client):
    s = _create(client)
    spent_before = _session_spent(s["session_id"])
    r = _help(client, s["session_id"], "repeat")
    assert r.status_code == 200
    body = r.json()
    assert body["reply_text"] == s["reply_text"]
    assert body["reply_audio_url"] == s["reply_audio_url"]
    assert body["is_answer_help"] is False
    assert _session_spent(s["session_id"]) == spent_before  # 无新模型费用


def test_explain_returns_chinese_no_answer(client):
    s = _create(client, scene_id="hotel_checkin")
    r = _help(client, s["session_id"], "explain")
    assert r.status_code == 200
    body = r.json()
    assert body["is_answer_help"] is False
    assert any("\u4e00" <= ch <= "\u9fff" for ch in body["reply_text"])
    assert "under the name Li" not in body["reply_text"]


def test_organize_progresses_keywords_half_full(client):
    s = _create(client, scene_id="hotel_checkin")
    levels = []
    for _ in range(4):
        r = _help(client, s["session_id"], "organize")
        assert r.status_code == 200
        levels.append(r.json()["help_level"])
    assert levels == [0, 1, 2, 2]
    # 刷新后（重新读 DB）层级不丢
    db = SessionLocal()
    try:
        turns = (
            db.query(Turn)
            .filter(Turn.session_id == s["session_id"], Turn.help_type == "organize")
            .order_by(Turn.seq)
            .all()
        )
        assert [t.help_level for t in turns] == [0, 1, 2, 2]
    finally:
        db.close()


def test_repeat_does_not_clear_answer_help(client):
    s = _create(client)
    _help(client, s["session_id"], "organize")  # 答案帮助
    _help(client, s["session_id"], "repeat")  # 重播不覆盖
    done = _turn(client, s["session_id"], "I have three years of experience in software development.")
    assert done["eval_result"] == "hinted"


def test_new_question_resets_organize_level(client):
    s = _create(client)
    assert _help(client, s["session_id"], "organize").json()["help_level"] == 0
    _turn(client, s["session_id"], "I have three years of experience in software development.")
    assert _help(client, s["session_id"], "organize").json()["help_level"] == 0


def test_translate_requires_source_text(client):
    s = _create(client)
    r = _help(client, s["session_id"], "translate")
    assert r.status_code == 422
    assert r.json()["error"]["code"]


def test_translate_records_answer_help_no_invented_facts(client):
    s = _create(client)
    r = _help(client, s["session_id"], "translate", source_text="我想订两晚的房间。")
    assert r.status_code == 200
    body = r.json()
    assert body["is_answer_help"] is True
    assert body["reply_text"]
    for banned in ("thirty percent", "under the name li", "product manager", "CEO", "30%"):
        assert banned.lower() not in body["reply_text"].lower()


def test_translate_and_organize_affect_independence(client):
    for help_type in ("organize", "translate"):
        s = _create(client)
        if help_type == "translate":
            _help(client, s["session_id"], "translate", source_text="我想介绍一下我的经历。")
        else:
            _help(client, s["session_id"], "organize")
        done = _turn(client, s["session_id"], "I have three years of experience in software development.")
        assert done["eval_result"] == "hinted"


def test_repeat_and_explain_keep_independence(client, monkeypatch):
    for help_type in ("repeat", "explain"):
        s = _create(client)
        _help(client, s["session_id"], help_type)
        done = _turn_voice(client, s["session_id"], "I have three years of experience in software development.", monkeypatch)
        assert done["eval_result"] == "independent"


def test_help_keeps_question_binding(client):
    s = _create(client)
    _help(client, s["session_id"], "repeat")
    _help(client, s["session_id"], "explain")
    done = _turn(client, s["session_id"], "I have three years of experience in software development.")
    db = SessionLocal()
    try:
        ev = db.query(Evaluation).filter(Evaluation.turn_id == done["turn_id"]).first()
        assert ev is not None
        assert ev.question == s["reply_text"]  # 绑定原问题，不是帮助文字
    finally:
        db.close()


def test_invalid_help_type_422(client):
    s = _create(client)
    r = client.post(f"/api/v1/sessions/{s['session_id']}/help", json={"help_type": "bogus"}, headers=USER)
    assert r.status_code == 422
    assert r.json()["error"]["code"]


def test_translate_idempotent_no_double_record(client):
    s = _create(client)
    r1 = _help(client, s["session_id"], "translate", source_text="我想订两晚的房间。")
    r2 = _help(client, s["session_id"], "translate", source_text="我想订两晚的房间。")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["reply_text"] == r2.json()["reply_text"]
    db = SessionLocal()
    try:
        n = db.query(Turn).filter(Turn.session_id == s["session_id"], Turn.help_type == "translate").count()
        assert n == 1
    finally:
        db.close()
