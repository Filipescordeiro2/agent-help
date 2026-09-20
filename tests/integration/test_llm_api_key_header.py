"""A chave do OpenRouter vem por requisicao (X-API-Key-LLM), nunca do .env, e nunca vaza."""

from __future__ import annotations

import logging

import pytest

import app.agent.customer_support.agent  # noqa: F401 -- garante o registro dos agentes
from app.config.settings import get_settings
from app.llm import credentials
from app.llm.credentials import LLM_API_KEY_HEADER, resolve_api_key

SECRET = "sk-or-v1-TESTSECRET-do-not-leak-1234567890"


@pytest.fixture
def real_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "llm_provider", "openrouter")
    monkeypatch.setattr(get_settings(), "openrouter_api_key", None)


def _new_session(client) -> str:
    return client.post("/api/v1/sessions", json={"user_id": "u1"}).json()["session_id"]


def test_message_without_key_is_rejected_with_a_clear_error(client, real_provider) -> None:
    sid = _new_session(client)
    response = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"message": "qual a taxa?", "user_id": "u1", "session_id": sid},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["status"] == "ERROR"
    assert body["metadata"]["error_code"] == "LLM_API_KEY_REQUIRED"
    assert LLM_API_KEY_HEADER in body["message"]


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("POST", "/api/v1/search/semantic", {"query": "taxa"}),
        ("POST", "/api/v1/knowledge/ingest", {"title": "t", "source": "s", "content": "c"}),
        ("POST", "/api/v1/skills/search", {"query": "taxa"}),
        ("POST", "/api/v1/feedback-agent/run", None),
    ],
)
def test_llm_routes_require_the_key(client, real_provider, method, path, payload) -> None:
    response = client.request(method, path, json=payload)
    assert response.status_code == 400
    assert response.json()["metadata"]["error_code"] == "LLM_API_KEY_REQUIRED"


def test_routes_that_do_not_use_llm_do_not_require_the_key(client, real_provider) -> None:
    assert client.post("/api/v1/sessions", json={"user_id": "u1"}).status_code == 200
    assert client.get("/api/v1/knowledge/documents").status_code == 200
    assert client.get("/api/v1/feedback-agent/proposals").status_code == 200


def test_header_key_reaches_the_llm_layer_and_is_gone_after_the_request(
    client, real_provider, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def _capture(_texts):
        seen.append(resolve_api_key())
        return [[0.0] * 8]

    import app.llm.embeddings_client as embeddings_client

    class _Client:
        def embed_query(self, _text):
            seen.append(resolve_api_key())
            return [0.0] * 8

    monkeypatch.setattr(embeddings_client, "get_embeddings_client", lambda: _Client())

    response = client.post(
        "/api/v1/search/semantic",
        json={"query": "taxa"},
        headers={LLM_API_KEY_HEADER: SECRET},
    )

    assert response.status_code == 200
    assert seen == [SECRET]
    # fora da requisicao a chave nao permanece disponivel
    assert credentials.has_api_key() is False


def test_key_is_never_logged_or_echoed(client, real_provider, caplog, capsys) -> None:
    caplog.set_level(logging.DEBUG)
    sid = _new_session(client)
    response = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"message": "oi", "user_id": "u1", "session_id": sid},
        headers={LLM_API_KEY_HEADER: SECRET},
    )

    captured = capsys.readouterr()
    assert SECRET not in response.text
    assert SECRET not in str(dict(response.headers))
    assert SECRET not in caplog.text
    assert SECRET not in captured.out + captured.err


def test_key_is_not_written_to_audit_events(client, real_provider, test_db) -> None:
    import asyncio

    sid = _new_session(client)
    client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"message": "oi", "user_id": "u1", "session_id": sid},
        headers={LLM_API_KEY_HEADER: SECRET},
    )

    async def _dump() -> str:
        events = await test_db["audit_events"].find({}).to_list(1000)
        return repr(events)

    assert SECRET not in asyncio.run(_dump())


def test_fake_provider_never_requires_the_key(client) -> None:
    sid = _new_session(client)
    response = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"message": "oi", "user_id": "u1", "session_id": sid},
    )
    assert response.status_code != 400


def test_blank_or_oversized_header_is_treated_as_missing() -> None:
    assert credentials.normalize_api_key("   ") is None
    assert credentials.normalize_api_key("x" * 600) is None
    assert credentials.normalize_api_key("  abc  ") == "abc"


def test_swagger_declares_the_llm_key_only_on_llm_routes() -> None:
    from app.main import app

    schema = app.openapi()
    assert schema["components"]["securitySchemes"]["LLMApiKey"]["name"] == LLM_API_KEY_HEADER
    messages = schema["paths"]["/api/v1/sessions/{session_id}/messages"]["post"]
    assert "LLMApiKey" in messages["security"][0]
    assert "LLMApiKey" not in schema["paths"]["/api/v1/skills"]["get"]["security"][0]
