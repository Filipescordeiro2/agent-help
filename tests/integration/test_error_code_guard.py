"""Pergunta com codigo de erro: trecho da base de OUTRO codigo nao conta como resposta."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agent.knowledge.agent import KnowledgeAgent, KnowledgeAnswerDraft
from app.schemas.grounding import GroundingEvaluation
from app.schemas.router import Intent, RouterDecision
from app.tools.web_tools import WebSearchOutput


def test_chunk_of_another_error_code_sends_the_agent_to_the_web(
    client: TestClient, fake_llm: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Erro ADQ 4-83",
            "source": "manual",
            "content": "Erro ADQ 4-83 erro de integracao com as bandeiras: use a funcao 38.",
        },
    )
    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.9,
        requires_clarification=False,
        reason_code="ERRO",
    )
    fake_llm[KnowledgeAnswerDraft] = KnowledgeAnswerDraft(
        message="Siga os passos do erro ADQ 4-91.", grounded_in_sources=True
    )
    fake_llm[GroundingEvaluation] = GroundingEvaluation(
        score=5,
        reasoning="ok",
        adheres_to_question=True,
        uses_context_correctly=True,
        no_unsupported_claims=True,
        is_complete=True,
    )
    web_calls: list[str] = []

    async def fake_web(self, message: str) -> WebSearchOutput:
        web_calls.append(message)
        return WebSearchOutput(
            results=[
                {
                    "document_id": "w491",
                    "chunk_id": "c491",
                    "url": "https://site.exemplo.com/erro-adq-4-91",
                    "title": "Erro ADQ 4-91",
                    "content": "Erro ADQ 4-91: erro na aprovacao da venda. Passos...",
                    "score": 0.8,
                }
            ],
            errors=[],
        )

    monkeypatch.setattr(KnowledgeAgent, "_search_web", fake_web)
    sid = client.post("/api/v1/sessions", json={"user_id": "u1"}).json()["session_id"]

    body = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"message": "estou com o Erro ADQ 4-91: erro na aprovacao da venda", "user_id": "u1"},
    ).json()

    assert len(web_calls) == 1  # o trecho do 4-83 foi descartado -> a base "nao respondeu"
    assert [s["url"] for s in body["sources"]] == ["https://site.exemplo.com/erro-adq-4-91"]
