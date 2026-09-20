"""FeedbackAgentRunsRepository -- colecao `feedback_agent_runs` (data-model.md, spec FR-010)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.repository.base import BaseRepository


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TriggerType(StrEnum):
    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"


class FeedbackAgentRun(BaseModel):
    run_id: str
    trigger_type: TriggerType
    status: RunStatus = RunStatus.RUNNING
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    feedback_processed_count: int = 0
    proposals_created_count: int = 0
    proposal_ids: list[str] = Field(default_factory=list)
    processed_feedback_ids: list[str] = Field(default_factory=list)
    blocked_feedback_ids: list[str] = Field(default_factory=list)
    blocked_reasons: dict[str, str] = Field(default_factory=dict)
    error_summary: str | None = None


class FeedbackAgentRunsRepository(BaseRepository[FeedbackAgentRun]):
    collection_name = "feedback_agent_runs"
    model = FeedbackAgentRun
    id_field = "run_id"

    async def list_recent(
        self, status: RunStatus | None = None, limit: int = 50, cursor: int = 0
    ) -> list[FeedbackAgentRun]:
        filters = {"status": status.value} if status else {}
        raw_docs = (
            await self._collection.find(filters)
            .sort("started_at", -1)
            .skip(cursor)
            .limit(limit)
            .to_list(length=limit)
        )
        for doc in raw_docs:
            doc.pop("_id", None)
        return [FeedbackAgentRun.model_validate(doc) for doc in raw_docs]

    async def all_processed_feedback_ids(self) -> set[str]:
        """Uniao dos feedbacks ja processados por runs COMPLETED (runs FAILED sao reprocessados)."""
        raw_docs = await self._collection.find({"status": RunStatus.COMPLETED.value}).to_list(
            length=100_000
        )
        processed: set[str] = set()
        for doc in raw_docs:
            processed.update(doc.get("processed_feedback_ids", []))
        return processed

    async def add_proposal_id(self, run_id: str, proposal_id: str) -> None:
        """Registra imediatamente (sem esperar o fim do run) a proposta criada nesta execucao."""
        await self._collection.update_one(
            {"run_id": run_id}, {"$addToSet": {"proposal_ids": proposal_id}}
        )
