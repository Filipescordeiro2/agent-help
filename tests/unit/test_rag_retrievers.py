"""T063: teste unitario dos retrievers semantico e hibrido (score minimo, top_k, filtros,
deduplicacao)."""

from __future__ import annotations

import pytest
from mongomock_motor import AsyncMongoMockClient

import app.rag.retrievers as retrievers_module
from app.rag.retrievers import hybrid_search, semantic_search
from app.repository.knowledge_chunks_repository import (
    KnowledgeChunk,
    KnowledgeChunksRepository,
)


@pytest.fixture
def repo():
    db = AsyncMongoMockClient()["getnet_test"]
    return KnowledgeChunksRepository(db)


def _fake_embed(text: str) -> list[float]:
    """Vetor deterministico (baseado em ord(), nunca em hash() -- randomizado por processo)."""
    vector = [0.0] * 4
    vector[ord(text[:1]) % 4] = 1.0
    return vector


@pytest.fixture(autouse=True)
def _patch_embeddings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(retrievers_module, "embed_text", _fake_embed)


async def _seed(
    repo: KnowledgeChunksRepository,
    *,
    chunk_id: str,
    document_id: str,
    content: str,
    vector: list[float],
    metadata: dict | None = None,
) -> None:
    await repo.insert(
        KnowledgeChunk(
            chunk_id=chunk_id,
            document_id=document_id,
            content=content,
            embedding=vector,
            metadata=metadata or {},
        )
    )


async def test_semantic_search_respects_min_score(repo: KnowledgeChunksRepository) -> None:
    await _seed(
        repo, chunk_id="c1", document_id="d1", content="taxa da maquininha", vector=[1, 0, 0, 0]
    )
    await _seed(
        repo,
        chunk_id="c2",
        document_id="d2",
        content="assunto totalmente diferente",
        vector=[0, 1, 0, 0],
    )

    results = await semantic_search(repo, query="taxa", top_k=10, min_score=0.99)

    assert len(results) == 1
    assert results[0].document_id == "d1"


async def test_semantic_search_respects_top_k(repo: KnowledgeChunksRepository) -> None:
    for i in range(5):
        await _seed(
            repo,
            chunk_id=f"c{i}",
            document_id=f"d{i}",
            content="taxa da maquininha",
            vector=[1, 0, 0, 0],
        )

    results = await semantic_search(repo, query="taxa", top_k=2, min_score=0.0)

    assert len(results) == 2


async def test_semantic_search_applies_metadata_filters(repo: KnowledgeChunksRepository) -> None:
    await _seed(
        repo,
        chunk_id="c1",
        document_id="d1",
        content="taxa",
        vector=[1, 0, 0, 0],
        metadata={"product": "maquininha"},
    )
    await _seed(
        repo,
        chunk_id="c2",
        document_id="d2",
        content="taxa",
        vector=[1, 0, 0, 0],
        metadata={"product": "pix"},
    )

    results = await semantic_search(
        repo, query="taxa", top_k=10, min_score=0.0, filters={"metadata.product": "pix"}
    )

    assert len(results) == 1
    assert results[0].document_id == "d2"


async def test_semantic_search_deduplicates_by_document_keeping_best_score(
    repo: KnowledgeChunksRepository,
) -> None:
    await _seed(repo, chunk_id="c1", document_id="d1", content="taxa a", vector=[1, 0, 0, 0])
    await _seed(repo, chunk_id="c2", document_id="d1", content="taxa b", vector=[1, 0, 0, 0])

    results = await semantic_search(repo, query="taxa", top_k=10, min_score=0.0)

    assert len(results) == 1
    assert results[0].document_id == "d1"


async def test_hybrid_search_boosts_keyword_matches(repo: KnowledgeChunksRepository) -> None:
    await _seed(
        repo,
        chunk_id="c1",
        document_id="d1",
        content="informacao sobre maquininha e taxas",
        vector=[1, 0, 0, 0],
    )
    await _seed(
        repo,
        chunk_id="c2",
        document_id="d2",
        content="informacao nao relacionada",
        vector=[1, 0, 0, 0],
    )

    results = await hybrid_search(repo, query="maquininha taxas", top_k=10, min_score=0.0)

    assert results[0].document_id == "d1"
    assert results[0].score >= results[1].score
