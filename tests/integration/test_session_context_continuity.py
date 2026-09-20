"""T099: teste de integracao para mensagem de acompanhamento usando contexto anterior da
sessao (US4, Acceptance Scenario 1)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agent.knowledge.agent import KnowledgeAnswerDraft
from app.schemas.router import Intent, RouterDecision


async def test_followup_message_prompt_includes_prior_conversation_context(
    client: TestClient, fake_llm: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Taxas",
            "source": "manual",
            "content": "A taxa da maquininha no credito e 1,99%. No debito e 1,00%.",
        },
    )

    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.95,
        requires_clarification=False,
        reason_code="PRODUCT_QUESTION",
    )
    fake_llm[KnowledgeAnswerDraft] = KnowledgeAnswerDraft(
        message="A taxa no credito e 1,99%.", grounded_in_sources=True
    )

    session_id = client.post("/api/v1/sessions", json={"user_id": "client_xpto"}).json()[
        "session_id"
    ]

    first = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": "Qual a taxa da maquininha no credito?", "user_id": "client_xpto"},
    )
    assert first.status_code == 200

    captured_messages: list[list[dict]] = []

    async def fake_get_structured_output(schema, messages):
        if schema is KnowledgeAnswerDraft:
            captured_messages.append(messages)
            return KnowledgeAnswerDraft(message="No debito e 1,00%.", grounded_in_sources=True)
        return fake_llm[schema]

    import app.agent.knowledge.agent as knowledge_agent_module

    monkeypatch.setattr(knowledge_agent_module, "get_structured_output", fake_get_structured_output)

    second = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": "E a taxa da maquininha no debito?", "user_id": "client_xpto"},
    )

    assert second.status_code == 200
    system_prompt = captured_messages[0][0]["content"]
    assert "Contexto recente da conversa" in system_prompt
    assert "Qual a taxa da maquininha no credito?" in system_prompt
    assert "A taxa no credito e 1,99%." in system_prompt
