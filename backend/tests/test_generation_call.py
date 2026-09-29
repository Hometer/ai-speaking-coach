"""真实模型调用包装层的离线故障注入，不发起网络请求。"""
import sys
from types import SimpleNamespace

import pytest

from app.core.errors import AppError
from app.core.config import settings
from app.services.generation_call import call_generation


def test_generation_retries_timeout_with_explicit_deadline(monkeypatch):
    seen = []

    def fake_call(**kwargs):
        seen.append(kwargs)
        if len(seen) == 1:
            raise TimeoutError("injected timeout")
        return SimpleNamespace(status_code=200, output=SimpleNamespace(choices=[]))

    monkeypatch.setitem(sys.modules, "dashscope", SimpleNamespace(Generation=SimpleNamespace(call=fake_call)))
    monkeypatch.setattr("app.services.generation_call.time.sleep", lambda _seconds: None)
    response = call_generation(api_key="local-test-only", model="fake-model", messages=[{"role": "user", "content": "hello"}])
    assert response.status_code == 200
    assert len(seen) == 2
    assert all(call["request_timeout"] == settings.llm_timeout_seconds for call in seen)


def test_generation_does_not_retry_nontransient_error(monkeypatch):
    seen = []

    def fake_call(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(status_code=400)

    monkeypatch.setitem(sys.modules, "dashscope", SimpleNamespace(Generation=SimpleNamespace(call=fake_call)))
    with pytest.raises(AppError) as exc:
        call_generation(api_key="local-test-only", model="fake-model", messages=[])
    assert exc.value.code == "LLM_ERROR"
    assert len(seen) == 1
