"""T130: teste E2E "pergunta sobre produto -> Knowledge Agent -> resposta fundamentada"
(spec quickstart.md, secao 3)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.agent.knowledge.agent import KnowledgeAnswerDraft
from app.schemas.agent_response import Status
from app.schemas.router import Intent, RouterDecision


def test_end_to_end_product_question_returns_grounded_answer(
    client: TestClient, fake_llm: dict
) -> None:
    # 1. Ingerir conhecimento (equivalente a POST /api/v1/knowledge/documents do quickstart).
    ingest = client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Taxas da maquininha",
            "source": "manual_produto",
            "content": "A taxa padrao da maquininha e 1,99% por transacao no credito.",
            "product": "maquininha",
        },
    )
    assert ingest.status_code == 200

    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.94,
        requires_clarification=False,
        reason_code="DEVICE_CONNECTION_PROBLEM",
    )
    fake_llm[KnowledgeAnswerDraft] = KnowledgeAnswerDraft(
        message="A taxa padrao da maquininha e 1,99% no credito.", grounded_in_sources=True
    )

    # 2. Criar sessao.
    session = client.post("/api/v1/sessions", json={"user_id": "client_xpto"})
    assert session.status_code == 200
    session_id = session.json()["session_id"]

    # 3. Enviar a pergunta.
    response = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": "Quais as taxas da maquininha?", "user_id": "client_xpto"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == Status.OK.value
    assert body["agent"] == "knowledge_agent"
    assert len(body["sources"]) >= 1
    assert body["metadata"]["grounded_in_sources"] is True

    # 4. A execucao e recuperavel via /executions/{id} (auditoria, SC-008).
    execution_id = body["metadata"]["execution_id"]
    execution = client.get(f"/api/v1/executions/{execution_id}")
    assert execution.status_code == 200
    assert execution.json()["routing_decision"]["intent"] == "KNOWLEDGE"
