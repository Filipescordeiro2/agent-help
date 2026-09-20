"""KeywordsRepository -- colecao `keywords` (data-model.md, spec FR-030)."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.repository.base import BaseRepository


def normalize_term(term: str) -> str:
    return term.strip().lower()


class Keyword(BaseModel):
    keyword_id: str
    term: str
    normalized_term: str
    synonyms: list[str] = Field(default_factory=list)
    weight: float = 1.0
    related_intents: list[str] = Field(default_factory=list)
    associated_documents: list[str] = Field(default_factory=list)
    associated_skills: list[str] = Field(default_factory=list)
    associated_playbooks: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class KeywordsRepository(BaseRepository[Keyword]):
    collection_name = "keywords"
    model = Keyword
    id_field = "keyword_id"

    async def search(self, query: str) -> list[Keyword]:
        normalized_query = normalize_term(query)
        all_keywords = await self.list(limit=1000)
        return [
            k
            for k in all_keywords
            if normalized_query in k.normalized_term
            or any(normalized_query in normalize_term(s) for s in k.synonyms)
        ]
