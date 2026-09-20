"""Servico de memoria (longo prazo/semantica/episodica) -- criterios de relevancia,
privacidade e retencao antes de persistir (spec FR-031 a FR-034)."""

from __future__ import annotations

import uuid

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.llm.embeddings_client import embed_text
from app.rag.retrievers import cosine_similarity
from app.repository.memory_repository import (
    MemoryRecord,
    MemoryRecordsRepository,
    MemoryType,
    RetentionPolicy,
    SemanticMemoriesRepository,
    SemanticMemory,
)

# Retencao padrao por tipo (spec.md Assumptions -- ponto de partida a confirmar com
# compliance/LGPD antes de producao, ver research.md).
_DEFAULT_TTL_DAYS = {
    MemoryType.EPISODIC: 730,  # ~24 meses
    MemoryType.SEMANTIC: 730,  # ~24 meses
    MemoryType.LONG_TERM: 36500,  # enquanto a relacao com o cliente estiver ativa (sentinela)
}

MIN_CONFIDENCE_TO_PERSIST = 0.3
MAX_CONTENT_LENGTH = 4000


class MemoryValidationError(Exception):
    """Levantado quando o conteudo nao passa nos criterios de relevancia/privacidade (FR-033)."""


def meets_relevance_and_privacy_criteria(content: str, confidence: float) -> bool:
    """FR-033: nem toda interacao e armazenada automaticamente -- aplica um filtro minimo de
    relevancia (confianca) antes de persistir. Conteudo vazio nunca e relevante."""
    if not content or not content.strip():
        return False
    if confidence < MIN_CONFIDENCE_TO_PERSIST:
        return False
    if len(content) > MAX_CONTENT_LENGTH:
        return False
    return True


async def create_memory_record(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    type_: str,
    content: str,
    origin: str,
    confidence: float = 1.0,
    session_id: str | None = None,
    interaction_id: str | None = None,
) -> MemoryRecord | SemanticMemory:
    if not meets_relevance_and_privacy_criteria(content, confidence):
        raise MemoryValidationError(
            "conteudo nao atende aos criterios minimos de relevancia/privacidade (FR-033)"
        )

    memory_type = MemoryType(type_)
    retention_policy = RetentionPolicy(ttl_days=_DEFAULT_TTL_DAYS[memory_type])
    memory_id = str(uuid.uuid4())

    if memory_type == MemoryType.SEMANTIC:
        record = SemanticMemory(
            memory_id=memory_id,
            user_id=user_id,
            session_id=session_id,
            interaction_id=interaction_id,
            type=memory_type,
            content=content,
            origin=origin,
            confidence=confidence,
            retention_policy=retention_policy,
            embedding=embed_text(content),
        )
        return await SemanticMemoriesRepository(db).insert(record)

    record = MemoryRecord(
        memory_id=memory_id,
        user_id=user_id,
        session_id=session_id,
        interaction_id=interaction_id,
        type=memory_type,
        content=content,
        origin=origin,
        confidence=confidence,
        retention_policy=retention_policy,
    )
    return await MemoryRecordsRepository(db).insert(record)


async def list_memory(
    db: AsyncIOMotorDatabase, *, user_id: str, type_: str | None = None
) -> list[dict]:
    memory_type = MemoryType(type_) if type_ else None
    if memory_type == MemoryType.SEMANTIC:
        semantic_only = await SemanticMemoriesRepository(db).list_for_user(user_id)
        return [r.model_dump(mode="json") for r in semantic_only]

    non_semantic = await MemoryRecordsRepository(db).list_for_user(user_id, type_=memory_type)
    records = [r.model_dump(mode="json") for r in non_semantic]
    if memory_type is None:
        semantic = await SemanticMemoriesRepository(db).list_for_user(user_id)
        records += [r.model_dump(mode="json") for r in semantic]
    return records


async def search_semantic_memory(
    db: AsyncIOMotorDatabase, *, user_id: str, query: str, top_k: int = 5, min_score: float = 0.0
) -> list[dict]:
    query_embedding = embed_text(query)
    records = await SemanticMemoriesRepository(db).list_for_user(user_id)
    scored = [
        {
            "memory": r.model_dump(mode="json"),
            "score": cosine_similarity(query_embedding, r.embedding),
        }
        for r in records
    ]
    scored = [s for s in scored if s["score"] >= min_score]
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored[:top_k]


async def delete_memory_record(db: AsyncIOMotorDatabase, memory_id: str) -> bool:
    """Exclusao sob demanda do titular (FR-034, direito a eliminacao/LGPD)."""
    deleted = await MemoryRecordsRepository(db).delete(memory_id)
    if deleted:
        return True
    return await SemanticMemoriesRepository(db).delete(memory_id)
