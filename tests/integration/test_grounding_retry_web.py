"""Se o grounding reprova uma resposta que veio so da base, a retentativa tambem consulta a web."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agent.knowledge.agent import KnowledgeAgent, KnowledgeAnswerDraft
from app.schemas.agent_response import Status
from app.schemas.grounding import GroundingEvaluation
from app.schemas.router import Intent, RouterDecision
from app.tools.web_tools import WebSearchOutput


def _evaluation(score: int) -> GroundingEvaluation:
    return GroundingEvaluation(
        score=score,
        reasoning="r",
        adheres_to_question=True,
        uses_context_correctly=score > 2,
        no_unsupported_claims=True,
        is_complete=score > 2,
    )


def test_retry_after_low_grounding_also_consults_the_web(
    client: TestClient, fake_llm: dict, monkeypatch: pytest.MonkeyPatch
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
        reason_code="TAXA",
    )
    fake_llm[KnowledgeAnswerDraft] = KnowledgeAnswerDraft(
        message="A taxa da maquininha e 1,99%.", grounded_in_sources=True
    )
    scores = iter([2, 5])
    fake_llm[GroundingEvaluation] = lambda: _evaluation(next(scores))
    web_calls: list[str] = []

    async def fake_web(self, message: str) -> WebSearchOutput:
        web_calls.append(message)
        return WebSearchOutput(
            results=[
                {
                    "document_id": "w1",
                    "chunk_id": "c1",
                    "url": "https://site.exemplo.com/taxas",
                    "title": "Taxas",
                    "content": "Pagina de taxas.",
                    "score": 0.8,
                }
            ],
            errors=[],
        )

    monkeypatch.setattr(KnowledgeAgent, "_search_web", fake_web)
    sid = client.post("/api/v1/sessions", json={"user_id": "u1"}).json()["session_id"]

    body = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"message": "Qual a taxa da maquininha?", "user_id": "u1"},
    ).json()

    assert body["status"] == Status.OK.value and body["metadata"]["grounding_score"] == 5
    assert len(web_calls) == 1  # so na retentativa; a 1a tentativa respondeu da base
    assert any(s["url"] == "https://site.exemplo.com/taxas" for s in body["sources"])
