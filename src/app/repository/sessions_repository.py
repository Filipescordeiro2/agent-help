"""SessionsRepository -- colecao `sessions` (data-model.md SS Session)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.repository.base import BaseRepository


class SessionStatus(StrEnum):
    OPEN = "open"
    ACTIVE = "active"
    CLOSED = "closed"


class Session(BaseModel):
    session_id: str
    user_id: str
    status: SessionStatus = SessionStatus.OPEN
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    closed_at: datetime | None = None
    channel: str | None = None
    last_execution_id: str | None = None


class SessionsRepository(BaseRepository[Session]):
    collection_name = "sessions"
    model = Session
    id_field = "session_id"

    async def mark_active(self, session_id: str, execution_id: str) -> Session | None:
        return await self.update(
            session_id,
            {
                "status": SessionStatus.ACTIVE.value,
                "updated_at": datetime.now(UTC).isoformat(),
                "last_execution_id": execution_id,
            },
        )

    async def close(self, session_id: str) -> Session | None:
        now = datetime.now(UTC)
        return await self.update(
            session_id,
            {
                "status": SessionStatus.CLOSED.value,
                "updated_at": now.isoformat(),
                "closed_at": now.isoformat(),
            },
        )
