"""Servico de Skills -- CRUD, versionamento e regeracao de embedding (spec FR-024 a FR-026)."""

from __future__ import annotations

import uuid

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.llm.embeddings_client import embed_text
from app.repository.skills_repository import Skill, SkillsRepository


class SkillNotFoundError(Exception):
    pass


def _embedding_text(name: str, description: str, instructions: str, keywords: list[str]) -> str:
    return " ".join([name, description, instructions, *keywords])


async def create_skill(
    db: AsyncIOMotorDatabase,
    *,
    name: str,
    description: str,
    owning_agent: str,
    keywords: list[str],
    instructions: str,
    allowed_tools: list[str],
    enabled: bool = True,
    metadata: dict | None = None,
) -> Skill:
    skill = Skill(
        skill_id=str(uuid.uuid4()),
        name=name,
        description=description,
        owning_agent=owning_agent,
        keywords=keywords,
        instructions=instructions,
        allowed_tools=allowed_tools,
        enabled=enabled,
        metadata=metadata or {},
        embedding=embed_text(_embedding_text(name, description, instructions, keywords)),
    )
    repo = SkillsRepository(db)
    return await repo.insert(skill)


async def update_skill(
    db: AsyncIOMotorDatabase,
    skill_id: str,
    *,
    name: str,
    description: str,
    owning_agent: str,
    keywords: list[str],
    instructions: str,
    allowed_tools: list[str],
    enabled: bool,
    metadata: dict | None = None,
) -> Skill:
    repo = SkillsRepository(db)
    existing = await repo.get(skill_id)
    if existing is None:
        raise SkillNotFoundError(skill_id)

    # Regera o embedding sempre que instructions/keywords mudam (T109) -- comparar antes de
    # decidir se e necessario recalcular evita uma chamada de embedding desnecessaria.
    content_changed = instructions != existing.instructions or keywords != existing.keywords
    embedding = (
        embed_text(_embedding_text(name, description, instructions, keywords))
        if content_changed
        else existing.embedding
    )

    updated = await repo.update(
        skill_id,
        {
            "name": name,
            "description": description,
            "owning_agent": owning_agent,
            "keywords": keywords,
            "instructions": instructions,
            "allowed_tools": allowed_tools,
            "enabled": enabled,
            "metadata": metadata or {},
            "embedding": embedding,
            "version": existing.version + 1,
        },
    )
    assert updated is not None
    return updated


async def regenerate_embedding(db: AsyncIOMotorDatabase, skill_id: str) -> Skill:
    repo = SkillsRepository(db)
    skill = await repo.get(skill_id)
    if skill is None:
        raise SkillNotFoundError(skill_id)
    embedding = embed_text(
        _embedding_text(skill.name, skill.description, skill.instructions, skill.keywords)
    )
    updated = await repo.update(skill_id, {"embedding": embedding})
    assert updated is not None
    return updated


async def delete_skill(db: AsyncIOMotorDatabase, skill_id: str) -> bool:
    return await SkillsRepository(db).delete(skill_id)
