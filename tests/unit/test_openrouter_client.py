"""T048: teste unitario do cliente OpenRouter -- modo fake, retry e circuit breaker."""

from __future__ import annotations

import pytest

import app.llm.openrouter_client as openrouter_client
from app.llm.openrouter_client import (
    FakeChatModel,
    RetryableLLMError,
    clear_chat_model_cache,
    get_chat_model,
    invoke_chat_model,
)


@pytest.fixture(autouse=True)
def _reset_caches_and_breaker():
    clear_chat_model_cache()
    openrouter_client._circuit_breaker.close()
    yield
    clear_chat_model_cache()
    openrouter_client._circuit_breaker.close()


def test_fake_provider_returns_fake_chat_model(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = openrouter_client.get_settings()
    monkeypatch.setattr(settings, "llm_provider", "fake")
    model = get_chat_model()
    assert isinstance(model, FakeChatModel)


async def test_invoke_chat_model_retries_on_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    class FlakyModel:
        async def ainvoke(self, *_args, **_kwargs):
            calls["count"] += 1
            if calls["count"] < 2:
                raise RuntimeError("transient network error")
            return "ok"

    monkeypatch.setattr(openrouter_client, "get_chat_model", lambda: FlakyModel())

    result = await invoke_chat_model([{"role": "user", "content": "oi"}])

    assert result == "ok"
    assert calls["count"] == 2


async def test_invoke_chat_model_raises_retryable_error_after_exhausting_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AlwaysFailingModel:
        async def ainvoke(self, *_args, **_kwargs):
            raise RuntimeError("persistent failure")

    monkeypatch.setattr(openrouter_client, "get_chat_model", lambda: AlwaysFailingModel())

    with pytest.raises(RetryableLLMError):
        await invoke_chat_model([{"role": "user", "content": "oi"}])
