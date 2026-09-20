"""WebSourcesRepository -- colecao `web_sources`: sites que o agente pode consultar sozinho.

Cada fonte descreve o que o site e (`description`), quando usa-lo (`usage`) e seus temas
(`topics`); as estatisticas de consulta (`consulted_count`, `useful_count`, `last_status`) mostram
quais fontes de fato ajudam, para melhorar a escolha das fontes e das tools.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.repository.base import BaseRepository


def normalize_url(url: str) -> str:
    """Forma canonica para detectar duplicatas: sem ancora, sem barra final, minusculas no host."""
    from urllib.parse import urlsplit

    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{(parts.hostname or '').lower()}{path}{query}"


class WebSource(BaseModel):
    source_id: str
    name: str
    url: str
    normalized_url: str
    description: str  # o que o site e (principal)
    usage: str  # quando/como usa-lo
    topics: list[str] = Field(default_factory=list)
    priority: int = 100  # menor = mais prioritaria
    enabled: bool = True
    origin: Literal["default", "api"] = "api"
    consulted_count: int = 0
    useful_count: int = 0
    last_consulted_at: datetime | None = None
    last_status: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict = Field(default_factory=dict)


class WebSourcesRepository(BaseRepository[WebSource]):
    collection_name = "web_sources"
    model = WebSource
    id_field = "source_id"

    async def list_all(self) -> list[WebSource]:
        sources = await self.list(limit=1000)
        return sorted(sources, key=lambda s: (s.priority, s.name.lower()))

    async def list_enabled(self) -> list[WebSource]:
        return [s for s in await self.list_all() if s.enabled]

    async def find_by_url(self, url: str) -> WebSource | None:
        found = await self.list(filters={"normalized_url": normalize_url(url)}, limit=1)
        return found[0] if found else None

    async def record_consultation(self, source_id: str, *, useful: bool, status: str) -> None:
        await self._collection.update_one(
            {self.id_field: source_id},
            {
                "$inc": {"consulted_count": 1, "useful_count": 1 if useful else 0},
                "$set": {"last_consulted_at": datetime.now(UTC).isoformat(), "last_status": status},
            },
        )
