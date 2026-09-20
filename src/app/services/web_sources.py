"""Servico de fontes web (sites que o agente consulta quando a base nao responde)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.rag.loaders.web import validate_source_url
from app.repository.web_sources_repository import (
    WebSource,
    WebSourcesRepository,
    normalize_url,
)


class WebSourceNotFoundError(Exception):
    pass


class WebSourceConflictError(Exception):
    """Ja existe uma fonte com esta URL."""


async def create_web_source(
    db: AsyncIOMotorDatabase,
    *,
    name: str,
    url: str,
    description: str,
    usage: str,
    topics: list[str],
    priority: int = 100,
    enabled: bool = True,
) -> WebSource:
    url = validate_source_url(url)  # levanta WebFetchError (WEB_URL_*)
    repo = WebSourcesRepository(db)
    if await repo.find_by_url(url) is not None:
        raise WebSourceConflictError(url)
    source = WebSource(
        source_id=str(uuid.uuid4()),
        name=name.strip(),
        url=url,
        normalized_url=normalize_url(url),
        description=description.strip(),
        usage=usage.strip(),
        topics=[t.strip() for t in topics if t.strip()],
        priority=priority,
        enabled=enabled,
        origin="api",
    )
    return await repo.insert(source)


async def get_web_source(db: AsyncIOMotorDatabase, source_id: str) -> WebSource:
    source = await WebSourcesRepository(db).get(source_id)
    if source is None:
        raise WebSourceNotFoundError(source_id)
    return source


async def update_web_source(
    db: AsyncIOMotorDatabase,
    source_id: str,
    *,
    name: str,
    url: str,
    description: str,
    usage: str,
    topics: list[str],
    priority: int,
    enabled: bool,
) -> WebSource:
    repo = WebSourcesRepository(db)
    await get_web_source(db, source_id)
    url = validate_source_url(url)
    other = await repo.find_by_url(url)
    if other is not None and other.source_id != source_id:
        raise WebSourceConflictError(url)
    updated = await repo.update(
        source_id,
        {
            "name": name.strip(),
            "url": url,
            "normalized_url": normalize_url(url),
            "description": description.strip(),
            "usage": usage.strip(),
            "topics": [t.strip() for t in topics if t.strip()],
            "priority": priority,
            "enabled": enabled,
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )
    assert updated is not None
    return updated


async def delete_web_source(db: AsyncIOMotorDatabase, source_id: str) -> bool:
    return await WebSourcesRepository(db).delete(source_id)
