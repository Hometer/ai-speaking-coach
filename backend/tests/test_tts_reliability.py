"""TTS 可靠性：有限重试、文字降级、SSE 顺序、音频重试（故障注入 / Mock）。"""
import json

from app.core.errors import AppError
from app.models.database import SessionLocal
from app.models.entities import BudgetLedger, Turn
from app.services import session_svc
from app.services.tts import DashScopeTTSProvider, TTSResult

USER = {"X-Invite-Code": "test-a"}


def _create(client):
    r = client.post("/api/v1/sessions", json={"level": "basic", "difficulty": "standard"}, headers=USER)
    assert r.status_code == 200, r.text
    return r.json()


def _events(text):
    out = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event, data = None, None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if event and data is not None:
            out.append((event, data))
    return out


def _turn(client, sid, text):
    r = client.post(f"/api/v1/sessions/{sid}/turns/text", json={"text": text}, headers=USER)
    assert r.status_code == 200, r.text
    return _events(r.text)


class _FlakyTTS:
    """模拟带内部重试的 provider：一次 synthesize_result 内部失败 fail_times 次后成功。"""
    ext = "wav"

    def __init__(self, fail_times=1):
        self.fail_times = fail_times
        self.calls = 0
        self.internal_attempts = 0

    def synthesize_result(self, text):
        self.calls += 1
        for i in range(1, self.fail_times + 2):
            self.internal_attempts += 1
            if i <= self.fail_times:
                continue  # 可恢复失败
            return TTSResult(audio=b"\x00\x00\x00\x00", attempts=i)
        return TTSResult(audio=None, error_type="recoverable", attempts=self.fail_times)


class _AlwaysFailTTS:
    """模拟内部重试仍连续失败。"""
    ext = "wav"

    def __init__(self):
        self.calls = 0
        self.internal_attempts = 0

    def synthesize_result(self, text):
        self.calls += 1
        self.internal_attempts += 2  # 初始 + 1 重试都失败
        return TTSResult(audio=None, error_type="recoverable", attempts=2)


# ---- provider 级重试（单元） ----

def test_tts_provider_retries_recoverable(monkeypatch):
    p = DashScopeTTSProvider("k", "m", "v")
    calls = []

    def fake_once(text):
        calls.append(text)
        if len(calls) == 1:
            raise Exception("TimeoutError: websocket connection could not established within 5s")
        return b"audio"

    monkeypatch.setattr(p, "_once", fake_once)
    r = p.synthesize_result("hello")
    assert r.audio == b"audio"
    assert r.attempts == 2
    assert len(calls) == 2


def test_tts_provider_no_retry_on_auth(monkeypatch):
    p = DashScopeTTSProvider("k", "m", "v")

    def fake_once(text):
        raise Exception("InvalidApiKey")

    monkeypatch.setattr(p, "_once", fake_once)
    r = p.synthesize_result("hello")
    assert r.audio is None
    assert r.error_type == "fatal"
    assert r.attempts == 1


def test_tts_provider_retries_empty_audio(monkeypatch):
    p = DashScopeTTSProvider("k", "m", "v")
    calls = []

    def fake_once(text):
        calls.append(text)
        if len(calls) == 1:
            raise AppError("TTS_EMPTY", "语音合成为空", status_code=502)
        return b"audio"

    monkeypatch.setattr(p, "_once", fake_once)
    r = p.synthesize_result("hello")
    assert r.audio == b"audio"
    assert r.attempts == 2
    assert len(calls) == 2


# ---- 会话级：重试不重复评估/教练/回合/计费 ----

def test_tts_retry_no_duplicate_work(client, monkeypatch):
    from app.services.coach import get_coach_provider as real_coach
    from app.services.evaluator import get_evaluator_provider as real_eval

    real_cp = real_coach()
    real_ep = real_eval()
    counts = {"eval": 0, "coach": 0}

    class SpyEval:
        def evaluate(self, ctx):
            counts["eval"] += 1
            return real_ep.evaluate(ctx)

    class SpyCoach:
        def opening(self, *a, **k):
            return real_cp.opening(*a, **k)

        def decide(self, ctx):
            counts["coach"] += 1
            return real_cp.decide(ctx)

        def help(self, ctx):
            return real_cp.help(ctx)

    s = _create(client)
    flaky = _FlakyTTS(fail_times=1)
    monkeypatch.setattr(session_svc, "get_evaluator_provider", lambda: SpyEval())
    monkeypatch.setattr(session_svc, "get_coach_provider", lambda: SpyCoach())
    monkeypatch.setattr(session_svc, "get_tts_provider", lambda: flaky)

    events = _turn(client, s["session_id"], "I have three years of experience in software development.")
    done = [d for e, d in events if e == "done"][-1]
    assert counts["eval"] == 1
    assert counts["coach"] == 1
    assert flaky.calls == 1  # 会话流只调用一次 synthesize_result
    assert flaky.internal_attempts == 2  # 重试发生在 provider 内部
    assert done["audio_status"] == "ok"
    # 只创建 1 个回合，不重复计费
    db = SessionLocal()
    try:
        n_turns = db.query(Turn).filter(Turn.session_id == s["session_id"], Turn.input_type == "text").count()
        assert n_turns == 1
        spent = db.query(BudgetLedger).filter_by(scope="session", scope_key=s["session_id"]).first().spent
        assert round(spent, 2) == 0.30  # 开场 + 文本回合 + 独立 TTS 预留各 0.10；重试未创建第二个 TTS 意图
    finally:
        db.close()


def test_tts_two_failures_keeps_text(client, monkeypatch):
    s = _create(client)
    failing = _AlwaysFailTTS()
    monkeypatch.setattr(session_svc, "get_tts_provider", lambda: failing)
    events = _turn(client, s["session_id"], "I have three years of experience in software development.")
    assert [e for e, _ in events] == ["chunk", "done"]
    chunk = [d for e, d in events if e == "chunk"][0]
    done = [d for e, d in events if e == "done"][-1]
    assert chunk["text"] == done["reply_text"]
    assert done["audio_status"] == "failed"
    assert done["reply_audio_url"] == ""
    assert done["eval_result"]  # 评估仍有效


def test_sse_chunk_before_done(client):
    s = _create(client)
    events = _turn(client, s["session_id"], "I have three years of experience in software development.")
    kinds = [e for e, _ in events]
    assert "chunk" in kinds and "done" in kinds
    assert kinds.index("chunk") < kinds.index("done")


def test_audio_retry_idempotent_and_own_turn(client, monkeypatch):
    s = _create(client)
    flaky = _FlakyTTS(fail_times=1)
    monkeypatch.setattr(session_svc, "get_tts_provider", lambda: flaky)
    events = _turn(client, s["session_id"], "I have three years of experience in software development.")
    done = [d for e, d in events if e == "done"][-1]
    assert done["audio_status"] == "ok"
    audio_url = done["reply_audio_url"]
    # 再次重试（幂等）→ 返回已有音频，不再调用 TTS
    r = client.post(f"/api/v1/sessions/{s['session_id']}/turns/{done['turn_id']}/audio", headers=USER)
    assert r.status_code == 200, r.text
    assert r.json()["reply_audio_url"] == audio_url
    # 他人不能访问
    r2 = client.post(
        f"/api/v1/sessions/{s['session_id']}/turns/{done['turn_id']}/audio",
        headers={"X-Invite-Code": "test-b"},
    )
    assert r2.status_code == 403


def test_tts_failure_persists_across_restart(client, monkeypatch):
    s = _create(client)
    failing = _AlwaysFailTTS()
    monkeypatch.setattr(session_svc, "get_tts_provider", lambda: failing)
    events = _turn(client, s["session_id"], "I have three years of experience in software development.")
    done = [d for e, d in events if e == "done"][-1]
    assert done["audio_status"] == "failed"
    # 模拟重启：全新 DB 会话读回，文字与失败状态仍在
    db = SessionLocal()
    try:
        turn = db.get(Turn, done["turn_id"])
        assert turn is not None
        assert turn.reply_text
        assert turn.audio_status == "failed"
        assert turn.reply_audio_url == ""
    finally:
        db.close()
