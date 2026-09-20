"""T060: teste de integracao para a distincao auditavel entre conteudo recuperado e
inferencia do modelo (metadata.grounded_in_sources) -- spec FR-015."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.agent.knowledge.agent import KnowledgeAnswerDraft
from app.schemas.router import Intent, RouterDecision


def _ingest_and_ask(client: TestClient, fake_llm: dict, *, grounded: bool) -> dict:
    client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Taxas",
            "source": "manual",
            "content": "A taxa da maquininha e 1,99% no credito.",
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
        message="resposta", grounded_in_sources=grounded
    )

    session_id = client.post("/api/v1/sessions", json={"user_id": "client_xpto"}).json()[
        "session_id"
    ]
    response = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": "Qual a taxa da maquininha?", "user_id": "client_xpto"},
    )
    return response.json()


def test_reformulation_of_source_is_marked_grounded_true(
    client: TestClient, fake_llm: dict
) -> None:
    body = _ingest_and_ask(client, fake_llm, grounded=True)
    assert body["metadata"]["grounded_in_sources"] is True


def test_answer_with_added_inference_is_marked_grounded_false(
    client: TestClient, fake_llm: dict
) -> None:
    body = _ingest_and_ask(client, fake_llm, grounded=False)
    assert body["metadata"]["grounded_in_sources"] is False
