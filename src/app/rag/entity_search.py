"""Busca semantica generica sobre entidades com embedding proprio (Skills, Playbooks) --
reutilizado pelas rotas de busca dedicadas e pelos endpoints /search/semantic/{skills,playbooks}
(spec FR-045)."""

from __future__ import annotations

from typing import Protocol

from app.llm.embeddings_client import embed_text
from app.rag.retrievers import RetrievalResult, cosine_similarity


class EmbeddedEntity(Protocol):
    embedding: list[float]


def semantic_search_entities(
    query: str,
    entities: list,
    *,
    id_field: str,
    content_field: str,
    metadata_fn,
    top_k: int = 5,
    min_score: float = 0.0,
) -> list[RetrievalResult]:
    query_embedding = embed_text(query)
    results = [
        RetrievalResult(
            document_id=getattr(entity, id_field),
            chunk_id=None,
            score=cosine_similarity(query_embedding, entity.embedding),
            content=getattr(entity, content_field),
            metadata=metadata_fn(entity),
        )
        for entity in entities
    ]
    results = [r for r in results if r.score >= min_score]
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:top_k]
