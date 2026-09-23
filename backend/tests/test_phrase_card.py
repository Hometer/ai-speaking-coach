"""语块卡（REQ-31，P1-4）Mock 测试。"""
import json

USER = {"X-Invite-Code": "test-a"}


def _create(client):
    r = client.post("/api/v1/sessions", json={"level": "basic", "difficulty": "standard"}, headers=USER)
    assert r.status_code == 200, r.text
    return r.json()


def _turn(client, sid, text):
    r = client.post(f"/api/v1/sessions/{sid}/turns/text", json={"text": text}, headers=USER)
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


def _source(client):
    s = _create(client)
    _turn(client, s["session_id"], "just working")
    client.post(f"/api/v1/sessions/{s['session_id']}/end", json={"reason": "user_ended"}, headers=USER)
    d = client.get(f"/api/v1/sessions/{s['session_id']}", headers=USER).json()
    imp = (d.get("review") or {}).get("improvements", [])[0]
    return s["session_id"], imp["turn_id"]


def test_phrase_card_fields_and_idempotent(client):
    sid, tid = _source(client)
    body = {"source_session_id": sid, "source_turn_id": tid}
    r1 = client.post("/api/v1/phrase-cards", json=body, headers=USER)
    assert r1.status_code == 200, r1.text
    c = r1.json()
    for f in ("original_text", "suggestion_text", "chunk_text", "explanation_zh", "context_zh", "replaceable", "example_text"):
        assert c.get(f), f"缺字段 {f}"
    r2 = client.post("/api/v1/phrase-cards", json=body, headers=USER)
    assert r2.json()["id"] == c["id"]  # 幂等


def test_phrase_card_list_and_isolation(client):
    sid, tid = _source(client)
    client.post("/api/v1/phrase-cards", json={"source_session_id": sid, "source_turn_id": tid}, headers=USER)
    items = client.get("/api/v1/phrase-cards", headers=USER).json()
    assert len(items) == 1
    # 其他用户不能生成
    r = client.post("/api/v1/phrase-cards", json={"source_session_id": sid, "source_turn_id": tid}, headers={"X-Invite-Code": "test-b"})
    assert r.status_code == 403


def test_phrase_card_invalidated_on_dispute(client):
    sid, tid = _source(client)
    client.post("/api/v1/phrase-cards", json={"source_session_id": sid, "source_turn_id": tid}, headers=USER)
    client.post(f"/api/v1/sessions/{sid}/review/dispute", json={}, params={"turn_id": tid}, headers=USER)
    items = client.get("/api/v1/phrase-cards", headers=USER).json()
    assert items == []  # 已失效
