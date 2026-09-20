"""Fluxo de demonstracao offline (`LLM_PROVIDER=fake`): a plataforma responde de ponta a ponta,
sem credenciais e sem patches -- exatamente o que o `docker compose up` com o .env de exemplo
entrega (Router por palavras-chave, agentes simulados, grounding real do grafo)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.llm.fake_defaults import _router_default
from app.llm.openrouter_client import clear_chat_model_cache
from app.repository.playbooks_repository import PlaybooksRepository
from app.schemas.agent_response import AgentResponse, Status
from app.schemas.router import Intent
from tests.playbook_fixtures import build_device_connection_playbook


@pytest.fixture(autouse=True)
def _fake_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "llm_provider", "fake")
    clear_chat_model_cache()
    yield
    clear_chat_model_cache()


def _ask(client: TestClient, message: str) -> dict:
    session_id = client.post("/api/v1/sessions", json={"user_id": "client_xpto"}).json()[
        "session_id"
    ]
    response = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": message, "user_id": "client_xpto", "session_id": session_id},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    AgentResponse.model_validate(body)
    return body


def _ingest_fees(client: TestClient) -> None:
    client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Taxas da maquininha",
            "source": "manual_produto",
            "content": "A taxa padrao da maquininha e 1,99% por transacao no credito.",
            "product": "maquininha",
        },
    )


def test_router_default_decides_by_keywords() -> None:
    assert _router_default("Quais as taxas?").intent == Intent.KNOWLEDGE
    assert _router_default("Minha maquininha nao conecta").intent == Intent.CUSTOMER_SUPPORT
    assert _router_default("taxa e nao conecta").intent == Intent.MULTI_AGENT
    assert _router_default("oi").intent == Intent.CLARIFICATION_REQUIRED


def test_knowledge_question_is_answered_with_sources_and_grounding(client: TestClient) -> None:
    _ingest_fees(client)

    body = _ask(client, "Quais as taxas da maquininha?")

    assert body["status"] == Status.OK.value and body["agent"] == "knowledge_agent"
    assert body["sources"] and "1,99%" in body["message"] and "modo fake" in body["message"]
    assert body["metadata"]["grounding_score"] == 5


async def test_device_problem_is_resolved_by_the_customer_support_playbook(
    client: TestClient, test_db
) -> None:
    await PlaybooksRepository(test_db).insert(build_device_connection_playbook())

    body = _ask(client, "Minha maquininha nao conecta")

    assert body["status"] == Status.OK.value and body["agent"] == "customer_support_agent"
    assert "Wi-Fi" in body["message"] and body["metadata"]["grounding_score"] == 5


def test_ambiguous_message_asks_for_clarification(client: TestClient) -> None:
    body = _ask(client, "Preciso de ajuda")
    assert body["status"] == Status.CLARIFICATION_REQUIRED.value


def test_greeting_gets_a_friendly_welcome_listing_what_the_agent_can_do(client: TestClient) -> None:
    body = _ask(client, "Ola")

    assert body["status"] == Status.OK.value and body["agent"] == "welcome"
    assert body["sources"] == [] and body["metadata"]["grounding_score"] is None
    assert body["message"].startswith("Olá!")
    for topic in ("estorno", "Pix", "taxa", "maquininha", "senha", "central de atendimento"):
        assert topic.lower() in body["message"].lower()


async def test_mixed_message_runs_both_agents(client: TestClient, test_db) -> None:
    await PlaybooksRepository(test_db).insert(build_device_connection_playbook())
    _ingest_fees(client)

    body = _ask(client, "Qual a taxa da maquininha e por que ela nao conecta?")

    assert body["agent"] == "multi_agent" and body["status"] == Status.OK.value
    assert "knowledge_agent" in body["message"] and "customer_support_agent" in body["message"]
    assert body["metadata"]["grounding_score"] == 5
