"""T057: teste de integracao de contrato para POST /api/v1/sessions/{id}/messages com uma
pergunta de conhecimento -- valida conformidade estrita com AgentResponse."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.agent.knowledge.agent import KnowledgeAnswerDraft
from app.schemas.agent_response import Status
from app.schemas.router import Intent, RouterDecision


def _create_session(client: TestClient) -> str:
    response = client.post("/api/v1/sessions", json={"user_id": "client_xpto"})
    assert response.status_code == 200
    return response.json()["session_id"]


def test_message_response_matches_agent_response_contract(
    client: TestClient, fake_llm: dict
) -> None:
    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.95,
        requires_clarification=False,
        reason_code="PRODUCT_QUESTION",
    )
    fake_llm[KnowledgeAnswerDraft] = KnowledgeAnswerDraft(
        message="Nao encontrei informacoes suficientes para responder com seguranca.",
        grounded_in_sources=True,
    )

    session_id = _create_session(client)
    response = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": "Qual a taxa da maquininha?", "user_id": "client_xpto"},
    )

    assert response.status_code == 200
    body = response.json()

    # Campos obrigatorios do contrato universal (FR-002 a FR-005)
    assert set(["status", "agent", "message", "sources", "metadata"]).issubset(body.keys())
    assert body["status"] in {s.value for s in Status}
    assert isinstance(body["message"], str) and body["message"]
    assert isinstance(body["sources"], list)
    assert "execution_id" in body["metadata"]
    assert "confidence" in body["metadata"]


def test_message_endpoint_never_returns_raw_string_body(client: TestClient, fake_llm: dict) -> None:
    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.95,
        requires_clarification=False,
        reason_code="PRODUCT_QUESTION",
    )
    fake_llm[KnowledgeAnswerDraft] = KnowledgeAnswerDraft(message="x", grounded_in_sources=True)

    session_id = _create_session(client)
    response = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": "Qual a taxa?", "user_id": "client_xpto"},
    )

    assert isinstance(response.json(), dict)
