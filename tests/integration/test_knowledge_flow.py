"""T058: teste de integracao para resposta fundamentada com fontes (US1, Acceptance Scenario 1)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.agent.knowledge.agent import KnowledgeAnswerDraft
from app.schemas.agent_response import Status
from app.schemas.router import Intent, RouterDecision


def test_knowledge_question_returns_ok_with_sources(client: TestClient, fake_llm: dict) -> None:
    # Ingesta um documento cujo conteudo cobre a pergunta (mesma palavra-chave usada pelo
    # fixture fake_embeddings para garantir alta similaridade semantica).
    ingest_response = client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Taxas da maquininha",
            "source": "manual_produto",
            "content": "A taxa padrao da maquininha e 1,99% por transacao no credito.",
            "product": "maquininha",
        },
    )
    assert ingest_response.status_code == 200

    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.95,
        requires_clarification=False,
        reason_code="PRODUCT_QUESTION",
    )
    fake_llm[KnowledgeAnswerDraft] = KnowledgeAnswerDraft(
        message="A taxa padrao da maquininha e 1,99% por transacao no credito.",
        grounded_in_sources=True,
    )

    session_response = client.post("/api/v1/sessions", json={"user_id": "client_xpto"})
    session_id = session_response.json()["session_id"]

    response = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": "Qual a taxa da maquininha?", "user_id": "client_xpto"},
    )

    body = response.json()
    assert body["status"] == Status.OK.value
    assert body["agent"] == "knowledge_agent"
    assert len(body["sources"]) >= 1
    assert body["sources"][0]["score"] > 0
    assert body["metadata"]["grounded_in_sources"] is True
