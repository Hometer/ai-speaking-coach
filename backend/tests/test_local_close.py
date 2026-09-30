"""v0.10 本地收口：重放快照、费用口径和旧风险录音迁移。"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.core.errors import AppError
from app.models.database import SessionLocal, migrate_db
from app.models.entities import AsrRiskRecord, BudgetLedger, DrillAttempt, ProviderUsageEvent, ReviewQueueItem, Session, Turn, User
from app.services import daily_review_svc, drill_svc, quality_label, review_queue, session_svc, training_plan
from app.services.generation_call import _append_attempt, reset_usage_attempts
from app.services.provider_usage import record_llm_attempts


def test_queue_replay_keeps_first_response_after_later_state_change():
    with SessionLocal() as db:
        db.add(User(id="u"))
        item = ReviewQueueItem(user_id="u", source_session_id="s", source_turn_id="t", suggestion_sentence="I booked a room for two nights", due_at=datetime.now(timezone.utc) - timedelta(hours=1))
        db.add(item)
        db.commit()
        first = review_queue.submit_retest(db, "u", item.id, "I booked two nights", "text", "same")
        review_queue.skip_item(db, "u", item.id)
        assert review_queue.submit_retest(db, "u", item.id, "I booked two nights", "text", "same") == first
        with pytest.raises(AppError) as conflict:
            review_queue.submit_retest(db, "u", item.id, "I booked two nights", "voice", "same")
        assert conflict.value.code == "IDEMPOTENCY_CONFLICT"


def test_drill_replay_keeps_first_stage_and_checks_input_type():
    with SessionLocal() as db:
        db.add(User(id="u"))
        db.add(Session(id="drill", user_id="u", practice_type="drill", status="WAITING_INPUT", drill_stage="full_text", drill_target="I booked a room for two nights"))
        db.commit()
        first = drill_svc.submit_drill_attempt(db, "drill", "u", "I booked a room for two nights", "text", "same")
        assert first["stage"] == "full_text"
        assert first["next_stage"] == "fill_blanks"
        drill_svc.drill_action(db, "drill", "u", "skip")
        assert drill_svc.submit_drill_attempt(db, "drill", "u", "I booked a room for two nights", "text", "same") == first
        with pytest.raises(AppError) as conflict:
            drill_svc.submit_drill_attempt(db, "drill", "u", "I booked a room for two nights", "voice", "same")
        assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
        assert db.query(DrillAttempt).filter_by(drill_session_id="drill", attempt_key="same").count() == 1


def test_legacy_risk_audio_reference_backfilled():
    with SessionLocal() as db:
        db.add(User(id="u"))
        db.add(Session(id="s", user_id="u", pending_risk=json.dumps({"recording_url": "/api/v1/audio/legacy.webm"})))
        db.add(AsrRiskRecord(user_id="u", session_id="s", turn_id="t", recording_url=""))
        db.commit()
    migrate_db()
    with SessionLocal() as db:
        assert db.query(AsrRiskRecord).first().recording_url == "/api/v1/audio/legacy.webm"


def test_provider_usage_is_raw_and_qa_bill_stays_unknown():
    previous = settings.provider_mode
    settings.provider_mode = "real"
    try:
        with SessionLocal() as db:
            db.add(User(id="u"))
            db.add(Session(id="s", user_id="u", status="ENDED"))
            db.add(BudgetLedger(scope="session", scope_key="s", spent=0.2, actual_spent=99.0))
            db.commit()
            reset_usage_attempts()
            _append_attempt("model", "http_503")
            class Usage:
                input_tokens = 23
                output_tokens = 17
            class Response:
                usage = Usage()
            _append_attempt("model", "ok", Response())
            record_llm_attempts(db, "u", "s", "t")
            rows = db.query(ProviderUsageEvent).filter_by(session_id="s").all()
            assert [(r.status, r.input_tokens, r.output_tokens, r.source) for r in rows] == [
                ("http_503", 0, 0, "unreported"), ("ok", 23, 17, "provider_response")
            ]
            qa = quality_label.list_sessions(db)[0]
            assert qa["billed_cost"] is None
            assert qa["supplier_estimate"] is None
            assert qa["provider_usage"]["llm_attempts"] == 2
    finally:
        settings.provider_mode = previous


def test_practice_replay_after_state_change_does_not_repeat_cost():
    audio = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 32
    with SessionLocal() as db:
        db.add(User(id="u"))
        db.add(Session(id="practice", user_id="u", status="PRACTICE", practice_type="interview", scene_id="job_interview"))
        db.commit()
        first = session_svc.submit_practice(db, "practice", "u", audio, "Please repeat this sentence", "same")
        assert session_svc.submit_practice(db, "practice", "u", audio, "Please repeat this sentence", "same") == first
        assert db.query(Turn).filter_by(session_id="practice", input_type="practice").count() == 1
        with pytest.raises(AppError) as conflict:
            session_svc.submit_practice(db, "practice", "u", audio + b"x", "Please repeat this sentence", "same")
        assert conflict.value.code == "IDEMPOTENCY_CONFLICT"


def test_daily_start_replay_after_end():
    with SessionLocal() as db:
        db.add(User(id="u"))
        db.add(Session(id="source", user_id="u", status="ENDED"))
        db.add(Turn(id="turn", session_id="source", seq=1, input_type="voice", eval_result="incomplete"))
        db.add(ReviewQueueItem(user_id="u", source_session_id="source", source_turn_id="turn", suggestion_sentence="I booked a room", due_at=datetime.now(timezone.utc) - timedelta(hours=1)))
        db.commit()
        first = daily_review_svc.start_daily_review(db, "u", "same")
        assert first["session_id"]
        db.get(Session, first["session_id"]).status = "ENDED"
        db.commit()
        assert daily_review_svc.start_daily_review(db, "u", "same") == first
        assert db.query(Session).filter_by(user_id="u", practice_type="daily_review").count() == 1


def test_plan_duplicate_generate_reuses_active_plan_but_restart_creates_new():
    with SessionLocal() as db:
        db.add(User(id="u"))
        db.commit()
        first = training_plan.create_or_renew_plan(db, "u", "travel", 10)
        duplicate = training_plan.create_or_renew_plan(db, "u", "travel", 10)
        assert duplicate["id"] == first["id"]
        restarted = training_plan.restart_plan(db, "u")
        assert restarted["id"] != first["id"]


def test_drill_voice_replay_does_not_call_asr_again_and_checks_owner(client, monkeypatch):
    calls = []
    class Asr:
        def transcribe(self, path):
            calls.append(path)
            return "I booked a room for two nights"
    monkeypatch.setattr(session_svc, "get_asr_provider", lambda: Asr())
    with SessionLocal() as db:
        db.add(User(id="user-a"))
        db.add(Session(id="drill", user_id="user-a", practice_type="drill", status="WAITING_INPUT", drill_stage="full_text", drill_target="I booked a room for two nights"))
        db.commit()
    audio = b"\x1aE\xdf\xa3" + b"\x00" * 32
    def post(data, owner="test-a"):
        return client.post(
            "/api/v1/sessions/drill/drill-attempt/voice",
            files={"audio": ("x.webm", data, "audio/webm")},
            data={"attempt_key": "same"}, headers={"X-Invite-Code": owner},
        )
    denied = post(audio, "test-b")
    assert denied.status_code == 403
    assert not calls
    first = post(audio)
    assert first.status_code == 200, first.text
    assert post(audio).json() == first.json()
    assert len(calls) == 1
    conflict = post(audio + b"x")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(calls) == 1


def test_create_and_turn_replay_keep_first_response_after_session_ends(client):
    headers = {"X-Invite-Code": "test-a"}
    body = {"level": "basic", "difficulty": "standard", "scene_id": "job_interview", "request_key": "create-once"}
    first = client.post("/api/v1/sessions", json=body, headers=headers)
    assert first.status_code == 200, first.text
    sid = first.json()["session_id"]
    def turn_response():
        res = client.post(
            f"/api/v1/sessions/{sid}/turns/text",
            json={"text": "I work with data", "request_key": "turn-once"}, headers=headers,
        )
        assert res.status_code == 200
        events = [json.loads(line[6:]) for line in res.text.splitlines() if line.startswith("data: ")]
        return events[-1]
    first_turn = turn_response()
    client.post(f"/api/v1/sessions/{sid}/end", json={"reason": "user_ended"}, headers=headers)
    assert client.post("/api/v1/sessions", json=body, headers=headers).json() == first.json()
    assert turn_response() == first_turn
    conflict = client.post("/api/v1/sessions", json={**body, "duration_minutes": 10}, headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
