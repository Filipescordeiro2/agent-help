"""Retrievers semantico e hibrido sobre knowledge_chunks (spec FR-011 a FR-013).

Score minimo, top_k e filtros de metadados sao sempre respeitados; a busca por palavra-chave
e sempre complementar, nunca substitui a busca semantica (spec FR-011).

A similaridade e computada em Python (cosseno) sobre os chunks recuperados do repositorio.
Em um cluster Atlas real, a etapa de recuperacao inicial pode ser otimizada com o operador
nativo `$vectorSearch`; a pontuacao/filtragem aqui descrita permanece a mesma.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.llm.embeddings_client import embed_text
from app.repository.knowledge_chunks_repository import (
    KnowledgeChunk,
    KnowledgeChunksRepository,
)


@dataclass
class RetrievalResult:
    document_id: str
    chunk_id: str
    score: float
    content: str
    metadata: dict = field(default_factory=dict)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


def _deduplicate_by_document(results: list[RetrievalResult]) -> list[RetrievalResult]:
    """Mantem apenas o chunk de maior score por documento (dedup, spec FR-011/013)."""
    best_by_document: dict[str, RetrievalResult] = {}
    for result in results:
        current_best = best_by_document.get(result.document_id)
        if current_best is None or result.score > current_best.score:
            best_by_document[result.document_id] = result
    return sorted(best_by_document.values(), key=lambda r: r.score, reverse=True)


async def semantic_search(
    repo: KnowledgeChunksRepository,
    *,
    query: str,
    top_k: int = 5,
    min_score: float = 0.70,
    filters: dict | None = None,
    deduplicate: bool = True,
) -> list[RetrievalResult]:
    query_embedding = embed_text(query)
    chunks: list[KnowledgeChunk] = await repo.all_chunks(filters=filters)

    scored = [
        RetrievalResult(
            document_id=c.document_id,
            chunk_id=c.chunk_id,
            score=cosine_similarity(query_embedding, c.embedding),
            content=c.content,
            metadata=c.metadata,
        )
        for c in chunks
    ]
    scored = [r for r in scored if r.score >= min_score]
    scored.sort(key=lambda r: r.score, reverse=True)

    if deduplicate:
        scored = _deduplicate_by_document(scored)

    return scored[:top_k]


def _keyword_score(query: str, content: str) -> float:
    query_terms = {t.lower() for t in query.split() if t}
    if not query_terms:
        return 0.0
    content_lower = content.lower()
    matches = sum(1 for term in query_terms if term in content_lower)
    return matches / len(query_terms)


async def hybrid_search(
    repo: KnowledgeChunksRepository,
    *,
    query: str,
    top_k: int = 5,
    min_score: float = 0.70,
    filters: dict | None = None,
    keyword_weight: float = 0.2,
) -> list[RetrievalResult]:
    """Busca semantica com um pequeno reforco de score por palavra-chave (complementar, nunca
    substituto -- spec FR-011). O peso da palavra-chave e deliberadamente baixo."""
    semantic_results = await semantic_search(
        repo, query=query, top_k=top_k * 3, min_score=0.0, filters=filters
    )

    boosted = [
        RetrievalResult(
            document_id=r.document_id,
            chunk_id=r.chunk_id,
            score=min(
                1.0,
                r.score * (1 - keyword_weight) + _keyword_score(query, r.content) * keyword_weight,
            ),
            content=r.content,
            metadata=r.metadata,
        )
        for r in semantic_results
    ]
    boosted = [r for r in boosted if r.score >= min_score]
    boosted.sort(key=lambda r: r.score, reverse=True)
    return _deduplicate_by_document(boosted)[:top_k]
