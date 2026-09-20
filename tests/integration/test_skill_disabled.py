"""T108: teste de integracao para uma Skill desabilitada nao ser aplicada (US5, Acceptance
Scenario 2)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agent.knowledge.agent import KnowledgeAnswerDraft
from app.schemas.router import Intent, RouterDecision


def _ingest_knowledge(client: TestClient) -> None:
    client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Taxas",
            "source": "manual",
            "content": "A taxa da maquininha e 1,99% no credito.",
        },
    )


def _create_skill(client: TestClient, *, enabled: bool) -> None:
    response = client.post(
        "/api/v1/skills",
        json={
            "name": "Orientacao de taxas",
            "description": "Skill de orientacao sobre taxas",
            "owning_agent": "knowledge_agent",
            "keywords": ["taxa"],
            "instructions": "Sempre mencione que taxas podem variar por plano contratado.",
            "allowed_tools": [],
            "enabled": enabled,
        },
    )
    assert response.status_code == 200


async def test_disabled_skill_guidance_is_never_included_in_the_prompt(
    client: TestClient, fake_llm: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ingest_knowledge(client)
    _create_skill(client, enabled=False)

    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.95,
        requires_clarification=False,
        reason_code="PRODUCT_QUESTION",
    )

    captured_messages: list[list[dict]] = []

    async def fake_get_structured_output(schema, messages):
        if schema is KnowledgeAnswerDraft:
            captured_messages.append(messages)
        return fake_llm[schema]

    import app.agent.knowledge.agent as knowledge_agent_module

    fake_llm[KnowledgeAnswerDraft] = KnowledgeAnswerDraft(
        message="A taxa e 1,99%.", grounded_in_sources=True
    )
    monkeypatch.setattr(knowledge_agent_module, "get_structured_output", fake_get_structured_output)

    session_id = client.post("/api/v1/sessions", json={"user_id": "client_xpto"}).json()[
        "session_id"
    ]
    response = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": "Qual a taxa da maquininha?", "user_id": "client_xpto"},
    )

    assert response.status_code == 200
    system_prompt = captured_messages[0][0]["content"]
    assert "Orientacoes de Skills aplicaveis" not in system_prompt
    assert "podem variar por plano contratado" not in system_prompt


async def test_enabled_skill_guidance_is_included_in_the_prompt(
    client: TestClient, fake_llm: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ingest_knowledge(client)
    _create_skill(client, enabled=True)

    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.95,
        requires_clarification=False,
        reason_code="PRODUCT_QUESTION",
    )

    captured_messages: list[list[dict]] = []

    async def fake_get_structured_output(schema, messages):
        if schema is KnowledgeAnswerDraft:
            captured_messages.append(messages)
        return fake_llm[schema]

    import app.agent.knowledge.agent as knowledge_agent_module

    fake_llm[KnowledgeAnswerDraft] = KnowledgeAnswerDraft(
        message="A taxa e 1,99%.", grounded_in_sources=True
    )
    monkeypatch.setattr(knowledge_agent_module, "get_structured_output", fake_get_structured_output)

    session_id = client.post("/api/v1/sessions", json={"user_id": "client_xpto"}).json()[
        "session_id"
    ]
    client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": "Qual a taxa da maquininha?", "user_id": "client_xpto"},
    )

    system_prompt = captured_messages[0][0]["content"]
    assert "Orientacoes de Skills aplicaveis" in system_prompt
    assert "podem variar por plano contratado" in system_prompt
