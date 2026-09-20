"""MessagesRepository -- colecao `messages` (data-model.md SS Message)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.repository.base import BaseRepository


class MessageSender(StrEnum):
    USER = "user"
    SYSTEM = "system"


class Message(BaseModel):
    message_id: str
    session_id: str
    sender: MessageSender
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MessagesRepository(BaseRepository[Message]):
    collection_name = "messages"
    model = Message
    id_field = "message_id"

    async def list_for_session(self, session_id: str, limit: int = 50) -> list[Message]:
        return await self.list(filters={"session_id": session_id}, limit=limit)
