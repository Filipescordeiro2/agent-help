"""Ferramentas do Knowledge Agent (spec plano): search_knowledge, semantic_search_knowledge,
search_product_documentation, get_document_source, search_faq, search_playbook.

Todas tipadas com Pydantic, com allowlist (apenas as operacoes aqui definidas), timeout e log
estruturado. Nenhuma exige autorizacao adicional (somente leitura de conhecimento publico
interno da Getnet).
"""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.rag.retrievers import RetrievalResult, hybrid_search, semantic_search
from app.repository.knowledge_chunks_repository import KnowledgeChunksRepository
from app.repository.knowledge_documents_repository import (
    KnowledgeDocument,
    KnowledgeDocumentsRepository,
)
from app.tools.base import BaseTool


class SearchInput(BaseModel):
    query: str
    top_k: int = 5
    min_score: float = 0.70
    filters: dict | None = None


class SearchOutput(BaseModel):
    results: list[dict]


class DocumentSourceInput(BaseModel):
    document_id: str


class DocumentSourceOutput(BaseModel):
    document: dict | None


def _results_to_dicts(results: list[RetrievalResult]) -> list[dict]:
    return [
        {
            "document_id": r.document_id,
            "chunk_id": r.chunk_id,
            "score": r.score,
            "content": r.content,
            "metadata": r.metadata,
        }
        for r in results
    ]


class SearchKnowledgeTool(BaseTool[SearchInput, SearchOutput]):
    """Busca semantica geral sobre a base de conhecimento (combina semantica + hibrida)."""

    name = "search_knowledge"

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._chunks_repo = KnowledgeChunksRepository(db)

    async def _run(self, input_data: SearchInput) -> SearchOutput:
        results = await hybrid_search(
            self._chunks_repo,
            query=input_data.query,
            top_k=input_data.top_k,
            min_score=input_data.min_score,
            filters=input_data.filters,
        )
        return SearchOutput(results=_results_to_dicts(results))


class SemanticSearchKnowledgeTool(BaseTool[SearchInput, SearchOutput]):
    """Busca puramente semantica (sem reforco de palavra-chave)."""

    name = "semantic_search_knowledge"

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._chunks_repo = KnowledgeChunksRepository(db)

    async def _run(self, input_data: SearchInput) -> SearchOutput:
        results = await semantic_search(
            self._chunks_repo,
            query=input_data.query,
            top_k=input_data.top_k,
            min_score=input_data.min_score,
            filters=input_data.filters,
        )
        return SearchOutput(results=_results_to_dicts(results))


class SearchProductDocumentationTool(BaseTool[SearchInput, SearchOutput]):
    """Busca restrita a documentacao de produto (filtra metadata.source == 'product_docs')."""

    name = "search_product_documentation"

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._chunks_repo = KnowledgeChunksRepository(db)

    async def _run(self, input_data: SearchInput) -> SearchOutput:
        filters = {**(input_data.filters or {}), "metadata.doc_type": "product_documentation"}
        results = await semantic_search(
            self._chunks_repo,
            query=input_data.query,
            top_k=input_data.top_k,
            min_score=input_data.min_score,
            filters=filters,
        )
        return SearchOutput(results=_results_to_dicts(results))


class SearchFaqTool(BaseTool[SearchInput, SearchOutput]):
    """Busca restrita a FAQ (filtra metadata.doc_type == 'faq')."""

    name = "search_faq"

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._chunks_repo = KnowledgeChunksRepository(db)

    async def _run(self, input_data: SearchInput) -> SearchOutput:
        filters = {**(input_data.filters or {}), "metadata.doc_type": "faq"}
        results = await semantic_search(
            self._chunks_repo,
            query=input_data.query,
            top_k=input_data.top_k,
            min_score=input_data.min_score,
            filters=filters,
        )
        return SearchOutput(results=_results_to_dicts(results))


class GetDocumentSourceTool(BaseTool[DocumentSourceInput, DocumentSourceOutput]):
    """Retorna os metadados de origem/versao de um documento de conhecimento."""

    name = "get_document_source"

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._documents_repo = KnowledgeDocumentsRepository(db)

    async def _run(self, input_data: DocumentSourceInput) -> DocumentSourceOutput:
        document: KnowledgeDocument | None = await self._documents_repo.get(input_data.document_id)
        return DocumentSourceOutput(document=document.model_dump(mode="json") if document else None)


class SearchPlaybookTool(BaseTool[SearchInput, SearchOutput]):
    """Busca por palavra-chave em Playbooks -- permite ao Knowledge Agent citar um Playbook
    relevante mesmo sem executa-lo (execucao e exclusiva do Customer Support Agent, FR-019)."""

    name = "search_playbook"

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._db = db

    async def _run(self, input_data: SearchInput) -> SearchOutput:
        query_terms = [t.lower() for t in input_data.query.split() if t]
        cursor = self._db["playbooks"].find({}).limit(50)
        matches: list[dict] = []
        async for playbook in cursor:
            haystack = " ".join([playbook.get("name", ""), *playbook.get("symptoms", [])]).lower()
            if any(term in haystack for term in query_terms):
                matches.append(
                    {
                        "document_id": playbook.get("playbook_id", ""),
                        "chunk_id": None,
                        "score": 1.0,
                        "content": playbook.get("name", ""),
                        "metadata": {"source": "playbooks"},
                    }
                )
        return SearchOutput(results=matches[: input_data.top_k])
