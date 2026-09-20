"""Servico de Keywords -- cadastro, normalizacao, sinonimos, busca (spec FR-030)."""

from __future__ import annotations

import uuid

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.repository.keywords_repository import (
    Keyword,
    KeywordsRepository,
    normalize_term,
)


class KeywordNotFoundError(Exception):
    pass


async def create_keyword(
    db: AsyncIOMotorDatabase,
    *,
    term: str,
    synonyms: list[str],
    weight: float,
    related_intents: list[str],
    associated_documents: list[str],
    associated_skills: list[str],
    associated_playbooks: list[str],
) -> Keyword:
    keyword = Keyword(
        keyword_id=str(uuid.uuid4()),
        term=term,
        normalized_term=normalize_term(term),
        synonyms=synonyms,
        weight=weight,
        related_intents=related_intents,
        associated_documents=associated_documents,
        associated_skills=associated_skills,
        associated_playbooks=associated_playbooks,
    )
    return await KeywordsRepository(db).insert(keyword)


async def update_keyword(
    db: AsyncIOMotorDatabase,
    keyword_id: str,
    *,
    term: str,
    synonyms: list[str],
    weight: float,
    related_intents: list[str],
    associated_documents: list[str],
    associated_skills: list[str],
    associated_playbooks: list[str],
) -> Keyword:
    repo = KeywordsRepository(db)
    if await repo.get(keyword_id) is None:
        raise KeywordNotFoundError(keyword_id)
    updated = await repo.update(
        keyword_id,
        {
            "term": term,
            "normalized_term": normalize_term(term),
            "synonyms": synonyms,
            "weight": weight,
            "related_intents": related_intents,
            "associated_documents": associated_documents,
            "associated_skills": associated_skills,
            "associated_playbooks": associated_playbooks,
        },
    )
    assert updated is not None
    return updated


async def delete_keyword(db: AsyncIOMotorDatabase, keyword_id: str) -> bool:
    return await KeywordsRepository(db).delete(keyword_id)
