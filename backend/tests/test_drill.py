"""脱稿阶梯与帮助证据（REQ-28，P1-1）Mock 测试。"""
import json

USER = {"X-Invite-Code": "test-a"}


def _create(client, scene_id="job_interview"):
    r = client.post("/api/v1/sessions", json={"level": "basic", "difficulty": "standard", "scene_id": scene_id}, headers=USER)
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
    raise AssertionError("无 done 事件")


def _source_improvement(client):
    s = _create(client)
    done = _turn(client, s["session_id"], "just working")
    assert done["eval_result"] == "incomplete"
    client.post(f"/api/v1/sessions/{s['session_id']}/end", json={"reason": "user_ended"}, headers=USER)
    detail = client.get(f"/api/v1/sessions/{s['session_id']}", headers=USER).json()
    imps = (detail.get("review") or {}).get("improvements", [])
    assert imps, "源会话应有改进建议"
    return s["session_id"], imps[0]["turn_id"], imps[0]


def _create_drill(client, source_sid, source_tid):
    r = client.post("/api/v1/sessions/practice-sentence", json={"source_session_id": source_sid, "turn_id": source_tid}, headers=USER)
    assert r.status_code == 200, r.text
    return r.json()


def _attempt(client, sid, text):
    r = client.post(f"/api/v1/sessions/{sid}/drill-attempt", json={"text": text}, headers=USER)
    assert r.status_code == 200, r.text
    return r.json()


def test_create_drill_session_sets_stage(client):
    source_sid, source_tid, imp = _source_improvement(client)
    d = _create_drill(client, source_sid, source_tid)
    detail = client.get(f"/api/v1/sessions/{d['session_id']}", headers=USER).json()
    drill = detail["drill"]
    assert drill["drill_stage"] == "full_text"
    assert drill["drill_target"] == imp["practice_sentence"]
    assert drill["visible_help"] == imp["practice_sentence"]


def test_drill_stage_progression_and_evidence(client):
    source_sid, source_tid, imp = _source_improvement(client)
    d = _create_drill(client, source_sid, source_tid)
    sid = d["session_id"]
    target = imp["practice_sentence"]
    answer = target  # 看全文阶段照着说

    r = _attempt(client, sid, answer)
    assert r["result"] == "reference" and r["next_stage"] == "fill_blanks"
    r = _attempt(client, sid, answer)
    assert r["result"] == "reference" and r["next_stage"] == "structure"
    r = _attempt(client, sid, answer)
    assert r["result"] == "hinted" and r["next_stage"] == "free_speak"
    r = _attempt(client, sid, answer)
    assert r["result"] == "independent" and r["next_stage"] == "transfer"
    r = _attempt(client, sid, answer)
    assert r["result"] == "transfer" and r["next_stage"] == "ended"
    assert r["completed"] is True


def test_drill_loose_match_not_exact(client):
    source_sid, source_tid, imp = _source_improvement(client)
    d = _create_drill(client, source_sid, source_tid)
    r = _attempt(client, d["session_id"], "I have three years of experience in software development, yes.")
    assert r["success"] is True


def test_drill_failure_stays(client):
    source_sid, source_tid, _ = _source_improvement(client)
    d = _create_drill(client, source_sid, source_tid)
    r = _attempt(client, d["session_id"], "hi")
    assert r["success"] is False
    assert r["result"] == "failed"
    assert r["next_stage"] == "full_text"  # 不推进


def test_drill_downgrade_and_skip(client):
    source_sid, source_tid, imp = _source_improvement(client)
    d = _create_drill(client, source_sid, source_tid)
    sid = d["session_id"]
    _attempt(client, sid, imp["practice_sentence"])  # → fill_blanks
    r = client.post(f"/api/v1/sessions/{sid}/drill/action", json={"action": "downgrade"}, headers=USER)
    assert r.status_code == 200
    assert r.json()["drill_stage"] == "full_text"
    r2 = client.post(f"/api/v1/sessions/{sid}/drill/action", json={"action": "skip"}, headers=USER)
    assert r2.json()["drill_stage"] == "fill_blanks"


def test_drill_write_back_to_queue(client):
    source_sid, source_tid, imp = _source_improvement(client)
    # 先加入待复练
    r = client.post("/api/v1/review-queue", json={"source_session_id": source_sid, "source_turn_id": source_tid}, headers=USER)
    assert r.status_code == 200, r.text
    d = _create_drill(client, source_sid, source_tid)
    sid = d["session_id"]
    target = imp["practice_sentence"]
    for _ in range(4):  # 推进到 free_speak 成功（独立）
        _attempt(client, sid, target)
    items = client.get("/api/v1/review-queue", headers=USER).json()
    item = next(x for x in items if x["source_turn_id"] == source_tid)
    assert item["success_count"] == 1
    assert item["status"] == "awaiting_retest"
