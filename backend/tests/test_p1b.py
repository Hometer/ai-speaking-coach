"""v0.7 P1 补充：七日训练计划 / 最小质检工作台 测试。"""
from app.core import config

USER = {"X-Invite-Code": "test-a"}
QA = {"X-Invite-Code": "qa-a"}


def _enable_qa():
    config.settings.qa_invite_codes = ["qa-a"]


def test_create_training_plan_seven_days(client):
    r = client.post("/api/v1/training-plan", json={"goal_type": "travel"}, headers=USER)
    assert r.status_code == 200, r.text
    plan = r.json()
    assert len(plan["days"]) == 7
    # 旅行目标只用旅行系列场景
    assert all(d["scene_id"] in ("hotel_checkin", "restaurant_order") for d in plan["days"])


def test_skip_and_restart_plan(client):
    client.post("/api/v1/training-plan", json={"goal_type": "daily"}, headers=USER)
    plan = client.get("/api/v1/training-plan", headers=USER).json()
    assert plan["days"][0]["status"] == "pending"
    r = client.post("/api/v1/training-plan/1/skip", headers=USER)
    assert r.json()["days"][0]["status"] == "skipped"
    r2 = client.post("/api/v1/training-plan/restart", headers=USER)
    assert all(d["status"] == "pending" for d in r2.json()["days"])


def test_qa_forbidden_for_normal_user(client):
    _enable_qa()
    r = client.get("/api/v1/qa/sessions", headers=USER)
    assert r.status_code == 403


def test_qa_accessible_for_reviewer(client):
    _enable_qa()
    # 先创建真实会话，标签必须绑定存在的来源
    s = client.post("/api/v1/sessions", json={"level": "basic", "difficulty": "standard"}, headers=USER)
    assert s.status_code == 200, s.text
    sid = s.json()["session_id"]

    r = client.get("/api/v1/qa/sessions", headers=QA)
    assert r.status_code == 200
    r2 = client.post(
        "/api/v1/qa/labels",
        json={"session_id": sid, "turn_id": "", "issue_type": "mis_correction", "conclusion": "confirmed"},
        headers=QA,
    )
    assert r2.status_code == 200, r2.text
    r3 = client.get("/api/v1/qa/stats", headers=QA)
    assert r3.status_code == 200
    assert r3.json()["total"] >= 1


def test_qa_label_rejects_invalid_source_and_type(client):
    _enable_qa()
    # 不存在的会话 → 404
    r = client.post(
        "/api/v1/qa/labels",
        json={"session_id": "missing", "turn_id": "", "issue_type": "mis_correction", "conclusion": "confirmed"},
        headers=QA,
    )
    assert r.status_code == 404, r.text
    # 非法问题类型 → 422
    s = client.post("/api/v1/sessions", json={"level": "basic", "difficulty": "standard"}, headers=USER)
    sid = s.json()["session_id"]
    r2 = client.post(
        "/api/v1/qa/labels",
        json={"session_id": sid, "turn_id": "", "issue_type": "bogus_type", "conclusion": "confirmed"},
        headers=QA,
    )
    assert r2.status_code == 422, r2.text


def test_revoked_qa_invite_cannot_access_workbench(client):
    from app.models.database import SessionLocal
    from app.models.entities import Invite

    _enable_qa()
    db = SessionLocal()
    try:
        invite = db.query(Invite).filter(Invite.code == "qa-a").one()
        invite.revoked = True
        db.commit()
    finally:
        db.close()
    assert client.get("/api/v1/qa/sessions", headers=QA).status_code == 401


def test_plan_pause_resume(client):
    client.post("/api/v1/training-plan", json={"goal_type": "daily"}, headers=USER)
    p = client.post("/api/v1/training-plan/pause", headers=USER).json()
    assert p["status"] == "paused"
    r = client.post("/api/v1/training-plan/resume", headers=USER).json()
    assert r["status"] == "active"


def test_qa_label_repeat_increments_version(client):
    _enable_qa()
    s = client.post("/api/v1/sessions", json={"level": "basic", "difficulty": "standard"}, headers=USER)
    sid = s.json()["session_id"]
    body = {"session_id": sid, "turn_id": "", "issue_type": "mis_correction", "conclusion": "confirmed"}
    r1 = client.post("/api/v1/qa/labels", json=body, headers=QA).json()
    assert r1["review_version"] == "v1"
    r2 = client.post("/api/v1/qa/labels", json={**body, "conclusion": "disputed"}, headers=QA).json()
    assert r2["review_version"] == "v2"
