"""MemoryRecordsRepository (`memory_records`) e SemanticMemoriesRepository
(`semantic_memories`) -- data-model.md secao "Memory Record", spec FR-031 a FR-034.

Memoria de curto prazo e servida por `sessions`/`messages`/`graph_checkpoints` (ver
app/services/memory/short_term.py) -- nao duplicada aqui. Este modulo cobre longo prazo, semantica e
episodica.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.repository.base import BaseRepository


class MemoryType(StrEnum):
    LONG_TERM = "long_term"
    SEMANTIC = "semantic"
    EPISODIC = "episodic"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    DELETED = "deleted"


class RetentionPolicy(BaseModel):
    ttl_days: int
    deletable_on_request: bool = True


class MemoryRecord(BaseModel):
    memory_id: str
    user_id: str
    session_id: str | None = None
    interaction_id: str | None = None
    type: MemoryType
    content: str
    origin: str
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: MemoryStatus = MemoryStatus.ACTIVE
    retention_policy: RetentionPolicy


class SemanticMemory(MemoryRecord):
    embedding: list[float] = Field(default_factory=list)


class MemoryRecordsRepository(BaseRepository[MemoryRecord]):
    collection_name = "memory_records"
    model = MemoryRecord
    id_field = "memory_id"

    async def list_for_user(
        self, user_id: str, *, type_: MemoryType | None = None
    ) -> list[MemoryRecord]:
        filters: dict = {"user_id": user_id, "status": MemoryStatus.ACTIVE.value}
        if type_ is not None:
            filters["type"] = type_.value
        return await self.list(filters=filters, limit=500)


class SemanticMemoriesRepository(BaseRepository[SemanticMemory]):
    collection_name = "semantic_memories"
    model = SemanticMemory
    id_field = "memory_id"

    async def list_for_user(self, user_id: str) -> list[SemanticMemory]:
        return await self.list(
            filters={"user_id": user_id, "status": MemoryStatus.ACTIVE.value}, limit=500
        )
