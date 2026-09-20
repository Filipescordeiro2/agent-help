"""Erros do provedor de LLM (chave invalida, sem saldo...) viram respostas estruturadas claras."""

from __future__ import annotations

import httpx
import openai
import pytest

import app.agent.router.agent as router_agent_module
from app.llm.structured_output import StructuredOutputError, get_structured_output
from app.schemas.router import RouterDecision


def _status_error(status: int) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(
        status, request=request, json={"error": {"message": "segredo-do-provedor"}}
    )
    return openai.APIStatusError("upstream", response=response, body=None)


@pytest.mark.parametrize(
    ("upstream", "http_status", "error_code"),
    [
        (401, 401, "LLM_API_KEY_INVALID"),
        (402, 402, "LLM_INSUFFICIENT_CREDITS"),
        (403, 403, "LLM_ACCESS_DENIED"),
        (429, 429, "LLM_RATE_LIMITED"),
        (500, 502, "LLM_UPSTREAM_ERROR"),
    ],
)
def test_upstream_error_is_mapped_to_a_clear_structured_response(
    client, fake_llm, monkeypatch, upstream, http_status, error_code
) -> None:
    async def boom(*_a, **_k):
        raise _status_error(upstream)

    monkeypatch.setattr(router_agent_module, "get_structured_output", boom)
    sid = client.post("/api/v1/sessions", json={"user_id": "u1"}).json()["session_id"]

    response = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"message": "qual a taxa da maquininha?", "user_id": "u1", "session_id": sid},
    )

    assert response.status_code == http_status
    body = response.json()
    assert body["status"] == "ERROR"
    assert body["metadata"]["error_code"] == error_code
    assert "segredo-do-provedor" not in response.text


async def test_auth_errors_are_not_retried_or_wrapped(monkeypatch) -> None:
    calls = {"n": 0}

    class _Model:
        def with_structured_output(self, _schema):
            return self

        async def ainvoke(self, _messages):
            calls["n"] += 1
            raise _status_error(401)

    import app.llm.structured_output as structured_output

    monkeypatch.setattr(structured_output, "get_chat_model", lambda *_a, **_k: _Model())

    with pytest.raises(openai.APIStatusError):
        await get_structured_output(RouterDecision, [{"role": "user", "content": "oi"}])
    assert calls["n"] == 1


async def test_other_failures_still_retry_once_and_wrap(monkeypatch) -> None:
    class _Model:
        def with_structured_output(self, _schema):
            return self

        async def ainvoke(self, _messages):
            raise RuntimeError("falha transitoria")

    import app.llm.structured_output as structured_output

    monkeypatch.setattr(structured_output, "get_chat_model", lambda *_a, **_k: _Model())

    with pytest.raises(StructuredOutputError):
        await get_structured_output(RouterDecision, [{"role": "user", "content": "oi"}])
