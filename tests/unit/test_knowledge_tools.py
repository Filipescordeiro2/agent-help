"""T065: teste unitario das ferramentas do Knowledge Agent -- schema, allowlist, timeout.

Simetrico a T085 (ferramentas do Customer Support Agent)."""

from __future__ import annotations

import asyncio

import pytest
from mongomock_motor import AsyncMongoMockClient

from app.repository.knowledge_chunks_repository import (
    KnowledgeChunk,
    KnowledgeChunksRepository,
)
from app.tools.base import ToolTimeoutError
from app.tools.knowledge_tools import (
    DocumentSourceInput,
    GetDocumentSourceTool,
    SearchFaqTool,
    SearchInput,
    SearchKnowledgeTool,
    SearchPlaybookTool,
    SearchProductDocumentationTool,
    SemanticSearchKnowledgeTool,
)


@pytest.fixture
def db():
    return AsyncMongoMockClient()["getnet_test"]


def test_all_knowledge_tools_have_distinct_names() -> None:
    names = {
        SearchKnowledgeTool.name,
        SemanticSearchKnowledgeTool.name,
        SearchProductDocumentationTool.name,
        GetDocumentSourceTool.name,
        SearchFaqTool.name,
        SearchPlaybookTool.name,
    }
    assert names == {
        "search_knowledge",
        "semantic_search_knowledge",
        "search_product_documentation",
        "get_document_source",
        "search_faq",
        "search_playbook",
    }


def test_knowledge_tools_do_not_require_explicit_authorization() -> None:
    # Apenas leitura de conhecimento publico interno -- nao exige autorizacao adicional
    # (diferente das tools de escrita do Customer Support Agent, ver T085).
    for tool_cls in (
        SearchKnowledgeTool,
        SemanticSearchKnowledgeTool,
        SearchProductDocumentationTool,
        GetDocumentSourceTool,
        SearchFaqTool,
        SearchPlaybookTool,
    ):
        assert tool_cls.requires_explicit_authorization is False


async def test_search_knowledge_input_schema_validates_query(db) -> None:
    tool = SearchKnowledgeTool(db)
    result = await tool.run(SearchInput(query="taxa da maquininha", top_k=3))
    assert result.results == []  # base vazia, mas nao levanta excecao


async def test_search_product_documentation_filters_by_doc_type(db) -> None:
    repo = KnowledgeChunksRepository(db)
    await repo.insert(
        KnowledgeChunk(
            chunk_id="c1",
            document_id="d1",
            content="doc de produto",
            embedding=[1, 0, 0],
            metadata={"doc_type": "product_documentation"},
        )
    )
    await repo.insert(
        KnowledgeChunk(
            chunk_id="c2",
            document_id="d2",
            content="doc de faq",
            embedding=[1, 0, 0],
            metadata={"doc_type": "faq"},
        )
    )

    tool = SearchProductDocumentationTool(db)
    result = await tool.run(SearchInput(query="doc", top_k=10, min_score=0.0))

    assert all(r["document_id"] == "d1" for r in result.results)


async def test_get_document_source_returns_none_when_missing(db) -> None:
    tool = GetDocumentSourceTool(db)
    result = await tool.run(DocumentSourceInput(document_id="does-not-exist"))
    assert result.document is None


async def test_search_playbook_matches_by_keyword(db) -> None:
    await db["playbooks"].insert_one(
        {
            "playbook_id": "pb1",
            "name": "Maquininha sem conexao",
            "symptoms": ["dispositivo offline"],
        }
    )

    tool = SearchPlaybookTool(db)
    result = await tool.run(SearchInput(query="maquininha", top_k=5))

    assert len(result.results) == 1
    assert result.results[0]["document_id"] == "pb1"


async def test_tool_enforces_timeout(db) -> None:
    class SlowTool(SearchKnowledgeTool):
        timeout_seconds = 0.01

        async def _run(self, _input_data):
            await asyncio.sleep(1)

    tool = SlowTool(db)
    with pytest.raises(ToolTimeoutError):
        await tool.run(SearchInput(query="x"))
