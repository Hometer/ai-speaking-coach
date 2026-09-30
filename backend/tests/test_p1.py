"""v0.7 P1：待复练队列 / 进步中心 / 发音证据 测试。"""
import json

USER = {"X-Invite-Code": "test-a"}


def _create_scene_session(client, scene_id="job_interview", level="basic"):
    r = client.post(
        "/api/v1/sessions",
        json={"level": level, "difficulty": "standard", "scene_id": scene_id},
        headers=USER,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _send_text_turn(client, session_id, text):
    r = client.post(
        f"/api/v1/sessions/{session_id}/turns/text",
        json={"text": text},
        headers=USER,
    )
    assert r.status_code == 200, r.text
    done = None
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
            done = data
    assert done is not None, r.text
    return done


def _make_queue_source(client, scene_id="job_interview"):
    """创建带回顾建议的会话，返回 (session, turn_id)。"""
    s = _create_scene_session(client, scene_id)
    r = _send_text_turn(client, s["session_id"], "this is my answer")  # incomplete
    turn_id = r["turn_id"]
    client.post(
        f"/api/v1/sessions/{s['session_id']}/end",
        json={"reason": "user_ended"},
        headers=USER,
    )
    return s, turn_id


def test_add_to_queue_and_retest_mastery(client):
    s, turn_id = _make_queue_source(client)
    r = client.post(
        "/api/v1/review-queue",
        json={"source_session_id": s["session_id"], "source_turn_id": turn_id},
        headers=USER,
    )
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["status"] == "pending"
    assert item["suggestion_sentence"] == ""  # 默认隐藏答案
    assert item["meaning_zh"]  # 给中文意图
    assert item["source_turn_id"] == turn_id

    # 列表可见
    rl = client.get("/api/v1/review-queue", headers=USER)
    assert any(x["id"] == item["id"] for x in rl.json())

    # 首次复测也必须到期，不能刚加入就增加成功次数。
    early = client.post(
        f"/api/v1/review-queue/{item['id']}/retest",
        json={"answer": "I have three years of experience in software development.", "input_type": "voice"},
        headers=USER,
    )
    assert early.status_code == 409
    assert early.json()["error"]["code"] == "NOT_DUE"

    from datetime import datetime, timedelta, timezone

    from app.models.database import SessionLocal
    from app.models.entities import ReviewQueueItem

    db = SessionLocal()
    qi = db.get(ReviewQueueItem, item["id"])
    qi.due_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()
    db.close()

    # 第一次复测独立成功 -> awaiting_retest
    r1 = client.post(
        f"/api/v1/review-queue/{item['id']}/retest",
        json={"answer": "I have three years of experience in software development.", "input_type": "voice"},
        headers=USER,
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["success_count"] == 1
    assert r1.json()["status"] == "awaiting_retest"

    # AC07-08：未到期立即第二次复测 -> 409 拒绝，成功次数保持 1
    r2 = client.post(
        f"/api/v1/review-queue/{item['id']}/retest",
        json={"answer": "I have three years of experience in software development.", "input_type": "voice"},
        headers=USER,
    )
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "NOT_DUE"

    # 到期后（due_at 改为过去）再复测 -> mastered
    db = SessionLocal()
    qi = db.get(ReviewQueueItem, item["id"])
    qi.due_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()
    db.close()

    r3 = client.post(
        f"/api/v1/review-queue/{item['id']}/retest",
        json={"answer": "I have three years of experience in software development.", "input_type": "voice"},
        headers=USER,
    )
    assert r3.status_code == 200, r3.text
    assert r3.json()["status"] == "mastered"


def test_retest_rejects_text_unrelated_and_copied(client):
    s, turn_id = _make_queue_source(client)
    item = client.post(
        "/api/v1/review-queue",
        json={"source_session_id": s["session_id"], "source_turn_id": turn_id},
        headers=USER,
    ).json()
    from datetime import datetime, timedelta, timezone
    from app.models.database import SessionLocal
    from app.models.entities import ReviewQueueItem

    def _make_due():
        db = SessionLocal()
        qi = db.get(ReviewQueueItem, item["id"])
        qi.due_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()
        db.close()

    # 看答案后照抄 → hinted，不升级
    client.post(f"/api/v1/review-queue/{item['id']}/reveal", headers=USER)
    _make_due()
    copied = client.post(
        f"/api/v1/review-queue/{item['id']}/retest",
        json={"answer": "I have three years of experience in software development.", "input_type": "voice"},
        headers=USER,
    )
    assert copied.json()["retest_result"] == "hinted"
    assert copied.json()["success_count"] == 0

    # 文字输入（相关）→ text_completed，不升级
    db = SessionLocal()
    qi = db.get(ReviewQueueItem, item["id"])
    qi.help_shown = False
    db.commit()
    db.close()
    _make_due()
    text = client.post(
        f"/api/v1/review-queue/{item['id']}/retest",
        json={"answer": "I have three years of experience in software development.", "input_type": "text"},
        headers=USER,
    )
    assert text.json()["retest_result"] == "text_completed"
    assert text.json()["success_count"] == 0

    # 无关句 → pending，不升级
    _make_due()
    unrelated = client.post(
        f"/api/v1/review-queue/{item['id']}/retest",
        json={"answer": "I have a reservation under the name Li.", "input_type": "voice"},
        headers=USER,
    )
    assert unrelated.json()["retest_result"] == "pending"
    assert unrelated.json()["success_count"] == 0


def test_queue_duplicate_is_idempotent(client):
    s, turn_id = _make_queue_source(client)
    r1 = client.post(
        "/api/v1/review-queue",
        json={"source_session_id": s["session_id"], "source_turn_id": turn_id},
        headers=USER,
    )
    r2 = client.post(
        "/api/v1/review-queue",
        json={"source_session_id": s["session_id"], "source_turn_id": turn_id},
        headers=USER,
    )
    assert r1.json()["id"] == r2.json()["id"]


def test_progress_endpoint_returns_factual_metrics(client):
    _make_queue_source(client)
    r = client.get("/api/v1/progress", headers=USER)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["completed_count"] >= 1
    for key in ("independent_rate", "help_usage_rate", "due_retest_done_rate", "mastered_count", "voice_evaluable_rate", "drill_completed_count", "transfer_completed_count", "text_completed_count", "samples", "by_scene", "trend", "mastered_list"):
        assert key in p
    assert p["calc_version"] == "v4"


def test_mock_practice_has_no_word_items(client):
    s = _create_scene_session(client)
    _send_text_turn(client, s["session_id"], "this is my answer")  # incomplete -> suggest_practice
    pr = client.post(
        f"/api/v1/sessions/{s['session_id']}/practice",
        data={"sentence": "I have a reservation under the name Li."},
        files={"audio": ("a.webm", b"\x1aE\xdf\xa3" + b"\x00" * 100, "audio/webm")},
        headers=USER,
    )
    assert pr.status_code == 200, pr.text
    eval_result = pr.json()["eval"]
    # mock 无真实词级证据，word_items 恒为空
    assert eval_result.get("word_items") == []
