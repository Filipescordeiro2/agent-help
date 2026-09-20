"""Busca semantica e hibrida -- /search/semantic(+/knowledge|/skills|/playbooks), /hybrid."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.rag.entity_search import semantic_search_entities
from app.rag.retrievers import hybrid_search, semantic_search
from app.repository.knowledge_chunks_repository import KnowledgeChunksRepository
from app.repository.playbooks_repository import PlaybooksRepository
from app.repository.skills_repository import SkillsRepository
from app.routers.deps import get_db, require_llm_api_key

router = APIRouter(prefix="/api/v1/search", tags=["search"])


class SemanticSearchRequest(BaseModel):
    query: str
    top_k: int = 5
    min_score: float = 0.70
    filters: dict | None = None


class SemanticSearchResponse(BaseModel):
    query: str
    results: list[dict]


def _to_response(query: str, results) -> SemanticSearchResponse:
    return SemanticSearchResponse(
        query=query,
        results=[
            {
                "document_id": r.document_id,
                "chunk_id": r.chunk_id,
                "score": r.score,
                "content": r.content,
                "metadata": r.metadata,
            }
            for r in results
        ],
    )


@router.post(
    "/semantic",
    response_model=SemanticSearchResponse,
    dependencies=[Depends(require_llm_api_key)],
)
@router.post(
    "/semantic/knowledge",
    response_model=SemanticSearchResponse,
    dependencies=[Depends(require_llm_api_key)],
)
async def search_semantic_knowledge(
    payload: SemanticSearchRequest, db: AsyncIOMotorDatabase = Depends(get_db)
) -> SemanticSearchResponse:
    results = await semantic_search(
        KnowledgeChunksRepository(db),
        query=payload.query,
        top_k=payload.top_k,
        min_score=payload.min_score,
        filters=payload.filters,
    )
    return _to_response(payload.query, results)


@router.post(
    "/semantic/skills",
    response_model=SemanticSearchResponse,
    dependencies=[Depends(require_llm_api_key)],
)
async def search_semantic_skills(
    payload: SemanticSearchRequest, db: AsyncIOMotorDatabase = Depends(get_db)
) -> SemanticSearchResponse:
    skills = await SkillsRepository(db).list_enabled()
    results = semantic_search_entities(
        payload.query,
        skills,
        id_field="skill_id",
        content_field="description",
        metadata_fn=lambda s: {"name": s.name, "owning_agent": s.owning_agent},
        top_k=payload.top_k,
        min_score=payload.min_score,
    )
    return _to_response(payload.query, results)


@router.post(
    "/semantic/playbooks",
    response_model=SemanticSearchResponse,
    dependencies=[Depends(require_llm_api_key)],
)
async def search_semantic_playbooks(
    payload: SemanticSearchRequest, db: AsyncIOMotorDatabase = Depends(get_db)
) -> SemanticSearchResponse:
    playbooks = await PlaybooksRepository(db).list(limit=500)
    results = semantic_search_entities(
        payload.query,
        playbooks,
        id_field="playbook_id",
        content_field="objective",
        metadata_fn=lambda p: {"name": p.name, "status": p.status.value},
        top_k=payload.top_k,
        min_score=payload.min_score,
    )
    return _to_response(payload.query, results)


@router.post(
    "/hybrid",
    response_model=SemanticSearchResponse,
    dependencies=[Depends(require_llm_api_key)],
)
async def search_hybrid(
    payload: SemanticSearchRequest, db: AsyncIOMotorDatabase = Depends(get_db)
) -> SemanticSearchResponse:
    results = await hybrid_search(
        KnowledgeChunksRepository(db),
        query=payload.query,
        top_k=payload.top_k,
        min_score=payload.min_score,
        filters=payload.filters,
    )
    return _to_response(payload.query, results)
