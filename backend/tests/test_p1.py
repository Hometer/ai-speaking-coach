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
    assert item["suggestion_sentence"]
    assert item["source_turn_id"] == turn_id

    # 列表可见
    rl = client.get("/api/v1/review-queue", headers=USER)
    assert any(x["id"] == item["id"] for x in rl.json())

    # 第一次复测独立成功 -> awaiting_retest
    r1 = client.post(
        f"/api/v1/review-queue/{item['id']}/retest",
        json={"answer": "I have a reservation under the name Li, please confirm it."},
        headers=USER,
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["success_count"] == 1
    assert r1.json()["status"] == "awaiting_retest"

    # 第二次复测独立成功 -> mastered
    r2 = client.post(
        f"/api/v1/review-queue/{item['id']}/retest",
        json={"answer": "I have a reservation under the name Li, please confirm it again."},
        headers=USER,
    )
    assert r2.json()["status"] == "mastered"


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
    for key in ("independent_rate", "help_usage_rate", "due_retest_done_rate", "mastered_count", "voice_evaluable_rate"):
        assert key in p
    assert p["calc_version"] == "v1"


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
