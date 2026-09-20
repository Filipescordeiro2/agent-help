"""CRUD de Keywords + busca -- spec FR-030."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError

from app.repository.keywords_repository import Keyword, KeywordsRepository
from app.routers.deps import get_db
from app.routers.errors import structured_error
from app.services import keywords as service

router = APIRouter(prefix="/api/v1/keywords", tags=["keywords"])


class KeywordInput(BaseModel):
    term: str
    synonyms: list[str] = []
    weight: float = 1.0
    related_intents: list[str] = []
    associated_documents: list[str] = []
    associated_skills: list[str] = []
    associated_playbooks: list[str] = []


def _duplicate():
    return structured_error(
        409,
        "KEYWORD_ALREADY_EXISTS",
        "Ja existe uma keyword com este termo (sem distinguir maiusculas).",
    )


@router.post("", response_model=Keyword)
async def create_keyword(payload: KeywordInput, db: AsyncIOMotorDatabase = Depends(get_db)):
    try:
        return await service.create_keyword(db, **payload.model_dump())
    except DuplicateKeyError:
        return _duplicate()


@router.get("", response_model=list[Keyword])
async def list_keywords(db: AsyncIOMotorDatabase = Depends(get_db)) -> list[Keyword]:
    return await KeywordsRepository(db).list(limit=500)


@router.get("/search", response_model=list[Keyword])
async def search_keywords(q: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> list[Keyword]:
    return await KeywordsRepository(db).search(q)


@router.get("/{keyword_id}", response_model=Keyword)
async def get_keyword(keyword_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> Keyword:
    keyword = await KeywordsRepository(db).get(keyword_id)
    if keyword is None:
        raise HTTPException(status_code=404, detail="keyword nao encontrada")
    return keyword


@router.put("/{keyword_id}", response_model=Keyword)
async def update_keyword(
    keyword_id: str, payload: KeywordInput, db: AsyncIOMotorDatabase = Depends(get_db)
):
    try:
        return await service.update_keyword(db, keyword_id, **payload.model_dump())
    except service.KeywordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="keyword nao encontrada") from exc
    except DuplicateKeyError:
        return _duplicate()


@router.delete("/{keyword_id}")
async def delete_keyword(keyword_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> dict:
    deleted = await service.delete_keyword(db, keyword_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="keyword nao encontrada")
    return {"status": "OK"}
