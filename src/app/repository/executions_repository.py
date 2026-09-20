"""ExecutionsRepository -- colecao `agent_executions` (data-model.md SS Execution & Checkpoint)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.repository.base import BaseRepository
from app.schemas.common import StructuredError


class ExecutionStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class Execution(BaseModel):
    execution_id: str
    session_id: str
    message_id: str
    status: ExecutionStatus = ExecutionStatus.RUNNING
    routing_decision: dict[str, Any] | None = None
    agents_invoked: list[str] = Field(default_factory=list)
    tools_used: list[dict[str, Any]] = Field(default_factory=list)
    final_response: dict[str, Any] | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    error: StructuredError | None = None


class ExecutionsRepository(BaseRepository[Execution]):
    collection_name = "agent_executions"
    model = Execution
    id_field = "execution_id"

    async def list_for_session(self, session_id: str, limit: int = 50) -> list[Execution]:
        return await self.list(filters={"session_id": session_id}, limit=limit)

    async def mark_completed(
        self, execution_id: str, final_response: dict[str, Any]
    ) -> Execution | None:
        return await self.update(
            execution_id,
            {
                "status": ExecutionStatus.COMPLETED.value,
                "final_response": final_response,
                "completed_at": datetime.now(UTC).isoformat(),
            },
        )

    async def mark_failed(self, execution_id: str, error: StructuredError) -> Execution | None:
        return await self.update(
            execution_id,
            {
                "status": ExecutionStatus.FAILED.value,
                "error": error.model_dump(),
                "completed_at": datetime.now(UTC).isoformat(),
            },
        )
