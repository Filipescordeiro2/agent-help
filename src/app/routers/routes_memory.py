"""POST/GET /api/v1/memory, POST /api/v1/memory/search, DELETE /api/v1/memory/{id} --
spec FR-031 a FR-034."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.routers.deps import get_db, require_llm_api_key
from app.routers.identity import IdentityHeaders, get_identity_headers
from app.services.memory import service

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


class MemoryRecordInput(BaseModel):
    user_id: str | None = Field(
        default=None,
        description="Obsoleto: envie no cabecalho X-User-Id.",
        json_schema_extra={"deprecated": True},
    )
    type: str
    content: str
    origin: str
    confidence: float = 1.0
    session_id: str | None = Field(
        default=None,
        description="Obsoleto: envie no cabecalho X-Session-Id.",
        json_schema_extra={"deprecated": True},
    )
    interaction_id: str | None = None


class MemorySearchRequest(BaseModel):
    user_id: str | None = Field(
        default=None,
        description="Obsoleto: envie no cabecalho X-User-Id.",
        json_schema_extra={"deprecated": True},
    )
    query: str
    top_k: int = 5
    min_score: float = 0.0


@router.post("", dependencies=[Depends(require_llm_api_key)])
async def create_memory(
    payload: MemoryRecordInput,
    ids: IdentityHeaders = Depends(get_identity_headers),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    user_id = ids.resolve("user_id", payload.user_id)
    session_id = ids.resolve("session_id", payload.session_id, required=False)
    try:
        record = await service.create_memory_record(
            db,
            user_id=user_id,
            type_=payload.type,
            content=payload.content,
            origin=payload.origin,
            confidence=payload.confidence,
            session_id=session_id,
            interaction_id=payload.interaction_id,
        )
    except service.MemoryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return record.model_dump(mode="json")


@router.get("")
async def list_memory(
    user_id: str | None = None,
    type: str | None = None,
    ids: IdentityHeaders = Depends(get_identity_headers),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[dict]:
    resolved = ids.resolve("user_id", user_id)
    return await service.list_memory(db, user_id=resolved, type_=type)


@router.post("/search", dependencies=[Depends(require_llm_api_key)])
async def search_memory(
    payload: MemorySearchRequest,
    ids: IdentityHeaders = Depends(get_identity_headers),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[dict]:
    return await service.search_semantic_memory(
        db,
        user_id=ids.resolve("user_id", payload.user_id),
        query=payload.query,
        top_k=payload.top_k,
        min_score=payload.min_score,
    )


@router.delete("/{memory_id}")
async def delete_memory(memory_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> dict:
    deleted = await service.delete_memory_record(db, memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="registro de memoria nao encontrado")
    return {"status": "OK"}
