"""SkillsRepository -- colecao `skills` (data-model.md, spec FR-024 a FR-026)."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.repository.base import BaseRepository


class Skill(BaseModel):
    skill_id: str
    name: str
    description: str
    owning_agent: str
    keywords: list[str] = Field(default_factory=list)
    instructions: str
    allowed_tools: list[str] = Field(default_factory=list)
    enabled: bool = True
    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict = Field(default_factory=dict)
    embedding: list[float] = Field(default_factory=list)


class SkillsRepository(BaseRepository[Skill]):
    collection_name = "skills"
    model = Skill
    id_field = "skill_id"

    async def list_enabled(self) -> list[Skill]:
        return await self.list(filters={"enabled": True}, limit=1000)
