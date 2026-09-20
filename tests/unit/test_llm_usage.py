"""T025: captura e registro de uso de tokens de chamadas de LLM."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from prometheus_client import generate_latest
from pydantic import BaseModel

import app.llm.structured_output as structured_output
from app.config.settings import get_settings
from app.llm import usage as usage_module
from app.llm.openrouter_client import (
    FakeChatModel,
    clear_chat_model_cache,
    register_fake_default,
)
from app.llm.structured_output import get_structured_output
from app.llm.usage import TokenUsageCallback, collect_usage, record_llm_usage
from app.observability import otel
from app.observability.instrumentation import current_agent_var


class _Out(BaseModel):
    value: str = "x"


@pytest.fixture(autouse=True)
def _telemetry() -> None:
    otel.init_telemetry()
    clear_chat_model_cache()
    yield
    clear_chat_model_cache()


async def _llm_events(db) -> list[dict]:
    return await db["audit_events"].find({"event_type": "llm_call"}).to_list(length=50)


async def test_fake_provider_records_deterministic_usage(test_db, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "llm_provider", "fake")
    token = current_agent_var.set("usage_agent")
    try:
        result = await get_structured_output(_Out, [{"role": "user", "content": "oi"}])
    finally:
        current_agent_var.reset(token)
    assert isinstance(result, _Out)

    events = await _llm_events(test_db)
    assert len(events) == 1
    meta = events[0]["safe_metadata"]
    assert (meta["prompt_tokens"], meta["completion_tokens"], meta["total_tokens"]) == (10, 5, 15)
    assert meta["model"] == "fake" and events[0]["actor_name"] == "usage_agent"
    text = generate_latest().decode()
    lines = [
        line
        for line in text.splitlines()
        if line.startswith('getnet_llm_tokens_total{agent="usage_agent",model="fake"')
    ]
    assert any('token_type="prompt"' in line for line in lines)
    assert any('token_type="completion"' in line for line in lines)


async def test_model_override_is_passed_to_get_chat_model(monkeypatch) -> None:
    seen: list[str | None] = []

    class _Model:
        def with_structured_output(self, _schema):
            return self

        async def ainvoke(self, _messages):
            return _Out()

    def fake_get_chat_model(model=None):
        seen.append(model)
        return _Model()

    monkeypatch.setattr(structured_output, "get_chat_model", fake_get_chat_model)
    await get_structured_output(_Out, [{"role": "user", "content": "x"}], model="cheap/model")
    await get_structured_output(_Out, [{"role": "user", "content": "x"}])
    assert seen == ["cheap/model", None]


async def test_custom_model_without_usage_does_not_break(test_db, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "llm_provider", "openrouter")

    class _Model:
        def with_structured_output(self, _schema):
            return self

        async def ainvoke(self, _messages):
            return _Out()

    monkeypatch.setattr(structured_output, "get_chat_model", lambda: _Model())
    assert isinstance(await get_structured_output(_Out, []), _Out)
    assert await _llm_events(test_db) == []


def test_callback_collects_usage_metadata_and_token_usage() -> None:
    callback = TokenUsageCallback()
    message = AIMessage(
        content="x",
        usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
        response_metadata={"model_name": "m1"},
    )
    with collect_usage() as usages:
        callback.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]))
        callback.on_llm_end(
            LLMResult(
                generations=[[]],
                llm_output={
                    "token_usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                    "model_name": "m2",
                },
            )
        )
    assert [(u.prompt_tokens, u.completion_tokens, u.total_tokens, u.model) for u in usages] == [
        (7, 3, 10, "m1"),
        (2, 1, 3, "m2"),
    ]


def test_callback_outside_collector_is_a_noop() -> None:
    TokenUsageCallback().on_llm_end(LLMResult(generations=[[]]))


async def test_record_llm_usage_never_raises(monkeypatch) -> None:
    def broken():
        raise RuntimeError("metrics down")

    monkeypatch.setattr(usage_module, "get_instruments", broken)
    await record_llm_usage([usage_module.fake_usage()])


def test_fake_default_factory_receives_last_user_text() -> None:
    register_fake_default(_Out, lambda text: _Out(value=text))
    model = FakeChatModel().with_structured_output(_Out)
    import asyncio

    result = asyncio.run(
        model.ainvoke([{"role": "system", "content": "s"}, {"role": "user", "content": "ultimo"}])
    )
    assert result.value == "ultimo"
