"""v0.7 P0：场景配置 / 多场景会话 / 设备预检 测试。"""
USER = {"X-Invite-Code": "test-a"}


def test_list_scenarios_returns_seven(client):
    r = client.get("/api/v1/scenarios", headers=USER)
    assert r.status_code == 200, r.text
    scenes = r.json()
    assert len(scenes) == 7
    ids = {s["scene_id"] for s in scenes}
    assert ids == {
        "job_interview",
        "new_colleague",
        "meeting_opinion",
        "hotel_checkin",
        "restaurant_order",
        "new_friend",
        "hobby",
    }
    # 列表项含系列与中文标签
    assert all(s["series_label"] and s["label"] and s["goal_zh"] for s in scenes)


def test_get_scenario_detail_includes_openings(client):
    r = client.get("/api/v1/scenarios/hotel_checkin", headers=USER)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["scene_id"] == "hotel_checkin"
    assert s["series"] == "travel"
    assert s["goal_text"]
    assert "basic" in s["openings"]
    assert "beginner" in s["levels"]


def test_get_unknown_scenario_returns_404(client):
    r = client.get("/api/v1/scenarios/not_exist", headers=USER)
    assert r.status_code == 404


def test_create_session_uses_scene_goal(client):
    r = client.post(
        "/api/v1/sessions",
        json={"level": "basic", "difficulty": "standard", "scene_id": "hotel_checkin"},
        headers=USER,
    )
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["scene_id"] == "hotel_checkin"
    assert s["goal_text"] == "Confirm your reservation and ask about one service detail."
    # 开场白来自该场景的 basic 档，而非面试场景
    assert "reservation" in s["reply_text"].lower() or "hotel" in s["reply_text"].lower()


def test_create_session_unknown_scene_returns_404(client):
    r = client.post(
        "/api/v1/sessions",
        json={"level": "basic", "difficulty": "standard", "scene_id": "not_exist"},
        headers=USER,
    )
    assert r.status_code == 404


def test_record_device_check(client):
    r = client.post(
        "/api/v1/device-check",
        json={"status": "ok", "summary": "mic,network"},
        headers=USER,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"recorded": True}


# ===== v0.7 P0 多场景收口回归 =====
import json


def _create_scene_session(client, scene_id, level="basic"):
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


def test_hotel_checkin_followup_and_completion_stay_on_scene(client):
    s = _create_scene_session(client, "hotel_checkin")
    r1 = _send_text_turn(client, s["session_id"], "I have a reservation under the name Li.")
    assert r1["eval_result"] == "independent"
    assert "experience" not in r1["reply_text"].lower()
    assert "software" not in r1["reply_text"].lower()
    r2 = _send_text_turn(client, s["session_id"], "What time does breakfast start?")
    assert r2["eval_result"] == "independent"
    assert r2["status"] == "ENDED"


def test_hotel_checkin_help_is_scene_specific(client):
    s = _create_scene_session(client, "hotel_checkin")
    r = client.post(
        f"/api/v1/sessions/{s['session_id']}/help",
        json={"help_type": "organize", "help_level": 0},
        headers=USER,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert ("reservation" in body["reply_text"]) or ("breakfast" in body["reply_text"])


def test_meeting_opinion_uses_opinion_and_reason(client):
    s = _create_scene_session(client, "meeting_opinion")
    r1 = _send_text_turn(client, s["session_id"], "I think we should launch the smaller version first.")
    assert r1["eval_result"] == "independent"
    r2 = _send_text_turn(client, s["session_id"], "Because it is faster to test and cheaper to fix.")
    assert r2["status"] == "ENDED"


def test_new_friend_requires_two_rounds(client):
    s = _create_scene_session(client, "new_friend")
    r1 = _send_text_turn(client, s["session_id"], "I am Alex and I work in marketing and I like hiking.")
    assert r1["eval_result"] == "independent"
    assert r1["status"] != "ENDED"
    r2 = _send_text_turn(client, s["session_id"], "In my free time I like hiking.")
    assert r2["status"] == "ENDED"


def test_new_colleague_intro_and_question_in_one_turn_completes(client):
    # 认识新同事：自我介绍 + 反问同句完成，两个子目标都独立 → 一轮结束（co_complete）
    s = _create_scene_session(client, "new_colleague")
    r1 = _send_text_turn(client, s["session_id"], "I just joined the sales team. What about you?")
    assert r1["eval_result"] == "independent"
    assert r1["status"] == "ENDED"


def test_new_colleague_self_intro_only_does_not_end(client):
    # 只自我介绍、不反问时，仅 self_intro 完成，ask_question 仍待完成，不应结束
    s = _create_scene_session(client, "new_colleague")
    r1 = _send_text_turn(client, s["session_id"], "I am a designer on the product team.")
    assert r1["eval_result"] == "independent"
    assert r1["status"] != "ENDED"


def test_job_interview_still_works(client):
    s = _create_scene_session(client, "job_interview")
    r1 = _send_text_turn(client, s["session_id"], "I have three years of experience in software development.")
    assert r1["eval_result"] == "independent"
    assert ("experience" in r1["reply_text"].lower()) or ("challenging" in r1["reply_text"].lower())


def test_list_sessions_returns_scene_info(client):
    s = _create_scene_session(client, "hotel_checkin")
    r = client.get("/api/v1/sessions", headers=USER)
    assert r.status_code == 200, r.text
    items = r.json()
    item = next(x for x in items if x["session_id"] == s["session_id"])
    assert item["scene_id"] == "hotel_checkin"
    assert item["scene_label"] == "酒店入住"
