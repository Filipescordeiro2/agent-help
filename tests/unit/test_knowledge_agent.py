"""T062: teste unitario do Knowledge Agent (limiar de confianca/score, INSUFFICIENT_CONTEXT,
LLM mockado)."""

from __future__ import annotations

import pytest
from mongomock_motor import AsyncMongoMockClient

import app.agent.knowledge.agent as knowledge_agent_module
from app.agent.knowledge.agent import KnowledgeAgent, KnowledgeAnswerDraft
from app.agent.state import new_state
from app.repository.knowledge_chunks_repository import (
    KnowledgeChunk,
    KnowledgeChunksRepository,
)
from app.schemas.agent_response import Status
from app.schemas.user_message import UserMessageInput


def _state(message: str):
    return new_state(
        session_id="s1",
        execution_id="e1",
        request_id="r1",
        user_message=UserMessageInput(message=message, user_id="client_xpto"),
    )


@pytest.fixture
def db():
    return AsyncMongoMockClient()["getnet_test"]


async def test_returns_insufficient_context_when_nothing_retrieved(db) -> None:
    agent = KnowledgeAgent(db)

    response = await agent.handle(_state("pergunta sem nenhuma cobertura"))

    assert response.status == Status.INSUFFICIENT_CONTEXT
    assert response.sources == []
    assert response.metadata.confidence == 0.0


async def test_returns_grounded_answer_with_sources_when_retrieval_succeeds(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks_repo = KnowledgeChunksRepository(db)
    await chunks_repo.insert(
        KnowledgeChunk(
            chunk_id="c1",
            document_id="doc_1",
            content="A taxa da maquininha e 1.99%.",
            embedding=[1, 0, 0],
        )
    )

    async def fake_hybrid_search(_repo, **_kwargs):
        from app.rag.retrievers import RetrievalResult

        return [
            RetrievalResult(
                document_id="doc_1",
                chunk_id="c1",
                score=0.95,
                content="A taxa da maquininha e 1.99%.",
            )
        ]

    async def fake_get_structured_output(_schema, _messages) -> KnowledgeAnswerDraft:
        return KnowledgeAnswerDraft(message="A taxa e 1.99%.", grounded_in_sources=True)

    monkeypatch.setattr(knowledge_agent_module, "get_structured_output", fake_get_structured_output)
    monkeypatch.setattr("app.tools.knowledge_tools.hybrid_search", fake_hybrid_search)

    agent = KnowledgeAgent(db)
    response = await agent.handle(_state("Qual a taxa da maquininha?"))

    assert response.status == Status.OK
    assert response.sources[0].document_id == "doc_1"
    assert response.metadata.grounded_in_sources is True
    assert response.metadata.confidence == 0.95
