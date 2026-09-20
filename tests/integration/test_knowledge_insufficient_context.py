"""T059: teste de integracao para INSUFFICIENT_CONTEXT quando nao ha cobertura na base
(US1, Acceptance Scenario 2)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.schemas.agent_response import Status
from app.schemas.router import Intent, RouterDecision


def test_returns_insufficient_context_when_no_knowledge_covers_the_question(
    client: TestClient, fake_llm: dict
) -> None:
    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.9,
        requires_clarification=False,
        reason_code="PRODUCT_QUESTION",
    )
    # deliberadamente NAO configuramos KnowledgeAnswerDraft: o agente nunca deve chegar a
    # chamar o LLM de sintese quando nao ha nenhum documento na base.

    session_response = client.post("/api/v1/sessions", json={"user_id": "client_xpto"})
    session_id = session_response.json()["session_id"]

    response = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": "Pergunta sem nenhuma cobertura na base", "user_id": "client_xpto"},
    )

    body = response.json()
    assert body["status"] == Status.INSUFFICIENT_CONTEXT.value
    assert body["sources"] == []
    assert body["metadata"]["confidence"] == 0.0
    assert "invent" not in body["message"].lower()
