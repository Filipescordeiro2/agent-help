"""CRUD completo de Skills + /embed + /search -- spec FR-024 a FR-026."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.llm.embeddings_client import embed_text
from app.rag.retrievers import RetrievalResult, cosine_similarity
from app.repository.skills_repository import Skill, SkillsRepository
from app.routers.deps import get_db, require_llm_api_key
from app.services import skills as service

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


class SkillInput(BaseModel):
    name: str
    description: str
    owning_agent: str
    keywords: list[str] = []
    instructions: str
    allowed_tools: list[str] = []
    enabled: bool = True
    metadata: dict = {}


class SkillSearchRequest(BaseModel):
    query: str
    top_k: int = 5
    min_score: float = 0.0


class SkillSearchResponse(BaseModel):
    query: str
    results: list[dict]


@router.post("", response_model=Skill, dependencies=[Depends(require_llm_api_key)])
async def create_skill(payload: SkillInput, db: AsyncIOMotorDatabase = Depends(get_db)) -> Skill:
    return await service.create_skill(db, **payload.model_dump())


@router.get("", response_model=list[Skill])
async def list_skills(db: AsyncIOMotorDatabase = Depends(get_db)) -> list[Skill]:
    return await SkillsRepository(db).list(limit=500)


@router.get("/{skill_id}", response_model=Skill)
async def get_skill(skill_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> Skill:
    skill = await SkillsRepository(db).get(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="skill nao encontrada")
    return skill


@router.put("/{skill_id}", response_model=Skill, dependencies=[Depends(require_llm_api_key)])
async def update_skill(
    skill_id: str, payload: SkillInput, db: AsyncIOMotorDatabase = Depends(get_db)
) -> Skill:
    try:
        return await service.update_skill(db, skill_id, **payload.model_dump())
    except service.SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail="skill nao encontrada") from exc


@router.delete("/{skill_id}")
async def delete_skill(skill_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> dict:
    deleted = await service.delete_skill(db, skill_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="skill nao encontrada")
    return {"status": "OK"}


@router.post("/{skill_id}/embed", response_model=Skill, dependencies=[Depends(require_llm_api_key)])
async def embed_skill(skill_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> Skill:
    try:
        return await service.regenerate_embedding(db, skill_id)
    except service.SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail="skill nao encontrada") from exc


@router.post(
    "/search",
    response_model=SkillSearchResponse,
    dependencies=[Depends(require_llm_api_key)],
)
async def search_skills(
    payload: SkillSearchRequest, db: AsyncIOMotorDatabase = Depends(get_db)
) -> SkillSearchResponse:
    query_embedding = embed_text(payload.query)
    skills = await SkillsRepository(db).list_enabled()

    results = [
        RetrievalResult(
            document_id=s.skill_id,
            chunk_id=None,
            score=cosine_similarity(query_embedding, s.embedding),
            content=s.description,
            metadata={"name": s.name, "owning_agent": s.owning_agent},
        )
        for s in skills
    ]
    results = [r for r in results if r.score >= payload.min_score]
    results.sort(key=lambda r: r.score, reverse=True)
    results = results[: payload.top_k]

    return SkillSearchResponse(
        query=payload.query,
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
