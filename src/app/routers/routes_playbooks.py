"""CRUD completo de Playbooks + /embed + /search -- spec FR-027 a FR-029."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.llm.embeddings_client import embed_text
from app.rag.retrievers import RetrievalResult, cosine_similarity
from app.repository.playbooks_repository import Playbook, PlaybooksRepository
from app.routers.deps import get_db, require_llm_api_key
from app.services import playbooks as service

router = APIRouter(prefix="/api/v1/playbooks", tags=["playbooks"])


class PlaybookInput(BaseModel):
    name: str
    objective: str
    symptoms: list[str] = []
    prerequisites: list[str] = []
    steps: list[dict] = []
    decision_points: list[dict] = []
    authorized_tools: list[str] = []
    exceptions: list[str] = []
    escalation_rules: list[str] = []
    success_criteria: list[str] = []
    closure_criteria: list[str] = []
    status: str = "active"
    metadata: dict = {}


class PlaybookSearchRequest(BaseModel):
    query: str
    top_k: int = 5
    min_score: float = 0.0


class PlaybookSearchResponse(BaseModel):
    query: str
    results: list[dict]


@router.post("", response_model=Playbook, dependencies=[Depends(require_llm_api_key)])
async def create_playbook(
    payload: PlaybookInput, db: AsyncIOMotorDatabase = Depends(get_db)
) -> Playbook:
    return await service.create_playbook(db, **payload.model_dump())


@router.get("", response_model=list[Playbook])
async def list_playbooks(db: AsyncIOMotorDatabase = Depends(get_db)) -> list[Playbook]:
    return await PlaybooksRepository(db).list(limit=500)


@router.get("/{playbook_id}", response_model=Playbook)
async def get_playbook(playbook_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> Playbook:
    playbook = await PlaybooksRepository(db).get(playbook_id)
    if playbook is None:
        raise HTTPException(status_code=404, detail="playbook nao encontrado")
    return playbook


@router.put("/{playbook_id}", response_model=Playbook, dependencies=[Depends(require_llm_api_key)])
async def update_playbook(
    playbook_id: str, payload: PlaybookInput, db: AsyncIOMotorDatabase = Depends(get_db)
) -> Playbook:
    try:
        return await service.update_playbook(db, playbook_id, **payload.model_dump())
    except service.PlaybookNotFoundError as exc:
        raise HTTPException(status_code=404, detail="playbook nao encontrado") from exc


@router.delete("/{playbook_id}")
async def delete_playbook(playbook_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> dict:
    deleted = await service.delete_playbook(db, playbook_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="playbook nao encontrado")
    return {"status": "OK"}


@router.post(
    "/{playbook_id}/embed",
    response_model=Playbook,
    dependencies=[Depends(require_llm_api_key)],
)
async def embed_playbook(playbook_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> Playbook:
    try:
        return await service.regenerate_embedding(db, playbook_id)
    except service.PlaybookNotFoundError as exc:
        raise HTTPException(status_code=404, detail="playbook nao encontrado") from exc


@router.post(
    "/search",
    response_model=PlaybookSearchResponse,
    dependencies=[Depends(require_llm_api_key)],
)
async def search_playbooks(
    payload: PlaybookSearchRequest, db: AsyncIOMotorDatabase = Depends(get_db)
) -> PlaybookSearchResponse:
    query_embedding = embed_text(payload.query)
    playbooks = await PlaybooksRepository(db).list(limit=500)

    results = [
        RetrievalResult(
            document_id=p.playbook_id,
            chunk_id=None,
            score=cosine_similarity(query_embedding, p.embedding),
            content=p.objective,
            metadata={"name": p.name, "status": p.status.value},
        )
        for p in playbooks
    ]
    results = [r for r in results if r.score >= payload.min_score]
    results.sort(key=lambda r: r.score, reverse=True)
    results = results[: payload.top_k]

    return PlaybookSearchResponse(
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
