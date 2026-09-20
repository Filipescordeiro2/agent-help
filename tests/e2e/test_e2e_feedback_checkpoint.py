"""T132: teste E2E "feedback -> persistencia -> recuperacao por checkpoint"."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.agent.knowledge.agent import KnowledgeAnswerDraft
from app.schemas.router import Intent, RouterDecision


def test_end_to_end_feedback_flow_referencing_a_persisted_execution(
    client: TestClient, fake_llm: dict
) -> None:
    client.post(
        "/api/v1/knowledge/documents",
        json={"title": "Taxas", "source": "manual", "content": "A taxa da maquininha e 1,99%."},
    )
    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.9,
        requires_clarification=False,
        reason_code="PRODUCT_QUESTION",
    )
    fake_llm[KnowledgeAnswerDraft] = KnowledgeAnswerDraft(
        message="A taxa e 1,99%.", grounded_in_sources=True
    )

    session_id = client.post("/api/v1/sessions", json={"user_id": "client_xpto"}).json()[
        "session_id"
    ]
    message_response = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": "Qual a taxa da maquininha?", "user_id": "client_xpto"},
    )
    execution_id = message_response.json()["metadata"]["execution_id"]

    # 1. A execucao ja esta persistida e recuperavel (checkpoint gravado por persist_node).
    execution_before = client.get(f"/api/v1/executions/{execution_id}")
    assert execution_before.status_code == 200
    assert execution_before.json()["status"] == "completed"

    # 2. Feedback referenciando essa execucao e submetido e fica disponivel para consulta.
    feedback_response = client.post(
        "/api/v1/feedback",
        json={
            "user_id": "client_xpto",
            "session_id": session_id,
            "message_id": "m1",
            "execution_id": execution_id,
            "problem_classification": "RESOLVED",
            "agent_invoked": "knowledge_agent",
            "rating": 5.0,
        },
    )
    assert feedback_response.status_code == 200
    feedback_id = feedback_response.json()["feedback_id"]

    feedback_list = client.get("/api/v1/feedback").json()
    assert any(f["feedback_id"] == feedback_id for f in feedback_list)

    # 3. A execucao original continua recuperavel apos o feedback -- o registro nao foi
    # alterado pelo feedback (FR-042), permanece o mesmo "checkpoint" persistido.
    execution_after = client.get(f"/api/v1/executions/{execution_id}")
    assert execution_after.json() == execution_before.json()
