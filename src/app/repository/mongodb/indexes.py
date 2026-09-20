"""Criacao de indices, incluindo os 4 indices vetoriais do Atlas Vector Search.

A dimensao de cada indice vetorial vem de EMBEDDING_DIMENSIONS -- nunca um valor fixo no
codigo (requisito explicito do usuario, ver data-model.md secao "Indices vetoriais").
"""

from __future__ import annotations

import asyncio

import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING
from pymongo.errors import OperationFailure

from app.config.settings import get_settings

logger = structlog.get_logger(__name__)

_VECTOR_INDEX_COLLECTIONS = ("knowledge_chunks", "semantic_memories", "skills", "playbooks")


def _vector_index_definition(dimensions: int) -> dict:
    return {
        "name": "embedding_vector_index",
        "type": "vectorSearch",
        "definition": {
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": dimensions,
                    "similarity": "cosine",
                }
            ]
        },
    }


async def create_indexes(db: AsyncIOMotorDatabase) -> None:
    settings = get_settings()

    await db["sessions"].create_index([("user_id", ASCENDING)])
    await db["sessions"].create_index([("session_id", ASCENDING)], unique=True)
    await db["messages"].create_index([("session_id", ASCENDING), ("created_at", ASCENDING)])
    await db["agent_executions"].create_index([("execution_id", ASCENDING)], unique=True)
    await db["agent_executions"].create_index([("session_id", ASCENDING)])
    await db["graph_checkpoints"].create_index(
        [("execution_id", ASCENDING), ("created_at", ASCENDING)]
    )
    await db["audit_events"].create_index([("session_id", ASCENDING), ("created_at", ASCENDING)])
    await db["knowledge_documents"].create_index([("document_id", ASCENDING)], unique=True)
    await db["knowledge_chunks"].create_index([("document_id", ASCENDING)])
    await db["skills"].create_index([("skill_id", ASCENDING)], unique=True)
    await db["playbooks"].create_index([("playbook_id", ASCENDING)], unique=True)
    await db["keywords"].create_index([("normalized_term", ASCENDING)], unique=True)
    await db["web_sources"].create_index([("source_id", ASCENDING)], unique=True)
    await db["web_sources"].create_index([("normalized_url", ASCENDING)], unique=True)
    await db["feedback"].create_index([("execution_id", ASCENDING)])
    await db["support_cases"].create_index([("case_id", ASCENDING)], unique=True)
    await db["support_cases"].create_index([("session_id", ASCENDING), ("status", ASCENDING)])
    await db["tickets"].create_index([("ticket_id", ASCENDING)], unique=True)
    await db["tickets"].create_index([("case_id", ASCENDING)], unique=True)
    await db["tickets"].create_index([("customer.user_id", ASCENDING)])
    await db["memory_records"].create_index([("user_id", ASCENDING), ("type", ASCENDING)])
    await db["semantic_memories"].create_index([("user_id", ASCENDING)])
    await db["audit_events"].create_index([("event_type", ASCENDING), ("created_at", ASCENDING)])
    await db["feedback_proposals"].create_index([("proposal_id", ASCENDING)], unique=True)
    await db["feedback_proposals"].create_index([("status", ASCENDING)])
    await db["feedback_proposals"].create_index([("created_at", ASCENDING)])
    await db["feedback_agent_runs"].create_index([("run_id", ASCENDING)], unique=True)
    await db["feedback_agent_runs"].create_index([("started_at", ASCENDING)])

    await _create_vector_indexes(db, settings.embedding_dimensions)


# O Mongo (atlas-local) responde ao ping antes de o servico de busca vetorial (mongot) estar
# pronto -- num banco recem-criado (sem volume persistente) a primeira tentativa costuma falhar.
_SEARCH_NOT_READY = "Search Index Management service"
_VECTOR_INDEX_MAX_ATTEMPTS = 40
_VECTOR_INDEX_RETRY_SECONDS = 3.0


async def _create_vector_index(db: AsyncIOMotorDatabase, collection_name: str, dimensions: int):
    """True se criado/ja existente; False se o servico de busca nunca ficou pronto."""
    for attempt in range(1, _VECTOR_INDEX_MAX_ATTEMPTS + 1):
        try:
            await db.command(
                {
                    "createSearchIndexes": collection_name,
                    "indexes": [_vector_index_definition(dimensions)],
                }
            )
            return True
        except OperationFailure as exc:
            if _SEARCH_NOT_READY not in str(exc):
                # ja existe, ou Vector Search indisponivel neste ambiente: nao ha o que repetir
                logger.info(
                    "vector_index_create_skipped", collection=collection_name, reason=str(exc)
                )
                return True
            if attempt == _VECTOR_INDEX_MAX_ATTEMPTS:
                break
            logger.info("vector_index_waiting_for_search_service", attempt=attempt)
            await asyncio.sleep(_VECTOR_INDEX_RETRY_SECONDS)
    logger.warning("vector_index_search_service_unavailable", collection=collection_name)
    return False


async def _create_vector_indexes(db: AsyncIOMotorDatabase, dimensions: int) -> None:
    for collection_name in _VECTOR_INDEX_COLLECTIONS:
        if not await _create_vector_index(db, collection_name, dimensions):
            return  # servico de busca fora do ar: nao repete o timeout nas demais collections
