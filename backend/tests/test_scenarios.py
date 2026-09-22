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
